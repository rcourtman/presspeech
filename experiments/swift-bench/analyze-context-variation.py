#!/usr/bin/env python3
"""Analyse paired context fixtures from a Presspeech model comparison TSV.

The companion composer emits probe, context, and probe+context triplets.  This
script compares the exact word-error counts for those three clips and reports
positive composition excess::

    max(0, combined errors - probe errors - context errors)

A positive value means the concatenation introduced errors that neither
component exhibited alone.  Production uses its best observed trial and the
candidate uses its worst, matching Presspeech's conservative model-candidate
policy.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import tempfile


MANIFEST_FIELDS = [
    "pair_id",
    "probe_fixture",
    "context_fixture",
    "combined_fixture",
    "probe_source",
    "context_source",
    "probe_duration_seconds",
    "context_duration_seconds",
    "combined_duration_seconds",
    "probe_reference_words",
    "context_reference_words",
    "combined_reference_words",
    "probe_audio_sha256",
    "context_audio_sha256",
    "combined_audio_sha256",
    "probe_reference_sha256",
    "context_reference_sha256",
    "combined_reference_sha256",
]

RESULT_FIELDS = [
    "clip_id",
    "backend",
    "backend_setting",
    "max_wer_percent",
    "final_word_retained",
    "p50_ms",
    "worst_word_errors",
    "reference_words",
    "best_word_errors",
    "context_manifest_sha256",
]

ROLES = ("probe", "context", "combined")
SAFE_ID = re.compile(r"^[A-Za-z0-9_.+-]+$")
MARKER_NAME = ".presspeech-public-context-fixtures"
MARKER_TEXT = "Presspeech generated public context-variation speech fixtures\n"


class AnalysisError(Exception):
    """The fixture manifest or comparison result is incomplete or inconsistent."""


@dataclass(frozen=True)
class PairDefinition:
    pair_id: str
    fixtures: dict[str, str]
    reference_words: dict[str, int]


@dataclass(frozen=True)
class BackendMetric:
    errors: int
    reference_words: int


@dataclass(frozen=True)
class PairAssessment:
    pair_id: str
    baseline_errors: dict[str, int]
    candidate_errors: dict[str, int]
    baseline_excess: int
    candidate_excess: int
    baseline_penalty: int
    candidate_penalty: int
    regressed_roles: tuple[str, ...]
    excess_regressed: bool

    @property
    def regressed(self) -> bool:
        return bool(self.regressed_roles) or self.excess_regressed

    @property
    def improved(self) -> bool:
        if self.regressed:
            return False
        return (
            self.candidate_penalty < self.baseline_penalty
            or any(
                self.candidate_errors[role] < self.baseline_errors[role]
                for role in ROLES
            )
        )

    @property
    def status(self) -> str:
        if self.regressed:
            return "regressed"
        if self.improved:
            return "improved"
        return "stable"


def parse_positive_int(value: str, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise AnalysisError(f"{label} is not an integer") from exc
    if parsed <= 0:
        raise AnalysisError(f"{label} must be positive")
    return parsed


def parse_nonnegative_int(value: str, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise AnalysisError(f"{label} is not an integer") from exc
    if parsed < 0:
        raise AnalysisError(f"{label} must be non-negative")
    return parsed


def validate_identifier(value: str, label: str) -> str:
    if not value or not SAFE_ID.fullmatch(value):
        raise AnalysisError(f"{label} contains unsafe characters")
    return value


def load_manifest(path: Path) -> list[PairDefinition]:
    if path.is_symlink() or not path.is_file():
        raise AnalysisError(f"context manifest is missing or unsafe: {path}")
    marker = path.parent / MARKER_NAME
    if (
        marker.is_symlink()
        or not marker.is_file()
        or marker.read_bytes() != MARKER_TEXT.encode("utf-8")
    ):
        raise AnalysisError(f"context manifest is not in composer-owned output: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames != MANIFEST_FIELDS:
            raise AnalysisError("context manifest has an unexpected schema")
        rows = list(reader)
    if not rows:
        raise AnalysisError("context manifest contains no pairs")

    pair_ids: set[str] = set()
    fixture_ids: set[str] = set()
    source_ids: set[str] = set()
    pairs: list[PairDefinition] = []
    for row in rows:
        pair_id = validate_identifier(row["pair_id"], "pair id")
        if pair_id in pair_ids:
            raise AnalysisError(f"duplicate context pair id: {pair_id}")
        pair_ids.add(pair_id)

        probe_source = validate_identifier(row["probe_source"], "probe source id")
        context_source = validate_identifier(row["context_source"], "context source id")
        if probe_source == context_source:
            raise AnalysisError(f"pair {pair_id} reuses one source for both roles")
        if probe_source in source_ids or context_source in source_ids:
            raise AnalysisError(f"source utterance reused across context pairs: {pair_id}")
        source_ids.update((probe_source, context_source))

        fixtures: dict[str, str] = {}
        reference_words: dict[str, int] = {}
        for role in ROLES:
            fixture = validate_identifier(row[f"{role}_fixture"], f"{role} fixture id")
            if fixture in fixture_ids:
                raise AnalysisError(f"duplicate context fixture id: {fixture}")
            fixture_ids.add(fixture)
            fixtures[role] = fixture
            reference_words[role] = parse_positive_int(
                row[f"{role}_reference_words"], f"{fixture} reference words"
            )
        if reference_words["combined"] != (
            reference_words["probe"] + reference_words["context"]
        ):
            raise AnalysisError(f"combined reference words do not add up for {pair_id}")
        pairs.append(PairDefinition(pair_id, fixtures, reference_words))
    return pairs


def resolve_fixture_id(clip_id: str, fixture_ids: set[str]) -> str:
    if clip_id in fixture_ids:
        return clip_id
    if clip_id.isdigit():
        clip_number = int(clip_id)
        ordered_fixtures = sorted(fixture_ids)
        if 1 <= clip_number <= len(ordered_fixtures):
            return ordered_fixtures[clip_number - 1]
    matches = [
        fixture
        for fixture in fixture_ids
        if clip_id.endswith(f"-{fixture}")
        and clip_id[: -(len(fixture) + 1)].isdigit()
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise AnalysisError(
            f"comparison clip id does not identify a context fixture: {clip_id}"
        )
    raise AnalysisError(f"comparison clip id is ambiguous: {clip_id}")


def load_results(
    path: Path,
    pairs: list[PairDefinition],
    baseline_backend: str,
    candidate_backend: str,
    manifest_sha256: str,
) -> dict[tuple[str, str], BackendMetric]:
    if path.is_symlink() or not path.is_file():
        raise AnalysisError(f"model-comparison TSV is missing or unsafe: {path}")
    fixture_ids = {
        fixture
        for pair in pairs
        for fixture in pair.fixtures.values()
    }
    expected_backends = {baseline_backend, candidate_backend}
    metrics: dict[tuple[str, str], BackendMetric] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames != RESULT_FIELDS:
            raise AnalysisError("model-comparison TSV lacks the context corpus binding or has an unexpected schema; rerun the context comparison")
        for row in reader:
            if row["context_manifest_sha256"] != manifest_sha256:
                raise AnalysisError("model-comparison results belong to a different context corpus")
            backend = row["backend"]
            if backend not in expected_backends:
                raise AnalysisError(f"unexpected backend in model-comparison TSV: {backend}")
            fixture = resolve_fixture_id(row["clip_id"], fixture_ids)
            key = (fixture, backend)
            if key in metrics:
                raise AnalysisError(
                    f"duplicate model-comparison row for {fixture} / {backend}"
                )
            best_errors = parse_nonnegative_int(
                row["best_word_errors"], f"{fixture} / {backend} best_word_errors"
            )
            worst_errors = parse_nonnegative_int(
                row["worst_word_errors"], f"{fixture} / {backend} worst_word_errors"
            )
            if best_errors > worst_errors:
                raise AnalysisError(
                    f"best errors exceed worst errors for {fixture} / {backend}"
                )
            metrics[key] = BackendMetric(
                errors=(best_errors if backend == baseline_backend else worst_errors),
                reference_words=parse_positive_int(
                    row["reference_words"], f"{fixture} / {backend} reference words"
                ),
            )

    expected_rows = len(fixture_ids) * 2
    if len(metrics) != expected_rows:
        raise AnalysisError(
            f"model-comparison TSV contains {len(metrics)} usable rows; expected {expected_rows}"
        )
    return metrics


def assess_pairs(
    pairs: list[PairDefinition],
    metrics: dict[tuple[str, str], BackendMetric],
    baseline_backend: str,
    candidate_backend: str,
) -> list[PairAssessment]:
    assessments: list[PairAssessment] = []
    for pair in pairs:
        backend_errors: dict[str, dict[str, int]] = {
            baseline_backend: {},
            candidate_backend: {},
        }
        for backend in (baseline_backend, candidate_backend):
            for role in ROLES:
                fixture = pair.fixtures[role]
                try:
                    metric = metrics[(fixture, backend)]
                except KeyError as exc:
                    raise AnalysisError(f"missing result for {fixture} / {backend}") from exc
                expected_words = pair.reference_words[role]
                if metric.reference_words != expected_words:
                    raise AnalysisError(
                        f"reference-word mismatch for {fixture} / {backend}: "
                        f"manifest={expected_words}, results={metric.reference_words}"
                    )
                backend_errors[backend][role] = metric.errors

        baseline_errors = backend_errors[baseline_backend]
        candidate_errors = backend_errors[candidate_backend]
        baseline_excess = (
            baseline_errors["combined"]
            - baseline_errors["probe"]
            - baseline_errors["context"]
        )
        candidate_excess = (
            candidate_errors["combined"]
            - candidate_errors["probe"]
            - candidate_errors["context"]
        )
        regressed_roles = tuple(
            role
            for role in ROLES
            if candidate_errors[role] > baseline_errors[role]
        )
        assessments.append(
            PairAssessment(
                pair_id=pair.pair_id,
                baseline_errors=baseline_errors,
                candidate_errors=candidate_errors,
                baseline_excess=baseline_excess,
                candidate_excess=candidate_excess,
                baseline_penalty=max(0, baseline_excess),
                candidate_penalty=max(0, candidate_excess),
                regressed_roles=regressed_roles,
                excess_regressed=max(0, candidate_excess) > max(0, baseline_excess),
            )
        )
    return assessments


def format_signed(value: int) -> str:
    return f"{value:+d}"


def render_report(
    assessments: list[PairAssessment],
    baseline_backend: str,
    candidate_backend: str,
    manifest_sha256: str,
    results_sha256: str,
) -> tuple[str, int]:
    improved = sum(assessment.improved for assessment in assessments)
    stable = sum(assessment.status == "stable" for assessment in assessments)
    regressed = sum(assessment.regressed for assessment in assessments)
    baseline_penalty_pairs = sum(assessment.baseline_penalty > 0 for assessment in assessments)
    candidate_penalty_pairs = sum(assessment.candidate_penalty > 0 for assessment in assessments)
    baseline_penalty_total = sum(assessment.baseline_penalty for assessment in assessments)
    candidate_penalty_total = sum(assessment.candidate_penalty for assessment in assessments)
    verdict = "passes" if regressed == 0 else "blocked"

    lines = [
        "# Presspeech Context-Variation Analysis",
        "",
        f"- Baseline backend: `{baseline_backend}` (best observed trial)",
        f"- Candidate backend: `{candidate_backend}` (worst observed trial)",
        f"- Disjoint source pairs: {len(assessments)}",
        f"- Context manifest SHA-256: `{manifest_sha256}`",
        f"- Model-comparison TSV SHA-256: `{results_sha256}`",
        "- Metric: positive composition excess = max(0, combined errors - "
        "probe errors - context errors)",
        "",
        "> This is supplementary context-sensitivity evidence. Probe and context",
        "> audio is intentionally repeated inside each combined clip, so these rows",
        "> do not satisfy Presspeech's independent-corpus model-candidate floor.",
        "",
        "## Per-Pair Results",
        "",
        "Errors are shown as `probe / context / combined`. A candidate pair is",
        "regressed if any component has more errors than the conservative baseline",
        "or if its positive composition excess increases.",
        "",
        "| Pair | Baseline errors | Baseline excess | Candidate errors | "
        "Candidate excess | Status | Regression detail |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for assessment in assessments:
        baseline = " / ".join(str(assessment.baseline_errors[role]) for role in ROLES)
        candidate = " / ".join(str(assessment.candidate_errors[role]) for role in ROLES)
        details = list(assessment.regressed_roles)
        if assessment.excess_regressed:
            details.append("composition-excess")
        lines.append(
            f"| `{assessment.pair_id}` | {baseline} | "
            f"{format_signed(assessment.baseline_excess)} "
            f"(penalty {assessment.baseline_penalty}) | "
            f"{candidate} | "
            f"{format_signed(assessment.candidate_excess)} "
            f"(penalty {assessment.candidate_penalty}) | "
            f"{assessment.status} | {', '.join(details)} |"
        )

    lines.extend(
        [
            "",
            "## Summary",
            "",
            "| Measure | Baseline | Candidate |",
            "|---|---:|---:|",
            "| Pairs with positive composition excess | "
            f"{baseline_penalty_pairs} | {candidate_penalty_pairs} |",
            "| Total positive composition-excess errors | "
            f"{baseline_penalty_total} | {candidate_penalty_total} |",
            "",
            f"- Improved pairs: {improved}",
            f"- Stable pairs: {stable}",
            f"- Regressed pairs: {regressed}",
            f"- Context non-regression verdict: **{verdict}**",
            "",
            "A pass means only that this paired corpus found no conservative",
            "context regression. General multilingual, private-dictation, latency,",
            "memory, and long-form gates remain separate requirements.",
            "",
        ]
    )
    return "\n".join(lines), regressed


def write_report(path: Path, report: str, force: bool) -> None:
    if path.exists() or path.is_symlink():
        if not force:
            raise AnalysisError(f"output already exists: {path}")
        if path.is_symlink() or not path.is_file():
            raise AnalysisError(f"refusing to replace unsafe output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(report)
    try:
        if force:
            os.replace(temporary, path)
        else:
            # Publish without a check-then-replace race: a report created by
            # another process after the preflight must remain untouched.
            os.link(temporary, path)
            temporary.unlink()
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def manifest_test_row(pair_number: int) -> dict[str, str]:
    pair_id = f"context-{pair_number:03d}"
    row = {field: "0" * 64 for field in MANIFEST_FIELDS}
    row.update(
        {
            "pair_id": pair_id,
            "probe_fixture": f"{pair_id}-probe",
            "context_fixture": f"{pair_id}-context",
            "combined_fixture": f"{pair_id}-combined",
            "probe_source": f"source-{pair_number:03d}-a",
            "context_source": f"source-{pair_number:03d}-b",
            "probe_duration_seconds": "4.000000",
            "context_duration_seconds": "5.000000",
            "combined_duration_seconds": "9.000000",
            "probe_reference_words": "10",
            "context_reference_words": "12",
            "combined_reference_words": "22",
        }
    )
    return row


def write_test_results(
    path: Path,
    error_sets: dict[tuple[int, str], tuple[int, int, int]],
    redacted_ids: bool = False,
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_FIELDS, delimiter="\t")
        writer.writeheader()
        for pair_number in sorted({key[0] for key in error_sets}):
            pair_id = f"context-{pair_number:03d}"
            for role, words in (("probe", 10), ("context", 12), ("combined", 22)):
                role_index = ROLES.index(role)
                if redacted_ids:
                    sorted_role_number = {"combined": 1, "context": 2, "probe": 3}[role]
                    clip_id = f"{((pair_number - 1) * 3) + sorted_role_number:03d}"
                else:
                    clip_id = f"001-{pair_id}-{role}"
                for backend in ("v3", "v3-int8-v2"):
                    errors = error_sets[(pair_number, backend)][role_index]
                    worst_errors = errors if backend == "v3-int8-v2" else errors + 1
                    best_errors = errors if backend == "v3" else max(0, errors - 1)
                    writer.writerow(
                        {
                            "clip_id": clip_id,
                            "backend": backend,
                            "backend_setting": "test",
                            "max_wer_percent": "0.0",
                            "final_word_retained": "true",
                            "p50_ms": "1.0",
                            "worst_word_errors": str(worst_errors),
                            "reference_words": str(words),
                            "best_word_errors": str(best_errors),
                            "context_manifest_sha256": hashlib.sha256((path.parent / "manifest.tsv").read_bytes()).hexdigest(),
                        }
                    )


def run_self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="presspeech-context-analysis-self-test-") as tmp:
        root = Path(tmp)
        manifest = root / "manifest.tsv"
        (root / MARKER_NAME).write_text(MARKER_TEXT, encoding="utf-8")
        with manifest.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS, delimiter="\t")
            writer.writeheader()
            writer.writerow(manifest_test_row(1))
            writer.writerow(manifest_test_row(2))

        marker = root / MARKER_NAME
        marker.write_text("not composer output\n", encoding="utf-8")
        try:
            load_manifest(manifest)
        except AnalysisError as exc:
            if "not in composer-owned output" not in str(exc):
                raise
        else:
            raise AssertionError("manifest with an invalid ownership marker passed")
        marker.write_text(MARKER_TEXT, encoding="utf-8")

        results = root / "results.tsv"
        write_test_results(
            results,
            {
                (1, "v3"): (0, 0, 1),
                (1, "v3-int8-v2"): (0, 0, 0),
                (2, "v3"): (1, 0, 1),
                (2, "v3-int8-v2"): (1, 0, 2),
            },
        )
        pairs = load_manifest(manifest)
        manifest_digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
        fixture_ids = {
            fixture for pair in pairs for fixture in pair.fixtures.values()
        }
        ordered_fixtures = sorted(fixture_ids)
        if resolve_fixture_id("001", fixture_ids) != ordered_fixtures[0]:
            raise AssertionError("redacted first clip id did not resolve by fixture order")
        if resolve_fixture_id("006", fixture_ids) != ordered_fixtures[5]:
            raise AssertionError("redacted final clip id did not resolve by fixture order")
        metrics = load_results(results, pairs, "v3", "v3-int8-v2", manifest_digest)
        assessments = assess_pairs(pairs, metrics, "v3", "v3-int8-v2")
        redacted_results = root / "redacted-results.tsv"
        write_test_results(
            redacted_results,
            {
                (1, "v3"): (0, 0, 1),
                (1, "v3-int8-v2"): (0, 0, 0),
                (2, "v3"): (1, 0, 1),
                (2, "v3-int8-v2"): (1, 0, 2),
            },
            redacted_ids=True,
        )
        redacted_metrics = load_results(
            redacted_results, pairs, "v3", "v3-int8-v2", manifest_digest
        )
        if assess_pairs(
            pairs, redacted_metrics, "v3", "v3-int8-v2"
        ) != assessments:
            raise AssertionError("redacted clip ids changed context analysis")
        report, regressions = render_report(
            assessments,
            "v3",
            "v3-int8-v2",
            hashlib.sha256(manifest.read_bytes()).hexdigest(),
            hashlib.sha256(results.read_bytes()).hexdigest(),
        )
        if regressions != 1 or "Improved pairs: 1" not in report:
            raise AssertionError("context analysis did not classify paired changes")
        if "Context non-regression verdict: **blocked**" not in report:
            raise AssertionError("context analysis omitted the blocked verdict")
        if assessments[0].baseline_penalty != 1 or assessments[0].candidate_penalty != 0:
            raise AssertionError("context composition penalty was calculated incorrectly")

        output = root / "report.md"
        write_report(output, report, False)
        try:
            write_report(output, report, False)
        except AnalysisError as exc:
            if "already exists" not in str(exc):
                raise
        else:
            raise AssertionError("context analysis replaced a report without --force")
        write_report(output, "replacement report\n", True)
        if output.read_text(encoding="utf-8") != "replacement report\n":
            raise AssertionError("--force did not replace the context analysis report")

        mismatch = root / "mismatch.tsv"
        mismatch.write_text(results.read_text(encoding="utf-8"), encoding="utf-8")
        text = mismatch.read_text(encoding="utf-8")
        mismatch.write_text(text.replace("\t10\t", "\t11\t", 1), encoding="utf-8")
        try:
            mismatched_metrics = load_results(mismatch, pairs, "v3", "v3-int8-v2", manifest_digest)
            assess_pairs(pairs, mismatched_metrics, "v3", "v3-int8-v2")
        except AnalysisError as exc:
            if "reference-word mismatch" not in str(exc):
                raise
        else:
            raise AssertionError("reference-word mismatch passed context analysis")

        invalid_order = root / "invalid-order.tsv"
        with results.open("r", encoding="utf-8", newline="") as source:
            invalid_rows = list(csv.DictReader(source, delimiter="\t"))
        invalid_rows[0]["best_word_errors"] = str(
            int(invalid_rows[0]["worst_word_errors"]) + 1
        )
        with invalid_order.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=RESULT_FIELDS, delimiter="\t")
            writer.writeheader()
            writer.writerows(invalid_rows)
        try:
            load_results(invalid_order, pairs, "v3", "v3-int8-v2", manifest_digest)
        except AnalysisError as exc:
            if "best errors exceed worst errors" not in str(exc):
                raise
        else:
            raise AssertionError("inverted best/worst errors passed context analysis")

    print("context variation analysis self-test passed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", help="context fixture manifest.tsv")
    parser.add_argument("--results", help="run-real-model-comparison results.tsv")
    parser.add_argument(
        "--baseline-backend", default="v3", help="baseline backend id (default: v3)"
    )
    parser.add_argument(
        "--candidate-backend",
        default="v3-int8-v2",
        help="candidate backend id (default: v3-int8-v2)",
    )
    parser.add_argument("--output", help="write Markdown report instead of stdout")
    parser.add_argument("--force", action="store_true", help="replace an existing regular report")
    parser.add_argument(
        "--require-nonregression",
        action="store_true",
        help="exit unsuccessfully when any conservative pair regresses",
    )
    parser.add_argument("--self-test", action="store_true", help="run parser/scoring tests")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        run_self_test()
        return 0
    if not args.manifest or not args.results:
        raise AnalysisError("--manifest and --results are required")
    baseline_backend = validate_identifier(args.baseline_backend, "baseline backend")
    candidate_backend = validate_identifier(args.candidate_backend, "candidate backend")
    if baseline_backend == candidate_backend:
        raise AnalysisError("baseline and candidate backends must differ")

    manifest_digest = hashlib.sha256(Path(args.manifest).read_bytes()).hexdigest()
    results_digest = hashlib.sha256(Path(args.results).read_bytes()).hexdigest()
    pairs = load_manifest(Path(args.manifest))
    metrics = load_results(
        Path(args.results), pairs, baseline_backend, candidate_backend, manifest_digest
    )
    assessments = assess_pairs(
        pairs, metrics, baseline_backend, candidate_backend
    )
    if (hashlib.sha256(Path(args.manifest).read_bytes()).hexdigest() != manifest_digest
            or hashlib.sha256(Path(args.results).read_bytes()).hexdigest() != results_digest):
        raise AnalysisError("context analysis inputs changed while being read")
    report, regressions = render_report(
        assessments,
        baseline_backend,
        candidate_backend,
        manifest_digest,
        results_digest,
    )
    if args.output:
        write_report(Path(args.output), report, args.force)
        print(f"context analysis report: {args.output}")
    else:
        print(report, end="")
    if args.require_nonregression and regressions:
        raise AnalysisError(
            f"context non-regression blocked: {regressions} pair(s) regressed"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AnalysisError, OSError) as exc:
        raise SystemExit(f"error: {exc}") from exc
