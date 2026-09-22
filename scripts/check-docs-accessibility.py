#!/usr/bin/env python3
"""Check static documentation conventions; not a browser accessibility audit.

The skip link must be the body's first element and use plain visible text.
Positive tabindex is forbidden. These intentionally strict site conventions
avoid approximating browser focus order with an HTML parser. CSS checks cover
explicit site rules, not computed styles, responsive visibility, or scripting.
"""

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
MIN_NAV_TARGET = 24
MIN_MOBILE_NAV_TARGET = 44
MIN_STICKY_HEADER_OFFSET = 64
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
        self.primary_nav_items: list[tuple[str, str]] = []
        self._in_primary_nav = False
        self._primary_nav_link: tuple[str, list[str]] | None = None
        self.nav_toggle_count = 0
        self.nav_toggle_names: list[str] = []
        self.nav_toggle_issues: list[str] = []
        self._nav_toggle_text: list[str] | None = None
        self.navigation_scripts: list[str] = []
        self.brand_link_count = 0
        self.brand_link_names: list[str] = []
        self.brand_link_issues: list[str] = []
        self.brand_mark_count = 0
        self.brand_mark_issues: list[str] = []
        self._brand_link_text: list[str] | None = None
        self._in_brand_mark = False
        self.current_links: list[tuple[str | None, str]] = []
        self.skip_links: list[str | None] = []
        self.skip_link_names: list[str] = []
        self.skip_link_orders: list[int] = []
        self.skip_link_issues: list[str] = []
        self._skip_link_text: list[str] | None = None
        self._in_body = False
        self.body_count = 0
        self._element_order = 0
        self.first_body_element: int | None = None
        self.tabindex_issues: list[str] = []
        self.video_descriptions: list[str | None] = []
        self.hidden_ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        self._element_order += 1
        if self._skip_link_text is not None:
            self.skip_link_issues.append("skip link must use plain visible text without child elements")
        if tag == "body":
            self.body_count += 1
            self._in_body = True
        elif self._in_body and self.first_body_element is None:
            self.first_body_element = self._element_order
        if tag in {"html", "body"} and (
            "hidden" in attributes or "inert" in attributes
            or (attributes.get("aria-hidden") or "").lower() == "true"
        ):
            self.skip_link_issues.append("skip-link ancestors must not be hidden or inert")
        tabindex = attributes.get("tabindex")
        if "tabindex" in attributes:
            if tabindex is None or re.fullmatch(r"[+-]?[0-9]+", tabindex.strip()) is None:
                self.tabindex_issues.append("tabindex must be an integer")
            elif int(tabindex) > 0:
                self.tabindex_issues.append("positive tabindex is forbidden by the site's source-order convention")
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
            if (
                "hidden" in attributes
                or "inert" in attributes
                or (attributes.get("aria-hidden") or "").lower() == "true"
            ):
                self.hidden_ids.add(attributes["id"])
        if tag == "img" and "alt" not in attributes:
            self.missing_alt_count += 1
        if tag == "video":
            self.video_descriptions.append(attributes.get("aria-describedby"))
        if tag == "script" and (attributes.get("src") or "").endswith(
            "site-navigation.js"
        ):
            self.navigation_scripts.append(attributes["src"] or "")
        if tag == "nav" and attributes.get("aria-label") == "Primary":
            self.primary_nav_count += 1
            self._in_primary_nav = True
        if tag == "button" and "nav-toggle" in (attributes.get("class") or "").split():
            self.nav_toggle_count += 1
            self._nav_toggle_text = []
            if not self._in_primary_nav:
                self.nav_toggle_issues.append(
                    "navigation toggle must be inside the primary navigation"
                )
            if attributes.get("type") != "button":
                self.nav_toggle_issues.append("navigation toggle must use type='button'")
            if attributes.get("aria-controls") != "primary-navigation-links":
                self.nav_toggle_issues.append(
                    "navigation toggle must control #primary-navigation-links"
                )
            if attributes.get("aria-expanded") != "false":
                self.nav_toggle_issues.append(
                    "navigation toggle must start with aria-expanded='false'"
                )
            if "data-navigation-toggle" not in attributes:
                self.nav_toggle_issues.append(
                    "navigation toggle must expose the shared script hook"
                )
            if any(name in attributes for name in ("hidden", "inert", "aria-label")) or (
                attributes.get("aria-hidden") or ""
            ).lower() == "true":
                self.nav_toggle_issues.append(
                    "navigation toggle must take its accessible name from visible text"
                )
        if tag == "a":
            classes = (attributes.get("class") or "").split()
            if "brand" in classes:
                self.brand_link_count += 1
                self._brand_link_text = []
                if not self._in_primary_nav:
                    self.brand_link_issues.append(
                        "brand link must be inside the primary navigation"
                    )
                if not attributes.get("href"):
                    self.brand_link_issues.append("brand link must have a destination")
                if any(name in attributes for name in ("hidden", "inert")) or (
                    attributes.get("aria-hidden") or ""
                ).lower() == "true":
                    self.brand_link_issues.append(
                        "brand link must be exposed to assistive technology"
                    )
                if any(name in attributes for name in ("aria-label", "aria-labelledby")):
                    self.brand_link_issues.append(
                        "brand link must take its accessible name from visible text"
                    )
                if attributes.get("role", "link") != "link":
                    self.brand_link_issues.append("brand link must retain link semantics")
            if self._in_primary_nav and attributes.get("href") is not None:
                if "brand" not in classes:
                    self._primary_nav_link = (attributes["href"], [])
            if attributes.get("aria-current") is not None:
                self.current_links.append(
                    (attributes.get("href"), attributes["aria-current"] or "")
                )
            if "skip-link" in classes:
                self.skip_links.append(attributes.get("href"))
                self.skip_link_orders.append(self._element_order)
                self._skip_link_text = []
                if "tabindex" in attributes and (tabindex is None or tabindex.strip() != "0"):
                    self.skip_link_issues.append("skip link tabindex must be absent or 0")
                if any(name in attributes for name in ("hidden", "inert", "style", "aria-labelledby")):
                    self.skip_link_issues.append("skip link must use shared styles and its visible text name")
                if any((attributes.get(name) or "").lower() == "true"
                       for name in ("aria-hidden", "aria-disabled")):
                    self.skip_link_issues.append("skip link must not be hidden or disabled to assistive technology")
                if attributes.get("aria-label", "Skip to content") != "Skip to content":
                    self.skip_link_issues.append("skip link accessible label must match 'Skip to content'")
                if attributes.get("role", "link") != "link":
                    self.skip_link_issues.append("skip link must retain link semantics")
        classes = (attributes.get("class") or "").split()
        if "brand-mark" in classes:
            self.brand_mark_count += 1
            self._in_brand_mark = True
            if self._brand_link_text is None:
                self.brand_mark_issues.append("brand mark must be inside the brand link")
            if (attributes.get("aria-hidden") or "").lower() != "true":
                self.brand_mark_issues.append(
                    "decorative brand mark must use aria-hidden='true'"
                )

    def handle_data(self, data: str) -> None:
        if self._primary_nav_link is not None:
            self._primary_nav_link[1].append(data)
        if self._skip_link_text is not None:
            self._skip_link_text.append(data)
        if self._brand_link_text is not None and not self._in_brand_mark:
            self._brand_link_text.append(data)
        if self._nav_toggle_text is not None:
            self._nav_toggle_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "nav" and self._in_primary_nav:
            self._in_primary_nav = False
        if tag == "body":
            self._in_body = False
        if tag == "span" and self._in_brand_mark:
            self._in_brand_mark = False
        if tag == "a" and self._skip_link_text is not None:
            self.skip_link_names.append(" ".join("".join(self._skip_link_text).split()))
            self._skip_link_text = None
        if tag == "a" and self._brand_link_text is not None:
            self.brand_link_names.append(
                " ".join("".join(self._brand_link_text).split())
            )
            self._brand_link_text = None
        if tag == "a" and self._primary_nav_link is not None:
            href, parts = self._primary_nav_link
            self.primary_nav_items.append((href, " ".join("".join(parts).split())))
            self._primary_nav_link = None
        if tag == "button" and self._nav_toggle_text is not None:
            self.nav_toggle_names.append(
                " ".join("".join(self._nav_toggle_text).split())
            )
            self._nav_toggle_text = None


