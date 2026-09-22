#!/usr/bin/env python3
"""Compose public ASR fixtures that vary trailing speech context.

Each pair produces three files from two distinct source utterances:

* ``probe``: the first utterance by itself;
* ``context``: the trailing utterance by itself; and
* ``combined``: the exact probe and context payloads concatenated.

The combined clip remains below one 15-second Parakeet encoder window.  Running
all three clips makes it possible to distinguish ordinary recognition errors
from errors introduced only when unchanged speech receives additional right
context.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import os
from pathlib import Path
import shutil
import struct
import tempfile
import unicodedata


MARKER_NAME = ".presspeech-public-context-fixtures"
MARKER_TEXT = "Presspeech generated public context-variation speech fixtures\n"
MANIFEST_NAME = "manifest.tsv"
DEFAULT_PAIR_COUNT = 10
DEFAULT_MIN_CONTEXT_SECONDS = 4.0
DEFAULT_MAX_COMBINED_SECONDS = 14.5
ENCODER_WINDOW_SECONDS = 15.0
SUPPORTED_BENCH_AUDIO_SUFFIXES = {
    ".wav",
    ".aiff",
    ".aif",
    ".caf",
    ".m4a",
    ".mp3",
    ".flac",
}

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


class FixtureError(Exception):
    """A source or generated context fixture set is unsafe or malformed."""


def _chunk(chunk_id: bytes, payload: bytes) -> bytes:
    padding = b"\0" if len(payload) % 2 else b""
    return chunk_id + struct.pack("<I", len(payload)) + payload + padding


def read_wave(path: Path) -> tuple[bytes, bytes, int, int, float]:
    """Return fmt bytes, audio bytes, format code, frame count, and duration."""
    blob = path.read_bytes()
    if len(blob) < 12 or blob[:4] != b"RIFF" or blob[8:12] != b"WAVE":
        raise FixtureError(f"not a RIFF/WAVE file: {path}")

    riff_end = struct.unpack_from("<I", blob, 4)[0] + 8
    if riff_end > len(blob):
        raise FixtureError(f"truncated RIFF payload: {path}")

    fmt = None
    data_parts: list[bytes] = []
    offset = 12
    while offset + 8 <= riff_end:
        chunk_id = blob[offset : offset + 4]
        size = struct.unpack_from("<I", blob, offset + 4)[0]
        start = offset + 8
        end = start + size
        if end > riff_end:
            raise FixtureError(f"truncated WAVE chunk: {path}")
        if chunk_id == b"fmt " and fmt is None:
            fmt = blob[start:end]
        elif chunk_id == b"data":
            data_parts.append(blob[start:end])
        offset = end + (size % 2)

    if fmt is None or len(fmt) < 16:
        raise FixtureError(f"missing or invalid WAVE fmt chunk: {path}")
    if not data_parts:
        raise FixtureError(f"missing WAVE data chunk: {path}")

    format_code, channels, sample_rate, byte_rate, block_align, bits = (
        struct.unpack_from("<HHIIHH", fmt)
    )
    if format_code not in (1, 3, 0xFFFE):
        raise FixtureError(f"unsupported WAVE format {format_code}: {path}")
    if channels < 1 or sample_rate < 1 or byte_rate < 1 or block_align < 1 or bits < 1:
        raise FixtureError(f"invalid WAVE format values: {path}")

    data = b"".join(data_parts)
    if not data or len(data) % block_align:
        raise FixtureError(f"WAVE data is empty or not frame-aligned: {path}")
    frame_count = len(data) // block_align
    duration = len(data) / byte_rate
    return fmt, data, format_code, frame_count, duration


def write_wave(path: Path, fmt: bytes, data: bytes, format_code: int, frame_count: int) -> None:
    chunks = [_chunk(b"fmt ", fmt)]
    if format_code != 1:
        if frame_count > 0xFFFFFFFF:
            raise FixtureError("fixture exceeds the RIFF frame-count limit")
        chunks.append(_chunk(b"fact", struct.pack("<I", frame_count)))
    chunks.append(_chunk(b"data", data))
    payload = b"WAVE" + b"".join(chunks)
    if len(payload) > 0xFFFFFFFF:
        raise FixtureError("fixture exceeds the 4 GiB RIFF limit")
    path.write_bytes(b"RIFF" + struct.pack("<I", len(payload)) + payload)


def normalized_reference(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise FixtureError(f"reference is not UTF-8: {path}") from exc
    text = " ".join(text.split())
    if not text:
        raise FixtureError(f"reference is empty: {path}")
    if "\t" in text or "\n" in text or "\r" in text:
        raise FixtureError(f"reference normalization failed: {path}")
    return text


def reference_word_count(text: str) -> int:
    """Mirror presspeech-bench's Unicode-alphanumeric WER tokenization."""
    normalized = unicodedata.normalize("NFC", text.lower())
    tokenized = "".join(character if character.isalnum() else " " for character in normalized)
    return len(tokenized.split())


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_sources(input_dir: Path) -> list[dict[str, object]]:
    sources: list[dict[str, object]] = []
    for audio_path in sorted(input_dir.glob("*.wav")):
        if audio_path.is_symlink() or not audio_path.is_file():
            raise FixtureError(f"fixture audio must be a regular file: {audio_path}")
        if any(character in audio_path.stem for character in "\t\r\n"):
            raise FixtureError(f"fixture filename contains a control character: {audio_path}")
        reference_path = audio_path.with_suffix(".txt")
        if reference_path.is_symlink() or not reference_path.is_file():
            raise FixtureError(f"missing regular reference sidecar: {reference_path}")
        fmt, data, format_code, frames, duration = read_wave(audio_path)
        reference = normalized_reference(reference_path)
        reference_words = reference_word_count(reference)
        if reference_words <= 0:
            raise FixtureError(f"reference contains no WER tokens: {reference_path}")
        sources.append(
            {
                "path": audio_path,
                "id": audio_path.stem,
                "fmt": fmt,
                "data": data,
                "format_code": format_code,
                "frames": frames,
                "duration": duration,
                "reference": reference,
                "reference_words": reference_words,
            }
        )
    if not sources:
        raise FixtureError(f"no WAV fixtures found in {input_dir}")

    baseline_fmt = sources[0]["fmt"]
    for source in sources[1:]:
        if source["fmt"] != baseline_fmt:
            raise FixtureError(
                "all source WAV files must have byte-identical formats; "
                f"format differs for {source['path']}"
            )
    return sources


