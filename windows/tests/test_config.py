import json
import os
import tempfile
import unittest
from unittest import mock

import config


class ConfigLoadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temporary.name, "config.json")
        self.path_patch = mock.patch.object(config, "CONFIG_PATH", self.path)
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.temporary.cleanup()

    def write(self, payload):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)

    def test_non_object_json_falls_back_to_defaults(self):
        for payload in (None, [], "settings", 7, True):
            with self.subTest(payload=payload):
                self.write(payload)
                self.assertEqual(config.load(), config.DEFAULTS)

    def test_invalid_values_fall_back_without_discarding_valid_settings(self):
        self.write({
            "hotkey": "left ctrl",
            "trigger": "sometimes",
            "model": None,
            "suffix": "tab",
            "remove_fillers": 1,
            "check_updates": False,
            "input_device": "",
            "last_update_check_epoch": -1,
            "capture_benchmark_remaining": True,
            "capture_benchmark_index": 0,
            "gpu_idle_unload_sec": "30",
            "unknown_future_setting": "ignored",
        })

        settings = config.load()

        self.assertEqual(settings["hotkey"], "left ctrl")
        self.assertFalse(settings["check_updates"])
        for key in (
                "trigger", "model", "suffix", "remove_fillers",
                "input_device", "last_update_check_epoch",
                "capture_benchmark_remaining", "capture_benchmark_index",
                "gpu_idle_unload_sec"):
            self.assertEqual(settings[key], config.DEFAULTS[key])
        self.assertNotIn("unknown_future_setting", settings)

    def test_dictionary_keeps_only_string_pairs(self):
        self.write({
            "dictionary": [
                ["press speech", "Presspeech"],
                ["empty replacement", ""],
                ["missing replacement"],
                ["too", "many", "parts"],
                [7, "number"],
                "not a pair",
            ],
        })

        self.assertEqual(config.load()["dictionary"], [
            ["press speech", "Presspeech"],
            ["empty replacement", ""],
        ])

    def test_malformed_json_falls_back_to_defaults(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json")

        self.assertEqual(config.load(), config.DEFAULTS)

    def test_default_dictionary_is_not_shared_between_loads(self):
        first = config.load()
        first["dictionary"].append(["local", "mutation"])

        self.assertEqual(config.load()["dictionary"], [])
        self.assertEqual(config.DEFAULTS["dictionary"], [])


if __name__ == "__main__":
    unittest.main()
