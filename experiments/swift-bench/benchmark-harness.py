#!/usr/bin/env python3
"""Fingerprint the local code that prepares and measures ASR regression clips.

The FluidAudio revision is recorded separately. This receipt makes a paired
SDK comparison refuse a changed benchmark implementation, even when its input
and dependency receipts still match. It is not a binary or machine attestation.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import stat
import sys
import tempfile


FILES = (
    "Package.swift",
    "benchmark-harness.py",
    "benchmark-inputs.py",
    "dependency-provenance.py",
    "experiment-environment.py",
    "audio-input-evidence.py",
    "run-real-dictation-regression.sh",
    "Sources/presspeech-bench/main.swift",
)
FLUID_PIN = re.compile(
    rb'(\.package\(\s*url:\s*"https://github\.com/FluidInference/FluidAudio\.git"'
    rb'\s*,\s*revision:\s*")[0-9a-f]{40}("\s*\))'
)


def fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for relative in FILES:
        path = root / relative
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode):
            raise ValueError("benchmark harness source is not a regular file")
        data = path.read_bytes()
        if relative == "Package.swift":
            # The SDK commit must differ, but no other benchmark build setting
            # should drift between the paired reports.
            data, replacements = FLUID_PIN.subn(
                lambda match: match[1] + b"<reviewed-sdk-revision>" + match[2], data
            )
            if replacements != 1:
                raise ValueError("benchmark package has no unique FluidAudio pin")
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()


def self_test() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for relative in FILES:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative == "Package.swift":
                path.write_bytes(
                    b'.package(url: "https://github.com/FluidInference/FluidAudio.git", '
                    b'revision: "' + b"a" * 40 + b'")\n'
                )
            else:
                path.write_bytes(relative.encode("utf-8"))
        original = fingerprint(root)
        assert len(original) == 64 and original == fingerprint(root)
        package = root / "Package.swift"
        package.write_bytes(package.read_bytes().replace(b"a" * 40, b"b" * 40))
        assert fingerprint(root) == original
        package.write_bytes(package.read_bytes() + b"// build setting changed\n")
        assert fingerprint(root) != original
        package.write_bytes(package.read_bytes().replace(b"// build setting changed\n", b""))
        changed = root / FILES[-1]
        changed.write_bytes(changed.read_bytes() + b"\n")
        assert fingerprint(root) != original
        changed.unlink()
        try:
            fingerprint(root)
        except OSError:
            pass
        else:
            raise AssertionError("missing harness source was accepted")
        changed.symlink_to(root / FILES[0])
        try:
            fingerprint(root)
        except ValueError:
            pass
        else:
            raise AssertionError("symlinked harness source was accepted")
    print("benchmark harness fingerprint self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    try:
        print(fingerprint(Path(__file__).resolve().parent))
    except (OSError, ValueError):
        print("benchmark harness fingerprint unavailable", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
