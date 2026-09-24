#!/usr/bin/env python3
"""Pair public long-form speech with an interior digital-silence gap.

This is a report-only probe for the risk that a model window ending inside a
long silent run loses nearby words. It does not infer FluidAudio's actual
window placement or change the production ASR path.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile


_spec = importlib.util.spec_from_file_location(
    "presspeech_long_form_fixtures",
    Path(__file__).with_name("compose-public-long-form-fixtures.py"),
)
if _spec is None or _spec.loader is None:
    raise RuntimeError("cannot load the public long-form fixture composer")
long_form = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = long_form
_spec.loader.exec_module(long_form)

MARKER_NAME = ".presspeech-public-silence-gap-fixtures"
MARKER_TEXT = "Presspeech generated public interior-silence speech fixtures\n"
FIELDS = (
    "fixture_id", "source_id", "gap_ms", "gap_start_frame",
    "nominal_edge_frame", "source_frames", "audio_sha256",
    "reference_sha256",
)
SAMPLE_RATE = 16000
NOMINAL_WINDOW_FRAMES = 15 * SAMPLE_RATE
DEFAULT_GAP_MS = 5000


class FixtureError(Exception):
    """An input or generated public probe is malformed or unsafe."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _regular(path: Path) -> bool:
    return not path.is_symlink() and path.is_file()


def _gap_frames(gap_ms: int) -> int:
    if (isinstance(gap_ms, bool) or not isinstance(gap_ms, int)
            or not 3000 <= gap_ms <= 10000):
        raise FixtureError("gap duration must be 3000–10000 whole milliseconds")
    return gap_ms * SAMPLE_RATE // 1000


def _format_alignment(fmt: bytes, code: int) -> int:
    if len(fmt) < 16:
        raise FixtureError("invalid WAVE format")
    _code, channels, rate, byte_rate, align, bits = struct.unpack_from("<HHIIHH", fmt)
    if code == 0xFFFE:
        guid_tail = bytes.fromhex("00001000800000aa00389b71")
        if (len(fmt) < 40 or struct.unpack_from("<H", fmt, 16)[0] < 22
                or fmt[26:28] != b"\0\0" or fmt[28:40] != guid_tail):
            raise FixtureError("unsupported extensible WAVE format")
        code = struct.unpack_from("<H", fmt, 24)[0]
    if ((code, bits) not in ((1, 16), (3, 32)) or channels != 1
            or rate != SAMPLE_RATE or align != bits // 8
            or byte_rate != SAMPLE_RATE * align):
        raise FixtureError("silence-gap probe requires 16 kHz mono PCM16 or Float32 WAVE")
    return align


def _edge_ok(start: int, edge: int, source_frames: int, gap_frames: int) -> bool:
    # At least one second of the inserted silence lies on each side of a
    # nominal 15-second end. Keep the gap well inside a long-form recording.
    return (start >= 5 * SAMPLE_RATE
            and source_frames - edge >= 10 * SAMPLE_RATE
            and SAMPLE_RATE <= edge - start <= gap_frames - SAMPLE_RATE)


