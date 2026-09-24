import sys
import types
import unittest
import queue
import inspect
import threading
from unittest import mock

try:
    import tkinter  # noqa: F401
except ModuleNotFoundError:
    # Keep these model-free state tests portable to minimal Python workers.
    tkinter = types.ModuleType("tkinter")
    tkinter.TclError = RuntimeError
    tkinter.messagebox = mock.Mock()
    tkinter.ttk = mock.Mock()
    sys.modules["tkinter"] = tkinter

import ui


class UpdateInstallRecoveryTests(unittest.TestCase):
    """The verified installer stays available when dictation blocks an exit."""

    def make_window(self):
        window = ui.UpdateWindow.__new__(ui.UpdateWindow)
        window.app = mock.Mock()
        window.update = {"version": "0.1.13"}
        window.root = mock.Mock()
        window.events = queue.Queue()
        window.cancel_download = threading.Event()
        window.download_lock = threading.Lock()
        window.downloaded_installer = "verified-installer.exe"
        window._remove_downloaded_installer = mock.Mock()
        window.download_finished = threading.Event()
        window.download_finished.set()
        window.status = mock.Mock()
        window.progress = mock.MagicMock()
        window.progress.__getitem__.return_value = 100
        window.download_button = mock.Mock()
        window.events.put(("ready", window.downloaded_installer))
        return window

    def test_busy_install_keeps_verified_file_and_exposes_retry(self):
        window = self.make_window()
        window.app.launch_update.side_effect = [
            ui.updates.UpdateInstallBusy(
                "Copy or discard the waiting dictation before installing."),
            None,
        ]
        with mock.patch.object(ui.messagebox, "askyesno", return_value=True), \
                mock.patch.object(ui.messagebox, "showwarning") as warning:
            window._poll()
            self.assertEqual(
                window.downloaded_installer, "verified-installer.exe")
            window._remove_downloaded_installer.assert_not_called()
            window.download_button.config.assert_called_with(
                text="Install Update", command=window._install_ready,
                state="normal")
            self.assertIn(
                "verified installer remains ready",
                window.status.config.call_args.kwargs["text"].lower())
            warning.assert_called_once()
            window._install_ready()
        self.assertEqual(window.app.launch_update.call_count, 2)
        window._remove_downloaded_installer.assert_not_called()

    def test_decline_discards_verified_file_without_launch(self):
        window = self.make_window()
        with mock.patch.object(ui.messagebox, "askyesno", return_value=False):
            window._poll()
        window.app.launch_update.assert_not_called()
        window._remove_downloaded_installer.assert_called_once_with(
            "verified-installer.exe")
        window.download_button.config.assert_called_with(
            text="Download Update", command=window._download, state="normal")

    def test_close_during_confirmation_cannot_launch(self):
        window = self.make_window()

        def close_then_approve(*_args, **_kwargs):
            window._close()
            return True

        with mock.patch.object(
                ui.messagebox, "askyesno", side_effect=close_then_approve):
            window._poll()
        window.app.launch_update.assert_not_called()
        window._remove_downloaded_installer.assert_called_once_with(
            "verified-installer.exe")
        window.root.after.assert_not_called()


class WindowHostPrivacyTests(unittest.TestCase):
    def make_host(self):
        host = ui._WindowHost.__new__(ui._WindowHost)
        host.commands = queue.Queue()
        host.root = None
        host.failure = None
        host.callback_failures = 0
        host._next_callback_notice_at = 0.0
        host.accessibility = "not initialized"
        host.ready = threading.Event()
        root = mock.Mock()
        with mock.patch.object(ui.tk, "Tk", return_value=root, create=True), \
                mock.patch.object(ui, "_watch_windows_text_scale"), \
                mock.patch.object(ui.tk_uia, "enable", return_value=types.SimpleNamespace(name="provided")):
            host._run()
        self.assertTrue(host.ready.is_set())
        self.assertIsNone(host.failure)
        self.assertEqual(root.report_callback_exception, host._report_callback_failure)
        return host, root

    def test_queued_and_native_tk_callback_errors_hide_private_details(self):
        host, root = self.make_host()
        secret = "synthetic-private-transcript-and-path"

        def fail():
            raise RuntimeError(secret)

        host.commands.put(fail)
        poll = root.after.call_args_list[0].args[1]
        with mock.patch.object(ui.messagebox, "showerror") as showerror:
            poll()
            # Tk itself calls this hook for ordinary widget callbacks.
            root.report_callback_exception(RuntimeError, RuntimeError(secret), None)

        self.assertEqual(host.callback_failures, 2)
        showerror.assert_called_once()
        title, message = showerror.call_args.args
        self.assertEqual(title, "Presspeech action failed")
        self.assertIn("Check the intended field and current clipboard", message)
        self.assertNotIn(secret, str(showerror.call_args))
        self.assertEqual(showerror.call_args.kwargs, {"parent": root})

    def test_failed_generic_notice_never_rethrows_original_error(self):
        host, root = self.make_host()
        with mock.patch.object(
                ui.messagebox, "showerror",
                side_effect=OSError("synthetic-private-dialog-detail")) as showerror:
            root.report_callback_exception(
                RuntimeError, RuntimeError("synthetic-private-transcript"), None)
            root.report_callback_exception(
                RuntimeError, RuntimeError("another-private-transcript"), None)
        self.assertEqual(host.callback_failures, 2)
        showerror.assert_called_once()

    def test_generic_notice_can_recur_after_rate_limit(self):
        host, root = self.make_host()
        with mock.patch.object(ui.time, "monotonic", side_effect=[100.0, 120.0, 161.0]), \
                mock.patch.object(ui.messagebox, "showerror") as showerror:
            for _ in range(3):
                root.report_callback_exception(
                    RuntimeError, RuntimeError("synthetic-private-transcript"), None)
        self.assertEqual(host.callback_failures, 3)
        self.assertEqual(showerror.call_count, 2)
        self.assertNotIn("synthetic-private", str(showerror.call_args_list))

    def test_host_startup_failure_does_not_chain_private_exception(self):
        host = ui._WindowHost.__new__(ui._WindowHost)
        host.commands = queue.Queue()
        host.root = None
        host.failure = None
        host.ready = threading.Event()
        with mock.patch.object(
                ui.tk, "Tk", create=True,
                side_effect=OSError("synthetic-private-user-path")):
            host._run()

        self.assertTrue(host.ready.is_set())
        self.assertIs(host.failure, True)
        with self.assertRaises(RuntimeError) as raised:
            host.submit(lambda: None)
        self.assertEqual(
            str(raised.exception), "Presspeech could not start its window system")
        self.assertIsNone(raised.exception.__cause__)
        self.assertTrue(raised.exception.__suppress_context__)

    def test_only_aggregate_callback_failures_enter_diagnostics(self):
        with mock.patch.object(ui, "_WINDOW_HOST", None):
            self.assertEqual(ui.callback_failure_count(), 0)
        host, _root = self.make_host()
        host.callback_failures = 3
        with mock.patch.object(ui, "_WINDOW_HOST", host):
            self.assertEqual(ui.callback_failure_count(), 3)


class WindowCallbackLifetimeTests(unittest.TestCase):
    class Interpreter:
        def __init__(self):
            self.pending = {}
            self.next_id = 0

        def drain(self):
            ready, self.pending = self.pending, {}
            for owner, callback, args in ready.values():
                if owner.destroyed:
                    raise RuntimeError("Tcl callback command deleted by destroy")
                callback(*args)

    class Root:
        def __init__(self, interpreter):
            self.interpreter = interpreter
            self.destroyed = False
            self.cancelled = []
            self.blocking_delays = []

        def after(self, delay, callback=None, *args):
            if callback is None:
                self.blocking_delays.append(delay)
                return None
            self.interpreter.next_id += 1
            identifier = str(self.interpreter.next_id)
            self.interpreter.pending[identifier] = (self, callback, args)
            return identifier

        def after_idle(self, callback, *args):
            return self.after("idle", callback, *args)

        def after_cancel(self, identifier):
            self.cancelled.append(identifier)
            self.interpreter.pending.pop(identifier, None)

        def destroy(self):
            self.destroyed = True

    def setUp(self):
        self.interpreter = self.Interpreter()
        self.root = self.Root(self.interpreter)
        self.lifetime = ui._WindowCallbacks(self.root)

    def test_destroy_cancels_delayed_and_idle_commands_but_preserves_other_windows(self):
        calls = []
        sibling = self.Root(self.interpreter)
        host = self.Root(self.interpreter)
        ui._WindowCallbacks(sibling)
        delayed = self.root.after(250, calls.append, "closed poll")
        idle = self.root.after_idle(calls.append, "closed focus")
        sibling.after(250, calls.append, "sibling")
        host.after(25, calls.append, "host")

        self.root.destroy()
        self.interpreter.drain()

        self.assertEqual(calls, ["sibling", "host"])
        self.assertCountEqual(self.root.cancelled, [delayed, idle])
        self.assertFalse(self.lifetime._pending)

    def test_completed_and_explicitly_cancelled_callbacks_do_not_accumulate(self):
        called = mock.Mock()
        completed = self.root.after(0, called, "argument")
        self.interpreter.drain()
        called.assert_called_once_with("argument")
        self.assertFalse(self.lifetime._pending)
        cancelled = self.root.after_idle(called)
        self.root.after_cancel(cancelled)
        self.root.destroy()
        self.assertEqual(self.root.cancelled, [cancelled])
        self.assertNotIn(completed, self.root.cancelled)
        self.interpreter.drain()
        called.assert_called_once()

    def test_recurring_poll_cancels_only_its_pending_successor(self):
        def poll():
            self.root.after(250, poll)
        self.root.after(250, poll)
        for _ in range(20):
            self.interpreter.drain()
            self.assertEqual(len(self.lifetime._pending), 1)
        successor = next(iter(self.lifetime._pending))
        self.root.destroy()
        self.interpreter.drain()
        self.assertEqual(self.root.cancelled, [successor])

    def test_callback_that_closes_window_is_retired_before_destruction(self):
        self.root.after_idle(self.root.destroy)
        self.interpreter.drain()
        self.assertTrue(self.root.destroyed)
        self.assertFalse(self.lifetime._pending)
        self.assertEqual(self.root.cancelled, [])

    def test_callback_exception_does_not_retain_completed_identifier(self):
        self.root.after(1, mock.Mock(side_effect=RuntimeError("callback failed")))
        with self.assertRaisesRegex(RuntimeError, "callback failed"):
            self.interpreter.drain()
        self.assertFalse(self.lifetime._pending)
        self.root.destroy()
        self.assertEqual(self.root.cancelled, [])

    def test_every_interactive_dialog_installs_callback_lifetime(self):
        root = mock.Mock()
        with mock.patch.object(ui.tk, "Toplevel", return_value=root, create=True), \
                mock.patch.object(ui, "_window_host"), \
                mock.patch.object(ui, "_WindowCallbacks") as lifetime:
            self.assertIs(ui._interactive_window("Test"), root)
        lifetime.assert_called_once_with(root)
        root.title.assert_called_once_with("Test")

    def test_named_tk_scheduling_and_cancellation_arguments_are_preserved(self):
        callback = mock.Mock()
        identifier = self.root.after(ms=1, func=callback)
        self.root.after_cancel(id=identifier)
        self.interpreter.drain()
        callback.assert_not_called()
        self.assertFalse(self.lifetime._pending)

    def test_blocking_after_retains_existing_contract(self):
        self.assertIsNone(self.root.after(3))
        self.assertEqual(self.root.blocking_delays, [3])
        self.assertFalse(self.lifetime._pending)

    def test_failed_recovery_build_cancels_callbacks_before_destroy(self):
        window = ui.DeliveryRecoveryWindow.__new__(ui.DeliveryRecoveryWindow)
        window.app = mock.Mock()
        window.root = self.root
        def fail():
            self.root.after(500, lambda: None)
            raise RuntimeError("build failed")
        window._build_window = fail
        with self.assertRaisesRegex(RuntimeError, "build failed"):
            window._build()
        self.interpreter.drain()
        self.assertTrue(self.root.destroyed)
        self.assertFalse(self.lifetime._pending)


