#!/usr/bin/env python3
"""Model-free checks for the SDK regression-report comparator."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("compare-sdk-asr-reports.py")
spec = importlib.util.spec_from_file_location("compare_sdk_asr_reports", SCRIPT)
comparator = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = comparator
spec.loader.exec_module(comparator)

APP_PIN = "a" * 40
CANDIDATE_PIN = "b" * 40
INPUT_DIGEST = "c" * 64
SECRET = "confidential spoken words and local fixture path"


def report(*, candidate=False, digest=INPUT_DIGEST, hint="auto", trials=3,
           state="default", controls=True, scored=True, revision=None,
           errors=None, app_pin=APP_PIN, kind="Real-Dictation"):
    pin = revision or (CANDIDATE_PIN if candidate else APP_PIN)
    dependency = "candidate-dependency" if candidate else "production-dependency"
    if errors is None:
        errors = 3 if candidate else 2
    wer = "8.57" if errors == 3 else "5.71"
    rounded_wer = "8.6" if errors == 3 else "5.7"
    deletion = 5 if candidate else 4
    failures = 1 if candidate else 0
    latency = "65.0" if candidate else "60.0"
    clip_count = 2 if controls else 1
    speech_line = (
        f"    transcript: [WER {rounded_wer}%] [final-word retained=true] "
        f"[word-errors={errors} reference-words=35] "
        f"[max-reference-deletion-run={deletion}] <redacted 12 chars>\n"
    ) if scored else "    transcript: <redacted 12 chars>\n"
    control_section = (
        "\n## Clip 002\n\n```text\n"
        "    output: trial=1/3 empty=true characters=0\n"
        "```\n"
    ) if controls else ""
    control_summary = (
        "\nNon-speech controls (zero-byte references): 1; "
        f"deliverable text in {1 if candidate else 0}/{trials} measured trials.\n"
    ) if controls else ""
    return (
        f"# Presspeech {kind} Regression\n\n"
        "- Date: 20260924T120000Z\n"
        f"- Input directory: {SECRET}\n"
        "- Backend: v3\n"
        f"- FluidAudio revision: {pin}\n"
        f"- App FluidAudio revision: {app_pin}\n"
        f"- Baseline dependency: {dependency} (not whole-app qualification)\n"
        f"- Benchmark inputs SHA-256: {digest}\n"
        f"- Trials per clip: {trials}\n"
        f"- Parakeet TDT v3 language/script hint: {hint}\n"
        f"- Clips: {clip_count}\n"
        "\n## Clip 001\n\n```text\n"
        f"{speech_line}```\n"
        f"{control_section}"
        "\n## Summary\n\n"
        "| Backend | Clip rows | Mean worst-speech-clip WER % | Worst speech WER % | "
        "Speech final-word failures | Average speech p50 ms |\n"
        "|---|---:|---:|---:|---:|---:|\n"
        f"| `v3` | {clip_count} | {rounded_wer} | {rounded_wer} | {failures} | {latency} |\n\n"
        f"Conservative corpus WER (worst observed transcript per clip): "
        f"{wer}% ({errors} errors / 35 reference words)\n"
        f"{control_summary}"
        "\n## Inherited SDK environment\n\n"
        f"- State: {state}\n"
    )


class ReportComparisonTests(unittest.TestCase):
    def test_same_inputs_and_distinct_pins_compare_without_private_content(self):
        baseline = comparator.parse_report(report())
        candidate = comparator.parse_report(report(candidate=True))
        comparator.validate_pair(baseline, candidate)
        table = comparator.comparison_table(baseline, candidate, 1)
        self.assertIn("+1 errors", table)
        self.assertIn("+1 words", table)
        self.assertIn("+5.0 ms", table)
        self.assertIn("1/3", table)
        self.assertNotIn(SECRET, table)

    def test_rejects_unmatched_provenance_and_input_coverage(self):
        changes = (
            (dict(digest="d" * 64), "benchmark inputs SHA-256"),
            (dict(hint="de"), "language hint"),
            (dict(trials=5, controls=False), "trial count"),
            (dict(kind="Public-Speech"), "corpus kind"),
            (dict(app_pin="d" * 40), "app FluidAudio pin"),
            (dict(controls=False), "clip count"),
        )
        baseline = comparator.parse_report(report())
        for kwargs, reason in changes:
            with self.subTest(reason=reason):
                candidate = comparator.parse_report(report(candidate=True, **kwargs))
                with self.assertRaisesRegex(comparator.ComparisonError, reason):
                    comparator.validate_pair(baseline, candidate)

    def test_rejects_wrong_pin_classification_and_incomplete_metrics(self):
        with self.assertRaisesRegex(comparator.ComparisonError, "baseline report"):
            comparator.validate_pair(
                comparator.parse_report(report(revision="d" * 40)),
                comparator.parse_report(report(candidate=True)),
            )
        with self.assertRaisesRegex(comparator.ComparisonError, "candidate report"):
            comparator.validate_pair(
                comparator.parse_report(report()),
                comparator.parse_report(report(candidate=True, revision=APP_PIN)),
            )
        with self.assertRaisesRegex(comparator.ComparisonError, "SDK environment"):
            comparator.parse_report(report(state="configured"))
        with self.assertRaisesRegex(comparator.ComparisonError, "deletion observations"):
            comparator.parse_report(report(scored=False))
        with self.assertRaisesRegex(comparator.ComparisonError, "corpus WER conflicts"):
            comparator.parse_report(report(errors=4))
        with self.assertRaisesRegex(comparator.ComparisonError, "non-speech control receipt"):
            comparator.parse_report(report().replace(
                "deliverable text in 0/3 measured trials",
                "deliverable text in 0/2 measured trials"))

    def test_cli_emits_only_aggregate_evidence_and_refuses_mixed_sdk_pairs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = [root / f"report-{index}.md" for index in range(4)]
            files[0].write_text(report().replace(
                "<redacted 12 chars>", f'"{SECRET}"'), encoding="utf-8")
            files[1].write_text(report(candidate=True), encoding="utf-8")
            files[2].write_text(report(digest="d" * 64), encoding="utf-8")
            files[3].write_text(report(candidate=True, digest="d" * 64,
                                       revision="e" * 40), encoding="utf-8")
            out = io.StringIO()
            err = io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = comparator.main(["--pair", str(files[0]), str(files[1])])
            self.assertEqual(code, 0)
            self.assertIn("Comparison only: not a release verdict", out.getvalue())
            self.assertNotIn(SECRET, out.getvalue() + err.getvalue())
            self.assertNotIn(str(root), out.getvalue() + err.getvalue())

            out = io.StringIO()
            err = io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = comparator.main([
                    "--pair", str(files[0]), str(files[1]),
                    "--pair", str(files[2]), str(files[3]),
                ])
            self.assertEqual(code, 1)
            self.assertEqual(out.getvalue(), "")
            self.assertIn("do not share one production/candidate revision pair", err.getvalue())
            self.assertNotIn(SECRET, err.getvalue())


if __name__ == "__main__":
    unittest.main()
