#!/usr/bin/env python3
"""Check static documentation for durable, testable accessibility basics."""

from __future__ import annotations

import argparse
import math
import re
import sys
import tempfile
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
STYLES = DOCS / "styles.css"
MIN_TEXT_CONTRAST = 4.5


class DocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.html_lang: str | None = None
        self.ids: list[str] = []
        self.heading_levels: list[int] = []
        self.main_count = 0
        self.title_count = 0
        self.missing_alt_count = 0
        self.primary_nav_count = 0
        self.current_links: list[tuple[str | None, str]] = []
        self.skip_links: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "html":
            self.html_lang = attributes.get("lang")
        if tag == "main":
            self.main_count += 1
        if tag == "title":
            self.title_count += 1
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.heading_levels.append(int(tag[1]))
        if "id" in attributes and attributes["id"] is not None:
            self.ids.append(attributes["id"])
        if tag == "img" and "alt" not in attributes:
            self.missing_alt_count += 1
        if tag == "nav" and attributes.get("aria-label") == "Primary":
            self.primary_nav_count += 1
        if tag == "a":
            if attributes.get("aria-current") is not None:
                self.current_links.append(
                    (attributes.get("href"), attributes["aria-current"] or "")
                )
            classes = (attributes.get("class") or "").split()
            if "skip-link" in classes:
                self.skip_links.append(attributes.get("href"))


def expected_current_href(path: Path, docs: Path) -> str:
    relative = path.relative_to(docs)
    if relative == Path("index.html"):
        return "./"
    if relative.parts[0] == "compare":
        return "./"
    return relative.name


def document_errors(path: Path, docs: Path) -> list[str]:
    parser = DocumentParser()
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()
    errors: list[str] = []

    if parser.html_lang != "en":
        errors.append(f"html lang must be 'en', found {parser.html_lang!r}")
    if parser.title_count != 1:
        errors.append(f"expected one title, found {parser.title_count}")
    if parser.main_count != 1:
        errors.append(f"expected one main landmark, found {parser.main_count}")
    if parser.heading_levels.count(1) != 1:
        errors.append(f"expected one h1, found {parser.heading_levels.count(1)}")
    if any(
        next_level > level + 1
        for level, next_level in zip(parser.heading_levels, parser.heading_levels[1:])
    ):
        errors.append(f"heading levels skip: {parser.heading_levels}")
    duplicate_ids = sorted(value for value, count in Counter(parser.ids).items() if count > 1)
    if duplicate_ids:
        errors.append(f"duplicate ids: {', '.join(duplicate_ids)}")
    if parser.missing_alt_count:
        errors.append(f"{parser.missing_alt_count} img element(s) lack alt")
    if parser.primary_nav_count != 1:
        errors.append(f"expected one primary navigation, found {parser.primary_nav_count}")
    expected_current = [(expected_current_href(path, docs), "page")]
    if parser.current_links != expected_current:
        errors.append(
            f"current navigation must be {expected_current!r}, found {parser.current_links!r}"
        )
    if parser.skip_links != ["#main-content"]:
        errors.append(
            f"expected one skip link to #main-content, found {parser.skip_links!r}"
        )
    if "main-content" not in parser.ids:
        errors.append("skip-link target #main-content is missing")
    return errors


def relative_luminance(hex_color: str) -> float:
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", hex_color):
        raise ValueError(f"expected six-digit hex color, found {hex_color!r}")
    channels = [int(hex_color[index : index + 2], 16) / 255 for index in (1, 3, 5)]

    def linear(channel: float) -> float:
        if channel <= 0.04045:
            return channel / 12.92
        return math.pow((channel + 0.055) / 1.055, 2.4)

    red, green, blue = (linear(channel) for channel in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first: str, second: str) -> float:
    lighter, darker = sorted(
        (relative_luminance(first), relative_luminance(second)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def css_variables(css: str) -> dict[str, str]:
    root_match = re.search(r":root\s*\{(?P<body>.*?)\}", css, flags=re.S)
    if root_match is None:
        raise ValueError("missing :root CSS block")
    return dict(
        re.findall(r"--([\w-]+)\s*:\s*(#[0-9a-fA-F]{6})\s*;", root_match.group("body"))
    )


def contrast_errors(styles: Path) -> list[str]:
    variables = css_variables(styles.read_text(encoding="utf-8"))
    pairs = [
        ("muted", "bg-tint", variables.get("muted"), variables.get("bg-tint")),
        ("muted-2", "menu mock", variables.get("muted-2"), "#ece9e1"),
        ("accent", "panel", variables.get("accent"), variables.get("panel")),
    ]
    errors: list[str] = []
    for foreground_name, background_name, foreground, background in pairs:
        if foreground is None or background is None:
            errors.append(
                f"missing CSS color for {foreground_name} on {background_name} contrast check"
            )
            continue
        ratio = contrast_ratio(foreground, background)
        if ratio + 1e-9 < MIN_TEXT_CONTRAST:
            errors.append(
                f"{foreground_name} {foreground} on {background_name} {background} "
                f"has {ratio:.2f}:1 contrast; need {MIN_TEXT_CONTRAST:.1f}:1"
            )
    return errors


def accessibility_errors(docs: Path = DOCS, styles: Path = STYLES) -> list[str]:
    errors: list[str] = []
    for path in sorted(docs.rglob("*.html")):
        for error in document_errors(path, docs):
            errors.append(f"{path.relative_to(docs)}: {error}")
    for error in contrast_errors(styles):
        errors.append(f"{styles.name}: {error}")
    return errors


def run_self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        docs = Path(tmp)
        index = docs / "index.html"
        index.write_text(
            "<!doctype html><html lang='en'><head><title>Test</title></head><body>"
            "<a class='skip-link' href='#main-content'>Skip</a>"
            "<nav aria-label='Primary'><a href='./' aria-current='page'>Home</a></nav>"
            "<main id='main-content'><h1>Test</h1><img src='test.png' alt=''></main>"
            "</body></html>",
            encoding="utf-8",
        )
        if document_errors(index, docs):
            raise RuntimeError("self-test: valid document was rejected")
        index.write_text(
            index.read_text(encoding="utf-8").replace(" aria-current='page'", ""),
            encoding="utf-8",
        )
        errors = document_errors(index, docs)
        if not any("current navigation" in error for error in errors):
            raise RuntimeError("self-test: missing current navigation was accepted")

    if round(contrast_ratio("#000000", "#ffffff"), 2) != 21.0:
        raise RuntimeError("self-test: contrast calculation is incorrect")
    if contrast_ratio("#8a948e", "#fbfaf8") >= MIN_TEXT_CONTRAST:
        raise RuntimeError("self-test: low-contrast fixture was accepted")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="run isolated checker tests")
    args = parser.parse_args()
    try:
        if args.self_test:
            run_self_test()
            print("docs accessibility self-test passed")
            return 0
        errors = accessibility_errors()
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        print("docs accessibility checks passed")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"check-docs-accessibility: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
