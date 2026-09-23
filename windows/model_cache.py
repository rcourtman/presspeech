"""Resolve complete pinned inference snapshots before loading model backends.

Cache lookup never contacts the Hub. Only a missing snapshot or required file
permits one anonymous pinned download; corrupt content and permission errors do
not become network retries. Backend construction remains local-only afterward.
"""
import json
import errno
import hashlib
from contextlib import contextmanager
import os
from pathlib import Path, PurePosixPath
import re
import stat
import shutil
import tempfile
import sys
import time

import model_network


class ModelCacheMissingError(FileNotFoundError):
    """A required inference file is absent from a pinned snapshot."""


class ModelCacheCorruptError(ValueError):
    """Present cached input cannot be used; do not silently download around it."""


class ModelCacheIntegrityError(ModelCacheCorruptError):
    """A pinned model file did not match Presspeech's SHA-256 manifest."""


def _download_progress_class(callback):
    """Build a silent Hub progress bar that reports received bytes to the UI."""
    from tqdm.auto import tqdm

    class DownloadProgress(tqdm):
        def __init__(self, *args, **kwargs):
            self._presspeech_tracks_download = (
                kwargs.get("unit") == "B" and
                kwargs.get("desc") == "Downloading bytes")
            self._presspeech_callback = callback
            self._presspeech_last_report = None
            # Hub passes this class to its transfer, reconstruction, and file
            # count bars. Track only the transfer-byte bar and never render a
            # second console bar in the windowed app.
            super().__init__(*args, **kwargs)

        def display(self, msg=None, pos=None):
            if not self._presspeech_tracks_download:
                return None
            now = time.monotonic()
            if (self._presspeech_last_report is not None and
                    now - self._presspeech_last_report < 0.25):
                return None
            self._presspeech_last_report = now
            try:
                self._presspeech_callback(self.n, self.total)
            except Exception:
                # Progress is advisory; a UI observer must never abort a model
                # download or change its cache/error handling.
                pass
            return None

    return DownloadProgress