def select_pairs(
    sources: list[dict[str, object]],
    pair_count: int,
    min_context_seconds: float,
    max_combined_seconds: float,
) -> list[tuple[dict[str, object], dict[str, object]]]:
    """Select disjoint, deterministic probe/context pairs.

    Longer eligible probes are considered first because they exercise more of
    the encoder window.  For each probe, the longest context that still fits is
    selected.  No source appears in more than one pair, so the number of pairs
    remains an honest count of distinct public utterances despite each selected
    source intentionally appearing in two generated files.
    """
    unused = {str(source["id"]): source for source in sources}
    pairs: list[tuple[dict[str, object], dict[str, object]]] = []

    while len(pairs) < pair_count:
        selected = None
        probes = sorted(
            unused.values(),
            key=lambda source: (-float(source["duration"]), str(source["id"])),
        )
        for probe in probes:
            contexts = [
                context
                for context in unused.values()
                if context["id"] != probe["id"]
                and context["reference"] != probe["reference"]
                and float(context["duration"]) >= min_context_seconds
                and float(probe["duration"]) + float(context["duration"])
                <= max_combined_seconds
            ]
            if not contexts:
                continue
            context = sorted(
                contexts,
                key=lambda source: (-float(source["duration"]), str(source["id"])),
            )[0]
            selected = (probe, context)
            break

        if selected is None:
            break
        probe, context = selected
        del unused[str(probe["id"])]
        del unused[str(context["id"])]
        pairs.append((probe, context))

    if len(pairs) != pair_count:
        raise FixtureError(
            f"could form only {len(pairs)} disjoint context pairs from {len(sources)} "
            f"sources; requested {pair_count} with context >= {min_context_seconds:.3f}s "
            f"and combined duration <= {max_combined_seconds:.3f}s"
        )
    return pairs


