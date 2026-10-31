#!/usr/bin/env python3
"""Bind a hosted Apple silicon build to the source approved for local signing."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import struct
import subprocess


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


INPUTS = ('swift/Sources/Presspeech/main.swift', 'swift/Package.swift',
          'swift/Package.resolved', 'swift/Info.plist')
WORKFLOW = '.github/workflows/macos-release-build.yml'


def check_binary(path):
    if path.is_symlink() or not path.is_file() or not 1024 < path.stat().st_size < 200 * 1024 * 1024:
        raise ValueError('Build artifact is not a regular bounded executable')
    with path.open('rb') as source:
        magic, cpu = struct.unpack('<II', source.read(8))
    if magic != 0xFEEDFACF or cpu != 0x0100000C:
        raise ValueError('Build artifact is not an Apple silicon Mach-O executable')


def target(root, version, build):
    if not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', version):
        raise ValueError('Release version is not canonical')
    info = plistlib.loads((root / 'swift/Info.plist').read_bytes())
    if tuple(map(int, version.split('.'))) <= tuple(map(int, info['CFBundleShortVersionString'].split('.'))):
        raise ValueError('Release must advance the source version')
    if str(int(info['CFBundleVersion']) + 1) != build:
        raise ValueError('Release build does not advance the source build exactly once')


def verify(root, directory, run, sha, version, build):
    target(root, version, build)
    if (run.get('conclusion') != 'success' or run.get('event') != 'workflow_dispatch'
            or run.get('head_sha') != sha or run.get('head_branch') != 'main'
            or run.get('path') != WORKFLOW
            or run.get('repository', {}).get('full_name') != 'rcourtman/presspeech'):
        raise ValueError('Hosted build does not match the approved successful main workflow')
    if {p.name for p in directory.iterdir()} != {'Presspeech', 'build-manifest.json'}:
        raise ValueError('Unexpected build artifact inventory')
    manifest_path = directory / 'build-manifest.json'
    if manifest_path.is_symlink():
        raise ValueError('Build manifest must be a regular file')
    manifest = json.loads(manifest_path.read_text())
    if (manifest.get('schema') != 1 or manifest.get('source_sha') != sha
            or manifest.get('run_id') != run.get('id') or manifest.get('version') != version
            or manifest.get('build') != build or manifest.get('architecture') != 'arm64'):
        raise ValueError('Build manifest does not match the approved release')
    inputs = {name: digest(root / name) for name in INPUTS}
    if manifest.get('inputs_sha256') != inputs:
        raise ValueError('Hosted build inputs differ from the signing checkout')
    binary = directory / 'Presspeech'
    check_binary(binary)
    if digest(binary) != manifest.get('binary_sha256'):
        raise ValueError('Hosted executable digest mismatch')
    # Artifact ZIP transport does not preserve executable permission bits.
    binary.chmod(0o755)
    return binary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['create', 'verify'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--build', required=True)
    parser.add_argument('--binary', type=Path)
    parser.add_argument('--run-json', type=Path)
    parser.add_argument('--sha')
    args = parser.parse_args()
    if args.mode == 'verify':
        print(verify(args.root, args.directory, json.loads(args.run_json.read_text()),
                     args.sha, args.version, args.build))
        return
    if (platform.system() != 'Darwin' or platform.machine() != 'arm64'
            or os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted'):
        parser.error('Build export requires a hosted Apple silicon runner')
    target(args.root, args.version, args.build)
    sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=args.root, text=True).strip()
    if sha != os.environ['GITHUB_SHA']:
        parser.error('Checkout does not match the workflow source')
    check_binary(args.binary)
    args.directory.mkdir()
    binary = args.directory / 'Presspeech'
    shutil.copy2(args.binary, binary)
    binary.chmod(0o755)
    manifest = dict(schema=1, source_sha=sha, run_id=int(os.environ['GITHUB_RUN_ID']),
                    version=args.version, build=args.build, architecture='arm64',
                    binary_sha256=digest(binary),
                    inputs_sha256={name: digest(args.root / name) for name in INPUTS})
    (args.directory / 'build-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    main()
