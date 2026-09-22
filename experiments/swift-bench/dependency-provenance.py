#!/usr/bin/env python3
"""Identify exact declared/locked benchmark and application FluidAudio revisions.

A matching dependency is necessary for production comparisons, but is not proof
that benchmark preprocessing, configuration, models or hardware match the app.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import subprocess
import tempfile
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent / "Package.swift"
PRODUCTION_PACKAGE = PACKAGE.parents[2] / "swift" / "Package.swift"
URL = "https://github.com/FluidInference/FluidAudio.git"


def metadata(path: Path):
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("dependency metadata contains duplicate JSON keys")
            result[key] = value
        return result
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)


def revision(package: Path) -> str:
    manifest = package.read_text(encoding="utf-8")
    pins = re.findall(
        r'\.package\(\s*url:\s*"' + re.escape(URL)
        + r'"\s*,\s*revision:\s*"([0-9a-f]{40})"\s*\)', manifest)
    if len(pins) != 1:
        raise ValueError("manifest must pin exactly one canonical FluidAudio revision")
    lock = metadata(package.with_name("Package.resolved"))
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


def verify_built(package: Path, expected_revision: str) -> None:
    """Check the selected SwiftPM source checkout, independently of Git's index.

    This validates source at inspection time, not the compiler or binary's full
    supply chain. In particular, assume-unchanged bits and ignored source files
    must not let modified SDK inputs masquerade as the declared revision.
    """
    if not re.fullmatch(r"[0-9a-f]{40}", expected_revision):
        raise ValueError("built dependency revision must be an immutable commit")
    build = package.resolve().parent / ".build"
    workspace = metadata(build / "workspace-state.json")
    if not isinstance(workspace, dict) or not isinstance(workspace.get("object"), dict):
        raise ValueError("built workspace has no dependency inventory")
    dependencies = workspace["object"].get("dependencies")
    if not isinstance(dependencies, list):
        raise ValueError("built workspace has no dependency inventory")
    matches = [item for item in dependencies if isinstance(item, dict)
               and isinstance(item.get("packageRef"), dict)
               and (item["packageRef"].get("identity") == "fluidaudio"
                    or item["packageRef"].get("location") == URL)]
    if len(matches) != 1:
        raise ValueError("built workspace must select one FluidAudio dependency")
    selected = matches[0]
    reference, state = selected["packageRef"], selected.get("state", {})
    if (not isinstance(state, dict)
            or not isinstance(state.get("checkoutState"), dict)
            or reference.get("identity") != "fluidaudio"
            or reference.get("location") != URL
            or reference.get("kind") != "remoteSourceControl"
            or state.get("name") != "sourceControlCheckout"
            or state.get("checkoutState", {}).get("revision") != expected_revision
            or selected.get("basedOn") is not None):
        raise ValueError("built workspace uses an edited or different FluidAudio dependency")
    subpath = selected.get("subpath")
    if (not isinstance(subpath, str) or not subpath
            or Path(subpath).name != subpath or subpath in {".", ".."}):
        raise ValueError("built dependency checkout path is invalid")
    checkout = build / "checkouts" / subpath
    if checkout.is_symlink() or not checkout.is_dir():
        raise ValueError("built dependency checkout must be an independent directory")
    if not (checkout / ".git").is_dir() or (checkout / ".git").is_symlink():
        raise ValueError("built dependency checkout must have its own Git directory")
    if (checkout / ".git" / "commondir").exists():
        raise ValueError("built dependency checkout must not redirect Git metadata")
    # Do not inherit GIT_DIR/WORK_TREE, injected config, object namespaces,
    # alternates or index paths from the benchmark process. The explicit Git
    # directory also prevents repository-local core.worktree redirection.
    environment = {name: value for name, value in os.environ.items()
                   if not name.startswith("GIT_")}
    environment.update({"GIT_NO_REPLACE_OBJECTS": "1", "GIT_OPTIONAL_LOCKS": "0",
                        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
    # Only object reads: no diff/status, filters, hooks or index trust.
    def git(*arguments: str) -> bytes:
        return subprocess.check_output(["git", "--no-replace-objects",
                                       "--git-dir", str(checkout / ".git"),
                                       "--work-tree", str(checkout), *arguments],
                                       env=environment, stderr=subprocess.PIPE)
    if git("rev-parse", "HEAD").decode().strip() != expected_revision:
        raise ValueError("built dependency HEAD differs from the declared revision")
    tracked = set()
    for entry in git("ls-tree", "-r", "-z", expected_revision).split(b"\0"):
        if not entry:
            continue
        attributes, encoded = entry.split(b"\t", 1)
        mode, kind, digest = attributes.decode().split()
        relative = Path(os.fsdecode(encoded))
        if relative.is_absolute() or ".." in relative.parts or ".git" in relative.parts:
            raise ValueError("built dependency tree contains an invalid source path")
        tracked.add(relative)
        path = checkout / relative
        info = path.lstat()
        if kind != "blob" or mode not in {"100644", "100755", "120000"}:
            raise ValueError("built dependency contains unsupported nested source")
        if mode == "120000":
            if not stat.S_ISLNK(info.st_mode):
                raise ValueError("built dependency symlink differs from its commit")
            resolved = path.resolve(strict=True).relative_to(checkout.resolve())
            if ".git" in resolved.parts:
                raise ValueError("built dependency link references mutable Git metadata")
            data = os.fsencode(os.readlink(path))
        else:
            if (not stat.S_ISREG(info.st_mode)
                    or bool(info.st_mode & 0o111) != (mode == "100755")):
                raise ValueError("built dependency file type or mode differs from its commit")
            data = path.read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if actual != digest:
            raise ValueError("built dependency source bytes differ from the declared revision")
    physical = set()
    def unreadable(error):
        raise error
    for directory, folders, files in os.walk(checkout, followlinks=False, onerror=unreadable):
        parent = Path(directory)
        for name in list(folders):
            path = parent / name
            if path == checkout / ".git":
                folders.remove(name)
            elif path.is_symlink():
                physical.add(path.relative_to(checkout))
                folders.remove(name)
        physical.update((parent / name).relative_to(checkout) for name in files)
    if physical != tracked:
        raise ValueError("built dependency contains untracked or missing files")


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
    subprocess.run([sys.executable, str(Path(__file__).with_name("test-dependency-provenance.py"))],
                   check=True)
    print("Dependency provenance self-test passed.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--self-test", action="store_true")
    mode.add_argument("--package", type=Path, help="validate and print one package revision")
    parser.add_argument("--benchmark-package", type=Path, default=PACKAGE)
    parser.add_argument("--production-package", type=Path, default=PRODUCTION_PACKAGE)
    parser.add_argument("--verify-built", action="store_true",
                        help="also validate the actual SwiftPM dependency source after building")
    mode.add_argument("--require-production", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    try:
        if args.package is not None:
            package_revision = revision(args.package)
            if args.verify_built:
                verify_built(args.package, package_revision)
            print(package_revision)
        else:
            result = provenance(args.benchmark_package, args.production_package)
            if args.require_production and result[2] != "production-dependency":
                raise ValueError("candidate dependency cannot supply a production baseline; use --no-threshold for exploration")
            if args.verify_built:
                verify_built(args.benchmark_package, result[0])
            print("\t".join(result))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Dependency provenance refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