class AccessibleWindowTests(unittest.TestCase):
    def test_indicator_distinguishes_blank_decode_from_vad_rejection(self):
        self.assertEqual(
            ui.DictationIndicator._STATES["no_text"][0],
            "No text recognized \u2014 try again")
        self.assertEqual(
            ui.DictationIndicator._STATES["no_speech"][0],
            "No speech detected \u2014 try again")

    def test_dialog_viewport_uses_content_size_until_screen_margin(self):
        self.assertEqual(ui._bounded_viewport(500, 1920, 96, 320), 500)
        self.assertEqual(ui._bounded_viewport(1900, 1920, 96, 320), 1824)
        self.assertEqual(ui._bounded_viewport(900, 768, 128, 240), 640)

    def test_dialog_viewport_stays_positive_on_unusual_small_desktop(self):
        self.assertEqual(ui._bounded_viewport(200, 100, 128, 240), 68)
        self.assertEqual(ui._bounded_viewport(0, 1920, 96, 320), 1)

    def test_indicator_layout_scales_from_effective_tk_dpi(self):
        self.assertEqual(ui._scaled_pixels(224, 96), 224)
        self.assertEqual(ui._scaled_pixels(224, 144), 336)
        self.assertEqual(ui._scaled_pixels(42, 192), 84)
        self.assertEqual(ui._scaled_pixels(42, 0), 42)

    def test_indicator_status_is_a_screen_reader_live_region(self):
        source = inspect.getsource(ui.DictationIndicator._run)
        self.assertIn("tk_uia.enable(root)", source)
        self.assertIn("_mark_live_region(label)", source)
        self.assertIn("label, self._STATES[command][0], announce=False", source)
        self.assertIn("changed = visible_state != command", source)
        self.assertIn('if changed and getattr(label, "_presspeech_live_region", False)', source)
        self.assertLess(source.index("position_visible_indicator()\n                if changed"),
                        source.index("_LIVE_REGIONS.announce(label.winfo_id())"))

    def test_windows_accessibility_text_scale_is_read_independently(self):
        registry = mock.MagicMock()
        registry.HKEY_CURRENT_USER = object()
        registry.REG_DWORD = 4
        registry.QueryValueEx.return_value = (225, registry.REG_DWORD)

        self.assertEqual(ui._windows_text_scale(registry), 2.25)
        registry.OpenKey.assert_called_once_with(
            registry.HKEY_CURRENT_USER,
            r"Software\Microsoft\Accessibility",
        )
        registry.QueryValueEx.assert_called_once_with(
            registry.OpenKey.return_value.__enter__.return_value,
            "TextScaleFactor",
        )

    def test_invalid_windows_text_scale_fails_back_without_extreme_ui(self):
        registry = mock.MagicMock()
        registry.REG_DWORD = 4
        for value in (99, 226, True, "225", None):
            with self.subTest(value=value):
                registry.QueryValueEx.return_value = (value, registry.REG_DWORD)
                self.assertEqual(ui._windows_text_scale(registry), 1.0)
        registry.QueryValueEx.side_effect = OSError("setting unavailable")
        self.assertEqual(ui._windows_text_scale(registry), 1.0)

    def test_tk_dialog_text_scale_preserves_the_display_dpi_baseline(self):
        interpreter = mock.Mock()
        interpreter.call.return_value = 1.25
        scaling = ui._TkTextScaling(types.SimpleNamespace(tk=interpreter))
        interpreter.call.reset_mock()

        self.assertTrue(scaling.update(1.5))
        interpreter.call.assert_called_once_with("tk", "scaling", 1.875)
        self.assertFalse(scaling.update(1.5))
        interpreter.call.assert_called_once()

        self.assertTrue(scaling.update(1.0))
        self.assertEqual(
            interpreter.call.call_args,
            mock.call("tk", "scaling", 1.25),
        )

    def test_tk_dialog_text_scale_rejects_values_outside_windows_range(self):
        interpreter = mock.Mock()
        interpreter.call.return_value = 1.0
        scaling = ui._TkTextScaling(types.SimpleNamespace(tk=interpreter))
        interpreter.call.reset_mock()

        self.assertTrue(scaling.update(2.25))
        self.assertTrue(scaling.update(2.26))
        self.assertEqual(interpreter.call.call_args_list, [
            mock.call("tk", "scaling", 2.25),
            mock.call("tk", "scaling", 1.0),
        ])

    def test_shared_tk_host_refreshes_text_scale_while_dialogs_are_open(self):
        interpreter = mock.Mock()
        interpreter.call.return_value = 1.25
        root = mock.Mock(tk=interpreter)

        with mock.patch.object(
                ui, "_windows_text_scale", side_effect=(1.5, 2.0)):
            scaling = ui._watch_windows_text_scale(root)
            refresh = root.after.call_args.args[1]
            refresh()

        self.assertEqual(scaling.current, 2.0)
        self.assertIn(mock.call(250, refresh), root.after.call_args_list)
        self.assertEqual(interpreter.call.call_args_list, [
            mock.call("tk", "scaling"),
            mock.call("tk", "scaling", 1.875),
            mock.call("tk", "scaling", 2.5),
        ])

    def test_indicator_custom_fonts_follow_accessibility_text_scale(self):
        self.assertEqual(ui._scaled_font_points(10, 1.0), 10)
        self.assertEqual(ui._scaled_font_points(10, 1.5), 15)
        self.assertEqual(ui._scaled_font_points(10, 2.25), 23)
        self.assertEqual(ui._scaled_font_points(11, 2.25), 25)
        self.assertEqual(ui._scaled_font_points(10, 9), 10)

    def test_compact_window_scales_but_stays_inside_the_desktop(self):
        self.assertEqual(
            ui._bounded_window_size(480, 280, 96, 1920, 1080),
            (480, 280),
        )
        self.assertEqual(
            ui._bounded_window_size(480, 280, 144, 1920, 1080),
            (720, 420),
        )
        self.assertEqual(
            ui._bounded_window_size(480, 280, 216, 1024, 768),
            (960, 630),
        )
        self.assertEqual(
            ui._bounded_window_size(480, 280, 216, 300, 200),
            (268, 168),
        )

    def test_scratchpad_editor_is_named_and_scrollable(self):
        source = inspect.getsource(ui.ScratchpadWindow._build)
        self.assertIn(
            '_name_control(self.text, "Private dictation scratchpad")',
            source,
        )
        self.assertIn("transcript_scrollbar = ttk.Scrollbar", source)
        self.assertIn("_bounded_window_size(", source)
        self.assertIn('font="TkDefaultFont"', source)
        self.assertIn('root.bind("<Configure>", resize_status', source)
        self.assertIn('self._protect_scratchpad_copy_and_cut()', source)

    def test_scratchpad_recovery_is_an_explicit_keyboard_command(self):
        source = inspect.getsource(ui.ScratchpadWindow._build)
        self.assertIn('text="Review Delivery…"', source)
        self.assertIn('command=self.app.open_delivery_recovery', source)
        self.assertIn('_add_access_key(root, self.review_button, "r")', source)
        self.assertIn('"Open Delivery Recovery for a waiting dictation', source)
        self.assertLess(
            source.index('text="Dictate (or use the hotkey)"'),
            source.index('text="Review Delivery…"'))

    def test_win32_system_colours_are_converted_from_bgr(self):
        self.assertEqual(ui._colourref_hex(0x00332211), "#112233")

    def test_indicator_uses_the_windows_high_contrast_pair(self):
        user32 = mock.Mock()

        def report_high_contrast(_action, _size, pointer, _flags):
            pointer._obj.dwFlags = 1
            return True

        user32.SystemParametersInfoW.side_effect = report_high_contrast
        user32.GetSysColor.side_effect = {
            13: 0x00654321,
            14: 0x00ccbbaa,
        }.__getitem__

        self.assertEqual(
            ui._indicator_system_palette(user32),
            ("#214365", "#aabbcc"),
        )

    def test_indicator_keeps_default_palette_outside_contrast_mode(self):
        user32 = mock.Mock()
        user32.SystemParametersInfoW.return_value = True

        self.assertIsNone(ui._indicator_system_palette(user32))
        user32.GetSysColor.assert_not_called()

    def test_temporary_indicator_does_not_hide_a_newer_state(self):
        indicator = ui.DictationIndicator()
        indicator._ensure_thread = mock.Mock()

        with mock.patch.object(ui.threading, "Timer") as timer:
            indicator.show_temporary("no_speech", 2.5)
            hide_if_current = timer.call_args.args[1]
            generation = timer.call_args.kwargs["args"][0]
            indicator.show("listening")
            hide_if_current(generation)

        self.assertEqual(indicator._commands.get_nowait(), "no_speech")
        self.assertEqual(indicator._commands.get_nowait(), "listening")
        with self.assertRaises(queue.Empty):
            indicator._commands.get_nowait()
        timer.return_value.start.assert_called_once_with()
        self.assertTrue(timer.return_value.daemon)

    def test_temporary_indicator_hides_when_it_is_still_current(self):
        indicator = ui.DictationIndicator()
        indicator._ensure_thread = mock.Mock()

        with mock.patch.object(ui.threading, "Timer") as timer:
            indicator.show_temporary("no_speech", 2.5)
            hide_if_current = timer.call_args.args[1]
            generation = timer.call_args.kwargs["args"][0]
            hide_if_current(generation)

        self.assertEqual(indicator._commands.get_nowait(), "no_speech")
        self.assertEqual(indicator._commands.get_nowait(), "hide")

    def test_every_interactive_window_uses_the_shared_host(self):
        host = mock.Mock()
        app = mock.Mock()

        with mock.patch.object(ui, "_window_host", return_value=host):
            windows = (
                ui.SetupWindow(app),
                ui.UpdateWindow(app, {"version": "1.2.3"}),
                ui.SettingsWindow(app),
                ui.DeliveryRecoveryWindow(app),
                ui.ScratchpadWindow(app),
            )

        self.assertEqual(host.submit.call_count, len(windows))
        self.assertEqual(
            [call.args[0].__self__ for call in host.submit.call_args_list],
            list(windows),
        )

    def test_present_window_restores_and_foregrounds_on_shared_host(self):
        host = mock.Mock()
        root = mock.Mock()
        window = types.SimpleNamespace(root=root)

        with mock.patch.object(ui, "_window_host", return_value=host):
            ui.present_window(window)

        present = host.submit.call_args.args[0]
        present()
        root.deiconify.assert_called_once_with()
        root.lift.assert_called_once_with()
        root.focus_force.assert_called_once_with()
        root.attributes.assert_called_once_with("-topmost", True)

        delay, clear_topmost = root.after.call_args.args
        self.assertEqual(delay, 250)
        clear_topmost()
        root.attributes.assert_called_with("-topmost", False)

    def test_structured_dialogs_are_scrollable_and_resizable(self):
        for window in (
                ui.SetupWindow, ui.UpdateWindow, ui.SettingsWindow,
                ui.DeliveryRecoveryWindow):
            body = inspect.getsource(window)
            self.assertIn("root.resizable(True, True)", body)
            self.assertIn("_ScrollableDialogBody(root", body)
            self.assertIn("self.scrollable_body.fit_to_screen()", body)

    def test_setup_completion_actions_follow_visual_tab_order(self):
        body = inspect.getsource(ui.SetupWindow._build)

        # Tk's default traversal follows widget creation order, while packing
        # two controls from the right lays out the first one at the far edge.
        # Later must therefore be created before Finish but packed after it.
        self.assertLess(
            body.index("later_button = ttk.Button"),
            body.index("self.finish_button = ttk.Button"),
        )
        self.assertLess(
            body.index('self.finish_button.pack(side="right")'),
            body.index('later_button.pack(side="right"'),
        )

    def test_setup_exposes_both_dictation_styles_before_try(self):
        body = inspect.getsource(ui.SetupWindow._build)

        self.assertIn('text="Hold to talk"', body)
        self.assertIn('text="Press to toggle"', body)
        self.assertLess(
            body.index('text="Hold to talk"'),
            body.index('text="Try Dictation"'),
        )
        self.assertLess(
            body.index('text="Press to toggle"'),
            body.index('text="Try Dictation"'),
        )

    def test_setup_explains_first_model_download_before_microphone_checks(self):
        body = inspect.getsource(ui.SetupWindow._build)

        self.assertIn("pinned speech-model files from ", body)
        self.assertIn('"Hugging Face. Setup asks', body)
        self.assertIn("Stay online", body)
        self.assertIn("English-only ", body)
        self.assertIn("Whisper base.en on CPU (~141 MiB)", body)
        self.assertIn("asks before downloading missing", body)
        self.assertIn("first-run model files", body)
        self.assertIn("Multilingual Parakeet and Whisper ", body)
        self.assertIn("supported NVIDIA GPU", body)
        self.assertLess(
            body.index("Presspeech fetches pinned speech-model files"),
            body.index('text="Microphone"'),
        )

    def test_setup_exposes_consent_and_cpu_fallback_for_large_first_download(self):
        body = inspect.getsource(ui.SetupWindow._build)

        for disclosure in (
                "full multilingual Parakeet model download is about 2.5 GB",
                "huggingface.co",
                "MODEL_DOWNLOAD_PRIVACY_NOTICE",
                "English-only ",
                "Whisper base.en on CPU (~141 MiB)"):
            self.assertIn(disclosure, body)
        for button in (
                'text="Download Parakeet model (up to ~2.5 GB)"',
                'text="Select and download English-only CPU model (~141 MiB)"',
                'text="Choose another model in Settings…"'):
            self.assertIn(button, body)
        self.assertIn(
            'command=self.app.select_cpu_model_after_download_declined', body)
        self.assertLess(
            body.index('text="Download Parakeet model (up to ~2.5 GB)"'),
            body.index('text="Microphone"'),
        )
        self.assertLess(
            body.index('text="Choose another model in Settings…"'),
            body.index('text="Dictation hotkey"'),
        )
        self.assertIn(
            "self.download_model_button, MODEL_DOWNLOAD_ACCESSIBLE_DESCRIPTION",
            body)
        self.assertIn(
            "self.cpu_model_button, MODEL_DOWNLOAD_ACCESSIBLE_DESCRIPTION",
            body)
        self.assertIn("self.model_consent_frame.grid_remove()", body)
        self.assertIn("root.after_idle(self._focus_initial_setup_control)", body)
        self.assertLess(
            body.index("self._poll_model()"),
            body.index("root.after_idle(self._focus_initial_setup_control)"),
        )

    def test_model_download_consent_discloses_request_privacy_for_both_paths(self):
        notice = (
            "Hugging Face receives a request for the selected model and revision. "
            "Presspeech disables Hugging Face model-library telemetry and sends "
            "no account token; audio and transcripts stay on this PC. "
            "Presspeech honors configured HTTP proxy and custom CA settings; "
            "a TLS-inspecting proxy trusted by that CA configuration can also "
            "see the model request.")
        self.assertEqual(ui.MODEL_DOWNLOAD_PRIVACY_NOTICE, notice)
        self.assertIn(
            "MODEL_DOWNLOAD_PRIVACY_NOTICE",
            inspect.getsource(ui.SetupWindow._build))
        self.assertIn(
            "MODEL_DOWNLOAD_PRIVACY_NOTICE",
            inspect.getsource(ui.SetupWindow._poll_model))
        self.assertIn(
            "MODEL_DOWNLOAD_PRIVACY_NOTICE",
            inspect.getsource(ui.SettingsWindow._build))

    def test_hidden_first_run_model_choices_start_disabled_before_first_poll(self):
        body = inspect.getsource(ui.SetupWindow._build)

        for button_name in (
                "download_model_button", "cpu_model_button",
                "other_model_button"):
            with self.subTest(button=button_name):
                construction = body.split(
                    "self.%s = ttk.Button(" % button_name, 1)[1].split(
                        "\n        )", 1)[0]
                self.assertIn('state="disabled"', construction)

        self.assertLess(body.index('state="disabled"'),
                        body.index("self._poll_model()"))

    def test_setup_names_all_windows_microphone_privacy_switches(self):
        body = inspect.getsource(ui.SetupWindow._build)

        for switch in (
                "enable Microphone ",
                "access, Let apps access your microphone",
                "Let desktop ",
                "apps access your microphone",
                "Windows 11 builds also ",
                "per-app microphone access for desktop apps",
                "allow Presspeech there too"):
            self.assertIn(switch, body)

    def test_setup_makes_microphone_probe_explicit_and_discloses_handling(self):
        body = inspect.getsource(ui.SetupWindow._build)

        for disclosure in (
                "The microphone check is optional",
                "finish Setup ",
                "without running it; select or connect a microphone in ",
                "Settings later. The local microphone check opens the ",
                "selected input only when you choose Check Microphone; ",
                "when you choose Check Microphone",
                "Check Microphone",
                "microphone-use ",
                "indicator. On some Windows 11 builds, the first check may ",
                "also show a Windows microphone-permission prompt",
                "approve ",
                "only if you want to run the check.",
                "Audio samples are ",
                "measure input ",
                "level in memory, then discarded",
                "discarded",
                "not saved, ",
                "sent, or transcribed"):
            self.assertIn(disclosure, body)
        self.assertIn('text="Not checked"', body)
        self.assertIn('text="Check Microphone"', body)
        self.assertNotIn("root.after(150, self._check_microphone)", body)
        self.assertNotIn("root.after(0, self._check_microphone)", body)
        self.assertIn("wraplength=560", body)

    def test_setup_escalates_managed_microphone_privacy_to_administrator(self):
        body = inspect.getsource(ui.SetupWindow._build)

        self.assertIn("managed by your organization", body)
        self.assertIn("contact your administrator", body)
        self.assertIn("cannot override that policy", body)

    def test_scrollable_dialog_routes_wheel_and_shift_wheel(self):
        body = ui._ScrollableDialogBody.__new__(ui._ScrollableDialogBody)
        body.canvas = mock.Mock()

        self.assertEqual(
            body._mouse_wheel(types.SimpleNamespace(delta=120, state=0)),
            "break")
        body.canvas.yview_scroll.assert_called_once_with(-1, "units")

        self.assertEqual(
            body._mouse_wheel(types.SimpleNamespace(delta=-240, state=1)),
            "break")
        body.canvas.xview_scroll.assert_called_once_with(2, "units")

    def test_scrollable_dialog_reveals_keyboard_focus(self):
        body = ui._ScrollableDialogBody.__new__(ui._ScrollableDialogBody)
        body.content = mock.Mock()
        body.content.winfo_width.return_value = 500
        body.content.winfo_height.return_value = 1000
        body.canvas = mock.Mock()
        body.canvas.canvasx.return_value = 0
        body.canvas.canvasy.return_value = 0
        body.canvas.winfo_width.return_value = 300
        body.canvas.winfo_height.return_value = 200
        widget = mock.Mock(master=body.content)
        widget.winfo_x.return_value = 10
        widget.winfo_y.return_value = 500
        widget.winfo_width.return_value = 100
        widget.winfo_height.return_value = 20

        body._show_widget(widget)

        body.canvas.xview_moveto.assert_not_called()
        body.canvas.yview_moveto.assert_called_once_with(0.32)

    def test_form_labels_and_names_reach_ui_automation(self):
        label = mock.Mock()
        control = mock.Mock()

        with mock.patch.object(ui.tk_uia, "label_for") as label_for, \
                mock.patch.object(ui.tk_uia, "set_acc_name") as set_name, \
                mock.patch.object(
                    ui.tk_uia, "set_acc_description") as set_description:
            ui._label_control(label, control)
            ui._name_control(control, "Dictionary rules")
            ui._describe_control(control, "Extra instructions")

        label_for.assert_called_once_with(label, control)
        set_name.assert_called_once_with(control, "Dictionary rules")
        set_description.assert_called_once_with(control, "Extra instructions")

    def test_trigger_choices_keep_context_in_ui_automation(self):
        label, hold, toggle = mock.Mock(), mock.Mock(), mock.Mock()

        with mock.patch.object(ui.tk_uia, "label_for") as label_for, \
                mock.patch.object(ui.tk_uia, "set_acc_name") as set_name:
            ui._label_trigger_choices(label, hold, toggle)

        label_for.assert_called_once_with(label, hold)
        self.assertEqual(set_name.call_args_list, [
            mock.call(hold, "Dictation style: Hold to talk"),
            mock.call(toggle, "Dictation style: Press to toggle"),
        ])

    def test_settings_trigger_names_are_registered_after_layout(self):
        body = inspect.getsource(ui.SettingsWindow._build)
        self.assertLess(
            body.index("root.update_idletasks()"),
            body.index("_label_trigger_choices(trigger_label"),
        )

    def test_settings_disclose_model_download_before_save(self):
        body = inspect.getsource(ui.SettingsWindow._build)

        self.assertIn(
            "Saving a different model starts preparation immediately.", body)
        self.assertIn("huggingface.co", body)
        self.assertIn("first Parakeet download can be about", body)
        self.assertIn("MODEL_DOWNLOAD_PRIVACY_NOTICE", body)
        self.assertIn("Dictation is unavailable until the model is ready.", body)
        self.assertIn("English-only CPU option (~141 MiB)", body)
        self.assertIn("wraplength=620", body)
        self.assertLess(
            body.index("Saving a different model"),
            body.index("self.save_button"),
        )

    def test_settings_exposes_private_scratchpad_without_tray_navigation(self):
        body = inspect.getsource(ui.SettingsWindow._build)

        self.assertIn(
            'text="Try Dictation…", command=self.app.open_scratchpad', body)
        self.assertIn('_add_access_key(root, self.try_button, "t")', body)
        self.assertLess(
            body.index("self.try_button.grid"),
            body.index("root.update_idletasks()"),
        )

    def test_changed_visible_text_refreshes_its_accessible_name(self):
        widget = mock.Mock()
        widget.cget.return_value = "Ready to download"

        with mock.patch.object(ui.tk_uia, "add_acc_object") as refresh:
            ui._set_accessible_text(widget, "Verified and ready to install")

        widget.config.assert_called_once_with(
            text="Verified and ready to install")
        refresh.assert_called_once_with(widget)

    def test_marked_status_change_raises_live_region_event_once(self):
        widget = mock.Mock()
        widget.cget.return_value = "Downloading…"
        widget.winfo_id.return_value = 8123
        widget._presspeech_live_region = True

        with mock.patch.object(ui.tk_uia, "add_acc_object"), \
                mock.patch.object(ui._LIVE_REGIONS, "announce") as announce:
            ui._set_accessible_text(widget, "Verified and ready to install")

        announce.assert_called_once_with(8123)

        widget.cget.return_value = "Verified and ready to install"
        with mock.patch.object(ui.tk_uia, "add_acc_object"), \
                mock.patch.object(ui._LIVE_REGIONS, "announce") as announce:
            ui._set_accessible_text(widget, "Verified and ready to install")

        announce.assert_not_called()

    def test_progress_text_can_refresh_without_interrupting_screen_reader(self):
        widget = mock.Mock()
        widget.cget.return_value = "Downloaded 1.0 GB"
        widget._presspeech_live_region = True

        with mock.patch.object(ui.tk_uia, "add_acc_object"), \
                mock.patch.object(ui._LIVE_REGIONS, "announce") as announce:
            ui._set_accessible_text(
                widget, "Downloaded 1.1 GB", announce=False)

        announce.assert_not_called()

    def test_live_region_is_marked_and_cleared_with_status_widget(self):
        widget = mock.Mock()
        widget.winfo_id.return_value = 8123

        with mock.patch.object(
                ui._LIVE_REGIONS, "mark", return_value=True) as mark, \
                mock.patch.object(ui._LIVE_REGIONS, "clear") as clear:
            ui._mark_live_region(widget)
            mark.assert_called_once_with(8123, ui.live_region.POLITE)
            self.assertTrue(widget._presspeech_live_region)
            sequence, handler = widget.bind.call_args.args
            self.assertEqual(sequence, "<Destroy>")
            self.assertEqual(widget.bind.call_args.kwargs, {"add": "+"})
            handler(types.SimpleNamespace(widget=widget))

        clear.assert_called_once_with(8123)

    def test_access_key_underlines_and_invokes_its_visible_command(self):
        root = mock.Mock()
        button = mock.Mock()
        button.cget.return_value = "Download Update"

        ui._add_access_key(root, button, "d")

        button.config.assert_called_once_with(underline=0)
        root.bind.assert_called_once()
        sequence, handler = root.bind.call_args.args
        self.assertEqual(sequence, "<Alt-KeyPress-d>")
        self.assertEqual(root.bind.call_args.kwargs, {"add": "+"})
        self.assertEqual(handler(types.SimpleNamespace()), "break")
        button.invoke.assert_called_once_with()

    def test_dynamic_command_keeps_its_bound_access_key_underlined(self):
        widget = mock.Mock()
        widget._presspeech_access_key = "d"

        with mock.patch.object(ui.tk_uia, "add_acc_object") as refresh:
            ui._set_accessible_text(widget, "Stop Dictation")

        widget.config.assert_called_once_with(
            text="Stop Dictation", underline=5)
        refresh.assert_called_once_with(widget)

    def test_access_key_must_be_visible_in_the_command_label(self):
        with self.assertRaisesRegex(ValueError, "not present"):
            ui._access_key_index("Later", "z")

    def test_disabling_a_focused_control_moves_focus_first(self):
        root = mock.Mock()
        control = mock.Mock()
        fallback = mock.Mock()
        root.focus_get.return_value = control
        order = []
        fallback.focus_set.side_effect = lambda: order.append("focus")
        control.config.side_effect = lambda **_values: order.append("disable")

        ui._set_control_state(root, control, "disabled", fallback)

        self.assertEqual(order, ["focus", "disable"])
        fallback.focus_set.assert_called_once_with()
        control.config.assert_called_once_with(state="disabled")

    def test_state_change_does_not_move_focus_from_another_control(self):
        root = mock.Mock()
        control = mock.Mock()
        fallback = mock.Mock()
        root.focus_get.return_value = object()

        ui._set_control_state(root, control, "disabled", fallback)

        fallback.focus_set.assert_not_called()
        control.config.assert_called_once_with(state="disabled")

    def test_every_interactive_window_supports_escape(self):
        commands = {
            ui.SetupWindow: "self._defer",
            ui.UpdateWindow: "self._close",
            ui.SettingsWindow: "self._close",
            ui.DeliveryRecoveryWindow: "self._close",
            ui.ScratchpadWindow: "self._close",
        }
        for window, command in commands.items():
            self.assertIn(
                f'_bind_window_command(root, "<Escape>", {command})',
                inspect.getsource(window),
            )

    def test_setup_hotkey_live_status_is_not_labelled_by_static_caption(self):
        source = inspect.getsource(ui.SetupWindow._build)

        self.assertNotIn(
            "_label_control(hotkey_status_label, self.hotkey_status)", source)
        self.assertIn("_mark_live_region(status)", source)
        self.assertIn("self.hotkey_status, self.autostart_status", source)

    def test_delivery_recovery_opens_on_a_non_destructive_command(self):
        source = inspect.getsource(ui.DeliveryRecoveryWindow)
        self.assertIn(
            "root.after_idle(self.leave_button.focus_set)", source)
        self.assertNotIn('default="active"', source)
        self.assertLess(
            source.index("self.copy_button ="),
            source.index("self.discard_button ="),
        )

    def test_diagnostics_do_not_start_the_window_host(self):
        with mock.patch.object(ui, "_WINDOW_HOST", None):
            self.assertEqual(ui.accessibility_status(), "not initialized")

    def test_accessibility_failures_are_visible_in_diagnostics(self):
        host = mock.Mock()
        host.accessibility = "provided"
        with mock.patch.object(ui, "_WINDOW_HOST", host), \
                mock.patch.object(
                    ui.tk_uia, "label_for", side_effect=OSError("UIA failed")):
            ui._label_control(mock.Mock(), mock.Mock())
            self.assertEqual(ui.accessibility_status(), "degraded")


