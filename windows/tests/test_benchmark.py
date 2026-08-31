import json
import os
import tempfile
import unittest
from unittest import mock

import benchmark


class MetricTests(unittest.TestCase):
    def test_identical_text_has_zero_error(self):
        metrics = benchmark.accuracy_metrics("It works well.", "It works well.")
        self.assertEqual(metrics["wer"], 0)
        self.assertEqual(metrics["cer"], 0)
        self.assertTrue(metrics["exact_match"])

    def test_word_error_rate_ignores_case_and_punctuation(self):
        metrics = benchmark.accuracy_metrics("Hello, colour!", "hello color")
        self.assertEqual(metrics["word_errors"], 1)
        self.assertEqual(metrics["reference_words"], 2)
        self.assertEqual(metrics["wer"], 0.5)

    def test_edit_distance_handles_insert_delete_and_replace(self):
        self.assertEqual(benchmark.edit_distance(["a", "b"], ["a", "x", "b"]), 1)
        self.assertEqual(benchmark.edit_distance(["a", "b"], ["a"]), 1)
        self.assertEqual(benchmark.edit_distance(["a"], ["b"]), 1)

    def test_percentile_uses_observed_upper_value(self):
        self.assertEqual(benchmark._percentile([0.1, 0.2, 0.3, 0.4], 0.95), 0.4)

    def test_auto_precision_does_not_mutate_model(self):
        transcriber = mock.Mock()
        benchmark._apply_precision(transcriber, "auto")
        transcriber.model.to.assert_not_called()

    def test_speech_detection_metrics_count_intermittent_vad_rejection(self):
        metrics = benchmark.speech_detection_metrics(2.0, [
            {"speech_seconds": 1.5},
            {"speech_seconds": 0.0},
            {"speech_seconds": 1.0},
        ])

        self.assertEqual(metrics["min_seconds"], 0.0)
        self.assertEqual(metrics["median_seconds"], 1.0)
        self.assertEqual(metrics["max_seconds"], 1.5)
        self.assertEqual(metrics["median_audio_ratio"], 0.5)
        self.assertEqual(metrics["rejected_trials"], 1)
        self.assertEqual(metrics["trials"], 3)

    def test_speech_detection_metrics_ignore_non_whisper_timings(self):
        self.assertIsNone(benchmark.speech_detection_metrics(
            1.0, [{"generate": 0.1}, mock.sentinel.timing]))

    def test_reviewed_silence_scores_empty_output_as_clean(self):
        self.assertEqual(
            benchmark.silence_metrics(True, True, [" \n", ""]),
            {
                "evaluated": True,
                "false_positive": False,
                "false_positive_trials": 0,
                "trials": 2,
            },
        )

    def test_reviewed_silence_scores_words_as_false_positive(self):
        self.assertEqual(
            benchmark.silence_metrics(True, True, ["", "Thank you.", ""]),
            {
                "evaluated": True,
                "false_positive": True,
                "false_positive_trials": 1,
                "trials": 3,
            },
        )

    def test_silence_fixture_requires_review_before_scoring(self):
        self.assertEqual(
            benchmark.silence_metrics(True, False, [""]),
            {
                "evaluated": False,
                "false_positive": None,
                "false_positive_trials": None,
                "trials": 1,
            },
        )
        self.assertIsNone(benchmark.silence_metrics(False, True, [""]))

    def test_benchmark_aggregates_reviewed_silence_false_positives(self):
        manifest = {
            "runs": 1,
            "samples": [
                {
                    "id": "quiet-room",
                    "audio": "quiet.wav",
                    "expected_silence": True,
                    "reference_reviewed": True,
                },
                {
                    "id": "background-noise",
                    "audio": "noise.wav",
                    "expected_silence": True,
                    "reference_reviewed": True,
                },
            ],
        }
        transcriber = mock.Mock()
        transcriber.transcribe.side_effect = ["", "Thank you."]
        transcriber.model.dtype = "int8"

        with tempfile.TemporaryDirectory() as directory:
            manifest_path = os.path.join(directory, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with mock.patch.object(
                    benchmark.engine, "Transcriber", return_value=transcriber), \
                    mock.patch.object(
                        benchmark, "load_audio",
                        return_value=(mock.sentinel.audio, 1.0, 16000)):
                result = benchmark.run_benchmark(manifest_path)

        self.assertEqual(result["reviewed_silence_sample_count"], 2)
        self.assertEqual(result["silence_false_positive_count"], 1)
        self.assertEqual(result["reviewed_silence_trial_count"], 2)
        self.assertEqual(result["silence_false_positive_trial_count"], 1)
        self.assertFalse(result["samples"][0]["silence"]["false_positive"])
        self.assertTrue(result["samples"][1]["silence"]["false_positive"])

    def test_benchmark_reports_reviewed_speech_vad_rejections(self):
        manifest = {
            "runs": 2,
            "samples": [{
                "id": "quiet-speech",
                "audio": "quiet.wav",
                "reference": "quiet speech",
                "reference_reviewed": True,
            }],
        }
        transcriber = mock.Mock()
        transcriber.model.dtype = "int8"
        speech_seconds = iter((1.25, 0.0))

        def transcribe(*_args, **_kwargs):
            detected = next(speech_seconds)
            transcriber.last_timing = {
                "backend": "whisper",
                "speech_seconds": detected,
            }
            return "quiet speech" if detected else ""

        transcriber.transcribe.side_effect = transcribe
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = os.path.join(directory, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with mock.patch.object(
                    benchmark.engine, "Transcriber", return_value=transcriber), \
                    mock.patch.object(
                        benchmark, "load_audio",
                        return_value=(mock.sentinel.audio, 2.0, 16000)):
                result = benchmark.run_benchmark(manifest_path)

        detection = result["samples"][0]["speech_detection"]
        self.assertEqual(detection["all_seconds"], [1.25, 0.0])
        self.assertEqual(result["reviewed_speech_vad_rejection_count"], 1)
        self.assertEqual(
            result["reviewed_speech_vad_rejection_trial_count"], 1)


if __name__ == "__main__":
    unittest.main()
