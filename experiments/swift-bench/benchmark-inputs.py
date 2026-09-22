#!/usr/bin/env python3
"""Freeze and fingerprint paired audio/reference benchmark fixtures.

The emitted SHA-256 folds every pair into one rename-independent corpus
identity. The fingerprint itself exposes neither individual hashes nor names.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import tempfile
from typing import Optional


SCHEMA_VERSION = 1
MARKER_NAME = ".presspeech-benchmark-inputs"
MARKER_TEXT = "Presspeech frozen benchmark inputs\n"
MANIFEST_NAME = "manifest.json"


class InputError(ValueError):
    """A fixture set cannot provide stable benchmark evidence."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def require_regular_file(path: Path, label: str) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise InputError(f"{label} is missing or unreadable") from exc
    if path.is_symlink() or not stat.S_ISREG(mode):
        raise InputError(f"{label} must be a regular file, not a symbolic link")


def copy_stable(source: Path, destination: Path, label: str) -> str:
    """Copy one file and reject content that changes during the snapshot."""
    require_regular_file(source, label)
    try:
        before = file_sha256(source)
        with source.open("rb") as reader, destination.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
        copied = file_sha256(destination)
        require_regular_file(source, label)
        after = file_sha256(source)
    except OSError as exc:
        raise InputError(f"could not freeze {label}") from exc
    if before != copied or before != after:
        raise InputError(f"{label} changed while it was being frozen")
    destination.chmod(0o400)
    return copied


