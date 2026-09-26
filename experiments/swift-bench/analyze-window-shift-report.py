#!/usr/bin/env python3
"""Summarize paired window-position sensitivity in one public v3 report.

The shifted clips repeat the same speech, so this is a diagnostic rather than
an independent quality gate. No audio, references, hypotheses, or paths are
printed. A changed WER is not proof of a particular CoreML window mechanism.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
from pathlib import Path
import sys


def _load_sibling(filename: str, module_name: str):
    spec = importlib.util.spec_from_file_location(
        module_name, Path(__file__).with_name(filename)
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load benchmark helper")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


comparator = _load_sibling("compare-sdk-asr-reports.py", "presspeech_sdk_report")
fixtures = _load_sibling(
    "compose-public-window-shift-fixtures.py", "presspeech_window_fixtures"
)
inputs = _load_sibling("benchmark-inputs.py", "presspeech_benchmark_inputs")


class AnalysisError(ValueError):
    """The report cannot be paired with the supplied public fixtures."""


def _header(source: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in source.splitlines()[1:]:
        if line.startswith("## "):
            break
        if line.startswith("- ") and ": " in line:
            key, value = line[2:].split(": ", 1)
            if key in fields:
                raise AnalysisError("duplicate report header field")
            fields[key] = value
    return fields


def _manifest_rows(directory: Path) -> list[dict[str, str]]:
    try:
        expected_count = fixtures.validate_output(directory)
        with (directory / "manifest.tsv").open(
            "r", encoding="utf-8", newline=""
        ) as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
    except (OSError, UnicodeError, fixtures.FixtureError) as exc:
        raise AnalysisError("window-position fixtures failed validation") from exc
    if len(rows) != expected_count:
        raise AnalysisError("window-position manifest count changed")
    return sorted(rows, key=lambda row: row["fixture_id"])


def _input_receipts(directory: Path, rows: list[dict[str, str]]) -> tuple[str, str]:
    entries: list[dict[str, object]] = []
    for index, row in enumerate(rows, 1):
        stem = row["fixture_id"]
        audio = directory / f"{stem}.wav"
        reference = directory / f"{stem}.txt"
        entries.append({
            "audio": f"{index:06d}/audio.wav",
            "audio_suffix": ".wav",
            "audio_sha256": inputs.file_sha256(audio),
            "reference": f"{index:06d}/audio.txt",
            "reference_sha256": inputs.file_sha256(reference),
        })
    return inputs.corpus_digest(entries), inputs.order_digest(entries)


def analyze(source: str, directory: Path) -> str:
    if not source.startswith("# Presspeech Public-Speech Regression\n"):
        raise AnalysisError("only public speech regression reports are supported")
    fields = _header(source)
    if fields.get("Evidence scope") != (
        "repeated-speech window-position diagnostic; not an independent release or candidate gate"
    ):
        raise AnalysisError("report lacks window-position diagnostic scope")
    if fields.get("Fixture paths") not in ("redacted", "included"):
        raise AnalysisError("report has no recognized fixture-path mode")
    try:
        report = comparator.parse_report(source)
    except comparator.ComparisonError as exc:
        raise AnalysisError("report metrics or provenance failed validation") from exc
    if report.kind != "public" or report.dependency != "production-dependency":
        raise AnalysisError("report is not a production-pin public probe")
    if report.revision != report.app_revision:
        raise AnalysisError("report does not use the app's production SDK pin")

    rows = _manifest_rows(directory)
    if report.clips != len(rows):
        raise AnalysisError("report and fixture clip counts differ")
    corpus, order = _input_receipts(directory, rows)
    if (report.digest, report.order_digest) != (corpus, order):
        raise AnalysisError("report input or execution-order receipt differs from fixtures")

    by_source: dict[str, dict[int, tuple[int, comparator.ClipMetrics]]] = {}
    for position, (row, metric) in enumerate(zip(rows, report.clip_metrics), 1):
        if metric.reference_words <= 0:
            raise AnalysisError("window-position fixture lacks a scored speech reference")
        group = by_source.setdefault(row["source_id"], {})
        group[int(row["leading_ms"])] = (position, metric)

    lines = [
        "Window-position diagnostic (repeated speech; not a release gate)",
        f"{len(by_source)} sources, {len(rows)} variants, {report.trials} trials per variant; "
        "input and execution-order receipts match the current fixtures.",
        "Position: offset | worst word errors | first/final failure | longest deletion run | signal",
    ]
    worsened_sources = 0
    worsened_variants = 0
    for group in by_source.values():
        if 0 not in group:
            raise AnalysisError("window-position group lacks an unshifted reference")
        base_position, base = group[0]
        group_worsened = False
        for offset, (position, shifted) in sorted(group.items()):
            if offset == 0:
                continue
            if shifted.reference_words != base.reference_words:
                raise AnalysisError("paired variants have different scored references")
            worsened = (
                shifted.worst_errors > base.worst_errors
                or shifted.first_failure and not base.first_failure
                or shifted.final_failure and not base.final_failure
                or shifted.worst_deletion_run > base.worst_deletion_run
            )
            group_worsened |= worsened
            worsened_variants += worsened
            lines.append(
                f"{base_position:03d}->{position:03d}: {offset} ms | "
                f"{base.worst_errors}->{shifted.worst_errors} | "
                f"{int(base.first_failure)}/{int(base.final_failure)}->"
                f"{int(shifted.first_failure)}/{int(shifted.final_failure)} | "
                f"{base.worst_deletion_run}->{shifted.worst_deletion_run} | "
                f"{'worsened' if worsened else 'no measured worsening'}"
            )
        worsened_sources += group_worsened
    lines.append(
        f"Measured worsening: {worsened_variants}/{len(rows) - len(by_source)} "
        f"shifted variants across {worsened_sources}/{len(by_source)} sources."
    )
    lines.append(
        "A tie cannot rule out changed words or unmeasured acoustic loss; added silence "
        "also changes preprocessing and workload, so latency is not paired here."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-dir", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        source = args.report.read_text(encoding="utf-8")
        print(analyze(source, args.fixture_dir))
    except (OSError, UnicodeError, AnalysisError) as exc:
        # Paths and report contents might be private even when a public probe
        # was intended. Keep diagnostics generic rather than echoing errors.
        print(f"window-position analysis failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
