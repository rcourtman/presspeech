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
MIN_FOCUS_CONTRAST = 3.0
MIN_FOCUS_THICKNESS = 2
MIN_MOBILE_NAV_TARGET = 44
ERROR_PAGE = Path("404.html")


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
        self.primary_nav_links: list[str] = []
        self._in_primary_nav = False
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
            self._in_primary_nav = True
        if tag == "a":
            if self._in_primary_nav and attributes.get("href") is not None:
                self.primary_nav_links.append(attributes["href"])
            if attributes.get("aria-current") is not None:
                self.current_links.append(
                    (attributes.get("href"), attributes["aria-current"] or "")
                )
            classes = (attributes.get("class") or "").split()
            if "skip-link" in classes:
                self.skip_links.append(attributes.get("href"))

    def handle_endtag(self, tag: str) -> None:
        if tag == "nav" and self._in_primary_nav:
            self._in_primary_nav = False


def expected_current_href(path: Path, docs: Path) -> str | None:
    relative = path.relative_to(docs)
    if relative == ERROR_PAGE:
        return None
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
    relative = path.relative_to(docs)
    if relative != ERROR_PAGE:
        expected_help_href = (
            "../troubleshooting.html"
            if relative.parts[0] == "compare"
            else "troubleshooting.html"
        )
        if parser.primary_nav_links.count(expected_help_href) != 1:
            errors.append(
                "primary navigation must contain one consistent Help link to "
                f"{expected_help_href!r}, found {parser.primary_nav_links!r}"
            )
    current_href = expected_current_href(path, docs)
    expected_current = [] if current_href is None else [(current_href, "page")]
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


def css_block(css: str, header: str) -> str | None:
    """Return the contents of the first balanced CSS block for header."""
    match = re.search(re.escape(header) + r"\s*\{", css)
    if match is None:
        return None
    start = match.end()
    depth = 1
    for index in range(start, len(css)):
        if css[index] == "{":
            depth += 1
        elif css[index] == "}":
            depth -= 1
            if depth == 0:
                return css[start:index]
    return None


def css_declarations(css: str, selector: str) -> dict[str, str] | None:
    block = css_block(css, selector)
    if block is None:
        return None
    return dict(re.findall(r"([\w-]+)\s*:\s*([^;{}]+);", block))


def pixel_value(value: str | None) -> float | None:
    if value is None:
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)px\s*", value)
    return float(match.group(1)) if match else None


def navigation_target_errors(css: str) -> list[str]:
    """Enforce the site's comfortable narrow-screen navigation baseline."""
    errors: list[str] = []
    media = css_block(css, "@media (max-width: 720px)")
    if media is None:
        return ["missing the max-width: 720px mobile navigation rules"]

    brand = css_declarations(media, ".brand")
    links = css_declarations(media, ".nav-links")
    link = css_declarations(media, ".nav-links a")
    brand_height = pixel_value(brand.get("min-height")) if brand is not None else None
    if brand_height is None or brand_height < MIN_MOBILE_NAV_TARGET:
        errors.append(
            f"mobile brand target must have a {MIN_MOBILE_NAV_TARGET}px minimum height"
        )
    if links is None or links.get("width", "").strip() != "100%":
        errors.append("mobile navigation links must occupy the full row")
    if link is None:
        errors.append("missing mobile navigation link rules")
        return errors
    if link.get("display", "").strip() != "inline-flex":
        errors.append("mobile navigation links must use inline-flex target boxes")
    for dimension in ("min-width", "min-height"):
        target_size = pixel_value(link.get(dimension))
        if target_size is None or target_size < MIN_MOBILE_NAV_TARGET:
            errors.append(
                f"mobile navigation links must have a {MIN_MOBILE_NAV_TARGET}px {dimension}"
            )
    return errors


def focus_indicator_errors(css: str) -> list[str]:
    """Enforce a durable keyboard focus ring on every light site surface."""
    errors: list[str] = []
    variables = css_variables(css)
    focus = variables.get("focus")
    if focus is None:
        return ["missing --focus CSS color"]

    declarations = css_declarations(css, "a:focus-visible")
    if declarations is None:
        return ["missing a:focus-visible rules"]
    outline = declarations.get("outline", "").strip()
    match = re.fullmatch(
        r"(?P<thickness>\d+(?:\.\d+)?)px\s+solid\s+var\(--focus\)", outline
    )
    if match is None:
        errors.append("keyboard focus outline must use a solid --focus color")
    elif float(match.group("thickness")) < MIN_FOCUS_THICKNESS:
        errors.append(
            f"keyboard focus outline must be at least {MIN_FOCUS_THICKNESS}px thick"
        )

    # Links occur directly on each of these surfaces. Requiring the ring to
    # survive the least favourable one avoids a passing homepage check while
    # focus remains hard to see in a card, note, code sample, or tinted block.
    surface_names = ("bg", "bg-tint", "panel", "soft", "soft-2", "warn", "code")
    for background_name in surface_names:
        background = variables.get(background_name)
        if background is None:
            errors.append(
                f"missing --{background_name} CSS color for focus contrast check"
            )
            continue
        ratio = contrast_ratio(focus, background)
        if ratio + 1e-9 < MIN_FOCUS_CONTRAST:
            errors.append(
                f"focus {focus} on {background_name} {background} has {ratio:.2f}:1 "
                f"contrast; need {MIN_FOCUS_CONTRAST:.1f}:1"
            )
    return errors


