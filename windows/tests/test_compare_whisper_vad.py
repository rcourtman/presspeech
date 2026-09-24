"""Model-free checks for private Whisper VAD report comparison."""

import copy
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

import compare_whisper_vad as compare


def reports():
    speech = {
        "id": "private-name-must-not-print",
        "audio": "private-path-must-not-print.wav",
        "audio_seconds": 2.0, "source_sample_rate": 16000,
        "runs": 2, "reference_reviewed": True,
        "task_group": "quiet-speech", "language_group": "en-GB",
        "passes_app_minimum_audio_duration": True,
        "inference_seconds": {"all": [0.2, 0.3]},
        "speech_detection": {
            "trials": 2, "measured_trials": 2, "missing_trials": 0,
            "rejected_trials": 0, "all_seconds": [1.5, 1.5],
        },
        "accuracy": {"reference_words": 3},
        "trial_accuracy": {
            "trials": 2, "all_word_errors": [0, 0],
            "all_max_reference_deletion_runs": [0, 0],
            "worst_word_errors": 0, "worst_max_reference_deletion_run": 0,
        },
        "first_word": {"trials": 2, "failed_trials": 0},
        "final_word": {"trials": 2, "failed_trials": 0},
        "silence": None,
        "reference": "private speech must not print",
        "transcript": "private speech must not print",
    }
    silence = {
        "id": "silence", "audio": "private-silence.wav",
        "audio_seconds": 2.0, "source_sample_rate": 16000,
        "runs": 2, "reference_reviewed": True,
        "task_group": "quiet-speech", "language_group": None,
        "passes_app_minimum_audio_duration": True,
        "inference_seconds": {"all": [0.1, 0.1]},
        "speech_detection": {
            "trials": 2, "measured_trials": 2, "missing_trials": 0,
            "rejected_trials": 2, "all_seconds": [0.0, 0.0],
        },
        "accuracy": None, "silence": {
            "evaluated": True, "trials": 2, "false_positive_trials": 0,
        },
        "transcript": "",
    }
    base = {
        "benchmark_version": 25,
        "benchmark_inputs_sha256": "a" * 64,
        "benchmark_order_sha256": "b" * 64,
        "model": "base.en",
        "model_snapshot": {"repository": "reviewed/model", "revision": "c" * 40},
        "requested_language": "en", "precision": "auto",
        "model_dtype": "unknown", "environment": {"platform": "test-system"},
        "app_minimum_audio_duration_seconds": 0.25,
        "parakeet_tail_silence_ms": None,
        "parakeet_recorded_tail_probe": False,
        "whisper_vad_policy": {
            "threshold": 0.5, "neg_threshold": 0.35,
            "min_speech_duration_ms": 0, "min_silence_duration_ms": 160,
            "speech_pad_ms": 400,
        },
        "sample_count": 2,
        "samples": [speech, silence],
    }
    candidate = copy.deepcopy(base)
    candidate["whisper_vad_policy"]["min_silence_duration_ms"] = 2000
    return base, candidate


