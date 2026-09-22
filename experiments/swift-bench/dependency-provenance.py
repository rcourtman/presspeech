#!/usr/bin/env python3
"""Identify exact declared/locked benchmark and application FluidAudio revisions.

A matching dependency is necessary for production comparisons, but is not proof
that benchmark preprocessing, configuration, models or hardware match the app.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import subprocess
import tempfile
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent / "Package.swift"
PRODUCTION_PACKAGE = PACKAGE.parents[2] / "swift" / "Package.swift"
URL = "https://github.com/FluidInference/FluidAudio.git"


def revision(package: Path) -> str:
    manifest = package.read_text(encoding="utf-8")
    pins = re.findall(
        r'\.package\(\s*url:\s*"' + re.escape(URL)
        + r'"\s*,\s*revision:\s*"([0-9a-f]{40})"\s*\)', manifest)
    if len(pins) != 1:
        raise ValueError("manifest must pin exactly one canonical FluidAudio revision")
    lock = json.loads(package.with_name("Package.resolved").read_text(encoding="utf-8"))
    if not isinstance(lock, dict) or not isinstance(lock.get("pins"), list):
        raise ValueError("dependency lock has no pin list")
    fluid = [pin for pin in lock["pins"] if isinstance(pin, dict)
             and (pin.get("identity") == "fluidaudio" or pin.get("location") == URL)]
    if len(fluid) != 1 or fluid[0].get("identity") != "fluidaudio" or fluid[0].get("location") != URL:
        raise ValueError("dependency lock must identify one canonical FluidAudio repository")
    state = fluid[0].get("state")
    if not isinstance(state, dict) or state.get("revision") != pins[0]:
        raise ValueError("FluidAudio manifest and resolved revisions differ")
    return pins[0]


def provenance(benchmark: Path, production: Path) -> tuple[str, str, str]:
    benchmark_revision, production_revision = revision(benchmark), revision(production)
    mode = ("production-dependency" if benchmark_revision == production_revision
            else "candidate-dependency")
    return benchmark_revision, production_revision, mode


def self_test() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)

        def write(name: str, sha: str) -> Path:
            directory = root / name
            directory.mkdir(exist_ok=True)
            package = directory / "Package.swift"
            package.write_text(f'.package(url: "{URL}", revision: "{sha}")\n')
            package.with_name("Package.resolved").write_text(json.dumps({"pins": [{
                "identity": "fluidaudio", "location": URL, "state": {"revision": sha}}]}))
            return package

        app, bench = write("app", "a" * 40), write("bench", "a" * 40)
        assert provenance(bench, app) == ("a" * 40, "a" * 40, "production-dependency")
        bench = write("bench", "b" * 40)
        assert provenance(bench, app) == ("b" * 40, "a" * 40, "candidate-dependency")
        rejected = subprocess.run([sys.executable, str(Path(__file__).resolve()),
            "--benchmark-package", str(bench), "--production-package", str(app),
            "--require-production"], capture_output=True, text=True)
        assert rejected.returncode == 1 and "candidate dependency" in rejected.stderr
        assert not rejected.stdout
        for invalid in (
            {}, [], {"pins": []}, {"pins": [True]},
            {"pins": [{"identity": "fluidaudio", "location": URL, "state": {"revision": "a" * 40}}]},
            {"pins": [{"identity": "fluidaudio", "location": "https://example.invalid/FluidAudio.git", "state": {"revision": "b" * 40}}]},
            {"pins": [{"identity": "fluidaudio", "location": URL, "state": {"revision": "b" * 40}}] * 2},
        ):
            bench.with_name("Package.resolved").write_text(json.dumps(invalid))
            try:
                provenance(bench, app)
            except ValueError:
                pass
            else:
                raise AssertionError("invalid FluidAudio lock was accepted")
        bench = write("bench", "b" * 40)
        bench.write_text(bench.read_text() * 2)
        try:
            revision(bench)
        except ValueError:
            pass
        else:
            raise AssertionError("ambiguous FluidAudio manifest was accepted")
    print("Dependency provenance self-test passed.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--self-test", action="store_true")
    mode.add_argument("--package", type=Path, help="validate and print one package revision")
    parser.add_argument("--benchmark-package", type=Path, default=PACKAGE)
    parser.add_argument("--production-package", type=Path, default=PRODUCTION_PACKAGE)
    mode.add_argument("--require-production", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    try:
        if args.package is not None:
            print(revision(args.package))
        else:
            result = provenance(args.benchmark_package, args.production_package)
            if args.require_production and result[2] != "production-dependency":
                raise ValueError("candidate dependency cannot supply a production baseline; use --no-threshold for exploration")
            print("\t".join(result))
    except (OSError, ValueError) as exc:
        print(f"Dependency provenance refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
