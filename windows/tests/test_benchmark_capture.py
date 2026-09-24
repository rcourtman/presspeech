import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from benchmark_capture import write_capture_wav


class BenchmarkCaptureTests(unittest.TestCase):
    def test_wav_replays_exact_asr_float_samples(self):
        # Include quiet, non-PCM16, and >1 samples to detect quantization and
        # clipping in a future capture writer.
        captured = np.array(
            [0.0, 0.00001, -0.12345679, 0.9876543, 1.125],
            dtype=np.float32)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.wav"
            write_capture_wav(path, captured)
            self.assertEqual(sf.info(path).subtype, "FLOAT")
            replay, sample_rate = sf.read(path, dtype="float32")

        self.assertEqual(sample_rate, 16000)
        np.testing.assert_array_equal(replay, captured)

    def test_rejects_input_that_could_change_on_float_wav_export(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "not-written.wav"
            for audio in (
                    np.array([0.1], dtype=np.float64),
                    np.array([[0.1]], dtype=np.float32),
                    np.array([np.nan], dtype=np.float32)):
                with self.subTest(shape=audio.shape, dtype=str(audio.dtype)):
                    with self.assertRaisesRegex(ValueError, "mono float32"):
                        write_capture_wav(path, audio)
                    self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
