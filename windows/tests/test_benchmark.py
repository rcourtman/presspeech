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


if __name__ == "__main__":
    unittest.main()
