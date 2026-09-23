"""Resolve complete pinned inference snapshots before loading model backends.

Cache lookup never contacts the Hub. Only a missing snapshot or required file
permits one anonymous pinned download; corrupt content and permission errors do
not become network retries. Backend construction remains local-only afterward.
"""
import json
import errno
from contextlib import contextmanager
import os
from pathlib import Path, PurePosixPath
import re
import stat
import shutil
import tempfile
import sys

import model_network


class ModelCacheMissingError(FileNotFoundError):
    """A required inference file is absent from a pinned snapshot."""


class ModelCacheCorruptError(ValueError):
    """Present cached input cannot be used; do not silently download around it."""


def offline_requested():
    truthy = {"1", "ON", "YES", "TRUE"}
    if any(os.environ.get(name, "").strip().upper() in truthy
           for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")):
        return True
    constants = sys.modules.get("huggingface_hub.constants")
    return bool(getattr(constants, "HF_HUB_OFFLINE", False))


def _validate_snapshot(snapshot, revision, required_files, optional_files=(), required_any=()):
    path = Path(snapshot)
    if path.name != revision:
        raise ModelCacheCorruptError("model cache is not the requested pinned snapshot")
    missing = []
    # Check present files before reporting missing ones: a corrupt config must
    # not be hidden by fetching an unrelated missing weight file.
    present = set()
    for name in (*required_files, *optional_files):
        item = path / name
        try:
            metadata = item.stat()
        except FileNotFoundError:
            if name in required_files:
                missing.append(name)
            continue
        present.add(name)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size == 0:
            raise ModelCacheCorruptError("cached model input is empty or not a file: " + name)
        if name.endswith(".json"):
            try:
                with item.open(encoding="utf-8") as stream:
                    value = json.load(stream)
            except (ValueError, UnicodeError) as exc:
                raise ModelCacheCorruptError("cached model JSON is invalid: " + name) from exc
            if name != "vocabulary.json" and not isinstance(value, dict):
                raise ModelCacheCorruptError("cached model JSON must be an object: " + name)
    for alternatives in required_any:
        if not present.intersection(alternatives):
            missing.append("one of (" + ", ".join(alternatives) + ")")
    if missing:
        raise ModelCacheMissingError("pinned model cache is incomplete: " + ", ".join(missing))
    return str(path)


def resolve_snapshot(
        repository, revision, required_files, *, optional_files=(),
        required_any=(), local_only=False):
    """Resolve only the reviewed inference files; never fetch alternative weights."""
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("model revision must be an immutable commit")
    required_files = tuple(required_files)
    optional_files = tuple(optional_files)
    required_any = tuple(tuple(group) for group in required_any)
    all_files = required_files + optional_files
    if not required_files or len(set(all_files)) != len(all_files):
        raise ValueError("model inference file contract must be nonempty and unique")
    if any(not group or not set(group).issubset(all_files) for group in required_any):
        raise ValueError("alternative requirements must name reviewed inference files")
    for name in all_files:
        parsed = PurePosixPath(name)
        if (parsed.is_absolute() or ".." in parsed.parts or "\\" in name or
                any(char in name for char in "*?[") or str(parsed) != name):
            raise ValueError("model inference file contract must contain exact relative paths")
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError

    def attempt(local_only):
        model_network.harden_loaded_runtime()
        snapshot = snapshot_download(
            repository, revision=revision, token=False,
            local_files_only=local_only, allow_patterns=list(all_files))
        return _validate_snapshot(snapshot, revision, required_files, optional_files, required_any)

    try:
        return attempt(True)
    except (LocalEntryNotFoundError, ModelCacheMissingError) as exc:
        if local_only:
            if isinstance(exc, LocalEntryNotFoundError):
                raise ModelCacheMissingError(
                    "pinned model snapshot is not locally available") from exc
            raise
        if offline_requested():
            raise
    # Do not wrap this attempt or backend loading: a second miss, HTTP failure,
    # invalid file, incompatible model or native parsing error must stay visible.
    return attempt(False)


@contextmanager
def whisper_snapshot(snapshot, required_files, *, optional_files=(), scratch_root=None):
    """Keep a private local tokenizer path for the supported Whisper constructor.

    Small inputs are copied. A resolved weight hardlink survives removal of the
    Hub path without duplicating GBs; it does not protect against in-place weight
    modification. Cross-device/unsupported-link filesystems copy weights instead.
    Other I/O errors propagate and never authorize a network retry.
    """
    source = Path(snapshot)
    with tempfile.TemporaryDirectory(prefix="presspeech-whisper-", dir=scratch_root) as directory:
        private = Path(directory) / source.name
        private.mkdir()
        for name in (*required_files, *optional_files):
            origin = source / name
            if name in optional_files:
                try:
                    origin.stat()
                except FileNotFoundError:
                    continue
            target = private / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if name == "model.bin":
                try:
                    os.link(origin.resolve(strict=True), target)
                except OSError as exc:
                    if exc.errno not in {errno.EXDEV, errno.ENOTSUP, errno.EOPNOTSUPP}:
                        raise
                    shutil.copyfile(origin, target)
            else:
                shutil.copyfile(origin, target)
        _validate_snapshot(private, source.name, required_files, optional_files)
        yield str(private)
