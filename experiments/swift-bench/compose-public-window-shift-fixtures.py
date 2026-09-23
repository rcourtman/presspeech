#!/usr/bin/env python3
"""Make paired long-form clips whose speech starts at different window positions.

This is a report-only model-behaviour probe, not an independent release corpus.
Every variant contains byte-identical source speech and the same reference;
only a bounded amount of digital silence is prepended.  Leading silence can
also affect preprocessing, so a difference is a signal for investigation, not
proof that a particular CoreML window or merge step caused it.
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

MARKER_NAME = ".presspeech-public-window-shift-fixtures"
MARKER_TEXT = "Presspeech generated public window-position speech fixtures\n"
FIELDS = (
    "fixture_id", "source_id", "leading_ms", "leading_frames",
    "source_frames", "audio_sha256", "reference_sha256",
)
DEFAULT_OFFSETS_MS = (0, 3500, 7000)
MAX_OFFSET_MS = 10000
SAMPLE_RATE = 16000


class FixtureError(Exception):
    """An input or generated probe is malformed or unsafe."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _regular(path: Path) -> bool:
    return not path.is_symlink() and path.is_file()


def _owned_output(path: Path) -> None:
    marker = path / MARKER_NAME
    if (path.is_symlink() or not path.is_dir() or not _regular(marker)
            or marker.read_bytes() != MARKER_TEXT.encode("utf-8")):
        raise FixtureError(f"refusing to replace unowned output directory: {path}")
    resolved = path.resolve()
    if resolved == Path(resolved.anchor) or resolved == Path.cwd().resolve():
        raise FixtureError(f"refusing to replace unsafe output directory: {path}")


def _format_details(fmt: bytes, code: int) -> int:
    """Accept only zero-silent 16 kHz mono PCM16 or IEEE Float32 WAVE."""
    if len(fmt) < 16:
        raise FixtureError("invalid WAVE format")
    _code, channels, rate, byte_rate, align, bits = struct.unpack_from("<HHIIHH", fmt)
    if code == 0xFFFE:
        # WAVE_FORMAT_EXTENSIBLE stores the actual format in the subformat GUID.
        guid_tail = bytes.fromhex("00001000800000aa00389b71")
        if (len(fmt) < 40 or struct.unpack_from("<H", fmt, 16)[0] < 22
                or fmt[26:28] != b"\0\0" or fmt[28:40] != guid_tail):
            raise FixtureError("unsupported extensible WAVE format")
        code = struct.unpack_from("<H", fmt, 24)[0]
    if ((code, bits) not in ((1, 16), (3, 32)) or channels != 1
            or rate != SAMPLE_RATE or align != bits // 8
            or byte_rate != SAMPLE_RATE * align):
        raise FixtureError("window-position probe requires 16 kHz mono PCM16 or Float32 WAVE")
    return align


def _offsets(value: str) -> tuple[int, ...]:
    try:
        items = tuple(int(item) for item in value.replace(",", " ").split())
    except ValueError as exc:
        raise FixtureError("offsets must be whole milliseconds") from exc
    if (len(items) < 2 or items[0] != 0 or len(items) != len(set(items))
            or sorted(items) != list(items)
            or any(item < 0 or item > MAX_OFFSET_MS for item in items)):
        raise FixtureError(
            "offsets must start at 0 and then increase uniquely up to 10000 ms"
        )
    return items


def _publish(stage: Path, output: Path, force: bool) -> None:
    if not output.exists() and not output.is_symlink():
        os.rename(stage, output)
        return
    if not force:
        raise FixtureError(f"output directory already exists: {output}")
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
        # Never delete the previous corpus if restoration also fails.
        try:
            os.rename(previous, output)
        except OSError as exc:
            raise FixtureError(f"replacement failed; previous corpus retained at {previous}") from exc
        backup.rmdir()
        raise
    shutil.rmtree(backup)


