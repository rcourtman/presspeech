import sys
import types
import unittest
import queue
import inspect
from unittest import mock

try:
    import tkinter  # noqa: F401
except ModuleNotFoundError:
    # Keep these model-free state tests portable to minimal Python workers.
    tkinter = types.ModuleType("tkinter")
    tkinter.messagebox = mock.Mock()
    tkinter.ttk = mock.Mock()
    sys.modules["tkinter"] = tkinter

import ui


class AccessibleWindowTests(unittest.TestCase):
    def test_dialog_viewport_uses_content_size_until_screen_margin(self):
        self.assertEqual(ui._bounded_viewport(500, 1920, 96, 320), 500)
        self.assertEqual(ui._bounded_viewport(1900, 1920, 96, 320), 1824)
        self.assertEqual(ui._bounded_viewport(900, 768, 128, 240), 640)

    def test_dialog_viewport_stays_positive_on_unusual_small_desktop(self):
        self.assertEqual(ui._bounded_viewport(200, 100, 128, 240), 68)
        self.assertEqual(ui._bounded_viewport(0, 1920, 96, 320), 1)

    def test_every_interactive_window_uses_the_shared_host(self):
        host = mock.Mock()
        app = mock.Mock()

        with mock.patch.object(ui, "_window_host", return_value=host):
            windows = (
                ui.SetupWindow(app),
                ui.UpdateWindow(app, {"version": "1.2.3"}),
                ui.SettingsWindow(app),
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

    def test_setup_and_settings_use_scrollable_resizable_dialogs(self):
        for window in (ui.SetupWindow, ui.SettingsWindow):
            body = inspect.getsource(window)
            self.assertIn("root.resizable(True, True)", body)
            self.assertIn("_ScrollableDialogBody(root", body)
            self.assertIn("self.scrollable_body.fit_to_screen()", body)

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
                mock.patch.object(ui.tk_uia, "set_acc_name") as set_name:
            ui._label_control(label, control)
            ui._name_control(control, "Dictionary rules")

        label_for.assert_called_once_with(label, control)
        set_name.assert_called_once_with(control, "Dictionary rules")

    def test_changed_visible_text_refreshes_its_accessible_name(self):
        widget = mock.Mock()

        with mock.patch.object(ui.tk_uia, "add_acc_object") as refresh:
            ui._set_accessible_text(widget, "Verified and ready to install")

        widget.config.assert_called_once_with(
            text="Verified and ready to install")
        refresh.assert_called_once_with(widget)

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
        window.root = mock.Mock()
        window.model_label = mock.Mock()
        window.progress = mock.Mock()
        window.retry_button = mock.Mock()
        window.try_button = mock.Mock()
        window.finish_button = mock.Mock()
        window.microphone_events = queue.Queue()
        window.microphone_checking = False
        window.check_microphone_button = mock.Mock()
        window.microphone_status = mock.Mock()
        window.device_values = {"Automatic (recommended)": "auto"}
        window.device = mock.Mock()
        window.device.get.return_value = "Automatic (recommended)"
        return window

    def test_error_remains_observed_and_enables_retry(self):
        window = self.make_window("error", "download failed")

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._poll_model()

        set_text.assert_called_once_with(
            window.model_label, "Needs attention — download failed")
        window.retry_button.config.assert_called_once_with(state="normal")
        window.try_button.config.assert_called_once_with(state="disabled")
        window.finish_button.config.assert_called_once_with(state="disabled")
        window.progress.config.assert_called_once_with(
            mode="determinate", value=0)
        window.root.after.assert_called_once_with(300, window._poll_model)

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

    def test_ready_model_can_finish_without_a_connected_microphone(self):
        window = self.make_window("ready")
        window.app.settings = {
            "input_device": "auto",
            "autostart": True,
            "setup_complete": False,
        }
        window.autostart = mock.Mock()
        window.autostart.get.return_value = False
        window._close = mock.Mock()

        with mock.patch.object(ui.cfg, "save") as save:
            window._finish()

        self.assertTrue(window.app.settings["setup_complete"])
        self.assertFalse(window.app.settings["autostart"])
        save.assert_called_once_with(window.app.settings)
        window.app.apply_autostart.assert_called_once_with()
        window._close.assert_called_once_with()

    def test_setup_hotkey_change_applies_and_persists_before_finish(self):
        window = self.make_window("pending")
        window.app.settings = {
            "hotkey": "right alt",
            "trigger": "hold",
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
            "Hold F8, speak, then release to type at the cursor.\n"
            "Speech stays on this PC; no audio or transcripts are uploaded.",
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

    def test_setup_instructions_respect_existing_toggle_mode(self):
        window = self.make_window("ready")
        window.app.settings = {"hotkey": "f9", "trigger": "toggle"}

        self.assertEqual(
            window._dictation_instructions(),
            "Press F9 to start, then press it again to type at the cursor.\n"
            "Speech stays on this PC; no audio or transcripts are uploaded.",
        )

    def test_microphone_check_runs_off_the_ui_thread(self):
        window = self.make_window("ready")

        with mock.patch.object(ui, "_set_accessible_text") as set_text, \
                mock.patch.object(ui.threading, "Thread") as thread:
            window._check_microphone()

        self.assertTrue(window.microphone_checking)
        window.check_microphone_button.config.assert_called_once_with(
            state="disabled")
        set_text.assert_called_once_with(window.microphone_status, "Checking…")
        thread.assert_called_once_with(
            target=window._check_microphone_worker,
            args=("auto",),
            name="presspeech-microphone-check",
            daemon=True,
        )
        thread.return_value.start.assert_called_once_with()

    def test_ready_microphone_result_is_exposed_accessibly(self):
        window = self.make_window("ready")
        window.microphone_checking = True
        window.app.check_input_device.return_value = True
        window._check_microphone_worker("auto")

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._poll_microphone_events()

        window.app.check_input_device.assert_called_once_with("auto")
        self.assertFalse(window.microphone_checking)
        window.check_microphone_button.config.assert_called_once_with(
            state="normal")
        set_text.assert_called_once_with(
            window.microphone_status,
            "Ready — microphone audio is available",
        )

    def test_failed_microphone_result_points_to_recovery_controls(self):
        window = self.make_window("ready")
        window.microphone_checking = True
        window.microphone_events.put(("auto", False))

        with mock.patch.object(ui, "_set_accessible_text") as set_text:
            window._poll_microphone_events()

        set_text.assert_called_once_with(
            window.microphone_status,
            "Needs attention — check the Windows settings below",
        )


class DictionarySettingsTests(unittest.TestCase):
    def make_window(self, rules=None):
        window = ui.SettingsWindow.__new__(ui.SettingsWindow)
        window.dictionary_rules = [list(rule) for rule in (rules or [])]
        window.var_spoken = mock.Mock()
        window.var_replace = mock.Mock()
        window.listbox = mock.Mock()
        window.status = mock.Mock()
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
        values = {
            "var_hotkey": "right alt",
            "var_trigger": "hold",
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

        with mock.patch.object(ui.cfg, "save") as save:
            window._save()

        self.assertEqual(window.app.settings["dictionary"], rules)
        self.assertTrue(window.app.settings["model_explicit"])
        self.assertIsNot(window.app.settings["dictionary"], window.dictionary_rules)
        window.listbox.get.assert_not_called()
        save.assert_called_once_with(window.app.settings)


if __name__ == "__main__":
    unittest.main()
