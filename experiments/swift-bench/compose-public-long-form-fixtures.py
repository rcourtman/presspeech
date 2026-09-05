#!/usr/bin/env python3
"""Compose short public WAV fixtures into deterministic long-form ASR clips."""

from __future__ import annotations

import argparse
import csv
import math
import os
from pathlib import Path
import shutil
import struct
import tempfile


MARKER_NAME = ".presspeech-public-long-form-fixtures"
MARKER_TEXT = "Presspeech generated public long-form speech fixtures\n"
DEFAULT_TARGET_SECONDS = 45.0
MIN_TARGET_SECONDS = 30.0
MIN_RELEASE_COMPOSITES = 2


class FixtureError(Exception):
    """A public fixture set is malformed or unsuitable for composition."""


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
    if len(data) % block_align:
        raise FixtureError(f"WAVE data is not frame-aligned: {path}")
    frame_count = len(data) // block_align
    duration = len(data) / byte_rate
    return fmt, data, format_code, frame_count, duration


def write_wave(path: Path, fmt: bytes, data: bytes, format_code: int, frame_count: int) -> None:
    chunks = [_chunk(b"fmt ", fmt)]
    # WAVE_FORMAT_IEEE_FLOAT and extensible files conventionally carry the
    # decoded sample length. AVAudioConverter accepts the canonical file with
    # this fact chunk and no afconvert-specific filler chunks.
    if format_code != 1:
        if frame_count > 0xFFFFFFFF:
            raise FixtureError("composite WAVE exceeds the RIFF frame-count limit")
        chunks.append(_chunk(b"fact", struct.pack("<I", frame_count)))
    chunks.append(_chunk(b"data", data))
    payload = b"WAVE" + b"".join(chunks)
    if len(payload) > 0xFFFFFFFF:
        raise FixtureError("composite WAVE exceeds the 4 GiB RIFF limit")
    path.write_bytes(b"RIFF" + struct.pack("<I", len(payload)) + payload)


