#!/usr/bin/env python3
"""Model-free checks for the SDK regression-report comparator."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from decimal import Decimal
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
ORDER_DIGEST = "e" * 64
SECRET = "confidential spoken words and local fixture path"


def report(*, candidate=False, digest=INPUT_DIGEST, order=ORDER_DIGEST,
           hint="auto", trials=3,
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
    speech_outputs = "".join(
        f"    output: trial={index}/{trials} empty=false characters=12\n"
        for index in range(1, trials + 1)
    )
    speech_line = (
        f"    transcript: [WER {rounded_wer}%] "
        f"[final-word retained={'false' if candidate else 'true'}] "
        f"[first-word retained={'false' if candidate else 'true'}] "
        f"[word-errors={errors} reference-words=35] "
        f"[max-reference-deletion-run={deletion}] <redacted 12 chars>\n"
    ) if scored else "    transcript: <redacted 12 chars>\n"
    control_outputs = "".join(
        f"    output: trial={index}/{trials} "
        f"empty={'false' if candidate and index == 1 else 'true'} "
        f"characters={9 if candidate and index == 1 else 0}\n"
        for index in range(1, trials + 1)
    )
    control_transcripts = (
        "    transcripts (2 distinct):\n"
        "      • [WER 0.0%] [word-errors=0 reference-words=0] "
        "[max-reference-deletion-run=0] <redacted 0 chars>\n"
        "      • [WER 100.0%] [word-errors=1 reference-words=0] "
        "[max-reference-deletion-run=0] <redacted 9 chars>\n"
    ) if candidate else (
        "    transcript: [WER 0.0%] [word-errors=0 reference-words=0] "
        "[max-reference-deletion-run=0] <redacted 0 chars>\n"
    )
    control_section = (
        "\n## Clip 002\n\n- Reference: <redacted path> (WER enabled)\n"
        "\n```text\n"
        "    latency:  p50=  50.0 ms  min=  49.0 ms  max=  51.0 ms\n"
        f"{control_outputs}{control_transcripts}"
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
        f"- Benchmark order SHA-256: {order}\n"
        f"- Trials per clip: {trials}\n"
        f"- Parakeet TDT v3 language/script hint: {hint}\n"
        f"- Clips: {clip_count}\n"
        "\n## Clip 001\n\n- Reference: <redacted path> (WER enabled)\n"
        "\n```text\n"
        f"    latency:  p50=  {latency} ms  min=  49.0 ms  max=  71.0 ms\n"
        f"{speech_outputs}{speech_line}```\n"
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
        self.assertIn("1 worse; 0 better", table)
        self.assertIn("1 newly failed; 0 recovered", table)
        self.assertIn("First-word failures | 0 | 1 | +1", table)
        self.assertIn("Review numbered positions with quality regressions: 001.", table)
        self.assertIn("Review numbered non-speech positions with new emissions: 002.", table)
        self.assertNotIn(SECRET, table)

    def test_first_word_loss_is_visible_when_other_quality_metrics_tie(self):
        baseline = comparator.parse_report(report(controls=False))
        candidate = comparator.parse_report(report(candidate=True, controls=False))
        candidate = replace(
            candidate,
            corpus_errors=baseline.corpus_errors,
            corpus_wer=baseline.corpus_wer,
            worst_wer=baseline.worst_wer,
            final_failures=baseline.final_failures,
            clip_metrics=(replace(
                candidate.clip_metrics[0],
                worst_errors=baseline.clip_metrics[0].worst_errors,
                worst_wer=baseline.clip_metrics[0].worst_wer,
                final_failure=False,
                worst_deletion_run=baseline.clip_metrics[0].worst_deletion_run,
            ),),
        )
        comparator.validate_pair(baseline, candidate)
        table = comparator.comparison_table(baseline, candidate, 1)
        self.assertIn("Conservative corpus WER | 5.71% (2/35) | 5.71% (2/35)", table)
        self.assertIn("Paired speech clips: first word | — | — | 1 newly failed", table)
        self.assertIn("Review numbered positions with quality regressions: 001.", table)

    def test_compensating_clip_changes_are_visible_when_corpus_errors_tie(self):
        baseline = comparator.parse_report(report(controls=False))
        candidate = comparator.parse_report(report(candidate=True, controls=False))
        baseline = replace(
            baseline, clips=2, corpus_errors=5, reference_words=70,
            corpus_wer=Decimal("7.14"), clip_metrics=(
                baseline.clip_metrics[0],
                replace(baseline.clip_metrics[0], worst_errors=3),
            ),
        )
        candidate = replace(
            candidate, clips=2, corpus_errors=5, reference_words=70,
            corpus_wer=Decimal("7.14"), clip_metrics=(
                candidate.clip_metrics[0],
                replace(candidate.clip_metrics[0], worst_errors=2,
                        final_failure=False, first_failure=False,
                        worst_deletion_run=4),
            ),
        )
        comparator.validate_pair(baseline, candidate)
        table = comparator.comparison_table(baseline, candidate, 1)
        self.assertIn("+0 errors", table)
        self.assertIn("1 worse; 1 better", table)
        self.assertIn("Review numbered positions with quality regressions: 001.", table)
        self.assertNotIn(SECRET, table)

    def test_rejects_unmatched_provenance_and_input_coverage(self):
        changes = (
            (dict(digest="d" * 64), "benchmark inputs SHA-256"),
            (dict(order="d" * 64), "benchmark order SHA-256"),
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
        with self.assertRaisesRegex(comparator.ComparisonError, "incomplete or mixed score"):
            comparator.parse_report(report(scored=False))
        with self.assertRaisesRegex(comparator.ComparisonError, "incomplete or mixed score"):
            comparator.parse_report(report().replace("[first-word retained=true] ", ""))
        with self.assertRaisesRegex(comparator.ComparisonError, "comparison provenance"):
            comparator.parse_report(report().replace(
                f"- Benchmark order SHA-256: {ORDER_DIGEST}\n", ""))
        with self.assertRaisesRegex(comparator.ComparisonError, "input-order fingerprint"):
            comparator.parse_report(report(order="not-a-digest"))
        with self.assertRaisesRegex(comparator.ComparisonError, "clip WER conflicts"):
            comparator.parse_report(report(errors=4))
        with self.assertRaisesRegex(comparator.ComparisonError, "non-speech control receipt"):
            comparator.parse_report(report().replace(
                "deliverable text in 0/3 measured trials",
                "deliverable text in 0/2 measured trials"))

    def test_rejects_truncated_clips_and_summary_rewrites(self):
        source = report()
        mutations = (
            (source.replace("| 5.7 | 5.7 | 0 | 60.0 |",
                            "| 5.7 | 99.9 | 0 | 60.0 |"), "summary conflicts"),
            (source.replace("\n## Clip 002", "\n## Removed 002"), "clip numbering"),
            (source[:source.index("\n## Clip 002")] +
             source[source.index("\n## Summary"):], "clip sections"),
            (source.replace("    output: trial=2/3 empty=false characters=12\n", ""),
             "trial receipts"),
            (source.replace("    output: trial=2/3 empty=false characters=12",
                            "    output: trial=1/3 empty=false characters=12"),
             "trial receipts"),
            (source.replace("deliverable text in 0/3 measured trials",
                            "deliverable text in 1/3 measured trials"),
             "control receipt"),
            (source.replace("[word-errors=2 reference-words=35]",
                            "[word-errors=1 reference-words=35]"),
             "clip WER conflicts"),
        )
        for mutated, reason in mutations:
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(comparator.ComparisonError, reason):
                    comparator.parse_report(mutated)

    def test_accepts_unredacted_numbered_clip_and_variable_hypotheses(self):
        source = report(candidate=True)
        source = source.replace("## Clip 001", "## 001-speech-example")
        source = source.replace("## Clip 002", "## 002-control-example")
        source = source.replace(
            '[final-word retained=false]',
            '[final-word retained=false expected="alpha" actual-last="beta"]',
        )
        source = source.replace(
            '[first-word retained=false]',
            '[first-word retained=false expected="alpha" actual-first="beta"]',
        )
        source = source.replace("<redacted 12 chars>", f'"{SECRET}"')
        parsed = comparator.parse_report(source)
        self.assertEqual(parsed.worst_deletion_run, 5)
        self.assertEqual(parsed.controls, (1, 1, 3))

    def test_reconciles_variable_speech_and_multiple_speech_clips(self):
        source = report(controls=False)
        source = source.replace(
            "    transcript: [WER 5.7%] [final-word retained=true] [first-word retained=true] "
            "[word-errors=2 reference-words=35] "
            "[max-reference-deletion-run=4] <redacted 12 chars>\n",
            "    transcripts (2 distinct):\n"
            "      • [WER 2.9%] [final-word retained=true] [first-word retained=true] "
            "[word-errors=1 reference-words=35] "
            "[max-reference-deletion-run=1] <redacted 12 chars>\n"
            "      • [WER 5.7%] [final-word retained=true] [first-word retained=true] "
            "[word-errors=2 reference-words=35] "
            "[max-reference-deletion-run=4] <redacted 12 chars>\n",
        )
        source = source.replace("- Clips: 1", "- Clips: 2")
        source = source.replace(
            "\n## Summary",
            "\n## Clip 002\n\n- Reference: <redacted path> (WER enabled)\n"
            "\n```text\n"
            "    latency:  p50=  70.0 ms  min=  69.0 ms  max=  71.0 ms\n"
            "    output: trial=1/3 empty=false characters=9\n"
            "    output: trial=2/3 empty=false characters=9\n"
            "    output: trial=3/3 empty=false characters=9\n"
            "    transcript: [WER 10.0%] [final-word retained=true] [first-word retained=true] "
            "[word-errors=1 reference-words=10] "
            "[max-reference-deletion-run=3] <redacted 9 chars>\n"
            "```\n\n## Summary",
        )
        source = source.replace(
            "| `v3` | 1 | 5.7 | 5.7 | 0 | 60.0 |",
            "| `v3` | 2 | 7.85 | 10.0 | 0 | 65.0 |",
        ).replace(
            "5.71% (2 errors / 35 reference words)",
            "6.67% (3 errors / 45 reference words)",
        )
        parsed = comparator.parse_report(source)
        self.assertEqual(parsed.reference_words, 45)
        self.assertEqual(parsed.corpus_errors, 3)
        self.assertEqual(parsed.worst_wer, 10)
        self.assertEqual(parsed.worst_deletion_run, 4)

        with self.assertRaisesRegex(comparator.ComparisonError, "corpus WER conflicts"):
            comparator.parse_report(source.replace(
                "6.67% (3 errors / 45 reference words)",
                "4.44% (2 errors / 45 reference words)"))

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

            files[0].write_text(report().replace(
                "| 5.7 | 5.7 | 0 | 60.0 |",
                "| 5.7 | 99.9 | 0 | 60.0 |",
            ).replace("<redacted 12 chars>", f'"{SECRET}"'), encoding="utf-8")
            out = io.StringIO()
            err = io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = comparator.main(["--pair", str(files[0]), str(files[1])])
            self.assertEqual(code, 1)
            self.assertEqual(out.getvalue(), "")
            self.assertIn("summary conflicts", err.getvalue())
            self.assertNotIn(SECRET, err.getvalue())
            self.assertNotIn(str(root), err.getvalue())


if __name__ == "__main__":
    unittest.main()
