import json
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
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

    def test_trial_accuracy_exposes_intermittent_non_final_error(self):
        metrics = benchmark.trial_accuracy_metrics(
            "open settings now",
            ["open settings now", "open sittings now", "open settings now"],
        )

        self.assertEqual(metrics["trials"], 3)
        self.assertEqual(metrics["exact_match_trials"], 2)
        self.assertEqual(metrics["all_word_errors"], [0, 1, 0])
        self.assertEqual(metrics["best_wer"], 0)
        self.assertEqual(metrics["median_wer"], 0)
        self.assertEqual(metrics["worst_wer"], 1 / 3)
        self.assertIsNone(benchmark.trial_accuracy_metrics("...", ["words"]))

    def test_trial_accuracy_preserves_insertions_and_empty_output(self):
        metrics = benchmark.trial_accuracy_metrics("one", ["one two three", ""])
        self.assertEqual(metrics["all_word_errors"], [2, 1])
        self.assertEqual(metrics["all_wer"], [2.0, 1.0])
        self.assertEqual(metrics["median_wer"], 1.5)
        self.assertEqual(metrics["exact_match_trials"], 0)
        self.assertIsNone(benchmark.trial_accuracy_metrics("one", []))

    def test_invalid_run_counts_are_rejected_before_model_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            for invalid in (0, -1, True, False, 1.5, "3", None):
                with self.subTest(manifest_runs=invalid):
                    with open(path, "w", encoding="utf-8") as handle:
                        json.dump({"runs": invalid}, handle)
                    with mock.patch.object(benchmark.engine, "Transcriber") as constructor:
                        with self.assertRaisesRegex(ValueError, "positive integer"):
                            benchmark.run_benchmark(path)
                        constructor.assert_not_called()
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"runs": 3}, handle)
            with mock.patch.object(benchmark.engine, "Transcriber") as constructor:
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    benchmark.run_benchmark(path, runs=0)
                constructor.assert_not_called()

    def test_unknown_model_is_rejected_before_model_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"model": "not-a-pinned-model"}, handle)
            with mock.patch.object(benchmark.engine, "Transcriber") as constructor:
                with self.assertRaisesRegex(ValueError, "unsupported speech model"):
                    benchmark.run_benchmark(path)
                constructor.assert_not_called()

    def test_invalid_language_is_rejected_before_model_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            for invalid in (None, True, "", "EN", "english", "../pl"):
                with self.subTest(language=invalid):
                    with open(path, "w", encoding="utf-8") as handle:
                        json.dump({"language": invalid}, handle)
                    with mock.patch.object(
                            benchmark.engine, "Transcriber") as constructor:
                        with self.assertRaisesRegex(ValueError, "language"):
                            benchmark.run_benchmark(path)
                        constructor.assert_not_called()

    def test_auto_language_reaches_backend_and_records_detected_code(self):
        manifest = {
            "model": "turbo",
            "language": "auto",
            "runs": 1,
            "samples": [{"id": "polish", "audio": "ignored.wav"}],
        }
        transcriber = mock.Mock()
        transcriber.model.dtype = "float16"

        def transcribe(*_args, **_kwargs):
            transcriber.last_timing = {
                "backend": "whisper",
                "speech_seconds": 0.8,
                "detected_language": "pl",
            }
            return "Dzie\u0144 dobry"

        transcriber.transcribe.side_effect = transcribe
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with mock.patch.object(
                    benchmark.engine, "Transcriber", return_value=transcriber), \
                    mock.patch.object(
                        benchmark, "load_audio",
                        return_value=(mock.sentinel.audio, 1.0, 16000)):
                result = benchmark.run_benchmark(path)

        transcriber.transcribe.assert_called_once_with(
            mock.sentinel.audio, language=None)
        self.assertEqual(result["requested_language"], "auto")
        self.assertEqual(
            result["samples"][0]["detected_languages"], {"pl": 1})
        output = io.StringIO()
        with redirect_stdout(output):
            benchmark._print_summary(result)
        self.assertIn("Language: automatic detection", output.getvalue())
        self.assertIn("Detected language trials: pl 1", output.getvalue())

    def test_unscoreable_and_unreviewed_references_do_not_pollute_trial_wer(self):
        manifest = {"runs": 1, "samples": [
            {"id": "punctuation", "audio": "ignored.wav", "reference": "...",
             "reference_reviewed": True},
            {"id": "unreviewed", "audio": "ignored.wav", "reference": "private placeholder",
             "reference_reviewed": False},
            {"id": "silence", "audio": "ignored.wav", "expected_silence": True,
             "reference_reviewed": True},
            {"id": "scored", "audio": "ignored.wav", "reference": "one two",
             "reference_reviewed": True},
        ]}
        transcriber = mock.Mock()
        transcriber.model.dtype = "int8"
        transcriber.transcribe.side_effect = ["wrong", "wrong", "", "one"]
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with mock.patch.object(benchmark.engine, "Transcriber", return_value=transcriber), \
                    mock.patch.object(benchmark, "load_audio",
                                      return_value=(mock.sentinel.audio, 1.0, 16000)):
                result = benchmark.run_benchmark(path)
        self.assertEqual(result["reviewed_sample_count"], 1)
        self.assertEqual(result["reviewed_reference_word_count"], 2)
        self.assertEqual(result["reviewed_trial_reference_word_count"], 2)
        self.assertEqual(result["reviewed_trial_word_error_count"], 1)
        self.assertEqual(result["aggregate_trial_wer"], 0.5)
        self.assertEqual(result["reviewed_silence_sample_count"], 1)
        for sample in result["samples"][:3]:
            self.assertIsNone(sample["accuracy"])
            self.assertIsNone(sample["trial_accuracy"])
        output = io.StringIO()
        with redirect_stdout(output):
            benchmark._print_summary(result)
        self.assertIn("Reviewed reference contains no scoreable words", output.getvalue())
        # Reports must stay strict-JSON encodable, even with unscoreable references.
        json.dumps(result, allow_nan=False)

    def test_edit_distance_handles_insert_delete_and_replace(self):
        self.assertEqual(benchmark.edit_distance(["a", "b"], ["a", "x", "b"]), 1)
        self.assertEqual(benchmark.edit_distance(["a", "b"], ["a"]), 1)
        self.assertEqual(benchmark.edit_distance(["a"], ["b"]), 1)

    def test_final_word_metrics_normalise_case_and_punctuation(self):
        self.assertEqual(
            benchmark.final_word_metrics(
                "Keep the ending.", ["keep the ENDING!", "The ending"]),
            {
                "retained": True,
                "retained_trials": 2,
                "failed_trials": 0,
                "trials": 2,
            },
        )

    def test_final_word_metrics_expose_intermittent_loss(self):
        self.assertEqual(
            benchmark.final_word_metrics(
                "Do not lose this word", ["Do not lose this word", "Do not lose this"]),
            {
                "retained": False,
                "retained_trials": 1,
                "failed_trials": 1,
                "trials": 2,
            },
        )
        self.assertIsNone(benchmark.final_word_metrics("", [""]))

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

    def test_detected_language_metrics_reject_malformed_backend_values(self):
        self.assertEqual(
            benchmark.detected_language_metrics([
                {"detected_language": "pl"},
                {"detected_language": "en"},
                {"detected_language": "pl"},
                {"detected_language": "EN"},
                {"detected_language": "private free text"},
                mock.sentinel.timing,
            ]),
            {"pl": 2, "en": 1},
        )
        self.assertIsNone(benchmark.detected_language_metrics([{}]))

    def test_backend_stage_metrics_report_each_observed_stage(self):
        metrics = benchmark.backend_stage_metrics([
            {
                "prepare": 0.03,
                "transfer": 0.01,
                "generate": 0.20,
                "decode": 0.02,
            },
            {
                "prepare": 0.01,
                "transfer": 0.03,
                "generate": 0.10,
                "decode": 0.04,
            },
        ])

        self.assertEqual(metrics["prepare"], {
            "min": 0.01,
            "median": 0.02,
            "p95": 0.03,
            "all": [0.03, 0.01],
        })
        self.assertAlmostEqual(metrics["generate"]["median"], 0.15)
        self.assertEqual(metrics["decode"]["p95"], 0.04)

    def test_backend_stage_metrics_ignore_unavailable_timings(self):
        self.assertIsNone(benchmark.backend_stage_metrics([
            {"backend": "whisper", "speech_seconds": 1.0},
            {"prepare": float("nan"), "generate": -0.1, "decode": True},
            mock.sentinel.timing,
        ]))

    def test_parakeet_window_metrics_expose_bounded_long_form_trials(self):
        metrics = benchmark.parakeet_window_metrics([
            {"chunk_count": 3, "max_chunk_seconds": 59.5},
            {"chunk_count": 3, "max_chunk_seconds": 59.75},
            {"backend": "whisper"},
        ])

        self.assertEqual(metrics, {
            "trials": 2,
            "windowed_trials": 2,
            "min_chunk_count": 3,
            "max_chunk_count": 3,
            "max_chunk_seconds": 59.75,
            "all_chunk_counts": [3, 3],
        })
        self.assertIsNone(benchmark.parakeet_window_metrics([
            {"chunk_count": True, "max_chunk_seconds": 60},
            {"chunk_count": 1, "max_chunk_seconds": float("nan")},
        ]))

    def test_benchmark_enables_and_persists_synchronized_stages(self):
        manifest = {
            "model": "parakeet-tdt-0.6b-v3",
            "runs": 1,
            "samples": [{
                "id": "latency",
                "audio": "latency.wav",
                "reference_reviewed": False,
            }],
        }
        transcriber = mock.Mock()
        transcriber.model.dtype = "float16"

        def transcribe(*_args, **_kwargs):
            transcriber.last_timing = {
                "backend": "parakeet",
                "prepare": 0.03,
                "transfer": 0.01,
                "generate": 0.20,
                "decode": 0.02,
                "chunk_count": 2,
                "max_chunk_seconds": 59.75,
            }
            return "latency sample"

        transcriber.transcribe.side_effect = transcribe
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = os.path.join(directory, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with mock.patch.object(
                    benchmark.engine, "Transcriber",
                    return_value=transcriber) as transcriber_type, \
                    mock.patch.object(
                        benchmark, "load_audio",
                        return_value=(mock.sentinel.audio, 1.0, 16000)):
                result = benchmark.run_benchmark(manifest_path)

        transcriber_type.assert_called_once_with(measure_stages=True)
        self.assertEqual(
            result["samples"][0]["backend_stages"]["prepare"]["all"],
            [0.03],
        )
        self.assertEqual(
            result["samples"][0]["backend_stages"]["generate"]["median"],
            0.20,
        )
        self.assertEqual(result["samples"][0]["parakeet_windowing"], {
            "trials": 1,
            "windowed_trials": 1,
            "min_chunk_count": 2,
            "max_chunk_count": 2,
            "max_chunk_seconds": 59.75,
            "all_chunk_counts": [2],
        })
        output = io.StringIO()
        with redirect_stdout(output):
            benchmark._print_summary(result)
        self.assertIn(
            "Parakeet windows: 2-2 per trial; longest input 59.750s",
            output.getvalue(),
        )
        self.assertEqual(result["benchmark_version"], 3)
        self.assertEqual(result["model_snapshot"], {
            "repository": benchmark.engine.PARAKEET_MODEL,
            "revision": benchmark.engine.PARAKEET_REVISION,
        })

    def test_benchmark_aggregates_every_accuracy_trial_conservatively(self):
        manifest = {
            "runs": 2,
            "samples": [
                {
                    "id": "short",
                    "audio": "short.wav",
                    "reference": "alpha beta",
                    "reference_reviewed": True,
                },
                {
                    "id": "longer",
                    "audio": "longer.wav",
                    "reference": "one two three four",
                    "reference_reviewed": True,
                },
            ],
        }
        transcriber = mock.Mock()
        transcriber.model.dtype = "int8"
        transcriber.transcribe.side_effect = [
            "alpha beta",
            "gamma beta",
            "one too three four",
            "one too free four",
        ]

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

        self.assertAlmostEqual(result["aggregate_wer"], 1 / 6)
        self.assertAlmostEqual(result["aggregate_trial_wer"], 4 / 12)
        self.assertEqual(result["reviewed_reference_word_count"], 6)
        self.assertEqual(result["reviewed_trial_reference_word_count"], 12)
        self.assertEqual(result["reviewed_trial_word_error_count"], 4)
        self.assertAlmostEqual(result["aggregate_best_trial_wer"], 1 / 6)
        self.assertAlmostEqual(result["aggregate_worst_trial_wer"], 3 / 6)
        self.assertEqual(
            result["samples"][0]["trial_accuracy"]["all_word_errors"],
            [0, 1],
        )

        output = io.StringIO()
        with redirect_stdout(output):
            benchmark._print_summary(result)
        self.assertIn(
            "Snapshot: nvidia/parakeet-tdt-0.6b-v3@", output.getvalue())
        self.assertIn(
            "16.67% consensus | 33.33% all trials | "
            "16.67/50.00% best/worst trial envelope",
            output.getvalue(),
        )

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

        self.assertIsNone(result["aggregate_wer"])
        self.assertIsNone(result["aggregate_trial_wer"])
        self.assertIsNone(result["aggregate_best_trial_wer"])
        self.assertIsNone(result["aggregate_worst_trial_wer"])
        self.assertEqual(result["reviewed_trial_reference_word_count"], 0)
        self.assertEqual(result["reviewed_silence_sample_count"], 2)
        self.assertEqual(result["silence_false_positive_count"], 1)
        self.assertEqual(result["reviewed_silence_trial_count"], 2)
        self.assertEqual(result["silence_false_positive_trial_count"], 1)
        self.assertFalse(result["samples"][0]["silence"]["false_positive"])
        self.assertTrue(result["samples"][1]["silence"]["false_positive"])

    def test_benchmark_reports_reviewed_speech_vad_rejections(self):
        manifest = {
            "model": "base.en",
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
        self.assertEqual(
            result["whisper_vad_policy"], benchmark.engine.WHISPER_VAD_POLICY)
        self.assertEqual(detection["all_seconds"], [1.25, 0.0])
        self.assertEqual(result["reviewed_speech_vad_rejection_count"], 1)
        self.assertEqual(
            result["reviewed_speech_vad_rejection_trial_count"], 1)
        self.assertEqual(result["reviewed_final_word_sample_count"], 1)
        self.assertEqual(result["final_word_failure_count"], 1)
        self.assertEqual(result["reviewed_final_word_trial_count"], 2)
        self.assertEqual(result["final_word_failure_trial_count"], 1)
        self.assertEqual(result["samples"][0]["final_word"], {
            "retained": False,
            "retained_trials": 1,
            "failed_trials": 1,
            "trials": 2,
        })


if __name__ == "__main__":
    unittest.main()