def safe_remove_output(output_dir: Path) -> None:
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise FixtureError(f"refusing to replace unsafe output directory: {output_dir}")
    marker = output_dir / MARKER_NAME
    if (
        marker.is_symlink()
        or not marker.is_file()
        or marker.read_bytes() != MARKER_TEXT.encode("utf-8")
    ):
        raise FixtureError(f"refusing to replace unowned output directory: {output_dir}")
    resolved = output_dir.resolve()
    if resolved == Path(resolved.anchor) or resolved == Path.cwd().resolve():
        raise FixtureError(f"refusing to remove unsafe output directory: {output_dir}")
    shutil.rmtree(output_dir)


def fixture_digests(audio_path: Path, reference: str) -> tuple[str, str]:
    return (
        sha256_bytes(audio_path.read_bytes()),
        sha256_bytes((reference + "\n").encode("utf-8")),
    )


def compose(
    input_dir: Path,
    output_dir: Path,
    pair_count: int,
    min_context_seconds: float,
    max_combined_seconds: float,
    force: bool,
) -> list[Path]:
    if not input_dir.is_dir() or input_dir.is_symlink():
        raise FixtureError(f"input directory is missing or unsafe: {input_dir}")
    if input_dir.resolve() == output_dir.resolve():
        raise FixtureError("input and output directories must differ")
    if output_dir.exists() or output_dir.is_symlink():
        if not force:
            raise FixtureError(f"output directory already exists: {output_dir}")
        safe_remove_output(output_dir)

    sources = load_sources(input_dir)
    pairs = select_pairs(sources, pair_count, min_context_seconds, max_combined_seconds)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.incomplete-", dir=output_dir.parent))
    outputs: list[Path] = []
    try:
        (stage / MARKER_NAME).write_text(MARKER_TEXT, encoding="utf-8")
        manifest_path = stage / MANIFEST_NAME
        with manifest_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS, delimiter="\t")
            writer.writeheader()
            for index, (probe, context) in enumerate(pairs, 1):
                pair_id = f"context-{index:03d}"
                fixture_names = {
                    "probe": f"{pair_id}-probe",
                    "context": f"{pair_id}-context",
                    "combined": f"{pair_id}-combined",
                }
                combined_reference = f"{probe['reference']} {context['reference']}"
                references = {
                    "probe": str(probe["reference"]),
                    "context": str(context["reference"]),
                    "combined": combined_reference,
                }
                payloads = {
                    "probe": (bytes(probe["data"]), int(probe["frames"])),
                    "context": (bytes(context["data"]), int(context["frames"])),
                    "combined": (
                        bytes(probe["data"]) + bytes(context["data"]),
                        int(probe["frames"]) + int(context["frames"]),
                    ),
                }
                digests: dict[str, tuple[str, str]] = {}
                for role in ("probe", "context", "combined"):
                    stem = fixture_names[role]
                    audio_path = stage / f"{stem}.wav"
                    data, frames = payloads[role]
                    write_wave(
                        audio_path,
                        bytes(probe["fmt"]),
                        data,
                        int(probe["format_code"]),
                        frames,
                    )
                    (stage / f"{stem}.txt").write_text(
                        references[role] + "\n", encoding="utf-8"
                    )
                    digests[role] = fixture_digests(audio_path, references[role])
                    outputs.append(output_dir / audio_path.name)

                probe_seconds = float(probe["duration"])
                context_seconds = float(context["duration"])
                combined_seconds = probe_seconds + context_seconds
                writer.writerow(
                    {
                        "pair_id": pair_id,
                        "probe_fixture": fixture_names["probe"],
                        "context_fixture": fixture_names["context"],
                        "combined_fixture": fixture_names["combined"],
                        "probe_source": probe["id"],
                        "context_source": context["id"],
                        "probe_duration_seconds": f"{probe_seconds:.6f}",
                        "context_duration_seconds": f"{context_seconds:.6f}",
                        "combined_duration_seconds": f"{combined_seconds:.6f}",
                        "probe_reference_words": probe["reference_words"],
                        "context_reference_words": context["reference_words"],
                        "combined_reference_words": reference_word_count(combined_reference),
                        "probe_audio_sha256": digests["probe"][0],
                        "context_audio_sha256": digests["context"][0],
                        "combined_audio_sha256": digests["combined"][0],
                        "probe_reference_sha256": digests["probe"][1],
                        "context_reference_sha256": digests["context"][1],
                        "combined_reference_sha256": digests["combined"][1],
                    }
                )

        (stage / "README.txt").write_text(
            "Generated public Presspeech context-variation benchmark fixtures.\n\n"
            f"Source fixture directory: {input_dir}\n"
            f"Disjoint source pairs: {len(pairs)}\n"
            f"Minimum trailing context: {min_context_seconds:.3f} seconds\n"
            f"Maximum combined duration: {max_combined_seconds:.3f} seconds\n\n"
            "Each pair contains a probe, a context clip, and their byte-exact "
            "sample-payload concatenation. The combined clip stays below one "
            "15-second Parakeet encoder window. Source utterances are distinct "
            "across pairs but are "
            "intentionally repeated within a pair; this corpus is supplementary "
            "context-sensitivity evidence, not an independent general-WER corpus.\n",
            encoding="utf-8",
        )
        for source_name, output_name in (
            ("README.txt", "SOURCE-README.txt"),
            ("manifest.tsv", "source-manifest.tsv"),
        ):
            source_metadata = input_dir / source_name
            if source_metadata.is_file() and not source_metadata.is_symlink():
                shutil.copyfile(source_metadata, stage / output_name)
        os.replace(stage, output_dir)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return outputs


