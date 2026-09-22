#!/usr/bin/env python3
"""Verify complete normalized WAV input against the benchmark's decoded count."""
import argparse
from pathlib import Path
import re
import struct
import sys


class EvidenceError(ValueError):
    pass


def normalized_frames(path):
    # Read chunk metadata only: a long corpus must not need another audio-sized
    # allocation just to check that the benchmark consumed its final frames.
    with Path(path).open('rb') as stream:
        size = stream.seek(0, 2)
        stream.seek(0)
        header = stream.read(12)
        if len(header) != 12 or header[:4] != b'RIFF' or header[8:] != b'WAVE':
            raise EvidenceError('expected a normalized RIFF WAVE input')
        if struct.unpack_from('<I', header, 4)[0] + 8 != size:
            raise EvidenceError('normalized WAV size does not match its header')
        offset, fmt, payload = 12, None, None
        while offset < size:
            stream.seek(offset)
            chunk = stream.read(8)
            if len(chunk) != 8:
                raise EvidenceError('truncated WAV chunk header')
            kind, length = struct.unpack('<4sI', chunk)
            end = offset + 8 + length
            padded_end = end + length % 2
            if padded_end > size:
                raise EvidenceError('truncated WAV chunk payload or padding')
            if kind == b'fmt ':
                if fmt is not None or length < 16:
                    raise EvidenceError('missing or duplicate WAV format')
                fmt = struct.unpack('<HHIIHH', stream.read(16))
            elif kind == b'data':
                if payload is not None:
                    raise EvidenceError('duplicate WAV audio payload')
                payload = length
            offset = padded_end
        if fmt is None or payload is None:
            raise EvidenceError('missing WAV format or audio payload')
        encoding, channels, rate, byte_rate, alignment, bits = fmt
        # afconvert -d LEF32@16000 emits IEEE Float32; PCM16 also covers native
        # test fixtures. Multichannel frame count survives the loader's downmix.
        if ((encoding, bits) not in ((1, 16), (3, 32)) or rate != 16000
                or channels < 1 or alignment != channels * (bits // 8)
                or byte_rate != rate * alignment):
            raise EvidenceError('expected normalized 16 kHz PCM16 or Float32 WAV')
        if payload == 0 or payload % alignment:
            raise EvidenceError('empty or incomplete normalized audio frames')
        return payload // alignment


def verify_log(path, expected):
    # Exactly one input is loaded per invocation, including --backend all.
    # Reject missing, malformed and duplicate records instead of finding a
    # plausible count in a transcript or an upstream diagnostic.
    records = [line for line in Path(path).read_text().splitlines()
               if line.startswith('audio:')]
    pattern = r'audio: ([0-9]{1,12}) samples \(~[0-9]+\.[0-9]+ s @ 16 kHz mono\)'
    match = re.fullmatch(pattern, records[0]) if len(records) == 1 else None
    if match is None:
        raise EvidenceError('benchmark lacks exactly one valid decoded audio count')
    observed = int(match[1])
    if observed != expected:
        raise EvidenceError(f'decoded audio length mismatch: expected {expected} frames, got {observed} samples')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audio', type=Path, required=True)
    parser.add_argument('--log', type=Path)
    args = parser.parse_args()
    try:
        expected = normalized_frames(args.audio)
        if args.log:
            verify_log(args.log, expected)
        print(f'audio-input-check: source-frames={expected}' +
              (f' decoded-samples={expected}' if args.log else ''))
        return 0
    except (EvidenceError, OSError, UnicodeError) as error:
        # Files and logs may be private. OS errors carry paths; do not echo them.
        reason = str(error) if isinstance(error, EvidenceError) else 'could not read audio evidence'
        print(f'audio input evidence failed: {reason}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