def corpus_digest(entries: list[dict[str, object]]) -> str:
    # Names and source paths are deliberately excluded.  The suffix remains
    # because it can affect decoder selection; sorting makes a copied or
    # renamed corpus retain its identity while preserving duplicate pairs.
    pairs = sorted(
        (
            str(entry["audio_suffix"]),
            str(entry["audio_sha256"]),
            str(entry["reference_sha256"] or "missing"),
        )
        for entry in entries
    )
    payload = {
        "domain": "presspeech-benchmark-fixture-set-v1",
        "pairs": pairs,
    }
    encoded = json.dumps(
        payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def safe_snapshot_path(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or any(part in ("", ".", "..") for part in pure.parts):
        raise InputError("snapshot manifest contains an unsafe relative path")
    path = root.joinpath(*pure.parts)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise InputError("snapshot manifest path escapes its directory") from exc
    return path


def snapshot(audio_paths: list[Path], output_dir: Path, allow_missing_reference: bool) -> str:
    if not audio_paths:
        raise InputError("at least one audio fixture is required")
    if output_dir.exists() or output_dir.is_symlink():
        raise InputError("snapshot output already exists")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.incomplete-", dir=output_dir.parent)
    )
    entries: list[dict[str, object]] = []
    try:
        (stage / MARKER_NAME).write_text(MARKER_TEXT, encoding="utf-8")
        for index, audio in enumerate(audio_paths, 1):
            suffix = audio.suffix
            if not suffix:
                raise InputError("audio fixture has no filename extension")
            fixture_dir = stage / f"{index:06d}"
            fixture_dir.mkdir()
            frozen_audio = fixture_dir / f"audio{suffix}"
            audio_hash = copy_stable(audio, frozen_audio, "audio fixture")

            reference = audio.with_suffix(".txt")
            reference_hash: Optional[str] = None
            frozen_reference: Optional[str] = None
            if reference.exists() or reference.is_symlink():
                destination = fixture_dir / "audio.txt"
                reference_hash = copy_stable(reference, destination, "reference sidecar")
                frozen_reference = destination.relative_to(stage).as_posix()
            elif not allow_missing_reference:
                raise InputError("audio fixture is missing its reference sidecar")

            entries.append(
                {
                    "audio": frozen_audio.relative_to(stage).as_posix(),
                    "audio_suffix": suffix.casefold(),
                    "audio_sha256": audio_hash,
                    "reference": frozen_reference,
                    "reference_sha256": reference_hash,
                }
            )

        digest = corpus_digest(entries)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "corpus_sha256": digest,
            "entries": entries,
        }
        manifest_path = stage / MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        manifest_path.chmod(0o400)
        os.replace(stage, output_dir)
        return digest
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def load_manifest(snapshot_dir: Path) -> dict[str, object]:
    if snapshot_dir.is_symlink() or not snapshot_dir.is_dir():
        raise InputError("snapshot directory is missing or unsafe")
    marker = snapshot_dir / MARKER_NAME
    require_regular_file(marker, "snapshot ownership marker")
    if marker.read_text(encoding="utf-8") != MARKER_TEXT:
        raise InputError("snapshot ownership marker is invalid")
    manifest_path = snapshot_dir / MANIFEST_NAME
    require_regular_file(manifest_path, "snapshot manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputError("snapshot manifest is unreadable") from exc
    if not isinstance(manifest, dict):
        raise InputError("snapshot manifest has an invalid root")
    return manifest


def verify(snapshot_dir: Path) -> str:
    manifest = load_manifest(snapshot_dir)
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise InputError("snapshot manifest has an unsupported schema")
    raw_entries = manifest.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise InputError("snapshot manifest contains no fixtures")

    entries: list[dict[str, object]] = []
    for raw in raw_entries:
        if not isinstance(raw, dict) or set(raw) != {
            "audio",
            "audio_suffix",
            "audio_sha256",
            "reference",
            "reference_sha256",
        }:
            raise InputError("snapshot manifest entry has an unexpected schema")
        audio_relative = raw["audio"]
        if not isinstance(audio_relative, str):
            raise InputError("snapshot manifest audio path is invalid")
        audio = safe_snapshot_path(snapshot_dir, audio_relative)
        require_regular_file(audio, "frozen audio fixture")
        if file_sha256(audio) != raw["audio_sha256"]:
            raise InputError("frozen audio fixture changed after snapshot")

        reference_relative = raw["reference"]
        reference_hash = raw["reference_sha256"]
        if reference_relative is None:
            if reference_hash is not None:
                raise InputError("snapshot manifest has inconsistent reference evidence")
        else:
            if not isinstance(reference_relative, str) or not isinstance(reference_hash, str):
                raise InputError("snapshot manifest reference evidence is invalid")
            reference = safe_snapshot_path(snapshot_dir, reference_relative)
            require_regular_file(reference, "frozen reference sidecar")
            if file_sha256(reference) != reference_hash:
                raise InputError("frozen reference sidecar changed after snapshot")
        entries.append(raw)

    digest = corpus_digest(entries)
    if manifest.get("corpus_sha256") != digest:
        raise InputError("snapshot corpus fingerprint does not match its manifest")
    return digest


def run_self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="presspeech-benchmark-inputs-test-") as temporary:
        root = Path(temporary)
        source = root / "Private Client Name"
        source.mkdir()
        audio = source / "confidential-note.WAV"
        reference = audio.with_suffix(".txt")
        audio.write_bytes(b"RIFF private audio bytes")
        reference.write_text("private dictated reference\n", encoding="utf-8")

        first = root / "first"
        first_digest = snapshot([audio], first, False)
        if verify(first) != first_digest or len(first_digest) != 64:
            raise AssertionError("snapshot fingerprint did not verify")
        manifest_text = (first / MANIFEST_NAME).read_text(encoding="utf-8")
        private_values = (
            source.name,
            audio.name,
            reference.read_text(encoding="utf-8").strip(),
        )
        for private_value in private_values:
            if private_value in manifest_text:
                raise AssertionError("snapshot manifest exposed a private name or reference")

        renamed = root / "renamed"
        renamed.mkdir()
        renamed_audio = renamed / "copy.wav"
        renamed_audio.write_bytes(audio.read_bytes())
        renamed_audio.with_suffix(".txt").write_bytes(reference.read_bytes())
        second_digest = snapshot([renamed_audio], root / "second", False)
        if second_digest != first_digest:
            raise AssertionError("renaming an unchanged fixture changed its fingerprint")

        audio.write_bytes(b"changed after snapshot")
        reference.write_text("changed after snapshot\n", encoding="utf-8")
        if verify(first) != first_digest:
            raise AssertionError("editing originals changed frozen benchmark inputs")

        frozen_reference = first / "000001" / "audio.txt"
        frozen_reference.chmod(0o600)
        frozen_reference.write_text("mutated frozen reference\n", encoding="utf-8")
        try:
            verify(first)
        except InputError as exc:
            if "changed after snapshot" not in str(exc):
                raise
        else:
            raise AssertionError("mutated frozen input passed verification")

        missing = root / "missing.wav"
        missing.write_bytes(b"audio without reference")
        try:
            snapshot([missing], root / "missing-rejected", False)
        except InputError as exc:
            if "missing its reference" not in str(exc):
                raise
        else:
            raise AssertionError("missing reference was accepted without an opt-in")
        missing_digest = snapshot([missing], root / "missing-allowed", True)
        if verify(root / "missing-allowed") != missing_digest:
            raise AssertionError("opt-in missing-reference snapshot did not verify")

    print("benchmark input snapshot self-test passed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    subparsers = parser.add_subparsers(dest="command")

    snapshot_parser = subparsers.add_parser("snapshot")
    snapshot_parser.add_argument("--output-dir", type=Path, required=True)
    snapshot_parser.add_argument("--allow-missing-reference", action="store_true")
    snapshot_parser.add_argument("audio", nargs="+", type=Path)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--snapshot-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.self_test:
            if args.command is not None:
                raise InputError("--self-test cannot be combined with a command")
            run_self_test()
            return 0
        if args.command == "snapshot":
            print(snapshot(args.audio, args.output_dir, args.allow_missing_reference))
            return 0
        if args.command == "verify":
            print(verify(args.snapshot_dir))
            return 0
        raise InputError("choose snapshot, verify, or --self-test")
    except (InputError, OSError, UnicodeError) as exc:
        print(f"benchmark input snapshot failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
