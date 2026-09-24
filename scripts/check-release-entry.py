#!/usr/bin/env python3
"""Require a first-launch privacy handoff in each standalone release entry.

This is a deliberately small pre-publication guard, not a semantic privacy
review. It catches a missing or buried model-download decision before a user
can reach an asset directly from GitHub Releases without visiting the site.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


GUIDES = {
    "macos": (
        "https://rcourtman.github.io/presspeech/install.html#model-download-privacy",
        "https://rcourtman.github.io/presspeech/privacy.html#network-calls",
    ),
    "windows": (
        "https://rcourtman.github.io/presspeech/windows.html#model-download-privacy",
    ),
}
VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")
LEAD_CHARACTERS = 1800
ROOT = Path(__file__).resolve().parents[1]
KNOWN_RISKS = {
    ("macos", "0.3.8"): ("hugging face token", "wait", "proxy"),
    ("windows", "0.1.12"): ("hugging face", "telemetry", "token", "routing", "wait", "proxy"),
}


def entry_errors(platform: str, version: str, body: str) -> list[str]:
    if platform not in GUIDES:
        raise ValueError(f"unknown platform: {platform}")
    if not VERSION.fullmatch(version):
        raise ValueError(f"invalid release version: {version}")
    errors: list[str] = []
    lead = body[:LEAD_CHARACTERS]
    prose = re.sub(r"https?://\S+", "", lead)
    # A previous version's warning must not satisfy this release's gate.
    if not re.search(rf"(?<!\d){re.escape(version)}(?!\d)", lead):
        errors.append("lead does not identify the exact release version")
    if not re.search(r"\bbefore\s+(?:opening|launching|running)\b", prose, re.I):
        errors.append("lead lacks a before-opening decision")
    if not re.search(r"\bmodel\b", prose, re.I) or not re.search(r"\bdownload\b", prose, re.I):
        errors.append("lead lacks the model-download boundary")
    if not re.search(r"\b(?:launch|setup|defer)\b", prose, re.I):
        errors.append("lead does not explain when or whether the download starts")
    if not any(guide in lead for guide in GUIDES[platform]):
        errors.append("lead lacks the platform's version-specific privacy-guide link")
    normalized = re.sub(r"\s+", " ", prose).casefold()
    missing_risks = [term for term in KNOWN_RISKS.get((platform, version), ()) if term not in normalized]
    if missing_risks:
        errors.append("lead omits known published-build caveats: " + ", ".join(missing_risks))
    return errors


def run_self_test() -> None:
    mac = (
        "# Presspeech 9.8.7\n\n> **Before opening 9.8.7:** A missing model "
        "download begins on launch. Read the "
        "https://rcourtman.github.io/presspeech/install.html#model-download-privacy "
        "guide before deciding.\n"
    )
    windows = (
        "Before launching Windows 9.8.7, decide whether to start the missing "
        "model download in Setup. Read "
        "https://rcourtman.github.io/presspeech/windows.html#model-download-privacy.\n"
    )
    assert not entry_errors("macos", "9.8.7", mac)
    assert not entry_errors("windows", "9.8.7", windows)
    assert entry_errors("macos", "9.8.8", mac)
    assert entry_errors("macos", "9.8.7", mac.replace("Before opening", "Release notes for"))
    assert entry_errors("windows", "9.8.7", windows.replace("model download", "local setup"))
    assert entry_errors("windows", "9.8.7", windows.replace("windows.html#model-download-privacy", "install.html"))
    assert entry_errors("windows", "9.8.7", "Release details.\n" * 100 + windows)
    assert entry_errors("windows", "0.1.12", windows.replace("9.8.7", "0.1.12"))
    for invalid in ("09.8.7", "9.8", "9.8.7-rc1"):
        try:
            entry_errors("macos", invalid, mac)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted malformed version {invalid}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=tuple(GUIDES))
    parser.add_argument("--version")
    parser.add_argument("--file", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--current", action="store_true", help="check both versions advertised in site-metadata.json")
    args = parser.parse_args()
    if args.self_test:
        run_self_test()
        print("release-entry self-test passed")
        return 0
    if args.current:
        if any((args.platform, args.version, args.file)):
            parser.error("--current cannot be combined with explicit entry arguments")
        try:
            metadata = json.loads((ROOT / "docs/site-metadata.json").read_text(encoding="utf-8"))
            if not isinstance(metadata, dict):
                raise ValueError("site metadata must be an object")
            if not all(isinstance(metadata.get(key), str) and VERSION.fullmatch(metadata[key])
                       for key in ("version", "windows_version")):
                raise ValueError("site metadata has a missing or invalid platform version")
            entries = [
                ("macos", metadata["version"], ROOT / "swift/release-notes" / f"v{metadata['version']}.md"),
                ("windows", metadata["windows_version"], ROOT / "windows/release-notes" / f"{metadata['windows_version']}.md"),
            ]
        except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
            print(f"check-release-entry: cannot load current site versions: {exc}", file=sys.stderr)
            return 1
    else:
        if not all((args.platform, args.version, args.file)):
            parser.error("--platform, --version, and --file are required")
        entries = [(args.platform, args.version, args.file)]
    failed = False
    for platform, version, path in entries:
        try:
            body = path.read_text(encoding="utf-8")
            errors = entry_errors(platform, version, body)
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"check-release-entry: {exc}", file=sys.stderr)
            failed = True
            continue
        for error in errors:
            print(f"check-release-entry: {path}: {error}", file=sys.stderr)
        if errors:
            failed = True
        else:
            print(f"{platform} {version} release entry has a lead model-download handoff; review its wording before publication")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
