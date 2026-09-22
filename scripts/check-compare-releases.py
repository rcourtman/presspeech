#!/usr/bin/env python3
"""Check pinned comparison-page releases against first-party GitHub releases."""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class TrackedRelease:
    product: str
    repository: str
    pages: tuple[Path, ...]


TRACKED_RELEASES = (
    TrackedRelease(
        "VoiceInk",
        "Beingpax/VoiceInk",
        (Path("docs/compare/index.html"), Path("docs/compare/voiceink.html")),
    ),
    TrackedRelease(
        "Handy",
        "cjpais/Handy",
        (Path("docs/compare/index.html"), Path("docs/compare/handy.html")),
    ),
)


def release_link_pattern(release: TrackedRelease) -> re.Pattern[str]:
    return re.compile(
        rf"https://github\.com/{re.escape(release.repository)}"
        r"/releases/tag/([^\"'< >?#]+)"
    )


def documented_tag(
    release: TrackedRelease, root: Path = ROOT
) -> tuple[str | None, list[str]]:
    errors: list[str] = []
    tags: set[str] = set()
    pattern = release_link_pattern(release)
    for relative_path in release.pages:
        path = root / relative_path
        if not path.is_file():
            errors.append(f"{relative_path}: missing comparison page")
            continue
        page_tags = {
            urllib.parse.unquote(tag)
            for tag in pattern.findall(path.read_text(encoding="utf-8"))
        }
        if not page_tags:
            errors.append(
                f"{relative_path}: no pinned {release.product} GitHub release link"
            )
            continue
        if len(page_tags) > 1:
            errors.append(
                f"{relative_path}: conflicting {release.product} release links: "
                f"{', '.join(sorted(page_tags))}"
            )
        tags.update(page_tags)
    if errors:
        return None, errors
    if len(tags) != 1:
        errors.append(
            f"{release.product}: comparison pages disagree on the pinned release: "
            f"{', '.join(sorted(tags))}"
        )
        return None, errors
    return next(iter(tags)), errors


def tag_from_release_url(release: TrackedRelease, url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    expected_prefix = f"/{release.repository}/releases/tag/"
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
        raise ValueError(f"unexpected release host in {url!r}")
    if not parsed.path.startswith(expected_prefix):
        raise ValueError(f"unexpected latest-release URL {url!r}")
    tag = urllib.parse.unquote(parsed.path[len(expected_prefix) :])
    if not tag or "/" in tag or any(character.isspace() for character in tag) or parsed.query or parsed.fragment:
        raise ValueError(f"missing or malformed release tag in {url!r}")
    return tag


def latest_tag(release: TrackedRelease) -> str:
    request = urllib.request.Request(
        f"https://github.com/{release.repository}/releases/latest",
        method="HEAD",
        headers={"User-Agent": "Presspeech comparison freshness checker"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return tag_from_release_url(release, response.geturl())


def check_releases(
    root: Path = ROOT,
    resolver: Callable[[TrackedRelease], str] = latest_tag,
    releases: tuple[TrackedRelease, ...] = TRACKED_RELEASES,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    current: list[str] = []
    for release in releases:
        pinned, page_errors = documented_tag(release, root)
        errors.extend(page_errors)
        if pinned is None:
            continue
        try:
            latest = resolver(release)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            errors.append(
                f"{release.product}: could not resolve the first-party latest release: {exc}"
            )
            continue
        if pinned != latest:
            pages = ", ".join(str(path) for path in release.pages)
            errors.append(
                f"{release.product}: comparison pages pin {pinned}, but GitHub's latest "
                f"release is {latest}; re-verify and update {pages}"
            )
            continue
        current.append(f"{release.product} {pinned}")
    return errors, current


def run_self_test() -> None:
    sample = TrackedRelease(
        "Sample", "example/sample", (Path("one.html"), Path("two.html"))
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        link = "https://github.com/example/sample/releases/tag/v1.2.3"
        for page in sample.pages:
            (root / page).write_text(f'<a href="{link}">v1.2.3</a>', encoding="utf-8")

        errors, current = check_releases(
            root=root, resolver=lambda _release: "v1.2.3", releases=(sample,)
        )
        if errors or current != ["Sample v1.2.3"]:
            raise RuntimeError("self-test: current release was rejected")

        errors, _current = check_releases(
            root=root, resolver=lambda _release: "v1.2.4", releases=(sample,)
        )
        if not errors or "latest release is v1.2.4" not in errors[0]:
            raise RuntimeError("self-test: stale release was not rejected")

        (root / "two.html").write_text(
            '<a href="https://github.com/example/sample/releases/tag/v1.2.2">old</a>',
            encoding="utf-8",
        )
        tag, errors = documented_tag(sample, root)
        if tag is not None or not errors or "disagree" not in errors[0]:
            raise RuntimeError("self-test: inconsistent page releases were not rejected")

        (root / "two.html").unlink()
        tag, errors = documented_tag(sample, root)
        if tag is not None or not any("missing comparison page" in error for error in errors):
            raise RuntimeError("self-test: missing comparison page was accepted")
        for page in sample.pages:
            (root / page).write_text(f'<a href="{link}">v1.2.3</a>', encoding="utf-8")
        def unavailable(_release):
            raise OSError("offline")
        errors, current = check_releases(root=root, resolver=unavailable, releases=(sample,))
        if current or not errors or "could not resolve" not in errors[0]:
            raise RuntimeError("self-test: network failure falsely reported a current release")
        (root / "two.html").write_text(
            f'<a href="{link}">v1.2.3</a><a href="{link[:-1]}4">v1.2.4</a>', encoding="utf-8")
        tag, errors = documented_tag(sample, root)
        if tag is not None or not any("conflicting" in error for error in errors):
            raise RuntimeError("self-test: conflicting links on one page were accepted")

        final_url = "https://github.com/example/sample/releases/tag/v1.2.3"
        if tag_from_release_url(sample, final_url) != "v1.2.3":
            raise RuntimeError("self-test: final release URL was not parsed")
        for invalid_url in (
            "https://example.com/releases/tag/v1.2.3",
            "https://github.com/other/sample/releases/tag/v1.2.3",
            "https://github.com/example/sample/releases/latest",
            final_url + "%0a",
            final_url + "%2fextra",
            final_url + "?anything=1",
            final_url + "#anything",
        ):
            try:
                tag_from_release_url(sample, invalid_url)
            except ValueError:
                pass
            else:
                raise RuntimeError("self-test: unexpected release URL was accepted")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--self-test", action="store_true", help="run offline tests")
    mode.add_argument(
        "--check-docs",
        action="store_true",
        help="check that local comparison pages pin one consistent release",
    )
    args = parser.parse_args()

    if args.self_test:
        run_self_test()
        print("comparison release checker self-test passed")
        return 0

    if args.check_docs:
        errors: list[str] = []
        pinned: list[str] = []
        for release in TRACKED_RELEASES:
            tag, page_errors = documented_tag(release)
            errors.extend(page_errors)
            if tag is not None:
                pinned.append(f"{release.product} {tag}")
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        print(f"comparison release links agree: {', '.join(pinned)}")
        return 0

    errors, current = check_releases()
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"comparison release references are current: {', '.join(current)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
