#!/usr/bin/env python3
"""Compare aggregate v3 reports from two FluidAudio revisions.

This is a comparison aid, not an ASR quality or release gate. It consumes the
Markdown artifacts emitted by run-real-dictation-regression.sh and never prints
fixture names, reference text, hypotheses, or input paths.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import re
import sys


REVISION = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
SUMMARY_ROW = re.compile(
    r"^\| `v3` \| ([0-9]+) \| ([0-9]+(?:\.[0-9]+)?) \| "
    r"([0-9]+(?:\.[0-9]+)?) \| ([0-9]+) \| "
    r"([0-9]+(?:\.[0-9]+)?) \|$",
    re.MULTILINE,
)
CORPUS_ROW = re.compile(
    r"^Conservative corpus WER \(worst observed transcript per clip\): "
    r"([0-9]+(?:\.[0-9]+)?)% \(([0-9]+) errors / "
    r"([1-9][0-9]*) reference words\)$",
    re.MULTILINE,
)
# The benchmark prints these tags before any unredacted hypothesis. Do not
# search arbitrary transcript text for a metric-like substring.
DELETION_TAG = re.compile(
    r"^\s*(?:transcript:|•)\s*\[WER [0-9]+(?:\.[0-9]+)?%\] "
    r"\[final-word retained=(?:true|false)[^\]\n]*\]"
    r"(?: \[critical-terms [^\]\n]*\])? "
    r"\[word-errors=[0-9]+ reference-words=[1-9][0-9]*\] "
    r"\[max-reference-deletion-run=([0-9]+)\]",
    re.MULTILINE,
)
CONTROL_ROW = re.compile(
    r"^Non-speech controls \(zero-byte references\): ([0-9]+); "
    r"deliverable text in ([0-9]+)/([0-9]+) measured trials\.$",
    re.MULTILINE,
)


class ComparisonError(ValueError):
    """A report is incomplete or the pair is not like-for-like."""


@dataclass(frozen=True)
class Report:
    kind: str
    revision: str
    app_revision: str
    dependency: str
    digest: str
    trials: int
    clips: int
    language: str
    corpus_wer: Decimal
    corpus_errors: int
    reference_words: int
    worst_wer: Decimal
    final_failures: int
    average_p50_ms: Decimal
    worst_deletion_run: int
    controls: tuple[int, int, int] | None


def one(pattern: re.Pattern[str], source: str, label: str) -> re.Match[str]:
    matches = list(pattern.finditer(source))
    if len(matches) != 1:
        raise ComparisonError(f"expected one {label}; found {len(matches)}")
    return matches[0]


def parse_report(source: str) -> Report:
    lines = source.splitlines()
    if not lines or lines[0] not in (
        "# Presspeech Real-Dictation Regression",
        "# Presspeech Public-Speech Regression",
    ):
        raise ComparisonError("not a single-backend dictation regression report")
    kind = "private" if "Real-Dictation" in lines[0] else "public"

    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.startswith("## "):
            break
        if line.startswith("- ") and ": " in line:
            key, value = line[2:].split(": ", 1)
            if key in fields:
                raise ComparisonError("duplicate report header field")
            fields[key] = value
    required = (
        "Backend", "FluidAudio revision", "App FluidAudio revision",
        "Baseline dependency", "Benchmark inputs SHA-256", "Trials per clip",
        "Parakeet TDT v3 language/script hint", "Clips",
    )
    if any(key not in fields for key in required):
        raise ComparisonError("report is missing comparison provenance")
    if fields["Backend"] != "v3":
        raise ComparisonError("comparison requires the explicit v3 backend")
    revision = fields["FluidAudio revision"]
    app_revision = fields["App FluidAudio revision"]
    digest = fields["Benchmark inputs SHA-256"]
    if not REVISION.fullmatch(revision) or not REVISION.fullmatch(app_revision):
        raise ComparisonError("report has an invalid FluidAudio revision")
    if not DIGEST.fullmatch(digest):
        raise ComparisonError("report has an invalid input fingerprint")
    dependency = fields["Baseline dependency"].split(" ", 1)[0]
    if dependency not in ("production-dependency", "candidate-dependency"):
        raise ComparisonError("report has an unknown dependency classification")
    language = fields["Parakeet TDT v3 language/script hint"]
    if not re.fullmatch(r"auto|[a-z]{2}", language):
        raise ComparisonError("report has an invalid language hint")
    try:
        trials = int(fields["Trials per clip"])
        clips = int(fields["Clips"])
    except ValueError as exc:
        raise ComparisonError("report has an invalid trial or clip count") from exc
    if trials < 1 or clips < 1:
        raise ComparisonError("report needs positive trial and clip counts")

    environment = one(
        re.compile(r"^## Inherited SDK environment\n\n- State: ([^\n]+)$", re.MULTILINE),
        source, "SDK environment receipt",
    ).group(1)
    if environment != "default":
        raise ComparisonError("SDK environment was not the default")
    summary = one(
        re.compile(r"^## Summary\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL),
        source, "summary section",
    ).group(1)
    row = one(SUMMARY_ROW, summary, "v3 summary row")
    corpus = one(CORPUS_ROW, summary, "conservative corpus WER")
    if int(row.group(1)) != clips:
        raise ComparisonError("summary clip count differs from report header")
    if int(row.group(4)) > clips:
        raise ComparisonError("summary final-word failures exceed clip count")
    corpus_wer = Decimal(corpus.group(1))
    errors = int(corpus.group(2))
    words = int(corpus.group(3))
    # The published report rounds to two decimal places. Reject internally
    # inconsistent rows instead of turning a hand-edited value into evidence.
    if abs(corpus_wer - Decimal(100 * errors) / Decimal(words)) > Decimal("0.011"):
        raise ComparisonError("corpus WER conflicts with exact word counts")
    deletion_runs = [int(match.group(1)) for match in DELETION_TAG.finditer(
        source.split("\n## Summary\n", 1)[0]
    )]
    if not deletion_runs:
        raise ComparisonError("report has no scored speech deletion observations")

    control_matches = list(CONTROL_ROW.finditer(summary))
    if len(control_matches) > 1:
        raise ComparisonError("report has duplicate non-speech control receipts")
    controls = None
    if control_matches:
        control = control_matches[0]
        controls = tuple(int(control.group(index)) for index in (1, 2, 3))
        count, emitted, measured = controls
        if count < 1 or measured != count * trials or emitted > measured:
            raise ComparisonError("non-speech control receipt is inconsistent")

    return Report(
        kind=kind, revision=revision, app_revision=app_revision,
        dependency=dependency, digest=digest, trials=trials, clips=clips,
        language=language, corpus_wer=corpus_wer, corpus_errors=errors,
        reference_words=words, worst_wer=Decimal(row.group(3)),
        final_failures=int(row.group(4)),
        average_p50_ms=Decimal(row.group(5)),
        worst_deletion_run=max(deletion_runs), controls=controls,
    )


def validate_pair(baseline: Report, candidate: Report) -> None:
    if baseline.dependency != "production-dependency" or baseline.revision != baseline.app_revision:
        raise ComparisonError("baseline report is not on its app's production pin")
    if candidate.dependency != "candidate-dependency" or candidate.revision == candidate.app_revision:
        raise ComparisonError("candidate report is not on a distinct SDK pin")
    for label, left, right in (
        ("app FluidAudio pin", baseline.app_revision, candidate.app_revision),
        ("corpus kind", baseline.kind, candidate.kind),
        ("benchmark inputs SHA-256", baseline.digest, candidate.digest),
        ("trial count", baseline.trials, candidate.trials),
        ("clip count", baseline.clips, candidate.clips),
        ("language hint", baseline.language, candidate.language),
        ("reference-word count", baseline.reference_words, candidate.reference_words),
        ("non-speech control coverage", baseline.controls is None, candidate.controls is None),
    ):
        if left != right:
            raise ComparisonError(f"reports differ in {label}")
    if baseline.controls is not None and candidate.controls is not None:
        if baseline.controls[0] != candidate.controls[0] or baseline.controls[2] != candidate.controls[2]:
            raise ComparisonError("reports differ in non-speech control coverage")


def signed_delta(left: Decimal | int, right: Decimal | int, places: int = 0) -> str:
    delta = Decimal(right) - Decimal(left)
    return f"{delta:+.{places}f}"


def comparison_table(baseline: Report, candidate: Report, index: int) -> str:
    rows = [
        f"Pair {index}: {baseline.kind} corpus; {baseline.clips} clips; "
        f"{baseline.trials} trials/clip; hint {baseline.language}; "
        "matching input fingerprints.",
        "| Metric | Production pin | Candidate SDK | Delta |",
        "|---|---:|---:|---:|",
        f"| Conservative corpus WER | {baseline.corpus_wer}% "
        f"({baseline.corpus_errors}/{baseline.reference_words}) | "
        f"{candidate.corpus_wer}% ({candidate.corpus_errors}/{candidate.reference_words}) | "
        f"{signed_delta(baseline.corpus_wer, candidate.corpus_wer, 2)} pp; "
        f"{signed_delta(baseline.corpus_errors, candidate.corpus_errors)} errors |",
        f"| Worst speech-clip WER | {baseline.worst_wer}% | {candidate.worst_wer}% | "
        f"{signed_delta(baseline.worst_wer, candidate.worst_wer, 1)} pp |",
        f"| Final-word failures | {baseline.final_failures} | {candidate.final_failures} | "
        f"{signed_delta(baseline.final_failures, candidate.final_failures)} |",
        f"| Worst deletion run | {baseline.worst_deletion_run} | {candidate.worst_deletion_run} | "
        f"{signed_delta(baseline.worst_deletion_run, candidate.worst_deletion_run)} words |",
        f"| Mean speech p50 | {baseline.average_p50_ms} ms | {candidate.average_p50_ms} ms | "
        f"{signed_delta(baseline.average_p50_ms, candidate.average_p50_ms, 1)} ms |",
    ]
    if baseline.controls is not None and candidate.controls is not None:
        rows.append(
            f"| Non-speech emitting trials | {baseline.controls[1]}/{baseline.controls[2]} | "
            f"{candidate.controls[1]}/{candidate.controls[2]} | "
            f"{signed_delta(baseline.controls[1], candidate.controls[1])} trials |"
        )
    else:
        rows.append("| Non-speech controls | not reported | not reported | — |")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pair", nargs=2, action="append", required=True,
        metavar=("PRODUCTION_REPORT", "CANDIDATE_REPORT"),
        help="a pair of v3 Markdown regression reports; repeat for each corpus",
    )
    args = parser.parse_args(argv)
    pairs: list[tuple[Report, Report]] = []
    try:
        for baseline_path, candidate_path in args.pair:
            try:
                baseline = parse_report(Path(baseline_path).read_text(encoding="utf-8"))
                candidate = parse_report(Path(candidate_path).read_text(encoding="utf-8"))
            except (OSError, UnicodeError) as exc:
                raise ComparisonError("could not read a report") from exc
            validate_pair(baseline, candidate)
            pairs.append((baseline, candidate))
        pins = {(baseline.app_revision, candidate.revision) for baseline, candidate in pairs}
        if len(pins) != 1:
            raise ComparisonError("report pairs do not share one production/candidate revision pair")
    except ComparisonError as exc:
        print(f"SDK ASR comparison refused: {exc}", file=sys.stderr)
        return 1

    baseline_pin, candidate_pin = next(iter(pins))
    print(f"Production FluidAudio: {baseline_pin}")
    print(f"Candidate FluidAudio: {candidate_pin}")
    for index, (baseline, candidate) in enumerate(pairs, 1):
        print()
        print(comparison_table(baseline, candidate, index))
    print("\nComparison only: not a release verdict or native-app qualification. "
          "Check audited speech/non-speech references, absolute gates, and "
          "per-clip regressions before changing the app pin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
