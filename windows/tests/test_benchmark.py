import hashlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
import unicodedata
from contextlib import redirect_stdout
from unittest import mock

import numpy as np

import benchmark


def fixture_audio_by_path(audio, seconds=1.0, source_rate=16000):
    """Mock distinct fixture digests while tests exercise unrelated metrics."""

    def load(path):
        digest = hashlib.sha256(os.fsencode(path)).hexdigest()
        return audio, seconds, source_rate, digest

    return load


class CudaTimingTests(unittest.TestCase):
    def test_environment_report_omits_raw_torch_exception_details(self):
        torch = types.ModuleType("torch")

        def private_error(_name):
            raise RuntimeError("private-installation-path-marker")

        torch.__getattr__ = private_error
        with mock.patch.dict(sys.modules, {"torch": torch}):
            environment = benchmark._environment()

        self.assertEqual(environment["torch_error"], "RuntimeError")
        self.assertNotIn("private-installation-path-marker", str(environment))

    def test_cpu_only_runtime_needs_no_cuda_barrier(self):
        with mock.patch.dict(sys.modules, {"torch": None}):
            benchmark._sync_cuda()

        torch = types.ModuleType("torch")
        torch.cuda = mock.Mock()
        torch.cuda.is_available.return_value = False
        with mock.patch.dict(sys.modules, {"torch": torch}):
            benchmark._sync_cuda()
        torch.cuda.synchronize.assert_not_called()

    def test_cuda_barrier_runs_when_available(self):
        torch = types.ModuleType("torch")
        torch.cuda = mock.Mock()
        torch.cuda.is_available.return_value = True
        with mock.patch.dict(sys.modules, {"torch": torch}):
            benchmark._sync_cuda()
        torch.cuda.synchronize.assert_called_once_with()

    def test_failed_cuda_barrier_invalidates_latency_run(self):
        torch = types.ModuleType("torch")
        torch.cuda = mock.Mock()
        torch.cuda.is_available.return_value = True
        torch.cuda.synchronize.side_effect = RuntimeError("CUDA synchronization failed")
        with mock.patch.dict(sys.modules, {"torch": torch}):
            with self.assertRaisesRegex(RuntimeError, "CUDA synchronization failed"):
                benchmark._sync_cuda()


