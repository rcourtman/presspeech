#!/usr/bin/env python3
"""Model-free checks for the paired public window-position report diagnostic."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("analyze-window-shift-report.py")
spec = importlib.util.spec_from_file_location("presspeech_window_report", SCRIPT)
analyzer = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = analyzer
spec.loader.exec_module(analyzer)

PIN = "a" * 40
HARNESS = "b" * 64
PRIVATE = "private spoken phrase and /private/audio/path"


def report(directory: Path, errors=None, first_failures=(), final_failures=(),
           deletion_runs=None) -> str:
    rows = analyzer._manifest_rows(directory)
    corpus, order = analyzer._input_receipts(directory, rows)
    errors = errors or {}
    deletion_runs = deletion_runs or {}
    sections = []
    words = []
    wers = []
    for position, row in enumerate(rows, 1):
        reference = (directory / f"{row['fixture_id']}.txt").read_text(encoding="utf-8")
        word_count = len(analyzer.inputs.wer_tokens(reference))
        words.append(word_count)
        count = errors.get(position, 0)
        wer = Decimal(100 * count) / word_count
        wers.append(wer)
        sections.append(
            f"\n## Clip {position:03d}\n\n"
            "- Clip name: <redacted>\n"
            "- Reference: <redacted path> (WER enabled)\n\n"
            "```text\n"
            "    latency:  p50=  50.0 ms  min=  49.0 ms  max=  51.0 ms\n"
            "    output: trial=1/1 empty=false characters=12\n"
            f"    transcript: [WER {wer:.1f}%] "
            f"[final-word retained={'false' if position in final_failures else 'true'}] "
            f"[first-word retained={'false' if position in first_failures else 'true'}] "
            f"[word-errors={count} reference-words={word_count}] "
            f"[max-reference-deletion-run={deletion_runs.get(position, 0)}] "
            "<redacted 12 chars>\n```\n"
        )
    total_errors = sum(errors.values())
    total_words = sum(words)
    return (
        "# Presspeech Public-Speech Regression\n\n"
        "- Backend: v3\n"
        f"- FluidAudio revision: {PIN}\n"
        f"- App FluidAudio revision: {PIN}\n"
        "- Baseline dependency: production-dependency (not whole-app qualification)\n"
        f"- Benchmark inputs SHA-256: {corpus}\n"
        f"- Benchmark order SHA-256: {order}\n"
        f"- Benchmark harness SHA-256: {HARNESS}\n"
        "- Host model: Mac16,11\n"
        "- Host chip: Apple M4\n"
        "- Host memory bytes: 17179869184\n"
        "- macOS version: 26.5.2\n"
        "- Trials per clip: 1\n"
        "- Parakeet TDT v3 language/script hint: en\n"
        "- Fixture paths: redacted\n"
        f"- Clips: {len(rows)}\n"
        "- Evidence scope: repeated-speech window-position diagnostic; "
        "not an independent release or candidate gate\n"
        + "".join(sections)
        + "\n## Summary\n\n"
        "| Backend | Clip rows | Mean worst-speech-clip WER % | Worst speech WER % | "
        "Speech final-word failures | Average speech p50 ms |\n"
        "|---|---:|---:|---:|---:|---:|\n"
        f"| `v3` | {len(rows)} | {sum(wers) / len(rows):.2f} | {max(wers):.1f} | "
        f"{len(final_failures)} | 50.0 |\n\n"
        "Conservative corpus WER (worst observed transcript per clip): "
        f"{Decimal(100 * total_errors) / total_words:.2f}% "
        f"({total_errors} errors / {total_words} reference words)\n"
        "\n## Inherited SDK environment\n\n- State: default\n"
    )


class WindowShiftReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="presspeech-window-report-test-")
        root = Path(cls.temp.name)
        source = root / "source"
        source.mkdir()
        for index in range(4):
            path = source / f"source-{index}.wav"
            analyzer.fixtures.long_form.write_pcm16_fixture(path, 16, 16000, index + 1)
            path.with_suffix(".txt").write_text(
                f"distinct reference words {index}\n", encoding="utf-8"
            )
        long_form = root / "long-form"
        analyzer.fixtures.long_form.compose(source, long_form, 30.0, False)
        cls.directory = root / "shifted"
        analyzer.fixtures.compose(
            long_form, cls.directory, analyzer.fixtures.DEFAULT_OFFSETS_MS, False
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_flags_paired_harm_without_leaking_text(self):
        source = report(
            self.directory,
            errors={2: 2},
            first_failures=(3,),
            deletion_runs={2: 2},
        )
        result = analyzer.analyze(source, self.directory)
        self.assertIn("001->002: 3500 ms | 0->2", result)
        self.assertIn("001->003: 7000 ms | 0->0 | 0/0->1/0", result)
        self.assertIn("Measured worsening: 2/4 shifted variants across 1/2 sources", result)
        self.assertNotIn(PRIVATE, result)
        self.assertNotIn("distinct reference words", result)
        self.assertNotIn(str(self.directory), result)

    def test_recomputed_receipts_match_the_benchmark_snapshot_algorithm(self):
        rows = analyzer._manifest_rows(self.directory)
        expected = analyzer._input_receipts(self.directory, rows)
        with tempfile.TemporaryDirectory() as temp:
            frozen = Path(temp) / "snapshot"
            audio = [self.directory / f"{row['fixture_id']}.wav" for row in rows]
            analyzer.inputs.snapshot(audio, frozen, False)
            self.assertEqual(analyzer.inputs.verified_digests(frozen), expected)

    def test_rejects_changed_fixture_and_order_receipts(self):
        source = report(self.directory)
        real_order = analyzer._input_receipts(
            self.directory, analyzer._manifest_rows(self.directory)
        )[1]
        altered = source.replace(real_order, "f" * 64, 1)
        with self.assertRaisesRegex(analyzer.AnalysisError, "receipt differs"):
            analyzer.analyze(altered, self.directory)
        variant = self.directory / "long-form-001-lead03500ms.wav"
        original = variant.read_bytes()
        try:
            variant.write_bytes(original[:-2] + b"\x01\x00")
            with self.assertRaisesRegex(analyzer.AnalysisError, "failed validation"):
                analyzer.analyze(source, self.directory)
        finally:
            variant.write_bytes(original)

    def test_rejects_wrong_scope_or_incomplete_receipts(self):
        source = report(self.directory)
        with self.assertRaisesRegex(analyzer.AnalysisError, "only public"):
            analyzer.analyze(source.replace("Public-Speech", "Real-Dictation", 1), self.directory)
        with self.assertRaisesRegex(analyzer.AnalysisError, "lacks window-position"):
            analyzer.analyze(source.replace("window-position diagnostic", "other diagnostic", 1),
                             self.directory)
        with self.assertRaisesRegex(analyzer.AnalysisError, "failed validation"):
            analyzer.analyze(source.replace("output: trial=1/1", "output: trial=2/1", 1),
                             self.directory)

    def test_cli_success_prints_only_aggregate_and_numbered_pairs(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "report.md"
            path.write_text(report(self.directory, final_failures=(6,)), encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = analyzer.main([
                    "--fixture-dir", str(self.directory), "--report", str(path)
                ])
            self.assertEqual(code, 0)
            self.assertIn("004->006: 7000 ms", stdout.getvalue())
            self.assertIn("1/2 sources", stdout.getvalue())
            self.assertNotIn(str(path), stdout.getvalue())
            self.assertNotIn("distinct reference words", stdout.getvalue())
            self.assertEqual(stderr.getvalue(), "")

    def test_cli_failure_does_not_echo_report_or_path(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "report.md"
            path.write_text(PRIVATE, encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = analyzer.main([
                    "--fixture-dir", str(self.directory), "--report", str(path)
                ])
            self.assertEqual(code, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertNotIn(PRIVATE, stderr.getvalue())
            self.assertNotIn(str(path), stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
