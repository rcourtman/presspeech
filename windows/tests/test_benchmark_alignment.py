"""Portable checks for benchmark-only long-form deletion diagnostics."""

from itertools import product
import unittest

from benchmark_alignment import word_error_alignment


def edit_distance(reference, hypothesis):
    """Independent distance oracle for short exhaustive examples."""
    rows = [[0] * (len(hypothesis) + 1)
            for _ in range(len(reference) + 1)]
    for index in range(len(reference) + 1):
        rows[index][0] = index
    for column in range(len(hypothesis) + 1):
        rows[0][column] = column
    for index, word in enumerate(reference, 1):
        for column, candidate in enumerate(hypothesis, 1):
            rows[index][column] = min(
                rows[index - 1][column - 1] + (word != candidate),
                rows[index - 1][column] + 1,
                rows[index][column - 1] + 1,
            )
    return rows[-1][-1]


class WordAlignmentTests(unittest.TestCase):
    def test_distinguishes_contiguous_loss_from_substitution_or_insertion(self):
        cases = [
            ("one two three four five", "one five", (3, 3)),
            ("one two three", "one too three", (1, 0)),
            ("one two three", "one extra two three", (1, 0)),
            # This has equal-cost substitution and delete/insert paths. The
            # stable diagonal tie preference must not invent a deletion.
            ("one two", "two one", (2, 0)),
            ("one two three", "", (3, 3)),
            ("", "extra words", (2, 0)),
            ("one two three", "one two three", (0, 0)),
            ("one two three four", "two four", (2, 1)),
        ]
        for reference, hypothesis, expected in cases:
            with self.subTest(reference=reference, hypothesis=hypothesis):
                self.assertEqual(word_error_alignment(
                    reference.split(), hypothesis.split()), expected)

    def test_minimum_error_count_matches_levenshtein_on_small_sequences(self):
        sequences = [tuple(items) for size in range(4)
                     for items in product(("one", "two"), repeat=size)]
        for reference in sequences:
            for hypothesis in sequences:
                errors, max_run = word_error_alignment(reference, hypothesis)
                self.assertEqual(errors, edit_distance(reference, hypothesis))
                self.assertGreaterEqual(max_run, 0)
                self.assertLessEqual(max_run, errors)

    def test_long_empty_output_keeps_full_loss_without_backtracking(self):
        self.assertEqual(word_error_alignment(["word"] * 1200, []),
                         (1200, 1200))


if __name__ == "__main__":
    unittest.main()
