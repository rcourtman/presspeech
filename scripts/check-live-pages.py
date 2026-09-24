#!/usr/bin/env python3
"""Compare the public onboarding pages with the intended checked-in docs.

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
CRITICAL_PAGES = (
    "index.html",
    "getting-started.html",
    "install.html",
    "windows.html",
    "privacy.html",
    "troubleshooting.html",
    "app-compatibility.html",
    "faq.html",
)
MAX_BYTES = 1024 * 1024


def public_url(page: str) -> str:
    return SITE + ("" if page == "index.html" else page)


def fetch_public(page: str) -> bytes:
    url = public_url(page)
    request = Request(url, headers={"User-Agent": "Presspeech-Pages-check/1.0",
                                    "Cache-Control": "no-cache"})
    with urlopen(request, timeout=15) as response:
        if response.status != 200 or response.geturl() != url:
            raise ValueError(f"unexpected response or redirect for {url}")
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError(f"public page exceeds {MAX_BYTES} bytes: {url}")
    return body


def short_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def compare_pages(docs: Path, fetch=fetch_public) -> list[str]:
    errors = []
    for page in CRITICAL_PAGES:
        try:
            local = (docs / page).read_bytes()
            remote = fetch(page)
            if local != remote:
                errors.append(
                    f"{page}: live Pages differs from checked-in docs "
                    f"(local sha256 {short_hash(local)}, live sha256 {short_hash(remote)})"
                )
        except (OSError, ValueError, HTTPError, URLError) as exc:
            errors.append(f"{page}: could not verify live Pages: {exc}")
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
        for page in CRITICAL_PAGES:
            (docs / page).write_bytes(page.encode())
        assert not compare_pages(docs, lambda page: page.encode())

        def stale(page: str) -> bytes:
            return b"old guide" if page == "getting-started.html" else page.encode()

        errors = compare_pages(docs, stale)
        assert len(errors) == 1 and errors[0].startswith("getting-started.html: live Pages differs")

        def unavailable(page: str) -> bytes:
            if page == "windows.html":
                raise URLError("offline")
            return page.encode()

        errors = compare_pages(docs, unavailable)
        assert len(errors) == 1 and errors[0].startswith("windows.html: could not verify")
    print("live Pages comparison self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    errors = compare_pages(DOCS)
    if errors:
        print("Live Pages does not match the intended onboarding source:", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    print(f"Live Pages matches {len(CRITICAL_PAGES)} checked-in onboarding pages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