def accessibility_errors(docs: Path = DOCS, styles: Path = STYLES) -> list[str]:
    errors: list[str] = []
    for path in sorted(docs.rglob("*.html")):
        for error in document_errors(path, docs):
            errors.append(f"{path.relative_to(docs)}: {error}")
    for error in contrast_errors(styles):
        errors.append(f"{styles.name}: {error}")
    css = styles.read_text(encoding="utf-8")
    for error in navigation_target_errors(css):
        errors.append(f"{styles.name}: {error}")
    for error in focus_indicator_errors(css):
        errors.append(f"{styles.name}: {error}")
    return errors


def run_self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        docs = Path(tmp)
        index = docs / "index.html"
        index.write_text(
            "<!doctype html><html lang='en'><head><title>Test</title></head><body>"
            "<a class='skip-link' href='#main-content'>Skip</a>"
            "<nav aria-label='Primary'><a href='./' aria-current='page'>Home</a>"
            "<a href='troubleshooting.html'>Help</a></nav>"
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
        index.write_text(
            index.read_text(encoding="utf-8").replace(
                "<a href='troubleshooting.html'>Help</a>", ""
            ),
            encoding="utf-8",
        )
        errors = document_errors(index, docs)
        if not any("consistent Help link" in error for error in errors):
            raise RuntimeError("self-test: missing Help navigation was accepted")

        error_page = docs / ERROR_PAGE
        error_page.write_text(
            "<!doctype html><html lang='en'><head><title>Missing</title></head><body>"
            "<a class='skip-link' href='#main-content'>Skip</a>"
            "<nav aria-label='Primary'><a href='./'>Home</a></nav>"
            "<main id='main-content'><h1>Not found</h1></main></body></html>",
            encoding="utf-8",
        )
        if document_errors(error_page, docs):
            raise RuntimeError("self-test: valid 404 page was rejected")

    if round(contrast_ratio("#000000", "#ffffff"), 2) != 21.0:
        raise RuntimeError("self-test: contrast calculation is incorrect")
    if contrast_ratio("#8a948e", "#fbfaf8") >= MIN_TEXT_CONTRAST:
        raise RuntimeError("self-test: low-contrast fixture was accepted")

    mobile_css = """
    @media (max-width: 720px) {
      .brand { min-height: 44px; }
      .nav-links { width: 100%; }
      .nav-links a {
        display: inline-flex;
        min-width: 44px;
        min-height: 44px;
      }
    }
    """
    if navigation_target_errors(mobile_css):
        raise RuntimeError("self-test: valid mobile navigation targets were rejected")
    undersized_css = mobile_css.replace("min-height: 44px;", "min-height: 23px;", 1)
    errors = navigation_target_errors(undersized_css)
    if not any("brand target" in error for error in errors):
        raise RuntimeError("self-test: undersized mobile brand target was accepted")
    missing_width_css = mobile_css.replace("min-width: 44px;", "")
    errors = navigation_target_errors(missing_width_css)
    if not any("44px min-width" in error for error in errors):
        raise RuntimeError("self-test: mobile link without a minimum width was accepted")

    focus_css = """
    :root {
      --focus: #0d7f5f;
      --bg: #fbfaf8;
      --bg-tint: #f4f1ea;
      --panel: #ffffff;
      --soft: #e9f5f0;
      --soft-2: #f3f9f6;
      --warn: #fff5d8;
      --code: #f3f2ed;
    }
    a:focus-visible {
      outline: 3px solid var(--focus);
      outline-offset: 3px;
    }
    """
    if focus_indicator_errors(focus_css):
        raise RuntimeError("self-test: valid focus indicator was rejected")
    low_contrast_focus_css = focus_css.replace(
        "--focus: #0d7f5f", "--focus: #7ab9a7"
    )
    errors = focus_indicator_errors(low_contrast_focus_css)
    if not any("focus #7ab9a7" in error for error in errors):
        raise RuntimeError("self-test: low-contrast focus indicator was accepted")
    thin_focus_css = focus_css.replace("outline: 3px", "outline: 1px")
    errors = focus_indicator_errors(thin_focus_css)
    if not any("at least 2px thick" in error for error in errors):
        raise RuntimeError("self-test: thin focus indicator was accepted")


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
