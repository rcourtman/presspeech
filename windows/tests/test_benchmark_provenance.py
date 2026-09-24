import array
import unittest

from benchmark_provenance import (
    asr_audio_sha256, benchmark_inputs_sha256,
    recorded_tail_probe_inputs_sha256,
)


class BenchmarkProvenanceTests(unittest.TestCase):
    def row(self, samples=(0.0, 0.25), **changes):
        row = {
            "id": "private-label",
            "audio": "private-path.wav",
            "asr_audio_sha256": asr_audio_sha256(array.array("f", samples)),
            "audio_seconds": 0.5,
            "source_sample_rate": 16000,
            "reference": "Zażółć gęślą",
            "reference_reviewed": True,
            "expected_silence": False,
            "task_group": "spontaneous-dictation",
            "language_group": "pl",
        }
        row.update(changes)
        return row

    def test_audio_digest_tracks_samples_given_to_asr(self):
        first = asr_audio_sha256(array.array("f", [0.0, 0.25]))
        same = asr_audio_sha256(array.array("f", [0.0, 0.25]))
        changed = asr_audio_sha256(array.array("f", [0.0, 0.26]))
        self.assertEqual(first, same)
        self.assertNotEqual(first, changed)
        self.assertRegex(first, r"^[0-9a-f]{64}$")

    def test_corpus_digest_ignores_paths_ids_and_order_but_not_duplicates(self):
        speech = self.row()
        silence = self.row(
            samples=(0.0, 0.0), reference="", reference_reviewed=True,
            expected_silence=True, task_group="silence-control",
            language_group=None,
        )
        original = benchmark_inputs_sha256([speech, silence])
        renamed = [dict(silence, id="other", audio="moved.wav"),
                   dict(speech, id="another", audio="elsewhere.wav")]
        self.assertEqual(original, benchmark_inputs_sha256(renamed))
        self.assertNotEqual(original, benchmark_inputs_sha256([speech]))
        self.assertNotEqual(original,
                            benchmark_inputs_sha256([speech, silence, silence]))

    def test_corpus_digest_tracks_audio_references_and_scoring_labels(self):
        baseline = self.row()
        baseline_digest = benchmark_inputs_sha256([baseline])
        changes = (
            {"asr_audio_sha256": self.row(samples=(0.0, 0.26))["asr_audio_sha256"]},
            {"reference": "Zażółć gęślę"},
            {"reference_reviewed": False},
            {"expected_silence": True},
            {"task_group": "read-speech"},
            {"language_group": "en"},
            {"audio_seconds": 0.6},
            {"source_sample_rate": 48000},
        )
        for change in changes:
            with self.subTest(change=change):
                self.assertNotEqual(
                    baseline_digest,
                    benchmark_inputs_sha256([dict(baseline, **change)]),
                )

    def test_rejects_non_digest_audio_identity(self):
        for invalid in ("", "x" * 64, "A" * 64, None):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "ASR audio SHA-256"):
                    benchmark_inputs_sha256([self.row(asr_audio_sha256=invalid)])

    def test_recorded_tail_digest_tracks_exact_trim_point_and_duplicates(self):
        first = {"asr_audio_sha256": self.row()["asr_audio_sha256"],
                 "trim_at_sample": 9600}
        second = {"asr_audio_sha256": self.row(samples=(0.1, 0.2))[
            "asr_audio_sha256"], "trim_at_sample": 8000}
        digest = recorded_tail_probe_inputs_sha256([first, second])
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual(digest,
                         recorded_tail_probe_inputs_sha256([second, first]))
        self.assertNotEqual(digest, recorded_tail_probe_inputs_sha256(
            [dict(first, trim_at_sample=9601), second]))
        self.assertNotEqual(digest, recorded_tail_probe_inputs_sha256(
            [first, second, second]))
        # A probe annotation must not silently change the baseline corpus
        # digest used to compare ordinary speech WER.
        self.assertEqual(benchmark_inputs_sha256([self.row()]),
                         benchmark_inputs_sha256([
                             dict(self.row(), trim_at_sample=9601)]))

    def test_recorded_tail_digest_rejects_invalid_identity(self):
        for row in ({"asr_audio_sha256": "x" * 64, "trim_at_sample": 10},
                    {"asr_audio_sha256": self.row()["asr_audio_sha256"],
                     "trim_at_sample": True},
                    {"asr_audio_sha256": self.row()["asr_audio_sha256"],
                     "trim_at_sample": 0}):
            with self.subTest(row=row):
                with self.assertRaises(ValueError):
                    recorded_tail_probe_inputs_sha256([row])
