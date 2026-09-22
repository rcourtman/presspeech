#!/usr/bin/env python3
"""Classify native experiment receipts without retaining environment names or values."""
import argparse
from pathlib import Path
import re

STATES = ('pending', 'default', 'configured', 'unreported')
RECEIPT = re.compile(r'experiment-environment: schema=1 inherited-controls=(0|[1-9][0-9]{0,5}) ci-present=([01])')


def classify(text):
    records = [line for line in text.splitlines() if line.startswith('experiment-environment:')]
    match = RECEIPT.fullmatch(records[0]) if len(records) == 1 else None
    if match is None:
        return 'unreported'
    # CI is recorded, but the pinned SDK's benchmark ASR paths do not call its
    # CI-sensitive optimizedConfiguration, diarization, or TTS code paths.
    return 'default' if int(match[1]) == 0 else 'configured'


def merge(previous, current):
    if previous == 'unreported' or current == 'unreported':
        return 'unreported'
    if previous == 'configured' or current == 'configured':
        return 'configured'
    return 'default'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--previous', choices=STATES, default='pending')
    args = parser.parse_args()
    try:
        current = classify(args.log.read_text())
    except (OSError, UnicodeError):
        current = 'unreported'
    print(merge(args.previous, current))


if __name__ == '__main__':
    main()