def _choose_boundary(boundaries: str, source_frames: int,
                     gap_frames: int) -> tuple[int, int]:
    candidates = []
    for value in boundaries.split(","):
        if not value:
            continue
        start = round(float(value) * SAMPLE_RATE)
        for edge in range(NOMINAL_WINDOW_FRAMES, source_frames,
                          NOMINAL_WINDOW_FRAMES):
            if _edge_ok(start, edge, source_frames, gap_frames):
                candidates.append((abs((edge - start) - gap_frames // 2),
                                   edge, start))
    if not candidates:
        raise FixtureError(
            "no interior source boundary brackets a nominal window end with silence; "
            "use a different public long-form corpus")
    _distance, edge, start = min(candidates)
    return start, edge


def _owned_output(path: Path) -> None:
    marker = path / MARKER_NAME
    if (path.is_symlink() or not path.is_dir() or not _regular(marker)
            or marker.read_bytes() != MARKER_TEXT.encode("utf-8")):
        raise FixtureError("refusing to replace unowned silence-gap output")
    resolved = path.resolve()
    if resolved == Path(resolved.anchor) or resolved == Path.cwd().resolve():
        raise FixtureError("refusing to replace unsafe output directory")


def _publish(stage: Path, output: Path, force: bool) -> None:
    if not output.exists() and not output.is_symlink():
        os.rename(stage, output)
        return
    if not force:
        raise FixtureError("output directory already exists")
    _owned_output(output)
    backup = Path(tempfile.mkdtemp(prefix=f".{output.name}.previous-", dir=output.parent))
    previous = backup / "corpus"
    try:
        os.rename(output, previous)
    except BaseException:
        backup.rmdir()
        raise
    try:
        os.rename(stage, output)
    except BaseException:
        # If restoration also fails, retain the previous corpus in backup.
        try:
            os.rename(previous, output)
        except OSError as exc:
            raise FixtureError(
                f"replacement failed; previous corpus retained at {previous}"
            ) from exc
        backup.rmdir()
        raise
    shutil.rmtree(backup)


def compose(input_dir: Path, output_dir: Path, gap_ms: int,
            force: bool) -> int:
    gap_frames = _gap_frames(gap_ms)
    if input_dir.is_symlink() or not input_dir.is_dir():
        raise FixtureError("public long-form input directory is missing or unsafe")
    source = input_dir.resolve()
    destination = output_dir.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise FixtureError("input and output directories must not contain one another")
    try:
        paths = long_form.validate_output(input_dir)
    except long_form.FixtureError as exc:
        raise FixtureError(f"invalid public long-form source: {exc}") from exc
    if len(paths) < long_form.MIN_RELEASE_COMPOSITES:
        raise FixtureError("silence-gap probe needs at least two long-form composites")
    with (input_dir / "manifest.tsv").open("r", encoding="utf-8", newline="") as handle:
        source_rows = {row["composite_id"]: row
                       for row in csv.DictReader(handle, delimiter="\t")}
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.incomplete-",
                                       dir=output_dir.parent))
    try:
        (stage / MARKER_NAME).write_text(MARKER_TEXT, encoding="utf-8")
        with (stage / "manifest.tsv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS, delimiter="\t")
            writer.writeheader()
            for path in paths:
                fmt, speech, code, frames, _duration = long_form.read_wave(path)
                align = _format_alignment(fmt, code)
                start, edge = _choose_boundary(
                    source_rows[path.stem]["source_boundaries_seconds"],
                    frames, gap_frames)
                reference = long_form.normalized_reference(path.with_suffix(".txt"))
                for duration_ms in (0, gap_ms):
                    fixture_id = f"{path.stem}-gap{duration_ms:05d}ms"
                    gap = b"\0" * (duration_ms * SAMPLE_RATE // 1000 * align)
                    split = start * align
                    data = speech[:split] + gap + speech[split:]
                    audio_path = stage / f"{fixture_id}.wav"
                    long_form.write_wave(audio_path, fmt, data, code,
                                         frames + duration_ms * SAMPLE_RATE // 1000)
                    (stage / f"{fixture_id}.txt").write_text(
                        reference + "\n", encoding="utf-8")
                    writer.writerow({
                        "fixture_id": fixture_id,
                        "source_id": path.stem,
                        "gap_ms": duration_ms,
                        "gap_start_frame": start,
                        "nominal_edge_frame": edge,
                        "source_frames": frames,
                        "audio_sha256": _sha256(audio_path.read_bytes()),
                        "reference_sha256": _sha256(reference.encode("utf-8")),
                    })
        (stage / "README.txt").write_text(
            "Generated public interior-silence ASR probe. Each pair has identical "
            "speech sample bytes and reference; only an interior run of digital "
            "silence differs. Nominal 15-second edges do not prove FluidAudio's "
            "actual window placement. Repeated speech is report-only, not an "
            "independent release or candidate gate.\n",
            encoding="utf-8",
        )
        validate_output(stage)
        _publish(stage, output_dir, force)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return len(paths) * 2


def validate_output(output_dir: Path) -> int:
    _owned_output(output_dir)
    manifest = output_dir / "manifest.tsv"
    if not _regular(manifest):
        raise FixtureError("missing regular silence-gap manifest")
    with manifest.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise FixtureError("silence-gap manifest has an unexpected schema")
        rows = list(reader)
    grouped: dict[str, dict[int, tuple]] = {}
    ids: set[str] = set()
    gap_durations: set[int] = set()
    for row in rows:
        try:
            source_id = row["source_id"]
            duration_ms = int(row["gap_ms"])
            start = int(row["gap_start_frame"])
            edge = int(row["nominal_edge_frame"])
            source_frames = int(row["source_frames"])
            fixture_id = f"{source_id}-gap{duration_ms:05d}ms"
            if (not source_id or any(char in source_id for char in "/\\\t\r\n")
                    or source_id in (".", "..") or fixture_id != row["fixture_id"]
                    or fixture_id in ids or duration_ms < 0
                    or source_frames < 30 * SAMPLE_RATE
                    or edge % NOMINAL_WINDOW_FRAMES != 0):
                raise ValueError("invalid fixture identity")
            ids.add(fixture_id)
            audio_path = output_dir / f"{fixture_id}.wav"
            reference_path = output_dir / f"{fixture_id}.txt"
            if not _regular(audio_path) or not _regular(reference_path):
                raise FixtureError("missing regular silence-gap audio/reference")
            if _sha256(audio_path.read_bytes()) != row["audio_sha256"]:
                raise FixtureError("silence-gap audio digest changed")
            reference = long_form.normalized_reference(reference_path)
            if _sha256(reference.encode("utf-8")) != row["reference_sha256"]:
                raise FixtureError("silence-gap reference digest changed")
            fmt, data, code, frames, _duration = long_form.read_wave(audio_path)
            align = _format_alignment(fmt, code)
            if frames != source_frames + duration_ms * SAMPLE_RATE // 1000:
                raise FixtureError("silence-gap frame count changed")
            pair = grouped.setdefault(source_id, {})
            if duration_ms in pair:
                raise FixtureError("duplicate silence-gap pair member")
            pair[duration_ms] = (fmt, data, reference, align)
            if duration_ms:
                gap_durations.add(duration_ms)
            if duration_ms and not _edge_ok(start, edge, source_frames,
                                            _gap_frames(duration_ms)):
                raise FixtureError("silence-gap placement is not interior")
            pair.setdefault(-1, (start, edge, source_frames, 0))
            if pair[-1][:3] != (start, edge, source_frames):
                raise FixtureError("silence-gap pair metadata disagrees")
        except (TypeError, ValueError, KeyError) as exc:
            raise FixtureError("invalid silence-gap manifest row") from exc
        except long_form.FixtureError as exc:
            raise FixtureError(f"invalid silence-gap WAV/reference: {exc}") from exc
    if len(grouped) < long_form.MIN_RELEASE_COMPOSITES or len(gap_durations) != 1:
        raise FixtureError("silence-gap pairs are incomplete")
    gap_ms = next(iter(gap_durations))
    gap_frames = _gap_frames(gap_ms)
    base_digests: set[str] = set()
    for pair in grouped.values():
        if set(pair) != {-1, 0, gap_ms}:
            raise FixtureError("silence-gap pairs are incomplete")
        start, edge, source_frames, _unused = pair[-1]
        if not _edge_ok(start, edge, source_frames, gap_frames):
            raise FixtureError("silence-gap placement is not interior")
        base_fmt, base_data, base_reference, base_align = pair[0]
        gap_fmt, gap_data, gap_reference, gap_align = pair[gap_ms]
        digest = _sha256(base_fmt + base_data)
        if digest in base_digests:
            raise FixtureError("silence-gap source speech is duplicated across pairs")
        base_digests.add(digest)
        split = start * base_align
        inserted = gap_frames * base_align
        if (base_fmt != gap_fmt or base_reference != gap_reference
                or base_align != gap_align
                or gap_data[:split] != base_data[:split]
                or gap_data[split:split + inserted] != b"\0" * inserted
                or gap_data[split + inserted:] != base_data[split:]):
            raise FixtureError("silence-gap variants do not contain identical speech")
    expected = {MARKER_NAME, "manifest.tsv", "README.txt",
                *(f"{stem}.wav" for stem in ids),
                *(f"{stem}.txt" for stem in ids)}
    if ({path.name for path in output_dir.iterdir()} != expected
            or any(not _regular(output_dir / name) for name in expected)):
        raise FixtureError("silence-gap inventory does not match the manifest")
    return len(rows)


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="presspeech-silence-gap-self-test-") as temp:
        root = Path(temp)
        source = root / "source"
        source.mkdir()
        for index in range(6):
            path = source / f"source-{index}.wav"
            long_form.write_pcm16_fixture(path, 12, SAMPLE_RATE, index + 1)
            path.with_suffix(".txt").write_text(
                f"reference {index}\n", encoding="utf-8")
        long_dir = root / "long"
        long_form.compose(source, long_dir, 30.0, False)
        output = root / "gapped"
        assert compose(long_dir, output, DEFAULT_GAP_MS, False) == 4
        assert validate_output(output) == 4
        variant = output / "long-form-001-gap05000ms.wav"
        original = variant.read_bytes()
        variant.write_bytes(original[:-2] + b"\x01\x00")
        try:
            validate_output(output)
        except FixtureError as exc:
            assert "digest changed" in str(exc)
        else:
            raise AssertionError("altered audio passed validation")
        manifest = output / "manifest.tsv"
        with manifest.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        for row in rows:
            if row["fixture_id"] == variant.stem:
                row["audio_sha256"] = _sha256(variant.read_bytes())
        with manifest.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)
        try:
            validate_output(output)
        except FixtureError as exc:
            assert "identical speech" in str(exc)
        else:
            raise AssertionError("changed speech with updated digest passed validation")
        variant.write_bytes(original)
        for row in rows:
            if row["fixture_id"] == variant.stem:
                row["audio_sha256"] = _sha256(original)
        with manifest.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)
        assert validate_output(output) == 4
        try:
            compose(long_dir, output, DEFAULT_GAP_MS, False)
        except FixtureError as exc:
            assert "already exists" in str(exc)
        else:
            raise AssertionError("existing output replaced without --force")
        unowned = root / "unowned"
        unowned.mkdir()
        (unowned / "keep.txt").write_text("keep\n", encoding="utf-8")
        try:
            compose(long_dir, unowned, DEFAULT_GAP_MS, True)
        except FixtureError as exc:
            assert "unowned" in str(exc)
        else:
            raise AssertionError("unowned output replaced")
        assert (unowned / "keep.txt").is_file()
        assert compose(long_dir, output, 6000, True) == 4
        assert validate_output(output) == 4

        # A boundary exactly at each nominal edge cannot exercise the
        # intended within-silence condition; never split a source utterance
        # merely to make such a corpus pass.
        edge_source = root / "edge-source"
        edge_source.mkdir()
        for index in range(4):
            path = edge_source / f"edge-{index}.wav"
            long_form.write_pcm16_fixture(path, 15, SAMPLE_RATE, index + 1)
            path.with_suffix(".txt").write_text(
                f"edge reference {index}\n", encoding="utf-8")
        edge_long = root / "edge-long"
        long_form.compose(edge_source, edge_long, 30.0, False)
        try:
            compose(edge_long, output, DEFAULT_GAP_MS, True)
        except FixtureError as exc:
            assert "no interior source boundary" in str(exc)
        else:
            raise AssertionError("ineligible boundaries passed composition")
        assert validate_output(output) == 4

        # Public imported speech is Float32 WAVE; zeros must be true Float32
        # silence and the validator must preserve every non-gap sample byte.
        float_source = root / "float-source"
        float_source.mkdir()
        float_fmt = struct.pack(
            "<HHIIHH", 3, 1, SAMPLE_RATE, SAMPLE_RATE * 4, 4, 32)
        for index in range(6):
            path = float_source / f"float-{index}.wav"
            data = struct.pack("<f", 0.125 + index / 16) * (12 * SAMPLE_RATE)
            long_form.write_wave(path, float_fmt, data, 3, 12 * SAMPLE_RATE)
            path.with_suffix(".txt").write_text(
                f"float reference {index}\n", encoding="utf-8")
        float_long = root / "float-long"
        long_form.compose(float_source, float_long, 30.0, False)
        float_output = root / "float-gapped"
        assert compose(float_long, float_output, DEFAULT_GAP_MS, False) == 4
        assert validate_output(float_output) == 4
        _fmt, base, _code, _frames, _duration = long_form.read_wave(
            float_output / "long-form-001-gap00000ms.wav")
        _fmt, gapped, _code, _frames, _duration = long_form.read_wave(
            float_output / "long-form-001-gap05000ms.wav")
        split = 12 * SAMPLE_RATE * 4
        gap_bytes = DEFAULT_GAP_MS * SAMPLE_RATE // 1000 * 4
        assert gapped == base[:split] + b"\0" * gap_bytes + base[split:]
    print("public silence-gap fixture composer self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", default="public-audio/librispeech-dev-clean-long-form")
    parser.add_argument("--output-dir", default="public-audio/librispeech-dev-clean-silence-gap")
    parser.add_argument("--gap-ms", type=int, default=DEFAULT_GAP_MS)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--validate-output-dir", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    elif args.validate_output_dir:
        print(f"validated {validate_output(Path(args.output_dir))} silence-gap fixtures")
    else:
        count = compose(Path(args.input_dir), Path(args.output_dir), args.gap_ms,
                        args.force)
        print(f"silence-gap fixtures: {args.output_dir} ({count} variants)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FixtureError, long_form.FixtureError, OSError, UnicodeError,
            csv.Error) as exc:
        raise SystemExit(f"error: {exc}") from exc
