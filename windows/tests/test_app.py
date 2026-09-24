import threading
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import soundfile as sf

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


class WindowOpenLifetimeTests(unittest.TestCase):
    def test_close_before_constructor_returns_does_not_restore_dead_dialog(self):
        for method, attribute, constructor in (
                ("open_setup", "setup_window", "SetupWindow"),
                ("open_settings", "settings_window", "SettingsWindow"),
                ("open_scratchpad", "scratchpad", "ScratchpadWindow")):
            with self.subTest(method=method):
                instance = app.PresspeechApp.__new__(app.PresspeechApp)
                instance._window_open_lock = threading.Lock()
                setattr(instance, attribute, None)

                def closes_before_return(owner):
                    window = mock.Mock(root=None)
                    setattr(owner, attribute, window)
                    setattr(owner, attribute, None)
                    return window

                with mock.patch.object(
                        app.ui, constructor, side_effect=closes_before_return):
                    getattr(instance, method)()
                self.assertIsNone(getattr(instance, attribute))


class BenchmarkCapturePrivacyTests(unittest.TestCase):
    def test_capture_keeps_session_and_file_path_out_of_diagnostic_log(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {
            "capture_benchmark_remaining": 1,
            "capture_benchmark_session": "private-session-label",
            "capture_benchmark_index": 1,
            "capture_next_benchmark": False,
        }
        instance._log = mock.Mock()
        instance.notify = mock.Mock()
        # These samples are deliberately not all representable as PCM16; the
        # benchmark export must retain the exact float signal sent to ASR.
        captured = app.np.array(
            [0.0, 0.00001, -0.12345679, 0.9876543, 1.125],
            dtype=app.np.float32)
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(app, "__file__", str(Path(directory) / "app.py")), \
                mock.patch.object(app.cfg, "save"):
            output = instance._capture_benchmark_if_armed(captured)
            self.assertTrue(Path(output).is_file())
            self.assertIn("private-session-label", Path(output).name)
            self.assertEqual(sf.info(output).subtype, "FLOAT")
            replay, sample_rate = sf.read(output, dtype="float32")
            self.assertEqual(sample_rate, 16000)
            app.np.testing.assert_array_equal(replay, captured)
        instance._log.assert_called_once_with("benchmark audio saved")
        self.assertNotIn("private-session-label", str(instance._log.call_args_list))
        self.assertNotIn("private-session-label", str(instance.notify.call_args_list))
        instance.notify.assert_called_once_with(
            "Benchmark clip saved",
            "Saved in the local benchmarks/audio folder (0 remaining).")

    def test_capture_failure_notice_omits_raw_exception_and_private_path(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {
            "capture_benchmark_remaining": 1,
            "capture_benchmark_session": "private-session-label",
            "capture_benchmark_index": 1,
            "capture_next_benchmark": False,
        }
        instance._log = mock.Mock()
        instance.notify = mock.Mock()
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(app, "__file__", str(Path(directory) / "app.py")), \
                mock.patch("soundfile.write", side_effect=OSError(
                    "synthetic-private-path-and-clip-detail")):
            self.assertIsNone(instance._capture_benchmark_if_armed(
                app.np.array([0.0, 0.1], dtype=app.np.float32)))

        self.assertNotIn("synthetic-private", str(instance.notify.call_args_list))
        self.assertNotIn("private-session-label", str(instance.notify.call_args_list))
        instance.notify.assert_called_once_with(
            "Benchmark capture failed",
            "The armed clip could not be saved. Check the local benchmark "
            "output directory before trying again.")


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


class SupportLinkTests(unittest.TestCase):
    def test_feedback_guide_is_fixed_public_repository_page(self):
        self.assertEqual(
            app.SUPPORT_GUIDE_URL,
            "https://github.com/rcourtman/presspeech/blob/main/SUPPORT.md",
        )

    def make_app(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance._log = mock.Mock()
        instance.notify = mock.Mock()
        return instance

    def test_report_problem_opens_support_guide(self):
        instance = self.make_app()
        with mock.patch.object(app.os, "startfile", create=True) as startfile:
            self.assertTrue(instance.report_problem())

        startfile.assert_called_once_with(app.SUPPORT_GUIDE_URL)
        instance.notify.assert_not_called()

    def test_suggest_improvement_opens_support_guide(self):
        instance = self.make_app()
        with mock.patch.object(app.os, "startfile", create=True) as startfile:
            self.assertTrue(instance.suggest_improvement())

        startfile.assert_called_once_with(app.SUPPORT_GUIDE_URL)

    def test_app_compatibility_opens_fixed_privacy_safe_guide(self):
        instance = self.make_app()
        with mock.patch.object(app.os, "startfile", create=True) as startfile:
            self.assertTrue(instance.test_app_compatibility())

        startfile.assert_called_once_with(app.APP_COMPATIBILITY_GUIDE_URL)

    def test_support_link_failure_has_manual_recovery_without_url_details(self):
        instance = self.make_app()
        with mock.patch.object(
                app.os, "startfile", side_effect=OSError("private browser detail"),
                create=True):
            self.assertFalse(instance.report_problem())

        instance._log.assert_called_once_with(
            "could not open support page: OSError")
        instance.notify.assert_called_once_with(
            "Could not open browser",
            "Open rcourtman.github.io/presspeech or "
            "github.com/rcourtman/presspeech in your browser.")


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

    def test_download_worker_redacts_unexpected_exception_details(self):
        window = self.make_window()
        private_detail = "proxy-password=synthetic-private-marker"

        with mock.patch.object(
                app.ui.updates, "download_update",
                side_effect=RuntimeError(private_detail)):
            window._download_worker()

        self.assertEqual(
            window.events.get_nowait(),
            ("error", "Could not prepare or download the update."),
        )

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
        window.download_button.config.assert_called_with(
            text="Download Update", command=window._download, state="normal")

    def test_busy_install_keeps_verified_installer_for_explicit_retry(self):
        window = self.make_window()
        installer = self.stage_ready_installer(window)
        window.app.launch_update.side_effect = [
            app.updates.UpdateInstallBusy(
                "Copy or discard the waiting dictation before installing."),
            None,
        ]

        with mock.patch.object(app.ui.messagebox, "askyesno", return_value=True), \
                mock.patch.object(app.ui.messagebox, "showwarning") as warning:
            window._poll()
            self.assertEqual(window.downloaded_installer, installer)
            self.assertTrue(app.os.path.exists(installer))
            window.download_button.config.assert_called_with(
                text="Install Update", command=window._install_ready,
                state="normal")
            self.assertIn(
                "verified installer remains ready",
                window.status.config.call_args.kwargs["text"].lower())
            warning.assert_called_once_with(
                "Update postponed",
                "Copy or discard the waiting dictation before installing.",
                parent=window.root)
            window._install_ready()

        self.assertEqual(window.app.launch_update.call_count, 2)
        self.assertEqual(window.downloaded_installer, installer)
        self.assertTrue(app.os.path.exists(installer))
        window._close()
        self.assertFalse(app.os.path.exists(installer))

    def test_close_during_install_confirmation_cannot_launch_stale_installer(self):
        window = self.make_window()
        installer = self.stage_ready_installer(window)
        with mock.patch.object(app.ui.messagebox, "askyesno") as approve:
            approve.side_effect = lambda *_args, **_kwargs: (
                window._close() or True)
            window._poll()
        window.app.launch_update.assert_not_called()
        self.assertFalse(app.os.path.exists(installer))

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
        window.download_button.config.assert_called_with(
            text="Download Update", command=window._download, state="normal")
        showerror.assert_called_once_with(
            "Update failed", "Could not start the verified installer.",
            parent=window.root)

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


class UpdateCheckFailureTests(unittest.TestCase):
    def make_app(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance._update_lock = threading.Lock()
        instance.settings = {"last_update_check_epoch": 0}
        instance._log = mock.Mock()
        instance.notify = mock.Mock()
        instance.pending_update = None
        instance.update_window = None
        return instance

    def test_manual_failure_notification_redacts_exception_details(self):
        instance = self.make_app()
        instance.settings["last_update_check_epoch"] = 99_999
        private_detail = "proxy-password=synthetic-private-marker"

        with mock.patch.object(
                app.updates, "fetch_update",
                side_effect=RuntimeError(private_detail)), \
                mock.patch.object(app.cfg, "save") as save:
            instance._update_check_worker(manual=True)

        save.assert_not_called()
        self.assertEqual(instance.settings["last_update_check_epoch"], 99_999)
        instance._log.assert_called_once_with(
            "update check failed: RuntimeError")
        instance.notify.assert_called_once_with(
            "Update check failed", "Could not check for updates. Please try again.")

    def test_automatic_failure_persists_attempt_before_request(self):
        instance = self.make_app()
        private_detail = "proxy-password=synthetic-private-marker"
        with mock.patch.object(app.time, "time", return_value=100_000), \
                mock.patch.object(app.cfg, "save") as save:
            def fail(_version):
                save.assert_called_once_with(instance.settings)
                self.assertEqual(instance.settings["last_update_check_epoch"], 100_000)
                raise RuntimeError(private_detail)

            with mock.patch.object(app.updates, "fetch_update", side_effect=fail) as fetch:
                instance._update_check_worker()

        fetch.assert_called_once_with(app.cfg.VERSION)
        self.assertFalse(app._update_check_due(
            instance.settings["last_update_check_epoch"], 100_001))
        instance._log.assert_called_once_with(
            "update check failed: RuntimeError")
        instance.notify.assert_not_called()

    def test_automatic_persistence_failure_skips_unthrottled_request(self):
        instance = self.make_app()
        with mock.patch.object(app.time, "time", return_value=100_000), \
                mock.patch.object(app.cfg, "save", side_effect=OSError("private path")), \
                mock.patch.object(app.updates, "fetch_update") as fetch:
            instance._update_check_worker()

        fetch.assert_not_called()
        instance._log.assert_called_once_with("update check failed: OSError")
        self.assertFalse(instance._update_lock.locked())

    def test_automatic_success_records_attempt_without_a_second_save(self):
        instance = self.make_app()
        with mock.patch.object(app.time, "time", return_value=100_000), \
                mock.patch.object(app.cfg, "save") as save, \
                mock.patch.object(app.updates, "fetch_update", return_value=None) as fetch:
            instance._update_check_worker()

        fetch.assert_called_once_with(app.cfg.VERSION)
        save.assert_called_once_with(instance.settings)
        self.assertEqual(instance.settings["last_update_check_epoch"], 100_000)
        instance.notify.assert_not_called()

    def test_manual_success_is_not_throttled_and_records_check(self):
        instance = self.make_app()
        instance.settings["last_update_check_epoch"] = 99_999
        with mock.patch.object(app.time, "time", return_value=100_000), \
                mock.patch.object(app.cfg, "save") as save, \
                mock.patch.object(app.updates, "fetch_update", return_value=None) as fetch:
            instance._update_check_worker(manual=True)

        fetch.assert_called_once_with(app.cfg.VERSION)
        save.assert_called_once_with(instance.settings)
        self.assertEqual(instance.settings["last_update_check_epoch"], 100_000)
        instance.notify.assert_called_once_with(
            "Presspeech", "Version %s is up to date." % app.cfg.VERSION)


class InputSelectionTests(unittest.TestCase):
    def make_app(self, selected="auto"):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = threading.Lock()
        instance.recording = False
        instance.stream = None
        instance.input_device = None
        instance._cached_input_selector = None
        instance.settings = {"input_device": selected}
        instance._log = mock.Mock()
        instance._rescan_audio_devices = mock.Mock(return_value=True)
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

    def test_successful_input_log_omits_the_device_label(self):
        private_name = "Alice's Conference Room microphone"
        devices = [
            {"name": private_name, "hostapi": 0, "max_input_channels": 1},
        ]
        instance = self.make_app()
        with mock.patch.object(app.sd, "query_devices", return_value=devices), \
                mock.patch.object(
                    app.sd, "query_hostapis", return_value=HOST_APIS), \
                mock.patch.object(
                    app.sd, "check_input_settings", return_value=None), \
                mock.patch.object(
                    app.PresspeechApp, "_probe_input", return_value=True):
            self.assertEqual(instance._get_input_device(), (0, 16000))

        rendered_log = "\n".join(
            call.args[0] for call in instance._log.call_args_list)
        self.assertIn("using automatic input", rendered_log)
        self.assertNotIn(private_name, rendered_log)

    def test_picker_excludes_stereo_mix_wdm_ks_and_hyperx(self):
        instance = self.make_app()
        with mock.patch.object(app.sd, "query_devices", return_value=DEVICES), \
                mock.patch.object(app.sd, "query_hostapis", return_value=HOST_APIS):
            options = instance.input_device_options()
        labels = [label for label, _selector in options]
        self.assertFalse(any("Stereo Mix" in label for label in labels))
        self.assertFalse(any("WDM-KS" in label for label in labels))
        self.assertFalse(any("HyperX" in label for label in labels))

    def test_picker_preserves_a_configured_microphone_while_unavailable(self):
        selected = "MME::USB conference microphone"
        instance = self.make_app(selected)
        with mock.patch.object(app.sd, "query_devices", return_value=DEVICES), \
                mock.patch.object(app.sd, "query_hostapis", return_value=HOST_APIS):
            options = instance.input_device_options()

        unavailable = [
            label for label, selector in options if selector == selected
        ]
        self.assertEqual(len(unavailable), 1)
        self.assertIn("USB conference microphone", unavailable[0])
        self.assertIn("currently unavailable", unavailable[0])

    def test_picker_preserves_configuration_when_device_query_fails(self):
        selected = "MME::USB conference microphone"
        instance = self.make_app(selected)
        with mock.patch.object(
                app.sd, "query_devices", side_effect=OSError("backend busy")):
            options = instance.input_device_options()

        self.assertEqual(options[-1][1], selected)
        self.assertIn("currently unavailable", options[-1][0])

    def test_picker_does_not_offer_indistinguishable_explicit_inputs(self):
        duplicated = [*DEVICES, dict(DEVICES[1])]
        selected = app.PresspeechApp._device_selector(DEVICES[1], "MME")
        instance = self.make_app()
        with mock.patch.object(app.sd, "query_devices", return_value=duplicated), \
                mock.patch.object(app.sd, "query_hostapis", return_value=HOST_APIS):
            options = instance.input_device_options()
        self.assertFalse(any(value == selected for _label, value in options))
        self.assertTrue(any(
            value == app.PresspeechApp._device_selector(
                DEVICES[2], "Windows WASAPI")
            for _label, value in options))

        instance.settings["input_device"] = selected
        with mock.patch.object(app.sd, "query_devices", return_value=duplicated), \
                mock.patch.object(app.sd, "query_hostapis", return_value=HOST_APIS):
            options = instance.input_device_options()
        ambiguous = [label for label, value in options if value == selected]
        self.assertEqual(len(ambiguous), 1)
        self.assertIn("multiple indistinguishable devices", ambiguous[0])

    def test_configured_ambiguous_input_is_not_probed_or_opened(self):
        duplicated = [*DEVICES, dict(DEVICES[1])]
        selected = app.PresspeechApp._device_selector(DEVICES[1], "MME")
        instance = self.make_app(selected)
        with mock.patch.object(app.sd, "query_devices", return_value=duplicated), \
                mock.patch.object(app.sd, "query_hostapis", return_value=HOST_APIS), \
                mock.patch.object(app.sd, "check_input_settings") as check, \
                mock.patch.object(app.PresspeechApp, "_probe_input") as probe:
            self.assertIsNone(instance._get_input_device())
        check.assert_not_called()
        probe.assert_not_called()
        self.assertIsNone(instance.input_device)
        self.assertIn("configured input is ambiguous",
                      instance._log.call_args_list[-1].args[0])

    def test_configured_device_never_falls_back_to_another_microphone(self):
        private_name = "Alice's private office microphone"
        instance = self.make_app("MME::" + private_name)
        patches = self.sounddevice_mocks()
        with patches[0], patches[1], patches[2], patches[3] as probe:
            self.assertIsNone(instance._get_input_device())
        probe.assert_not_called()
        rendered_log = "\n".join(
            call.args[0] for call in instance._log.call_args_list)
        self.assertIn("configured input is unavailable", rendered_log)
        self.assertNotIn(private_name, rendered_log)

    def test_reconnected_device_is_recovered_by_rescanning(self):
        instance = self.make_app()
        instance._find_input_device = mock.Mock(side_effect=[None, (1, 16000)])

        self.assertEqual(instance._get_input_device(), (1, 16000))
        self.assertEqual(instance._find_input_device.call_count, 2)
        instance._rescan_audio_devices.assert_called_once()

    def test_successful_lookup_does_not_rescan_audio_devices(self):
        instance = self.make_app()
        instance._find_input_device = mock.Mock(return_value=(0, 16000))

        self.assertEqual(instance._get_input_device(), (0, 16000))
        instance._rescan_audio_devices.assert_not_called()

    def test_lookup_for_changed_selection_is_returned_but_not_cached(self):
        instance = self.make_app("MME::Old microphone")

        def finish_old_lookup(selected):
            self.assertEqual(selected, "MME::Old microphone")
            instance.settings["input_device"] = "MME::New microphone"
            instance.input_device = None
            instance._cached_input_selector = None
            return (1, 48000)

        instance._find_input_device = mock.Mock(side_effect=finish_old_lookup)

        # The recording that began the lookup may use its result, but a later
        # recording must not reuse it for the newly selected microphone.
        self.assertEqual(instance._get_input_device(), (1, 48000))
        self.assertIsNone(instance.input_device)
        self.assertIsNone(instance._cached_input_selector)

        instance._find_input_device = mock.Mock(return_value=(2, 16000))
        self.assertEqual(instance._get_input_device(), (2, 16000))
        instance._find_input_device.assert_called_once_with(
            "MME::New microphone")
        self.assertEqual(instance._cached_input_selector,
                         "MME::New microphone")

    def test_cache_from_another_selector_is_not_reused(self):
        instance = self.make_app("MME::New microphone")
        instance.input_device = (1, 48000)
        instance._cached_input_selector = "MME::Old microphone"
        instance._find_input_device = mock.Mock(return_value=(2, 16000))

        self.assertEqual(instance._get_input_device(), (2, 16000))

        instance._find_input_device.assert_called_once_with(
            "MME::New microphone")
        self.assertEqual(instance.input_device, (2, 16000))
        self.assertEqual(instance._cached_input_selector,
                         "MME::New microphone")

    def test_live_identity_is_checked_before_reusing_configured_device_index(self):
        selected = app.PresspeechApp._device_selector(DEVICES[1], "MME")
        instance = self.make_app(selected)
        instance.input_device = (1, 16000)
        instance._cached_input_selector = selected
        reordered = [dict(device) for device in DEVICES]
        reordered[1]["name"] = "Built-in microphone"
        reordered[2] = dict(DEVICES[1])
        instance._find_input_device = mock.Mock(return_value=(2, 16000))

        with mock.patch.object(
                app.sd, "query_devices", return_value=reordered), \
                mock.patch.object(
                    app.sd, "query_hostapis", return_value=HOST_APIS), \
                mock.patch.object(
                    app.sd, "check_input_settings", return_value=None):
            self.assertEqual(instance._get_input_device(), (2, 16000))

        instance._find_input_device.assert_called_once_with(selected)
        self.assertEqual(instance.input_device, (2, 16000))

    def test_matching_live_identity_reuses_cache_without_full_probe(self):
        selected = app.PresspeechApp._device_selector(DEVICES[1], "MME")
        instance = self.make_app(selected)
        instance.input_device = (1, 16000)
        instance._cached_input_selector = selected
        instance._find_input_device = mock.Mock()

        with mock.patch.object(
                app.sd, "query_devices", return_value=DEVICES), \
                mock.patch.object(
                    app.sd, "query_hostapis", return_value=HOST_APIS), \
                mock.patch.object(
                    app.sd, "check_input_settings", return_value=None) as check:
            self.assertEqual(instance._get_input_device(), (1, 16000))

        check.assert_called_once_with(
            device=1, samplerate=16000, channels=1, dtype="float32")
        instance._find_input_device.assert_not_called()

    def test_explicit_cache_is_rejected_when_identical_input_appears(self):
        selected = app.PresspeechApp._device_selector(DEVICES[1], "MME")
        instance = self.make_app(selected)
        instance.input_device = (1, 16000)
        instance._cached_input_selector = selected
        duplicated = [*DEVICES, dict(DEVICES[1])]
        instance._find_input_device = mock.Mock(return_value=None)

        with mock.patch.object(app.sd, "query_devices", return_value=duplicated), \
                mock.patch.object(app.sd, "query_hostapis", return_value=HOST_APIS), \
                mock.patch.object(app.sd, "check_input_settings") as check:
            self.assertIsNone(instance._get_input_device())

        check.assert_not_called()
        self.assertIsNone(instance.input_device)
        self.assertEqual(instance._find_input_device.call_count, 2)

    def test_automatic_cache_does_not_reuse_an_index_that_became_unsafe(self):
        instance = self.make_app()
        instance.input_device = (1, 16000)
        instance._cached_input_selector = app.AUTO_INPUT_DEVICE
        instance._cached_input_topology = instance._automatic_input_topology(
            1, DEVICES, HOST_APIS)
        replaced = [dict(device) for device in DEVICES]
        replaced[1]["name"] = "Stereo Mix (loopback)"
        instance._find_input_device = mock.Mock(return_value=(0, 16000))

        with mock.patch.object(
                app.sd, "query_devices", return_value=replaced), \
                mock.patch.object(
                    app.sd, "query_hostapis", return_value=HOST_APIS), \
                mock.patch.object(
                    app.sd, "check_input_settings", return_value=None):
            self.assertEqual(instance._get_input_device(), (0, 16000))

        instance._find_input_device.assert_called_once_with(
            app.AUTO_INPUT_DEVICE)

    def test_automatic_cache_reuses_unchanged_unique_device(self):
        instance = self.make_app()
        instance._find_input_device = mock.Mock(return_value=(1, 16000))

        with mock.patch.object(
                app.sd, "query_devices", return_value=DEVICES), \
                mock.patch.object(
                    app.sd, "query_hostapis", return_value=HOST_APIS), \
                mock.patch.object(
                    app.sd, "check_input_settings", return_value=None):
            self.assertEqual(instance._get_input_device(), (1, 16000))
            self.assertEqual(instance._get_input_device(), (1, 16000))

        instance._find_input_device.assert_called_once_with(
            app.AUTO_INPUT_DEVICE)
        self.assertEqual(instance._cached_input_topology,
                         instance._automatic_input_topology(1, DEVICES, HOST_APIS))

    def test_automatic_cache_reprobes_usable_replacement_at_same_index(self):
        instance = self.make_app()
        instance.input_device = (1, 16000)
        instance._cached_input_selector = app.AUTO_INPUT_DEVICE
        instance._cached_input_topology = instance._automatic_input_topology(
            1, DEVICES, HOST_APIS)
        replaced = [dict(device) for device in DEVICES]
        replaced[1]["name"] = "Built-in microphone"
        instance._find_input_device = mock.Mock(return_value=(0, 16000))

        with mock.patch.object(
                app.sd, "query_devices", return_value=replaced), \
                mock.patch.object(
                    app.sd, "query_hostapis", return_value=HOST_APIS), \
                mock.patch.object(
                    app.sd, "check_input_settings", return_value=None):
            self.assertEqual(instance._get_input_device(), (0, 16000))

        instance._find_input_device.assert_called_once_with(
            app.AUTO_INPUT_DEVICE)
        self.assertEqual(instance.input_device, (0, 16000))

    def test_automatic_cache_reprobes_when_other_device_changes(self):
        instance = self.make_app()
        instance.input_device = (1, 16000)
        instance._cached_input_selector = app.AUTO_INPUT_DEVICE
        instance._cached_input_topology = instance._automatic_input_topology(
            1, DEVICES, HOST_APIS)
        changed = [dict(device) for device in DEVICES]
        changed[0]["name"] = "New preferred microphone"
        instance._find_input_device = mock.Mock(return_value=(0, 16000))

        with mock.patch.object(
                app.sd, "query_devices", return_value=changed), \
                mock.patch.object(
                    app.sd, "query_hostapis", return_value=HOST_APIS), \
                mock.patch.object(
                    app.sd, "check_input_settings", return_value=None):
            self.assertEqual(instance._get_input_device(), (0, 16000))

        instance._find_input_device.assert_called_once_with(
            app.AUTO_INPUT_DEVICE)

    def test_automatic_cache_never_reuses_ambiguous_identical_labels(self):
        instance = self.make_app()
        instance._find_input_device = mock.Mock(return_value=(1, 16000))
        duplicated = [dict(device) for device in DEVICES]
        duplicated.append(dict(DEVICES[1]))

        with mock.patch.object(
                app.sd, "query_devices", return_value=duplicated), \
                mock.patch.object(
                    app.sd, "query_hostapis", return_value=HOST_APIS), \
                mock.patch.object(
                    app.sd, "check_input_settings", return_value=None):
            self.assertEqual(instance._get_input_device(), (1, 16000))
            self.assertEqual(instance._get_input_device(), (1, 16000))

        self.assertIsNone(instance._cached_input_topology)
        self.assertEqual(instance._find_input_device.call_count, 2)

    def test_failed_rescan_leaves_no_microphone(self):
        instance = self.make_app()
        instance._rescan_audio_devices = mock.Mock(return_value=False)
        instance._find_input_device = mock.Mock(return_value=None)

        self.assertIsNone(instance._get_input_device())
        self.assertEqual(instance._find_input_device.call_count, 1)

    def test_audio_rescan_reinitializes_portaudio(self):
        instance = self.make_app()
        del instance._rescan_audio_devices
        with mock.patch.object(app.sd, "_terminate", create=True) as terminate, \
                mock.patch.object(app.sd, "_initialize", create=True) as initialize:
            self.assertTrue(instance._rescan_audio_devices())

        terminate.assert_called_once_with()
        initialize.assert_called_once_with()

    def test_audio_rescan_reports_failure_without_raising(self):
        instance = self.make_app()
        del instance._rescan_audio_devices
        with mock.patch.object(app.sd, "_terminate", create=True,
                               side_effect=OSError("device unavailable")):
            self.assertFalse(instance._rescan_audio_devices())

        instance._log.assert_called_once()

    def test_failed_probe_closes_the_created_microphone_stream(self):
        stream = mock.Mock()
        stream.start.side_effect = OSError("device became unavailable")

        with mock.patch.object(app.sd, "InputStream", return_value=stream):
            self.assertFalse(app.PresspeechApp._probe_input(3, 16000))

        stream.stop.assert_called_once_with()
        stream.close.assert_called_once_with()

    def test_microphone_level_probe_distinguishes_silent_samples(self):
        stream = mock.Mock()

        def input_stream(**kwargs):
            stream.start.side_effect = lambda: kwargs["callback"](
                __import__("numpy").zeros((80, 1), dtype="float32"),
                80, None, None)
            return stream

        with mock.patch.object(app.sd, "InputStream", side_effect=input_stream):
            level = app.PresspeechApp._probe_input_level(
                3, 16000, listen_for=0)

        self.assertEqual(level, 0.0)
        stream.stop.assert_called_once_with()
        stream.close.assert_called_once_with()

    def test_microphone_level_probe_reports_meaningful_input(self):
        stream = mock.Mock()

        def input_stream(**kwargs):
            stream.start.side_effect = lambda: kwargs["callback"](
                __import__("numpy").full((80, 1), 0.02, dtype="float32"),
                80, None, None)
            return stream

        with mock.patch.object(app.sd, "InputStream", side_effect=input_stream):
            level = app.PresspeechApp._probe_input_level(
                3, 16000, listen_for=0)

        self.assertAlmostEqual(level, 0.02, places=5)

    def test_setup_probe_invites_speech_only_after_first_audio_buffer(self):
        stream = mock.Mock()
        callback = {}
        ready = mock.Mock()

        def input_stream(**kwargs):
            callback["audio"] = kwargs["callback"]

            def start():
                ready.assert_not_called()
                callback["audio"](
                    app.np.zeros((80, 1), dtype="float32"),
                    80, None, None)

            stream.start.side_effect = start
            return stream

        with mock.patch.object(app.AUDIO_BACKEND, "open_input_stream",
                               side_effect=input_stream):
            self.assertEqual(
                app.PresspeechApp._probe_input_level(
                    3, 16000, listen_for=0, on_listening=ready),
                0.0)

        ready.assert_called_once_with()
        stream.close.assert_called_once_with()

    def test_setup_probe_never_invites_speech_without_audio(self):
        stream = mock.Mock()
        ready = mock.Mock()
        with mock.patch.object(app.AUDIO_BACKEND, "open_input_stream",
                               return_value=stream):
            self.assertIsNone(app.PresspeechApp._probe_input_level(
                3, 16000, listen_for=0, on_listening=ready))

        ready.assert_not_called()
        stream.close.assert_called_once_with()

    def test_closed_setup_status_callback_cannot_fail_a_working_probe(self):
        stream = mock.Mock()

        def input_stream(**kwargs):
            stream.start.side_effect = lambda: kwargs["callback"](
                app.np.zeros((80, 1), dtype="float32"),
                80, None, None)
            return stream

        with mock.patch.object(app.AUDIO_BACKEND, "open_input_stream",
                               side_effect=input_stream):
            self.assertEqual(app.PresspeechApp._probe_input_level(
                3, 16000, listen_for=0,
                on_listening=mock.Mock(side_effect=RuntimeError("closed"))), 0.0)

    def test_setup_check_freshly_probes_without_replacing_cached_input(self):
        instance = self.make_app()
        instance.input_device = (9, 48000)
        instance._probe_input_level = mock.Mock(return_value=0.02)

        def find_input(selected, probe=None):
            self.assertTrue(probe(1, 16000))
            return (1, 16000)

        instance._find_input_device = mock.Mock(side_effect=find_input)

        self.assertEqual(
            instance.check_input_device("MME::USB microphone"),
            app.MICROPHONE_CHECK_LEVEL)

        self.assertEqual(
            instance._find_input_device.call_args.args,
            ("MME::USB microphone",))
        self.assertIn("probe", instance._find_input_device.call_args.kwargs)
        self.assertEqual(instance.input_device, (9, 48000))

    def test_setup_check_passes_audio_readiness_to_probe(self):
        instance = self.make_app()
        ready = mock.Mock()

        def probe_level(_idx, _rate, *, listen_for, on_listening):
            self.assertEqual(listen_for, app.MICROPHONE_CHECK_LISTEN_SEC)
            on_listening()
            return 0.02

        instance._probe_input_level = mock.Mock(side_effect=probe_level)
        instance._find_input_device = mock.Mock(
            side_effect=lambda _selected, probe: (1, 16000)
            if probe(1, 16000) else None)

        self.assertEqual(
            instance.check_input_device("auto", on_listening=ready),
            app.MICROPHONE_CHECK_LEVEL)
        ready.assert_called_once_with()

    def test_setup_check_reports_connected_but_silent_input(self):
        instance = self.make_app()
        instance._probe_input_level = mock.Mock(return_value=0.0)
        instance._find_input_device = mock.Mock(
            side_effect=lambda selected, probe: (1, 16000)
            if probe(1, 16000) else None)

        self.assertEqual(
            instance.check_input_device("auto"),
            app.MICROPHONE_CHECK_SILENT)

    def test_setup_check_recovers_a_reconnected_device_by_rescanning(self):
        instance = self.make_app("MME::USB microphone")
        instance.input_device = (9, 48000)
        instance._cached_input_selector = "MME::USB microphone"
        instance._cached_input_topology = ("old device list",)
        instance._probe_input_level = mock.Mock(return_value=0.02)
        attempts = []

        def find_input(selected, probe=None):
            attempts.append(selected)
            if len(attempts) == 1:
                return None
            self.assertTrue(probe(1, 16000))
            return (1, 16000)

        instance._find_input_device = mock.Mock(side_effect=find_input)

        self.assertEqual(
            instance.check_input_device("MME::USB microphone"),
            app.MICROPHONE_CHECK_LEVEL)

        self.assertEqual(attempts, [
            "MME::USB microphone", "MME::USB microphone"])
        instance._rescan_audio_devices.assert_called_once_with(
            audio_lease=mock.ANY)
        self.assertIsNone(instance.input_device)
        self.assertIsNone(instance._cached_input_selector)
        self.assertIsNone(instance._cached_input_topology)

    def test_setup_check_refreshes_the_real_coordinated_backend(self):
        instance = self.make_app()
        del instance._rescan_audio_devices
        instance._probe_input_level = mock.Mock(return_value=0.02)
        refreshed = [False]

        def devices():
            return DEVICES if refreshed[0] else []

        def initialize():
            refreshed[0] = True

        with mock.patch.object(app.sd, "query_devices", side_effect=devices), \
                mock.patch.object(
                    app.sd, "query_hostapis", return_value=HOST_APIS), \
                mock.patch.object(
                    app.sd, "check_input_settings", return_value=None), \
                mock.patch.object(app.sd, "_terminate", create=True) as terminate, \
                mock.patch.object(
                    app.sd, "_initialize", create=True,
                    side_effect=initialize) as initialize_backend:
            result = instance.check_input_device("auto")

        self.assertEqual(result, app.MICROPHONE_CHECK_LEVEL)
        terminate.assert_called_once_with()
        initialize_backend.assert_called_once_with()

    def test_setup_check_recovers_when_stale_enumeration_raises(self):
        instance = self.make_app()
        instance._probe_input_level = mock.Mock(return_value=0.02)
        attempts = [0]

        def find_input(_selected, probe=None):
            attempts[0] += 1
            if attempts[0] == 1:
                raise OSError("stale backend")
            self.assertTrue(probe(1, 16000))
            return (1, 16000)

        instance._find_input_device = mock.Mock(side_effect=find_input)

        self.assertEqual(
            instance.check_input_device("auto"),
            app.MICROPHONE_CHECK_LEVEL)

        instance._rescan_audio_devices.assert_called_once_with(
            audio_lease=mock.ANY)
        instance._log.assert_not_called()

    def test_setup_check_reports_unavailable_when_rescan_is_vetoed(self):
        instance = self.make_app()
        instance.input_device = (4, 44100)
        instance._find_input_device = mock.Mock(return_value=None)
        instance._rescan_audio_devices.return_value = False

        self.assertEqual(
            instance.check_input_device("auto"),
            app.MICROPHONE_CHECK_UNAVAILABLE)

        instance._find_input_device.assert_called_once()
        self.assertIsNone(instance.input_device)

    def test_setup_check_does_not_invalidate_an_active_recording(self):
        instance = self.make_app()
        instance.input_device = (4, 44100)
        instance.recording = True
        instance.stream = mock.sentinel.active_stream
        instance._find_input_device = mock.Mock(return_value=None)

        self.assertEqual(
            instance.check_input_device("auto"),
            app.MICROPHONE_CHECK_BUSY)

        self.assertEqual(instance.input_device, (4, 44100))
        instance._find_input_device.assert_not_called()
        instance._rescan_audio_devices.assert_not_called()

    def test_microphone_check_claim_defers_hotkey_capture_until_probe_finishes(self):
        instance = self.make_app()
        instance.notify = mock.Mock()
        entered = threading.Event()
        finish = threading.Event()
        result = []

        def slow_probe(_selected, probe=None):
            entered.set()
            self.assertTrue(finish.wait(2))
            return (1, 16000)

        instance._find_input_device = mock.Mock(side_effect=slow_probe)
        worker = threading.Thread(
            target=lambda: result.append(instance.check_input_device("auto")))
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertFalse(instance.start_recording())
            instance.notify.assert_called_once_with(
                "Microphone check in progress",
                "Wait for the microphone check to finish, then start dictation.")
            self.assertFalse(getattr(instance, "_starting_recording", False))
        finally:
            finish.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [app.MICROPHONE_CHECK_SILENT])
        self.assertFalse(instance._microphone_check_in_progress)

    def test_microphone_check_does_not_start_during_recording_transition(self):
        instance = self.make_app()
        instance._starting_recording = True
        instance._find_input_device = mock.Mock()

        self.assertEqual(
            instance.check_input_device("auto"), app.MICROPHONE_CHECK_BUSY)
        instance._find_input_device.assert_not_called()

    def test_setup_check_reports_device_enumeration_failure_safely(self):
        instance = self.make_app()
        instance._find_input_device = mock.Mock(
            side_effect=OSError("private device detail"))
        instance._log = mock.Mock()

        self.assertEqual(
            instance.check_input_device("auto"),
            app.MICROPHONE_CHECK_UNAVAILABLE)

        instance._log.assert_called_once_with(
            "microphone readiness check failed: OSError")
        self.assertNotIn("private device detail", instance._log.call_args.args[0])

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

    def test_startup_recovery_opens_windows_startup_settings(self):
        instance = self.make_app()
        with mock.patch.object(app.os, "startfile", create=True) as startfile:
            self.assertTrue(instance.open_startup_settings())

        startfile.assert_called_once_with("ms-settings:startupapps")


class HotkeyRegressionTests(unittest.TestCase):
    def make_app(self, hotkey="right alt", trigger="hold"):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"hotkey": hotkey, "trigger": trigger}
        instance.lock = threading.Lock()
        instance._key_held = False
        instance._pressed_keys = set()
        instance._held_hotkey_keys = frozenset()
        instance._held_hotkey_trigger = None
        instance._suppress_escape_keyup = False
        instance._filter_pressed_vks = set()
        instance._passthrough_hotkey_vks = set()
        instance._suppressed_hotkey_vks = {}
        instance._injecting_keys = False
        instance.listener = mock.Mock()
        instance.recording = False
        instance.start_recording = mock.Mock()
        instance.request_stop = mock.Mock()
        instance.cancel_recording = mock.Mock()
        instance._log = mock.Mock()
        instance._hotkey_action_generation = 0
        instance._hotkey_action_queue = mock.Mock()
        instance._hotkey_action_queue.put.side_effect = (
            lambda action: instance._perform_hotkey_action(
                action[1], action[2]))
        return instance

    @staticmethod
    def enable_async_hotkey_actions(instance, generation=1):
        instance._hotkey_action_lock = threading.Lock()
        instance._hotkey_transaction_lock = threading.Lock()
        instance._hotkey_action_queue = app.queue.SimpleQueue()
        instance._hotkey_action_generation = generation
        instance._exiting = False
        worker = threading.Thread(
            target=instance._run_hotkey_actions, daemon=True)
        worker.start()
        return worker

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
        self.assertEqual(set(config.HOTKEYS), set(app.HOTKEY_VIRTUAL_KEYS))

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
        # Model the action worker completing cancellation before repeat/key-up.
        instance.recording = False
        instance._win32_event_filter(app.WM_KEYDOWN, event)
        instance._win32_event_filter(app.WM_KEYUP, event)
        instance._win32_event_filter(app.WM_KEYUP, event)

        self.assertEqual(instance.listener.suppress_event.call_count, 3)
        instance.cancel_recording.assert_called_once_with()
        self.assertFalse(instance._suppress_escape_keyup)
        self.assertNotIn(app.pkb.Key.esc, instance._pressed_keys)

    def test_escape_filter_ignores_other_virtual_keys(self):
        instance = self.make_app()
        instance.recording = True

        instance._win32_event_filter(
            app.WM_KEYDOWN, mock.Mock(vkCode=0x41))

        instance.listener.suppress_event.assert_not_called()

    def test_configured_hotkey_is_dispatched_but_withheld_from_focused_app(self):
        instance = self.make_app(hotkey="f8")
        event = mock.Mock(vkCode=0x77, flags=0)

        self.assertFalse(instance._win32_event_filter(app.WM_KEYDOWN, event))
        self.assertFalse(instance._win32_event_filter(app.WM_KEYUP, event))

        instance.start_recording.assert_called_once_with()
        instance.request_stop.assert_called_once_with()
        self.assertEqual(instance.listener.suppress_event.call_count, 2)
        self.assertEqual(instance._suppressed_hotkey_vks, {})

    def test_native_hook_returns_while_slow_hotkey_action_runs_on_worker(self):
        instance = self.make_app(hotkey="f8")
        self.enable_async_hotkey_actions(instance)
        action_entered = threading.Event()
        release_action = threading.Event()
        filter_returned = threading.Event()

        def slow_start():
            action_entered.set()
            release_action.wait(2)

        instance.start_recording.side_effect = slow_start
        event = mock.Mock(vkCode=0x77, flags=0)

        def invoke_filter():
            instance._win32_event_filter(app.WM_KEYDOWN, event)
            filter_returned.set()

        caller = threading.Thread(target=invoke_filter)
        caller.start()
        try:
            self.assertTrue(filter_returned.wait(1))
            self.assertTrue(action_entered.wait(1))
            self.assertFalse(release_action.is_set())
            instance.listener.suppress_event.assert_called_once_with()
        finally:
            release_action.set()
            caller.join(2)

    def test_worker_preserves_press_release_order(self):
        instance = self.make_app(hotkey="f8")
        self.enable_async_hotkey_actions(instance)
        actions = []
        released = threading.Event()
        instance.start_recording.side_effect = lambda: actions.append("press")

        def stop():
            actions.append("release")
            released.set()

        instance.request_stop.side_effect = stop
        event = mock.Mock(vkCode=0x77, flags=0)

        instance._win32_event_filter(app.WM_KEYDOWN, event)
        instance._win32_event_filter(app.WM_KEYUP, event)

        self.assertTrue(released.wait(1))
        self.assertEqual(actions, ["press", "release"])

    def test_listener_reset_retires_queued_actions_from_old_generation(self):
        instance = self.make_app(hotkey="f8")
        instance._hotkey_action_lock = threading.Lock()
        instance._hotkey_transaction_lock = threading.Lock()
        instance._hotkey_action_queue = app.queue.SimpleQueue()
        instance._hotkey_action_generation = 4
        instance._exiting = False
        instance._hotkey_action_queue.put(
            (4, instance._on_press, app.pkb.Key.f8))

        instance._reset_hotkey_transaction()
        current_action = threading.Event()
        instance._hotkey_action_queue.put(
            (5, lambda _key: current_action.set(), None))
        threading.Thread(
            target=instance._run_hotkey_actions, daemon=True).start()

        self.assertTrue(current_action.wait(1))
        instance.start_recording.assert_not_called()

    def test_repair_retires_a_queued_press_before_replacing_listener(self):
        instance = self.make_app(hotkey="f8")
        instance._hotkey_action_lock = threading.Lock()
        instance._hotkey_transaction_lock = threading.Lock()
        instance._hotkey_action_queue = app.queue.SimpleQueue()
        instance._hotkey_action_generation = 4
        instance._exiting = False
        instance._hotkey_action_queue.put(
            (4, instance._on_press, app.pkb.Key.f8))
        instance._start_hotkey_listener = mock.Mock(return_value=True)
        instance.notify = mock.Mock()

        self.assertTrue(instance.repair_hotkey())
        current_action = threading.Event()
        instance._hotkey_action_queue.put(
            (5, lambda _key: current_action.set(), None))
        threading.Thread(
            target=instance._run_hotkey_actions, daemon=True).start()

        self.assertTrue(current_action.wait(1))
        self.assertEqual(instance._hotkey_action_generation, 5)
        instance.start_recording.assert_not_called()
        instance._start_hotkey_listener.assert_called_once_with(force=True)

    def test_repair_does_not_retire_actions_during_active_dictation(self):
        instance = self.make_app(hotkey="f8")
        instance._hotkey_action_lock = threading.Lock()
        instance._hotkey_transaction_lock = threading.Lock()
        instance._hotkey_action_generation = 4
        instance.recording = True
        instance._start_hotkey_listener = mock.Mock()
        instance.notify = mock.Mock()

        self.assertFalse(instance.repair_hotkey())

        self.assertEqual(instance._hotkey_action_generation, 4)
        instance._start_hotkey_listener.assert_not_called()
        instance.notify.assert_called_once()

    def test_repair_does_not_retire_a_recording_start_in_progress(self):
        instance = self.make_app(hotkey="f8")
        instance._hotkey_action_lock = threading.Lock()
        instance._hotkey_transaction_lock = threading.Lock()
        instance._hotkey_action_generation = 4
        instance._starting_recording = True
        instance._start_hotkey_listener = mock.Mock()
        instance.notify = mock.Mock()

        self.assertFalse(instance.repair_hotkey())

        self.assertEqual(instance._hotkey_action_generation, 4)
        instance._start_hotkey_listener.assert_not_called()
        instance.notify.assert_called_once_with(
            "Finish the active dictation first",
            "Stop or cancel dictation, then choose Repair Global Hotkey.")

    def test_listener_reset_cannot_race_a_raw_filter_transaction(self):
        instance = self.make_app(hotkey="f8")
        instance._hotkey_action_lock = threading.Lock()
        instance._hotkey_transaction_lock = threading.Lock()
        instance._hotkey_action_generation = 4
        queue_entered = threading.Event()
        release_queue = threading.Event()
        reset_finished = threading.Event()

        def blocked_enqueue(_callback, _key, _generation):
            queue_entered.set()
            release_queue.wait(2)

        instance._queue_hotkey_action = blocked_enqueue
        event = mock.Mock(vkCode=0x77, flags=0)
        filter_thread = threading.Thread(
            target=instance._win32_event_filter,
            args=(app.WM_KEYDOWN, event),
        )
        filter_thread.start()
        self.assertTrue(queue_entered.wait(1))

        reset_thread = threading.Thread(
            target=lambda: (
                instance._reset_hotkey_transaction(), reset_finished.set()))
        reset_thread.start()
        try:
            self.assertFalse(reset_finished.wait(0.05))
        finally:
            release_queue.set()
            filter_thread.join(2)
            reset_thread.join(2)

        self.assertTrue(reset_finished.is_set())
        self.assertEqual(instance._hotkey_action_generation, 5)
        self.assertEqual(instance._suppressed_hotkey_vks, {})
        self.assertEqual(instance._filter_pressed_vks, set())

    def test_stale_listener_filter_does_not_touch_replacement_state(self):
        instance = self.make_app(hotkey="f8")
        instance._hotkey_transaction_lock = threading.Lock()
        instance._hotkey_action_generation = 5
        stale_listener = mock.Mock()
        event = mock.Mock(vkCode=0x77, flags=0)

        instance._win32_event_filter(
            app.WM_KEYDOWN, event, listener=stale_listener, generation=4)

        stale_listener.suppress_event.assert_not_called()
        instance.start_recording.assert_not_called()
        self.assertEqual(instance._suppressed_hotkey_vks, {})
        self.assertEqual(instance._filter_pressed_vks, set())

    def test_hotkey_action_failure_still_withholds_reserved_key(self):
        instance = self.make_app(hotkey="f8")
        instance.start_recording.side_effect = RuntimeError("test failure")
        event = mock.Mock(vkCode=0x77, flags=0)

        self.assertFalse(instance._win32_event_filter(app.WM_KEYDOWN, event))

        instance.listener.suppress_event.assert_called_once_with()
        self.assertEqual(
            instance._log.call_args_list[-1],
            mock.call("reserved hotkey action failed: RuntimeError"))

    def test_hotkey_queue_failure_still_withholds_reserved_key(self):
        instance = self.make_app(hotkey="f8")
        instance._hotkey_action_generation = 2
        instance._hotkey_action_queue = mock.Mock()
        instance._hotkey_action_queue.put.side_effect = MemoryError("private")
        event = mock.Mock(vkCode=0x77, flags=0)

        self.assertFalse(instance._win32_event_filter(app.WM_KEYDOWN, event))

        instance.listener.suppress_event.assert_called_once_with()
        instance.start_recording.assert_not_called()
        self.assertEqual(instance._hotkey_status, "error")
        self.assertNotIn("private", instance._hotkey_status_detail)

    def test_suppressed_keyup_completes_original_transaction_after_setting_change(self):
        instance = self.make_app(hotkey="left win")
        down = mock.Mock(vkCode=0x5B, flags=0)

        instance._win32_event_filter(app.WM_KEYDOWN, down)
        instance.settings.update({"hotkey": "f8", "trigger": "toggle"})
        instance._win32_event_filter(app.WM_KEYUP, down)

        instance.start_recording.assert_called_once_with()
        instance.request_stop.assert_called_once_with()
        self.assertFalse(instance._key_held)
        self.assertEqual(instance._suppressed_hotkey_vks, {})

    def test_hotkey_autorepeat_is_suppressed_without_restarting_dictation(self):
        instance = self.make_app(hotkey="f9")
        event = mock.Mock(vkCode=0x78, flags=0)

        instance._win32_event_filter(app.WM_KEYDOWN, event)
        instance._win32_event_filter(app.WM_KEYDOWN, event)
        instance._win32_event_filter(app.WM_KEYUP, event)

        instance.start_recording.assert_called_once_with()
        instance.request_stop.assert_called_once_with()
        self.assertEqual(instance.listener.suppress_event.call_count, 3)

    def test_injected_ctrl_for_paste_is_never_treated_as_physical_hotkey(self):
        instance = self.make_app(hotkey="left ctrl")
        for flags in (app.LLKHF_INJECTED, app.LLKHF_LOWER_IL_INJECTED):
            event = mock.Mock(vkCode=0xA2, flags=flags)
            instance._win32_event_filter(app.WM_KEYDOWN, event)
            instance._win32_event_filter(app.WM_KEYUP, event)

        instance.start_recording.assert_not_called()
        instance.request_stop.assert_not_called()
        instance.listener.suppress_event.assert_not_called()

    def test_altgr_right_alt_transaction_is_not_suppressed(self):
        instance = self.make_app(hotkey="right alt")
        ctrl = mock.Mock(vkCode=0xA2, flags=0)
        alt = mock.Mock(vkCode=0xA5, flags=0)

        # The hook can receive Right Alt before pynput has delivered its queued
        # Left Ctrl callback, so detection must use the raw filter state.
        instance._win32_event_filter(app.WM_KEYDOWN, ctrl)
        instance._win32_event_filter(app.WM_SYSKEYDOWN, alt)
        instance._win32_event_filter(app.WM_SYSKEYDOWN, alt)
        instance._win32_event_filter(app.WM_KEYUP, ctrl)
        instance._win32_event_filter(app.WM_SYSKEYUP, alt)

        instance.start_recording.assert_not_called()
        instance.request_stop.assert_not_called()
        instance.listener.suppress_event.assert_not_called()
        self.assertEqual(instance._passthrough_hotkey_vks, set())
        self.assertEqual(instance._filter_pressed_vks, set())

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


class HotkeyListenerLifecycleTests(unittest.TestCase):
    def make_app(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"hotkey": "f8", "trigger": "hold"}
        instance.lock = threading.Lock()
        instance.listener = None
        instance._hotkey_listener_lock = app.threading.Lock()
        instance._hotkey_status = "not started"
        instance._hotkey_status_detail = "Global hotkey has not started"
        instance._exiting = False
        instance._key_held = False
        instance._pressed_keys = set()
        instance._held_hotkey_keys = frozenset()
        instance._held_hotkey_trigger = None
        instance._suppress_escape_keyup = False
        instance._filter_pressed_vks = set()
        instance._passthrough_hotkey_vks = set()
        instance._suppressed_hotkey_vks = {}
        instance._log = mock.Mock()
        instance.notify = mock.Mock()
        return instance

    def test_listener_start_is_observed_and_reports_ready(self):
        instance = self.make_app()
        listener = mock.Mock()
        listener.is_alive.return_value = True

        with mock.patch.object(app.pkb, "Listener", return_value=listener), \
                mock.patch.object(app.threading, "Thread") as thread:
            self.assertTrue(instance._start_hotkey_listener())

        listener.start.assert_called_once_with()
        self.assertEqual(instance.hotkey_listener_status(), ("ready", "Ready — F8"))
        self.assertIs(instance.listener, listener)
        thread.assert_called_once_with(
            target=instance._watch_hotkey_listener,
            args=(listener,),
            name="presspeech-hotkey-watch",
            daemon=True,
        )
        thread.return_value.start.assert_called_once_with()

    def test_listener_start_failure_keeps_app_recoverable(self):
        instance = self.make_app()

        with mock.patch.object(
                app.pkb, "Listener", side_effect=OSError("private details")):
            self.assertFalse(instance._start_hotkey_listener())

        self.assertEqual(instance.hotkey_listener_status()[0], "error")
        self.assertIn("Repair Global Hotkey", instance.hotkey_listener_status()[1])
        instance._log.assert_called_once_with(
            "global hotkey could not start: OSError")

    def test_listener_callback_failure_becomes_visible_without_private_detail(self):
        instance = self.make_app()
        listener = mock.Mock()
        listener.join.side_effect = RuntimeError("pressed secret key")
        instance.listener = listener
        instance._hotkey_status = "ready"
        instance._key_held = True
        instance._pressed_keys.add(app.pkb.Key.f8)

        instance._watch_hotkey_listener(listener)

        self.assertEqual(instance.hotkey_listener_status()[0], "error")
        self.assertFalse(instance._key_held)
        self.assertEqual(instance._pressed_keys, set())
        instance._log.assert_called_once_with(
            "global hotkey listener stopped: RuntimeError")
        self.assertNotIn("secret", instance._log.call_args.args[0])
        instance.notify.assert_called_once()

    def test_user_repair_reports_success(self):
        instance = self.make_app()
        with mock.patch.object(
                instance, "_start_hotkey_listener", return_value=True) as start:
            self.assertTrue(instance.repair_hotkey())

        start.assert_called_once_with(force=True)
        instance.notify.assert_called_once_with(
            "Global hotkey ready", "F8 is ready for dictation.")

    def test_user_repair_replaces_an_apparently_alive_listener(self):
        instance = self.make_app()
        old_listener = mock.Mock()
        instance.listener = old_listener
        instance._hotkey_status = "ready"
        new_listener = mock.Mock()

        with mock.patch.object(app.pkb, "Listener", return_value=new_listener), \
                mock.patch.object(app.threading, "Thread"):
            self.assertTrue(instance._start_hotkey_listener(force=True))

        old_listener.stop.assert_called_once_with()
        new_listener.start.assert_called_once_with()
        self.assertIs(instance.listener, new_listener)

    def test_user_repair_does_not_interrupt_active_dictation(self):
        instance = self.make_app()
        instance.recording = True
        with mock.patch.object(instance, "_start_hotkey_listener") as start:
            self.assertFalse(instance.repair_hotkey())

        start.assert_not_called()
        instance.notify.assert_called_once_with(
            "Finish the active dictation first",
            "Stop or cancel dictation, then choose Repair Global Hotkey.")

    def test_repair_prevents_a_tray_start_during_listener_replacement(self):
        instance = self.make_app()
        instance._hotkey_action_lock = threading.Lock()
        instance._hotkey_transaction_lock = threading.Lock()
        instance._hotkey_action_generation = 1
        instance.recording = False
        starts = []

        def replace(*, force):
            self.assertTrue(force)
            starts.append(instance.start_recording())
            return True

        instance._start_hotkey_listener = mock.Mock(side_effect=replace)
        self.assertEqual(instance._attempt_hotkey_repair(), "ready")
        self.assertEqual(starts, [False])
        self.assertFalse(instance._hotkey_repairing)

    def test_lock_discards_capture_and_rejects_a_new_start(self):
        instance = self.make_app()
        instance.cancel_recording = mock.Mock(return_value=True)

        instance._on_windows_session_pause()

        self.assertFalse(instance._session_available)
        instance.cancel_recording.assert_called_once_with()
        self.assertFalse(instance.start_recording())
        instance._log.assert_called_once_with(
            "Windows session unavailable; active capture discarded")

    def test_pause_during_start_rejects_late_recording_claim(self):
        instance = self.make_app()
        instance.recording = False
        instance.cancel_recording = mock.Mock(return_value=False)
        instance.has_undelivered_dictation = mock.Mock(return_value=False)
        instance._dictation_model_ready = mock.Mock(return_value=True)
        instance._on_windows_session_pause()

        with mock.patch.object(app, "_foreground_paste_target"):
            self.assertFalse(instance._start_recording_claimed())
        self.assertFalse(instance.recording)

    def test_unlock_replaces_even_an_apparently_ready_hook_without_toast(self):
        instance = self.make_app()
        instance._session_repair_lock = threading.Lock()
        instance._session_repair_generation = 0
        instance._session_repair_running = False
        instance._session_available = False
        instance._hotkey_status = "ready"
        instance._attempt_hotkey_repair = mock.Mock(return_value="ready")

        with mock.patch.object(app.threading, "Thread") as thread:
            instance._on_windows_session_resume()
        thread.return_value.start.assert_called_once_with()
        self.assertEqual(instance.hotkey_listener_status()[0], "starting")
        self.assertTrue(instance._session_available)

        instance._repair_hotkey_after_session()

        instance._attempt_hotkey_repair.assert_called_once_with()
        self.assertFalse(instance._session_repair_running)
        instance.notify.assert_not_called()
        instance._log.assert_called_once_with(
            "global hotkey reconnected after Windows session change")

    def test_resume_waits_for_cleanup_and_rechecks_newer_session_event(self):
        instance = self.make_app()
        instance._session_repair_lock = threading.Lock()
        instance._session_repair_generation = 0
        instance._session_repair_running = False
        outcomes = iter(("busy", "ready", "ready"))

        def attempt():
            outcome = next(outcomes)
            if outcome == "ready" and instance._session_repair_generation == 1:
                instance._on_windows_session_resume()
            return outcome

        instance._attempt_hotkey_repair = mock.Mock(side_effect=attempt)
        with mock.patch.object(app.threading, "Thread"), \
                mock.patch.object(app.time, "sleep") as sleep:
            instance._on_windows_session_resume()
            instance._repair_hotkey_after_session()

        self.assertEqual(instance._attempt_hotkey_repair.call_count, 3)
        sleep.assert_called_once_with(0.25)
        self.assertFalse(instance._session_repair_running)
        instance.notify.assert_not_called()

    def test_session_repair_timeout_exposes_manual_fallback(self):
        instance = self.make_app()
        instance._session_repair_lock = threading.Lock()
        instance._session_repair_generation = 1
        instance._session_repair_running = True
        instance._attempt_hotkey_repair = mock.Mock(return_value="busy")

        with mock.patch.object(app.time, "monotonic", side_effect=(0, 61)):
            instance._repair_hotkey_after_session()

        self.assertEqual(instance.hotkey_listener_status()[0], "error")
        self.assertFalse(instance._session_repair_running)
        instance.notify.assert_called_once_with(
            "Global hotkey needs repair",
            "Finish dictation, then choose Repair Global Hotkey "
            "from the notification-area menu.")


class AudioResamplingTests(unittest.TestCase):
    @staticmethod
    def tone(sample_rate, frequency, seconds=1.0):
        numpy = __import__("numpy")
        times = numpy.arange(
            int(sample_rate * seconds), dtype="float32") / sample_rate
        return numpy.sin(2 * numpy.pi * frequency * times).astype("float32")

    @staticmethod
    def rms(audio):
        numpy = __import__("numpy")
        return float(numpy.sqrt(numpy.mean(audio * audio)))

    def test_native_asr_rate_is_not_copied(self):
        audio = self.tone(16000, 1000, seconds=0.1)

        self.assertIs(app._resample_to_16k(audio, 16000), audio)

    def test_tiny_capture_does_not_enter_the_resampler(self):
        numpy = __import__("numpy")
        audio = numpy.array([0.25], dtype="float32")

        with mock.patch.object(app.soxr, "resample") as resample:
            result = app._resample_to_16k(audio, 48000)

        numpy.testing.assert_array_equal(result, audio)
        resample.assert_not_called()

    def test_common_microphone_rates_preserve_duration_and_dtype(self):
        for sample_rate in (44100, 48000, 96000):
            with self.subTest(sample_rate=sample_rate):
                audio = self.tone(sample_rate, 1000)

                result = app._resample_to_16k(audio, sample_rate)

                self.assertEqual(len(result), 16000)
                self.assertEqual(result.dtype.name, "float32")

    def test_resampling_preserves_speech_band(self):
        audio = self.tone(48000, 7000)

        result = app._resample_to_16k(audio, 48000)

        self.assertGreater(self.rms(result), self.rms(audio) * 0.98)

    def test_resampling_rejects_content_above_asr_nyquist(self):
        audio = self.tone(48000, 10000)

        result = app._resample_to_16k(audio, 48000)

        self.assertLess(self.rms(result), 0.01)


class TextRegressionTests(unittest.TestCase):
    def setUp(self):
        # Delivery failures now open a real Tk recovery surface. Keep these
        # lower-level text/target tests headless while still exercising the
        # app's open-or-present control flow around a test double.
        patcher = mock.patch.object(app.ui, "DeliveryRecoveryWindow")
        self.delivery_recovery_window = patcher.start()
        self.addCleanup(patcher.stop)

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
                                  side_effect=load_module) as importer, \
                mock.patch.object(
                    app.model_network, "harden_loaded_runtime") as harden:
            app._package_selftest()

        self.assertEqual(
            [call.args[0] for call in importer.call_args_list],
            [name for name, _symbols in app.PACKAGE_SMOKE_IMPORTS],
        )
        self.assertEqual(set(loaded), {
            "comtypes", "ctranslate2", "faster_whisper", "librosa",
            "hf_xet", "huggingface_hub.constants", "huggingface_hub.utils",
            "huggingface_hub.file_download",
            "model_integrity",
            "onnxruntime",
            "pycaw.constants", "pycaw.pycaw", "safetensors", "sentencepiece",
            "soundfile", "soxr", "tokenizers", "torch", "tk_uia",
            "transformers", "transformers.utils.hub",
        })
        harden.assert_called_once_with(require_loaded=True)

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
        instance._audio_sequence = 9
        instance._last_audio_callback_started_at = 9.0
        instance._stop_before_ready = True
        instance._model_idle_epoch = 0
        instance.settings = {"model": "parakeet-tdt-0.6b-v3"}
        instance.model_status = "ready"
        instance.transcriber = mock.Mock()
        instance.transcriber.loaded.return_value = True
        instance.indicator = mock.Mock()
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
        self.assertEqual(instance._audio_sequence, 0)
        self.assertEqual(instance._last_audio_callback_started_at, 0.0)
        self.assertFalse(instance._capture_ready)
        self.assertFalse(instance._stop_before_ready)
        self.assertFalse(instance._first_audio_callback.is_set())
        instance.indicator.show.assert_called_once_with("connecting")
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

    def test_closed_scratchpad_transcript_is_retained_not_pasted(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        original_scratchpad = mock.Mock()
        original_scratchpad.root = None
        instance.scratchpad = None
        instance._paste = mock.Mock()
        instance._remember_undelivered_dictation = mock.Mock()

        instance._deliver_text(
            "private test transcript", app.PasteTarget("notepad.exe", 1234),
            original_scratchpad)

        original_scratchpad.append_text.assert_not_called()
        instance._paste.assert_not_called()
        instance._remember_undelivered_dictation.assert_called_once_with(
            "private test transcript", "scratchpad-unavailable")

    def test_replaced_scratchpad_cannot_receive_old_transcript(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        original_scratchpad = mock.Mock()
        original_scratchpad.root = mock.Mock()
        instance.scratchpad = mock.Mock()
        instance._paste = mock.Mock()
        instance._remember_undelivered_dictation = mock.Mock()

        instance._deliver_text(
            "private test transcript", app.PasteTarget("presspeech.exe", 1234),
            original_scratchpad)

        original_scratchpad.append_text.assert_not_called()
        instance.scratchpad.append_text.assert_not_called()
        instance._paste.assert_not_called()
        instance._remember_undelivered_dictation.assert_called_once_with(
            "private test transcript", "scratchpad-unavailable")

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

    def test_child_focus_change_inside_scratchpad_stays_private(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        scratchpad = mock.Mock()
        scratchpad.root = mock.Mock()
        instance.scratchpad = scratchpad
        instance._paste = mock.Mock()
        target = app.PasteTarget(
            "presspeech.exe", 1234, app.os.getpid(), 0, 501)
        another_control = target._replace(focus_handle=502)

        with mock.patch.object(
                app, "_foreground_paste_target", return_value=another_control):
            instance._deliver_text(
                "private test transcript", target, scratchpad)

        scratchpad.append_text.assert_called_once_with(
            "private test transcript")
        instance._paste.assert_not_called()

    def test_failed_scratchpad_append_retains_text_without_pasting(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        scratchpad = mock.Mock()
        scratchpad.root = mock.Mock()
        scratchpad.append_text.side_effect = RuntimeError("synthetic UI error")
        instance.scratchpad = scratchpad
        instance._paste = mock.Mock()
        instance._remember_undelivered_dictation = mock.Mock()
        target = app.PasteTarget("presspeech.exe", 1234, app.os.getpid())

        with mock.patch.object(
                app, "_foreground_paste_target", return_value=target):
            instance._deliver_text("private test transcript", target, scratchpad)

        instance._paste.assert_not_called()
        instance._remember_undelivered_dictation.assert_called_once_with(
            "private test transcript", "scratchpad-unavailable")

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

    def test_known_focus_change_retains_without_replacing_clipboard(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance._log = mock.Mock()
        instance.notify = mock.Mock()
        target = app.PasteTarget("notepad.exe", 1234)

        with mock.patch.object(app.clipboard_delivery, "is_current", return_value=True), \
                mock.patch.object(app.clipboard_delivery, "write_text") as copy, \
                mock.patch.object(app.time, "sleep"), \
                mock.patch.object(
                    app, "_foreground_paste_target",
                    return_value=app.PasteTarget("calculator.exe", 5678)), \
                mock.patch.object(
                    app.keyboard_delivery, "Controller") as controller:
            instance._paste("private transcript", target)

        copy.assert_not_called()
        controller.assert_not_called()
        self.assertEqual(instance._undelivered_dictations, ["private transcript"])
        instance.notify.assert_called_once()
        logged = str(instance._log.mock_calls)
        self.assertIn("paste skipped; original target could not be verified", logged)
        self.assertNotIn("notepad.exe", logged)
        self.assertNotIn("calculator.exe", logged)

    def test_missing_recording_target_retains_without_replacing_clipboard(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance._log = mock.Mock()
        instance.notify = mock.Mock()

        with mock.patch.object(app.clipboard_delivery, "is_current", return_value=True), \
                mock.patch.object(app.clipboard_delivery, "write_text") as copy, \
                mock.patch.object(app.time, "sleep") as sleep, \
                mock.patch.object(
                    app, "_foreground_paste_target") as foreground, \
                mock.patch.object(
                    app.keyboard_delivery, "Controller") as controller:
            instance._paste("private transcript", app.PasteTarget("", 0))

        copy.assert_not_called()
        sleep.assert_not_called()
        foreground.assert_not_called()
        controller.assert_not_called()
        self.assertEqual(instance._undelivered_dictations, ["private transcript"])
        instance.notify.assert_called_once()

    def test_reused_window_handle_from_another_process_never_receives_paste(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance._log = mock.Mock()
        instance.notify = mock.Mock()
        target = app.PasteTarget("notepad.exe", 1234, 41)
        replacement = app.PasteTarget("notepad.exe", 1234, 42)

        with mock.patch.object(app.clipboard_delivery, "is_current", return_value=True), \
                mock.patch.object(app.clipboard_delivery, "write_text") as copy, \
                mock.patch.object(app.time, "sleep"), \
                mock.patch.object(
                    app, "_foreground_paste_target",
                    return_value=replacement), \
                mock.patch.object(
                    app.keyboard_delivery, "Controller") as controller:
            instance._paste("private transcript", target)

        copy.assert_not_called()
        controller.assert_not_called()
        self.assertEqual(instance._undelivered_dictations, ["private transcript"])
        instance.notify.assert_called_once()

    def test_focus_moves_to_another_control_in_same_window_without_paste(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance._log = mock.Mock()
        instance.notify = mock.Mock()
        original = app.PasteTarget("notepad.exe", 1234, 41, 0, 501)
        other_control = app.PasteTarget("notepad.exe", 1234, 41, 0, 502)

        with mock.patch.object(app.clipboard_delivery, "is_current", return_value=True), \
                mock.patch.object(app.clipboard_delivery, "write_text") as copy, \
                mock.patch.object(app.time, "sleep"), \
                mock.patch.object(
                    app, "_foreground_paste_target", return_value=other_control), \
                mock.patch.object(
                    app.keyboard_delivery, "Controller") as controller:
            self.assertFalse(instance._paste("private transcript", original))

        copy.assert_not_called()
        controller.assert_not_called()
        self.assertEqual(instance._undelivered_dictations, ["private transcript"])
        instance.notify.assert_called_once()

    def test_later_focus_identity_cannot_authorize_uncaptured_control(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance._log = mock.Mock()
        instance.notify = mock.Mock()
        original = app.PasteTarget("notepad.exe", 1234, 41, 0, 0)
        later_control = app.PasteTarget("notepad.exe", 1234, 41, 0, 501)

        with mock.patch.object(app.clipboard_delivery, "is_current", return_value=True), \
                mock.patch.object(app.clipboard_delivery, "write_text") as copy, \
                mock.patch.object(app.time, "sleep"), \
                mock.patch.object(
                    app, "_foreground_paste_target", return_value=later_control), \
                mock.patch.object(
                    app.keyboard_delivery, "Controller") as controller:
            self.assertFalse(instance._paste("private transcript", original))

        copy.assert_not_called()
        controller.assert_not_called()
        self.assertEqual(instance._undelivered_dictations, ["private transcript"])
        self.assertIn("focused control could not be verified",
                      str(instance.notify.mock_calls))
        self.assertNotIn("notepad.exe", str(instance._log.mock_calls))

    def test_focus_change_before_shortcut_never_emits_paste_key(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance._injecting_keys = False
        instance._log = mock.Mock()
        instance.notify = mock.Mock()
        # Moonlight has the longest shortcut (Ctrl+Alt+Shift+V), making
        # modifier cleanup and the final pre-V check especially important.
        target = app.PasteTarget("moonlight.exe", 1234, 41, 0x2000)
        replacement = app.PasteTarget("calculator.exe", 5678, 42)

        with mock.patch.object(app.clipboard_delivery, "is_current", return_value=True), \
                mock.patch.object(app.clipboard_delivery, "write_text") as copy, \
                mock.patch.object(app.time, "sleep"), \
                mock.patch.object(
                    app, "_foreground_paste_target",
                    side_effect=[target, replacement]), \
                mock.patch.object(app.keyboard_delivery, "paste_keys_held",
                                  return_value=False), \
                mock.patch.object(
                    app, "_process_integrity_level", return_value=0x2000), \
                mock.patch.object(
                    app.keyboard_delivery, "Controller") as controller:
            instance._paste("private transcript", target)

        keyboard = controller.return_value
        copy.assert_called_once_with("private transcript")
        keyboard.shortcut.assert_not_called()
        keyboard.release.assert_not_called()
        self.assertFalse(instance._injecting_keys)
        self.assertEqual(instance._undelivered_dictations, ["private transcript"])
        instance.notify.assert_called_once()

    def test_original_window_still_receives_paste(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance._injecting_keys = False
        instance._log = mock.Mock()
        target = app.PasteTarget("notepad.exe", 1234, 41, 0x2000)

        with mock.patch.object(app.clipboard_delivery, "is_current", return_value=True), \
                mock.patch.object(app.clipboard_delivery, "write_text"), \
                mock.patch.object(app.time, "sleep"), \
                mock.patch.object(
                    app, "_foreground_paste_target", return_value=target), \
                mock.patch.object(app.keyboard_delivery, "paste_keys_held",
                                  return_value=False), \
                mock.patch.object(
                    app, "_process_integrity_level", return_value=0x2000), \
                mock.patch.object(
                    app.keyboard_delivery, "Controller") as controller:
            instance._paste("transcript", target)

        keyboard = controller.return_value
        keyboard.shortcut.assert_called_once()
        self.assertEqual(keyboard.shortcut.call_args.args, (
            [app.keyboard_delivery.VK_LCONTROL], app.keyboard_delivery.VK_V))
        self.assertTrue(callable(
            keyboard.shortcut.call_args.kwargs["before_submit"]))
        keyboard.release.assert_not_called()

    def test_higher_integrity_target_retains_without_replacing_clipboard(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance._log = mock.Mock()
        instance.notify = mock.Mock()
        target = app.PasteTarget("admin-tool.exe", 1234, 41, 0x3000)

        with mock.patch.object(app.clipboard_delivery, "is_current", return_value=True), \
                mock.patch.object(app.clipboard_delivery, "write_text") as copy, \
                mock.patch.object(app.time, "sleep"), \
                mock.patch.object(
                    app, "_foreground_paste_target", return_value=target), \
                mock.patch.object(
                    app, "_process_integrity_level", return_value=0x2000), \
                mock.patch.object(
                    app.keyboard_delivery, "Controller") as controller:
            instance._paste("private transcript", target)

        copy.assert_not_called()
        controller.assert_not_called()
        self.assertEqual(instance._undelivered_dictations, ["private transcript"])
        instance.notify.assert_called_once()

    def test_equal_integrity_target_still_receives_paste(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance._injecting_keys = False
        instance._log = mock.Mock()
        target = app.PasteTarget("notepad.exe", 1234, 41, 0x2000)

        with mock.patch.object(app.clipboard_delivery, "is_current", return_value=True), \
                mock.patch.object(app.clipboard_delivery, "write_text"), \
                mock.patch.object(app.time, "sleep"), \
                mock.patch.object(
                    app, "_foreground_paste_target", return_value=target), \
                mock.patch.object(
                    app, "_process_integrity_level", return_value=0x2000), \
                mock.patch.object(app.keyboard_delivery, "paste_keys_held",
                                  return_value=False), \
                mock.patch.object(
                    app.keyboard_delivery, "Controller") as controller:
            instance._paste("transcript", target)

        keyboard = controller.return_value
        keyboard.shortcut.assert_called_once()
        self.assertEqual(keyboard.shortcut.call_args.args, (
            [app.keyboard_delivery.VK_LCONTROL], app.keyboard_delivery.VK_V))
        self.assertTrue(callable(
            keyboard.shortcut.call_args.kwargs["before_submit"]))

    def test_unknown_source_integrity_retains_without_replacing_clipboard(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance._log = mock.Mock()
        instance.notify = mock.Mock()
        instance.open_delivery_recovery = mock.Mock(return_value=True)
        target = app.PasteTarget("notepad.exe", 1234, 41, 0x2000)

        with mock.patch.object(app.clipboard_delivery, "write_text") as copy, \
                mock.patch.object(
                    app, "_foreground_paste_target", return_value=target), \
                mock.patch.object(
                    app, "_process_integrity_level", return_value=0), \
                mock.patch.object(
                    app.keyboard_delivery, "Controller") as controller:
            self.assertFalse(instance._paste("private transcript", target))

        copy.assert_not_called()
        controller.assert_not_called()
        self.assertEqual(instance._undelivered_dictations, ["private transcript"])
        self.assertIn("input privilege boundary blocks automatic paste or could not be verified",
                      str(instance.notify.mock_calls))
        self.assertNotIn("private transcript", str(instance._log.mock_calls))

    def test_unknown_target_integrity_blocks_unverified_input(self):
        unknown_target = app.PasteTarget("notepad.exe", 1234, 41)

        with mock.patch.object(
                app, "_process_integrity_level") as process_integrity:
            self.assertTrue(
                app._paste_target_blocks_simulated_input(unknown_target))

        process_integrity.assert_not_called()

        known_target = app.PasteTarget("admin-tool.exe", 1234, 42, 0x3000)
        with mock.patch.object(
                app, "_process_integrity_level", return_value=0) as source:
            self.assertTrue(
                app._paste_target_blocks_simulated_input(known_target))

        source.assert_called_once_with(app.os.getpid())

    def test_unknown_target_integrity_retains_without_replacing_clipboard(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance._log = mock.Mock()
        instance.notify = mock.Mock()
        instance.open_delivery_recovery = mock.Mock(return_value=True)
        target = app.PasteTarget("notepad.exe", 1234, 41)

        with mock.patch.object(
                app, "_foreground_paste_target", return_value=target), \
                mock.patch.object(
                    app.clipboard_delivery, "write_text") as copy, \
                mock.patch.object(
                    app.keyboard_delivery, "Controller") as controller:
            self.assertFalse(instance._paste("private transcript", target))

        copy.assert_not_called()
        controller.assert_not_called()
        self.assertEqual(instance._undelivered_dictations, ["private transcript"])
        self.assertIn("input privilege boundary blocks automatic paste or could not be verified",
                      str(instance.notify.mock_calls))
        self.assertNotIn("private transcript", str(instance._log.mock_calls))

    def test_recording_is_blocked_while_startup_model_is_loading(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = __import__("threading").Lock()
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
        instance.lock = __import__("threading").Lock()
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

    def test_recording_rechecks_recovery_after_foreground_discovery(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = threading.Lock()
        instance.recording = True
        instance.transcribing = False
        instance._rec_epoch = 1
        instance._model_idle_epoch = 0
        instance._undelivered_lock = threading.Lock()
        instance._undelivered_dictations = []
        instance._dictation_model_ready = mock.Mock(return_value=True)
        instance._log = mock.Mock()
        instance._set_indicator = mock.Mock()
        instance._wake_model_if_idle = mock.Mock()
        instance._schedule_recording_limit = mock.Mock()
        instance._start_audio_worker = mock.Mock()

        def previous_delivery_finishes():
            # This can run while a start that saw the previous recording in
            # progress is doing unlocked foreground discovery. Delivery has
            # retained text and completed before start takes its final lock.
            instance.recording = False
            instance._undelivered_dictations.append("private transcript")
            return app.PasteTarget("notepad.exe", 1234)

        with mock.patch.object(
                app, "_foreground_paste_target",
                side_effect=previous_delivery_finishes), \
                mock.patch.object(app.threading, "Thread") as worker:
            self.assertFalse(instance.start_recording())

        self.assertFalse(instance.recording)
        self.assertFalse(instance._starting_recording)
        self.assertEqual(instance._rec_epoch, 1)
        self.assertEqual(instance._undelivered_dictations, ["private transcript"])
        instance._dictation_model_ready.assert_called_once_with()
        instance._set_indicator.assert_not_called()
        worker.assert_not_called()
        self.assertNotIn("private transcript", str(instance._log.mock_calls))

    def test_first_press_after_model_error_starts_one_retry_not_recording(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = __import__("threading").Lock()
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
            instance._preload_model_worker, "parakeet-tdt-0.6b-v3", 1)
        self.assertFalse(instance.start_recording())
        instance._model_executor.submit.assert_called_once_with(
            instance._preload_model_worker, "parakeet-tdt-0.6b-v3", 1)

    def test_recording_start_publishes_and_clears_its_transition(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = __import__("threading").Lock()
        observed = []

        def claimed():
            observed.append(instance._starting_recording)
            return False

        instance._start_recording_claimed = claimed

        self.assertFalse(instance.start_recording())
        self.assertEqual(observed, [True])
        self.assertFalse(instance._starting_recording)

    def test_explicit_model_retry_is_single_flight(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"model": "parakeet-tdt-0.6b-v3"}
        instance.model_status = "error"
        instance.transcriber = mock.Mock()
        instance.transcriber.loaded.return_value = False
        instance._model_executor = mock.Mock()
        instance._model_retry_lock = __import__("threading").Lock()
        instance._model_load_generation = 0

        self.assertTrue(instance.retry_model())
        self.assertFalse(instance.retry_model())

        self.assertEqual(instance.model_status, "loading")
        instance._model_executor.submit.assert_called_once_with(
            instance._preload_model_worker, "parakeet-tdt-0.6b-v3", 1)

    def test_changed_model_is_prepared_before_the_next_dictation(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"model": "small.en"}
        instance.model_status = "ready"
        instance.model_status_detail = "base.en on cpu (int8)"
        instance.transcriber = mock.Mock()
        instance.transcriber.loaded.side_effect = lambda name: name == "base.en"
        instance._model_executor = mock.Mock()
        instance._model_retry_lock = __import__("threading").Lock()
        instance._model_load_target = None
        instance._model_load_generation = 0

        self.assertTrue(instance.prepare_configured_model())

        self.assertEqual(instance.model_status, "loading")
        self.assertEqual(instance.model_status_detail, "Checking local model files…")
        instance._model_executor.submit.assert_called_once_with(
            instance._preload_model_worker, "small.en", 1)

    def test_selected_model_preparation_is_single_flight(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"model": "small.en"}
        instance.model_status = "loading"
        instance.transcriber = mock.Mock()
        instance._model_executor = mock.Mock()
        instance._model_retry_lock = __import__("threading").Lock()
        instance._model_load_target = "small.en"
        instance._model_load_generation = 1

        self.assertFalse(instance.prepare_configured_model())

        instance._model_executor.submit.assert_not_called()

    def test_audio_cues_default_on(self):
        self.assertTrue(config.DEFAULTS["audio_cues"])

    def test_playback_muting_defaults_on(self):
        self.assertTrue(config.DEFAULTS["mute_playback_while_recording"])

    def test_visual_indicator_defaults_on(self):
        self.assertTrue(config.DEFAULTS["visual_indicator"])

    def test_update_checks_default_on_and_first_run_autostart_is_opt_in(self):
        self.assertTrue(config.DEFAULTS["check_updates"])
        self.assertFalse(config.DEFAULTS["setup_complete"])
        self.assertFalse(config.DEFAULTS["autostart"])

    def test_daily_update_check_interval(self):
        day = app.UPDATE_CHECK_INTERVAL_SEC
        self.assertFalse(app._update_check_due(1000, 1000 + day - 1))
        self.assertTrue(app._update_check_due(1000, 1000 + day))
        self.assertTrue(app._update_check_due("invalid", 1000))

    def test_update_is_revalidated_after_approval_before_launch(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.exit_app = mock.Mock()
        instance.lock = threading.Lock()
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
        self.assertFalse(instance._update_installing)

    def test_update_refuses_active_and_retained_dictation_before_process_launch(self):
        for state in ("_starting_recording", "recording",
                      "_canceling_recording", "transcribing", "retained"):
            with self.subTest(state=state):
                instance = app.PresspeechApp.__new__(app.PresspeechApp)
                instance.lock = threading.Lock()
                instance._undelivered_lock = threading.Lock()
                instance._undelivered_dictations = (
                    ["private transcript"] if state == "retained" else [])
                if state != "retained":
                    setattr(instance, state, True)
                instance.exit_app = mock.Mock()
                with mock.patch.object(
                        app.updates, "locked_verified_installer") as verify, \
                        mock.patch.object(app.subprocess, "Popen") as launch:
                    with self.assertRaises(app.updates.UpdateInstallBusy) as caught:
                        instance.launch_update("installer.exe", {})
                verify.assert_not_called()
                launch.assert_not_called()
                instance.exit_app.assert_not_called()
                self.assertFalse(getattr(instance, "_update_installing", False))
                if state == "retained":
                    self.assertEqual(
                        instance._undelivered_dictations, ["private transcript"])
                    self.assertNotIn(
                        "private transcript", str(caught.exception))

    def test_update_reservation_blocks_new_capture_and_clears_after_launch_failure(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = threading.Lock()
        instance.notify = mock.Mock()
        instance._dictation_model_ready = mock.Mock()
        instance.exit_app = mock.Mock()

        def verify_while_reserved(_update, _path):
            self.assertTrue(instance._update_installing)
            self.assertFalse(instance.start_recording())
            instance._dictation_model_ready.assert_not_called()
            raise app.updates.UpdateError("Installer verification failed.")

        with mock.patch.object(
                app.updates, "locked_verified_installer",
                side_effect=verify_while_reserved), \
                mock.patch.object(app.subprocess, "Popen") as launch:
            with self.assertRaises(app.updates.UpdateError):
                instance.launch_update("installer.exe", {})
        launch.assert_not_called()
        instance.notify.assert_called_once_with(
            "Update starting",
            "Wait for the update installer before starting another dictation.")
        self.assertFalse(instance._update_installing)

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
        instance.lock = threading.Lock()
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
            "max_recording_seconds": 300,
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
        self.assertIn("Maximum recording length: 300 seconds", diagnostics)
        self.assertIn("Model status: ready", diagnostics)
        self.assertIn("Global hotkey status: not started", diagnostics)
        self.assertIn("Windows UI Automation: not initialized", diagnostics)
        self.assertIn("Window action failures: 0", diagnostics)
        self.assertIn("Configured microphone: Specific input (name omitted)", diagnostics)
        self.assertIn("Active microphone: Open at 16000 Hz (name omitted)", diagnostics)
        self.assertIn("exact microphone names, raw error details, or raw log lines included",
                      diagnostics)
        self.assertNotIn("\\Users\\", diagnostics)
        self.assertNotIn("MME::Yeti Nano", diagnostics)
        self.assertNotIn("private spoken phrase", diagnostics)
        self.assertNotIn("private replacement", diagnostics)

    def test_diagnostic_microphone_summary_does_not_render_untrusted_values(self):
        configured = "MME::Alice's private office microphone\nsecret"
        lines = app._diagnostic_microphone_lines(configured, (7, 48000))

        rendered = "\n".join(lines)
        self.assertEqual(lines, (
            "Configured microphone: Specific input (name omitted)",
            "Active microphone: Open at 48000 Hz (name omitted)",
        ))
        self.assertNotIn("Alice", rendered)
        self.assertNotIn("secret", rendered)

    def test_copy_diagnostics_reports_success_only_after_clipboard_write(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.diagnostics_text = mock.Mock(return_value="safe diagnostics")
        instance.notify = mock.Mock()

        receipt = app.clipboard_delivery.WriteReceipt(101)
        with mock.patch.object(
                app.clipboard_delivery, "write_text", return_value=receipt) as write, \
                mock.patch.object(
                    app.clipboard_delivery, "is_current", return_value=True) as current:
            self.assertTrue(instance.copy_diagnostics())

        write.assert_called_once_with("safe diagnostics")
        current.assert_called_once_with(receipt)
        instance.notify.assert_called_once_with(
            "Presspeech", "Privacy-safe diagnostics copied to the clipboard.")

    def test_copy_diagnostics_does_not_claim_success_after_clipboard_changes(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.diagnostics_text = mock.Mock(return_value="safe diagnostics")
        instance.notify = mock.Mock()
        instance._log = mock.Mock()
        receipt = app.clipboard_delivery.WriteReceipt(101)

        with mock.patch.object(
                app.clipboard_delivery, "write_text", return_value=receipt) as write, \
                mock.patch.object(
                    app.clipboard_delivery, "is_current", return_value=False) as current:
            self.assertFalse(instance.copy_diagnostics())

        write.assert_called_once_with("safe diagnostics")
        current.assert_called_once_with(receipt)
        instance._log.assert_called_once_with(
            "diagnostics clipboard ownership unconfirmed")
        instance.notify.assert_called_once_with(
            "Clipboard not confirmed",
            "Diagnostics could not be confirmed on the clipboard. "
            "Check its contents before choosing Copy Diagnostics again.")

    def test_copy_diagnostics_exposes_locked_clipboard_without_raw_detail(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.diagnostics_text = mock.Mock(return_value="safe diagnostics")
        instance.notify = mock.Mock()
        instance._log = mock.Mock()

        with mock.patch.object(
                app.clipboard_delivery, "write_text",
                side_effect=app.clipboard_delivery.ClipboardError(
                    "private clipboard owner")) as write:
            self.assertFalse(instance.copy_diagnostics())

        write.assert_called_once_with("safe diagnostics")
        instance._log.assert_called_once_with(
            "could not copy diagnostics: ClipboardError")
        instance.notify.assert_called_once_with(
            "Clipboard unavailable",
            "Diagnostics could not be confirmed on the clipboard. "
            "Check its contents; if needed, close any app using the "
            "clipboard and choose Copy Diagnostics again.")
        self.assertNotIn(
            "private clipboard owner", str(instance.notify.mock_calls))

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

    def test_no_speech_feedback_is_visible_and_actionable(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"visual_indicator": True}
        instance.indicator = mock.Mock()
        instance.notify = mock.Mock()

        instance._show_no_speech_feedback()

        instance.indicator.show_temporary.assert_called_once_with(
            "no_speech", app.NO_SPEECH_FEEDBACK_SEC)
        instance.notify.assert_called_once_with(
            "No speech detected",
            "Try again and speak after the start cue. If this keeps happening, "
            "run the microphone check in Setup.")

    def test_no_speech_notification_remains_when_visual_indicator_is_off(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"visual_indicator": False}
        instance.indicator = mock.Mock()
        instance.notify = mock.Mock()

        instance._show_no_speech_feedback()

        instance.indicator.show_temporary.assert_not_called()
        instance.notify.assert_called_once()

    def test_no_text_feedback_does_not_claim_silence(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"visual_indicator": True}
        instance.indicator = mock.Mock()
        instance.notify = mock.Mock()

        instance.lock = threading.Lock()
        instance.transcribing = True
        instance._finish_transcribing(app.NO_TEXT_OUTCOME)

        instance.indicator.show_temporary.assert_called_once_with(
            "no_text", app.NO_SPEECH_FEEDBACK_SEC)
        instance.notify.assert_called_once_with(
            "No text recognized",
            "The local recognizer returned no text. Try again. If this keeps "
            "happening, check the microphone in Setup or try another model.")
        self.assertFalse(instance.transcribing)

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

    @staticmethod
    def winreg_module(key):
        winreg = mock.Mock()
        winreg.HKEY_CURRENT_USER = "current-user"
        winreg.KEY_SET_VALUE = "set-value"
        winreg.REG_SZ = "string"
        winreg.OpenKey.return_value = key
        winreg.CreateKeyEx.return_value = key
        return winreg

    def test_autostart_success_is_reported_after_registry_write(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"autostart": True}
        instance._log = mock.Mock()
        instance.notify = mock.Mock()
        key = mock.MagicMock()
        key.__enter__.return_value = "run-key"
        winreg = self.winreg_module(key)

        with mock.patch.dict("sys.modules", {"winreg": winreg}), \
                mock.patch.object(app, "_autostart_command",
                                  return_value='"Presspeech.exe"'):
            self.assertTrue(instance.apply_autostart())

        winreg.OpenKey.assert_called_once_with(
            "current-user",
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            "set-value",
        )
        winreg.CreateKeyEx.assert_not_called()
        winreg.SetValueEx.assert_called_once_with(
            "run-key", "Presspeech", 0, "string", '"Presspeech.exe"')
        instance.notify.assert_not_called()

    def test_autostart_opt_in_creates_missing_run_key(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"autostart": True}
        instance._log = mock.Mock()
        instance.notify = mock.Mock()
        key = mock.MagicMock()
        key.__enter__.return_value = "new-run-key"
        winreg = self.winreg_module(key)
        winreg.OpenKey.side_effect = FileNotFoundError("Run key absent")

        with mock.patch.dict("sys.modules", {"winreg": winreg}), \
                mock.patch.object(app, "_autostart_command",
                                  return_value='"Presspeech.exe"'):
            self.assertTrue(instance.apply_autostart())

        winreg.CreateKeyEx.assert_called_once_with(
            "current-user",
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            "set-value",
        )
        winreg.SetValueEx.assert_called_once_with(
            "new-run-key", "Presspeech", 0, "string", '"Presspeech.exe"')
        instance.notify.assert_not_called()

    def test_autostart_opt_in_reports_missing_key_creation_failure(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"autostart": True}
        instance._log = mock.Mock()
        instance.notify = mock.Mock()
        winreg = self.winreg_module(mock.MagicMock())
        winreg.OpenKey.side_effect = FileNotFoundError("Run key absent")
        winreg.CreateKeyEx.side_effect = PermissionError("registry denied")

        with mock.patch.dict("sys.modules", {"winreg": winreg}):
            self.assertFalse(instance.apply_autostart())

        winreg.SetValueEx.assert_not_called()
        instance._log.assert_called_once_with(
            "autostart error: PermissionError")
        instance.notify.assert_called_once()

    def test_autostart_opt_out_succeeds_when_run_key_is_absent(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"autostart": False}
        instance._log = mock.Mock()
        instance.notify = mock.Mock()
        winreg = self.winreg_module(mock.MagicMock())
        winreg.OpenKey.side_effect = FileNotFoundError("Run key absent")

        with mock.patch.dict("sys.modules", {"winreg": winreg}):
            self.assertTrue(instance.apply_autostart())

        winreg.CreateKeyEx.assert_not_called()
        winreg.DeleteValue.assert_not_called()
        instance._log.assert_not_called()
        instance.notify.assert_not_called()

    def test_autostart_opt_out_succeeds_when_value_is_absent(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"autostart": False}
        instance._log = mock.Mock()
        instance.notify = mock.Mock()
        key = mock.MagicMock()
        key.__enter__.return_value = "run-key"
        winreg = self.winreg_module(key)
        winreg.DeleteValue.side_effect = FileNotFoundError("value absent")

        with mock.patch.dict("sys.modules", {"winreg": winreg}):
            self.assertTrue(instance.apply_autostart())

        winreg.OpenKey.assert_called_once_with(
            "current-user",
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            "set-value",
        )
        winreg.CreateKeyEx.assert_not_called()
        winreg.DeleteValue.assert_called_once_with("run-key", "Presspeech")
        instance.notify.assert_not_called()

    def test_autostart_opt_out_removes_only_presspeech_value(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"autostart": False}
        instance._log = mock.Mock()
        instance.notify = mock.Mock()
        key = mock.MagicMock()
        key.__enter__.return_value = "run-key"
        winreg = self.winreg_module(key)

        with mock.patch.dict("sys.modules", {"winreg": winreg}):
            self.assertTrue(instance.apply_autostart())

        winreg.CreateKeyEx.assert_not_called()
        winreg.DeleteValue.assert_called_once_with("run-key", "Presspeech")
        instance.notify.assert_not_called()

    def test_autostart_opt_out_reports_permission_failure(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"autostart": False}
        instance._log = mock.Mock()
        instance.notify = mock.Mock()
        winreg = self.winreg_module(mock.MagicMock())
        winreg.OpenKey.side_effect = PermissionError("registry denied")

        with mock.patch.dict("sys.modules", {"winreg": winreg}):
            self.assertFalse(instance.apply_autostart())

        winreg.CreateKeyEx.assert_not_called()
        instance._log.assert_called_once_with(
            "autostart error: PermissionError")
        instance.notify.assert_called_once()

    def test_autostart_failure_is_reported_without_claiming_success(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"autostart": True}
        instance._log = mock.Mock()
        instance.notify = mock.Mock()
        key = mock.MagicMock()
        key.__enter__.side_effect = PermissionError("registry denied")
        winreg = self.winreg_module(key)

        with mock.patch.dict("sys.modules", {"winreg": winreg}):
            self.assertFalse(instance.apply_autostart())

        instance._log.assert_called_once_with(
            "autostart error: PermissionError")
        instance.notify.assert_called_once_with(
            "Start with Windows not updated",
            "Open Settings, then choose Apps and Startup to review "
            "Presspeech's startup state.",
        )

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

    def test_start_cue_waits_for_microphone_before_playback_mutes_and_capture(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"audio_cues": True}
        instance.lock = __import__("threading").Lock()
        instance.recording = True
        instance._rec_epoch = 4
        instance._capture_ready = False
        instance._first_audio_callback = threading.Event()
        instance._first_audio_callback.set()
        instance.icon = None
        instance._log = mock.Mock()
        calls = mock.Mock()
        instance._play_cue_worker = calls.cue
        instance._mute_playback_for_recording = calls.mute
        instance._open_mic_worker = mock.Mock(side_effect=lambda _epoch: (
            calls.open_mic(_epoch), True)[1])
        instance._set_indicator = calls.indicator
        calls.cue.side_effect = lambda _name: self.assertFalse(instance._capture_ready)
        calls.mute.side_effect = lambda _epoch: self.assertFalse(instance._capture_ready)
        instance._start_audio_worker(4)
        self.assertEqual(
            calls.mock_calls,
            [mock.call.open_mic(4), mock.call.cue("start"),
             mock.call.mute(4), mock.call.indicator("listening")],
        )
        self.assertTrue(instance._capture_ready)

    def test_first_audio_callback_unblocks_readiness_but_is_not_transcribed(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"audio_cues": True}
        instance.lock = threading.Lock()
        instance.recording = True
        instance._rec_epoch = 4
        instance._capture_ready = False
        instance._first_audio_callback = threading.Event()
        instance.buffer = []
        instance._peak_rms = 0.0
        instance._audio_sequence = 0
        instance._last_audio_callback_started_at = 0.0
        instance._capture_ready_at = 0.0
        instance.icon = None
        instance._log = mock.Mock()
        instance._open_mic_worker = mock.Mock(return_value=True)
        instance._play_cue_worker = mock.Mock()
        instance._mute_playback_for_recording = mock.Mock()
        instance._set_indicator = mock.Mock()
        worker = threading.Thread(target=instance._start_audio_worker, args=(4,))
        worker.start()
        try:
            self.assertFalse(instance._first_audio_callback.is_set())
            instance._play_cue_worker.assert_not_called()
            chunk = __import__("numpy").ones((8, 1), dtype="float32")
            instance._audio_cb(chunk, 8, None, None, 4)
            worker.join(1)
            self.assertFalse(worker.is_alive())
            self.assertEqual(instance.buffer, [])
            self.assertEqual(instance._audio_sequence, 0)
            self.assertTrue(instance._capture_ready)
            instance._play_cue_worker.assert_called_once_with("start")
            instance._set_indicator.assert_called_once_with("listening")
            instance._audio_cb(chunk, 8, None, None, 4)
            self.assertEqual(len(instance.buffer), 1)
        finally:
            instance.recording = False
            worker.join(1)

    def test_no_audio_callback_aborts_without_claiming_listening(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"audio_cues": True}
        instance.lock = threading.Lock()
        instance.recording = True
        instance._canceling_recording = False
        instance._capture_ready = False
        instance._rec_epoch = 4
        instance._first_audio_callback = threading.Event()
        instance._recording_input_device = (2, 16000)
        instance.input_device = (2, 16000)
        instance._cached_input_selector = "auto"
        instance.buffer = []
        stream = mock.Mock()
        instance.stream = stream
        instance._open_mic_worker = mock.Mock(return_value=True)
        instance._cancel_recording_limit = mock.Mock()
        instance._restore_playback_after_recording = mock.Mock()
        instance._set_indicator = mock.Mock()
        instance._play_cue_worker = mock.Mock()
        instance.icon = mock.Mock()
        instance.idle_icon = object()
        instance._schedule_model_idle_unload = mock.Mock()
        instance._log = mock.Mock()
        instance.notify = mock.Mock()

        with mock.patch.object(app, "MICROPHONE_START_TIMEOUT_SEC", 0.01):
            instance._start_audio_worker(4)

        self.assertFalse(instance.recording)
        self.assertFalse(instance._canceling_recording)
        self.assertFalse(instance._capture_ready)
        self.assertIsNone(instance._recording_input_device)
        self.assertIsNone(instance.input_device)
        self.assertIsNone(instance._cached_input_selector)
        self.assertIsNone(instance.stream)
        self.assertEqual(instance._recording_paste_target, app.PasteTarget("", 0))
        self.assertIs(instance.icon.icon, instance.idle_icon)
        instance._schedule_model_idle_unload.assert_called_once_with()
        stream.stop.assert_called_once_with()
        stream.close.assert_called_once_with()
        instance._play_cue_worker.assert_not_called()
        instance._set_indicator.assert_called_once_with(None)
        instance.notify.assert_called_once_with(
            "Microphone not responding",
            "Presspeech could not receive audio from the selected input. "
            "Check the microphone in Setup or reconnect it, then try again.")

    def test_release_during_start_cue_cannot_revive_listening(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"audio_cues": True}
        instance.lock = threading.Lock()
        instance.recording = True
        instance._rec_epoch = 4
        instance._capture_ready = False
        instance._first_audio_callback = threading.Event()
        instance._first_audio_callback.set()
        instance.icon = None
        instance._log = mock.Mock()
        instance._open_mic_worker = mock.Mock(return_value=True)
        instance._play_cue_worker = mock.Mock(
            side_effect=lambda _name: setattr(instance, "recording", False))
        instance._mute_playback_for_recording = mock.Mock()
        instance._set_indicator = mock.Mock()

        instance._start_audio_worker(4)

        instance._mute_playback_for_recording.assert_not_called()
        instance._set_indicator.assert_not_called()
        self.assertFalse(instance._capture_ready)

    def test_early_release_marker_prevents_late_listening_after_start_cue(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"audio_cues": True}
        instance.lock = threading.Lock()
        instance.recording = True
        instance._rec_epoch = 4
        instance._capture_ready = False
        instance._stop_before_ready = False
        instance._first_audio_callback = threading.Event()
        instance._first_audio_callback.set()
        instance.icon = None
        instance._log = mock.Mock()
        instance._open_mic_worker = mock.Mock(return_value=True)
        instance._play_cue_worker = mock.Mock(
            side_effect=lambda _name: setattr(instance, "_stop_before_ready", True))
        instance._mute_playback_for_recording = mock.Mock()
        instance._set_indicator = mock.Mock()

        instance._start_audio_worker(4)

        instance._play_cue_worker.assert_called_once_with("start")
        instance._mute_playback_for_recording.assert_not_called()
        instance._set_indicator.assert_not_called()
        self.assertFalse(instance._capture_ready)

    def test_stale_audio_worker_cannot_attach_to_a_new_recording(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = __import__("threading").Lock()
        instance.recording = True
        instance._rec_epoch = 4
        instance.input_device = None
        current_stream = object()
        instance.stream = current_stream

        def finish_old_device_search(**_kwargs):
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
        instance._audio_sequence = 0
        instance._last_audio_callback_started_at = 0.0
        instance._capture_ready = True
        instance._capture_ready_at = 0.0
        instance._first_audio_callback = threading.Event()
        chunk = __import__("numpy").ones((8, 1), dtype="float32")

        instance._audio_cb(chunk, 8, None, None, 4)
        self.assertEqual(instance.buffer, [])
        self.assertEqual(instance._audio_sequence, 0)
        instance._audio_cb(chunk, 8, None, None, 5)
        self.assertEqual(len(instance.buffer), 1)
        self.assertEqual(instance._audio_sequence, 1)
        self.assertGreater(instance._last_audio_callback_started_at, 0.0)

    def test_pre_ready_callback_delayed_in_copy_cannot_enter_capture(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = threading.Lock()
        instance.recording = True
        instance._rec_epoch = 5
        instance.buffer = []
        instance._peak_rms = 0.0
        instance._audio_sequence = 0
        instance._last_audio_callback_started_at = 0.0
        instance._capture_ready = False
        instance._capture_ready_at = 0.0
        instance._first_audio_callback = threading.Event()
        copying = threading.Event()
        continue_copy = threading.Event()
        samples = __import__("numpy").ones((8, 1), dtype="float32")

        class DelayedChunk:
            def copy(self):
                copying.set()
                continue_copy.wait(2)
                return samples

        # Keep the callback waiting before it gets the app lock. Opening the
        # capture gate must not retroactively accept its cue-era buffer.
        chunk = DelayedChunk()
        worker = threading.Thread(
            target=instance._audio_cb, args=(chunk, 8, None, None, 5))
        worker.start()
        try:
            self.assertTrue(copying.wait(1))
            with instance.lock:
                instance._capture_ready_at = app.time.perf_counter()
                instance._capture_ready = True
            continue_copy.set()
            worker.join(1)
            self.assertFalse(worker.is_alive())
            self.assertEqual(instance.buffer, [])
            self.assertEqual(instance._audio_sequence, 0)
        finally:
            continue_copy.set()
            worker.join(1)

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
                               side_effect=OSError(
                                   "private device label and path")):
            instance._open_mic_worker(7)
        self.assertFalse(instance.recording)
        self.assertIsNone(instance.input_device)
        self.assertIsNone(instance._recording_limit_timer)
        timer.cancel.assert_called_once_with()
        instance.notify.assert_called_once_with(
            "Microphone error",
            "Presspeech couldn't open the selected input. Check Settings > "
            "System > Sound > Input and Windows microphone privacy settings, "
            "including 'Let desktop apps access your microphone'. On Windows "
            "11 builds with per-app desktop microphone controls, also allow "
            "Presspeech there. Choose Check Microphone in Setup or another "
            "input in Settings, then try again.")
        self.assertNotIn("private", str(instance.notify.mock_calls))

    def test_recording_rate_is_bound_before_native_stream_starts(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = __import__("threading").Lock()
        instance.recording = True
        instance._rec_epoch = 7
        instance.input_device = (3, 48000)
        instance._recording_input_device = None
        instance.stream = None
        instance.icon = None
        instance._get_input_device = mock.Mock(return_value=(3, 48000))
        instance._log = mock.Mock()
        stream = mock.Mock()

        def verify_rate_is_owned():
            # PortAudio may deliver its first callback from inside start().
            self.assertEqual(instance._recording_input_device, (3, 48000))

        stream.start.side_effect = verify_rate_is_owned
        with mock.patch.object(
                app.AUDIO_BACKEND, "open_input_stream",
                return_value=stream) as open_stream:
            instance._open_mic_worker(7)

        open_stream.assert_called_once()
        self.assertIs(instance.stream, stream)
        self.assertEqual(instance._recording_input_device, (3, 48000))

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
            "Check the selected microphone in Setup and Settings > System > "
            "Sound > Input. If two inputs have the same name, disconnect one; "
            "Presspeech cannot choose a specific one. Enable microphone "
            "access for desktop apps in Windows privacy settings and try again. "
            "On Windows 11 builds with per-app desktop microphone controls, "
            "also allow Presspeech there.")

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
        instance.settings = {"max_recording_seconds": 300}
        instance.lock = __import__("threading").Lock()
        instance.recording = True
        instance._rec_epoch = 7
        instance._recording_limit_timer = None
        with mock.patch.object(app.threading, "Timer") as timer:
            instance._schedule_recording_limit(7)
        timer.assert_called_once_with(
            300, instance._recording_limit_reached, (7,))
        self.assertTrue(timer.return_value.daemon)
        timer.return_value.start.assert_called_once_with()

    def test_recording_limit_timer_falls_back_from_invalid_runtime_setting(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"max_recording_seconds": 3600}
        instance.lock = __import__("threading").Lock()
        instance.recording = True
        instance._rec_epoch = 7
        instance._recording_limit_timer = None

        with mock.patch.object(app.threading, "Timer") as timer:
            instance._schedule_recording_limit(7)

        timer.assert_called_once_with(
            app.cfg.DEFAULTS["max_recording_seconds"],
            instance._recording_limit_reached,
            (7,),
        )

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
        instance._capture_ready = True
        instance.buffer = []
        instance.stream = None
        instance.icon = None
        instance._recording_input_device = None
        instance._recording_paste_target = app.PasteTarget("notepad.exe", 1234)
        timer = mock.Mock()
        instance._recording_limit_timer = timer
        instance._restore_playback_after_recording = mock.Mock()
        instance._play_cue = mock.Mock()
        instance._show_no_speech_feedback = mock.Mock()
        instance._log = mock.Mock()
        instance._schedule_model_idle_unload = mock.Mock()
        self.assertTrue(instance.stop_recording(expected_epoch=7))
        self.assertIsNone(instance._recording_limit_timer)
        timer.cancel.assert_called_once_with()
        instance._show_no_speech_feedback.assert_called_once_with()

    def test_releasing_before_microphone_ready_reports_not_ready_not_no_speech(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = threading.Lock()
        instance.recording = True
        instance._rec_epoch = 7
        instance._capture_ready = False
        instance.buffer = []
        instance.stream = None
        instance.icon = None
        instance._recording_input_device = None
        instance._recording_paste_target = app.PasteTarget("notepad.exe", 1234)
        instance._recording_limit_timer = None
        instance._restore_playback_after_recording = mock.Mock()
        instance._play_cue = mock.Mock()
        instance._show_no_speech_feedback = mock.Mock()
        instance._show_not_ready_feedback = mock.Mock()
        instance._log = mock.Mock()
        instance._schedule_model_idle_unload = mock.Mock()

        self.assertTrue(instance.stop_recording(expected_epoch=7))

        instance._play_cue.assert_not_called()
        instance._show_no_speech_feedback.assert_not_called()
        instance._show_not_ready_feedback.assert_called_once_with()

    def test_early_release_discards_audio_that_arrived_before_stop_claimed_lock(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = threading.Lock()
        instance.recording = True
        instance._rec_epoch = 7
        # Readiness and even a buffer can race the stop cleanup after the
        # physical release was observed during the start cue.
        instance._capture_ready = True
        instance._stop_before_ready = True
        instance.buffer = [app.np.ones(4800, dtype=app.np.float32)]
        instance.stream = None
        instance.icon = None
        instance._recording_input_device = (0, 16000)
        instance._recording_paste_target = app.PasteTarget("notepad.exe", 1234)
        instance._recording_limit_timer = None
        instance._restore_playback_after_recording = mock.Mock()
        instance._play_cue = mock.Mock()
        instance._show_not_ready_feedback = mock.Mock()
        instance._set_indicator = mock.Mock()
        instance._log = mock.Mock()
        instance._schedule_model_idle_unload = mock.Mock()
        instance._model_executor = mock.Mock()

        self.assertTrue(instance.stop_recording(expected_epoch=7))

        instance._show_not_ready_feedback.assert_called_once_with()
        instance._play_cue.assert_not_called()
        instance._model_executor.submit.assert_not_called()
        self.assertFalse(instance.transcribing)

    def test_too_short_recording_reports_no_speech_without_model_work(self):
        self.assertEqual(app.MIN_TRANSCRIPTION_AUDIO_SAMPLES, 4000)
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = __import__("threading").Lock()
        instance.recording = True
        instance.transcribing = False
        instance._rec_epoch = 7
        instance._capture_ready = True
        instance.buffer = [
            __import__("numpy").ones(3999, dtype="float32")]
        instance.stream = None
        instance.icon = None
        instance.input_device = (0, 16000)
        instance._recording_input_device = (0, 16000)
        instance._recording_paste_target = app.PasteTarget(
            "notepad.exe", 1234)
        instance._recording_scratchpad = None
        instance._recording_limit_timer = None
        instance._restore_playback_after_recording = mock.Mock()
        instance._play_cue = mock.Mock()
        instance._finish_transcribing = mock.Mock()
        instance._log = mock.Mock()
        instance._schedule_model_idle_unload = mock.Mock()
        instance._model_executor = mock.Mock()

        self.assertTrue(instance.stop_recording(expected_epoch=7))

        instance._finish_transcribing.assert_called_once_with(
            app.NO_SPEECH_OUTCOME)
        instance._model_executor.submit.assert_not_called()

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

        with mock.patch.object(app.clipboard_delivery, "write_text") as write, \
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
        write.assert_not_called()
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

    def test_cancel_before_microphone_ready_has_no_stop_cue(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = threading.Lock()
        instance.recording = True
        instance._capture_ready = False
        instance._canceling_recording = False
        instance.stream = None
        instance.buffer = []
        instance._peak_rms = 0.0
        instance._recording_paste_target = app.PasteTarget("", 0)
        instance._recording_scratchpad = None
        instance._recording_input_device = None
        instance._recording_limit_timer = None
        instance.icon = None
        instance._restore_playback_after_recording = mock.Mock()
        instance._play_cue = mock.Mock()
        instance._set_indicator = mock.Mock()
        instance._log = mock.Mock()
        instance._schedule_model_idle_unload = mock.Mock()
        with mock.patch.object(app.threading, "Thread"):
            self.assertTrue(instance.cancel_recording())
        instance._cancel_recording_worker(None, None)
        instance._play_cue.assert_not_called()
        instance._set_indicator.assert_called_once_with(None)

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
        instance._capture_ready = True
        # Exactly 250 ms passes the same duration gate reported by the benchmark.
        audio = __import__("numpy").ones(4000, dtype="float32")
        instance.buffer = [audio]
        instance.stream = None
        instance.icon = None
        instance.input_device = (0, 16000)
        instance._recording_input_device = (0, 16000)
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

    def test_active_stream_rate_survives_microphone_cache_invalidation(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = __import__("threading").Lock()
        instance.recording = True
        instance.transcribing = False
        instance._rec_epoch = 7
        instance._capture_ready = True
        raw_audio = __import__("numpy").ones(14400, dtype="float32")
        instance.buffer = [raw_audio]
        instance.stream = None
        instance.icon = None
        # Settings has selected another input and invalidated the reusable
        # cache while this 48 kHz stream remains the recording's source.
        instance.input_device = None
        instance._cached_input_selector = None
        instance._recording_input_device = (3, 48000)
        instance._recording_paste_target = app.PasteTarget(
            "notepad.exe", 1234)
        instance._recording_scratchpad = None
        instance._recording_limit_timer = None
        instance._restore_playback_after_recording = mock.Mock()
        instance._play_cue = mock.Mock()
        instance._capture_benchmark_if_armed = mock.Mock()
        instance._set_indicator = mock.Mock()
        instance._log = mock.Mock()
        instance._model_executor = mock.Mock()
        converted = __import__("numpy").ones(4800, dtype="float32")

        with mock.patch.object(
                app, "_resample_to_16k", return_value=converted) as resample:
            self.assertTrue(instance.stop_recording(expected_epoch=7))

        resample.assert_called_once()
        __import__("numpy").testing.assert_array_equal(
            resample.call_args.args[0], raw_audio)
        self.assertEqual(resample.call_args.args[1], 48000)
        self.assertIsNone(instance._recording_input_device)
        queued = instance._model_executor.submit.call_args.args
        self.assertIs(queued[1], converted)


class PostRollTests(unittest.TestCase):
    def make_app(self, value, peak=0.1, rate=16000):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = __import__("threading").Lock()
        instance.buffer = [__import__("numpy").full(
            (int(rate * app.POST_ROLL_TAIL_SEC), 1), value, dtype="float32")]
        instance.input_device = (0, rate)
        instance._recording_input_device = (0, rate)
        instance._peak_rms = peak
        instance._audio_sequence = 3
        instance._last_audio_callback_started_at = 9.9
        return instance

    def test_quiet_tail_is_silence(self):
        instance = self.make_app(0.001)
        rms, threshold, sequence, callback_started_at = (
            instance._post_roll_tail())
        self.assertLessEqual(rms, threshold)
        self.assertEqual(sequence, 3)
        self.assertEqual(callback_started_at, 9.9)

    def test_voiced_tail_keeps_recording(self):
        instance = self.make_app(0.03)
        rms, threshold, sequence, callback_started_at = (
            instance._post_roll_tail())
        self.assertGreater(rms, threshold)
        self.assertEqual(sequence, 3)
        self.assertEqual(callback_started_at, 9.9)

    def test_cache_invalidation_does_not_shorten_a_48khz_tail(self):
        numpy = __import__("numpy")
        instance = self.make_app(0.001, rate=48000)
        # Only the earliest two thirds of the 90 ms tail are still voiced. A
        # wrong 16 kHz fallback would inspect just the final quiet 30 ms.
        instance.buffer = [numpy.concatenate((
            numpy.full(2880, 0.03, dtype="float32"),
            numpy.full(1440, 0.001, dtype="float32"),
        ))]
        instance.input_device = None

        rms, threshold, _sequence, _callback_started_at = (
            instance._post_roll_tail())

        self.assertGreater(rms, threshold)

    def test_release_keeps_the_capture_epoch_through_post_roll(self):
        instance = self.make_app(0.03)
        instance.recording = True
        instance._rec_epoch = 7
        instance._capture_ready = True
        instance._capture_ready_at = 9.0
        instance._schedule_post_roll = mock.Mock()
        with mock.patch.object(app.time, "perf_counter", return_value=10.0):
            instance.request_stop()
        self.assertEqual(instance._rec_epoch, 7)
        instance._schedule_post_roll.assert_called_once_with(
            app.POST_ROLL_MIN_SEC, 7, 10.0, 3)

    def test_release_while_connecting_stops_without_post_roll(self):
        instance = self.make_app(0.03)
        instance.recording = True
        instance._rec_epoch = 7
        instance._capture_ready = False
        instance._capture_ready_at = 0.0
        instance._stop_before_ready = False
        instance.stop_recording = mock.Mock()
        instance._schedule_post_roll = mock.Mock()

        with mock.patch.object(app.time, "perf_counter", return_value=10.0):
            instance.request_stop()

        self.assertTrue(instance._stop_before_ready)
        instance.stop_recording.assert_called_once_with(expected_epoch=7)
        instance._schedule_post_roll.assert_not_called()

    def test_release_timestamp_before_readiness_still_stops_immediately(self):
        instance = self.make_app(0.03)
        instance.recording = True
        instance._rec_epoch = 7
        instance._capture_ready = True
        instance._capture_ready_at = 10.01
        instance._stop_before_ready = False
        instance.stop_recording = mock.Mock()
        instance._schedule_post_roll = mock.Mock()

        with mock.patch.object(app.time, "perf_counter", return_value=10.0):
            instance.request_stop()

        self.assertTrue(instance._stop_before_ready)
        instance.stop_recording.assert_called_once_with(expected_epoch=7)
        instance._schedule_post_roll.assert_not_called()

    def test_post_roll_timer_keeps_the_release_audio_sequence(self):
        instance = self.make_app(0.03)
        with mock.patch.object(app.threading, "Timer") as timer:
            instance._schedule_post_roll(0.08, 7, 10.0, 3)

        timer.assert_called_once_with(
            0.08, instance._finish_after_roll, (7, 10.0, 3))
        self.assertTrue(timer.return_value.daemon)
        timer.return_value.start.assert_called_once_with()

    def test_quiet_pre_release_tail_waits_for_a_new_audio_callback(self):
        instance = self.make_app(0.001)
        instance._rec_epoch = 7
        instance.stop_recording = mock.Mock()
        instance._schedule_post_roll = mock.Mock()

        with mock.patch.object(app.time, "perf_counter", return_value=10.08):
            instance._finish_after_roll(7, 10.0, 3)

        instance.stop_recording.assert_not_called()
        instance._schedule_post_roll.assert_called_once_with(
            app.POST_ROLL_CHECK_SEC, 7, 10.0, 3)

    def test_quiet_tail_stops_after_a_new_audio_callback(self):
        instance = self.make_app(0.001)
        instance._rec_epoch = 7
        instance._audio_sequence = 4
        instance._last_audio_callback_started_at = 10.01
        instance.stop_recording = mock.Mock()
        instance._schedule_post_roll = mock.Mock()

        with mock.patch.object(app.time, "perf_counter", return_value=10.08), \
                mock.patch.object(app.PresspeechApp, "_log"):
            instance._finish_after_roll(7, 10.0, 3)

        instance.stop_recording.assert_called_once_with(expected_epoch=7)
        instance._schedule_post_roll.assert_not_called()

    def test_inflight_pre_release_callback_does_not_satisfy_boundary(self):
        instance = self.make_app(0.001)
        instance._rec_epoch = 7
        # The callback was accepted after the release sequence snapshot, but
        # it began copying its input block before the stop gesture.
        instance._audio_sequence = 4
        instance._last_audio_callback_started_at = 9.99
        instance.stop_recording = mock.Mock()
        instance._schedule_post_roll = mock.Mock()

        with mock.patch.object(app.time, "perf_counter", return_value=10.08):
            instance._finish_after_roll(7, 10.0, 3)

        instance.stop_recording.assert_not_called()
        instance._schedule_post_roll.assert_called_once_with(
            app.POST_ROLL_CHECK_SEC, 7, 10.0, 3)

    def test_maximum_window_stops_even_with_voiced_tail(self):
        instance = self.make_app(0.03)
        instance._rec_epoch = 7
        instance.stop_recording = mock.Mock()
        with mock.patch.object(app.time, "perf_counter", return_value=10.5), \
                mock.patch.object(app.PresspeechApp, "_log"):
            instance._finish_after_roll(7, 10.0, 3)
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

    def test_model_timing_summary_exposes_bounded_parakeet_chunks(self):
        summary = app._model_timing_summary({
            "backend": "parakeet",
            "chunk_count": 2,
            "max_chunk_seconds": 60.0,
        })

        self.assertIn("backend=parakeet", summary)
        self.assertIn("chunks=2", summary)
        self.assertIn("max_chunk=60.000s", summary)

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

    def test_empty_transcription_keeps_a_transient_recovery_status(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.lock = __import__("threading").Lock()
        instance.transcribing = True
        instance._last_model_use = 0.0
        instance._transcribe_worker_inner = mock.Mock(
            return_value=app.NO_SPEECH_OUTCOME)
        instance._schedule_model_idle_unload = mock.Mock()
        instance._set_indicator = mock.Mock()
        instance._set_temporary_indicator = mock.Mock()
        instance.notify = mock.Mock()

        instance._transcribe_worker(
            [], app.PasteTarget("notepad.exe", 1234))

        instance._set_indicator.assert_not_called()
        instance._set_temporary_indicator.assert_called_once_with(
            "no_speech", app.NO_SPEECH_FEEDBACK_SEC)
        instance.notify.assert_called_once()
        self.assertFalse(instance.transcribing)

    def test_whisper_vad_zero_result_is_classified_as_no_speech(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"model": "base.en"}
        instance.transcriber = mock.Mock()
        instance.transcriber.loaded.return_value = True
        instance.transcriber.transcribe.return_value = ""
        instance.transcriber.last_timing = {
            "backend": "whisper", "speech_seconds": 0.0}
        instance._log = mock.Mock()

        outcome = instance._transcribe_worker_inner([])

        self.assertEqual(outcome, app.NO_SPEECH_OUTCOME)
        self.assertTrue(any(
            "transcription returned empty" in call.args[0]
            for call in instance._log.call_args_list))
        instance.transcriber.transcribe.assert_called_once_with([])

    def test_blank_decode_without_vad_rejection_is_not_called_no_speech(self):
        for model, timing in (
                ("parakeet-tdt-0.6b-v3", {"backend": "parakeet"}),
                ("base.en", {"backend": "whisper", "speech_seconds": 0.8}),
                ("base.en", {"backend": "whisper", "speech_seconds": None}),
        ):
            with self.subTest(model=model, timing=timing):
                instance = app.PresspeechApp.__new__(app.PresspeechApp)
                instance.settings = {"model": model}
                instance.transcriber = mock.Mock()
                instance.transcriber.loaded.return_value = True
                instance.transcriber.transcribe.return_value = ""
                instance.transcriber.last_timing = timing
                instance._log = mock.Mock()

                self.assertEqual(
                    instance._transcribe_worker_inner(mock.sentinel.audio),
                    app.NO_TEXT_OUTCOME)
                instance.transcriber.transcribe.assert_called_once_with(
                    mock.sentinel.audio)

    def test_multilingual_model_is_not_forced_to_english(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"model": "turbo"}
        instance.transcriber = mock.Mock()
        instance.transcriber.loaded.return_value = True
        instance.transcriber.transcribe.return_value = "Dzie\u0144 dobry"
        instance.transcriber.last_timing = {"backend": "whisper"}
        instance._apply_text = mock.Mock(return_value="Dzie\u0144 dobry")
        instance._deliver_text = mock.Mock()
        instance._log = mock.Mock()

        instance._transcribe_worker_inner(mock.sentinel.audio)

        instance.transcriber.transcribe.assert_called_once_with(
            mock.sentinel.audio)
        instance._deliver_text.assert_called_once()

    def test_transcription_exception_text_is_not_logged_or_notified(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"model": "base.en"}
        instance.transcriber = mock.Mock()
        instance.transcriber.loaded.return_value = True
        instance.transcriber.transcribe.side_effect = RuntimeError(
            "decoded private transcript")
        instance._log = mock.Mock()
        instance.notify = mock.Mock()

        instance._transcribe_worker_inner(mock.sentinel.audio)

        messages = str(instance._log.mock_calls) + str(instance.notify.mock_calls)
        self.assertNotIn("decoded private transcript", messages)
        instance._log.assert_called_once_with(
            "transcription failed; recognizer error details suppressed")
        instance.notify.assert_called_once_with(
            "Transcription failed",
            "The local speech model could not complete this dictation. "
            "Try again, or choose another model in Settings.")

    def test_fallback_exception_text_is_not_logged_or_notified(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"model": "parakeet-tdt-0.6b-v3"}
        instance.transcriber = mock.Mock()
        instance.transcriber.loaded.return_value = True
        instance.transcriber.transcribe.side_effect = [
            RuntimeError("first private hypothesis"),
            RuntimeError("fallback private hypothesis"),
        ]
        instance._log = mock.Mock()
        instance.notify = mock.Mock()

        instance._transcribe_worker_inner(mock.sentinel.audio)

        messages = str(instance._log.mock_calls) + str(instance.notify.mock_calls)
        self.assertNotIn("private hypothesis", messages)
        self.assertEqual(
            instance._log.call_args_list,
            [
                mock.call(
                    "transcription failed; recognizer error details suppressed"),
                mock.call(
                    "fallback transcription failed; recognizer error details "
                    "suppressed"),
            ],
        )
        instance.notify.assert_has_calls([
            mock.call(
                "Parakeet failed",
                "Checking for an already-installed English-only Whisper "
                "base.en fallback. No model will be downloaded."),
            mock.call(
                "Transcription failed",
                "Neither local speech model could complete this dictation. "
                "Try again, or choose another model in Settings."),
        ])


class StartupTests(unittest.TestCase):
    def test_first_run_parakeet_stays_local_until_download_is_confirmed(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {
            "model": "parakeet-tdt-0.6b-v3",
            "setup_complete": False,
        }
        instance.transcriber = mock.Mock()
        instance.transcriber.load.side_effect = (
            app.engine.model_cache.ModelCacheMissingError("not cached"))
        instance.notify = mock.Mock()
        instance._set_indicator = mock.Mock()
        instance._model_retry_lock = __import__("threading").Lock()
        instance._model_load_generation = 0
        instance._model_load_target = None
        with mock.patch.object(app.engine, "cuda_available", return_value=True), \
                mock.patch.object(app.PresspeechApp, "_log") as log:
            instance._preload_model_worker()

        instance.transcriber.load.assert_called_once_with(
            "parakeet-tdt-0.6b-v3", notify=instance.notify, local_only=True,
            progress_callback=mock.ANY)
        instance.transcriber.warmup.assert_not_called()
        self.assertEqual(instance.model_status, "awaiting_download_consent")
        self.assertIn("2.5 GB", instance.model_status_detail)
        self.assertIsNone(instance._model_load_target)
        self.assertEqual(
            instance._set_indicator.mock_calls,
            [mock.call("loading"), mock.call(None)],
        )
        log.assert_any_call(
            "first-run model download deferred pending user choice")

    def test_first_run_cpu_model_stays_local_until_download_is_confirmed(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {
            "model": "parakeet-tdt-0.6b-v3",
            "setup_complete": False,
        }
        instance.transcriber = mock.Mock()
        instance.transcriber.load.side_effect = (
            app.engine.model_cache.ModelCacheMissingError("not cached"))
        instance.notify = mock.Mock()
        instance._set_indicator = mock.Mock()
        instance._model_retry_lock = __import__("threading").Lock()
        instance._model_load_generation = 0
        instance._model_load_target = None
        with mock.patch.object(app.engine, "cuda_available", return_value=False), \
                mock.patch.object(app.PresspeechApp, "_log"):
            instance._preload_model_worker()

        self.assertEqual(instance.settings["model"], "base.en")
        instance.transcriber.load.assert_called_once_with(
            "base.en", notify=instance.notify, local_only=True,
            progress_callback=mock.ANY)
        instance.transcriber.warmup.assert_not_called()
        self.assertEqual(instance.model_status, "awaiting_download_consent")
        self.assertIn("141 MiB", instance.model_status_detail)

    def test_parakeet_consent_does_not_authorize_cpu_default_download(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"model": "base.en", "setup_complete": False}
        instance._initial_model_download_consented = True
        instance._initial_model_download_consent_model = "parakeet-tdt-0.6b-v3"
        instance.transcriber = mock.Mock()
        instance.transcriber.load.side_effect = (
            app.engine.model_cache.ModelCacheMissingError("not cached"))
        instance.notify = mock.Mock()
        instance._set_indicator = mock.Mock()
        instance._model_retry_lock = threading.Lock()
        instance._model_load_generation = 0
        instance._model_load_target = None
        with mock.patch.object(app.PresspeechApp, "_log"):
            instance._preload_model_worker()

        instance.transcriber.load.assert_called_once_with(
            "base.en", notify=instance.notify, local_only=True,
            progress_callback=mock.ANY)
        self.assertEqual(instance.model_status, "awaiting_download_consent")

    def test_first_run_parakeet_uses_a_complete_local_snapshot_without_prompt(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {
            "model": "parakeet-tdt-0.6b-v3",
            "setup_complete": False,
        }
        instance.transcriber = mock.Mock()
        instance.notify = mock.Mock()
        instance._set_indicator = mock.Mock()
        instance._schedule_model_idle_unload = mock.Mock()
        instance._model_retry_lock = __import__("threading").Lock()
        instance._model_load_generation = 0
        instance._model_load_target = None
        with mock.patch.object(app.engine, "cuda_available", return_value=True), \
                mock.patch.object(app.PresspeechApp, "_log"):
            instance._preload_model_worker()

        instance.transcriber.load.assert_called_once_with(
            "parakeet-tdt-0.6b-v3", notify=instance.notify, local_only=True,
            progress_callback=mock.ANY)
        instance.transcriber.warmup.assert_called_once_with(
            seconds=app.MODEL_WARMUP_SEC, all_buckets=True)
        self.assertEqual(instance.model_status, "ready")
        instance._schedule_model_idle_unload.assert_called_once_with()

    def test_setup_download_choice_queues_exactly_the_waiting_model(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {
            "model": "parakeet-tdt-0.6b-v3", "setup_complete": False}
        instance.model_status = "awaiting_download_consent"
        instance._model_retry_lock = __import__("threading").Lock()
        instance._model_load_generation = 0
        instance._model_executor = mock.Mock()

        self.assertTrue(instance.confirm_initial_model_download())
        self.assertTrue(instance._initial_model_download_consented)
        self.assertEqual(instance._initial_model_download_consent_model,
                         "parakeet-tdt-0.6b-v3")
        self.assertEqual(instance.model_status, "loading")
        instance._model_executor.submit.assert_called_once_with(
            instance._preload_model_worker, "parakeet-tdt-0.6b-v3", 1)
        self.assertFalse(instance.confirm_initial_model_download())
        instance._model_executor.submit.assert_called_once()

        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"model": "base.en", "setup_complete": False}
        instance.model_status = "awaiting_download_consent"
        instance._model_retry_lock = __import__("threading").Lock()
        instance._model_load_generation = 0
        instance._model_executor = mock.Mock()
        self.assertTrue(instance.confirm_initial_model_download())
        self.assertTrue(instance._initial_model_download_consented)
        self.assertEqual(instance._initial_model_download_consent_model,
                         "base.en")
        instance._model_executor.submit.assert_called_once_with(
            instance._preload_model_worker, "base.en", 1)

    def test_confirmed_first_run_model_download_can_use_the_network(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {
            "model": "parakeet-tdt-0.6b-v3",
            "setup_complete": False,
        }
        instance._initial_model_download_consented = True
        instance._initial_model_download_consent_model = "parakeet-tdt-0.6b-v3"
        instance.transcriber = mock.Mock()
        instance.notify = mock.Mock()
        instance._set_indicator = mock.Mock()
        instance._schedule_model_idle_unload = mock.Mock()
        instance._model_retry_lock = __import__("threading").Lock()
        instance._model_load_generation = 1
        instance._model_load_target = "parakeet-tdt-0.6b-v3"

        with mock.patch.object(app.PresspeechApp, "_log"):
            instance._preload_model_worker("parakeet-tdt-0.6b-v3", 1)

        instance.transcriber.load.assert_called_once_with(
            "parakeet-tdt-0.6b-v3", notify=instance.notify,
            progress_callback=mock.ANY)
        self.assertEqual(instance.model_status, "ready")

    def test_model_load_callback_exposes_download_and_load_phases(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {
            "model": "parakeet-tdt-0.6b-v3",
            "setup_complete": True,
        }
        instance.transcriber = mock.Mock()
        instance.notify = mock.Mock()
        instance._set_indicator = mock.Mock()
        instance._schedule_model_idle_unload = mock.Mock()
        instance._model_retry_lock = __import__("threading").Lock()
        instance._model_load_generation = 1
        instance._model_load_target = "parakeet-tdt-0.6b-v3"
        phases = []

        def load(_model, *, progress_callback, **_kwargs):
            progress_callback("downloading")
            progress_callback("downloading", 3 * 1024 * 1024, 8 * 1024 * 1024)
            phases.append((instance.model_status_detail,
                           instance.model_download_progress))
            progress_callback("verifying")
            phases.append((instance.model_status_detail,
                           instance.model_download_progress))
            progress_callback("loading")
            phases.append((instance.model_status_detail,
                           instance.model_download_progress))

        def warmup(**_kwargs):
            phases.append((instance.model_status_detail,
                           instance.model_download_progress))

        instance.transcriber.load.side_effect = load
        instance.transcriber.warmup.side_effect = warmup
        with mock.patch.object(app.PresspeechApp, "_log"):
            instance._preload_model_worker("parakeet-tdt-0.6b-v3", 1)

        self.assertEqual(phases, [
            ("Downloading model files…", (3 * 1024 * 1024, 8 * 1024 * 1024)),
            ("Verifying model files…", None),
            ("Loading speech model…", None),
            ("Warming speech model…", None),
        ])
        self.assertEqual(instance.model_status, "ready")

    def test_setup_can_choose_and_persist_the_smaller_cpu_model(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {
            "model": "parakeet-tdt-0.6b-v3", "setup_complete": False}
        instance.model_status = "awaiting_download_consent"
        instance._model_retry_lock = __import__("threading").Lock()
        instance._model_load_generation = 0
        instance._model_executor = mock.Mock()

        with mock.patch.object(app.cfg, "save") as save:
            self.assertTrue(instance.select_cpu_model_after_download_declined())

        self.assertEqual(instance.settings["model"], "base.en")
        self.assertTrue(instance.settings["model_explicit"])
        self.assertTrue(instance._initial_model_download_consented)
        self.assertEqual(instance._initial_model_download_consent_model,
                         "base.en")
        save.assert_called_once_with(instance.settings)
        self.assertEqual(instance.model_status, "loading")
        instance._model_executor.submit.assert_called_once_with(
            instance._preload_model_worker, "base.en", 1)

    def test_hotkey_reopens_setup_instead_of_claiming_to_load_while_waiting(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"model": "parakeet-tdt-0.6b-v3"}
        instance.model_status = "awaiting_download_consent"
        instance.transcriber = mock.Mock()
        instance.transcriber.loaded.return_value = False
        instance.open_setup = mock.Mock()
        instance._set_indicator = mock.Mock()
        instance._log = mock.Mock()

        self.assertFalse(instance._dictation_model_ready())
        instance.open_setup.assert_called_once_with()
        instance._set_indicator.assert_not_called()

    def test_fresh_non_cuda_install_selects_cpu_model_without_network(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {
            "model": "parakeet-tdt-0.6b-v3",
            "setup_complete": False,
        }
        instance.transcriber = mock.Mock()
        instance.transcriber.load.side_effect = (
            app.engine.model_cache.ModelCacheMissingError("not cached"))
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
            "base.en", notify=instance.notify, local_only=True,
            progress_callback=mock.ANY)
        self.assertEqual(instance.model_status, "awaiting_download_consent")
        self.assertIn("141 MiB", instance.model_status_detail)
        instance.notify.assert_any_call(
            "CPU speech model selected",
            "NVIDIA CUDA is unavailable; using English-only Whisper "
            "base.en on CPU. "
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
        instance.settings = {
            "model": "parakeet-tdt-0.6b-v3",
            "setup_complete": True,
        }
        instance.transcriber = mock.Mock()
        instance.notify = mock.Mock()
        instance._set_indicator = mock.Mock()
        indicator_states = []
        instance._set_indicator.side_effect = lambda state: indicator_states.append(
            (state, instance.model_status))
        with mock.patch.object(app.PresspeechApp, "_log") as log:
            instance._preload_model_worker()
        instance.transcriber.load.assert_called_once_with(
            "parakeet-tdt-0.6b-v3", notify=instance.notify,
            progress_callback=mock.ANY)
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
        instance.settings = {
            "model": "parakeet-tdt-0.6b-v3",
            "setup_complete": True,
        }
        instance.transcriber = mock.Mock()
        instance.transcriber.load.side_effect = RuntimeError("model unavailable")
        instance.notify = mock.Mock()
        instance._set_indicator = mock.Mock()
        indicator_states = []
        instance._set_indicator.side_effect = lambda state: indicator_states.append(
            (state, instance.model_status))
        with mock.patch.object(app.PresspeechApp, "_log") as log:
            instance._preload_model_worker()
        self.assertEqual(instance.model_status, "error")
        self.assertNotIn("model unavailable", instance.model_status_detail)
        self.assertEqual(
            instance.model_status_detail,
            "Model load failed; use Retry Speech Model")
        self.assertTrue(all("model unavailable" not in str(call)
                            for call in log.call_args_list))
        self.assertEqual(
            indicator_states, [("loading", "loading"), (None, "error")])

    def test_superseded_load_does_not_revert_or_ready_the_new_selection(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"model": "small.en"}
        instance.model_status = "loading"
        instance.model_status_detail = "Loading small.en"
        instance._model_load_target = "small.en"
        instance._model_retry_lock = __import__("threading").Lock()
        instance._model_load_generation = 2
        instance.transcriber = mock.Mock()
        instance.notify = mock.Mock()
        instance._set_indicator = mock.Mock()
        instance._schedule_model_idle_unload = mock.Mock()

        with mock.patch.object(app.PresspeechApp, "_log"), \
                mock.patch.object(app.cfg, "save") as save:
            instance._preload_model_worker("base.en", 1)

        self.assertEqual(instance.model_status, "loading")
        self.assertEqual(instance.model_status_detail, "Loading small.en")
        self.assertEqual(instance._model_load_target, "small.en")
        save.assert_not_called()
        instance._schedule_model_idle_unload.assert_not_called()
        instance._set_indicator.assert_called_once_with("loading")

    def test_older_same_model_generation_cannot_finish_a_rapid_reversal(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {"model": "small.en"}
        instance.model_status = "loading"
        instance.model_status_detail = "Loading small.en"
        instance._model_load_target = "small.en"
        instance._model_retry_lock = __import__("threading").Lock()
        instance._model_load_generation = 3
        instance.transcriber = mock.Mock()
        instance.notify = mock.Mock()
        instance._set_indicator = mock.Mock()
        instance._schedule_model_idle_unload = mock.Mock()

        with mock.patch.object(app.PresspeechApp, "_log"):
            instance._preload_model_worker("small.en", 1)

        self.assertEqual(instance.model_status, "loading")
        self.assertEqual(instance._model_load_target, "small.en")
        instance._schedule_model_idle_unload.assert_not_called()




class DeliveryRecoveryTests(unittest.TestCase):
    """Delivery control-flow tests; never touch the clipboard or inject input."""
    def setUp(self):
        self.instance = app.PresspeechApp.__new__(app.PresspeechApp)
        self.instance.lock = threading.Lock()
        self.instance._log = mock.Mock()
        self.instance.notify = mock.Mock()
        self.instance._undelivered_dictations = []
        self.instance._undelivered_lock = __import__("threading").Lock()
        self.instance.open_delivery_recovery = mock.Mock(return_value=True)
        self.instance._injecting_keys = False
        self.target = app.PasteTarget("notepad.exe", 1234, 41)
        def patch(*args, **kwargs):
            patcher = mock.patch.object(*args, **kwargs)
            value = patcher.start()
            self.addCleanup(patcher.stop)
            return value
        self.copy = patch(app.clipboard_delivery, "write_text",
                          return_value=app.clipboard_delivery.WriteReceipt(101))
        self.owned = patch(app.clipboard_delivery, "is_current", return_value=True)
        self.checked_controller = app.keyboard_delivery.Controller
        self.controller = patch(app.keyboard_delivery, "Controller")
        self.keyboard = self.controller.return_value
        self.sleep = patch(app.time, "sleep")
        self.foreground = patch(
            app, "_foreground_paste_target", return_value=self.target)
        self.blocked = patch(
            app, "_paste_target_blocks_simulated_input", return_value=False)
        self.physical_keys_held = patch(
            app.keyboard_delivery, "paste_keys_held", return_value=False)

    def paste(self):
        return self.instance._paste("private transcript", self.target)

    def assert_retained_without_content_logs(self):
        self.assertEqual(self.instance._undelivered_dictations, ["private transcript"])
        self.assertNotIn("private transcript", str(self.instance._log.mock_calls))
        self.assertNotIn("private transcript", str(self.instance.notify.mock_calls))

    def test_unavailable_scratchpad_uses_private_recovery_without_clipboard(self):
        self.instance._remember_undelivered_dictation(
            "private transcript", "scratchpad-unavailable")

        self.assert_retained_without_content_logs()
        self.assertIn("private editor", str(self.instance.notify.mock_calls))
        self.instance.open_delivery_recovery.assert_called_once_with()
        self.copy.assert_not_called()
        self.controller.assert_not_called()

    def test_clipboard_failure_retains_text_and_never_constructs_keyboard(self):
        self.copy.side_effect = RuntimeError("private clipboard detail")
        self.assertFalse(self.paste())
        self.controller.assert_not_called()
        self.assert_retained_without_content_logs()
        self.assertNotIn("private clipboard detail", str(self.instance._log.mock_calls))

    def test_two_failed_focus_queries_preserve_prior_clipboard(self):
        # An unavailable GUI-thread query at capture and delivery must not
        # collapse into the completed-query, no-child window-only fallback.
        self.target = self.target._replace(focus_handle=None)
        self.foreground.return_value = self.target

        self.assertFalse(self.paste())

        self.copy.assert_not_called()
        self.controller.assert_not_called()
        self.assert_retained_without_content_logs()
        self.assertIn("focused control could not be verified",
                      str(self.instance.notify.mock_calls))

    def test_focus_query_failure_after_copy_never_sends_shortcut(self):
        # The transcript may already be on the current clipboard, but a
        # failed delivery-time query must not authorize keyboard insertion.
        failed_query = self.target._replace(focus_handle=None)
        self.foreground.side_effect = [self.target, failed_query]

        self.assertFalse(self.paste())

        self.copy.assert_called_once_with("private transcript")
        self.controller.assert_not_called()
        self.assert_retained_without_content_logs()

    def test_hook_held_paste_key_preserves_previous_clipboard(self):
        for key in app.keyboard_delivery._MODIFIER_KEYS:
            with self.subTest(key=key):
                self.instance._filter_pressed_vks = {key}
                self.instance._undelivered_dictations.clear()
                self.copy.reset_mock()
                self.assertFalse(self.paste())
                self.copy.assert_not_called()
                self.controller.assert_not_called()
                self.assert_retained_without_content_logs()

    def test_preexisting_physical_paste_key_preserves_previous_clipboard(self):
        # The low-level hook may miss a key that was already down before it
        # began listening; the Win32 preflight must still run before Copy.
        self.physical_keys_held.return_value = True

        self.assertFalse(self.paste())

        self.copy.assert_not_called()
        self.controller.assert_not_called()
        self.assertIn("no paste shortcut was sent",
                      str(self.instance.notify.mock_calls))
        self.assert_retained_without_content_logs()

    def test_unavailable_physical_state_preserves_previous_clipboard(self):
        self.physical_keys_held.side_effect = (
            app.keyboard_delivery.ModifierStateError("synthetic private detail"))

        self.assertFalse(self.paste())

        self.copy.assert_not_called()
        self.controller.assert_not_called()
        self.assertNotIn("synthetic private detail",
                         str(self.instance.notify.mock_calls))
        self.assert_retained_without_content_logs()

    def test_hook_key_pressed_after_clipboard_write_prevents_shortcut(self):
        with mock.patch.object(
                self.instance, "_paste_keys_held_in_hook",
                side_effect=[False, True]):
            self.assertFalse(self.paste())
        self.copy.assert_called_once()
        self.controller.assert_not_called()
        self.assert_retained_without_content_logs()

    def test_external_copy_immediately_after_write_stops_delivery(self):
        self.owned.return_value = False
        self.assertFalse(self.paste())
        self.sleep.assert_not_called()
        self.controller.assert_not_called()
        self.assert_retained_without_content_logs()
        self.copy.assert_called_once()

    def test_external_copy_during_delay_stops_delivery(self):
        self.owned.side_effect = [True, False]
        self.assertFalse(self.paste())
        self.controller.assert_not_called()
        self.assert_retained_without_content_logs()

    def test_external_copy_before_shortcut_never_injects_keys(self):
        self.owned.side_effect = [True, True, False]
        self.assertFalse(self.paste())
        self.keyboard.shortcut.assert_not_called()
        self.keyboard.release.assert_not_called()
        self.assertFalse(self.instance._injecting_keys)
        self.assert_retained_without_content_logs()

    def test_external_copy_at_controller_submission_stops_shortcut(self):
        # Construction and the modifier query happen after the app's final
        # receipt check. The controller's own guard closes that interval.
        self.owned.side_effect = [True, True, True, False]
        api = mock.Mock()
        api.MapVirtualKeyW.return_value = 0x1D
        api.GetAsyncKeyState.return_value = 0
        self.controller.return_value = self.checked_controller(api=api)

        self.assertFalse(self.paste())
        api.SendInput.assert_not_called()
        self.assertFalse(self.instance._injecting_keys)
        self.assert_retained_without_content_logs()
        self.assertIn("no paste shortcut was sent",
                      str(self.instance.notify.mock_calls))

    def test_focus_change_during_controller_creation_never_sends_paste(self):
        replacement = app.PasteTarget("other.exe", 4321, 99)
        api = mock.Mock()
        api.MapVirtualKeyW.return_value = 0x1D
        api.GetAsyncKeyState.return_value = 0

        def construct():
            self.foreground.return_value = replacement
            return self.checked_controller(api=api)

        self.controller.side_effect = construct
        self.assertFalse(self.paste())
        api.SendInput.assert_not_called()
        self.assert_retained_without_content_logs()
        self.assertIn("focused control could not be verified",
                      str(self.instance.notify.mock_calls))
        self.assertNotIn("partly completed", str(self.instance.notify.mock_calls))

    def test_hook_key_pressed_during_shortcut_setup_never_sends_paste(self):
        api = mock.Mock()

        def map_key(_key, _mode):
            self.instance._filter_pressed_vks = {app.keyboard_delivery.VK_V}
            return 0x1D

        api.MapVirtualKeyW.side_effect = map_key
        api.GetAsyncKeyState.return_value = 0
        self.controller.return_value = self.checked_controller(api=api)
        self.assertFalse(self.paste())
        api.SendInput.assert_not_called()
        self.assert_retained_without_content_logs()
        self.assertIn("key was held", str(self.instance.notify.mock_calls))
        self.assertNotIn("partly completed", str(self.instance.notify.mock_calls))

    def test_unavailable_final_check_never_sends_paste_or_error_detail(self):
        api = mock.Mock()
        api.MapVirtualKeyW.return_value = 0x1D
        api.GetAsyncKeyState.return_value = 0

        def construct():
            self.foreground.side_effect = OSError("private window title")
            return self.checked_controller(api=api)

        self.controller.side_effect = construct
        self.assertFalse(self.paste())
        api.SendInput.assert_not_called()
        self.assert_retained_without_content_logs()
        self.assertIn("final delivery check could not be completed",
                      str(self.instance.notify.mock_calls))
        self.assertNotIn("private window title",
                         str(self.instance.notify.mock_calls))

    def test_external_copy_during_shortcut_is_reported_uncertain(self):
        # A successful SendInput return is not a paste-consumed receipt.
        self.owned.side_effect = [True, True, True, True, False]
        api = mock.Mock()
        api.MapVirtualKeyW.return_value = 0x1D
        api.GetAsyncKeyState.return_value = 0
        api.SendInput.return_value = 4
        self.controller.return_value = self.checked_controller(api=api)

        self.assertFalse(self.paste())
        api.SendInput.assert_called_once()
        self.assert_retained_without_content_logs()
        self.assertIn("may have run fully or partly",
                      str(self.instance.notify.mock_calls))

    def test_clipboard_and_focus_change_during_shortcut_warns_about_both(self):
        # The post-submit receipt check runs before the post-submit focus
        # check. An external copy must not hide a simultaneous destination
        # change from the user-facing recovery instructions.
        replacement = app.PasteTarget("other.exe", 4321, 99)
        self.owned.side_effect = [True, True, True, True, False]
        api = mock.Mock()
        api.MapVirtualKeyW.return_value = 0x1D
        api.GetAsyncKeyState.return_value = 0

        def accept_then_change_both(count, _inputs, _size):
            self.foreground.return_value = replacement
            return count

        api.SendInput.side_effect = accept_then_change_both
        self.controller.return_value = self.checked_controller(api=api)

        self.assertFalse(self.paste())

        api.SendInput.assert_called_once()
        self.copy.assert_called_once_with("private transcript")
        self.assert_retained_without_content_logs()
        notice = str(self.instance.notify.mock_calls)
        self.assertIn("any field that may have gained focus", notice)
        self.assertIn(
            "check the current clipboard before choosing Copy or Discard", notice)
        self.assertNotIn("no paste shortcut was sent", notice)
        self.assertFalse(self.instance._injecting_keys)

    def test_focus_change_during_accepted_shortcut_is_reported_uncertain(self):
        # SendInput can accept the chord while the foreground changes; its
        # return value does not identify the field that consumes the paste.
        replacement = app.PasteTarget("other.exe", 4321, 99)
        api = mock.Mock()
        api.MapVirtualKeyW.return_value = 0x1D
        api.GetAsyncKeyState.return_value = 0

        def accept_then_change_focus(count, _inputs, _size):
            self.foreground.return_value = replacement
            return count

        api.SendInput.side_effect = accept_then_change_focus
        self.controller.return_value = self.checked_controller(api=api)

        self.assertFalse(self.paste())

        api.SendInput.assert_called_once()
        self.copy.assert_called_once_with("private transcript")
        self.assertFalse(self.instance._injecting_keys)
        self.assert_retained_without_content_logs()
        self.instance.open_delivery_recovery.assert_called_once_with()
        self.assertIn("Text may have reached the original field or a different field",
                      str(self.instance.notify.mock_calls))
        self.assertIn("any field that may have gained focus",
                      str(self.instance.notify.mock_calls))
        self.assertNotIn("no paste shortcut was sent",
                         str(self.instance.notify.mock_calls))
        self.assertIn("paste outcome uncertain; original target could not be verified",
                      str(self.instance._log.mock_calls))
        self.assertNotIn("paste skipped", str(self.instance._log.mock_calls))

    def test_failed_focus_query_after_shortcut_is_not_reported_as_success(self):
        api = mock.Mock()
        api.MapVirtualKeyW.return_value = 0x1D
        api.GetAsyncKeyState.return_value = 0

        def accept_then_lose_focus_query(count, _inputs, _size):
            self.foreground.return_value = self.target._replace(
                focus_handle=None)
            return count

        api.SendInput.side_effect = accept_then_lose_focus_query
        self.controller.return_value = self.checked_controller(api=api)

        self.assertFalse(self.paste())

        api.SendInput.assert_called_once()
        self.assert_retained_without_content_logs()
        self.assertIn("could not be verified after the paste shortcut was sent",
                      str(self.instance.notify.mock_calls))

    def test_target_becomes_elevated_after_preflight_still_blocks_paste(self):
        self.blocked.side_effect = [False, True]

        self.assertFalse(self.paste())

        self.copy.assert_called_once_with("private transcript")
        self.controller.assert_not_called()
        self.assert_retained_without_content_logs()

    def test_controller_failure_does_not_claim_current_clipboard_contains_text(self):
        self.controller.side_effect = RuntimeError("synthetic controller failure")
        self.assertFalse(self.paste())
        self.assert_retained_without_content_logs()
        self.assertNotIn("remains on the clipboard", str(self.instance.notify.mock_calls))
        self.assertIn("delivery could not be verified",
                      str(self.instance.notify.mock_calls))

    def test_held_modifier_retains_text_without_shortcut_or_keyup_cleanup(self):
        self.keyboard.shortcut.side_effect = (
            app.keyboard_delivery.ModifierHeldError("modifier held"))

        self.assertFalse(self.paste())

        self.keyboard.release.assert_not_called()
        self.assertFalse(self.instance._injecting_keys)
        self.assertIn("no paste shortcut was sent",
                      str(self.instance.notify.mock_calls))
        self.assert_retained_without_content_logs()

    def test_unavailable_modifier_snapshot_retains_without_keyup_cleanup(self):
        self.keyboard.shortcut.side_effect = (
            app.keyboard_delivery.ModifierStateError("state unavailable"))

        self.assertFalse(self.paste())

        self.keyboard.release.assert_not_called()
        self.assertFalse(self.instance._injecting_keys)
        self.assertIn("no paste shortcut was sent",
                      str(self.instance.notify.mock_calls))
        self.assert_retained_without_content_logs()

    def test_ambiguous_shortcut_failure_releases_every_possible_down_key(self):
        self.keyboard.shortcut.side_effect = RuntimeError("side effect then failure")
        self.assertFalse(self.paste())
        self.assertEqual(self.keyboard.release.call_args_list, [
            mock.call(app.keyboard_delivery.VK_V),
            mock.call(app.keyboard_delivery.VK_LCONTROL),
        ])
        self.assertFalse(self.instance._injecting_keys)
        self.assert_retained_without_content_logs()

    def test_partial_native_shortcut_retains_and_attempts_release(self):
        api = mock.Mock()
        api.MapVirtualKeyW.return_value = 0x1D
        api.GetAsyncKeyState.return_value = 0
        api.SendInput.side_effect = [2, 1, 1]
        self.controller.return_value = self.checked_controller(api=api)

        self.assertFalse(self.paste())

        self.assertEqual(api.SendInput.call_count, 3)
        self.assertFalse(self.instance._injecting_keys)
        self.assert_retained_without_content_logs()

    def test_rejected_batch_skips_cleanup_but_partial_batch_keeps_it(self):
        # SendInput reports an accepted count. A zero result must not send
        # key-ups that could disturb newly held user keys; nonzero partial
        # results still need conservative release of every possible down key.
        for accepted, expected in (
                (0, ()),
                (1, (app.keyboard_delivery.VK_V,
                     app.keyboard_delivery.VK_LCONTROL)),
                (2, (app.keyboard_delivery.VK_V,
                     app.keyboard_delivery.VK_LCONTROL)),
                (3, (app.keyboard_delivery.VK_V,
                     app.keyboard_delivery.VK_LCONTROL))):
            with self.subTest(accepted=accepted):
                self.instance._undelivered_dictations.clear()
                api = mock.Mock()
                api.MapVirtualKeyW.return_value = 0x1D
                api.GetAsyncKeyState.return_value = 0
                api.SendInput.side_effect = [accepted, *([1] * len(expected))]
                keyboard = self.checked_controller(api=api)
                self.controller.return_value = keyboard
                with mock.patch.object(
                        keyboard, "release", wraps=keyboard.release) as release:
                    self.assertFalse(self.paste())
                self.assertEqual(
                    release.call_args_list,
                    [mock.call(key) for key in expected])
                self.assertEqual(api.SendInput.call_count, 1 + len(expected))
                self.assertFalse(self.instance._injecting_keys)
                self.assert_retained_without_content_logs()

    def test_shortcut_preparation_failure_does_not_inject_cleanup(self):
        api = mock.Mock()
        api.MapVirtualKeyW.side_effect = OSError("private mapping detail")
        api.GetAsyncKeyState.return_value = 0
        keyboard = self.checked_controller(api=api)
        self.controller.return_value = keyboard

        with mock.patch.object(keyboard, "release", wraps=keyboard.release) as release:
            self.assertFalse(self.paste())

        release.assert_not_called()
        api.SendInput.assert_not_called()
        self.assert_retained_without_content_logs()
        self.assertNotIn("private mapping detail", str(self.instance.notify.mock_calls))

    def test_native_shortcut_exception_keeps_best_effort_cleanup(self):
        api = mock.Mock()
        api.MapVirtualKeyW.return_value = 0x1D
        api.GetAsyncKeyState.return_value = 0
        api.SendInput.side_effect = [OSError("private native detail"), 1, 1]
        keyboard = self.checked_controller(api=api)
        self.controller.return_value = keyboard

        with mock.patch.object(keyboard, "release", wraps=keyboard.release) as release:
            self.assertFalse(self.paste())

        self.assertEqual(release.call_args_list, [
            mock.call(app.keyboard_delivery.VK_V),
            mock.call(app.keyboard_delivery.VK_LCONTROL),
        ])
        self.assert_retained_without_content_logs()
        self.assertNotIn("private native detail", str(self.instance.notify.mock_calls))

    def test_failed_cleanup_release_is_retried_and_modifiers_released(self):
        self.keyboard.shortcut.side_effect = RuntimeError("uncertain")
        self.keyboard.release.side_effect = [RuntimeError("uncertain"), None, None]
        self.assertFalse(self.paste())
        self.assertEqual(self.keyboard.release.call_args_list, [
            mock.call(app.keyboard_delivery.VK_V),
            mock.call(app.keyboard_delivery.VK_V),
            mock.call(app.keyboard_delivery.VK_LCONTROL)])
        self.assertFalse(self.instance._injecting_keys)
        self.assert_retained_without_content_logs()

    def test_successful_shortcut_does_not_claim_consumption(self):
        self.assertTrue(self.paste())
        self.assertFalse(self.instance.has_undelivered_dictation())
        self.instance.notify.assert_not_called()

    def test_moonlight_shortcut_is_submitted_as_one_complete_chord(self):
        self.target = app.PasteTarget("moonlight.exe", 1234, 41)
        self.foreground.return_value = self.target

        self.assertTrue(self.paste())

        self.keyboard.shortcut.assert_called_once()
        self.assertEqual(self.keyboard.shortcut.call_args.args, ([
            app.keyboard_delivery.VK_LCONTROL,
            app.keyboard_delivery.VK_LMENU,
            app.keyboard_delivery.VK_LSHIFT,
        ], app.keyboard_delivery.VK_V))
        self.assertTrue(callable(
            self.keyboard.shortcut.call_args.kwargs["before_submit"]))
        self.keyboard.press.assert_not_called()

    def test_explicit_copy_failure_or_new_owner_preserves_recovery(self):
        self.instance._undelivered_dictations = ["private transcript"]
        self.copy.side_effect = RuntimeError("clipboard locked")
        self.assertFalse(self.instance.copy_undelivered_dictation())
        self.assert_retained_without_content_logs()
        self.copy.side_effect = None
        self.owned.return_value = False
        self.assertFalse(self.instance.copy_undelivered_dictation())
        self.assert_retained_without_content_logs()
        self.controller.assert_not_called()

    def test_explicit_owned_copy_clears_only_the_copied_transcript(self):
        self.instance._undelivered_dictations = ["private transcript", "next transcript"]
        self.assertTrue(self.instance.copy_undelivered_dictation())
        self.copy.assert_called_once_with("private transcript")
        self.assertEqual(self.instance._undelivered_dictations, ["next transcript"])
        self.controller.assert_not_called()

    def test_discard_does_not_write_clipboard_and_preserves_later_recovery(self):
        self.instance._undelivered_dictations = [
            "private transcript", "next transcript"]
        self.assertTrue(self.instance.discard_undelivered_dictation())
        self.assertEqual(
            self.instance._undelivered_dictations, ["next transcript"])
        self.assertIn(
            "Another dictation is waiting", str(self.instance.notify.mock_calls))
        self.copy.assert_not_called()
        self.controller.assert_not_called()

    def test_discard_last_recovery_unblocks_capture(self):
        self.instance._undelivered_dictations = ["private transcript"]
        self.assertTrue(self.instance.discard_undelivered_dictation())
        self.assertFalse(self.instance.has_undelivered_dictation())
        self.assertIn("You can record again", str(self.instance.notify.mock_calls))
        self.copy.assert_not_called()

    def test_stale_discard_does_not_claim_that_text_was_discarded(self):
        self.assertFalse(self.instance.discard_undelivered_dictation())
        self.assertEqual(
            self.instance.notify.call_args.args,
            ("No undelivered dictation", "There is nothing waiting to discard."))
        self.copy.assert_not_called()

    def test_repeated_launch_opens_recovery_without_copying_retained_text(self):
        self.instance._undelivered_dictations = ["private transcript"]
        self.instance.settings = {"setup_complete": True}
        self.instance.update_window = None
        self.instance.setup_window = None
        self.instance.settings_window = None
        self.instance.scratchpad = None
        self.instance.open_settings = mock.Mock()
        self.instance._activate_from_launch()
        self.instance.open_delivery_recovery.assert_called_once_with()
        self.instance.open_settings.assert_not_called()
        self.copy.assert_not_called()
        self.assert_retained_without_content_logs()

    def test_retention_opens_recovery_surface_after_private_text_is_secured(self):
        self.instance._remember_undelivered_dictation(
            "private transcript", "clipboard-unavailable")

        self.assert_retained_without_content_logs()
        self.instance.open_delivery_recovery.assert_called_once_with()

    def test_recovery_command_constructs_once_then_restores_existing_window(self):
        # Exercise the implementation rather than the per-test mock above.
        self.instance._undelivered_dictations = ["private transcript"]
        self.instance._delivery_recovery_window_lock = threading.Lock()
        self.instance.delivery_recovery_window = None
        window = mock.Mock()
        def create_window(owner):
            owner.delivery_recovery_window = window
            return window
        with mock.patch.object(
                app.ui, "DeliveryRecoveryWindow", side_effect=create_window) as create, \
                mock.patch.object(app.ui, "present_window") as present:
            self.assertTrue(app.PresspeechApp.open_delivery_recovery(self.instance))
            self.assertTrue(app.PresspeechApp.open_delivery_recovery(self.instance))

        create.assert_called_once_with(self.instance)
        present.assert_called_once_with(window)
        self.copy.assert_not_called()

    def test_ui_close_before_constructor_returns_does_not_restore_dead_window(self):
        self.instance._undelivered_dictations = ["private transcript"]
        self.instance._delivery_recovery_window_lock = threading.Lock()
        self.instance.delivery_recovery_window = None
        host = mock.Mock()
        roots = []

        def build_window(window):
            window.root = mock.Mock()
            roots.append(window.root)

        def build_then_close(command):
            # The UI thread completes the queued build and handles Close
            # before the calling worker resumes after submit().
            command()
            command.__self__._close()

        host.submit.side_effect = build_then_close
        with mock.patch.object(app.ui, "_window_host", return_value=host), \
                mock.patch.object(app.ui.DeliveryRecoveryWindow,
                                  "_build_window", build_window), \
                mock.patch.object(app.ui, "present_window") as present:
            self.assertFalse(app.PresspeechApp.open_delivery_recovery(self.instance))
            self.assertIsNone(self.instance.delivery_recovery_window)
            roots[0].destroy.assert_called_once_with()
            # A later request must construct a new usable surface.
            host.submit.side_effect = lambda command: command()
            self.assertTrue(app.PresspeechApp.open_delivery_recovery(self.instance))
            reopened = self.instance.delivery_recovery_window
            self.assertIs(reopened.root, roots[1])
            self.assertTrue(app.PresspeechApp.open_delivery_recovery(self.instance))
            present.assert_called_once_with(reopened)
        self.assertEqual(self.instance._undelivered_dictations, ["private transcript"])
        self.copy.assert_not_called()

    def test_recovery_window_failure_keeps_text_and_redacts_details(self):
        self.instance._undelivered_dictations = ["private transcript"]
        self.instance._delivery_recovery_window_lock = threading.Lock()
        self.instance.delivery_recovery_window = None
        with mock.patch.object(
                app.ui, "DeliveryRecoveryWindow",
                side_effect=OSError(r"C:\private\desktop unavailable")):
            self.assertFalse(
                app.PresspeechApp.open_delivery_recovery(self.instance))

        self.assert_retained_without_content_logs()
        self.assertNotIn("private", str(self.instance._log.mock_calls))
        self.assertEqual(
            self.instance.notify.call_args.args[0],
            "Delivery Recovery unavailable")
        self.copy.assert_not_called()

    def test_async_window_failure_cannot_reinstall_the_failed_window(self):
        self.instance._undelivered_dictations = ["private transcript"]
        self.instance._delivery_recovery_window_lock = threading.Lock()
        self.instance.delivery_recovery_window = None

        class FailingBetweenChecks:
            def __init__(window, owner):
                window.owner = owner
                window.checks = 0
                owner.delivery_recovery_window = window

            @property
            def _build_failed(window):
                window.checks += 1
                if window.checks == 1:
                    # Model the UI-thread failure callback winning immediately
                    # after the caller's first state check.
                    window.owner.delivery_recovery_window = None
                    return False
                return True

        with mock.patch.object(
                app.ui, "DeliveryRecoveryWindow",
                side_effect=FailingBetweenChecks):
            self.assertFalse(
                app.PresspeechApp.open_delivery_recovery(self.instance))

        self.assertIsNone(self.instance.delivery_recovery_window)
        self.copy.assert_not_called()

    def test_pending_recovery_blocks_new_capture_before_device_or_model_work(self):
        self.instance._undelivered_dictations = ["private transcript"]
        self.instance._dictation_model_ready = mock.Mock()
        self.assertFalse(self.instance.start_recording())
        self.instance._dictation_model_ready.assert_not_called()
        self.instance.open_delivery_recovery.assert_called_once_with()
        self.copy.assert_not_called()
        self.assert_retained_without_content_logs()

    def test_exiting_discards_recovery_and_rejects_late_retention(self):
        self.instance._undelivered_dictations = ["private transcript"]
        self.instance._restore_playback_after_recording = mock.Mock()
        self.instance.indicator = mock.Mock()
        self.instance.listener = None
        self.instance.update_window = None
        self.instance.settings_window = None
        self.instance.setup_window = None
        self.instance.scratchpad = None
        self.instance.icon = None
        with mock.patch.object(app.os, "_exit") as terminate:
            self.instance.exit_app()
        terminate.assert_called_once_with(0)
        self.instance._remember_undelivered_dictation(
            "private transcript", "clipboard-unavailable")
        self.assertFalse(self.instance.has_undelivered_dictation())
        self.copy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