def _parse_positive_float(value: str, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise FixtureError(f"invalid {label} in manifest") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise FixtureError(f"invalid {label} in manifest")
    return parsed


def _parse_positive_int(value: str, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise FixtureError(f"invalid {label} in manifest") from exc
    if parsed <= 0:
        raise FixtureError(f"invalid {label} in manifest")
    return parsed


def validate_output(output_dir: Path) -> list[Path]:
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise FixtureError(f"context output directory is missing or unsafe: {output_dir}")
    marker = output_dir / MARKER_NAME
    if (
        marker.is_symlink()
        or not marker.is_file()
        or marker.read_bytes() != MARKER_TEXT.encode("utf-8")
    ):
        raise FixtureError(f"context output is not owned by this composer: {output_dir}")

    manifest_path = output_dir / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise FixtureError(f"missing regular context manifest: {manifest_path}")
    with manifest_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames != MANIFEST_FIELDS:
            raise FixtureError("context manifest has an unexpected schema")
        rows = list(reader)
    if not rows:
        raise FixtureError("context manifest contains no pairs")

    pair_ids: set[str] = set()
    expected_stems: set[str] = set()
    used_sources: set[str] = set()
    audio_paths: list[Path] = []
    for row in rows:
        pair_id = row["pair_id"]
        if not pair_id or any(character in pair_id for character in "\t\r\n"):
            raise FixtureError("context manifest contains an invalid pair id")
        if pair_id in pair_ids:
            raise FixtureError(f"duplicate context pair id: {pair_id}")
        pair_ids.add(pair_id)
        probe_source = row["probe_source"]
        context_source = row["context_source"]
        if not probe_source or not context_source or probe_source == context_source:
            raise FixtureError(f"invalid source assignment for {pair_id}")
        if probe_source in used_sources or context_source in used_sources:
            raise FixtureError(f"source utterance reused across context pairs: {pair_id}")
        used_sources.update((probe_source, context_source))

        durations = {
            role: _parse_positive_float(row[f"{role}_duration_seconds"], f"{role} duration")
            for role in ("probe", "context", "combined")
        }
        if not math.isclose(
            durations["combined"], durations["probe"] + durations["context"], abs_tol=0.001
        ):
            raise FixtureError(f"combined duration does not equal its components for {pair_id}")
        if durations["combined"] >= ENCODER_WINDOW_SECONDS:
            raise FixtureError(f"combined fixture is not single-window audio for {pair_id}")

        words = {
            role: _parse_positive_int(row[f"{role}_reference_words"], f"{role} word count")
            for role in ("probe", "context", "combined")
        }
        if words["combined"] != words["probe"] + words["context"]:
            raise FixtureError(f"combined word count does not equal its components for {pair_id}")

        observed_payloads: dict[str, tuple[bytes, bytes, int, int]] = {}
        observed_references: dict[str, str] = {}
        for role in ("probe", "context", "combined"):
            stem = row[f"{role}_fixture"]
            if not stem or stem in expected_stems or Path(stem).name != stem:
                raise FixtureError(f"invalid or duplicate {role} fixture for {pair_id}")
            expected_stems.add(stem)
            audio_path = output_dir / f"{stem}.wav"
            reference_path = output_dir / f"{stem}.txt"
            if (
                audio_path.is_symlink()
                or not audio_path.is_file()
                or reference_path.is_symlink()
                or not reference_path.is_file()
            ):
                raise FixtureError(f"missing regular {role} fixture for {pair_id}")
            (
                observed_fmt,
                observed_data,
                observed_format_code,
                observed_frames,
                observed_duration,
            ) = read_wave(audio_path)
            reference = normalized_reference(reference_path)
            if not math.isclose(observed_duration, durations[role], abs_tol=0.001):
                raise FixtureError(f"manifest duration does not match {stem}")
            if reference_word_count(reference) != words[role]:
                raise FixtureError(f"manifest word count does not match {stem}")
            audio_digest, reference_digest = fixture_digests(audio_path, reference)
            if audio_digest != row[f"{role}_audio_sha256"]:
                raise FixtureError(f"manifest audio digest does not match {stem}")
            if reference_digest != row[f"{role}_reference_sha256"]:
                raise FixtureError(f"manifest reference digest does not match {stem}")
            observed_payloads[role] = (
                observed_fmt,
                observed_data,
                observed_format_code,
                observed_frames,
            )
            observed_references[role] = reference
            audio_paths.append(audio_path)

        probe_fmt, probe_data, probe_code, probe_frames = observed_payloads["probe"]
        context_fmt, context_data, context_code, context_frames = observed_payloads["context"]
        combined_fmt, combined_data, combined_code, combined_frames = observed_payloads["combined"]
        if probe_fmt != context_fmt or probe_fmt != combined_fmt or not (
            probe_code == context_code == combined_code
        ):
            raise FixtureError(f"audio formats differ within {pair_id}")
        if combined_data != probe_data + context_data:
            raise FixtureError(
                f"combined audio is not an exact payload concatenation for {pair_id}"
            )
        if combined_frames != probe_frames + context_frames:
            raise FixtureError(f"combined frame count does not equal its components for {pair_id}")
        expected_combined_reference = (
            f"{observed_references['probe']} {observed_references['context']}"
        )
        if observed_references["combined"] != expected_combined_reference:
            raise FixtureError(f"combined reference does not equal its components for {pair_id}")
        if observed_references["probe"] == observed_references["context"]:
            raise FixtureError(f"probe and context references are identical for {pair_id}")

    wav_entries: list[Path] = []
    for path in output_dir.rglob("*"):
        if path.suffix.lower() not in SUPPORTED_BENCH_AUDIO_SUFFIXES:
            continue
        if (
            path.parent != output_dir
            or path.suffix != ".wav"
            or path.is_symlink()
            or not path.is_file()
        ):
            raise FixtureError(
                f"context fixture audio must be a top-level regular WAV file: {path}"
            )
        wav_entries.append(path)
    observed_wavs = {path.stem for path in wav_entries}
    if observed_wavs != expected_stems:
        extra = sorted(observed_wavs - expected_stems)
        missing = sorted(expected_stems - observed_wavs)
        detail = extra[0] if extra else missing[0]
        raise FixtureError(f"context fixture set does not match its manifest: {detail}")
    return sorted(audio_paths)


def write_pcm16_fixture(path: Path, seconds: int, sample_rate: int, value: int) -> None:
    fmt = struct.pack("<HHIIHH", 1, 1, sample_rate, sample_rate * 2, 2, 16)
    data = struct.pack("<h", value) * (seconds * sample_rate)
    write_wave(path, fmt, data, 1, seconds * sample_rate)


def run_self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="presspeech-context-composer-self-test-") as tmp:
        root = Path(tmp)
        source = root / "source"
        output = root / "output"
        source.mkdir()
        durations = (3, 4, 5, 6, 7, 8)
        for index, seconds in enumerate(durations):
            audio = source / f"clip-{index:02d}.wav"
            write_pcm16_fixture(audio, seconds, 16_000, index + 1)
            audio.with_suffix(".txt").write_text(
                f"reference number {index}\n", encoding="utf-8"
            )

        outputs = compose(source, output, 2, 4.0, 14.5, False)
        if len(outputs) != 6:
            raise AssertionError(f"expected six triplet files, got {len(outputs)}")
        validated = validate_output(output)
        if len(validated) != 6:
            raise AssertionError("valid context corpus did not pass preflight")

        with (output / MANIFEST_NAME).open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        if len(rows) != 2:
            raise AssertionError("context manifest omitted a pair")
        assigned_sources = {
            value
            for row in rows
            for value in (row["probe_source"], row["context_source"])
        }
        if len(assigned_sources) != 4:
            raise AssertionError("a source utterance was reused across pairs")
        first = rows[0]
        probe_data = read_wave(output / f"{first['probe_fixture']}.wav")[1]
        context_data = read_wave(output / f"{first['context_fixture']}.wav")[1]
        combined_data = read_wave(output / f"{first['combined_fixture']}.wav")[1]
        if combined_data != probe_data + context_data:
            raise AssertionError("combined fixture did not preserve exact component payloads")

        modified = output / f"{first['probe_fixture']}.wav"
        modified.write_bytes(modified.read_bytes() + b"changed")
        try:
            validate_output(output)
        except FixtureError as exc:
            if "digest" not in str(exc):
                raise
        else:
            raise AssertionError("modified context audio passed manifest validation")

        compose(source, output, 2, 4.0, 14.5, True)

        extra_audio = output / "unexpected.wav"
        extra_audio.symlink_to(next(output.glob("*.wav")).name)
        try:
            validate_output(output)
        except FixtureError as exc:
            if "top-level regular WAV" not in str(exc):
                raise
        else:
            raise AssertionError("extra symlinked context audio passed validation")
        extra_audio.unlink()

        unexpected_format = output / "unexpected.mp3"
        unexpected_format.write_bytes(b"not audio")
        try:
            validate_output(output)
        except FixtureError as exc:
            if "top-level regular WAV" not in str(exc):
                raise
        else:
            raise AssertionError("extra supported-format audio passed validation")
        unexpected_format.unlink()

        try:
            compose(source, output, 2, 4.0, 14.5, False)
        except FixtureError as exc:
            if "already exists" not in str(exc):
                raise
        else:
            raise AssertionError("existing context output was replaced without --force")

        unowned = root / "unowned"
        unowned.mkdir()
        (unowned / "keep.txt").write_text("user data\n", encoding="utf-8")
        try:
            compose(source, unowned, 1, 4.0, 14.5, True)
        except FixtureError as exc:
            if "unowned" not in str(exc):
                raise
        else:
            raise AssertionError("--force replaced an unowned output directory")
        if not (unowned / "keep.txt").is_file():
            raise AssertionError("unowned output data was altered")

        invalid_source = root / "invalid-source"
        invalid_source.mkdir()
        invalid_audio = invalid_source / "punctuation.wav"
        write_pcm16_fixture(invalid_audio, 1, 16_000, 1)
        invalid_audio.with_suffix(".txt").write_text("!!!\n", encoding="utf-8")
        try:
            load_sources(invalid_source)
        except FixtureError as exc:
            if "no WER tokens" not in str(exc):
                raise
        else:
            raise AssertionError("reference without WER tokens passed source validation")

        try:
            compose(source, root / "too-many", 4, 4.0, 14.5, False)
        except FixtureError as exc:
            if "could form only" not in str(exc):
                raise
        else:
            raise AssertionError("impossible pair count unexpectedly composed")

    print("public context fixture composer self-test passed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir",
        default="public-audio/fleurs-uk_ua-test",
        help="directory containing public WAV + .txt fixture pairs",
    )
    parser.add_argument(
        "--output-dir",
        default="public-audio/fleurs-uk_ua-test-context",
        help="generated context-variation fixture directory",
    )
    parser.add_argument(
        "--pair-count",
        type=int,
        default=DEFAULT_PAIR_COUNT,
        help=f"number of disjoint source pairs (default: {DEFAULT_PAIR_COUNT})",
    )
    parser.add_argument(
        "--min-context-seconds",
        type=float,
        default=DEFAULT_MIN_CONTEXT_SECONDS,
        help=f"minimum trailing context duration (default: {DEFAULT_MIN_CONTEXT_SECONDS:g})",
    )
    parser.add_argument(
        "--max-combined-seconds",
        type=float,
        default=DEFAULT_MAX_COMBINED_SECONDS,
        help=(
            "maximum probe+context duration, strictly below the 15-second encoder "
            f"window (default: {DEFAULT_MAX_COMBINED_SECONDS:g})"
        ),
    )
    parser.add_argument("--force", action="store_true", help="replace an owned generated output")
    parser.add_argument(
        "--validate-output-dir",
        action="store_true",
        help="validate an existing generated context corpus",
    )
    parser.add_argument("--self-test", action="store_true", help="run local composition tests")
    return parser.parse_args()


def validate_cli_args(args: argparse.Namespace) -> None:
    if args.pair_count < 1:
        raise FixtureError("--pair-count must be a positive integer")
    if not math.isfinite(args.min_context_seconds) or args.min_context_seconds <= 0:
        raise FixtureError("--min-context-seconds must be finite and positive")
    if (
        not math.isfinite(args.max_combined_seconds)
        or args.max_combined_seconds <= args.min_context_seconds
        or args.max_combined_seconds >= ENCODER_WINDOW_SECONDS
    ):
        raise FixtureError(
            "--max-combined-seconds must be finite, greater than the minimum "
            f"context, and below {ENCODER_WINDOW_SECONDS:g}"
        )


def main() -> int:
    args = parse_args()
    if args.self_test:
        run_self_test()
        return 0
    if args.validate_output_dir:
        outputs = validate_output(Path(args.output_dir))
        print(f"validated public context fixtures: {args.output_dir}")
        print(f"triplet files: {len(outputs)}")
        return 0
    validate_cli_args(args)
    outputs = compose(
        Path(args.input_dir),
        Path(args.output_dir),
        args.pair_count,
        args.min_context_seconds,
        args.max_combined_seconds,
        args.force,
    )
    print(f"public context fixtures: {args.output_dir}")
    print(f"source pairs: {len(outputs) // 3}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FixtureError, OSError) as exc:
        raise SystemExit(f"error: {exc}") from exc