class MetricTests(unittest.TestCase):
    def test_task_group_metrics_keep_accuracy_and_latency_stratified(self):
        samples = [
            {
                "task_group": "spontaneous-dictation",
                "accuracy": {"reference_words": 4, "word_errors": 1},
                "trial_accuracy": {
                    "trials": 2, "all_word_errors": [1, 0],
                    "worst_word_errors": 1,
                },
                "inference_seconds": {"all": [0.2, 0.4]},
                "silence": None,
                "first_word": {"retained": False, "failed_trials": 1,
                               "trials": 2},
                "final_word": {"retained": True, "failed_trials": 0,
                               "trials": 2},
            },
            {
                "task_group": "spontaneous-dictation",
                "accuracy": None,
                "inference_seconds": {"all": [0.6]},
                "silence": {"evaluated": True,
                            "trials": 2,
                            "false_positive_trials": 1},
                "first_word": None,
                "final_word": None,
            },
            {
                "task_group": "spontaneous-dictation",
                "accuracy": {"reference_words": 2, "word_errors": 2},
                "trial_accuracy": {
                    "trials": 2, "all_word_errors": [2, 1],
                    "worst_word_errors": 2,
                },
                "inference_seconds": {"all": [0.8]},
                "silence": None,
                "first_word": {"retained": True, "failed_trials": 0,
                               "trials": 2},
                "final_word": {"retained": False, "failed_trials": 1,
                               "trials": 2},
            },
            {"task_group": "read-speech", "accuracy": None,
             "inference_seconds": {"all": [0.1]}, "silence": None,
             "first_word": None, "final_word": None},
            {"accuracy": None, "inference_seconds": {"all": [9.0]},
             "silence": None, "first_word": None, "final_word": None},
        ]

        result = benchmark.task_group_metrics(samples)

        self.assertEqual(set(result), {"read-speech", "spontaneous-dictation"})
        spontaneous = result["spontaneous-dictation"]
        self.assertEqual(spontaneous["sample_count"], 3)
        self.assertEqual(spontaneous["reviewed_sample_count"], 2)
        self.assertEqual(spontaneous["reviewed_reference_word_count"], 6)
        self.assertEqual(spontaneous["aggregate_wer"], 0.5)
        self.assertEqual(spontaneous["aggregate_trial_wer"], 4 / 12)
        self.assertEqual(spontaneous["aggregate_worst_trial_wer"], 3 / 6)
        self.assertEqual(spontaneous["inference_seconds"]["median"], 0.5)
        self.assertEqual(spontaneous["inference_seconds"]["p95"], 0.8)
        self.assertEqual(spontaneous["inference_seconds"]["measured_trials"], 4)
        self.assertEqual(spontaneous["reviewed_silence_trial_count"], 2)
        self.assertEqual(spontaneous["silence_false_positive_trial_count"], 1)
        self.assertEqual(spontaneous["reviewed_first_word_trial_count"], 4)
        self.assertEqual(spontaneous["first_word_failure_count"], 1)
        self.assertEqual(spontaneous["first_word_failure_trial_count"], 1)
        self.assertEqual(spontaneous["reviewed_final_word_trial_count"], 4)
        self.assertEqual(spontaneous["final_word_failure_trial_count"], 1)

    def test_language_group_metrics_are_independent_of_task_groups(self):
        samples = [
            {
                "language_group": "pl",
                "task_group": "read-speech",
                "accuracy": {"reference_words": 2, "word_errors": 1},
                "trial_accuracy": {
                    "trials": 2, "all_word_errors": [1, 0],
                    "worst_word_errors": 1,
                },
                "inference_seconds": {"all": [0.2, 0.4]},
                "silence": None,
                "first_word": None,
                "final_word": None,
            },
            {
                "language_group": "pl",
                "task_group": "spontaneous-dictation",
                "accuracy": {"reference_words": 3, "word_errors": 1},
                "trial_accuracy": {
                    "trials": 1, "all_word_errors": [1],
                    "worst_word_errors": 1,
                },
                "inference_seconds": {"all": [0.5]},
                "silence": None,
                "first_word": None,
                "final_word": None,
            },
            {
                "language_group": "en-GB",
                "task_group": "read-speech",
                "accuracy": None,
                "inference_seconds": {"all": [0.7]},
                "silence": None,
                "first_word": None,
                "final_word": None,
            },
        ]

        result = benchmark.language_group_metrics(samples)

        self.assertEqual(set(result), {"en-GB", "pl"})
        polish = result["pl"]
        self.assertEqual(polish["sample_count"], 2)
        self.assertEqual(polish["reviewed_sample_count"], 2)
        self.assertEqual(polish["aggregate_wer"], 2 / 5)
        self.assertEqual(polish["aggregate_trial_wer"], 2 / 7)
        self.assertEqual(polish["aggregate_worst_trial_wer"], 2 / 5)
        self.assertEqual(polish["inference_seconds"]["median"], 0.4)

    def test_language_task_metrics_require_and_preserve_both_labels(self):
        samples = [
            {
                "language_group": " pl ",
                "task_group": "spontaneous-dictation",
                "accuracy": {"reference_words": 4, "word_errors": 1},
                "trial_accuracy": {
                    "trials": 2, "all_word_errors": [1, 0],
                    "worst_word_errors": 1,
                },
                "inference_seconds": {"all": [0.2, 0.4]},
                "silence": None,
                "first_word": {"retained": False, "failed_trials": 1,
                               "trials": 2},
                "final_word": {"retained": True, "failed_trials": 0,
                               "trials": 2},
            },
            {
                "language_group": "pl",
                "task_group": "read-speech",
                "accuracy": {"reference_words": 2, "word_errors": 0},
                "trial_accuracy": {
                    "trials": 1, "all_word_errors": [0],
                    "worst_word_errors": 0,
                },
                "inference_seconds": {"all": [0.1]},
                "silence": None,
                "first_word": None,
                "final_word": None,
            },
            {
                "language_group": "en-GB",
                "task_group": "spontaneous-dictation",
                "accuracy": None,
                "inference_seconds": {"all": [0.7]},
                "silence": None,
                "first_word": None,
                "final_word": None,
            },
            {"language_group": "pl", "accuracy": None},
            {"task_group": "read-speech", "accuracy": None},
            {"language_group": "pl", "task_group": "  ", "accuracy": None},
        ]

        result = benchmark.language_task_group_metrics(samples)

        self.assertEqual(set(result), {"pl", "en-GB"})
        self.assertEqual(set(result["pl"]), {
            "read-speech", "spontaneous-dictation",
        })
        spontaneous = result["pl"]["spontaneous-dictation"]
        self.assertEqual(spontaneous["sample_count"], 1)
        self.assertEqual(spontaneous["reviewed_reference_word_count"], 4)
        self.assertEqual(spontaneous["aggregate_wer"], 0.25)
        self.assertEqual(spontaneous["aggregate_trial_wer"], 1 / 8)
        self.assertAlmostEqual(spontaneous["inference_seconds"]["median"], 0.3)
        self.assertEqual(spontaneous["first_word_failure_trial_count"], 1)
        self.assertEqual(
            result["en-GB"]["spontaneous-dictation"]["reviewed_sample_count"],
            0,
        )

    def test_reviewed_speech_vad_metrics_keep_missing_trials_and_trial_ratios(self):
        reviewed = [
            {
                "silence": None, "audio_seconds": 2.0,
                "speech_detection": {
                    "trials": 3, "measured_trials": 2, "missing_trials": 1,
                    "rejected_trials": 1, "all_seconds": [1.0, 0.0],
                },
            },
            {
                "silence": None, "audio_seconds": 4.0,
                "speech_detection": {
                    "trials": 2, "measured_trials": 2, "missing_trials": 0,
                    "rejected_trials": 0, "all_seconds": [3.0, 2.0],
                },
            },
            {
                "silence": {"evaluated": True}, "audio_seconds": 1.0,
                "speech_detection": {
                    "trials": 1, "measured_trials": 1, "missing_trials": 0,
                    "rejected_trials": 1, "all_seconds": [0.0],
                },
            },
        ]

        metrics = benchmark._reviewed_speech_vad_metrics(reviewed)

        self.assertEqual(metrics["reviewed_speech_vad_sample_count"], 2)
        self.assertEqual(metrics["reviewed_speech_vad_trial_count"], 5)
        self.assertEqual(metrics["reviewed_speech_vad_measured_trial_count"], 4)
        self.assertEqual(metrics["reviewed_speech_vad_missing_trial_count"], 1)
        self.assertFalse(metrics["reviewed_speech_vad_complete"])
        self.assertEqual(metrics["reviewed_speech_vad_rejection_count"], 1)
        self.assertEqual(metrics["reviewed_speech_vad_rejection_trial_count"], 1)
        self.assertEqual(metrics["reviewed_speech_vad_retained_audio_ratio"], {
            "min": 0.0, "median": 0.5, "max": 0.75,
        })
        self.assertIsNone(benchmark._reviewed_speech_vad_metrics([])[
            "reviewed_speech_vad_retained_audio_ratio"]["median"])
        self.assertIsNone(benchmark._reviewed_speech_vad_metrics([])[
            "reviewed_speech_vad_complete"])

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

    def test_canonically_equivalent_polish_text_has_no_scoring_errors(self):
        reference = "Zażółć gęślą jaźń"
        decomposed = unicodedata.normalize("NFD", reference)
        self.assertNotEqual(reference, decomposed)

        for source, output in ((reference, decomposed), (decomposed, reference)):
            with self.subTest(reference_is_decomposed=source == decomposed):
                metrics = benchmark.accuracy_metrics(source, output)
                self.assertEqual(metrics["reference_words"], 3)
                self.assertEqual(metrics["word_errors"], 0)
                self.assertEqual(metrics["character_errors"], 0)
                self.assertEqual(metrics["case_sensitive_character_errors"], 0)
                self.assertTrue(metrics["exact_match"])
                self.assertTrue(
                    benchmark.first_word_metrics(source, [output])["retained"])
                self.assertTrue(
                    benchmark.final_word_metrics(source, [output])["retained"])
                self.assertEqual(
                    benchmark.trial_accuracy_metrics(source, [output])[
                        "all_word_errors"],
                    [0],
                )

    def test_canonical_scoring_still_counts_real_accent_and_case_changes(self):
        reference = "Émile"
        unaccented = "Emile"
        lowercased = "émile"

        self.assertEqual(
            benchmark.accuracy_metrics(reference, unaccented)["wer"], 1.0)
        self.assertEqual(
            benchmark.accuracy_metrics(reference, lowercased)["wer"], 0.0)
        self.assertGreater(
            benchmark.accuracy_metrics(reference, lowercased)["case_sensitive_cer"],
            0,
        )
        # Some combining marks have no precomposed NFC form. They remain part
        # of a word and must not disappear from WER, unlike punctuation.
        self.assertEqual(
            benchmark._normalise_words("q\u0307 next"), ["q\u0307", "next"])
        self.assertEqual(benchmark.accuracy_metrics("q\u0307", "q")["wer"], 1.0)
        # NFC is deliberately narrower than NFKC compatibility folding.
        self.assertEqual(benchmark.accuracy_metrics("①", "1")["wer"], 1.0)

    def test_case_sensitive_cer_exposes_capitalization_hidden_by_cer(self):
        metrics = benchmark.accuracy_metrics("Hello, World!", "hello, world!")
        self.assertEqual(metrics["cer"], 0)
        self.assertEqual(metrics["case_sensitive_character_errors"], 2)
        self.assertEqual(metrics["case_sensitive_cer"], 2 / len("Hello, World!"))

    def test_trial_accuracy_reports_case_sensitive_cer_variation(self):
        metrics = benchmark.trial_accuracy_metrics(
            "Hello world", ["Hello world", "hello world"])
        self.assertEqual(metrics["all_case_sensitive_cer"], [0, 1 / 11])
        self.assertEqual(metrics["best_case_sensitive_cer"], 0)
        self.assertEqual(metrics["median_case_sensitive_cer"], 1 / 22)
        self.assertEqual(metrics["worst_case_sensitive_cer"], 1 / 11)

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

    def test_paired_tail_silence_metrics_expose_intermittent_blank_decode(self):
        metrics = benchmark.paired_tail_silence_metrics(
            "alpha beta", ["alpha beta", "alpha beta", ""],
            ["", "alpha beta gamma", ""])

        self.assertEqual(metrics["trial_count"], 3)
        self.assertEqual(metrics["baseline_empty_trial_count"], 1)
        self.assertEqual(metrics["tailed_empty_trial_count"], 2)
        self.assertEqual(metrics["nonempty_to_empty_trial_count"], 1)
        self.assertEqual(metrics["final_word_lost_trial_count"], 2)
        self.assertEqual(metrics["final_word_recovered_trial_count"], 0)
        self.assertEqual(metrics["changed_text_trial_count"], 2)
        self.assertEqual(metrics["baseline_word_error_count"], 2)
        self.assertEqual(metrics["tailed_word_error_count"], 5)
        self.assertEqual(metrics["worsened_word_error_trial_count"], 2)
        self.assertEqual(metrics["improved_word_error_trial_count"], 0)
        self.assertEqual(metrics["tailed_first_word_failure_trial_count"], 2)
        self.assertEqual(metrics["tailed_final_word_failure_trial_count"], 3)
        self.assertEqual(
            [pair["nonempty_to_empty"] for pair in metrics["pairs"]],
            [True, False, False])
        self.assertEqual(
            [pair["final_word_lost"] for pair in metrics["pairs"]],
            [True, True, False])
        with self.assertRaisesRegex(ValueError, "matching non-empty trials"):
            benchmark.paired_tail_silence_metrics("alpha", ["alpha"], [])

    def test_paired_tail_final_word_counts_only_new_losses_and_recoveries(self):
        metrics = benchmark.paired_tail_silence_metrics(
            "open settings now",
            ["open settings now", "open settings", "open settings now"],
            ["open settings", "open settings now", "open settings now"])
        self.assertEqual(metrics["tailed_final_word_failure_trial_count"], 1)
        self.assertEqual(metrics["final_word_lost_trial_count"], 1)
        self.assertEqual(metrics["final_word_recovered_trial_count"], 1)
        self.assertEqual(
            [pair["final_word_lost"] for pair in metrics["pairs"]],
            [True, False, False])
        self.assertEqual(
            [pair["final_word_recovered"] for pair in metrics["pairs"]],
            [False, True, False])
        orders = ["baseline-first", "tailed-first", "baseline-first"]
        metrics["order_breakdown"] = benchmark.tail_probe_order_breakdown(
            metrics["pairs"], orders)
        metrics["paired_inference_delta_seconds"] = (
            benchmark.paired_tail_latency_metrics(
                [1.0] * 3, [1.0] * 3, orders))
        summary = benchmark.summarise_tail_silence_probe([
            {"tail_silence_probe": {**metrics, "trial_order": orders}},
        ])
        self.assertEqual(summary["final_word_lost_trial_count"], 1)
        self.assertEqual(summary["final_word_recovered_trial_count"], 1)
        self.assertEqual(summary["order_breakdown"]["baseline-first"][
            "final_word_lost_trial_count"], 1)
        self.assertEqual(summary["order_breakdown"]["tailed-first"][
            "final_word_lost_trial_count"], 0)

    def test_tail_probe_aggregate_counts_only_probed_samples(self):
        probe = benchmark.paired_tail_silence_metrics(
            "spoken words", ["spoken words"], [""])
        probe["order_breakdown"] = benchmark.tail_probe_order_breakdown(
            probe["pairs"], ["baseline-first"])
        probe["paired_inference_delta_seconds"] = (
            benchmark.paired_tail_latency_metrics(
                [1.0], [1.25], ["baseline-first"]))
        summary = benchmark.summarise_tail_silence_probe([
            {"tail_silence_probe": {
                **probe, "trial_order": ["baseline-first"]}},
            {"silence": {"evaluated": True}},
            {"tail_silence_probe": None},
        ])
        self.assertEqual(summary["sample_count"], 1)
        self.assertEqual(summary["nonempty_to_empty_trial_count"], 1)
        self.assertEqual(summary["final_word_lost_trial_count"], 1)
        self.assertEqual(summary["final_word_recovered_trial_count"], 0)
        self.assertEqual(summary["trial_count"], 1)
        self.assertEqual(summary["baseline_first_trial_count"], 1)
        self.assertEqual(summary["tailed_first_trial_count"], 0)
        self.assertEqual(summary["order_breakdown"]["baseline-first"], {
            "trial_count": 1, "nonempty_to_empty_trial_count": 1,
            "final_word_lost_trial_count": 1,
            "worsened_word_error_trial_count": 1})
        self.assertEqual(summary["order_breakdown"]["tailed-first"]["trial_count"], 0)
        self.assertEqual(summary["paired_inference_delta_seconds"]["all"], [0.25])
        self.assertEqual(summary["paired_inference_delta_seconds"]["by_order"][
            "baseline-first"]["median"], 0.25)
        self.assertIsNone(summary["paired_inference_delta_seconds"]["by_order"][
            "tailed-first"]["median"])

    def test_paired_tail_latency_keeps_signed_deltas_and_execution_order(self):
        metrics = benchmark.paired_tail_latency_metrics(
            [1.0, 1.25, 1.0, 1.25], [1.5, 1.0, 1.25, 1.0],
            ["baseline-first", "tailed-first", "baseline-first", "tailed-first"])
        self.assertEqual(metrics["all"], [0.5, -0.25, 0.25, -0.25])
        self.assertEqual(metrics["median"], 0.0)
        self.assertEqual(metrics["p95"], 0.5)
        self.assertEqual(metrics["by_order"]["baseline-first"], {
            "trial_count": 2, "median": 0.375, "p95": 0.5,
            "all": [0.5, 0.25]})
        self.assertEqual(metrics["by_order"]["tailed-first"], {
            "trial_count": 2, "median": -0.25, "p95": -0.25,
            "all": [-0.25, -0.25]})

    def test_tail_probe_aggregates_paired_latency_trials_not_clip_medians(self):
        def probe(clean_times, tailed_times, orders):
            clean_text = ["spoken words"] * len(orders)
            scored = benchmark.paired_tail_silence_metrics(
                "spoken words", clean_text, clean_text)
            return {
                **scored,
                "trial_order": orders,
                "order_breakdown": benchmark.tail_probe_order_breakdown(
                    scored["pairs"], orders),
                "paired_inference_delta_seconds": (
                    benchmark.paired_tail_latency_metrics(
                        clean_times, tailed_times, orders)),
            }

        summary = benchmark.summarise_tail_silence_probe([
            {"tail_silence_probe": probe(
                [1, 1, 1], [1.5, 1, 1.5],
                ["baseline-first", "tailed-first", "baseline-first"])},
            {"tail_silence_probe": probe(
                [3], [2], ["tailed-first"])},
        ])
        latency = summary["paired_inference_delta_seconds"]
        self.assertEqual(latency["all"], [0.5, 0, 0.5, -1])
        self.assertEqual(latency["median"], 0.25)
        self.assertEqual(latency["by_order"]["baseline-first"]["median"], 0.5)
        self.assertEqual(latency["by_order"]["tailed-first"]["median"], -0.5)

    def test_paired_tail_latency_rejects_invalid_or_unpaired_timings(self):
        for baseline, tailed, orders, error in (
                ([], [], [], "matching non-empty"),
                ([1.0], [], ["baseline-first"], "matching non-empty"),
                ([1.0], [2.0], [], "matching non-empty"),
                ([float("nan")], [2.0], ["baseline-first"], "finite"),
                ([1.0], [float("inf")], ["baseline-first"], "finite"),
                ([True], [2.0], ["baseline-first"], "finite"),
                ([-1.0], [2.0], ["baseline-first"], "finite"),
                ([1.0], [2.0], ["unknown"], "valid order")):
            with self.subTest(baseline=baseline, tailed=tailed, orders=orders):
                with self.assertRaisesRegex(ValueError, error):
                    benchmark.paired_tail_latency_metrics(
                        baseline, tailed, orders)

    def test_recorded_tail_latency_keeps_signed_pairs_and_both_orders(self):
        metrics = benchmark.paired_recorded_tail_latency_metrics(
            [1.0, 1.25, 1.0, 1.25], [1.5, 1.0, 1.25, 1.0],
            ["full-first", "trimmed-first", "full-first", "trimmed-first"])
        self.assertEqual(metrics["all"], [0.5, -0.25, 0.25, -0.25])
        self.assertEqual(metrics["median"], 0.0)
        self.assertEqual(metrics["by_order"]["full-first"]["median"], 0.375)
        self.assertEqual(metrics["by_order"]["trimmed-first"]["median"], -0.25)
        for full, trimmed, orders, error in (
                ([], [], [], "matching non-empty"),
                ([1.0], [], ["full-first"], "matching non-empty"),
                ([float("nan")], [2.0], ["full-first"], "finite"),
                ([1.0], [2.0], ["baseline-first"], "valid order")):
            with self.subTest(full=full, trimmed=trimmed, orders=orders):
                with self.assertRaisesRegex(ValueError, error):
                    benchmark.paired_recorded_tail_latency_metrics(
                        full, trimmed, orders)

    def test_recorded_tail_aggregates_paired_trials_not_clip_medians(self):
        def probe(full_times, trimmed_times, orders):
            full_text = ["spoken words"] * len(orders)
            scored = benchmark.paired_recorded_tail_metrics(
                "spoken words", full_text, full_text)
            return {
                **scored,
                "trial_order": orders,
                "order_breakdown": benchmark.recorded_tail_order_breakdown(
                    scored["pairs"], orders),
                "paired_inference_delta_seconds": (
                    benchmark.paired_recorded_tail_latency_metrics(
                        full_times, trimmed_times, orders)),
            }

        summary = benchmark.summarise_recorded_tail_probe([
            {"recorded_tail_probe": probe(
                [1, 1, 1], [1.5, 1, 1.5],
                ["full-first", "trimmed-first", "full-first"])},
            {"recorded_tail_probe": probe(
                [3], [2], ["trimmed-first"])},
        ])
        latency = summary["paired_inference_delta_seconds"]
        self.assertEqual(latency["all"], [0.5, 0, 0.5, -1])
        self.assertEqual(latency["median"], 0.25)
        self.assertEqual(latency["by_order"]["full-first"]["median"], 0.5)
        self.assertEqual(latency["by_order"]["trimmed-first"]["median"], -0.5)

    def test_recorded_tail_aggregate_exposes_unsafe_crops_in_each_order(self):
        def probe(full_text, trimmed_text, orders):
            scored = benchmark.paired_recorded_tail_metrics(
                "hello world", full_text, trimmed_text)
            return {
                **scored,
                "trial_order": orders,
                "order_breakdown": benchmark.recorded_tail_order_breakdown(
                    scored["pairs"], orders),
                "paired_inference_delta_seconds": (
                    benchmark.paired_recorded_tail_latency_metrics(
                        [1.0] * len(orders), [0.9] * len(orders), orders)),
            }

        summary = benchmark.summarise_recorded_tail_probe([
            {"recorded_tail_probe": probe(
                ["", "hello world"], ["hello world", ""],
                ["full-first", "trimmed-first"])},
            {"recorded_tail_probe": probe(
                ["hello world"], ["hello there"], ["full-first"])},
        ])
        self.assertEqual(summary["full_worsened_word_error_trial_count"], 1)
        self.assertEqual(summary["trimmed_worsened_word_error_trial_count"], 2)
        self.assertEqual(summary[
            "full_nonempty_to_trimmed_empty_trial_count"], 1)
        self.assertEqual(summary["order_breakdown"]["full-first"][
            "trimmed_worsened_word_error_trial_count"], 1)
        self.assertEqual(summary["order_breakdown"]["trimmed-first"][
            "trimmed_worsened_word_error_trial_count"], 1)

    def test_tail_probe_order_breakdown_rejects_misaligned_orders(self):
        pairs = benchmark.paired_tail_silence_metrics(
            "spoken words", ["spoken words"], [""])["pairs"]
        with self.assertRaisesRegex(ValueError, "valid order"):
            benchmark.tail_probe_order_breakdown(pairs, [])
        with self.assertRaisesRegex(ValueError, "valid order"):
            benchmark.tail_probe_order_breakdown(pairs, ["unknown"])

    def test_recorded_tail_metrics_show_both_tail_harm_and_unsafe_trim(self):
        metrics = benchmark.paired_recorded_tail_metrics(
            "hello world", ["", "hello world", "hello there"],
            ["hello world", "", "hello world"])
        self.assertEqual(metrics["trimmed_nonempty_to_full_empty_trial_count"], 1)
        self.assertEqual(metrics["full_nonempty_to_trimmed_empty_trial_count"], 1)
        self.assertEqual(metrics["full_word_error_count"], 3)
        self.assertEqual(metrics["trimmed_word_error_count"], 2)
        self.assertEqual(metrics["full_worsened_word_error_trial_count"], 2)
        self.assertEqual(metrics["trimmed_worsened_word_error_trial_count"], 1)
        self.assertEqual(benchmark.recorded_tail_order_breakdown(
            metrics["pairs"], ["full-first", "trimmed-first", "full-first"]), {
                "full-first": {
                    "trial_count": 2,
                    "trimmed_nonempty_to_full_empty_trial_count": 1,
                    "full_nonempty_to_trimmed_empty_trial_count": 0,
                    "full_worsened_word_error_trial_count": 2,
                    "trimmed_worsened_word_error_trial_count": 0,
                },
                "trimmed-first": {
                    "trial_count": 1,
                    "trimmed_nonempty_to_full_empty_trial_count": 0,
                    "full_nonempty_to_trimmed_empty_trial_count": 1,
                    "full_worsened_word_error_trial_count": 0,
                    "trimmed_worsened_word_error_trial_count": 1,
                },
            })
        with self.assertRaisesRegex(ValueError, "valid order"):
            benchmark.recorded_tail_order_breakdown(metrics["pairs"], [])

    def test_tail_probe_requires_one_unchanged_feature_bucket(self):
        rate = benchmark.engine.PARAKEET_SAMPLE_RATE
        # Exact boundaries are valid; one additional effective sample would
        # make the tailed member use a different shape or long-form path.
        for bucket in benchmark.engine.PARAKEET_BUCKET_SECONDS:
            with self.subTest(bucket=bucket):
                clean_count = bucket * rate - 400 * rate // 1000
                self.assertIsNone(
                    benchmark._validate_tail_probe_bucket(clean_count, 400))
                error = ("Parakeet window" if bucket == 60
                         else "same Parakeet feature bucket")
                with self.assertRaisesRegex(ValueError, error):
                    benchmark._validate_tail_probe_bucket(clean_count + 1, 400)

    def test_recorded_tail_probe_requires_bounded_same_bucket_crop(self):
        rate = benchmark.engine.PARAKEET_SAMPLE_RATE
        self.assertEqual(
            benchmark._validate_recorded_tail_probe_bucket(rate, 600),
            600 * rate // 1000)
        for count, endpoint, error in (
                (rate, 1000, "1–400 ms"),
                (rate, 599, "1–400 ms"),
                (rate, 1, "1–400 ms"),
                (rate + 15, 1000, "1–400 ms"),
                (15 * rate + 1, 14900, "feature bucket"),
                (60 * rate + 1, 59999, "Parakeet window")):
            with self.subTest(count=count, endpoint=endpoint):
                with self.assertRaisesRegex(ValueError, error):
                    benchmark._validate_recorded_tail_probe_bucket(
                        count, endpoint)

    def test_recorded_tail_probe_validates_manifest_and_audio_before_loading(self):
        sample = {"id": "speech", "audio": "speech.wav",
                  "reference": "spoken words", "reference_reviewed": True,
                  "speech_end_ms": 600}
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            for endpoint, error in ((True, "positive integer"),
                                    (600.0, "positive integer"),
                                    (0, "positive integer"),
                                    (1000, "1–400 ms"),
                                    (599, "1–400 ms")):
                with self.subTest(endpoint=endpoint):
                    with open(path, "w", encoding="utf-8") as handle:
                        json.dump({"model": "parakeet-tdt-0.6b-v3",
                                   "samples": [dict(sample, speech_end_ms=endpoint)]},
                                  handle)
                    with mock.patch.object(
                            benchmark, "load_audio",
                            return_value=(np.ones(16000, dtype=np.float32),
                                          1.0, 16000, "0" * 64)), \
                            mock.patch.object(
                                benchmark.engine, "Transcriber") as constructor:
                        with self.assertRaisesRegex(ValueError, error):
                            benchmark.run_benchmark(
                                path, parakeet_recorded_tail_probe=True)
                        constructor.assert_not_called()

            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"model": "base.en", "samples": [sample]}, handle)
            with mock.patch.object(benchmark.engine, "Transcriber") as constructor:
                with self.assertRaisesRegex(ValueError, "Parakeet model"):
                    benchmark.run_benchmark(
                        path, parakeet_recorded_tail_probe=True)
                constructor.assert_not_called()

            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"model": "parakeet-tdt-0.6b-v3",
                           "samples": [sample]}, handle)
            with mock.patch.object(benchmark.engine, "Transcriber") as constructor:
                with self.assertRaisesRegex(ValueError, "only one"):
                    benchmark.run_benchmark(
                        path, parakeet_recorded_tail_probe=True,
                        parakeet_tail_silence_ms=400)
                constructor.assert_not_called()
            with mock.patch.object(benchmark.engine, "Transcriber") as constructor:
                with self.assertRaisesRegex(ValueError, "endpoint-cropped"):
                    benchmark.run_benchmark(
                        path, parakeet_tail_silence_ms=400)
                constructor.assert_not_called()

            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"model": "parakeet-tdt-0.6b-v3",
                           "samples": [dict(sample, speech_end_ms=14900)]},
                          handle)
            with mock.patch.object(
                    benchmark, "load_audio", return_value=(
                        np.ones(15 * 16000 + 1, dtype=np.float32),
                        15.0, 16000, "0" * 64)), \
                    mock.patch.object(
                        benchmark.engine, "Transcriber") as constructor:
                with self.assertRaisesRegex(ValueError, "feature bucket"):
                    benchmark.run_benchmark(
                        path, parakeet_recorded_tail_probe=True)
                constructor.assert_not_called()

            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"model": "parakeet-tdt-0.6b-v3",
                           "samples": [dict(sample, reference_reviewed=False)]},
                          handle)
            with mock.patch.object(benchmark.engine, "Transcriber") as constructor:
                with self.assertRaisesRegex(ValueError, "reviewed, scoreable"):
                    benchmark.run_benchmark(
                        path, parakeet_recorded_tail_probe=True)
                constructor.assert_not_called()

    def test_recorded_tail_probe_keeps_full_capture_as_baseline(self):
        manifest = {"model": "parakeet-tdt-0.6b-v3", "runs": 2, "samples": [
            {"id": "speech", "audio": "speech.wav", "reference": "hello world",
             "reference_reviewed": True, "speech_end_ms": 600},
            {"id": "silence", "audio": "silence.wav",
             "expected_silence": True, "reference_reviewed": True},
        ]}
        transcriber = mock.Mock()
        transcriber.model.dtype = "float16"
        # Full, trimmed, trimmed, full, then two ordinary silence controls.
        transcriber.transcribe.side_effect = [
            "", "hello world", "hello world", "hello there", "", ""]
        audio = np.ones(16000, dtype=np.float32)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with mock.patch.object(
                    benchmark.engine, "Transcriber", return_value=transcriber), \
                    mock.patch.object(
                        benchmark, "load_audio",
                        side_effect=fixture_audio_by_path(audio)):
                result = benchmark.run_benchmark(
                    path, parakeet_recorded_tail_probe=True)

        calls = list(transcriber.transcribe.call_args_list)
        self.assertEqual([len(call.args[0]) for call in calls],
                         [16000, 9600, 9600, 16000, 16000, 16000])
        self.assertIs(calls[0].args[0], audio)
        self.assertIs(calls[3].args[0], audio)
        self.assertEqual(result["benchmark_version"], 17)
        self.assertEqual(result["aggregate_trial_wer"], 0.75)
        self.assertEqual(result["samples"][0]["transcript"], "")
        probe = result["samples"][0]["recorded_tail_probe"]
        self.assertEqual(probe["trial_order"], ["full-first", "trimmed-first"])
        self.assertEqual(probe["trim_at_sample"], 9600)
        self.assertEqual(probe["removed_tail_ms"], 400.0)
        self.assertEqual(probe["trimmed_nonempty_to_full_empty_trial_count"], 1)
        self.assertEqual(result["recorded_tail_probe"]["full_first_trial_count"], 1)
        self.assertEqual(result["recorded_tail_probe"]["trimmed_first_trial_count"], 1)
        self.assertEqual(result["recorded_tail_probe"]["order_breakdown"][
            "trimmed-first"]["trimmed_worsened_word_error_trial_count"], 0)
        sample_latency = probe["paired_inference_delta_seconds"]
        full_times = result["samples"][0]["inference_seconds"]["all"]
        trimmed_times = probe["trimmed_inference_seconds"]["all"]
        for observed, full, trimmed in zip(
                sample_latency["all"], full_times, trimmed_times):
            self.assertAlmostEqual(observed, trimmed - full)
        self.assertEqual(result["recorded_tail_probe"][
            "paired_inference_delta_seconds"]["trial_count"], 2)
        self.assertRegex(result["recorded_tail_probe_inputs_sha256"],
                         r"^[0-9a-f]{64}$")
        self.assertNotIn("recorded_tail_probe", result["samples"][1])
        output = io.StringIO()
        with redirect_stdout(output):
            benchmark._print_summary(result)
        self.assertIn("Parakeet recorded tail (benchmark-only)",
                      output.getvalue())
        self.assertIn("Paired inference trimmed-minus-full median",
                      output.getvalue())
        self.assertIn("trimmed worsened", output.getvalue())
        json.dumps(result, allow_nan=False)

    def test_recorded_tail_probe_balances_odd_runs_across_clips(self):
        manifest = {"model": "parakeet-tdt-0.6b-v3", "runs": 3, "samples": [
            {"id": name, "audio": name + ".wav", "reference": "spoken words",
             "reference_reviewed": True, "speech_end_ms": 600}
            for name in ("first", "second")
        ]}
        transcriber = mock.Mock()
        transcriber.model.dtype = "float16"
        transcriber.transcribe.return_value = "spoken words"
        audio = np.ones(16000, dtype=np.float32)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with mock.patch.object(
                    benchmark.engine, "Transcriber", return_value=transcriber), \
                    mock.patch.object(
                        benchmark, "load_audio",
                        side_effect=fixture_audio_by_path(audio)):
                result = benchmark.run_benchmark(
                    path, parakeet_recorded_tail_probe=True)

        self.assertEqual([
            sample["recorded_tail_probe"]["trial_order"]
            for sample in result["samples"]], [
                ["full-first", "trimmed-first", "full-first"],
                ["trimmed-first", "full-first", "trimmed-first"],
            ])
        self.assertEqual(result["recorded_tail_probe"]["full_first_trial_count"], 3)
        self.assertEqual(result["recorded_tail_probe"]["trimmed_first_trial_count"], 3)
        self.assertEqual([len(call.args[0]) for call in
                          transcriber.transcribe.call_args_list], [
                              16000, 9600, 9600, 16000, 16000, 9600,
                              9600, 16000, 16000, 9600, 9600, 16000,
                          ])

    def test_tail_probe_rejects_cross_bucket_audio_before_model_loading(self):
        sample = {"id": "speech", "audio": "speech.wav",
                  "reference": "spoken words", "reference_reviewed": True}
        # The source duration sits on the nominal boundary, but the effective
        # ASR array has one extra sample. Validate the samples, not metadata.
        rate = benchmark.engine.PARAKEET_SAMPLE_RATE
        audio = np.zeros(15 * rate - 400 * rate // 1000 + 1,
                         dtype=np.float32)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"model": "parakeet-tdt-0.6b-v3",
                           "samples": [sample]}, handle)
            with mock.patch.object(
                    benchmark, "load_audio",
                    return_value=(audio, 14.6, 48000, "0" * 64)), \
                    mock.patch.object(
                        benchmark.engine, "Transcriber") as constructor:
                with self.assertRaisesRegex(ValueError, "feature bucket"):
                    benchmark.run_benchmark(
                        path, parakeet_tail_silence_ms=400)
                constructor.assert_not_called()

    def test_tail_probe_bucket_check_skips_unscored_controls(self):
        audio = np.zeros(16 * 16000, dtype=np.float32)
        samples = [
            {"id": "unreviewed", "audio": "unreviewed.wav",
             "reference": "spoken words"},
            {"id": "silence", "audio": "silence.wav",
             "expected_silence": True, "reference_reviewed": True},
        ]
        with mock.patch.object(
                benchmark, "load_audio",
                side_effect=fixture_audio_by_path(audio, 16.0)):
            checked = benchmark._preflight_audio(
                ".", samples, parakeet_tail_silence_ms=400)
        self.assertEqual(len(checked), 2)

    def test_tail_probe_rejects_invalid_settings_before_model_loading(self):
        sample = {"id": "speech", "audio": "ignored.wav",
                  "reference": "spoken words", "reference_reviewed": True}
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            for model, samples, duration, error in (
                    ("parakeet-tdt-0.6b-v3", [sample], 0, "integer"),
                    ("parakeet-tdt-0.6b-v3", [sample], 401, "integer"),
                    ("parakeet-tdt-0.6b-v3", [sample], True, "integer"),
                    ("parakeet-tdt-0.6b-v3", [sample], 1.5, "integer"),
                    ("base.en", [sample], 400, "Parakeet"),
                    ("parakeet-tdt-0.6b-v3", [
                        {"id": "silence", "audio": "ignored.wav",
                         "expected_silence": True, "reference_reviewed": True}],
                     400, "reviewed speech"),
            ):
                with self.subTest(model=model, duration=duration, samples=samples):
                    with open(path, "w", encoding="utf-8") as handle:
                        json.dump({"model": model, "samples": samples}, handle)
                    with mock.patch.object(
                            benchmark.engine, "Transcriber") as constructor:
                        with self.assertRaisesRegex(ValueError, error):
                            benchmark.run_benchmark(
                                path, parakeet_tail_silence_ms=duration)
                        constructor.assert_not_called()

    def test_tail_probe_pairs_clean_and_tailed_parakeet_without_changing_wer(self):
        manifest = {"model": "parakeet-tdt-0.6b-v3", "runs": 2, "samples": [
            {"id": "short", "audio": "short.wav", "reference": "hello world",
             "reference_reviewed": True, "task_group": "short-command"},
            {"id": "silence", "audio": "silence.wav",
             "expected_silence": True, "reference_reviewed": True},
        ]}
        transcriber = mock.Mock()
        transcriber.model.dtype = "float16"
        transcriber.transcribe.side_effect = [
            "hello world", "", "hello there", "hello world", "", ""]
        audio = np.ones(16000, dtype=np.float32)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with mock.patch.object(
                    benchmark.engine, "Transcriber", return_value=transcriber), \
                    mock.patch.object(
                        benchmark, "load_audio",
                        side_effect=fixture_audio_by_path(audio)):
                result = benchmark.run_benchmark(
                    path, parakeet_tail_silence_ms=400)
                calls = list(transcriber.transcribe.call_args_list)
                transcriber.transcribe.reset_mock()
                transcriber.transcribe.side_effect = [
                    "hello world", "hello world", "", ""]
                plain_result = benchmark.run_benchmark(path)

        self.assertEqual(len(calls), 6)
        self.assertIs(calls[0].args[0], audio)
        self.assertIs(calls[3].args[0], audio)
        self.assertEqual(len(calls[1].args[0]), 22400)
        self.assertEqual(len(calls[2].args[0]), 22400)
        np.testing.assert_array_equal(calls[1].args[0][:16000], audio)
        np.testing.assert_array_equal(
            calls[1].args[0][16000:], np.zeros(6400, dtype=np.float32))
        self.assertEqual(result["aggregate_trial_wer"], 0)
        self.assertEqual(result["benchmark_inputs_sha256"],
                         plain_result["benchmark_inputs_sha256"])
        self.assertEqual(result["aggregate_trial_wer"],
                         plain_result["aggregate_trial_wer"])
        self.assertIsNone(plain_result["tail_silence_probe"])
        self.assertEqual(result["tail_silence_probe"]["sample_count"], 1)
        self.assertEqual(result["tail_silence_probe"]["trial_count"], 2)
        self.assertEqual(result["benchmark_version"], 17)
        self.assertEqual(
            result["samples"][0]["tail_silence_probe"]["trial_order"],
            ["baseline-first", "tailed-first"])
        self.assertEqual(result["tail_silence_probe"]["baseline_first_trial_count"], 1)
        self.assertEqual(result["tail_silence_probe"]["tailed_first_trial_count"], 1)
        self.assertEqual(
            result["tail_silence_probe"]["order_breakdown"], {
                "baseline-first": {"trial_count": 1,
                                   "nonempty_to_empty_trial_count": 1,
                                   "final_word_lost_trial_count": 1,
                                   "worsened_word_error_trial_count": 1},
                "tailed-first": {"trial_count": 1,
                                 "nonempty_to_empty_trial_count": 0,
                                 "final_word_lost_trial_count": 1,
                                 "worsened_word_error_trial_count": 1},
            })
        self.assertEqual(
            result["tail_silence_probe"]["nonempty_to_empty_trial_count"], 1)
        self.assertEqual(
            result["tail_silence_probe"]["final_word_lost_trial_count"], 2)
        self.assertEqual(result["tail_silence_probe"]["tailed_word_error_count"], 3)
        sample_latency = result["samples"][0]["tail_silence_probe"][
            "paired_inference_delta_seconds"]
        clean_times = result["samples"][0]["inference_seconds"]["all"]
        tail_times = result["samples"][0]["tail_silence_probe"][
            "tailed_inference_seconds"]["all"]
        for observed, clean, tailed in zip(
                sample_latency["all"], clean_times, tail_times):
            self.assertAlmostEqual(observed, tailed - clean)
        self.assertEqual(result["tail_silence_probe"][
            "paired_inference_delta_seconds"]["trial_count"], 2)
        self.assertEqual(
            result["samples"][0]["tail_silence_probe"]["pairs"][1]["baseline_transcript"],
            "hello world")
        self.assertEqual(
            result["samples"][0]["tail_silence_probe"]["pairs"][1]["tailed_transcript"],
            "hello there")
        self.assertNotIn("tail_silence_probe", result["samples"][1])
        output = io.StringIO()
        with redirect_stdout(output):
            benchmark._print_summary(result)
        self.assertIn("Parakeet +400 ms silence (benchmark-only)",
                      output.getvalue())
        self.assertIn("order baseline-first 1, tailed-first 1",
                      output.getvalue())
        self.assertIn("final word lost 2, recovered 0", output.getvalue())
        # A probe containing only reviewed silence has no latency pairs.
        result["tail_silence_probe"] = benchmark.summarise_tail_silence_probe([])
        output = io.StringIO()
        with redirect_stdout(output):
            benchmark._print_summary(result)
        self.assertIn("no reviewed speech pairs", output.getvalue())
        json.dumps(result, allow_nan=False)

    def test_tail_probe_balances_odd_runs_across_clips_and_keeps_baseline_stages(self):
        manifest = {"model": "parakeet-tdt-0.6b-v3", "runs": 3, "samples": [
            {"id": "first", "audio": "first.wav", "reference": "hello world",
             "reference_reviewed": True},
            {"id": "second", "audio": "second.wav", "reference": "hello world",
             "reference_reviewed": True},
        ]}
        transcriber = mock.Mock()
        transcriber.model.dtype = "float16"

        def transcribe(input_audio, language=None):
            transcriber.last_timing = {
                "backend": "parakeet",
                "prepare": len(input_audio) / 16000,
                "chunk_count": 1,
                "max_chunk_seconds": len(input_audio) / 16000,
            }
            return "hello world"

        transcriber.transcribe.side_effect = transcribe
        audio = np.ones(16000, dtype=np.float32)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with mock.patch.object(
                    benchmark.engine, "Transcriber", return_value=transcriber), \
                    mock.patch.object(
                        benchmark, "load_audio",
                        side_effect=fixture_audio_by_path(audio)):
                result = benchmark.run_benchmark(
                    path, parakeet_tail_silence_ms=400)

        orders = [sample["tail_silence_probe"]["trial_order"]
                  for sample in result["samples"]]
        self.assertEqual(orders, [
            ["baseline-first", "tailed-first", "baseline-first"],
            ["tailed-first", "baseline-first", "tailed-first"],
        ])
        self.assertEqual(result["tail_silence_probe"]["baseline_first_trial_count"], 3)
        self.assertEqual(result["tail_silence_probe"]["tailed_first_trial_count"], 3)
        for sample in result["samples"]:
            self.assertEqual(sample["backend_stages"]["prepare"]["median"], 1.0)
            self.assertEqual(sample["parakeet_windowing"]["max_chunk_seconds"], 1.0)
        calls = list(transcriber.transcribe.call_args_list)
        self.assertEqual([len(call.args[0]) for call in calls], [
            16000, 22400, 22400, 16000, 16000, 22400,
            22400, 16000, 16000, 22400, 22400, 16000,
        ])

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

    def test_invalid_task_group_is_rejected_before_model_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"samples": [{"task_group": "  "}]}, handle)
            with mock.patch.object(
                benchmark.engine, "Transcriber") as constructor:
                with self.assertRaisesRegex(ValueError, "task_group"):
                    benchmark.run_benchmark(path)
                constructor.assert_not_called()

    def test_invalid_language_group_is_rejected_before_model_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            for invalid in ("  ", True, 3):
                with self.subTest(language_group=invalid):
                    with open(path, "w", encoding="utf-8") as handle:
                        json.dump({"samples": [{"language_group": invalid}]}, handle)
                    with mock.patch.object(
                            benchmark.engine, "Transcriber") as constructor:
                        with self.assertRaisesRegex(ValueError, "language_group"):
                            benchmark.run_benchmark(path)
                        constructor.assert_not_called()

    def test_non_object_sample_is_rejected_before_model_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"samples": [None]}, handle)
            with mock.patch.object(
                    benchmark.engine, "Transcriber") as constructor:
                with self.assertRaisesRegex(ValueError, "sample must be an object"):
                    benchmark.run_benchmark(path)
                constructor.assert_not_called()

    def test_review_and_silence_flags_require_json_booleans_before_model_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            for field in ("reference_reviewed", "expected_silence"):
                for invalid in ("false", "true", 0, 1, None, []):
                    with self.subTest(field=field, invalid=invalid):
                        sample = {"id": "sample", "audio": "ignored.wav",
                                  "reference": "spoken words", field: invalid}
                        with open(path, "w", encoding="utf-8") as handle:
                            json.dump({"samples": [sample]}, handle)
                        with mock.patch.object(
                                benchmark.engine, "Transcriber") as constructor:
                            with self.assertRaisesRegex(ValueError, field):
                                benchmark.run_benchmark(path)
                            constructor.assert_not_called()

    def test_reviewed_speech_needs_reference_and_silence_cannot_have_one(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            cases = [
                ({"reference_reviewed": True}, "reference"),
                ({"reference_reviewed": True, "reference": "   "}, "reference"),
                ({"reference": None}, "reference"),
                ({"expected_silence": True, "reference": "spoken words"},
                 "reference"),
            ]
            for fields, expected_error in cases:
                with self.subTest(fields=fields):
                    sample = {"id": "sample", "audio": "ignored.wav", **fields}
                    with open(path, "w", encoding="utf-8") as handle:
                        json.dump({"samples": [sample]}, handle)
                    with mock.patch.object(
                            benchmark.engine, "Transcriber") as constructor:
                        with self.assertRaisesRegex(ValueError, expected_error):
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

    def test_empty_corpus_is_rejected_before_model_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"samples": []}, handle)
            with mock.patch.object(benchmark.engine, "Transcriber") as constructor:
                with self.assertRaisesRegex(ValueError, "at least one audio fixture"):
                    benchmark.run_benchmark(path)
                constructor.assert_not_called()

    def test_sample_identity_and_path_are_checked_before_model_loading(self):
        cases = (
            ([{"audio": "ignored.wav"}], "sample id"),
            ([{"id": "  ", "audio": "ignored.wav"}], "sample id"),
            ([{"id": "one"}], "sample audio"),
            ([{"id": "one", "audio": "  "}], "sample audio"),
            ([{"id": "one", "audio": "a.wav"},
              {"id": "one", "audio": "b.wav"}], "unique"),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            for samples, error in cases:
                with self.subTest(samples=samples):
                    with open(path, "w", encoding="utf-8") as handle:
                        json.dump({"samples": samples}, handle)
                    with mock.patch.object(
                            benchmark.engine, "Transcriber") as constructor:
                        with self.assertRaisesRegex(ValueError, error):
                            benchmark.run_benchmark(path)
                        constructor.assert_not_called()

    def test_all_audio_is_preflighted_before_model_loading(self):
        manifest = {"samples": [
            {"id": "first", "audio": "first.wav"},
            {"id": "second", "audio": "broken.wav"},
        ]}
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with mock.patch.object(benchmark.engine, "Transcriber") as constructor, \
                    mock.patch.object(benchmark, "load_audio", side_effect=[
                        (mock.sentinel.audio, 1.0, 16000, "0" * 64),
                        ValueError("broken fixture"),
                    ]) as load_audio:
                with self.assertRaisesRegex(ValueError, "broken fixture"):
                    benchmark.run_benchmark(path)
                constructor.assert_not_called()
                self.assertEqual(load_audio.call_count, 2)

    def test_duplicate_effective_audio_aborts_before_model_loading(self):
        manifest = {"samples": [
            {"id": "private-first", "audio": "private-first.wav",
             "reference": "first", "reference_reviewed": True},
            {"id": "private-second", "audio": "private-second.wav",
             "reference": "second", "reference_reviewed": True},
        ]}
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            # The source files may have different rates or containers while
            # normalising to exactly the same samples sent to ASR.
            with mock.patch.object(benchmark.engine, "Transcriber") as constructor, \
                    mock.patch.object(benchmark, "load_audio", side_effect=[
                        (mock.sentinel.first, 1.0, 16000, "0" * 64),
                        (mock.sentinel.second, 1.0, 48000, "0" * 64),
                    ]):
                with self.assertRaisesRegex(
                        ValueError, "duplicate effective ASR audio") as caught:
                    benchmark.run_benchmark(path)
                constructor.assert_not_called()
        self.assertNotIn("private-first", str(caught.exception))
        self.assertNotIn("private-second", str(caught.exception))

    def test_preflight_accepts_distinct_effective_audio(self):
        samples = [
            {"id": "first", "audio": "first.wav"},
            {"id": "second", "audio": "second.wav"},
        ]
        with mock.patch.object(benchmark, "load_audio", side_effect=[
                (mock.sentinel.first, 1.0, 16000, "0" * 64),
                (mock.sentinel.second, 1.0, 16000, "1" * 64),
        ]):
            checked = benchmark._preflight_audio(".", samples)
        self.assertEqual(len(checked), 2)
        self.assertEqual([row[3] for row in checked], ["0" * 64, "1" * 64])

    def test_invalid_precision_is_rejected_before_model_loading(self):
        manifest = {"model": "base.en", "samples": [
            {"id": "speech", "audio": "speech.wav"}]}
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            for precision in ("invalid", "fp16"):
                with self.subTest(precision=precision):
                    with mock.patch.object(
                            benchmark.engine, "Transcriber") as constructor:
                        with self.assertRaisesRegex(
                                ValueError, "precision|Parakeet"):
                            benchmark.run_benchmark(path, precision=precision)
                        constructor.assert_not_called()

    def test_audio_changed_after_preflight_aborts_before_inference(self):
        manifest = {"samples": [{"id": "speech", "audio": "speech.wav"}]}
        transcriber = mock.Mock()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with mock.patch.object(
                    benchmark.engine, "Transcriber", return_value=transcriber), \
                    mock.patch.object(benchmark, "load_audio", side_effect=[
                        (mock.sentinel.audio, 1.0, 16000, "0" * 64),
                        (mock.sentinel.audio, 1.0, 16000, "1" * 64),
                    ]):
                with self.assertRaisesRegex(RuntimeError, "changed after preflight"):
                    benchmark.run_benchmark(path)
        transcriber.load.assert_called_once()
        transcriber.transcribe.assert_not_called()

    def test_audio_decoder_rejects_empty_and_nonfinite_fixtures(self):
        for audio in (np.zeros(0, dtype=np.float32),
                      np.array([float("nan")], dtype=np.float32),
                      np.array([float("inf")], dtype=np.float32)):
            with self.subTest(audio=audio):
                with mock.patch.object(benchmark.sf, "read", return_value=(audio, 16000)):
                    with self.assertRaisesRegex(ValueError, "positive duration|non-finite"):
                        benchmark.load_audio("ignored.wav")
        with mock.patch.object(
                benchmark.sf, "read",
                return_value=(np.array([0.1], dtype=np.float32), 0)):
            with self.assertRaisesRegex(ValueError, "positive duration and sample rate"):
                benchmark.load_audio("ignored.wav")
        with mock.patch.object(
                benchmark.sf, "read",
                return_value=(np.array([0.1, 0.2], dtype=np.float32), 48000)), \
                mock.patch.object(
                    benchmark.app, "_resample_to_16k",
                    return_value=np.array([float("nan")], dtype=np.float32)):
            with self.assertRaisesRegex(ValueError, "resampled benchmark audio"):
                benchmark.load_audio("ignored.wav")

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
                        return_value=(mock.sentinel.audio, 1.0, 16000, "0" * 64)):
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

    def test_whisper_pause_override_is_benchmark_only_and_reported(self):
        manifest = {
            "model": "base.en",
            "runs": 1,
            "samples": [{"id": "pause", "audio": "ignored.wav"}],
        }
        transcriber = mock.Mock()
        transcriber.model.dtype = "int8"
        transcriber.transcribe.return_value = "pause test"
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with mock.patch.object(
                    benchmark.engine, "Transcriber",
                    return_value=transcriber) as transcriber_type, \
                    mock.patch.object(
                        benchmark, "load_audio",
                        return_value=(mock.sentinel.audio, 1.0, 16000, "0" * 64)):
                result = benchmark.run_benchmark(
                    path, whisper_vad_min_silence_ms=2000)

        transcriber_type.assert_called_once_with(
            measure_stages=True, whisper_vad_min_silence_ms=2000)
        expected_policy = dict(benchmark.engine.WHISPER_VAD_POLICY)
        expected_policy["min_silence_duration_ms"] = 2000
        self.assertEqual(result["whisper_vad_policy"], expected_policy)
        self.assertEqual(
            result["whisper_vad_policy_origin"], "benchmark-only override")
        output = io.StringIO()
        with redirect_stdout(output):
            benchmark._print_summary(result)
        self.assertIn(
            "Whisper VAD (benchmark-only override)", output.getvalue())

    def test_invalid_whisper_pause_override_fails_before_model_load(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"model": "base.en", "samples": []}, handle)
            for invalid in (-1, True, 2.0, "2000"):
                with self.subTest(invalid=invalid):
                    with mock.patch.object(
                            benchmark.engine, "Transcriber") as constructor:
                        with self.assertRaisesRegex(ValueError, "non-negative integer"):
                            benchmark.run_benchmark(
                                path, whisper_vad_min_silence_ms=invalid)
                        constructor.assert_not_called()

    def test_whisper_pause_override_rejects_non_whisper_model_before_load(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "manifest.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"model": "parakeet-tdt-0.6b-v3", "samples": []}, handle)
            with mock.patch.object(
                    benchmark.engine, "Transcriber") as constructor:
                with self.assertRaisesRegex(ValueError, "faster-whisper"):
                    benchmark.run_benchmark(
                        path, whisper_vad_min_silence_ms=2000)
                constructor.assert_not_called()

    def test_unscoreable_and_unreviewed_references_do_not_pollute_trial_wer(self):
        manifest = {"runs": 1, "samples": [
            {"id": "punctuation", "audio": "punctuation.wav", "reference": "...",
             "reference_reviewed": True},
            {"id": "unreviewed", "audio": "unreviewed.wav", "reference": "private placeholder",
             "reference_reviewed": False},
            {"id": "silence", "audio": "silence.wav", "expected_silence": True,
             "reference_reviewed": True},
            {"id": "scored", "audio": "scored.wav", "reference": "one two",
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
                                      side_effect=fixture_audio_by_path(mock.sentinel.audio)):
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

    def test_first_word_metrics_normalise_case_and_punctuation(self):
        self.assertEqual(
            benchmark.first_word_metrics(
                "Hello, keep the ending.", ["hello! keep going", "HELLO keep"]),
            {
                "retained": True,
                "retained_trials": 2,
                "failed_trials": 0,
                "trials": 2,
            },
        )

    def test_first_word_metrics_expose_intermittent_loss(self):
        self.assertEqual(
            benchmark.first_word_metrics(
                "Open settings now", ["Open settings now", "Settings now"]),
            {
                "retained": False,
                "retained_trials": 1,
                "failed_trials": 1,
                "trials": 2,
            },
        )
        self.assertIsNone(benchmark.first_word_metrics("...", [""]))

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
        self.assertEqual(metrics["measured_trials"], 3)
        self.assertEqual(metrics["missing_trials"], 0)

    def test_speech_detection_metrics_expose_missing_whisper_timings(self):
        metrics = benchmark.speech_detection_metrics(2.0, [
            {"speech_seconds": 1.5},
            {"generate": 0.2},
            mock.sentinel.timing,
        ], expected_trials=3)

        self.assertEqual(metrics["trials"], 3)
        self.assertEqual(metrics["measured_trials"], 1)
        self.assertEqual(metrics["missing_trials"], 2)
        self.assertEqual(metrics["rejected_trials"], 0)
        self.assertEqual(metrics["all_seconds"], [1.5])

    def test_speech_detection_metrics_keep_all_missing_whisper_trials_visible(self):
        metrics = benchmark.speech_detection_metrics(
            2.0, [{"generate": 0.2}, {}], expected_trials=2)

        self.assertEqual(metrics["trials"], 2)
        self.assertEqual(metrics["measured_trials"], 0)
        self.assertEqual(metrics["missing_trials"], 2)
        self.assertIsNone(metrics["median_seconds"])

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
                        side_effect=fixture_audio_by_path(mock.sentinel.audio)):
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
        self.assertIn("not measured delivery", output.getvalue())
        self.assertEqual(result["benchmark_version"], 17)
        self.assertEqual(result["reviewed_speech_vad_sample_count"], 0)
        self.assertIsNone(
            result["reviewed_speech_vad_retained_audio_ratio"]["median"])
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
                    "language_group": "en-GB",
                    "task_group": "short-command",
                    "reference": "alpha beta",
                    "reference_reviewed": True,
                },
                {
                    "id": "longer",
                    "audio": "longer.wav",
                    "language_group": "en-GB",
                    "task_group": "spontaneous-dictation",
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
                        side_effect=fixture_audio_by_path(mock.sentinel.audio)):
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
        self.assertEqual(result["samples"][0]["first_word"], {
            "retained": False,
            "retained_trials": 1,
            "failed_trials": 1,
            "trials": 2,
        })
        self.assertEqual(result["reviewed_first_word_sample_count"], 2)
        self.assertEqual(result["first_word_failure_count"], 1)
        self.assertEqual(result["reviewed_first_word_trial_count"], 4)
        self.assertEqual(result["first_word_failure_trial_count"], 1)
        self.assertAlmostEqual(
            result["language_groups"]["en-GB"]["aggregate_worst_trial_wer"],
            3 / 6,
        )
        self.assertAlmostEqual(
            result["language_task_groups"]["en-GB"]["short-command"]
            ["aggregate_trial_wer"],
            1 / 4,
        )
        self.assertEqual(result["samples"][0]["language_group"], "en-GB")
        self.assertEqual(
            set(result["task_groups"]),
            {"short-command", "spontaneous-dictation"},
        )

        output = io.StringIO()
        with redirect_stdout(output):
            benchmark._print_summary(result)
        self.assertIn(
            "Snapshot: nvidia/parakeet-tdt-0.6b-v3@", output.getvalue())
        self.assertIn("Language group en-GB:", output.getvalue())
        self.assertIn("Language/task en-GB / short-command:", output.getvalue())
        self.assertIn(
            "WER 16.67% consensus / 33.33% all trials / "
            "50.00% worst-trial envelope",
            output.getvalue(),
        )
        self.assertIn("Task group short-command:", output.getvalue())
        self.assertIn(
            "16.67% consensus | 33.33% all trials | "
            "16.67/50.00% best/worst trial envelope",
            output.getvalue(),
        )
        self.assertIn(
            "Reviewed edge words: first not retained 1/4 trials; "
            "final not retained 0/4 trials",
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
                        side_effect=fixture_audio_by_path(mock.sentinel.audio)):
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
                "task_group": "quiet-speech",
                "language_group": "en-GB",
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
                        return_value=(mock.sentinel.audio, 2.0, 16000, "0" * 64)):
                result = benchmark.run_benchmark(manifest_path)

        detection = result["samples"][0]["speech_detection"]
        self.assertEqual(
            result["whisper_vad_policy"], benchmark.engine.WHISPER_VAD_POLICY)
        self.assertEqual(
            result["whisper_vad_policy_origin"], "Presspeech product default")
        self.assertEqual(detection["all_seconds"], [1.25, 0.0])
        self.assertEqual(detection["measured_trials"], 2)
        self.assertEqual(detection["missing_trials"], 0)
        self.assertEqual(result["reviewed_speech_vad_rejection_count"], 1)
        self.assertEqual(
            result["reviewed_speech_vad_rejection_trial_count"], 1)
        self.assertEqual(result["reviewed_speech_vad_trial_count"], 2)
        self.assertEqual(result["reviewed_speech_vad_measured_trial_count"], 2)
        self.assertEqual(result["reviewed_speech_vad_missing_trial_count"], 0)
        self.assertTrue(result["reviewed_speech_vad_complete"])
        for group in (
                result["task_groups"]["quiet-speech"],
                result["language_groups"]["en-GB"],
                result["language_task_groups"]["en-GB"]["quiet-speech"]):
            self.assertEqual(group["reviewed_speech_vad_rejection_count"], 1)
            self.assertEqual(
                group["reviewed_speech_vad_rejection_trial_count"], 1)
            self.assertEqual(
                group["reviewed_speech_vad_retained_audio_ratio"]["median"],
                0.3125)
        output = io.StringIO()
        with redirect_stdout(output):
            benchmark._print_summary(result)
        self.assertIn(
            "Reviewed speech VAD: observed rejections 1/2 measured trials in 1/1 clips; "
            "retained-audio median 31.25%; duration coverage 2/2 trials",
            output.getvalue())
        self.assertEqual(result["reviewed_final_word_sample_count"], 1)
        self.assertEqual(result["final_word_failure_count"], 1)
        self.assertEqual(result["reviewed_final_word_trial_count"], 2)
        self.assertEqual(result["final_word_failure_trial_count"], 1)
        self.assertEqual(result["reviewed_first_word_sample_count"], 1)
        self.assertEqual(result["first_word_failure_count"], 1)
        self.assertEqual(result["reviewed_first_word_trial_count"], 2)
        self.assertEqual(result["first_word_failure_trial_count"], 1)
        self.assertEqual(result["samples"][0]["first_word"], {
            "retained": False,
            "retained_trials": 1,
            "failed_trials": 1,
            "trials": 2,
        })
        self.assertEqual(result["samples"][0]["final_word"], {
            "retained": False,
            "retained_trials": 1,
            "failed_trials": 1,
            "trials": 2,
        })

    def test_benchmark_prints_all_missing_whisper_vad_timings(self):
        manifest = {
            "model": "base.en",
            "runs": 2,
            "samples": [{"id": "speech", "audio": "speech.wav",
                         "task_group": "quiet-speech", "reference": "speech",
                         "reference_reviewed": True}],
        }
        transcriber = mock.Mock()
        transcriber.model.dtype = "int8"
        transcriber.last_timing = {}
        transcriber.transcribe.return_value = "speech"
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = os.path.join(directory, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with mock.patch.object(
                    benchmark.engine, "Transcriber", return_value=transcriber), \
                    mock.patch.object(
                        benchmark, "load_audio",
                        return_value=(mock.sentinel.audio, 2.0, 16000, "0" * 64)):
                result = benchmark.run_benchmark(manifest_path)

        detection = result["samples"][0]["speech_detection"]
        self.assertEqual(detection["trials"], 2)
        self.assertEqual(detection["measured_trials"], 0)
        self.assertEqual(detection["missing_trials"], 2)
        group = result["task_groups"]["quiet-speech"]
        self.assertEqual(group["reviewed_speech_vad_trial_count"], 2)
        self.assertEqual(group["reviewed_speech_vad_measured_trial_count"], 0)
        self.assertEqual(group["reviewed_speech_vad_missing_trial_count"], 2)
        self.assertFalse(group["reviewed_speech_vad_complete"])
        self.assertIsNone(
            group["reviewed_speech_vad_retained_audio_ratio"]["median"])
        output = io.StringIO()
        with redirect_stdout(output):
            benchmark._print_summary(result)
        self.assertIn("VAD speech: not measured (0/2 trials; 2 missing)",
                      output.getvalue())
        self.assertIn(
            "Reviewed speech VAD: observed rejections 0/0 measured trials in 0/1 clips; "
            "retained-audio median n/a; duration coverage 0/2 trials (2 missing; incomplete)",
            output.getvalue())
        json.dumps(result, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