def compose(input_dir: Path, output_dir: Path, offsets: tuple[int, ...], force: bool) -> int:
    offsets = _offsets(" ".join(map(str, offsets)))
    if input_dir.is_symlink() or not input_dir.is_dir():
        raise FixtureError(f"public long-form input directory is missing or unsafe: {input_dir}")
    source = input_dir.resolve()
    destination = output_dir.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise FixtureError("input and output directories must not contain one another")
    if output_dir.exists() or output_dir.is_symlink():
        if not force:
            raise FixtureError(f"output directory already exists: {output_dir}")
        _owned_output(output_dir)
    try:
        paths = long_form.validate_output(input_dir)
    except long_form.FixtureError as exc:
        raise FixtureError(f"invalid public long-form source: {exc}") from exc
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.incomplete-", dir=output_dir.parent))
    try:
        (stage / MARKER_NAME).write_text(MARKER_TEXT, encoding="utf-8")
        with (stage / "manifest.tsv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS, delimiter="\t")
            writer.writeheader()
            for source_path in paths:
                fmt, speech, code, frames, _duration = long_form.read_wave(source_path)
                align = _format_details(fmt, code)
                reference = long_form.normalized_reference(source_path.with_suffix(".txt"))
                for offset_ms in offsets:
                    leading_frames = offset_ms * SAMPLE_RATE // 1000
                    stem = f"{source_path.stem}-lead{offset_ms:05d}ms"
                    audio_path = stage / f"{stem}.wav"
                    data = (b"\0" * (leading_frames * align)) + speech
                    long_form.write_wave(audio_path, fmt, data, code, frames + leading_frames)
                    (stage / f"{stem}.txt").write_text(reference + "\n", encoding="utf-8")
                    writer.writerow({
                        "fixture_id": stem,
                        "source_id": source_path.stem,
                        "leading_ms": offset_ms,
                        "leading_frames": leading_frames,
                        "source_frames": frames,
                        "audio_sha256": _sha256(audio_path.read_bytes()),
                        "reference_sha256": _sha256(reference.encode("utf-8")),
                    })
        shutil.copyfile(input_dir / "manifest.tsv", stage / "source-long-form-manifest.tsv")
        (stage / "README.txt").write_text(
            "Generated public long-form window-position probe.\n"
            f"Source: {input_dir}\nOffsets (ms): {', '.join(map(str, offsets))}\n"
            "Each variant has the same speech sample bytes and reference. "
            "Only leading digital silence changes. This repeated-speech corpus "
            "is report-only, not an independent release or candidate gate.\n",
            encoding="utf-8",
        )
        validate_output(stage)
        _publish(stage, output_dir, force)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return len(paths) * len(offsets)


def validate_output(output_dir: Path) -> int:
    _owned_output(output_dir)
    manifest = output_dir / "manifest.tsv"
    if not _regular(manifest):
        raise FixtureError("missing regular window-position manifest")
    with manifest.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise FixtureError("window-position manifest has an unexpected schema")
        rows = list(reader)
    grouped: dict[str, list[tuple[int, bytes, str, bytes, int, int]]] = {}
    ids: set[str] = set()
    for row in rows:
        try:
            source_id = row["source_id"]
            offset = int(row["leading_ms"])
            leading_frames = int(row["leading_frames"])
            source_frames = int(row["source_frames"])
            expected_id = f"{source_id}-lead{offset:05d}ms"
            if (not source_id or "/" in source_id or "\\" in source_id
                    or any(char in source_id for char in "\t\r\n")
                    or source_id in (".", "..") or expected_id != row["fixture_id"]
                    or expected_id in ids or offset < 0 or offset > MAX_OFFSET_MS
                    or leading_frames != offset * SAMPLE_RATE // 1000
                    or source_frames < SAMPLE_RATE * 30):
                raise ValueError("invalid fixture identity")
            ids.add(expected_id)
            audio_path = output_dir / f"{expected_id}.wav"
            ref_path = output_dir / f"{expected_id}.txt"
            if not _regular(audio_path) or not _regular(ref_path):
                raise FixtureError("missing regular window-position audio/reference")
            if _sha256(audio_path.read_bytes()) != row["audio_sha256"]:
                raise FixtureError("window-position audio digest changed")
            reference = long_form.normalized_reference(ref_path)
            if _sha256(reference.encode("utf-8")) != row["reference_sha256"]:
                raise FixtureError("window-position reference digest changed")
            fmt, data, code, frames, _duration = long_form.read_wave(audio_path)
            align = _format_details(fmt, code)
            if frames != leading_frames + source_frames:
                raise FixtureError("window-position frame count changed")
            grouped.setdefault(source_id, []).append(
                (offset, data, reference, fmt, leading_frames, align)
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise FixtureError("invalid window-position manifest row") from exc
        except long_form.FixtureError as exc:
            raise FixtureError(f"invalid window-position WAV/reference: {exc}") from exc
    if len(grouped) < long_form.MIN_RELEASE_COMPOSITES:
        raise FixtureError("window-position probe requires at least two source composites")
    expected_offsets = None
    base_digests: set[str] = set()
    for variants in grouped.values():
        variants.sort(key=lambda item: item[0])
        offsets = tuple(item[0] for item in variants)
        if (len(offsets) < 2 or offsets[0] != 0 or len(offsets) != len(set(offsets))
                or (expected_offsets is not None and offsets != expected_offsets)):
            raise FixtureError("window-position pairs are incomplete")
        expected_offsets = offsets
        _offsets(" ".join(map(str, offsets)))
        _offset, base_data, base_reference, base_fmt, _frames, _align = variants[0]
        digest = _sha256(base_fmt + base_data)
        if digest in base_digests:
            raise FixtureError("window-position source speech is duplicated across pairs")
        base_digests.add(digest)
        for _offset, data, reference, fmt, leading_frames, align in variants[1:]:
            prefix_bytes = leading_frames * align
            if (reference != base_reference or fmt != base_fmt
                    or data[:prefix_bytes] != b"\0" * prefix_bytes
                    or data[prefix_bytes:] != base_data):
                raise FixtureError("window-position variants do not contain identical speech")
    expected_files = {
        MARKER_NAME, "manifest.tsv", "README.txt", "source-long-form-manifest.tsv",
        *(f"{stem}.wav" for stem in ids),
        *(f"{stem}.txt" for stem in ids),
    }
    observed = list(output_dir.iterdir())
    if ({path.name for path in observed} != expected_files
            or any(not _regular(output_dir / name) for name in expected_files)):
        raise FixtureError("window-position inventory does not match the manifest")
    return len(rows)


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="presspeech-window-position-self-test-") as temp:
        root = Path(temp)
        source = root / "source"
        source.mkdir()
        for index in range(4):
            path = source / f"source-{index}.wav"
            long_form.write_pcm16_fixture(path, 16, SAMPLE_RATE, index + 1)
            path.with_suffix(".txt").write_text(f"reference {index}\n", encoding="utf-8")
        long_dir = root / "long"
        long_form.compose(source, long_dir, 30.0, False)
        output = root / "shifted"
        assert compose(long_dir, output, DEFAULT_OFFSETS_MS, False) == 6
        assert validate_output(output) == 6
        variant = output / "long-form-001-lead03500ms.wav"
        original = variant.read_bytes()
        variant.write_bytes(original[:-2] + b"\x01\x00")
        try:
            validate_output(output)
        except FixtureError as exc:
            assert "digest changed" in str(exc)
        else:
            raise AssertionError("altered audio passed validation")
        variant.write_bytes(original)
        # Re-signing a changed file in the manifest must not defeat the
        # byte-exact speech-pair invariant.
        variant.write_bytes(original[:-2] + b"\x01\x00")
        manifest_path = output / "manifest.tsv"
        with manifest_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        for row in rows:
            if row["fixture_id"] == variant.stem:
                row["audio_sha256"] = _sha256(variant.read_bytes())
        with manifest_path.open("w", encoding="utf-8", newline="") as handle:
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
        with manifest_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)
        try:
            compose(long_dir, output, DEFAULT_OFFSETS_MS, False)
        except FixtureError as exc:
            assert "already exists" in str(exc)
        else:
            raise AssertionError("existing output replaced without --force")
        unowned = root / "unowned"
        unowned.mkdir()
        (unowned / "keep.txt").write_text("keep\n", encoding="utf-8")
        try:
            compose(long_dir, unowned, DEFAULT_OFFSETS_MS, True)
        except FixtureError as exc:
            assert "unowned" in str(exc)
        else:
            raise AssertionError("unowned output replaced")
        assert (unowned / "keep.txt").is_file()
        source_ref = long_dir / "long-form-001.txt"
        original_ref = source_ref.read_bytes()
        source_ref.write_text("changed source reference\n", encoding="utf-8")
        try:
            compose(long_dir, output, (0, 5000), True)
        except FixtureError as exc:
            assert "invalid public long-form source" in str(exc)
        else:
            raise AssertionError("invalid source replaced prior output")
        assert validate_output(output) == 6
        source_ref.write_bytes(original_ref)
        assert compose(long_dir, output, (0, 5000), True) == 4
        assert validate_output(output) == 4
        extra_audio = output / "extra.mp3"
        extra_audio.write_bytes(b"not speech")
        try:
            validate_output(output)
        except FixtureError as exc:
            assert "inventory" in str(exc)
        else:
            raise AssertionError("extra benchmark audio passed validation")
        extra_audio.unlink()

        # The public importer emits Float32 WAVE, not just the PCM16 files
        # used by the long-form composer's existing synthetic self-test.
        float_source = root / "float-source"
        float_source.mkdir()
        float_fmt = struct.pack("<HHIIHH", 3, 1, SAMPLE_RATE, SAMPLE_RATE * 4, 4, 32)
        for index in range(4):
            path = float_source / f"float-{index}.wav"
            data = struct.pack("<f", 0.125 + index / 16) * (16 * SAMPLE_RATE)
            long_form.write_wave(path, float_fmt, data, 3, 16 * SAMPLE_RATE)
            path.with_suffix(".txt").write_text(f"float reference {index}\n", encoding="utf-8")
        float_long = root / "float-long"
        long_form.compose(float_source, float_long, 30.0, False)
        float_output = root / "float-shifted"
        assert compose(float_long, float_output, (0, 3500), False) == 4
        assert validate_output(float_output) == 4
        _fmt, shifted_data, _code, _frames, _duration = long_form.read_wave(
            float_output / "long-form-001-lead03500ms.wav"
        )
        assert shifted_data[:3500 * SAMPLE_RATE // 1000 * 4] == (
            b"\0" * (3500 * SAMPLE_RATE // 1000 * 4)
        )
        assert shifted_data[3500 * SAMPLE_RATE // 1000 * 4:] == (
            long_form.read_wave(float_output / "long-form-001-lead00000ms.wav")[1]
        )
    print("public window-position fixture composer self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", default="public-audio/librispeech-dev-clean-long-form")
    parser.add_argument("--output-dir", default="public-audio/librispeech-dev-clean-window-shift")
    parser.add_argument("--offsets-ms", default=",".join(map(str, DEFAULT_OFFSETS_MS)))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--validate-output-dir", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    elif args.validate_output_dir:
        print(f"validated {validate_output(Path(args.output_dir))} window-position fixtures")
    else:
        count = compose(Path(args.input_dir), Path(args.output_dir), _offsets(args.offsets_ms), args.force)
        print(f"window-position fixtures: {args.output_dir} ({count} variants)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FixtureError, long_form.FixtureError, OSError, UnicodeError, csv.Error) as exc:
        raise SystemExit(f"error: {exc}") from exc
