"""Content identity for paired Windows ASR benchmark runs.

Only the aggregate digest is reported. Neither individual audio hashes nor
reference text are exposed by this module's output.
"""

import hashlib
import json


_DOMAIN = b"presspeech-windows-benchmark-inputs-v1\0"
_RECORDED_TAIL_DOMAIN = b"presspeech-windows-recorded-tail-probe-v1\0"


def asr_audio_sha256(audio):
    """Hash the contiguous float32 samples actually passed to the model."""
    return hashlib.sha256(memoryview(audio).cast("B")).hexdigest()


def benchmark_inputs_sha256(rows):
    """Hash the multiset of effective audio and scoring inputs.

    File paths, sample IDs, manifest order, model, and run count are excluded:
    the first three may change without changing a comparison corpus, while the
    latter two are separate report fields. Duplicate rows remain significant.
    """
    row_hashes = []
    for row in rows:
        audio_digest = row["asr_audio_sha256"]
        if (not isinstance(audio_digest, str) or len(audio_digest) != 64
                or any(char not in "0123456789abcdef" for char in audio_digest)):
            raise ValueError("ASR audio SHA-256 must be lowercase hexadecimal")
        relevant = {
            "asr_audio_sha256": audio_digest,
            "audio_seconds": row["audio_seconds"],
            "source_sample_rate": row["source_sample_rate"],
            "reference": row.get("reference", ""),
            "reference_reviewed": row.get("reference_reviewed", False),
            "expected_silence": row.get("expected_silence", False),
            "task_group": row.get("task_group"),
            "language_group": row.get("language_group"),
        }
        encoded = json.dumps(
            relevant, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        row_hashes.append(hashlib.sha256(encoded).digest())

    digest = hashlib.sha256(_DOMAIN)
    for row_hash in sorted(row_hashes):
        digest.update(row_hash)
    return digest.hexdigest()


def recorded_tail_probe_inputs_sha256(rows):
    """Identify paired recorded-tail inputs without publishing per-clip hashes.

    Compare this *and* benchmark_inputs_sha256 when comparing recorded-tail
    reports: the ordinary corpus digest deliberately excludes probe endpoints.
    """
    row_hashes = []
    for row in rows:
        audio_digest = row["asr_audio_sha256"]
        trim_at_sample = row["trim_at_sample"]
        if (not isinstance(audio_digest, str) or len(audio_digest) != 64
                or any(char not in "0123456789abcdef" for char in audio_digest)):
            raise ValueError("ASR audio SHA-256 must be lowercase hexadecimal")
        if (isinstance(trim_at_sample, bool) or not isinstance(trim_at_sample, int)
                or trim_at_sample < 1):
            raise ValueError("recorded-tail trim point must be a positive sample count")
        encoded = json.dumps(
            [audio_digest, trim_at_sample], separators=(",", ":")
        ).encode("utf-8")
        row_hashes.append(hashlib.sha256(encoded).digest())

    digest = hashlib.sha256(_RECORDED_TAIL_DOMAIN)
    for row_hash in sorted(row_hashes):
        digest.update(row_hash)
    return digest.hexdigest()
