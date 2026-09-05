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
            "max_recording_seconds": 30,
            "unknown_future_setting": "ignored",
        })

        settings = config.load()

        self.assertEqual(settings["hotkey"], "left ctrl")
        self.assertFalse(settings["check_updates"])
        for key in (
                "trigger", "model", "suffix", "remove_fillers",
                "input_device", "last_update_check_epoch",
                "capture_benchmark_remaining", "capture_benchmark_index",
                "gpu_idle_unload_sec", "max_recording_seconds"):
            self.assertEqual(settings[key], config.DEFAULTS[key])
        self.assertNotIn("unknown_future_setting", settings)

    def test_every_advertised_hotkey_survives_validation(self):
        for hotkey in config.HOTKEYS:
            with self.subTest(hotkey=hotkey):
                self.write({"hotkey": hotkey})
                self.assertEqual(config.load()["hotkey"], hotkey)

    def test_every_recording_length_survives_validation(self):
        for seconds in config.RECORDING_LENGTHS:
            with self.subTest(seconds=seconds):
                self.assertEqual(config.recording_length_seconds(seconds), seconds)
                self.write({"max_recording_seconds": seconds})
                self.assertEqual(
                    config.load()["max_recording_seconds"], seconds)

    def test_recording_length_rejects_boolean_and_unbounded_values(self):
        for value in (True, 0, 30, 601, 3600, "300", [], None):
            with self.subTest(value=value):
                self.assertEqual(
                    config.recording_length_seconds(value),
                    config.DEFAULTS["max_recording_seconds"],
                )
                self.write({"max_recording_seconds": value})
                self.assertEqual(
                    config.load()["max_recording_seconds"],
                    config.DEFAULTS["max_recording_seconds"],
                )

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

    def test_dictionary_rejects_oversized_and_nul_rules(self):
        self.write({
            "dictionary": [
                ["valid", "replacement"],
                ["", "empty source"],
                ["   ", "blank source"],
                ["nul\0source", "replacement"],
                ["source", "nul\0replacement"],
                ["unpaired-\ud800", "replacement"],
                ["s" * (config.MAX_DICTIONARY_SPOKEN_BYTES + 1), "replacement"],
                ["source", "r" * (config.MAX_DICTIONARY_REPLACEMENT_BYTES + 1)],
                ["é" * config.MAX_DICTIONARY_SPOKEN_BYTES, "too many UTF-8 bytes"],
            ],
        })

        self.assertEqual(config.load()["dictionary"], [
            ["valid", "replacement"],
        ])

    def test_dictionary_rule_count_is_bounded(self):
        rules = [["source-%d" % index, "replacement"]
                 for index in range(config.MAX_DICTIONARY_RULES + 3)]
        self.write({"dictionary": rules})

        loaded = config.load()["dictionary"]

        self.assertEqual(len(loaded), config.MAX_DICTIONARY_RULES)
        self.assertEqual(loaded[-1][0], "source-%d" %
                         (config.MAX_DICTIONARY_RULES - 1))

    def test_dictionary_keeps_effective_case_insensitive_duplicate(self):
        self.write({
            "dictionary": [
                ["Press Speech", "presspeech"],
                ["other phrase", "other"],
                ["PRESS SPEECH", "ineffective newer replacement"],
            ],
        })

        self.assertEqual(config.load()["dictionary"], [
            ["Press Speech", "presspeech"],
            ["other phrase", "other"],
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