def offline_requested():
    truthy = {"1", "ON", "YES", "TRUE"}
    if any(os.environ.get(name, "").strip().upper() in truthy
           for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")):
        return True
    constants = sys.modules.get("huggingface_hub.constants")
    return bool(getattr(constants, "HF_HUB_OFFLINE", False))


def _file_fingerprint(path, metadata):
    """Capture stable file identity for the local post-verification cache."""
    return {
        "path": str(path.resolve(strict=True)),
        "device": int(metadata.st_dev),
        "inode": int(metadata.st_ino),
        "size": int(metadata.st_size),
        "mtime_ns": int(getattr(
            metadata, "st_mtime_ns", metadata.st_mtime * 1_000_000_000)),
        "ctime_ns": int(getattr(
            metadata, "st_ctime_ns", metadata.st_ctime * 1_000_000_000)),
    }


def _file_stat_identity(metadata):
    return tuple(int(getattr(metadata, name)) for name in
                 ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
                 if hasattr(metadata, name))


def _calculate_sha256(path, name):
    """Hash a regular file and reject changes during the verification read."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ModelCacheIntegrityError(
                    "pinned model file is not a regular file: " + name)
            before_path = path.stat()
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
            after = os.fstat(stream.fileno())
        current = path.stat()
    except ModelCacheIntegrityError:
        raise
    except OSError as exc:
        raise ModelCacheIntegrityError(
            "pinned model file could not be verified: " + name) from exc

    # Windows can report different metadata for fstat(handle) and stat(path)
    # on the same unchanged file. Compare each API with itself instead.
    if (_file_stat_identity(before) != _file_stat_identity(after) or
            _file_stat_identity(before_path) != _file_stat_identity(current)):
        raise ModelCacheIntegrityError(
            "pinned model file changed while being verified: " + name)
    return digest.hexdigest(), _file_fingerprint(path, current)


def _integrity_marker_path(integrity_cache_dir, repository, revision):
    identity = hashlib.sha256(
        (repository + "\0" + revision).encode("utf-8")).hexdigest()
    return Path(integrity_cache_dir) / (identity + ".json")


def _manifest_digest(expected_sha256s):
    encoded = json.dumps(
        expected_sha256s, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _integrity_marker_matches(marker_path, snapshot, manifest_digest, files):
    try:
        with marker_path.open(encoding="utf-8") as stream:
            marker = json.load(stream)
    except (OSError, ValueError, UnicodeError):
        return False
    return (
        isinstance(marker, dict) and
        marker.get("schema_version") == 1 and
        marker.get("snapshot") == str(snapshot.resolve(strict=True)) and
        marker.get("manifest_sha256") == manifest_digest and
        marker.get("files") == files
    )


def _write_integrity_marker(marker_path, snapshot, manifest_digest, files):
    """Persist only a performance hint; missing/unwritable markers re-hash."""
    temporary = None
    try:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n",
                prefix=".presspeech-integrity-", suffix=".tmp",
                dir=str(marker_path.parent), delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({
                "schema_version": 1,
                "snapshot": str(snapshot.resolve(strict=True)),
                "manifest_sha256": manifest_digest,
                "files": files,
            }, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
        os.replace(temporary, marker_path)
    except OSError:
        # Verification remains authoritative. A read-only or unavailable
        # marker directory only means the next launch must hash the files again.
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _verify_snapshot_integrity(
        path, present, expected_sha256s, repository, revision,
        integrity_cache_dir):
    files = {}
    for name in expected_sha256s:
        if name not in present:
            continue  # Optional files may be absent at their reviewed revision.
        item = path / name
        files[name] = _file_fingerprint(item, item.stat())

    manifest_digest = _manifest_digest(expected_sha256s)
    marker_path = (
        _integrity_marker_path(integrity_cache_dir, repository, revision)
        if integrity_cache_dir is not None else None)
    if (marker_path is not None and
            _integrity_marker_matches(marker_path, path, manifest_digest, files)):
        return

    for name, expected in expected_sha256s.items():
        if name not in present:
            continue
        actual, fingerprint = _calculate_sha256(path / name, name)
        if actual != expected:
            raise ModelCacheIntegrityError(
                "pinned model SHA-256 mismatch: " + name)
        files[name] = fingerprint

    if marker_path is not None:
        _write_integrity_marker(marker_path, path, manifest_digest, files)


def _validate_snapshot(
        snapshot, revision, required_files, optional_files=(), required_any=(),
        expected_sha256s=None, repository=None, integrity_cache_dir=None):
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
    if expected_sha256s is not None:
        _verify_snapshot_integrity(
            path, present, expected_sha256s, repository, revision,
            integrity_cache_dir)

    for name in present:
        if name.endswith(".json"):
            item = path / name
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
        required_any=(), local_only=False, progress=None,
        expected_sha256s=None, integrity_cache_dir=None):
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
    if expected_sha256s is not None:
        if (not isinstance(expected_sha256s, dict) or
                set(expected_sha256s) != set(all_files) or
                any(not isinstance(name, str) or
                    not isinstance(digest, str) or
                    not re.fullmatch(r"[0-9a-f]{64}", digest)
                    for name, digest in expected_sha256s.items())):
            raise ValueError(
                "SHA-256 manifest must cover every reviewed inference file")
    for name in all_files:
        parsed = PurePosixPath(name)
        if (parsed.is_absolute() or ".." in parsed.parts or "\\" in name or
                any(char in name for char in "*?[") or str(parsed) != name):
            raise ValueError("model inference file contract must contain exact relative paths")
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError

    def attempt(local_only):
        model_network.harden_loaded_runtime()
        options = {}
        if progress is not None and not local_only:
            options["tqdm_class"] = _download_progress_class(
                lambda done, total: _report_progress(
                    progress, "downloading", done, total))
        snapshot = snapshot_download(
            repository, revision=revision, token=False,
            local_files_only=local_only, allow_patterns=list(all_files),
            **options)
        if expected_sha256s is not None:
            _report_progress(progress, "verifying")
        return _validate_snapshot(
            snapshot, revision, required_files, optional_files, required_any,
            expected_sha256s=expected_sha256s, repository=repository,
            integrity_cache_dir=integrity_cache_dir)

    try:
        snapshot = attempt(True)
        _report_progress(progress, "loading")
        return snapshot
    except (LocalEntryNotFoundError, ModelCacheMissingError) as exc:
        if local_only:
            if isinstance(exc, LocalEntryNotFoundError):
                raise ModelCacheMissingError(
                    "pinned model snapshot is not locally available") from exc
            raise
        if offline_requested():
            raise
    _report_progress(progress, "downloading")
    # Do not wrap this attempt or backend loading: a second miss, HTTP failure,
    # invalid file, incompatible model or native parsing error must stay visible.
    snapshot = attempt(False)
    _report_progress(progress, "loading")
    return snapshot


def _report_progress(callback, phase, done=None, total=None):
    if callback is None:
        return
    try:
        callback(phase, done, total)
    except Exception:
        # Progress is advisory and must not affect model-load correctness.
        pass


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
