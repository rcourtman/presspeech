#!/usr/bin/env python3
"""Input completeness regressions using real WAV bytes and benchmark records."""
import importlib.util
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('evidence', ROOT / 'audio-input-evidence.py')
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


def wave_bytes(frames=173760, encoding=3, bits=32, channels=1, rate=16000, extra=b''):
    align = channels * (bits // 8)
    fmt = struct.pack('<HHIIHH', encoding, channels, rate, rate * align, align, bits)
    data = bytes(frames * align)
    body = b'WAVE' + b'fmt ' + struct.pack('<I', len(fmt)) + fmt + extra + b'data' + struct.pack('<I', len(data)) + data
    return b'RIFF' + struct.pack('<I', len(body)) + body


class AudioEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.audio = Path(self.temp.name) / 'PRIVATE_SOURCE.wav'
        self.log = Path(self.temp.name) / 'PRIVATE_LOG.txt'

    def test_real_normalized_encodings_channels_and_short_final_blocks(self):
        for encoding, bits in ((1, 16), (3, 32)):
            for channels in (1, 2):
                for frames in (1, 1023, 1024, 1025, 16037, 173760):
                    with self.subTest(encoding=encoding, channels=channels, frames=frames):
                        self.audio.write_bytes(wave_bytes(frames, encoding, bits, channels))
                        self.assertEqual(evidence.normalized_frames(self.audio), frames)

    def test_metadata_chunk_padding_does_not_count_as_audio(self):
        self.audio.write_bytes(wave_bytes(extra=b'JUNK\x03\x00\x00\x00abc\x00'))
        self.assertEqual(evidence.normalized_frames(self.audio), 173760)

    def test_historical_short_read_is_rejected_but_full_read_passes(self):
        self.audio.write_bytes(wave_bytes())
        expected = evidence.normalized_frames(self.audio)
        self.log.write_text('audio: 173056 samples (~10.82 s @ 16 kHz mono)\nlatency: p50=10.0 ms\n')
        with self.assertRaisesRegex(evidence.EvidenceError, 'expected 173760 frames, got 173056'):
            evidence.verify_log(self.log, expected)
        self.log.write_text('audio: 173760 samples (~10.86 s @ 16 kHz mono)\n')
        evidence.verify_log(self.log, expected)

    def test_missing_duplicate_malformed_or_overlong_decoded_counts_fail(self):
        valid = 'audio: 173760 samples (~10.86 s @ 16 kHz mono)\n'
        for log in ('', valid * 2, valid + 'audio: malformed\n', valid.replace('173760', '173761'),
                    'transcript: ' + valid, valid.replace('173760', '-1')):
            with self.subTest(log=log):
                self.log.write_text(log)
                with self.assertRaises(evidence.EvidenceError):
                    evidence.verify_log(self.log, 173760)

    def test_truncated_or_ambiguous_wav_is_not_qualified(self):
        valid = wave_bytes(frames=7)
        fmt = valid[12:36]
        variants = [valid[:-1], valid + b'x', valid[:10], wave_bytes(frames=0),
                    wave_bytes(rate=22050), wave_bytes(encoding=1, bits=32),
                    wave_bytes(extra=fmt), wave_bytes(extra=b'data\x00\x00\x00\x00'),
                    wave_bytes(extra=b'JUNK\xff\xff\xff\xff')]
        # A missing pad byte can otherwise advance a parser beyond EOF.
        body = valid[8:] + b'JUNK\x01\x00\x00\x00x'
        variants.append(b'RIFF' + struct.pack('<I', len(body)) + body)
        for data in variants:
            with self.subTest(size=len(data)):
                self.audio.write_bytes(data)
                with self.assertRaises(evidence.EvidenceError):
                    evidence.normalized_frames(self.audio)

    def test_cli_does_not_disclose_paths_or_log_content_on_failure(self):
        for content in (None, b'PRIVATE_AUDIO'):
            if content is not None:
                self.audio.write_bytes(content)
            result = subprocess.run([sys.executable, str(ROOT / 'audio-input-evidence.py'),
                '--audio', str(self.audio), '--log', str(self.log)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn('PRIVATE_', result.stdout + result.stderr)
            self.assertNotIn(self.temp.name, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
