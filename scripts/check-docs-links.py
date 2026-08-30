#!/usr/bin/env python3
"""Validate relative links and fragments in the checked-in documentation site."""

from __future__ import annotations

import argparse
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
LINK_ATTRIBUTES = {"href", "src", "poster"}


class DocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.anchors: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if value is None:
                continue
            if name in LINK_ATTRIBUTES:
                self.links.append((name, value))
            if name == "id" or (tag == "a" and name == "name"):
                self.anchors.add(value)


def parse_document(path: Path) -> DocumentParser:
    parser = DocumentParser()
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()
    return parser


def display(path: Path, docs: Path) -> str:
    try:
        return str(path.relative_to(docs))
    except ValueError:
        return str(path)


def link_errors(docs: Path = DOCS) -> list[str]:
    docs = docs.resolve()
    html_paths = sorted(docs.rglob("*.html"))
    parsed = {path.resolve(): parse_document(path) for path in html_paths}
    errors: list[str] = []

    for source, document in list(parsed.items()):
        for attribute, raw_value in document.links:
            value = raw_value.strip()
            parts = urlsplit(value)
            if not value or parts.scheme or parts.netloc or value.startswith("//"):
                continue

            raw_path = unquote(parts.path)
            if raw_path.startswith("/"):
                errors.append(
                    f"{display(source, docs)}: {attribute}={raw_value!r} uses an unsupported site-absolute path"
                )
                continue

            target = (source.parent / raw_path).resolve() if raw_path else source
            try:
                target.relative_to(docs)
            except ValueError:
                errors.append(
                    f"{display(source, docs)}: {attribute}={raw_value!r} escapes docs/"
                )
                continue

            if raw_path.endswith("/") or target.is_dir():
                target /= "index.html"
            if not target.is_file():
                errors.append(
                    f"{display(source, docs)}: {attribute}={raw_value!r} targets missing {display(target, docs)}"
                )
                continue

            if parts.fragment and target.suffix.lower() == ".html":
                target_document = parsed.get(target.resolve())
                if target_document is None:
                    target_document = parse_document(target)
                    parsed[target.resolve()] = target_document
                fragment = unquote(parts.fragment)
                if fragment not in target_document.anchors:
                    errors.append(
                        f"{display(source, docs)}: {attribute}={raw_value!r} targets missing fragment #{fragment}"
                    )
    return errors


def run_self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        docs = Path(tmp)
        (docs / "guide").mkdir()
        (docs / "asset.txt").write_text("ok\n", encoding="utf-8")
        (docs / "guide" / "index.html").write_text(
            '<!doctype html><main id="setup">Guide</main>\n', encoding="utf-8"
        )
        index = docs / "index.html"
        index.write_text(
            '<!doctype html><main><a href="guide/#setup">Guide</a>'
            '<a href="asset.txt">Asset</a><a href="https://example.com">External</a></main>\n',
            encoding="utf-8",
        )
        if link_errors(docs):
            raise RuntimeError("self-test: valid local links were rejected")
        index.write_text(
            '<!doctype html><main><a href="missing.html">Missing</a>'
            '<a href="guide/#absent">Bad fragment</a></main>\n',
            encoding="utf-8",
        )
        errors = link_errors(docs)
        if len(errors) != 2:
            raise RuntimeError(f"self-test: expected two errors, found {errors!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="run isolated parser checks")
    args = parser.parse_args()
    try:
        if args.self_test:
            run_self_test()
            print("docs link self-test passed")
            return 0
        errors = link_errors()
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        print("docs links are valid")
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"check-docs-links: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
