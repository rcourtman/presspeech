import sys
import types
import unittest
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