def expected_current_href(path: Path, docs: Path) -> str | None:
    relative = path.relative_to(docs)
    if relative == ERROR_PAGE:
        return None
    if relative == Path("index.html"):
        return "./"
    if relative.parts[0] == "compare":
        return "./"
    if relative == Path("app-compatibility.html"):
        return "troubleshooting.html"
    return relative.name


def expected_primary_nav(path: Path, docs: Path) -> list[tuple[str, str]]:
    """Return the complete shared navigation for a page's directory depth."""
    relative = path.relative_to(docs)
    if relative == ERROR_PAGE:
        prefix = "/presspeech/"
        compare_href = "/presspeech/compare/"
    elif relative.parts[0] == "compare":
        prefix = "../"
        compare_href = "./"
    else:
        prefix = ""
        compare_href = "compare/"
    return [
        (f"{prefix}getting-started.html", "Get started"),
        (f"{prefix}install.html", "macOS"),
        (f"{prefix}windows.html", "Windows"),
        (f"{prefix}privacy.html", "Privacy"),
        (f"{prefix}benchmarks.html", "Benchmarks"),
        (f"{prefix}faq.html", "FAQ"),
        (f"{prefix}troubleshooting.html", "Help"),
        (compare_href, "Compare"),
        ("https://github.com/rcourtman/presspeech", "GitHub"),
    ]


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
    for description in parser.video_descriptions:
        references = description.split() if description is not None else []
        if not references:
            errors.append("video must reference a visible description with aria-describedby")
            continue
        missing = [reference for reference in references if reference not in parser.ids]
        if missing:
            errors.append(
                "video aria-describedby targets missing id(s): " + ", ".join(missing)
            )
        hidden = [reference for reference in references if reference in parser.hidden_ids]
        if hidden:
            errors.append(
                "video aria-describedby targets hidden id(s): " + ", ".join(hidden)
            )
    if parser.primary_nav_count != 1:
        errors.append(f"expected one primary navigation, found {parser.primary_nav_count}")
    if parser.nav_toggle_count != 1:
        errors.append(f"expected one navigation toggle, found {parser.nav_toggle_count}")
    if parser.nav_toggle_names != ["Menu"]:
        errors.append(
            "navigation toggle accessible name must come from its visible 'Menu' text; "
            f"found {parser.nav_toggle_names!r}"
        )
    if parser.ids.count("primary-navigation-links") != 1:
        errors.append("navigation toggle target #primary-navigation-links must exist once")
    errors.extend(dict.fromkeys(parser.nav_toggle_issues))
    if parser.brand_link_count != 1:
        errors.append(f"expected one brand link, found {parser.brand_link_count}")
    if parser.brand_mark_count != 1:
        errors.append(f"expected one decorative brand mark, found {parser.brand_mark_count}")
    if parser.brand_link_names != ["Presspeech"]:
        errors.append(
            "brand link accessible name must come from its visible 'Presspeech' text; "
            f"found {parser.brand_link_names!r}"
        )
    errors.extend(dict.fromkeys(parser.brand_link_issues + parser.brand_mark_issues))
    relative = path.relative_to(docs)
    expected_nav = expected_primary_nav(path, docs)
    if parser.primary_nav_items != expected_nav:
        errors.append(
            "primary navigation links and visible names must match the shared order; "
            f"expected {expected_nav!r}, found {parser.primary_nav_items!r}"
        )
    expected_navigation_script = (
        "/presspeech/site-navigation.js"
        if relative == ERROR_PAGE
        else "../site-navigation.js"
        if relative.parts[0] == "compare"
        else "site-navigation.js"
    )
    if parser.navigation_scripts != [expected_navigation_script]:
        errors.append(
            "expected one shared navigation script at "
            f"{expected_navigation_script!r}, found {parser.navigation_scripts!r}"
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
    if parser.body_count != 1:
        errors.append("expected one body element")
    if parser.skip_link_orders != [parser.first_body_element]:
        errors.append("site convention: skip link must be the first element inside body")
    if parser.skip_link_names != ["Skip to content"]:
        errors.append("skip link must have the consistent visible name 'Skip to content'")
    errors.extend(dict.fromkeys(parser.skip_link_issues + parser.tabindex_issues))
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
    """Enforce WCAG-sized desktop and comfortable narrow-screen navigation."""
    errors: list[str] = []
    desktop_link = css_declarations(css, ".nav-links a")
    if desktop_link is None:
        errors.append("missing shared navigation link rules")
    else:
        for dimension in ("min-width", "min-height"):
            target_size = pixel_value(desktop_link.get(dimension))
            if target_size is None or target_size < MIN_NAV_TARGET:
                errors.append(
                    f"navigation links must have at least a {MIN_NAV_TARGET}px {dimension}"
                )

    media = css_block(css, "@media (max-width: 720px)")
    if media is None:
        return ["missing the max-width: 720px mobile navigation rules"]

    brand = css_declarations(media, ".brand")
    toggle = css_declarations(media, "html.navigation-ready .nav-toggle")
    links = css_declarations(media, ".nav-links")
    link = css_declarations(media, ".nav-links a")
    brand_height = pixel_value(brand.get("min-height")) if brand is not None else None
    if brand_height is None or brand_height < MIN_MOBILE_NAV_TARGET:
        errors.append(
            f"mobile brand target must have a {MIN_MOBILE_NAV_TARGET}px minimum height"
        )
    if toggle is None or toggle.get("display", "").strip() != "inline-flex":
        errors.append("enhanced mobile navigation must expose its menu button")
    else:
        for dimension in ("min-width", "min-height"):
            target_size = pixel_value(toggle.get(dimension))
            if target_size is None or target_size < MIN_MOBILE_NAV_TARGET:
                errors.append(
                    "mobile navigation toggle must have a "
                    f"{MIN_MOBILE_NAV_TARGET}px {dimension}"
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


def sticky_header_offset_errors(css: str) -> list[str]:
    """Keep fragment and focus scrolling clear of the shared sticky header."""
    errors: list[str] = []
    root = css_declarations(css, "html")
    header = css_declarations(css, ".site-header")
    if header is None:
        return ["missing shared site-header rules"]
    if header.get("position", "").strip() not in {"fixed", "sticky"}:
        return []

    offset = pixel_value(root.get("scroll-padding-top")) if root is not None else None
    if offset is None or offset < MIN_STICKY_HEADER_OFFSET:
        errors.append(
            "wide-screen fragment scrolling must reserve at least "
            f"{MIN_STICKY_HEADER_OFFSET}px above targets"
        )

    # Below this breakpoint the navigation can wrap onto a second row. Keeping
    # that taller header sticky either needs a fragile second offset or can
    # obscure focus at large text sizes, so the shared layout deliberately
    # returns it to normal flow and removes the now-unneeded scroll gap.
    tablet = css_block(css, "@media (max-width: 920px)")
    if tablet is None:
        errors.append("missing the max-width: 920px wrapped-navigation rules")
        return errors
    tablet_root = css_declarations(tablet, "html")
    tablet_header = css_declarations(tablet, ".site-header")
    if tablet_root is None or pixel_value(tablet_root.get("scroll-padding-top")) != 0:
        errors.append("non-sticky wrapped navigation must reset scroll-padding-top to 0px")
    if tablet_header is None or tablet_header.get("position", "").strip() != "static":
        errors.append("wrapped navigation must use a static site header")
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


def skip_link_style_errors(css: str) -> list[str]:
    """Check explicit skip-link stylesheet conventions, not computed visibility."""
    resting = css_declarations(css, ".skip-link")
    focused = css_declarations(css, ".skip-link:focus")
    if resting is None:
        return ["missing .skip-link rules"]

    def value(declarations: dict[str, str], name: str) -> str:
        return re.sub(r"\s*!important\s*$", "", declarations.get(name, ""),
                      flags=re.I).strip().lower()

    for declarations in (resting, focused or {}):
        if value(declarations, "display") == "none":
            return ["skip link must not use display: none in its shared resting/focus rules"]
        if value(declarations, "visibility") in {"hidden", "collapse"}:
            return ["skip link must not use hidden visibility in its shared resting/focus rules"]
    # Always-visible links are valid. This site's transform-hidden variant
    # must explicitly reset on focus; we do not interpret arbitrary CSS.
    if value(resting, "transform") in {"", "none"}:
        return []
    if focused is None or value(focused, "transform") not in {"translatey(0)", "none"}:
        return ["transform-hidden skip link must reset with translateY(0) or none on focus"]
    return []


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
    for error in sticky_header_offset_errors(css):
        errors.append(f"{styles.name}: {error}")
    for error in focus_indicator_errors(css):
        errors.append(f"{styles.name}: {error}")
    for error in skip_link_style_errors(css):
        errors.append(f"{styles.name}: {error}")
    return errors


def run_self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        docs = Path(tmp)

        def primary_nav(
            path: Path, *, current_href: str | None = None, brand_current: bool = False
        ) -> str:
            relative = path.relative_to(docs)
            brand_href = "/presspeech/" if relative == ERROR_PAGE else "./"
            brand_state = " aria-current='page'" if brand_current else ""
            links = "".join(
                f"<a href='{href}'"
                f"{' aria-current=\"page\"' if href == current_href else ''}>"
                f"{label}</a>"
                for href, label in expected_primary_nav(path, docs)
            )
            return (
                "<nav aria-label='Primary'>"
                f"<a class='brand' href='{brand_href}'{brand_state}>"
                "<span class='brand-mark' aria-hidden='true'>P</span>"
                "<span>Presspeech</span></a>"
                "<button class='nav-toggle' type='button' aria-expanded='false' "
                "aria-controls='primary-navigation-links' data-navigation-toggle>Menu</button>"
                "<div class='nav-links' id='primary-navigation-links'>"
                f"{links}</div></nav>"
            )

        index = docs / "index.html"
        index.write_text(
            "<!doctype html><html lang='en'><head><title>Test</title>"
            "<script src='site-navigation.js' defer></script></head><body>"
            "<a class='skip-link' href='#main-content'>Skip to content</a>"
            + primary_nav(index, brand_current=True)
            + "<main id='main-content'><h1>Test</h1><img src='test.png' alt=''>"
            "<figure><video aria-describedby='video-description'></video>"
            "<figcaption id='video-description'>Silent demo description.</figcaption>"
            "</figure></main>"
            "</body></html>",
            encoding="utf-8",
        )
        if document_errors(index, docs):
            raise RuntimeError("self-test: valid document was rejected")
        valid_index = index.read_text(encoding="utf-8")
        cases = [
            (valid_index.replace("<body>", "<body><button>Before</button>"), "first element"),
            # A same-href anchor must not impersonate the actual skip-link element.
            (valid_index.replace("<body>", "<body><a href='#main-content'>Other</a>"), "first element"),
            (valid_index.replace("</main>", "</main><button tabindex='1'>Later</button>"), "positive tabindex"),
            (valid_index.replace("<body>", "<body hidden>"), "ancestors"),
            (valid_index.replace("<html lang='en'>", "<html lang='en' inert>"), "ancestors"),
            (valid_index.replace("class='skip-link'", "class='skip-link' tabindex='-1'"), "tabindex"),
            (valid_index.replace("class='skip-link'", "class='skip-link' tabindex='nonsense'"), "tabindex"),
            (valid_index.replace("class='skip-link'", "class='skip-link' hidden"), "shared styles"),
            (valid_index.replace("class='skip-link'", "class='skip-link' aria-hidden='true'"), "assistive technology"),
            (valid_index.replace("class='skip-link'", "class='skip-link' aria-label='Wrong'"), "accessible label"),
            (valid_index.replace("class='skip-link'", "class='skip-link' role='button'"), "link semantics"),
            (valid_index.replace("Skip to content", "Continue"), "consistent visible name"),
            (valid_index.replace("Skip to content", "<span hidden>Skip to content</span>"), "plain visible text"),
            (valid_index.replace(" aria-hidden='true'>P", ">P"), "decorative brand mark"),
            (valid_index.replace("<span>Presspeech</span>", "<span>Other</span>"), "brand link accessible name"),
            (valid_index.replace("class='brand'", "class='brand' aria-label='Home'"), "visible text"),
            (valid_index.replace("class='brand'", "class='brand' aria-hidden='true'"), "assistive technology"),
            (valid_index.replace("type='button'", "type='submit'"), "type='button'"),
            (valid_index.replace("aria-controls='primary-navigation-links'", "aria-controls='other'"), "must control"),
            (valid_index.replace("aria-expanded='false'", "aria-expanded='true'"), "must start"),
            (valid_index.replace(" data-navigation-toggle", ""), "script hook"),
            (valid_index.replace(">Menu</button>", ">Navigate</button>"), "visible 'Menu' text"),
            (valid_index.replace("id='primary-navigation-links'", "id='other-links'"), "target #primary-navigation-links"),
            (valid_index.replace("<script src='site-navigation.js' defer></script>", ""), "shared navigation script"),
            (
                valid_index.replace(" aria-describedby='video-description'", ""),
                "video must reference",
            ),
            (
                valid_index.replace(
                    "aria-describedby='video-description'",
                    "aria-describedby='missing-video-description'",
                ),
                "missing id",
            ),
            (
                valid_index.replace(
                    "id='video-description'", "id='video-description' hidden"
                ),
                "hidden id",
            ),
        ]
        for markup, expected_error in cases:
            index.write_text(markup, encoding="utf-8")
            if not any(expected_error in error for error in document_errors(index, docs)):
                raise RuntimeError(f"self-test: missing {expected_error!r} rejection")
        index.write_text(valid_index.replace("class='skip-link'", "class='skip-link' tabindex='0'"),
                         encoding="utf-8")
        if document_errors(index, docs):
            raise RuntimeError("self-test: zero-tabindex skip link was rejected")
        index.write_text(valid_index, encoding="utf-8")

        compatibility = docs / "app-compatibility.html"
        compatibility.write_text(
            "<!doctype html><html lang='en'><head><title>Compatibility</title>"
            "<script src='site-navigation.js' defer></script></head><body>"
            "<a class='skip-link' href='#main-content'>Skip to content</a>"
            + primary_nav(compatibility, current_href="troubleshooting.html")
            + "<main id='main-content'><h1>Compatibility</h1></main>"
            "</body></html>",
            encoding="utf-8",
        )
        if document_errors(compatibility, docs):
            raise RuntimeError("self-test: Help subsection navigation was rejected")

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
        if not any("shared order" in error for error in errors):
            raise RuntimeError("self-test: incomplete shared navigation was accepted")

        index.write_text(
            valid_index.replace(">Benchmarks</a>", ">Performance</a>"),
            encoding="utf-8",
        )
        errors = document_errors(index, docs)
        if not any("visible names" in error for error in errors):
            raise RuntimeError("self-test: inconsistent navigation name was accepted")

        error_page = docs / ERROR_PAGE
        error_page.write_text(
            "<!doctype html><html lang='en'><head><title>Missing</title>"
            "<script src='/presspeech/site-navigation.js' defer></script></head><body>"
            "<a class='skip-link' href='#main-content'>Skip to content</a>"
            + primary_nav(error_page)
            + "<main id='main-content'><h1>Not found</h1></main></body></html>",
            encoding="utf-8",
        )
        if document_errors(error_page, docs):
            raise RuntimeError("self-test: valid 404 page was rejected")

    if round(contrast_ratio("#000000", "#ffffff"), 2) != 21.0:
        raise RuntimeError("self-test: contrast calculation is incorrect")
    if contrast_ratio("#8a948e", "#fbfaf8") >= MIN_TEXT_CONTRAST:
        raise RuntimeError("self-test: low-contrast fixture was accepted")

    mobile_css = """
    .nav-links a {
      min-width: 24px;
      min-height: 36px;
    }
    @media (max-width: 720px) {
      .brand { min-height: 44px; }
      html.navigation-ready .nav-toggle {
        display: inline-flex;
        min-width: 44px;
        min-height: 44px;
      }
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
    undersized_desktop_css = mobile_css.replace("min-height: 36px;", "min-height: 23px;")
    errors = navigation_target_errors(undersized_desktop_css)
    if not any("at least a 24px min-height" in error for error in errors):
        raise RuntimeError("self-test: undersized desktop navigation target was accepted")
    undersized_css = mobile_css.replace("min-height: 44px;", "min-height: 23px;", 1)
    errors = navigation_target_errors(undersized_css)
    if not any("brand target" in error for error in errors):
        raise RuntimeError("self-test: undersized mobile brand target was accepted")
    missing_width_css = mobile_css.replace("min-width: 44px;", "")
    errors = navigation_target_errors(missing_width_css)
    if not any("44px min-width" in error for error in errors):
        raise RuntimeError("self-test: mobile link without a minimum width was accepted")

    sticky_header_css = """
    html { scroll-padding-top: 76px; }
    .site-header { position: sticky; top: 0; }
    @media (max-width: 920px) {
      html { scroll-padding-top: 0px; }
      .site-header { position: static; }
    }
    """
    if sticky_header_offset_errors(sticky_header_css):
        raise RuntimeError("self-test: valid sticky-header offsets were rejected")
    if sticky_header_offset_errors(".site-header { position: static; }"):
        raise RuntimeError("self-test: unobstructed static header was rejected")
    missing_offset_css = sticky_header_css.replace("scroll-padding-top: 76px;", "")
    errors = sticky_header_offset_errors(missing_offset_css)
    if not any("reserve at least" in error for error in errors):
        raise RuntimeError("self-test: missing sticky-header offset was accepted")
    wrapped_sticky_css = sticky_header_css.replace(
        ".site-header { position: static; }", ".site-header { position: sticky; }"
    )
    errors = sticky_header_offset_errors(wrapped_sticky_css)
    if not any("static site header" in error for error in errors):
        raise RuntimeError("self-test: sticky wrapped navigation was accepted")
    retained_offset_css = sticky_header_css.replace(
        "scroll-padding-top: 0px;", "scroll-padding-top: 76px;"
    )
    errors = sticky_header_offset_errors(retained_offset_css)
    if not any("reset scroll-padding-top" in error for error in errors):
        raise RuntimeError("self-test: stale non-sticky scroll offset was accepted")

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

    valid_skip_css = ".skip-link { transform: translateY(-100%); } .skip-link:focus { transform: translateY(0); }"
    if skip_link_style_errors(valid_skip_css):
        raise RuntimeError("self-test: valid shared skip-link styles were rejected")
    if skip_link_style_errors(".skip-link { color: blue; }"):
        raise RuntimeError("self-test: always-visible skip link was rejected")
    if skip_link_style_errors(valid_skip_css.replace("translateY(0)", "none")):
        raise RuntimeError("self-test: transform reset to none was rejected")
    for css in [".skip-link { display: none; }", ".skip-link { visibility: hidden; }",
                ".skip-link { display: none !important; }",
                ".skip-link { transform: translateY(-100%); }",
                valid_skip_css.replace("transform: translateY(0);", "transform: translateY(0); display: none;")]:
        if not skip_link_style_errors(css):
            raise RuntimeError("self-test: explicitly hidden skip-link style was accepted")


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
