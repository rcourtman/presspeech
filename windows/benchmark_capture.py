"""Sample-exact WAV export for explicitly armed local speech benchmarks."""

import numpy as np
import soundfile as sf


def write_capture_wav(path, audio):
    """Preserve the float32 ASR input, including quiet and out-of-range samples."""
    if (not isinstance(audio, np.ndarray) or audio.ndim != 1
            or audio.dtype != np.float32 or not np.isfinite(audio).all()):
        raise ValueError("benchmark capture requires finite mono float32 ASR audio")
    sf.write(path, audio, 16000, subtype="FLOAT")