def normalized_reference(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise FixtureError(f"reference is not UTF-8: {path}") from exc
    text = " ".join(text.split())
    if not text:
        raise FixtureError(f"reference is empty: {path}")
    return text


def load_sources(input_dir: Path) -> list[dict[str, object]]:
    sources: list[dict[str, object]] = []
    for audio_path in sorted(input_dir.glob("*.wav")):
        if audio_path.is_symlink() or not audio_path.is_file():
            raise FixtureError(f"fixture audio must be a regular file: {audio_path}")
        reference_path = audio_path.with_suffix(".txt")
        if reference_path.is_symlink() or not reference_path.is_file():
            raise FixtureError(f"missing regular reference sidecar: {reference_path}")
        fmt, data, format_code, frames, duration = read_wave(audio_path)
        sources.append(
            {
                "path": audio_path,
                "id": audio_path.stem,
                "fmt": fmt,
                "data": data,
                "format_code": format_code,
                "frames": frames,
                "duration": duration,
                "reference": normalized_reference(reference_path),
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


def group_sources(sources: list[dict[str, object]], target_seconds: float) -> list[list[dict[str, object]]]:
    groups: list[list[dict[str, object]]] = []
    pending: list[dict[str, object]] = []
    pending_seconds = 0.0
    for source in sources:
        pending.append(source)
        pending_seconds += float(source["duration"])
        if pending_seconds >= target_seconds:
            groups.append(pending)
            pending = []
            pending_seconds = 0.0

    if pending:
        if not groups:
            raise FixtureError(
                f"source audio totals {pending_seconds:.3f}s; at least "
                f"{target_seconds:.3f}s is required"
            )
        # Do not publish a short final composite that misses the window seam
        # being tested. Every source remains represented by extending the last
        # complete group instead.
        groups[-1].extend(pending)
    return groups


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


def compose(input_dir: Path, output_dir: Path, target_seconds: float, force: bool) -> list[Path]:
    if not input_dir.is_dir() or input_dir.is_symlink():
        raise FixtureError(f"input directory is missing or unsafe: {input_dir}")
    if input_dir.resolve() == output_dir.resolve():
        raise FixtureError("input and output directories must differ")
    if output_dir.exists() or output_dir.is_symlink():
        if not force:
            raise FixtureError(f"output directory already exists: {output_dir}")
        safe_remove_output(output_dir)

    sources = load_sources(input_dir)
    groups = group_sources(sources, target_seconds)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.incomplete-", dir=output_dir.parent))
    outputs: list[Path] = []
    try:
        (stage / MARKER_NAME).write_text(MARKER_TEXT, encoding="utf-8")
        manifest = stage / "manifest.tsv"
        with manifest.open("w", encoding="utf-8", newline="") as handle:
            handle.write(
                "composite_id\tsource_clip_count\tsource_clips\tduration_seconds\t"
                "source_boundaries_seconds\tnominal_15s_boundaries_seconds\treference\n"
            )
            for index, group in enumerate(groups, 1):
                composite_id = f"long-form-{index:03d}"
                fmt = group[0]["fmt"]
                format_code = int(group[0]["format_code"])
                data = b"".join(bytes(item["data"]) for item in group)
                frame_count = sum(int(item["frames"]) for item in group)
                duration = sum(float(item["duration"]) for item in group)
                reference = " ".join(str(item["reference"]) for item in group)
                boundaries: list[float] = []
                elapsed = 0.0
                for item in group[:-1]:
                    elapsed += float(item["duration"])
                    boundaries.append(elapsed)
                nominal_boundaries = []
                boundary = 15.0
                while boundary < duration:
                    nominal_boundaries.append(boundary)
                    boundary += 15.0

                audio_path = stage / f"{composite_id}.wav"
                write_wave(audio_path, bytes(fmt), data, format_code, frame_count)
                (stage / f"{composite_id}.txt").write_text(reference + "\n", encoding="utf-8")
                handle.write(
                    f"{composite_id}\t{len(group)}\t"
                    f"{','.join(str(item['id']) for item in group)}\t{duration:.6f}\t"
                    f"{','.join(f'{value:.6f}' for value in boundaries)}\t"
                    f"{','.join(f'{value:.1f}' for value in nominal_boundaries)}\t"
                    f"{reference}\n"
                )
                outputs.append(output_dir / audio_path.name)

        (stage / "README.txt").write_text(
            "Generated public long-form Presspeech benchmark fixtures.\n\n"
            f"Source fixture directory: {input_dir}\n"
            f"Target duration per composite: {target_seconds:.3f} seconds\n"
            f"Composite clips: {len(groups)}\n\n"
            "The source WAV files are concatenated in sorted order without resampling or "
            "invented audio. References are joined in the same order. The manifest records "
            "both source-clip boundaries and nominal 15-second boundary markers; FluidAudio's "
            "actual overlapping window starts remain an implementation detail. Preserve the "
            "source corpus's attribution and license.\n",
            encoding="utf-8",
        )
        # Keep the fetched corpus's exact attribution and per-row provenance
        # beside this derived corpus when those files are available.
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


def validate_output(output_dir: Path) -> list[Path]:
    """Validate that an existing generated corpus really exercises many windows."""
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise FixtureError(f"long-form output directory is missing or unsafe: {output_dir}")
    marker = output_dir / MARKER_NAME
    if (
        marker.is_symlink()
        or not marker.is_file()
        or marker.read_bytes() != MARKER_TEXT.encode("utf-8")
    ):
        raise FixtureError(f"long-form output is not owned by this composer: {output_dir}")

    audio_paths = sorted(output_dir.glob("*.wav"))
    if len(audio_paths) < MIN_RELEASE_COMPOSITES:
        raise FixtureError(
            "long-form release coverage requires at least "
            f"{MIN_RELEASE_COMPOSITES} composite WAV files; found {len(audio_paths)}"
        )
    other_audio = sorted(
        path
        for path in output_dir.iterdir()
        if path.suffix.lower() in {".aiff", ".aif", ".caf", ".m4a", ".mp3", ".flac"}
    )
    if other_audio:
        raise FixtureError(
            "generated long-form output contains unsupported extra audio: "
            f"{other_audio[0]}"
        )

    observed: dict[str, tuple[float, str]] = {}
    for audio_path in audio_paths:
        if audio_path.is_symlink() or not audio_path.is_file():
            raise FixtureError(f"composite audio must be a regular file: {audio_path}")
        reference_path = audio_path.with_suffix(".txt")
        if reference_path.is_symlink() or not reference_path.is_file():
            raise FixtureError(f"missing regular composite reference: {reference_path}")
        _fmt, _data, _format_code, _frames, duration = read_wave(audio_path)
        if duration < MIN_TARGET_SECONDS:
            raise FixtureError(
                f"composite {audio_path.name} is {duration:.3f}s; release coverage "
                f"requires at least {MIN_TARGET_SECONDS:.3f}s per clip"
            )
        observed[audio_path.stem] = (duration, normalized_reference(reference_path))

    manifest_path = output_dir / "manifest.tsv"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise FixtureError(f"missing regular long-form manifest: {manifest_path}")
    with manifest_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        expected_fields = [
            "composite_id",
            "source_clip_count",
            "source_clips",
            "duration_seconds",
            "source_boundaries_seconds",
            "nominal_15s_boundaries_seconds",
            "reference",
        ]
        if reader.fieldnames != expected_fields:
            raise FixtureError("long-form manifest has an unexpected schema")
        rows = list(reader)

    manifest_ids: set[str] = set()
    for row in rows:
        composite_id = row["composite_id"]
        if composite_id in manifest_ids:
            raise FixtureError(f"duplicate long-form manifest id: {composite_id}")
        manifest_ids.add(composite_id)
        if composite_id not in observed:
            raise FixtureError(f"manifest references missing composite: {composite_id}")
        try:
            source_clip_count = int(row["source_clip_count"])
            manifest_duration = float(row["duration_seconds"])
            source_boundaries = [
                float(value)
                for value in row["source_boundaries_seconds"].split(",")
                if value
            ]
            nominal_boundaries = [
                float(value)
                for value in row["nominal_15s_boundaries_seconds"].split(",")
                if value
            ]
        except ValueError as exc:
            raise FixtureError(f"invalid numeric manifest data for {composite_id}") from exc
        duration, reference = observed[composite_id]
        if source_clip_count < 2 or len(row["source_clips"].split(",")) != source_clip_count:
            raise FixtureError(f"invalid source-clip evidence for {composite_id}")
        if (
            len(source_boundaries) != source_clip_count - 1
            or any(
                current <= previous
                for previous, current in zip(source_boundaries, source_boundaries[1:])
            )
            or any(value <= 0 or value >= duration for value in source_boundaries)
        ):
            raise FixtureError(f"invalid source boundaries for {composite_id}")
        if not math.isclose(manifest_duration, duration, abs_tol=0.001):
            raise FixtureError(f"manifest duration does not match {composite_id}")
        expected_boundaries: list[float] = []
        boundary = 15.0
        while boundary < duration:
            expected_boundaries.append(boundary)
            boundary += 15.0
        if nominal_boundaries != expected_boundaries:
            raise FixtureError(f"manifest window boundaries do not match {composite_id}")
        if row["reference"] != reference:
            raise FixtureError(f"manifest reference does not match {composite_id}")

    if manifest_ids != set(observed):
        missing = sorted(set(observed) - manifest_ids)[0]
        raise FixtureError(f"composite is missing from long-form manifest: {missing}")
    return audio_paths


def write_pcm16_fixture(path: Path, seconds: int, sample_rate: int, value: int) -> None:
    fmt = struct.pack("<HHIIHH", 1, 1, sample_rate, sample_rate * 2, 2, 16)
    data = struct.pack("<h", value) * (seconds * sample_rate)
    write_wave(path, fmt, data, 1, seconds * sample_rate)


def run_self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="presspeech-long-form-self-test-") as tmp:
        root = Path(tmp)
        source = root / "source"
        output = root / "output"
        source.mkdir()
        for index in range(4):
            audio = source / f"clip-{index:02d}.wav"
            write_pcm16_fixture(audio, 16, 16_000, index + 1)
            audio.with_suffix(".txt").write_text(f"reference {index}\n", encoding="utf-8")

        outputs = compose(source, output, 30.0, False)
        if len(outputs) != 2:
            raise AssertionError(f"expected two composites, got {len(outputs)}")
        for index, audio in enumerate(outputs):
            _fmt, _data, _code, frames, duration = read_wave(audio)
            if frames != 32 * 16_000 or not math.isclose(duration, 32.0):
                raise AssertionError("composite duration or frame count changed")
            expected = f"reference {index * 2} reference {index * 2 + 1}\n"
            if audio.with_suffix(".txt").read_text(encoding="utf-8") != expected:
                raise AssertionError("references were not joined in source order")

        manifest = (output / "manifest.tsv").read_text(encoding="utf-8")
        if "\t16.000000\t15.0,30.0\t" not in manifest:
            raise AssertionError("manifest omitted source/nominal boundary evidence")
        if len(validate_output(output)) != 2:
            raise AssertionError("valid long-form corpus did not pass release preflight")

        (output / "long-form-001.txt").write_text("changed reference\n", encoding="utf-8")
        try:
            validate_output(output)
        except FixtureError as exc:
            if "manifest reference" not in str(exc):
                raise
        else:
            raise AssertionError("changed composite reference passed release preflight")
        compose(source, output, 30.0, True)
        manifest_path = output / "manifest.tsv"
        manifest = manifest_path.read_text(encoding="utf-8")
        manifest_path.write_text(
            manifest.replace("\t16.000000\t15.0,30.0\t", "\t99.000000\t15.0,30.0\t", 1),
            encoding="utf-8",
        )
        try:
            validate_output(output)
        except FixtureError as exc:
            if "source boundaries" not in str(exc):
                raise
        else:
            raise AssertionError("invalid source boundaries passed release preflight")
        compose(source, output, 30.0, True)
        try:
            compose(source, output, 30.0, False)
        except FixtureError as exc:
            if "already exists" not in str(exc):
                raise
        else:
            raise AssertionError("existing output was replaced without --force")
        compose(source, output, 30.0, True)

        unowned = root / "unowned"
        unowned.mkdir()
        (unowned / "keep.txt").write_text("user data\n", encoding="utf-8")
        try:
            compose(source, unowned, 30.0, True)
        except FixtureError as exc:
            if "unowned" not in str(exc):
                raise
        else:
            raise AssertionError("--force replaced an unowned output directory")
        if not (unowned / "keep.txt").is_file():
            raise AssertionError("unowned output data was altered")

        short_source = root / "short"
        short_source.mkdir()
        write_pcm16_fixture(short_source / "only.wav", 10, 16_000, 1)
        (short_source / "only.txt").write_text("too short\n", encoding="utf-8")
        try:
            compose(short_source, root / "short-output", 30.0, False)
        except FixtureError as exc:
            if "at least" not in str(exc):
                raise
        else:
            raise AssertionError("short source corpus unexpectedly composed")

    print("public long-form fixture composer self-test passed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir",
        default="public-audio/librispeech-dev-clean",
        help="directory containing sorted public WAV + .txt fixture pairs",
    )
    parser.add_argument(
        "--output-dir",
        default="public-audio/librispeech-dev-clean-long-form",
        help="generated long-form fixture directory",
    )
    parser.add_argument(
        "--target-seconds",
        type=float,
        default=DEFAULT_TARGET_SECONDS,
        help=f"minimum duration per composite (default: {DEFAULT_TARGET_SECONDS:g})",
    )
    parser.add_argument("--force", action="store_true", help="replace an owned generated output")
    parser.add_argument(
        "--validate-output-dir",
        action="store_true",
        help="validate an existing generated corpus for release coverage",
    )
    parser.add_argument("--self-test", action="store_true", help="run local format/composition tests")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        run_self_test()
        return 0
    if args.validate_output_dir:
        outputs = validate_output(Path(args.output_dir))
        print(f"validated long-form fixtures: {args.output_dir}")
        print(f"composites: {len(outputs)}")
        return 0
    if not math.isfinite(args.target_seconds) or args.target_seconds < MIN_TARGET_SECONDS:
        raise FixtureError(
            f"--target-seconds must be finite and at least {MIN_TARGET_SECONDS:g}"
        )
    outputs = compose(
        Path(args.input_dir), Path(args.output_dir), args.target_seconds, args.force
    )
    print(f"long-form fixtures: {args.output_dir}")
    print(f"composites: {len(outputs)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FixtureError, OSError) as exc:
        raise SystemExit(f"error: {exc}") from exc