class SetupWindowTests(unittest.TestCase):
    def make_window(self, status, detail=""):
        window = ui.SetupWindow.__new__(ui.SetupWindow)
        window.app = mock.Mock(
            model_status=status, model_status_detail=detail)
        window.app.lock = threading.Lock()
        window.app._microphone_check_in_progress = False
        window._setup_interacted = False
        window.app.settings = {"model": "parakeet-tdt-0.6b-v3"}
        window.root = mock.Mock()
        window.model_label = mock.Mock()
        window.model_consent_frame = mock.Mock()
        window.model_consent_label = mock.Mock()
        window.download_model_button = mock.Mock()
        window.cpu_model_button = mock.Mock()
        window.other_model_button = mock.Mock()
        window.progress = mock.Mock()
        window.retry_button = mock.Mock()
        window.try_button = mock.Mock()
        window.finish_button = mock.Mock()
        window.hotkey_status = mock.Mock()
        window.repair_hotkey_button = mock.Mock()
        window.app.hotkey_listener_status.return_value = (
            "ready", "Ready — Right Alt")
        window.autostart_status = mock.Mock()
        window.microphone_events = queue.Queue()
        window.microphone_checking = False
        window.check_microphone_button = mock.Mock()
        window.microphone_status = mock.Mock()
        window.hotkey = mock.Mock()
        window.later_button = mock.Mock()
        window.device_values = {"Automatic (recommended)": "auto"}
        window.device = mock.Mock()
        window.device.get.return_value = "Automatic (recommended)"
        window.device.set.side_effect = (
            lambda value: setattr(window.device.get, "return_value", value))
        window.app.input_device_options.return_value = [
            ("Automatic (recommended)", "auto")]
        return window

    def test_error_remains_observed_and_enables_retry(self):
        window = self.make_window("error", "download failed")

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._poll_model()

        self.assertEqual(set_text.call_args_list, [
            mock.call(window.model_label, "Needs attention — download failed"),
            mock.call(window.hotkey_status, "Ready — Right Alt"),
        ])
        window.repair_hotkey_button.config.assert_called_once_with(
            state="normal")
        window.retry_button.config.assert_called_once_with(state="normal")
        window.try_button.config.assert_called_once_with(state="disabled")
        window.finish_button.config.assert_called_once_with(state="disabled")
        window.progress.config.assert_called_once_with(
            mode="determinate", value=0)
        window.root.after.assert_called_once_with(300, window._poll_model)

    def test_model_load_error_points_to_the_retry_action_in_setup(self):
        window = self.make_window(
            "error", "Model load failed; use Retry Speech Model")

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._poll_model()

        self.assertIn(
            mock.call(
                window.model_label,
                "Needs attention — Model load failed; use Retry Speech Model"),
            set_text.call_args_list,
        )
        window.retry_button.config.assert_called_once_with(state="normal")

    def test_first_run_model_consent_shows_choices_without_progress_claim(self):
        window = self.make_window("awaiting_download_consent")

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._poll_model()

        self.assertIn(
            mock.call(
                window.model_label,
                "Needs your choice — Full Parakeet model download is about 2.5 GB"),
            set_text.call_args_list,
        )
        set_text.assert_any_call(
            window.model_consent_label,
            "A full multilingual Parakeet model download is about 2.5 GB "
            "from huggingface.co; a partial local cache may need less. "
            + ui.MODEL_DOWNLOAD_PRIVACY_NOTICE + " Or choose English-only "
            "Whisper base.en on CPU (~141 MiB).", announce=False)
        set_text.assert_any_call(
            window.download_model_button,
            "Download Parakeet model (up to ~2.5 GB)", announce=False)
        window.model_consent_frame.grid.assert_called_once_with()
        window.download_model_button.config.assert_any_call(state="normal")
        window.cpu_model_button.config.assert_called_once_with(state="normal")
        window.cpu_model_button.pack.assert_called_once_with(
            before=window.other_model_button, anchor="w", pady=(4, 0))
        window.other_model_button.config.assert_called_once_with(state="normal")
        window.progress.config.assert_called_once_with(
            mode="determinate", value=0)
        self.assertFalse(window.app.confirm_initial_model_download.called)
        self.assertFalse(window.app.select_cpu_model_after_download_declined.called)

    def test_first_run_download_action_names_model_and_privacy_boundary(self):
        cases = (
            (
                "parakeet-tdt-0.6b-v3",
                "Download the multilingual Parakeet model, up to about "
                "2.5 GB.",
            ),
            (
                "base.en",
                "Download the English-only CPU Whisper base.en model, "
                "about 141 MiB. This requests pinned model files from "
                "huggingface.co.",
            ),
        )
        for model, accessible_name in cases:
            with self.subTest(model=model):
                window = self.make_window("awaiting_download_consent")
                window.app.settings["model"] = model

                with mock.patch.object(ui, "_set_accessible_text"), \
                        mock.patch.object(ui, "_name_control") as name_control:
                    window._poll_model()

                name_control.assert_any_call(
                    window.download_model_button, accessible_name)

    def test_model_download_accessible_description_matches_policy_boundary(self):
        description = ui.MODEL_DOWNLOAD_ACCESSIBLE_DESCRIPTION
        for detail in (
                "Hugging Face", "Model-library telemetry", "account tokens",
                "dictation audio", "transcripts", "HTTP proxy", "custom CA",
                "TLS-inspecting proxy can read the request"):
            with self.subTest(detail=detail):
                self.assertIn(detail, description)

    def test_first_run_cpu_download_waits_for_choice_and_hides_redundant_fallback(self):
        window = self.make_window(
            "awaiting_download_consent",
            "English-only Whisper base.en model download is about 141 MiB")
        window.app.settings["model"] = "base.en"

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._poll_model()

        self.assertIn(
            mock.call(
                window.model_label,
                "Needs your choice — English-only Whisper base.en model "
                "download is about 141 MiB"),
            set_text.call_args_list,
        )
        set_text.assert_any_call(
            window.model_consent_label,
            "The English-only Whisper base.en speech model is about 141 MiB. "
            "Choose Download to fetch its pinned files from huggingface.co. "
            + ui.MODEL_DOWNLOAD_PRIVACY_NOTICE, announce=False)
        set_text.assert_any_call(
            window.download_model_button,
            "Download English-only CPU model (~141 MiB)", announce=False)
        window.download_model_button.config.assert_any_call(state="normal")
        window.cpu_model_button.pack_forget.assert_called_once_with()
        window.cpu_model_button.config.assert_any_call(state="disabled")
        self.assertFalse(window.app.confirm_initial_model_download.called)

    def test_ready_enables_try_dictation_and_keeps_observing(self):
        window = self.make_window("ready", "base.en on cpu")

        with mock.patch.object(ui, "_set_accessible_text"):
            window._poll_model()

        window.retry_button.config.assert_called_once_with(state="disabled")
        window.try_button.config.assert_called_once_with(state="normal")
        window.finish_button.config.assert_called_once_with(state="normal")
        window.progress.config.assert_called_once_with(
            mode="determinate", value=100)
        window.root.after.assert_called_once_with(300, window._poll_model)

    def test_retry_resumes_progress_animation_after_an_error(self):
        window = self.make_window("loading", "Downloading model")
        window._progress_active = False
        window.root.focus_get.return_value = window.retry_button

        with mock.patch.object(ui, "_set_accessible_text"):
            window._poll_model()

        window.later_button.focus_set.assert_called_once_with()
        window.device.focus_set.assert_not_called()
        window.progress.config.assert_called_once_with(mode="indeterminate")
        window.progress.start.assert_called_once_with(12)
        self.assertTrue(window._progress_active)

    def test_model_consent_disabling_preserves_forward_setup_focus(self):
        for action in (
                "download_model_button", "cpu_model_button",
                "other_model_button"):
            with self.subTest(action=action):
                window = self.make_window("loading", "Downloading model")
                focused_control = getattr(window, action)
                window.root.focus_get.return_value = focused_control

                with mock.patch.object(ui, "_set_accessible_text"):
                    window._poll_model()

                window.device.focus_set.assert_called_once_with()
                focused_control.config.assert_called_once_with(
                    state="disabled")

    def test_initial_setup_focus_starts_at_required_model_choice_when_needed(self):
        for model, expected_label in (
                ("parakeet-tdt-0.6b-v3", "Download Parakeet"),
                ("base.en", "Download English-only CPU")):
            with self.subTest(model=model):
                window = self.make_window("awaiting_download_consent")
                window.app.settings["model"] = model

                with mock.patch.object(ui, "_set_accessible_text") as set_text:
                    window._poll_model()
                window._focus_initial_setup_control()

                window.download_model_button.focus_set.assert_called_once_with()
                window.device.focus_set.assert_not_called()
                set_text.assert_any_call(
                    window.download_model_button,
                    expected_label + (
                        " model (~141 MiB)" if model == "base.en" else
                        " model (up to ~2.5 GB)"), announce=False)

    def test_initial_setup_focus_uses_microphone_when_model_choice_not_needed(self):
        for status in ("pending", "loading", "ready", "error"):
            with self.subTest(status=status):
                window = self.make_window(status)
                window._focus_initial_setup_control()

                window.device.focus_set.assert_called_once_with()
                window.download_model_button.focus_set.assert_not_called()

    def test_setup_focus_navigation_counts_as_interaction_except_initial_focus(self):
        window = ui.SetupWindow.__new__(ui.SetupWindow)
        window._setup_interacted = False
        window._initial_focus_pending = False
        window.root = mock.Mock()
        initial = object()
        window._initial_focus_widget = initial

        window._mark_setup_focus_interacted(
            types.SimpleNamespace(widget=initial))
        self.assertFalse(window._setup_interacted)

        window._mark_setup_focus_interacted(
            types.SimpleNamespace(widget=window.root))
        self.assertFalse(window._setup_interacted)

        window._mark_setup_focus_interacted(
            types.SimpleNamespace(widget=object()))
        self.assertFalse(window._setup_interacted)

        focused = object()
        window.root.focus_get.return_value = focused
        window._mark_setup_focus_interacted(
            types.SimpleNamespace(widget=focused))
        self.assertTrue(window._setup_interacted)

    def test_initial_focus_does_not_override_early_setup_interaction(self):
        window = self.make_window("awaiting_download_consent")
        window._setup_interacted = True
        window._initial_focus_pending = True

        window._focus_initial_setup_control()

        self.assertFalse(window._initial_focus_pending)
        window.download_model_button.focus_set.assert_not_called()
        window.device.focus_set.assert_not_called()

    def test_model_choice_arriving_later_moves_untouched_initial_focus(self):
        window = self.make_window("loading")
        window._initial_focus_pending = False
        window._setup_interacted = False
        window.root.focus_get.return_value = window.device

        with mock.patch.object(ui, "_set_accessible_text"):
            window._poll_model()
            window.app.model_status = "awaiting_download_consent"
            window._poll_model()

        window.download_model_button.focus_set.assert_called_once_with()

    def test_model_choice_arriving_later_does_not_steal_active_focus(self):
        window = self.make_window("loading")
        window._initial_focus_pending = False
        window._setup_interacted = True
        window.root.focus_get.return_value = window.device

        with mock.patch.object(ui, "_set_accessible_text"):
            window._poll_model()
            window.app.model_status = "awaiting_download_consent"
            window._poll_model()

        window.download_model_button.focus_set.assert_not_called()

    def test_finish_gate_closing_keeps_focus_on_adjacent_defer_action(self):
        window = self.make_window("ready", "base.en on cpu")
        window.app.hotkey_listener_status.return_value = (
            "error", "Global hotkey stopped. Choose Repair Global Hotkey.")
        window.root.focus_get.return_value = window.finish_button

        with mock.patch.object(ui, "_set_accessible_text"):
            window._poll_model()

        window.later_button.focus_set.assert_called_once_with()
        window.device.focus_set.assert_not_called()
        window.finish_button.config.assert_called_once_with(state="disabled")

    def test_loading_status_does_not_mislabel_download_as_load_only(self):
        window = self.make_window("loading", "Loading parakeet")

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._poll_model()

        self.assertEqual(
            set_text.call_args_list[0],
            mock.call(
                window.model_label,
                "Preparing speech model — downloading or loading…"),
        )

    def test_model_integrity_verification_has_an_explicit_loading_phase(self):
        window = self.make_window("loading", "Verifying model files…")

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._poll_model()

        self.assertEqual(
            set_text.call_args_list[0],
            mock.call(window.model_label, "Verifying model files…"),
        )
        self.assertIn(
            mock.call(mode="indeterminate"),
            window.progress.config.call_args_list)

    def test_setup_labels_bytes_as_current_file_progress(self):
        window = self.make_window("loading", "Downloading model files…")
        window.app.model_download_progress = (2 * 1024 * 1024, 8 * 1024 * 1024)

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._poll_model()

        first = mock.call(
            window.model_label,
            "Downloading model files… — 2.0 MiB of 8.0 MiB transferred "
            "in current file")
        self.assertEqual(set_text.call_args_list[0], first)
        self.assertIn(
            mock.call(mode="indeterminate"),
            window.progress.config.call_args_list)

        window.app.model_download_progress = (3 * 1024 * 1024, 8 * 1024 * 1024)
        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._poll_model()

        self.assertEqual(
            set_text.call_args_list[0],
            mock.call(
                window.model_label,
                "Downloading model files… — 3.0 MiB of 8.0 MiB transferred "
                "in current file",
                announce=False),
        )

        window.app.model_status_detail = "Loading speech model…"
        window.app.model_download_progress = None
        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._poll_model()

        self.assertEqual(
            set_text.call_args_list[0],
            mock.call(window.model_label, "Loading speech model…"),
        )

    def test_download_progress_formatter_describes_one_file_transfer(self):
        self.assertEqual(
            ui._format_download_progress((512, 1024)),
            " — 512 bytes of 1.0 KiB transferred in current file")
        self.assertEqual(
            ui._format_download_progress((2 * 1024 * 1024, 8 * 1024 * 1024)),
            " — 2.0 MiB of 8.0 MiB transferred in current file")
        self.assertEqual(
            ui._format_download_progress((2 * 1024 * 1024, None)),
            " — 2.0 MiB transferred in current file")

    def test_download_progress_formatter_rejects_invalid_counts(self):
        self.assertEqual(ui._format_download_progress(None), "")
        self.assertEqual(ui._format_download_progress((1,)), "")
        self.assertEqual(ui._format_download_progress((0, 100)), "")
        self.assertEqual(ui._format_download_progress((True, 100)), "")
        self.assertEqual(
            ui._format_download_progress((float("nan"), 100)), "")
        self.assertEqual(
            ui._format_download_progress((1024, float("nan"))),
            " — 1.0 KiB transferred in current file")

    def test_stopped_global_hotkey_exposes_repair_and_blocks_finish(self):
        window = self.make_window("ready", "base.en on cpu")
        window.app.hotkey_listener_status.return_value = (
            "error", "Global hotkey stopped. Choose Repair Global Hotkey.")

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._poll_model()

        self.assertIn(
            mock.call(
                window.hotkey_status,
                "Global hotkey stopped. Choose Repair Global Hotkey."),
            set_text.call_args_list,
        )
        window.repair_hotkey_button.config.assert_called_once_with(state="normal")
        window.try_button.config.assert_called_once_with(state="normal")
        window.finish_button.config.assert_called_once_with(state="disabled")

    def test_retry_action_uses_app_single_flight_gate(self):
        window = self.make_window("error")

        window._retry_model()

        window.app.retry_model.assert_called_once_with()

    def test_setup_cannot_finish_before_model_is_ready(self):
        window = self.make_window("error", "download failed")
        window.app.settings = {"setup_complete": False}

        with mock.patch.object(ui.cfg, "save") as save:
            window._finish()

        self.assertFalse(window.app.settings["setup_complete"])
        window.finish_button.config.assert_called_once_with(state="disabled")
        save.assert_not_called()
        window.app.apply_autostart.assert_not_called()

    def test_setup_cannot_finish_with_a_stopped_global_hotkey(self):
        window = self.make_window("ready", "base.en on cpu")
        window.app.settings = {"setup_complete": False}
        window.app.hotkey_listener_status.return_value = (
            "error", "Global hotkey stopped. Choose Repair Global Hotkey.")

        with mock.patch.object(ui.cfg, "save") as save:
            window._finish()

        self.assertFalse(window.app.settings["setup_complete"])
        window.finish_button.config.assert_called_once_with(state="disabled")
        save.assert_not_called()
        window.app.apply_autostart.assert_not_called()

    def test_ready_model_can_finish_without_a_connected_microphone(self):
        window = self.make_window("ready")
        window.app.settings = {
            "input_device": "auto",
            "autostart": True,
            "setup_complete": False,
        }
        window.autostart = mock.Mock()
        window.autostart.get.return_value = False
        window.app.apply_autostart.return_value = True
        window._close = mock.Mock()
        events = []

        def save(settings):
            events.append(("save", settings["setup_complete"]))

        def apply_autostart():
            events.append(("apply", window.app.settings["setup_complete"]))
            return True

        window.app.apply_autostart.side_effect = apply_autostart

        with mock.patch.object(ui.cfg, "save", side_effect=save):
            window._finish()

        self.assertTrue(window.app.settings["setup_complete"])
        self.assertFalse(window.app.settings["autostart"])
        self.assertEqual(events, [
            ("save", False), ("apply", False), ("save", True),
        ])
        window.app.apply_autostart.assert_called_once_with()
        window._close.assert_called_once_with()

    def test_setup_stays_open_and_exposes_autostart_failure(self):
        window = self.make_window("ready")
        window.app.settings = {
            "input_device": "auto",
            "autostart": True,
            "setup_complete": False,
        }
        window.autostart = mock.Mock()
        window.autostart.get.return_value = True
        window.app.apply_autostart.return_value = False
        window._close = mock.Mock()
        saved = []

        with mock.patch.object(
                ui.cfg, "save",
                side_effect=lambda settings: saved.append(dict(settings))) as save, \
                mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._finish()

        self.assertFalse(window.app.settings["setup_complete"])
        self.assertEqual(saved[0]["setup_complete"], False)
        self.assertEqual(saved[0]["autostart"], True)
        save.assert_called_once_with(window.app.settings)
        set_text.assert_called_once_with(
            window.autostart_status,
            "Start with Windows was not updated. Setup remains open; "
            "review Startup Settings, then retry Finish Setup.",
        )
        window._close.assert_not_called()

    def test_setup_can_finish_after_startup_registration_retry(self):
        window = self.make_window("ready")
        window.app.settings = {
            "input_device": "auto",
            "autostart": True,
            "setup_complete": False,
        }
        window.autostart = mock.Mock()
        window.autostart.get.return_value = True
        window.app.apply_autostart.side_effect = [False, True]
        window._close = mock.Mock()
        saved = []

        with mock.patch.object(
                ui.cfg, "save",
                side_effect=lambda settings: saved.append(dict(settings))), \
                mock.patch.object(ui, "_set_accessible_text"):
            window._finish()
            self.assertFalse(window.app.settings["setup_complete"])
            window._finish()

        self.assertEqual(
            [settings["setup_complete"] for settings in saved],
            [False, False, True],
        )
        self.assertTrue(window.app.settings["setup_complete"])
        window._close.assert_called_once_with()

    def test_failed_completion_save_keeps_setup_incomplete(self):
        window = self.make_window("ready")
        window.app.settings = {
            "input_device": "auto",
            "autostart": False,
            "setup_complete": False,
        }
        window.autostart = mock.Mock()
        window.autostart.get.return_value = False
        window.app.apply_autostart.return_value = True
        window._close = mock.Mock()

        with mock.patch.object(ui.cfg, "save", side_effect=[None, OSError]):
            with self.assertRaises(OSError):
                window._finish()

        self.assertFalse(window.app.settings["setup_complete"])
        window._close.assert_not_called()

    def test_setup_hotkey_change_applies_and_persists_before_finish(self):
        window = self.make_window("pending")
        window.app.settings = {
            "hotkey": "right alt",
            "trigger": "hold",
            "max_recording_seconds": 120,
            "setup_complete": False,
        }
        window.hotkey = mock.Mock()
        window.hotkey.get.return_value = "f8"
        window.instructions = mock.Mock()

        with mock.patch.object(ui.cfg, "save") as save, \
                mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._hotkey_changed()

        self.assertEqual(window.app.settings["hotkey"], "f8")
        save.assert_called_once_with(window.app.settings)
        set_text.assert_called_once_with(
            window.instructions,
            "Hold F8, wait for the start cue (if enabled) to finish and the "
            "Listening… status (if shown), speak, then release to type at "
            "the cursor.\n"
            "First setup may take time while the speech model downloads and "
            "loads. Speech stays on this PC; no audio or transcripts are uploaded.\n"
            "Windows 11 also includes Voice Access for on-device, offline voice "
            "control and dictation. Presspeech focuses on hotkey-driven "
            "dictation that inserts text at your cursor.",
        )

    def test_setup_rejects_unknown_hotkey_without_saving(self):
        window = self.make_window("pending")
        window.app.settings = {"hotkey": "right alt", "trigger": "hold"}
        window.hotkey = mock.Mock()
        window.hotkey.get.return_value = "letter a"

        with mock.patch.object(ui.cfg, "save") as save:
            window._hotkey_changed()

        window.hotkey.set.assert_called_once_with("right alt")
        self.assertEqual(window.app.settings["hotkey"], "right alt")
        save.assert_not_called()

    def test_setup_trigger_change_applies_and_persists_before_try(self):
        window = self.make_window("pending")
        window.app.settings = {"hotkey": "f8", "trigger": "hold"}
        window.trigger = mock.Mock()
        window.trigger.get.return_value = "toggle"
        window.instructions = mock.Mock()

        with mock.patch.object(ui.cfg, "save") as save, \
                mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._trigger_changed()

        self.assertEqual(window.app.settings["trigger"], "toggle")
        save.assert_called_once_with(window.app.settings)
        set_text.assert_called_once_with(
            window.instructions,
            "Press F8 to start, wait for the start cue (if enabled) to finish "
            "and the Listening… status (if shown), speak, then press it again "
            "to type at the cursor.\n"
            "First setup may take time while the speech model downloads and "
            "loads. Speech stays on this PC; no audio or transcripts are uploaded.\n"
            "Windows 11 also includes Voice Access for on-device, offline voice "
            "control and dictation. Presspeech focuses on hotkey-driven "
            "dictation that inserts text at your cursor.",
        )

    def test_setup_rejects_unknown_trigger_without_saving(self):
        window = self.make_window("pending")
        window.app.settings = {"hotkey": "right alt", "trigger": "hold"}
        window.trigger = mock.Mock()
        window.trigger.get.return_value = "voice activation"

        with mock.patch.object(ui.cfg, "save") as save:
            window._trigger_changed()

        window.trigger.set.assert_called_once_with("hold")
        self.assertEqual(window.app.settings["trigger"], "hold")
        save.assert_not_called()

    def test_setup_microphone_change_applies_before_try_and_persists(self):
        window = self.make_window("ready")
        window.app.settings = {"input_device": "auto"}
        window.app.input_device = (4, 48000)
        window.app._cached_input_selector = "auto"
        window.app._cached_input_topology = ("old device list",)
        window.device_values["Desk microphone"] = "MME::Desk microphone"
        window.device.get.return_value = "Desk microphone"

        with mock.patch.object(ui.cfg, "save") as save, \
                mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._microphone_changed()

        self.assertEqual(
            window.app.settings["input_device"], "MME::Desk microphone")
        self.assertIsNone(window.app.input_device)
        self.assertIsNone(window.app._cached_input_selector)
        self.assertIsNone(window.app._cached_input_topology)
        save.assert_called_once_with(window.app.settings)
        set_text.assert_called_once_with(
            window.microphone_status, "Not checked")
        window.root.after.assert_not_called()

    def test_setup_does_not_switch_microphones_during_recording(self):
        window = self.make_window("ready")
        window.app.recording = True
        window.app.settings = {"input_device": "auto"}
        window.app.input_device = (4, 48000)
        window.app._cached_input_selector = "auto"
        window.device_values["Desk microphone"] = "MME::Desk microphone"
        window.device.get.return_value = "Desk microphone"

        with mock.patch.object(ui.cfg, "save") as save, \
                mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._microphone_changed()

        self.assertEqual(window.app.settings["input_device"], "auto")
        self.assertEqual(window.app.input_device, (4, 48000))
        self.assertEqual(window.app._cached_input_selector, "auto")
        window.device.set.assert_called_once_with("Automatic (recommended)")
        save.assert_not_called()
        set_text.assert_called_once_with(
            window.microphone_status,
            "Finish or cancel the current dictation before changing microphones.")

    def test_setup_close_cannot_commit_a_racing_microphone_change(self):
        window = self.make_window("loading")
        window.app.recording = True
        window.app.settings = {"input_device": "auto", "autostart": False}
        window.device_values["Desk microphone"] = "MME::Desk microphone"
        window.device.get.return_value = "Desk microphone"
        window.autostart = mock.Mock()
        window.autostart.get.return_value = True
        window._close = mock.Mock()

        with mock.patch.object(ui.cfg, "save") as save, \
                mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._defer()

        self.assertEqual(window.app.settings["input_device"], "auto")
        self.assertFalse(window.app.settings["autostart"])
        save.assert_not_called()
        window._close.assert_not_called()
        set_text.assert_called_once_with(
            window.microphone_status,
            "Finish or cancel the current dictation before changing microphones.")

    def test_deferred_setup_keeps_choices_and_applies_autostart(self):
        window = self.make_window("loading")
        window.app.settings = {
            "input_device": "auto",
            "autostart": True,
            "setup_complete": False,
        }
        window.app.input_device = (2, 16000)
        window.app._cached_input_selector = "auto"
        window.app._cached_input_topology = ("old device list",)
        window.device_values["Headset"] = "MME::Headset"
        window.device.get.return_value = "Headset"
        window.autostart = mock.Mock()
        window.autostart.get.return_value = False
        window._close = mock.Mock()

        with mock.patch.object(ui.cfg, "save") as save:
            window._defer()

        self.assertEqual(window.app.settings["input_device"], "MME::Headset")
        self.assertFalse(window.app.settings["autostart"])
        self.assertFalse(window.app.settings["setup_complete"])
        self.assertIsNone(window.app.input_device)
        self.assertIsNone(window.app._cached_input_selector)
        self.assertIsNone(window.app._cached_input_topology)
        save.assert_called_once_with(window.app.settings)
        window.app.apply_autostart.assert_called_once_with()
        window._close.assert_called_once_with()

    def test_deferred_setup_warns_then_closes_after_autostart_failure(self):
        window = self.make_window("loading")
        window.app.settings = {
            "input_device": "auto",
            "autostart": False,
            "setup_complete": False,
        }
        window.autostart = mock.Mock()
        window.autostart.get.return_value = True
        window.app.apply_autostart.return_value = False
        window._close = mock.Mock()

        with mock.patch.object(ui.cfg, "save") as save, \
                mock.patch.object(ui.messagebox, "showwarning") as warning:
            window._defer()

        self.assertFalse(window.app.settings["setup_complete"])
        self.assertTrue(window.app.settings["autostart"])
        save.assert_called_once_with(window.app.settings)
        window.app.apply_autostart.assert_called_once_with()
        warning.assert_called_once_with(
            "Start with Windows not updated",
            "Your choices were saved, but Start with Windows was not "
            "updated. Setup remains incomplete and will open again "
            "on the next launch. Review Startup Settings before "
            "trying again.",
            parent=window.root,
        )
        window._close.assert_called_once_with()

    def test_deferred_setup_closes_even_if_warning_cannot_be_shown(self):
        window = self.make_window("loading")
        window.app.settings = {
            "input_device": "auto",
            "autostart": False,
            "setup_complete": False,
        }
        window.autostart = mock.Mock()
        window.autostart.get.return_value = False
        window.app.apply_autostart.return_value = False
        window._close = mock.Mock()

        with mock.patch.object(ui.cfg, "save"), \
                mock.patch.object(
                    ui.messagebox, "showwarning", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                window._defer()

        self.assertFalse(window.app.settings["setup_complete"])
        window._close.assert_called_once_with()

    def test_setup_instructions_respect_existing_toggle_mode(self):
        window = self.make_window("ready")
        window.app.settings = {"hotkey": "f9", "trigger": "toggle"}

        self.assertEqual(
            window._dictation_instructions(),
            "Press F9 to start, wait for the start cue (if enabled) to finish "
            "and the Listening… status (if shown), speak, then press it again to "
            "type at the cursor.\n"
            "First setup may take time while the speech model downloads and "
            "loads. Speech stays on this PC; no audio or transcripts are uploaded.\n"
            "Windows 11 also includes Voice Access for on-device, offline voice "
            "control and dictation. Presspeech focuses on hotkey-driven "
            "dictation that inserts text at your cursor.",
        )

    def test_setup_hold_instructions_wait_for_readiness(self):
        window = self.make_window("ready")
        window.app.settings = {"hotkey": "f8", "trigger": "hold"}

        self.assertTrue(window._dictation_instructions().startswith(
            "Hold F8, wait for the start cue (if enabled) to finish and the "
            "Listening… status (if shown), speak, then release"))

    def test_microphone_check_runs_off_the_ui_thread(self):
        window = self.make_window("ready")
        window.root.focus_get.return_value = window.check_microphone_button

        with mock.patch.object(ui, "_set_accessible_text") as set_text, \
                mock.patch.object(ui.threading, "Thread") as thread:
            window._check_microphone()

        self.assertTrue(window.microphone_checking)
        window.device.focus_set.assert_called_once_with()
        window.check_microphone_button.config.assert_called_once_with(
            state="disabled")
        set_text.assert_called_once_with(
            window.microphone_status,
            "Connecting microphone… Wait for Listening before speaking.")
        thread.assert_called_once_with(
            target=window._check_microphone_worker,
            args=("auto",),
            name="presspeech-microphone-check",
            daemon=True,
        )
        thread.return_value.start.assert_called_once_with()

    def test_setup_does_not_start_microphone_check_during_recording(self):
        window = self.make_window("ready")
        window.app.recording = True

        with mock.patch.object(ui, "_set_accessible_text") as set_text, \
                mock.patch.object(ui.threading, "Thread") as thread:
            window._check_microphone()

        self.assertFalse(window.microphone_checking)
        thread.assert_not_called()
        set_text.assert_called_once_with(
            window.microphone_status,
            "Finish or cancel the current dictation before checking the microphone.")

    def test_racing_hotkey_postpones_check_without_enumerating_devices(self):
        window = self.make_window("ready")
        window.microphone_checking = True
        window.app.recording = True
        window.app.check_input_device.return_value = "busy"

        window._check_microphone_worker("auto")
        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._poll_microphone_events()

        window.app.input_device_options.assert_not_called()
        window.check_microphone_button.config.assert_called_once_with(
            state="disabled")
        set_text.assert_called_once_with(
            window.microphone_status,
            "Microphone check postponed — finish or cancel dictation, "
            "then choose Check Microphone")

    def test_busy_setup_controls_move_focus_and_block_microphone_actions(self):
        window = self.make_window("ready")
        window.app.recording = True
        window.root.focus_get.return_value = window.device

        with mock.patch.object(ui, "_set_accessible_text"):
            window._poll_model()

        window.later_button.focus_set.assert_called_once_with()
        window.device.config.assert_called_once_with(state="disabled")
        window.check_microphone_button.config.assert_called_once_with(
            state="disabled")

    def test_busy_microphone_guidance_clears_when_dictation_finishes(self):
        window = self.make_window("ready")
        window.app.recording = True
        window._microphone_busy_feedback = True

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._poll_model()
            window.app.recording = False
            window._poll_model()

        self.assertFalse(window._microphone_busy_feedback)
        self.assertIn(
            mock.call(
                window.microphone_status,
                "You can now choose Check Microphone to test the selected input."),
            set_text.call_args_list)
        self.assertEqual(window.device.config.call_args, mock.call(state="readonly"))
        self.assertEqual(
            window.check_microphone_button.config.call_args,
            mock.call(state="normal"))

    def test_ready_microphone_result_is_exposed_accessibly(self):
        window = self.make_window("ready")
        window.microphone_checking = True
        window.app.check_input_device.return_value = "level"
        window._check_microphone_worker("auto")

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._poll_microphone_events()

        window.app.check_input_device.assert_called_once()
        self.assertEqual(window.app.check_input_device.call_args.args, ("auto",))
        self.assertTrue(callable(
            window.app.check_input_device.call_args.kwargs["on_listening"]))
        window.app.input_device_options.assert_called_once_with()
        self.assertFalse(window.microphone_checking)
        window.check_microphone_button.config.assert_called_once_with(
            state="normal")
        set_text.assert_called_once_with(
            window.microphone_status,
            "Ready — input level detected",
        )

    def test_unexpected_microphone_check_error_completes_without_details(self):
        window = self.make_window("ready")
        window.microphone_checking = True
        window.app.check_input_device.side_effect = OSError(
            "private device or driver detail")

        window._check_microphone_worker("auto")

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._poll_microphone_events()

        window.app.check_input_device.assert_called_once()
        self.assertEqual(window.app.check_input_device.call_args.args, ("auto",))
        self.assertTrue(window.microphone_events.empty())
        self.assertFalse(window.microphone_checking)
        window.check_microphone_button.config.assert_called_once_with(
            state="normal")
        set_text.assert_called_once_with(
            window.microphone_status,
            "Microphone check failed — choose Check Microphone to retry",
        )
        self.assertNotIn(
            "private device or driver detail",
            str(set_text.call_args_list),
        )

    def test_silent_microphone_result_does_not_claim_readiness(self):
        window = self.make_window("ready")
        window.microphone_checking = True
        window.microphone_events.put((
            "auto", "silent", [("Automatic (recommended)", "auto")]))

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._poll_microphone_events()

        set_text.assert_called_once_with(
            window.microphone_status,
            "Connected, but no input level detected — unmute and "
            "choose Check Microphone again",
        )

    def test_setup_says_listening_only_after_probe_reports_audio_buffers(self):
        window = self.make_window("ready")
        window.microphone_checking = True
        window.microphone_events.put(("auto", "listening", None))

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._poll_microphone_events()

        self.assertTrue(window.microphone_checking)
        window.check_microphone_button.config.assert_not_called()
        set_text.assert_called_once_with(
            window.microphone_status, "Listening — speak a few words…")

    def test_worker_queues_listening_before_final_microphone_result(self):
        window = self.make_window("ready")

        def check(_selected, on_listening):
            on_listening()
            return "silent"

        window.app.check_input_device.side_effect = check
        window._check_microphone_worker("auto")

        self.assertEqual(
            window.microphone_events.get_nowait(),
            ("auto", "listening", None))
        selected, result, _options = window.microphone_events.get_nowait()
        self.assertEqual((selected, result), ("auto", "silent"))

    def test_stale_listening_event_does_not_invite_speech_on_new_selection(self):
        window = self.make_window("ready")
        window.microphone_checking = True
        window.device_values["Desk microphone"] = "desk"
        window.device.get.return_value = "Desk microphone"
        window.microphone_events.put(("auto", "listening", None))

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._poll_microphone_events()

        set_text.assert_not_called()
        self.assertTrue(window.microphone_checking)

    def test_completed_check_supersedes_unshown_listening_event(self):
        window = self.make_window("ready")
        window.microphone_checking = True
        window.microphone_events.put(("auto", "listening", None))
        window.microphone_events.put(("auto", "level", None))

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._poll_microphone_events()

        self.assertFalse(window.microphone_checking)
        set_text.assert_called_once_with(
            window.microphone_status, "Ready — input level detected")

    def test_failed_microphone_result_points_to_recovery_controls(self):
        window = self.make_window("ready")
        window.microphone_checking = True
        window.microphone_events.put((
            "auto", "unavailable",
            [("Automatic (recommended)", "auto")]))

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._poll_microphone_events()

        set_text.assert_called_once_with(
            window.microphone_status,
            "Needs attention — microphone could not be opened",
        )

    def test_selection_change_during_check_does_not_open_new_device(self):
        window = self.make_window("ready")
        window.microphone_checking = True
        window.device_values = {"Desk microphone": "desk"}
        window.device.get.return_value = "Desk microphone"
        window.microphone_events.put((
            "auto", "level", [("Desk microphone", "desk")]))

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._poll_microphone_events()

        self.assertFalse(window.microphone_checking)
        window.check_microphone_button.config.assert_called_once_with(
            state="normal")
        set_text.assert_called_once_with(
            window.microphone_status, "Not checked")
        window.root.after.assert_not_called()

    def test_reconnected_microphone_refreshes_picker_without_losing_selection(self):
        window = self.make_window("ready")
        selected = "MME::USB microphone"
        unavailable_label = "USB microphone — MME (currently unavailable)"
        available_label = "USB microphone — MME (device 1)"
        window.microphone_checking = True
        window.device_values = {unavailable_label: selected}
        window.device.get.return_value = unavailable_label
        options = [
            ("Automatic (recommended)", "auto"),
            (available_label, selected),
        ]
        window.microphone_events.put((selected, "level", options))

        with mock.patch.object(ui, "_set_accessible_text"):
            window._poll_microphone_events()

        self.assertEqual(window.device_values[available_label], selected)
        window.device.config.assert_called_once_with(
            values=["Automatic (recommended)", available_label])
        window.device.set.assert_called_once_with(available_label)

    def test_picker_refresh_never_falls_back_from_a_newer_selection(self):
        window = self.make_window("ready")
        selected = "MME::Headset microphone"
        window.device_values = {"Headset microphone": selected}

        window._refresh_microphone_options(
            [("Automatic (recommended)", "auto")], selected)

        self.assertEqual(window.device_values, {"Headset microphone": selected})
        window.device.config.assert_not_called()
        window.device.set.assert_not_called()


class UpdateWindowTests(unittest.TestCase):
    def test_release_notes_are_plain_bounded_and_keep_disclosure_first(self):
        rendered = ui._release_notes_for_display(
            "## Model-download privacy correction\r\n"
            "- **Tokens** are not needed. See [privacy guide]"
            "(https://example.invalid/private).\r\n"
            "`HF_ENDPOINT` is ignored.")

        self.assertIn("Model-download privacy correction", rendered)
        self.assertIn("• Tokens are not needed.", rendered)
        self.assertIn("privacy guide", rendered)
        self.assertNotIn("https://example.invalid/private", rendered)
        self.assertIn("HF_ENDPOINT", rendered)
        self.assertNotIn("\r", rendered)
        self.assertEqual(
            ui._release_notes_for_display(None),
            "(No release notes available.)")

    def test_release_notes_are_capped(self):
        rendered = ui._release_notes_for_display("x" * 9000)

        self.assertLessEqual(len(rendered), 8000)
        self.assertTrue(rendered.endswith("[Release notes shortened]"))

    def test_update_dialog_shows_release_notes_before_download_controls(self):
        window = ui.UpdateWindow.__new__(ui.UpdateWindow)
        window.update = {
            "version": "0.1.13",
            "body": "## Model-download privacy correction\n"
                    "This release removes inherited account credentials.",
        }
        root = mock.Mock()
        scrollable = mock.Mock()
        scrollable.content = mock.Mock()
        label_calls = []

        def label(*args, **kwargs):
            label_calls.append(kwargs)
            return mock.Mock()

        with mock.patch.object(ui, "_interactive_window", return_value=root), \
                mock.patch.object(ui, "_ScrollableDialogBody",
                                  return_value=scrollable), \
                mock.patch.object(ui.ttk, "Label", side_effect=label), \
                mock.patch.object(ui.ttk, "Progressbar", return_value=mock.Mock()), \
                mock.patch.object(ui.ttk, "Frame", return_value=mock.Mock()), \
                mock.patch.object(ui.ttk, "Button", return_value=mock.Mock()), \
                mock.patch.object(ui, "_add_access_key"), \
                mock.patch.object(ui, "_bind_window_command"), \
                mock.patch.object(ui, "_mark_live_region"):
            window._build()

        displayed = [call.get("text", "") for call in label_calls]
        notes_position = next(
            index for index, text in enumerate(displayed)
            if "Model-download privacy correction" in text)
        status_position = next(
            index for index, text in enumerate(displayed)
            if text == "Ready to download")
        self.assertLess(notes_position, status_position)
        self.assertIn("inherited account credentials", displayed[notes_position])

    def test_download_moves_focus_before_disabling_its_command(self):
        window = ui.UpdateWindow.__new__(ui.UpdateWindow)
        window.root = mock.Mock()
        window.download_button = mock.Mock()
        window.later_button = mock.Mock()
        window.status = mock.Mock()
        window.cancel_download = mock.Mock()
        window.download_finished = mock.Mock()
        window._discard_completed_download = mock.Mock()
        window.root.focus_get.return_value = window.download_button
        order = []
        window.later_button.focus_set.side_effect = lambda: order.append("focus")
        window.download_button.config.side_effect = (
            lambda **_values: order.append("disable"))

        with mock.patch.object(ui, "_set_accessible_text"), \
                mock.patch.object(ui.threading, "Thread") as thread:
            window._download()

        self.assertEqual(order, ["focus", "disable"])
        window._discard_completed_download.assert_called_once_with()
        window.cancel_download.clear.assert_called_once_with()
        window.download_finished.clear.assert_called_once_with()
        thread.return_value.start.assert_called_once_with()


class ScratchpadWindowTests(unittest.TestCase):
    def make_window(self, *, recording=False, transcribing=False,
                    canceling=False, model_status="ready", waiting=False,
                    capture_ready=True):
        window = ui.ScratchpadWindow.__new__(ui.ScratchpadWindow)
        window.app = mock.Mock()
        window.app.recording = recording
        window.app._capture_ready = capture_ready
        window.app.transcribing = transcribing
        window.app._canceling_recording = canceling
        window.app.model_status = model_status
        window.app.has_undelivered_dictation.return_value = waiting
        window.root = mock.Mock()
        window.btn = mock.Mock()
        window.review_button = mock.Mock()
        window.status = mock.Mock()
        window.text = mock.Mock()
        return window

    def selected_window(self):
        window = self.make_window()
        window.text.index.side_effect = {
            "sel.first": "1.0", "sel.last": "1.15",
        }.get
        window.text.get.return_value = "private words 🐈"
        return window

    def test_dictate_button_uses_same_stop_path_as_hotkey_and_tray(self):
        window = self.make_window(recording=True)
        window._refresh_controls = mock.Mock()

        window.toggle()

        window.app.request_stop.assert_called_once_with()
        window.app.stop_recording.assert_not_called()
        window.app.start_recording.assert_not_called()
        window._refresh_controls.assert_called_once_with()

    def test_dictate_button_starts_when_idle(self):
        window = self.make_window()
        window._refresh_controls = mock.Mock()

        window.toggle()

        window.app.start_recording.assert_called_once_with()
        window.app.request_stop.assert_not_called()
        window._refresh_controls.assert_called_once_with()

    def test_copy_and_cut_virtual_events_stop_tk_class_clipboard_bindings(self):
        window = self.make_window()
        window._copy_or_cut_selection = mock.Mock(return_value="break")

        window._protect_scratchpad_copy_and_cut()

        bindings = {call.args[0]: call.args[1]
                    for call in window.text.bind.call_args_list}
        self.assertEqual(set(bindings), {"<<Copy>>", "<<Cut>>"})
        self.assertEqual(bindings["<<Copy>>"](object()), "break")
        self.assertEqual(bindings["<<Cut>>"](object()), "break")
        self.assertEqual(window._copy_or_cut_selection.call_args_list,
                         [mock.call(cut=False), mock.call(cut=True)])

    def test_unexpected_copy_callback_failure_still_blocks_tk_class_binding(self):
        window = self.make_window()
        window._copy_or_cut_selection = mock.Mock(
            side_effect=RuntimeError("private callback detail"))
        window._protect_scratchpad_copy_and_cut()
        bindings = {call.args[0]: call.args[1]
                    for call in window.text.bind.call_args_list}

        self.assertEqual(bindings["<<Copy>>"](object()), "break")
        self.assertEqual(bindings["<<Cut>>"](object()), "break")

    def test_copy_uses_privacy_writer_and_confirms_current_receipt(self):
        window = self.selected_window()
        receipt = object()
        with mock.patch.object(ui.clipboard_delivery, "write_text",
                               return_value=receipt) as write, \
                mock.patch.object(ui.clipboard_delivery, "is_current",
                                  return_value=True) as current:
            self.assertEqual(window._copy_or_cut_selection(), "break")

        write.assert_called_once_with("private words 🐈")
        current.assert_called_once_with(receipt)
        window.text.delete.assert_not_called()
        window.app.notify.assert_not_called()

    def test_cut_deletes_only_after_confirmed_protected_copy(self):
        window = self.selected_window()
        with mock.patch.object(ui.clipboard_delivery, "write_text",
                               return_value=object()), \
                mock.patch.object(ui.clipboard_delivery, "is_current",
                                  return_value=True):
            self.assertEqual(window._copy_or_cut_selection(cut=True), "break")

        window.text.delete.assert_called_once_with("1.0", "1.15")

    def test_missing_selection_neither_changes_clipboard_nor_runs_default_copy(self):
        window = self.make_window()
        window.text.index.side_effect = ui.tk.TclError("no selection")
        with mock.patch.object(ui.clipboard_delivery, "write_text") as write:
            self.assertEqual(window._copy_or_cut_selection(cut=True), "break")
        write.assert_not_called()
        window.text.delete.assert_not_called()

    def test_unconfirmed_or_failed_copy_never_cuts_or_leaks_text_in_notice(self):
        for failure in ("write", "receipt"):
            with self.subTest(failure=failure):
                window = self.selected_window()
                with mock.patch.object(ui.clipboard_delivery, "write_text") as write, \
                        mock.patch.object(ui.clipboard_delivery, "is_current",
                                          return_value=False):
                    if failure == "write":
                        write.side_effect = OSError("private backend detail")
                    self.assertEqual(
                        window._copy_or_cut_selection(cut=True), "break")
                window.text.delete.assert_not_called()
                window.app.notify.assert_called_once()
                self.assertNotIn("private words", str(window.app.notify.call_args))
                self.assertNotIn("private backend detail", str(window.app.notify.call_args))

    def test_cut_does_not_delete_a_selection_changed_during_clipboard_write(self):
        window = self.selected_window()
        positions = ["1.0", "1.15", "1.1", "1.15"]
        window.text.index.side_effect = positions
        with mock.patch.object(ui.clipboard_delivery, "write_text",
                               return_value=object()), \
                mock.patch.object(ui.clipboard_delivery, "is_current",
                                  return_value=True):
            self.assertEqual(window._copy_or_cut_selection(cut=True), "break")
        window.text.delete.assert_not_called()

    def test_waiting_dictation_enables_review_without_reenabling_dictate(self):
        window = self.make_window(waiting=True)

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._refresh_controls()

        window.btn.config.assert_called_once_with(state="disabled")
        window.review_button.config.assert_called_once_with(state="normal")
        set_text.assert_any_call(
            window.status,
            "An undelivered dictation needs review before recording again. "
            "Choose Review Delivery to copy or discard it.")
        window.app.open_delivery_recovery.assert_not_called()

    def test_resolved_dictation_disables_focused_review_before_next_capture(self):
        window = self.make_window(waiting=True)
        window.root.focus_get.return_value = window.review_button
        order = []
        window.text.focus_set.side_effect = lambda: order.append("focus")
        window.review_button.config.side_effect = (
            lambda **values: order.append(values["state"]))

        with mock.patch.object(ui, "_set_accessible_text"):
            window._refresh_controls()
            window.app.has_undelivered_dictation.return_value = False
            window._refresh_controls()

        self.assertEqual(order, ["normal", "focus", "disabled"])
        self.assertEqual(window.btn.config.call_args_list, [
            mock.call(state="disabled"), mock.call(state="normal")])
        window.app.open_delivery_recovery.assert_not_called()

    def test_connecting_microphone_does_not_invite_speech_before_ready(self):
        window = self.make_window(recording=True, capture_ready=False)

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._refresh_controls()
            window.app._capture_ready = True
            window._refresh_controls()

        set_text.assert_any_call(
            window.status,
            "Connecting microphone… Wait for the start cue or Listening status "
            "before speaking. Press Escape to cancel.",
        )
        set_text.assert_any_call(
            window.status,
            "Recording… Speak, then stop dictation or press Escape to cancel.",
        )

    def test_external_stop_restores_truthful_dictate_command(self):
        window = self.make_window(recording=True)

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._refresh_controls()
            window.app.recording = False
            window._refresh_controls()

        self.assertIn(
            mock.call(window.btn, "Stop Dictation", announce=False),
            set_text.call_args_list,
        )
        self.assertIn(
            mock.call(
                window.btn, "Dictate (or use the hotkey)", announce=False),
            set_text.call_args_list,
        )
        self.assertEqual(
            window.btn.config.call_args_list[-1], mock.call(state="normal"))

    def test_transcribing_moves_focus_before_disabling_command(self):
        window = self.make_window(transcribing=True)
        window.root.focus_get.return_value = window.btn
        order = []
        window.text.focus_set.side_effect = lambda: order.append("focus")
        window.btn.config.side_effect = lambda **_values: order.append("disable")

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._refresh_controls()

        self.assertEqual(order[:2], ["focus", "disable"])
        window.btn.config.assert_called_once_with(state="disabled")
        self.assertIn(
            mock.call(
                window.status,
                "Transcribing… Dictation will be available when this finishes."),
            set_text.call_args_list,
        )

    def test_microphone_check_disables_scratchpad_dictate_until_it_finishes(self):
        window = self.make_window()
        window.app._microphone_check_in_progress = True

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._refresh_controls()
            window.app._microphone_check_in_progress = False
            window._refresh_controls()

        self.assertEqual(
            window.btn.config.call_args_list,
            [mock.call(state="disabled"), mock.call(state="normal")])
        self.assertIn(
            mock.call(
                window.status,
                "Microphone check in progress… Wait for it to finish before dictating."),
            set_text.call_args_list)

    def test_model_failure_exposes_recovery_in_live_status(self):
        window = self.make_window(model_status="error")

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._refresh_controls()

        window.btn.config.assert_called_once_with(state="disabled")
        set_text.assert_any_call(
            window.status,
            "Speech model needs attention. Open Setup or Settings to retry.",
        )

    def test_pending_model_download_choice_opens_setup_in_live_status(self):
        window = self.make_window(model_status="awaiting_download_consent")

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._refresh_controls()

        window.btn.config.assert_called_once_with(state="disabled")
        set_text.assert_any_call(
            window.status,
            "Parakeet model files aren't fully cached. Open Setup to start "
            "the full download (~2.5 GB), choose the smaller English-only "
            "CPU model, or defer.",
        )

    def test_control_poll_keeps_observing_external_lifecycle_changes(self):
        window = self.make_window()
        window._refresh_controls = mock.Mock()

        window._poll_controls()

        window._refresh_controls.assert_called_once_with()
        window.root.after.assert_called_once_with(100, window._poll_controls)


class DeliveryRecoveryWindowTests(unittest.TestCase):
    def make_window(self, waiting=True):
        window = ui.DeliveryRecoveryWindow.__new__(ui.DeliveryRecoveryWindow)
        window.app = mock.Mock()
        window.app.has_undelivered_dictation.return_value = waiting
        window.root = mock.Mock()
        window.status = mock.Mock()
        window.copy_button = mock.Mock()
        window.discard_button = mock.Mock()
        window.leave_button = mock.Mock()
        window._waiting = waiting
        return window

    def test_recovery_actions_match_safe_tab_and_visual_order(self):
        body = inspect.getsource(ui.DeliveryRecoveryWindow._build_window)

        # Creation order is Tk's default tab order. With right-packed controls,
        # packing Discard before Copy puts Copy visually before Discard too.
        self.assertLess(
            body.index('text="Copy for Manual Paste"'),
            body.index('text="Discard Dictation"'),
        )
        self.assertLess(
            body.index('self.discard_button.pack(side="right"'),
            body.index('self.copy_button.pack(side="right")'),
        )

    def test_recovery_dialog_explains_multiple_pending_dictations(self):
        body = inspect.getsource(ui.DeliveryRecoveryWindow._build_window)
        self.assertIn("one or more recent", body)
        self.assertIn("each action handles the ", body)
        self.assertIn("oldest one first", body)
        self.assertIn("until all are resolved", body)

    def test_async_build_failure_restores_tray_fallback(self):
        window = self.make_window()
        window._build_failed = False
        window.root = None
        failure = OSError(r"C:\private\desktop unavailable")
        failed_root = mock.Mock()

        def fail_after_root_creation():
            window.root = failed_root
            raise failure

        window._build_window = mock.Mock(side_effect=fail_after_root_creation)

        with self.assertRaises(OSError) as caught:
            window._build()

        self.assertIs(caught.exception, failure)
        self.assertTrue(window._build_failed)
        self.assertIsNone(window.root)
        failed_root.destroy.assert_called_once_with()
        window.app._report_delivery_recovery_window_failure.assert_called_once_with(
            failure, window)

    def test_failed_copy_stays_visible_and_retains_recovery_actions(self):
        window = self.make_window()
        window.app.copy_undelivered_dictation.return_value = False

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._copy()

        window.copy_button.config.assert_called_once_with(state="normal")
        window.discard_button.config.assert_called_once_with(state="normal")
        self.assertIn("still kept in memory", set_text.call_args.args[1])
        window.leave_button.focus_set.assert_not_called()

    def test_verified_copy_disables_actions_and_moves_focus_before_disabling_it(self):
        window = self.make_window()
        window.root.focus_get.return_value = window.copy_button
        window.app.copy_undelivered_dictation.return_value = True
        window.app.has_undelivered_dictation.return_value = False
        order = []
        window.leave_button.focus_set.side_effect = lambda: order.append("focus")
        window.copy_button.config.side_effect = (
            lambda **_options: order.append("disable-copy"))
        window.discard_button.config.side_effect = (
            lambda **_options: order.append("disable-discard"))

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._copy()

        window.copy_button.config.assert_called_once_with(state="disabled")
        window.discard_button.config.assert_called_once_with(state="disabled")
        self.assertIn("You can record again", set_text.call_args.args[1])
        window.leave_button.focus_set.assert_called_once_with()
        self.assertEqual(order[0], "focus")

    def test_external_resolution_does_not_steal_focus_from_another_control(self):
        window = self.make_window()
        window.app.has_undelivered_dictation.return_value = False
        another_control = object()
        window.root.focus_get.return_value = another_control

        with mock.patch.object(ui, "_set_accessible_text"):
            window._refresh_waiting_state()

        window.leave_button.focus_set.assert_not_called()
        window.copy_button.config.assert_called_once_with(state="disabled")
        window.discard_button.config.assert_called_once_with(state="disabled")

    def test_disabling_focused_discard_moves_focus_before_it_is_disabled(self):
        window = self.make_window()
        window.app.has_undelivered_dictation.return_value = False
        window.root.focus_get.return_value = window.discard_button
        order = []
        window.leave_button.focus_set.side_effect = lambda: order.append("focus")
        window.copy_button.config.side_effect = (
            lambda **_options: order.append("disable-copy"))
        window.discard_button.config.side_effect = (
            lambda **_options: order.append("disable-discard"))

        with mock.patch.object(ui, "_set_accessible_text"):
            window._refresh_waiting_state()

        window.leave_button.focus_set.assert_called_once_with()
        self.assertEqual(order, ["disable-copy", "focus", "disable-discard"])

    def test_discard_never_requests_a_copy_and_reports_completion(self):
        window = self.make_window()
        window.app.discard_undelivered_dictation.return_value = True
        window.app.has_undelivered_dictation.return_value = False

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._discard()

        window.app.discard_undelivered_dictation.assert_called_once_with()
        window.app.copy_undelivered_dictation.assert_not_called()
        self.assertEqual(
            set_text.call_args.args[1],
            "Dictation discarded. You can record again.")

    def test_stale_discard_does_not_claim_completion(self):
        window = self.make_window()
        window.app.discard_undelivered_dictation.return_value = False
        window.app.has_undelivered_dictation.return_value = False

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._discard()

        self.assertEqual(
            set_text.call_args.args[1],
            "No undelivered dictation remains. You can record again.")

    def test_failed_discard_preserves_waiting_state(self):
        window = self.make_window()
        window.app.discard_undelivered_dictation.return_value = False
        window.app.has_undelivered_dictation.return_value = True

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._discard()

        window.copy_button.config.assert_called_once_with(state="normal")
        window.discard_button.config.assert_called_once_with(state="normal")
        self.assertIn("still kept in memory", set_text.call_args.args[1])

    def test_external_tray_action_updates_the_open_window(self):
        window = self.make_window(waiting=True)
        window.app.has_undelivered_dictation.return_value = False

        with mock.patch.object(window, "_refresh_waiting_state") as refresh:
            window._poll()

        refresh.assert_called_once_with()
        window.root.after.assert_called_once_with(250, window._poll)

    def test_close_keeps_text_owned_by_app_for_later_review(self):
        window = self.make_window()
        window.app.delivery_recovery_window = window
        root = window.root

        window._close()

        root.destroy.assert_called_once_with()
        self.assertIsNone(window.root)
        self.assertIsNone(window.app.delivery_recovery_window)
        window.app.discard_undelivered_dictation.assert_not_called()
        # A stale queued poll or repeated close must not touch destroyed Tk.
        window._poll()
        window._close()
        root.after.assert_not_called()
        root.destroy.assert_called_once_with()

    def test_old_close_does_not_clear_a_newer_recovery_window(self):
        window = self.make_window()
        newer = object()
        window.app.delivery_recovery_window = newer

        window._close()

        self.assertIsNone(window.root)
        self.assertIs(window.app.delivery_recovery_window, newer)
        window.app.discard_undelivered_dictation.assert_not_called()


class ScratchpadCloseTests(unittest.TestCase):
    def make_window(self, *, recording, owns_recording):
        window = ui.ScratchpadWindow.__new__(ui.ScratchpadWindow)
        window.app = mock.Mock()
        window.app.recording = recording
        window.app._recording_scratchpad = window if owns_recording else None
        window.app.scratchpad = window
        window.root = mock.Mock()
        window.window_handle = 1234
        window.text = mock.Mock()
        window._append_lock = threading.Lock()
        window._pending_appends = {}
        window._next_append_id = 0
        return window

    def test_close_cancels_recording_owned_by_private_scratchpad(self):
        window = self.make_window(recording=True, owns_recording=True)
        root = window.root

        window._close()

        window.app.cancel_recording.assert_called_once_with()
        root.destroy.assert_called_once_with()
        self.assertIsNone(window.root)
        self.assertEqual(window.window_handle, 0)
        self.assertIsNone(window.app.scratchpad)

    def test_close_does_not_cancel_another_app_destination(self):
        window = self.make_window(recording=True, owns_recording=False)

        window._close()

        window.app.cancel_recording.assert_not_called()

    def test_close_after_capture_stopped_does_not_cancel_transcription(self):
        window = self.make_window(recording=False, owns_recording=True)

        window._close()

        window.app.cancel_recording.assert_not_called()

    def test_close_retains_queued_insert_and_late_callback_cannot_duplicate_it(self):
        window = self.make_window(recording=False, owns_recording=True)
        root = window.root
        window.append_text("private test transcript")
        callback = root.after.call_args.args[1]

        window._close()
        callback()  # A queued callback must be harmless even if Tk did not cancel it.

        window.text.insert.assert_not_called()
        window.app._remember_undelivered_dictation.assert_called_once_with(
            "private test transcript", "scratchpad-unavailable")

    def test_successful_editor_insert_is_not_recovered_on_later_close(self):
        window = self.make_window(recording=False, owns_recording=True)
        root = window.root
        window.append_text("private test transcript")
        root.after.call_args.args[1]()

        window._close()

        window.text.insert.assert_called_once_with(
            "end", "private test transcript")
        window.app._remember_undelivered_dictation.assert_not_called()

    def test_close_during_editor_insert_recovers_without_deadlock(self):
        window = self.make_window(recording=False, owns_recording=True)
        root = window.root
        window.text.insert.side_effect = lambda *_args: window._close()
        window.append_text("private test transcript")

        root.after.call_args.args[1]()

        window.app._remember_undelivered_dictation.assert_called_once_with(
            "private test transcript", "scratchpad-unavailable")

    def test_scrolling_failure_after_insert_does_not_duplicate_recovery(self):
        window = self.make_window(recording=False, owns_recording=True)
        root = window.root
        window.text.see.side_effect = RuntimeError("synthetic scroll failure")
        window.append_text("private test transcript")

        root.after.call_args.args[1]()
        window._close()

        window.text.insert.assert_called_once_with(
            "end", "private test transcript")
        window.app._remember_undelivered_dictation.assert_not_called()

    def test_failed_scheduling_or_insert_retains_text_exactly_once(self):
        for failure in ("schedule", "insert"):
            with self.subTest(failure=failure):
                window = self.make_window(recording=False, owns_recording=True)
                root = window.root
                if failure == "schedule":
                    root.after.side_effect = RuntimeError("synthetic Tk failure")
                else:
                    window.text.insert.side_effect = RuntimeError(
                        "synthetic editor failure")

                window.append_text("private test transcript")
                if failure == "insert":
                    root.after.call_args.args[1]()
                window._close()

                window.app._remember_undelivered_dictation.assert_called_once_with(
                    "private test transcript", "scratchpad-unavailable")

    def test_append_after_close_is_recovered_without_scheduling(self):
        window = self.make_window(recording=False, owns_recording=True)
        root = window.root
        window._close()

        window.append_text("private test transcript")

        root.after.assert_not_called()
        window.app._remember_undelivered_dictation.assert_called_once_with(
            "private test transcript", "scratchpad-unavailable")

    def test_old_close_does_not_clear_newer_scratchpad(self):
        window = self.make_window(recording=False, owns_recording=True)
        newer = object()
        window.app.scratchpad = newer

        window._close()

        self.assertIs(window.app.scratchpad, newer)


class PasteSuffixGuidanceTests(unittest.TestCase):
    def test_newline_submission_risk_is_visible_and_in_the_control_name(self):
        source = inspect.getsource(ui.SettingsWindow._build)

        self.assertIn(
            "A newline can submit dictated text in shells and terminals",
            ui.PASTE_SUFFIX_GUIDANCE,
        )
        self.assertIn(
            "Space is the default, not a safety barrier",
            ui.PASTE_SUFFIX_GUIDANCE,
        )
        self.assertTrue(
            ui.PASTE_SUFFIX_ACCESSIBLE_NAME.startswith("After pasting. "))
        self.assertIn("text=PASTE_SUFFIX_GUIDANCE", source)
        self.assertIn(
            "_name_control(self.var_suffix, PASTE_SUFFIX_ACCESSIBLE_NAME)",
            source,
        )


class DictionarySettingsTests(unittest.TestCase):
    def make_window(self, rules=None):
        window = ui.SettingsWindow.__new__(ui.SettingsWindow)
        window.root = mock.Mock()
        window.dictionary_rules = [list(rule) for rule in (rules or [])]
        window.var_spoken = mock.Mock()
        window.var_replace = mock.Mock()
        window.listbox = mock.Mock()
        window.status = mock.Mock()
        window.save_button = mock.Mock()
        return window

    def test_add_rule_keeps_arrow_and_exact_replacement_whitespace(self):
        window = self.make_window()
        window.var_spoken.get.return_value = "  maps to arrow  "
        window.var_replace.get.return_value = "  A \u2192 B  "

        window._add_rule()

        self.assertEqual(
            window.dictionary_rules,
            [["maps to arrow", "  A \u2192 B  "]],
        )
        window.listbox.insert.assert_called_once_with(
            "end", "maps to arrow \u2192   A \u2192 B  ")

    def test_add_rule_rejects_an_oversized_source(self):
        window = self.make_window()
        window.var_spoken.get.return_value = (
            "s" * (ui.cfg.MAX_DICTIONARY_SPOKEN_BYTES + 1))
        window.var_replace.get.return_value = "replacement"

        window._add_rule()

        self.assertEqual(window.dictionary_rules, [])
        window.listbox.insert.assert_not_called()
        window.status.config.assert_called_once_with(
            text="Rule not added. Check the text length or remove an existing rule.")

    def test_add_rule_rejects_more_than_the_rule_limit(self):
        rules = [["source-%d" % index, "replacement"]
                 for index in range(ui.cfg.MAX_DICTIONARY_RULES)]
        window = self.make_window(rules)
        window.var_spoken.get.return_value = "one too many"
        window.var_replace.get.return_value = "replacement"

        window._add_rule()

        self.assertEqual(window.dictionary_rules, rules)
        window.listbox.insert.assert_not_called()

    def test_remove_rule_uses_selected_structured_rule(self):
        window = self.make_window([
            ["first", "one \u2192 two"],
            ["second", "  exact  "],
        ])
        window.listbox.curselection.return_value = (0,)

        window._remove_rule()

        self.assertEqual(window.dictionary_rules, [["second", "  exact  "]])
        window.listbox.delete.assert_called_once_with(0)

    def test_save_does_not_reparse_rendered_rule_labels(self):
        rules = [["maps \u2192 arrow", "  exact \u2192 text  "]]
        window = self.make_window(rules)
        window.app = mock.Mock()
        window.app.lock = threading.Lock()
        window.app.apply_autostart.return_value = True
        window.app.settings = {
            "hotkey": "right alt",
            "trigger": "hold",
            "input_device": "auto",
            "model": "parakeet-tdt-0.6b-v3",
            "suffix": "space",
            "remove_fillers": True,
            "british": True,
            "audio_cues": True,
            "mute_playback_while_recording": True,
            "visual_indicator": True,
            "check_updates": True,
            "autostart": True,
            "dictionary": [],
        }
        window.device_values = {"Automatic": "auto"}
        window.recording_length_values = {
            label: seconds
            for seconds, label in ui.cfg.RECORDING_LENGTHS.items()
        }
        values = {
            "var_hotkey": "right alt",
            "var_trigger": "hold",
            "var_recording_length": ui.cfg.RECORDING_LENGTHS[300],
            "var_device": "Automatic",
            "var_model": ui.cfg.MODEL_LABELS["parakeet-tdt-0.6b-v3"],
            "var_suffix": "space",
            "var_fillers": True,
            "var_british": True,
            "var_audio_cues": True,
            "var_mute_playback": True,
            "var_visual_indicator": True,
            "var_check_updates": True,
            "var_autostart": True,
        }
        for name, value in values.items():
            variable = mock.Mock()
            variable.get.return_value = value
            setattr(window, name, variable)
        window.status = mock.Mock()

        with mock.patch.object(ui.cfg, "save") as save, \
                mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._save()

        self.assertEqual(window.app.settings["dictionary"], rules)
        self.assertTrue(window.app.settings["model_explicit"])
        self.assertEqual(window.app.settings["max_recording_seconds"], 300)
        self.assertIsNot(window.app.settings["dictionary"], window.dictionary_rules)
        window.listbox.get.assert_not_called()
        save.assert_called_once_with(window.app.settings)
        window.app.apply_autostart.assert_called_once_with()
        set_text.assert_called_once_with(
            window.status, "Saved. Changes apply immediately.")

        window.app.apply_autostart.return_value = False
        with mock.patch.object(ui.cfg, "save"), \
                mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._save()

        set_text.assert_called_once_with(
            window.status,
            "Saved, but Start with Windows was not updated. "
            "Open Startup Settings to review it.",
        )

    def test_save_prepares_a_changed_model_immediately(self):
        window = self.make_window()
        window.app = mock.Mock()
        window.app.lock = threading.Lock()
        window.app.apply_autostart.return_value = True
        window.app.settings = {
            "hotkey": "right alt",
            "trigger": "hold",
            "max_recording_seconds": 120,
            "input_device": "auto",
            "model": "base.en",
            "suffix": "space",
            "remove_fillers": True,
            "british": True,
            "audio_cues": True,
            "mute_playback_while_recording": True,
            "visual_indicator": True,
            "check_updates": True,
            "autostart": True,
            "dictionary": [],
        }
        window.device_values = {"Automatic": "auto"}
        window.recording_length_values = {
            label: seconds
            for seconds, label in ui.cfg.RECORDING_LENGTHS.items()
        }
        values = {
            "var_hotkey": "right alt",
            "var_trigger": "hold",
            "var_recording_length": ui.cfg.RECORDING_LENGTHS[600],
            "var_device": "Automatic",
            "var_model": ui.cfg.MODEL_LABELS["small.en"],
            "var_suffix": "space",
            "var_fillers": True,
            "var_british": True,
            "var_audio_cues": True,
            "var_mute_playback": True,
            "var_visual_indicator": True,
            "var_check_updates": True,
            "var_autostart": True,
        }
        for name, value in values.items():
            variable = mock.Mock()
            variable.get.return_value = value
            setattr(window, name, variable)

        with mock.patch.object(ui.cfg, "save"):
            window._save()

        self.assertEqual(window.app.settings["model"], "small.en")
        self.assertEqual(window.app.settings["max_recording_seconds"], 600)
        window.app.prepare_configured_model.assert_called_once_with()

    def test_model_status_exposes_ready_error_and_retry_states(self):
        window = self.make_window()
        window.root = mock.Mock()
        window.model_status = mock.Mock()
        window.retry_model_button = mock.Mock()
        window.var_model = mock.Mock()
        window.hotkey_status = mock.Mock()
        window.repair_hotkey_button = mock.Mock()
        window.app = mock.Mock()
        window.app.settings = {"model": "base.en"}
        window.app.model_status = "ready"
        window.app.model_status_detail = "base.en on cpu (int8)"
        window.app.transcriber.loaded.return_value = True
        window.app.hotkey_listener_status.return_value = (
            "ready", "Ready — Right Alt")
        window.root.focus_get.return_value = window.retry_model_button

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._poll_model()

        self.assertEqual(set_text.call_args_list, [
            mock.call(
                window.model_status,
                "Speech model ready — base.en on cpu (int8)"),
            mock.call(
                window.hotkey_status,
                "Global hotkey status: Ready — Right Alt"),
        ])
        window.retry_model_button.config.assert_called_once_with(state="disabled")
        window.var_model.focus_set.assert_called_once_with()
        window.repair_hotkey_button.config.assert_called_once_with(
            state="normal")

        window.app.model_status = "error"
        window.app.model_status_detail = "download unavailable"
        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._poll_model()

        self.assertIn(
            mock.call(
                window.model_status,
                "Speech model needs attention — download unavailable"),
            set_text.call_args_list,
        )
        window.retry_model_button.config.assert_called_with(state="normal")

        window.app.model_status = "awaiting_download_consent"
        window.app.settings["model"] = "parakeet-tdt-0.6b-v3"
        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._poll_model()

        self.assertIn(
            mock.call(
                window.model_status,
                "Parakeet model files aren't fully cached. Choose whether to "
                "start the full download (~2.5 GB) in Setup."),
            set_text.call_args_list,
        )
        window.retry_model_button.config.assert_called_with(state="disabled")

    def test_save_is_blocked_atomically_during_recording(self):
        window = self.make_window([["original", "rule"]])
        window.app = mock.Mock()
        window.app.lock = threading.Lock()
        window.app._starting_recording = False
        window.app.recording = True
        window.app.transcribing = False
        window.app._canceling_recording = False
        window.app.settings = {"model": "base.en", "dictionary": []}
        window.root.focus_get.return_value = window.save_button
        order = []
        window.listbox.focus_set.side_effect = lambda: order.append("focus")
        window.save_button.config.side_effect = lambda **_kwargs: order.append("disable")

        with mock.patch.object(ui.cfg, "save") as save, \
                mock.patch.object(ui, "_set_accessible_text") as set_text:
            result = window._save()

        self.assertFalse(result)
        self.assertEqual(
            window.app.settings, {"model": "base.en", "dictionary": []})
        save.assert_not_called()
        window.app.apply_autostart.assert_not_called()
        self.assertEqual(order, ["focus", "disable"])
        window.save_button.config.assert_called_once_with(state="disabled")
        set_text.assert_called_once_with(
            window.status,
            "Finish or cancel the current dictation before saving settings.",
        )

    def test_save_poll_moves_focus_only_when_disabling_focused_save(self):
        window = self.make_window()
        window.app = types.SimpleNamespace(recording=True)
        window.root.focus_get.return_value = window.save_button
        order = []
        window.listbox.focus_set.side_effect = lambda: order.append("focus")
        window.save_button.config.side_effect = lambda **_kwargs: order.append("disable")

        with mock.patch.object(ui, "_set_accessible_text"):
            window._refresh_save_state()
            self.assertEqual(order, ["focus", "disable"])
            window.root.focus_get.return_value = window.listbox
            window._refresh_save_state()
            window.app.recording = False
            window._refresh_save_state()

        window.listbox.focus_set.assert_called_once_with()
        window.save_button.config.assert_called_with(state="normal")

    def test_every_dictation_transition_has_specific_save_guidance(self):
        app = types.SimpleNamespace(
            _starting_recording=False,
            recording=False,
            _canceling_recording=False,
            transcribing=False,
        )
        states = (
            ("_starting_recording",
             "Wait for the current dictation to finish starting before saving settings."),
            ("recording",
             "Finish or cancel the current dictation before saving settings."),
            ("_canceling_recording",
             "Wait for the canceled dictation to finish closing before saving settings."),
            ("transcribing",
             "Wait for the current dictation to finish before saving settings."),
        )
        for name, message in states:
            with self.subTest(state=name):
                setattr(app, name, True)
                self.assertEqual(ui._settings_save_block_reason(app), message)
                setattr(app, name, False)
        self.assertEqual(ui._settings_save_block_reason(app), "")

    def test_save_state_announces_busy_and_ready_transitions_once(self):
        window = self.make_window()
        window.app = mock.Mock()
        window.app._starting_recording = False
        window.app.recording = True
        window.app.transcribing = False
        window.app._canceling_recording = False

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._refresh_save_state()
            window._refresh_save_state()
            window.app.recording = False
            window.app.transcribing = True
            window._refresh_save_state()
            window.app.transcribing = False
            window._refresh_save_state()

        self.assertEqual(set_text.call_args_list, [
            mock.call(
                window.status,
                "Finish or cancel the current dictation before saving settings."),
            mock.call(
                window.status,
                "Wait for the current dictation to finish before saving settings."),
            mock.call(
                window.status,
                "Dictation finished. Settings can now be saved."),
        ])
        self.assertEqual(window.save_button.config.call_args_list, [
            mock.call(state="disabled"),
            mock.call(state="disabled"),
            mock.call(state="disabled"),
            mock.call(state="normal"),
        ])


if __name__ == "__main__":
    unittest.main()
