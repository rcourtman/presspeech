"""Prepare the public Parakeet trailing-silence repro for the local benchmark.

The source is the exact public audio used in NVIDIA-NeMo/Speech#15757.  This
script does not score its reported model output as a human-reviewed reference
and does not change Presspeech's production transcription policy.
"""

import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
from urllib.request import urlopen


SOURCE_URL = (
    "https://raw.githubusercontent.com/huggingface/speech-to-speech/"
    "006da8deacf75050959d7d3fdde07670ac379ca1/"
    "src/speech_to_speech/TTS/ref_audio.wav"
)
SOURCE_SHA256 = "c8f3be0d4e32aa3ab4bbc859bb5c2ae176d2170853661edc65c94f232ecca232"
MAX_SOURCE_BYTES = 1_000_000
SAMPLE_RATE = 16_000
CROP_START_SAMPLE = SAMPLE_RATE
CROP_END_SAMPLE = 32 * SAMPLE_RATE // 10  # 3.2 s; upstream starts at 1.0 s.
FIXTURE_DIR_NAME = "public-parakeet-tail"
CLIP_NAME = "upstream-short-speech.wav"


def download_source(opener=urlopen):
    """Fetch only the pinned public bytes; reject truncation or mutation."""
    with opener(SOURCE_URL, timeout=30) as response:
        source = response.read(MAX_SOURCE_BYTES + 1)
    if len(source) > MAX_SOURCE_BYTES:
        raise ValueError("public tail-probe source exceeds its size limit")
    if hashlib.sha256(source).hexdigest() != SOURCE_SHA256:
        raise ValueError("public tail-probe source SHA-256 does not match")
    return source


def prepare_clip(source):
    """Match the app's mono/SoXR-HQ input path before applying the issue crop."""
    import numpy as np
    import soundfile as sf
    import soxr

    audio, source_rate = sf.read(io.BytesIO(source), dtype="float32")
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    elif audio.ndim != 1:
        raise ValueError("public tail-probe source has invalid channel shape")
    audio = np.asarray(audio, dtype=np.float32)
    if source_rate != SAMPLE_RATE:
        audio = soxr.resample(audio, source_rate, SAMPLE_RATE, quality="HQ")
    if len(audio) < CROP_END_SAMPLE or not np.isfinite(audio).all():
        raise ValueError("public tail-probe source is too short or non-finite")
    clip = np.ascontiguousarray(
        audio[CROP_START_SAMPLE:CROP_END_SAMPLE], dtype=np.float32)
    if len(clip) != 35_200:
        raise AssertionError("public tail-probe crop is not 2.2 seconds")
    return clip


def fixture_manifest():
    return {
        "model": "parakeet-tdt-0.6b-v3",
        "language": "en",
        "runs": 5,
        "samples": [{
            "id": "public-parakeet-short-tail-001",
            "audio": CLIP_NAME,
            "task_group": "public-short-speech",
            "language_group": "en",
            "reference": "",
            "reference_reviewed": False,
        }],
    }


def write_fixture(benchmarks_dir, *, fetch=download_source,
                  prepare=prepare_clip, write_audio=None):
    """Stage both files, then publish one new ignored local directory."""
    benchmarks_dir = Path(benchmarks_dir)
    destination = benchmarks_dir / FIXTURE_DIR_NAME
    if destination.exists():
        raise FileExistsError("public tail-probe fixture already exists")
    source = fetch()
    clip = prepare(source)
    if write_audio is None:
        import soundfile as sf
        write_audio = sf.write
    benchmarks_dir.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(
        prefix=".public-parakeet-tail-", dir=benchmarks_dir))
    try:
        write_audio(temporary / CLIP_NAME, clip, SAMPLE_RATE, subtype="FLOAT")
        (temporary / "manifest.json").write_text(
            json.dumps(fixture_manifest(), indent=2) + "\n", encoding="utf-8")
        # No generated audio or report belongs in version control.  The entire
        # directory becomes visible only after conversion and manifest writing.
        temporary.rename(destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return destination / "manifest.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmarks-dir", type=Path,
        default=Path(__file__).resolve().parent / "benchmarks",
        help="local benchmark directory (default: windows/benchmarks)")
    args = parser.parse_args()
    manifest = write_fixture(args.benchmarks_dir)
    print("Prepared %s" % manifest)
    print("Listen to the generated clip, enter its exact reference, and set "
          "reference_reviewed to true before running the tail probe.")


if __name__ == "__main__":
    main()
