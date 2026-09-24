#!/usr/bin/env python3
"""Compare public onboarding files with the intended checked-in docs.

Run this read-only check from the exact main commit intended for Pages, after
its deployment. Candidate source can legitimately be ahead of the public site.
Unlike source-only validators, this catches an old Pages artifact still being
served after safety or support guidance has changed.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DOCS = Path(__file__).resolve().parents[1] / "docs"
SITE = "https://rcourtman.github.io/presspeech/"
CRITICAL_FILES = (
    "index.html",
    "getting-started.html",
    "install.html",
    "windows.html",
    "privacy.html",
    "troubleshooting.html",
    "app-compatibility.html",
    "faq.html",
    # The shared assets determine whether the warnings remain readable and
    # reachable on narrow screens. Fresh HTML alone does not prove that Pages
    # serves the same navigation behavior and layout.
    "styles.css",
    "site-navigation.js",
    # These are also direct first-launch entry points. A fresh Pages HTML
    # deployment does not prove its versioned data, agent instructions, or
    # worksheet script were deployed with it.
    "site-metadata.json",
    "privacy/network-calls.json",
    "install/agents.md",
    "llms.txt",
    "llms-full.txt",
    "compatibility-worksheet.js",
)
MAX_BYTES = 1024 * 1024


def public_url(path: str) -> str:
    return SITE + ("" if path == "index.html" else path)


def fetch_public(path: str) -> bytes:
    url = public_url(path)
    request = Request(url, headers={"User-Agent": "Presspeech-Pages-check/1.0",
                                    "Cache-Control": "no-cache"})
    with urlopen(request, timeout=15) as response:
        if response.status != 200 or response.geturl() != url:
            raise ValueError(f"unexpected response or redirect for {url}")
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError(f"public file exceeds {MAX_BYTES} bytes: {url}")
    return body


def short_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def compare_files(docs: Path, fetch=fetch_public) -> list[str]:
    errors = []
    for path in CRITICAL_FILES:
        try:
            local = (docs / path).read_bytes()
            remote = fetch(path)
            if local != remote:
                errors.append(
                    f"{path}: live Pages differs from checked-in docs "
                    f"(local sha256 {short_hash(local)}, live sha256 {short_hash(remote)})"
                )
        except (OSError, ValueError, HTTPError, URLError) as exc:
            errors.append(f"{path}: could not verify live Pages: {exc}")
    return errors


def self_test() -> None:
    class FakeResponse(BytesIO):
        status = 200

        def __init__(self, url: str, body: bytes):
            super().__init__(body)
            self.url = url

        def geturl(self) -> str:
            return self.url

    assert public_url("index.html") == SITE
    assert public_url("getting-started.html") == SITE + "getting-started.html"
    assert public_url("styles.css") == SITE + "styles.css"
    assert public_url("site-navigation.js") == SITE + "site-navigation.js"
    assert public_url("privacy/network-calls.json") == SITE + "privacy/network-calls.json"
    assert public_url("install/agents.md") == SITE + "install/agents.md"
    with patch(__name__ + ".urlopen", return_value=FakeResponse(SITE, b"current")):
        assert fetch_public("index.html") == b"current"
    with patch(__name__ + ".urlopen", return_value=FakeResponse(SITE + "old", b"current")):
        try:
            fetch_public("index.html")
        except ValueError:
            pass
        else:
            raise AssertionError("redirected page was accepted")
    with patch(__name__ + ".urlopen", return_value=FakeResponse(SITE, b"x" * (MAX_BYTES + 1))):
        try:
            fetch_public("index.html")
        except ValueError:
            pass
        else:
            raise AssertionError("oversized page was accepted")

    with tempfile.TemporaryDirectory() as tmp:
        docs = Path(tmp)
        for path in CRITICAL_FILES:
            local = docs / path
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(path.encode())
        assert not compare_files(docs, lambda path: path.encode())

        for stale_path in (
            "getting-started.html", "styles.css", "site-navigation.js",
            "site-metadata.json",
            "privacy/network-calls.json", "install/agents.md",
            "llms.txt", "llms-full.txt", "compatibility-worksheet.js",
        ):
            def stale(path: str) -> bytes:
                return b"old content" if path == stale_path else path.encode()

            errors = compare_files(docs, stale)
            assert len(errors) == 1 and errors[0].startswith(
                f"{stale_path}: live Pages differs"
            )

        def unavailable(path: str) -> bytes:
            if path == "install/agents.md":
                raise URLError("offline")
            return path.encode()

        errors = compare_files(docs, unavailable)
        assert len(errors) == 1 and errors[0].startswith("install/agents.md: could not verify")
    print("live Pages comparison self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    errors = compare_files(DOCS)
    if errors:
        print("Live Pages does not match the intended onboarding source:", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    print(f"Live Pages matches {len(CRITICAL_FILES)} checked-in onboarding files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