class CompareWhisperVadTests(unittest.TestCase):
    def test_paired_comparison_exposes_regressions_without_private_content(self):
        base, candidate = reports()
        changed = candidate["samples"][0]
        changed["trial_accuracy"].update({
            "all_word_errors": [0, 1], "worst_word_errors": 1,
            "all_max_reference_deletion_runs": [0, 1],
            "worst_max_reference_deletion_run": 1,
        })
        changed["first_word"]["failed_trials"] = 1
        changed["speech_detection"].update({
            "rejected_trials": 1, "all_seconds": [1.5, 0.0],
        })
        candidate["samples"][1]["silence"]["false_positive_trials"] = 1
        result = compare.compare_reports(base, candidate)

        self.assertEqual(result["baseline"]["trial_wer"], 0)
        self.assertEqual(result["candidate"]["trial_wer"], 1 / 6)
        self.assertEqual(result["candidate"]["worst_deletion_run"], 1)
        self.assertEqual(result["baseline"]["error_free_trials"], 2)
        self.assertEqual(result["candidate"]["error_free_trials"], 1)
        self.assertEqual(result["candidate"]["vad_rejections"], 1)
        self.assertEqual(result["candidate"]["silence_false_positives"], 1)
        self.assertEqual(result["regressions"]["word_errors"], [1])
        self.assertEqual(result["regressions"]["error_free_trials"], [1])
        self.assertEqual(result["regressions"]["first_word"], [1])
        self.assertEqual(result["regressions"]["worst_deletion_run"], [1])
        self.assertEqual(result["regressions"]["silence_false_positives"], [2])
        self.assertEqual(result["strata"]["task_group"], {
            "count": 1, "regressed": 1,
        })
        self.assertEqual(result["strata"]["language_group"], {
            "count": 1, "regressed": 1,
        })
        self.assertEqual(result["strata"]["language_task_group"], {
            "count": 1, "regressed": 1,
        })
        self.assertNotIn("private", repr(result))

    def test_refuses_noncomparable_inputs_settings_and_review_scope(self):
        for field, value in (
                ("benchmark_inputs_sha256", "d" * 64),
                ("benchmark_order_sha256", "d" * 64),
                ("model", "turbo"),
                ("requested_language", "auto"),
                ("environment", {"platform": "other"})):
            base, candidate = reports()
            candidate[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                    ValueError, "reports differ"):
                compare.compare_reports(base, candidate)
        base, candidate = reports()
        candidate["samples"][0]["runs"] = 3
        with self.assertRaises(ValueError):
            compare.compare_reports(base, candidate)
        base, candidate = reports()
        candidate["samples"][0]["reference_reviewed"] = False
        with self.assertRaises(ValueError):
            compare.compare_reports(base, candidate)
        base, candidate = reports()
        candidate["samples"][0]["accuracy"]["reference_words"] = 2
        with self.assertRaisesRegex(ValueError, "reference word count"):
            compare.compare_reports(base, candidate)

    def test_only_minimum_silence_threshold_may_change(self):
        base, candidate = reports()
        candidate["whisper_vad_policy"]["speech_pad_ms"] = 100
        with self.assertRaisesRegex(ValueError, "beyond minimum silence"):
            compare.compare_reports(base, candidate)
        base, candidate = reports()
        candidate["whisper_vad_policy"]["min_silence_duration_ms"] = 160
        with self.assertRaisesRegex(ValueError, "did not change"):
            compare.compare_reports(base, candidate)

    def test_requires_reviewed_speech_and_silence_controls(self):
        for retained_index in (0, 1):
            base, candidate = reports()
            base["samples"] = [base["samples"][retained_index]]
            candidate["samples"] = [candidate["samples"][retained_index]]
            base["sample_count"] = candidate["sample_count"] = 1
            with self.subTest(retained_index=retained_index), \
                    self.assertRaisesRegex(ValueError, "speech and silence"):
                compare.compare_reports(base, candidate)

    def test_below_app_gate_model_only_clips_remain_visible(self):
        base, candidate = reports()
        for report in (base, candidate):
            report["samples"][0]["passes_app_minimum_audio_duration"] = False
        result = compare.compare_reports(base, candidate)
        self.assertEqual(result["baseline"]["below_app_gate_clips"], 1)

    def test_rejects_incomplete_vad_or_trial_metrics(self):
        for mutation in (
                lambda report: report["samples"][0]["speech_detection"].update(
                    missing_trials=1),
                lambda report: report["samples"][0]["trial_accuracy"].update(
                    all_word_errors=[0]),
                lambda report: report["samples"][0]["inference_seconds"].update(
                    all=[float("nan"), 0.2]),
                lambda report: report.update(benchmark_version=24)):
            base, candidate = reports()
            mutation(candidate)
            with self.assertRaises(ValueError):
                compare.compare_reports(base, candidate)

    def test_missing_vad_duration_is_not_a_clean_pass(self):
        base, candidate = reports()
        candidate["samples"][0]["speech_detection"].update({
            "measured_trials": 1, "missing_trials": 1,
            "all_seconds": [1.5],
        })
        result = compare.compare_reports(base, candidate)
        self.assertEqual(result["candidate"]["vad_missing"], 1)
        self.assertEqual(result["regressions"]["vad_missing"], [1])

    def test_clip_regression_is_visible_even_when_pooled_wer_improves(self):
        base, candidate = reports()
        second_base = copy.deepcopy(base["samples"][0])
        second_base["id"] = "another-anonymous-clip"
        second_base["trial_accuracy"].update({
            "all_word_errors": [2, 2], "worst_word_errors": 2,
        })
        second_candidate = copy.deepcopy(second_base)
        second_candidate["trial_accuracy"].update({
            "all_word_errors": [0, 0], "worst_word_errors": 0,
        })
        base["samples"].append(second_base)
        candidate["samples"].append(second_candidate)
        base["sample_count"] = candidate["sample_count"] = 3
        candidate["samples"][0]["trial_accuracy"].update({
            "all_word_errors": [1, 0], "worst_word_errors": 1,
        })

        result = compare.compare_reports(base, candidate)

        self.assertLess(result["candidate"]["trial_wer"],
                        result["baseline"]["trial_wer"])
        self.assertEqual(result["regressions"]["word_errors"], [1])
        self.assertEqual(result["regressions"]["worst_trial_errors"], [1])
        self.assertEqual(result["strata"]["task_group"], {
            "count": 1, "regressed": 1,
        })

    def test_loss_of_error_free_trial_is_visible_despite_unchanged_wer(self):
        base, candidate = reports()
        base["samples"][0]["trial_accuracy"].update({
            "all_word_errors": [0, 2], "worst_word_errors": 2,
        })
        candidate["samples"][0]["trial_accuracy"].update({
            "all_word_errors": [1, 1], "worst_word_errors": 1,
        })

        result = compare.compare_reports(base, candidate)

        self.assertEqual(result["baseline"]["trial_wer"],
                         result["candidate"]["trial_wer"])
        self.assertLess(candidate["samples"][0]["trial_accuracy"]["worst_word_errors"],
                        base["samples"][0]["trial_accuracy"]["worst_word_errors"])
        self.assertEqual(result["baseline"]["error_free_trials"], 1)
        self.assertEqual(result["candidate"]["error_free_trials"], 0)
        self.assertEqual(result["regressions"], {"error_free_trials": [1]})

    def test_trial_order_alone_does_not_change_clean_decode_count(self):
        base, candidate = reports()
        base["samples"][0]["trial_accuracy"].update({
            "all_word_errors": [0, 2], "worst_word_errors": 2,
        })
        candidate["samples"][0]["trial_accuracy"].update({
            "all_word_errors": [2, 0], "worst_word_errors": 2,
        })

        result = compare.compare_reports(base, candidate)

        self.assertEqual(result["baseline"]["error_free_trials"], 1)
        self.assertEqual(result["candidate"]["error_free_trials"], 1)
        self.assertEqual(result["regressions"], {})

    def test_cli_does_not_print_transcript_path_or_raw_parse_error(self):
        base, candidate = reports()
        with tempfile.TemporaryDirectory() as root:
            paths = [os.path.join(root, name) for name in ("base.json", "new.json")]
            for path, report in zip(paths, (base, candidate)):
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(report, handle)
            output = io.StringIO()
            with mock.patch.object(sys, "argv", ["compare", *paths]), \
                    redirect_stdout(output):
                compare.main()
            self.assertIn("160 -> 2000 ms", output.getvalue())
            self.assertIn("error_free_trials: 2 -> 2", output.getvalue())
            self.assertNotIn("private speech", output.getvalue())
            self.assertNotIn("private-path", output.getvalue())
            with open(paths[1], "w", encoding="utf-8") as handle:
                handle.write("not JSON")
            error = io.StringIO()
            with mock.patch.object(sys, "argv", ["compare", *paths]), \
                    redirect_stderr(error), self.assertRaises(SystemExit) as exit_info:
                compare.main()
            self.assertEqual(exit_info.exception.code, 2)
            self.assertNotIn(root, error.getvalue())


if __name__ == "__main__":
    unittest.main()
