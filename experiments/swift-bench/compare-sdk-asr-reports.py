#!/usr/bin/env python3
"""Compare aggregate v3 reports from two FluidAudio revisions.

This is a comparison aid, not an ASR quality or release gate. It consumes the
Markdown artifacts emitted by run-real-dictation-regression.sh, requires both
matching input-set and execution-order receipts, and never prints fixture
names, reference text, hypotheses, or input paths. The benchmark harness
receipt must also match so an SDK change is not confounded with local code.
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
# The benchmark prints these tags before any unredacted hypothesis. Match
# only its result lines, never metric-like substrings in a transcript.
SPEECH_SCORE = re.compile(
    r"^[ \t]*(?:transcript:|•) \[WER ([0-9]+(?:\.[0-9]+)?)%\] "
    r"\[final-word retained=(true|false)[^\n]*?\]"
    r" \[first-word retained=(true|false)[^\n]*?\]"
    r"(?: \[critical-terms [^\]\n]*\])? "
    r"\[word-errors=([0-9]+) reference-words=([1-9][0-9]*)\] "
    r"\[max-reference-deletion-run=([0-9]+)\](?: |$)",
)
CONTROL_SCORE = re.compile(
    r"^[ \t]*(?:transcript:|•) \[WER [0-9]+(?:\.[0-9]+)?%\] "
    r"\[word-errors=[0-9]+ reference-words=0\] "
    r"\[max-reference-deletion-run=0\](?: |$)",
)
OUTPUT_ROW = re.compile(
    r"^    output: trial=([0-9]+)/([0-9]+) "
    r"empty=(true|false) characters=([0-9]+)$",
)
LATENCY_ROW = re.compile(
    r"^    latency:[ \t]+p50=[ \t]*([0-9]+(?:\.[0-9]+)?) ms(?: |$)",
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
    order_digest: str
    harness_digest: str
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
    clip_metrics: tuple[ClipMetrics, ...]


@dataclass(frozen=True)
class ClipMetrics:
    reference_words: int
    worst_errors: int
    worst_wer: Decimal
    final_failure: bool
    first_failure: bool
    worst_deletion_run: int
    p50_ms: Decimal
    emitting_trials: int


def parse_clip(body: str, trials: int) -> ClipMetrics:
    """Reconcile benchmark-owned receipts without reading hypothesis text."""
    lines = body.splitlines()
    if not any(line.startswith("- Reference: ") and
               line.endswith(" (WER enabled)") for line in lines):
        raise ComparisonError("clip lacks a scored reference")

    output_lines = [line for line in lines if line.startswith("    output:")]
    outputs = [OUTPUT_ROW.fullmatch(line) for line in output_lines]
    if len(outputs) != trials or any(match is None for match in outputs):
        raise ComparisonError("clip has incomplete trial receipts")
    emitting = 0
    for number, match in enumerate(outputs, 1):
        assert match is not None
        trial, total, empty, characters = match.groups()
        if int(trial) != number or int(total) != trials or (
                (empty == "true") != (int(characters) == 0)):
            raise ComparisonError("clip has inconsistent trial receipts")
        emitting += empty == "false"

    latencies = [LATENCY_ROW.match(line) for line in lines
                 if line.startswith("    latency:")]
    if len(latencies) != 1 or latencies[0] is None:
        raise ComparisonError("clip has incomplete latency metrics")
    p50 = Decimal(latencies[0].group(1))

    stable = [line for line in lines if line.startswith("    transcript:")]
    variable = [line for line in lines if line.startswith("    transcripts (")]
    bullets = [line for line in lines if line.startswith("      • ")]
    if len(stable) == 1 and not variable and not bullets:
        score_lines = stable
    elif not stable and len(variable) == 1 and bullets:
        distinct = re.fullmatch(r"    transcripts \(([0-9]+) distinct\):", variable[0])
        if distinct is None or int(distinct.group(1)) != len(bullets) or len(bullets) > trials:
            raise ComparisonError("clip has incomplete transcript metrics")
        score_lines = bullets
    else:
        raise ComparisonError("clip has incomplete transcript metrics")

    speech = [SPEECH_SCORE.match(line) for line in score_lines]
    if all(match is not None for match in speech):
        matches = [match for match in speech if match is not None]
        words = {int(match.group(5)) for match in matches}
        if len(words) != 1:
            raise ComparisonError("clip has inconsistent reference-word counts")
        reference_words = words.pop()
        for match in matches:
            displayed = Decimal(match.group(1))
            exact = Decimal(100 * int(match.group(4))) / Decimal(reference_words)
            if abs(displayed - exact) > Decimal("0.051"):
                raise ComparisonError("clip WER conflicts with exact word counts")
        return ClipMetrics(
            reference_words=reference_words,
            worst_errors=max(int(match.group(4)) for match in matches),
            worst_wer=max(Decimal(match.group(1)) for match in matches),
            final_failure=any(match.group(2) == "false" for match in matches),
            first_failure=any(match.group(3) == "false" for match in matches),
            worst_deletion_run=max(int(match.group(6)) for match in matches),
            p50_ms=p50, emitting_trials=emitting,
        )
    if all(CONTROL_SCORE.match(line) is not None for line in score_lines):
        return ClipMetrics(0, 0, Decimal(0), False, False, 0, p50, emitting)
    raise ComparisonError("clip has incomplete or mixed score metrics")


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
        "Baseline dependency", "Benchmark inputs SHA-256",
        "Benchmark order SHA-256", "Benchmark harness SHA-256",
        "Trials per clip",
        "Parakeet TDT v3 language/script hint", "Clips",
    )
    if any(key not in fields for key in required):
        raise ComparisonError("report is missing comparison provenance")
    if fields["Backend"] != "v3":
        raise ComparisonError("comparison requires the explicit v3 backend")
    revision = fields["FluidAudio revision"]
    app_revision = fields["App FluidAudio revision"]
    digest = fields["Benchmark inputs SHA-256"]
    order = fields["Benchmark order SHA-256"]
    harness = fields["Benchmark harness SHA-256"]
    if not REVISION.fullmatch(revision) or not REVISION.fullmatch(app_revision):
        raise ComparisonError("report has an invalid FluidAudio revision")
    if not DIGEST.fullmatch(digest):
        raise ComparisonError("report has an invalid input fingerprint")
    if not DIGEST.fullmatch(order):
        raise ComparisonError("report has an invalid input-order fingerprint")
    if not DIGEST.fullmatch(harness):
        raise ComparisonError("report has an invalid benchmark harness fingerprint")
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
    summary_match = one(
        re.compile(r"^## Summary\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL),
        source, "summary section",
    )
    summary = summary_match.group(1)
    row = one(SUMMARY_ROW, summary, "v3 summary row")
    corpus = one(CORPUS_ROW, summary, "conservative corpus WER")
    if int(row.group(1)) != clips:
        raise ComparisonError("summary clip count differs from report header")

    # A summary can still look plausible when a Markdown report was truncated
    # or hand-edited. Bind it to every numbered clip and each trial receipt.
    before_summary = source[:summary_match.start()]
    headings = list(re.finditer(r"^## ([^\n]+)$", before_summary, re.MULTILINE))
    if len(headings) != clips:
        raise ComparisonError("report clip sections differ from header")
    clip_metrics = []
    for index, heading in enumerate(headings, 1):
        label = re.fullmatch(r"(?:Clip )?([0-9]{3,})(?:-[^\n]*)?", heading.group(1))
        if label is None or int(label.group(1)) != index:
            raise ComparisonError("report clip numbering is incomplete")
        end = headings[index].start() if index < clips else len(before_summary)
        clip_metrics.append(parse_clip(before_summary[heading.end():end], trials))
    speech = [clip for clip in clip_metrics if clip.reference_words > 0]
    controls_found = [clip for clip in clip_metrics if clip.reference_words == 0]
    if not speech:
        raise ComparisonError("report has no scored speech observations")

    corpus_wer = Decimal(corpus.group(1))
    errors = int(corpus.group(2))
    words = int(corpus.group(3))
    # The published report rounds to two decimal places. Reject internally
    # inconsistent rows instead of turning a hand-edited value into evidence.
    if abs(corpus_wer - Decimal(100 * errors) / Decimal(words)) > Decimal("0.011"):
        raise ComparisonError("corpus WER conflicts with exact word counts")
    if (errors != sum(clip.worst_errors for clip in speech) or
            words != sum(clip.reference_words for clip in speech)):
        raise ComparisonError("corpus WER conflicts with clip scores")
    mean_wer = sum((clip.worst_wer for clip in speech), Decimal(0)) / len(speech)
    mean_p50 = sum((clip.p50_ms for clip in speech), Decimal(0)) / len(speech)
    if (abs(Decimal(row.group(2)) - mean_wer) > Decimal("0.011") or
            Decimal(row.group(3)) != max(clip.worst_wer for clip in speech) or
            int(row.group(4)) != sum(clip.final_failure for clip in speech) or
            abs(Decimal(row.group(5)) - mean_p50) > Decimal("0.051")):
        raise ComparisonError("summary conflicts with clip scores or latency")

    control_matches = list(CONTROL_ROW.finditer(summary))
    if len(control_matches) > 1:
        raise ComparisonError("report has duplicate non-speech control receipts")
    controls = None
    if bool(control_matches) != bool(controls_found):
        raise ComparisonError("non-speech control receipt does not match clips")
    if controls_found:
        control = control_matches[0]
        controls = tuple(int(control.group(index)) for index in (1, 2, 3))
        count, emitted, measured = controls
        if (count != len(controls_found) or measured != count * trials or
                emitted != sum(clip.emitting_trials for clip in controls_found)):
            raise ComparisonError("non-speech control receipt is inconsistent")

    return Report(
        kind=kind, revision=revision, app_revision=app_revision,
        dependency=dependency, digest=digest, order_digest=order,
        harness_digest=harness,
        trials=trials, clips=clips,
        language=language, corpus_wer=corpus_wer, corpus_errors=errors,
        reference_words=words, worst_wer=Decimal(row.group(3)),
        final_failures=int(row.group(4)),
        average_p50_ms=Decimal(row.group(5)),
        worst_deletion_run=max(clip.worst_deletion_run for clip in speech),
        controls=controls,
        clip_metrics=tuple(clip_metrics),
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
        ("benchmark order SHA-256", baseline.order_digest, candidate.order_digest),
        ("benchmark harness SHA-256", baseline.harness_digest, candidate.harness_digest),
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
    for left, right in zip(baseline.clip_metrics, candidate.clip_metrics):
        if left.reference_words != right.reference_words:
            raise ComparisonError("reports differ in numbered clip reference coverage")


def signed_delta(left: Decimal | int, right: Decimal | int, places: int = 0) -> str:
    delta = Decimal(right) - Decimal(left)
    return f"{delta:+.{places}f}"


def comparison_table(baseline: Report, candidate: Report, index: int) -> str:
    speech_pairs = [
        (position, left, right)
        for position, (left, right) in enumerate(
            zip(baseline.clip_metrics, candidate.clip_metrics), 1
        ) if left.reference_words > 0
    ]
    control_pairs = [
        (position, left, right)
        for position, (left, right) in enumerate(
            zip(baseline.clip_metrics, candidate.clip_metrics), 1
        ) if left.reference_words == 0
    ]
    worse_errors = sum(right.worst_errors > left.worst_errors
                       for _, left, right in speech_pairs)
    better_errors = sum(right.worst_errors < left.worst_errors
                        for _, left, right in speech_pairs)
    new_final_failures = sum(not left.final_failure and right.final_failure
                             for _, left, right in speech_pairs)
    recovered_final_words = sum(left.final_failure and not right.final_failure
                                for _, left, right in speech_pairs)
    new_first_failures = sum(not left.first_failure and right.first_failure
                             for _, left, right in speech_pairs)
    recovered_first_words = sum(left.first_failure and not right.first_failure
                                for _, left, right in speech_pairs)
    baseline_first_failures = sum(clip.first_failure for _, clip, _ in speech_pairs)
    candidate_first_failures = sum(clip.first_failure for _, _, clip in speech_pairs)
    worse_deletion_runs = sum(right.worst_deletion_run > left.worst_deletion_run
                              for _, left, right in speech_pairs)
    slower = sum(right.p50_ms > left.p50_ms for _, left, right in speech_pairs)
    faster = sum(right.p50_ms < left.p50_ms for _, left, right in speech_pairs)
    new_control_emissions = sum(right.emitting_trials > left.emitting_trials
                                for _, left, right in control_pairs)
    resolved_control_emissions = sum(right.emitting_trials < left.emitting_trials
                                     for _, left, right in control_pairs)
    quality_positions = [
        position for position, left, right in speech_pairs
        if (right.worst_errors > left.worst_errors
            or (right.final_failure and not left.final_failure)
            or (right.first_failure and not left.first_failure)
            or right.worst_deletion_run > left.worst_deletion_run)
    ]
    control_positions = [
        position for position, left, right in control_pairs
        if right.emitting_trials > left.emitting_trials
    ]

    rows = [
        f"Pair {index}: {baseline.kind} corpus; {baseline.clips} clips; "
        f"{baseline.trials} trials/clip; hint {baseline.language}; "
        "matching input, execution-order, and benchmark-harness fingerprints.",
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
        f"| First-word failures | {baseline_first_failures} | {candidate_first_failures} | "
        f"{signed_delta(baseline_first_failures, candidate_first_failures)} |",
        f"| Worst deletion run | {baseline.worst_deletion_run} | {candidate.worst_deletion_run} | "
        f"{signed_delta(baseline.worst_deletion_run, candidate.worst_deletion_run)} words |",
        f"| Mean speech p50 | {baseline.average_p50_ms} ms | {candidate.average_p50_ms} ms | "
        f"{signed_delta(baseline.average_p50_ms, candidate.average_p50_ms, 1)} ms |",
        f"| Paired speech clips: worst-trial errors | — | — | "
        f"{worse_errors} worse; {better_errors} better |",
        f"| Paired speech clips: final word | — | — | "
        f"{new_final_failures} newly failed; {recovered_final_words} recovered |",
        f"| Paired speech clips: first word | — | — | "
        f"{new_first_failures} newly failed; {recovered_first_words} recovered |",
        f"| Paired speech clips: deletion run | — | — | "
        f"{worse_deletion_runs} worse |",
        f"| Paired speech clips: p50 latency | — | — | "
        f"{slower} slower; {faster} faster |",
    ]
    if baseline.controls is not None and candidate.controls is not None:
        rows.append(
            f"| Non-speech emitting trials | {baseline.controls[1]}/{baseline.controls[2]} | "
            f"{candidate.controls[1]}/{candidate.controls[2]} | "
            f"{signed_delta(baseline.controls[1], candidate.controls[1])} trials |"
        )
        rows.append(
            f"| Paired non-speech controls | — | — | "
            f"{new_control_emissions} newly worse; {resolved_control_emissions} improved |"
        )
    else:
        rows.append("| Non-speech controls | not reported | not reported | — |")

    def positions(indices: list[int]) -> str:
        return ", ".join(f"{position:03d}" for position in indices) if indices else "none"

    rows.append(
        "Review numbered positions with quality regressions: "
        + positions(quality_positions) + "."
    )
    if control_pairs:
        rows.append(
            "Review numbered non-speech positions with new emissions: "
            + positions(control_positions) + "."
        )
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
