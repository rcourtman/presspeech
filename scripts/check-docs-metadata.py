#!/usr/bin/env python3
"""Validate public-site discovery metadata and release structured data."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import date
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SITE_ROOT = "https://rcourtman.github.io/presspeech/"
MAC_APP_ID = f"{SITE_ROOT}#software"
WINDOWS_APP_ID = f"{SITE_ROOT}windows.html#software"
WEBSITE_ID = f"{SITE_ROOT}#website"
HOME_PAGE_ID = f"{SITE_ROOT}#webpage"
WINDOWS_PAGE_ID = f"{SITE_ROOT}windows.html#webpage"
SITEMAP_URL = f"{SITE_ROOT}sitemap.xml"
SEMVER = re.compile(r"\d+\.\d+\.\d+")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
ERROR_PAGE = Path("404.html")
ERROR_PAGE_URL = f"{SITE_ROOT}404.html"


class DocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.canonicals: list[str] = []
        self.robots: list[str] = []
        self.previews: dict[str, list[str]] = {"description": [], "og:description": []}
        self.structured_data: list[str] = []
        self._json_ld: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "link" and "canonical" in (attributes.get("rel") or "").split():
            href = attributes.get("href")
            if href is not None:
                self.canonicals.append(href)
        if tag == "meta" and (attributes.get("name") or "").lower() == "robots":
            self.robots.append((attributes.get("content") or "").lower())
        if tag == "meta":
            name = (attributes.get("name") or "").lower()
            prop = (attributes.get("property") or "").lower()
            if name == "description":
                self.previews["description"].append(attributes.get("content") or "")
            if prop == "og:description":
                self.previews["og:description"].append(attributes.get("content") or "")
        if tag == "script" and attributes.get("type") == "application/ld+json":
            self._json_ld = []

    def handle_data(self, data: str) -> None:
        if self._json_ld is not None:
            self._json_ld.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._json_ld is not None:
            self.structured_data.append("".join(self._json_ld))
            self._json_ld = None


def parse_document(path: Path) -> DocumentParser:
    parser = DocumentParser()
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()
    return parser


def graph_nodes(value: object) -> list[dict[str, object]]:
    if not isinstance(value, dict):
        return []
    graph = value.get("@graph")
    if isinstance(graph, list):
        return [node for node in graph if isinstance(node, dict)]
    return [value]


def document_metadata(path: Path) -> tuple[DocumentParser, list[dict[str, object]], list[str]]:
    parser = parse_document(path)
    nodes: list[dict[str, object]] = []
    errors: list[str] = []
    for number, raw_value in enumerate(parser.structured_data, start=1):
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            errors.append(f"JSON-LD block {number} is invalid: {exc.msg}")
            continue
        nodes.extend(graph_nodes(value))
    return parser, nodes, errors


def app_nodes(nodes: list[dict[str, object]]) -> list[dict[str, object]]:
    return [node for node in nodes if node.get("@type") == "SoftwareApplication"]


def website_nodes(nodes: list[dict[str, object]]) -> list[dict[str, object]]:
    return [node for node in nodes if node.get("@type") == "WebSite"]


def webpage_nodes(nodes: list[dict[str, object]]) -> list[dict[str, object]]:
    return [node for node in nodes if node.get("@type") == "WebPage"]


def app_by_id(apps: list[dict[str, object]], app_id: str) -> dict[str, object] | None:
    matches = [app for app in apps if app.get("@id") == app_id]
    return matches[0] if len(matches) == 1 else None


def has_delivery_boundary(description: object) -> bool:
    if not isinstance(description, str):
        return False
    normalized = " ".join(description.lower().split())
    return all(
        phrase in normalized
        for phrase in (
            "original destination window", "field or browser tab",
            "same window", "clipboard", "manual paste",
        )
    )


def preview_decision_errors(
    page: str, parser: DocumentParser, required: tuple[str, ...]
) -> list[str]:
    """Keep short search/social snippets from bypassing first-launch decisions."""
    errors: list[str] = []
    for field in ("description", "og:description"):
        values = parser.previews[field]
        if len(values) != 1:
            errors.append(f"{page}: expected one {field} preview, found {len(values)}")
            continue
        normalized = " ".join(values[0].lower().split())
        missing = [phrase for phrase in required if phrase not in normalized]
        if missing:
            errors.append(
                f"{page}: {field} preview omits first-launch decision: "
                + ", ".join(missing)
            )
    return errors


def robots_directives(parser: DocumentParser) -> set[str]:
    return {
        directive.strip()
        for value in parser.robots
        for directive in value.split(",")
    }


def expected_canonical(path: Path, docs: Path) -> str:
    relative = path.relative_to(docs)
    if relative == Path("index.html"):
        return SITE_ROOT
    if relative.name == "index.html":
        return f"{SITE_ROOT}{relative.parent.as_posix()}/"
    return f"{SITE_ROOT}{relative.as_posix()}"


def public_path_for_url(url: str, docs: Path) -> Path | None:
    parsed = urlsplit(url)
    site = urlsplit(SITE_ROOT)
    if (
        parsed.scheme != site.scheme
        or parsed.netloc != site.netloc
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith(site.path)
    ):
        return None
    relative = unquote(parsed.path[len(site.path) :])
    public_path = PurePosixPath(relative)
    if public_path.is_absolute() or ".." in public_path.parts:
        return None
    if not relative or relative.endswith("/"):
        public_path /= "index.html"
    return docs.joinpath(*public_path.parts)


def robots_errors(docs: Path) -> list[str]:
    path = docs / "robots.txt"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [f"robots.txt: cannot read crawl policy: {exc}"]

    errors: list[str] = []
    fields: list[tuple[str, str]] = []
    for number, raw_line in enumerate(lines, start=1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" not in line:
            errors.append(f"robots.txt:{number}: expected a field and value")
            continue
        name, value = line.split(":", 1)
        fields.append((name.strip().lower(), value.strip()))

    sitemaps = [value for name, value in fields if name == "sitemap"]
    if sitemaps != [SITEMAP_URL]:
        errors.append(
            f"robots.txt: expected one Sitemap entry for {SITEMAP_URL}, found {sitemaps!r}"
        )

    wildcard_seen = False
    current_agents: list[str] = []
    group_has_directive = False
    for name, value in fields:
        if name == "user-agent":
            if group_has_directive:
                current_agents = []
                group_has_directive = False
            current_agents.append(value.lower())
            if value == "*":
                wildcard_seen = True
        elif name in {"allow", "disallow"}:
            group_has_directive = True
            if name == "disallow" and value == "/" and "*" in current_agents:
                errors.append("robots.txt: wildcard crawl policy must not disallow the site root")
        elif name != "sitemap":
            group_has_directive = True
    if not wildcard_seen:
        errors.append("robots.txt: missing wildcard User-agent policy")
    return errors


def metadata_errors(docs: Path = DOCS, today: date | None = None) -> list[str]:
    today = today or date.today()
    errors: list[str] = []
    metadata_path = docs / "site-metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"site-metadata.json: cannot read metadata: {exc}"]

    versions = {
        MAC_APP_ID: metadata.get("version"),
        WINDOWS_APP_ID: metadata.get("windows_version"),
    }
    for app_id, version in versions.items():
        if not isinstance(version, str) or not SEMVER.fullmatch(version):
            errors.append(f"site-metadata.json: invalid version for {app_id}: {version!r}")
    last_updated = metadata.get("last_updated")
    try:
        if not isinstance(last_updated, str) or not ISO_DATE.fullmatch(last_updated):
            raise ValueError
        if date.fromisoformat(last_updated) > today:
            errors.append(
                f"site-metadata.json: last_updated {last_updated!r} is in the future"
            )
    except ValueError:
        errors.append(
            f"site-metadata.json: invalid last_updated date {last_updated!r}"
        )

    errors.extend(robots_errors(docs))

    documents: dict[Path, tuple[DocumentParser, list[dict[str, object]]]] = {}
    canonical_paths: dict[str, Path] = {}
    for path in sorted(docs.rglob("*.html")):
        parser, nodes, parse_errors = document_metadata(path)
        display = path.relative_to(docs)
        errors.extend(f"{display}: {error}" for error in parse_errors)
        if display == ERROR_PAGE:
            if parser.canonicals:
                errors.append(f"{display}: error page must not declare a canonical URL")
            directives = robots_directives(parser)
            if "noindex" not in directives:
                errors.append(f"{display}: error page must declare robots noindex")
            documents[path] = (parser, nodes)
            continue
        blocked = robots_directives(parser).intersection({"noindex", "none"})
        if blocked:
            errors.append(
                f"{display}: public canonical page must remain indexable, found {sorted(blocked)!r}"
            )
        if len(parser.canonicals) != 1:
            errors.append(f"{display}: expected one canonical URL, found {parser.canonicals!r}")
        else:
            canonical = parser.canonicals[0]
            expected_url = expected_canonical(path, docs)
            if canonical != expected_url:
                errors.append(
                    f"{display}: canonical URL is {canonical!r}; expected {expected_url!r}"
                )
            elif canonical in canonical_paths:
                errors.append(
                    f"{display}: canonical URL duplicates {canonical_paths[canonical].relative_to(docs)}"
                )
            else:
                canonical_paths[canonical] = path
        documents[path] = (parser, nodes)

    try:
        sitemap_root = ET.parse(docs / "sitemap.xml").getroot()
        namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        sitemap_urls: dict[str, ET.Element] = {}
        for url in sitemap_root.findall("s:url", namespace):
            loc = url.findtext("s:loc", namespaces=namespace)
            if not loc:
                errors.append("sitemap.xml: URL entry has no loc")
                continue
            if loc in sitemap_urls:
                errors.append(f"sitemap.xml: duplicate URL {loc}")
            sitemap_urls[loc] = url
            public_path = public_path_for_url(loc, docs)
            if public_path is None:
                errors.append(f"sitemap.xml: URL is outside the canonical site: {loc}")
            elif not public_path.is_file():
                errors.append(
                    f"sitemap.xml: URL has no published file: {loc} -> "
                    f"{public_path.relative_to(docs)}"
                )
            lastmod = url.findtext("s:lastmod", namespaces=namespace)
            try:
                modified = date.fromisoformat(lastmod or "")
                if modified > today:
                    errors.append(f"sitemap.xml: {loc} has future lastmod {lastmod}")
            except ValueError:
                errors.append(f"sitemap.xml: {loc} has invalid lastmod {lastmod!r}")
    except (OSError, ET.ParseError) as exc:
        errors.append(f"sitemap.xml: cannot read sitemap: {exc}")
        sitemap_urls = {}

    for canonical in canonical_paths:
        if canonical not in sitemap_urls:
            errors.append(f"sitemap.xml: missing HTML canonical {canonical}")
    if ERROR_PAGE_URL in sitemap_urls:
        errors.append(f"sitemap.xml: error page must not be listed as {ERROR_PAGE_URL}")

    index_path = docs / "index.html"
    windows_path = docs / "windows.html"
    preview_requirements = {
        "index.html": ("model", "launch", "privacy decision", "before opening"),
        "install.html": (
            f"macos {versions[MAC_APP_ID]}", "before opening", "model",
            "launch", "privacy decision", "inherited", "token",
        ),
        "windows.html": (
            f"windows {versions[WINDOWS_APP_ID]}", "before opening", "model",
            "launch", "privacy decision", "telemetry", "token",
        ),
    }
    for page, required in preview_requirements.items():
        parser = documents.get(docs / page, (DocumentParser(), []))[0]
        errors.extend(preview_decision_errors(page, parser, required))
    index_apps = app_nodes(documents.get(index_path, (DocumentParser(), []))[1])
    windows_apps = app_nodes(documents.get(windows_path, (DocumentParser(), []))[1])
    index_pages = webpage_nodes(documents.get(index_path, (DocumentParser(), []))[1])
    windows_pages = webpage_nodes(documents.get(windows_path, (DocumentParser(), []))[1])
    index_websites = website_nodes(
        documents.get(index_path, (DocumentParser(), []))[1]
    )
    expected_website = {
        "@type": "WebSite",
        "@id": WEBSITE_ID,
        "url": SITE_ROOT,
        "name": "Presspeech",
    }
    if len(index_websites) != 1:
        errors.append(
            f"index.html: expected one WebSite identity, found {len(index_websites)}"
        )
    else:
        website = index_websites[0]
        for field, expected_value in expected_website.items():
            if website.get(field) != expected_value:
                errors.append(
                    f"index.html: WebSite {field} is {website.get(field)!r}; "
                    f"expected {expected_value!r}"
                )
        if not isinstance(website.get("description"), str) or not website["description"]:
            errors.append("index.html: WebSite identity is missing description")
    if len(index_apps) != 2:
        errors.append(f"index.html: expected macOS and Windows app metadata, found {len(index_apps)} app(s)")
    if len(windows_apps) != 1:
        errors.append(f"windows.html: expected one app metadata object, found {len(windows_apps)}")

    expected_pages = [
        ("index.html", index_pages, HOME_PAGE_ID, SITE_ROOT,
         [MAC_APP_ID, WINDOWS_APP_ID]),
        ("windows.html", windows_pages, WINDOWS_PAGE_ID,
         f"{SITE_ROOT}windows.html", WINDOWS_APP_ID),
    ]
    for display, pages, page_id, page_url, main_entity in expected_pages:
        if len(pages) != 1:
            errors.append(
                f"{display}: expected one WebPage metadata object, found {len(pages)}"
            )
        matches = [page for page in pages if page.get("@id") == page_id]
        if len(matches) != 1:
            errors.append(
                f"{display}: expected exactly one WebPage with @id {page_id}, "
                f"found {len(matches)}"
            )
            continue
        page = matches[0]
        if page.get("url") != page_url:
            errors.append(
                f"{display}: {page_id} URL {page.get('url')!r} does not match {page_url!r}"
            )
        if page.get("mainEntity") != main_entity:
            errors.append(
                f"{display}: {page_id} mainEntity {page.get('mainEntity')!r} "
                f"does not identify {main_entity!r}"
            )
        if page.get("dateModified") != last_updated:
            errors.append(
                f"{display}: {page_id} dateModified {page.get('dateModified')!r} "
                f"does not match site metadata {last_updated!r}"
            )

    expected_apps: list[tuple[str, dict[str, object] | None, str]] = [
        ("index.html", app_by_id(index_apps, MAC_APP_ID), MAC_APP_ID),
        ("index.html", app_by_id(index_apps, WINDOWS_APP_ID), WINDOWS_APP_ID),
        ("windows.html", app_by_id(windows_apps, WINDOWS_APP_ID), WINDOWS_APP_ID),
    ]
    for display, app, app_id in expected_apps:
        if app is None:
            errors.append(f"{display}: expected exactly one app with @id {app_id}")
            continue
        expected_version = versions[app_id]
        if app.get("softwareVersion") != expected_version:
            errors.append(
                f"{display}: {app_id} version {app.get('softwareVersion')!r} "
                f"does not match site metadata {expected_version!r}"
            )
        for field in (
            "name",
            "description",
            "operatingSystem",
            "applicationCategory",
            "installUrl",
        ):
            if not isinstance(app.get(field), str) or not app[field]:
                errors.append(f"{display}: {app_id} is missing {field}")
        if not has_delivery_boundary(app.get("description")):
            errors.append(
                f"{display}: {app_id} description must state the window-level delivery limit "
                "and manual clipboard-paste boundary"
            )
        if "downloadUrl" in app:
            errors.append(
                f"{display}: {app_id} must route discovery through installUrl, "
                "not advertise a downloadUrl that bypasses the release preflight"
            )
        install_url = app.get("installUrl")
        if isinstance(install_url, str) and urlsplit(install_url).scheme != "https":
            errors.append(f"{display}: {app_id} has non-HTTPS installUrl {install_url!r}")
        offer = app.get("offers")
        if not isinstance(offer, dict) or offer.get("price") != "0" or offer.get("priceCurrency") != "USD":
            errors.append(f"{display}: {app_id} must carry the free USD offer")

        if app_id == MAC_APP_ID:
            expected_urls = {
                "installUrl": f"{SITE_ROOT}install.html",
            }
        else:
            expected_urls = {
                "releaseNotes": (
                    "https://github.com/rcourtman/presspeech/releases/tag/"
                    f"windows-v{expected_version}"
                ),
                "installUrl": f"{SITE_ROOT}windows.html",
            }
        for field, expected_url in expected_urls.items():
            if app.get(field) != expected_url:
                errors.append(
                    f"{display}: {app_id} has stale {field} {app.get(field)!r}; "
                    f"expected {expected_url!r}"
                )

    index_windows = app_by_id(index_apps, WINDOWS_APP_ID)
    page_windows = app_by_id(windows_apps, WINDOWS_APP_ID)
    if index_windows is not None and page_windows is not None:
        # A standalone JSON-LD object carries its own context; a node inside
        # the homepage graph inherits the graph's context.
        normalized_page_windows = {
            key: value for key, value in page_windows.items() if key != "@context"
        }
        if index_windows != normalized_page_windows:
            errors.append("index.html and windows.html disagree on Windows structured data")
    return errors


def run_self_test() -> None:
    parser = DocumentParser()
    parser.feed(
        '<link rel="canonical" href="https://example.com/">'
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@graph":['
        '{"@type":"SoftwareApplication","@id":"#one"},'
        '{"@type":"SoftwareApplication","@id":"#two"}]}'
        "</script>"
    )
    parser.close()
    nodes = graph_nodes(json.loads(parser.structured_data[0]))
    if parser.canonicals != ["https://example.com/"] or len(app_nodes(nodes)) != 2:
        raise RuntimeError("self-test: canonical or JSON-LD graph was not parsed")

    parser = DocumentParser()
    parser.feed('<meta name="robots" content="noindex, follow">')
    parser.close()
    if parser.robots != ["noindex, follow"]:
        raise RuntimeError("self-test: robots metadata was not parsed")

    with tempfile.TemporaryDirectory() as tmp:
        docs = Path(tmp)
        (docs / "robots.txt").write_text(
            f"User-agent: *\nAllow: /\n\nSitemap: {SITEMAP_URL}\n",
            encoding="utf-8",
        )
        if robots_errors(docs):
            raise RuntimeError("self-test: valid robots.txt was rejected")
        (docs / "robots.txt").write_text(
            "User-agent: *\nAllow: /public\nDisallow: /\n", encoding="utf-8"
        )
        crawl_errors = robots_errors(docs)
        if not any("disallow" in error for error in crawl_errors):
            raise RuntimeError("self-test: root crawl block was accepted")
        if not any("Sitemap" in error for error in crawl_errors):
            raise RuntimeError("self-test: missing sitemap discovery was accepted")

    if expected_canonical(DOCS / "compare" / "index.html", DOCS) != f"{SITE_ROOT}compare/":
        raise RuntimeError("self-test: directory canonical was not normalized")
    if public_path_for_url(f"{SITE_ROOT}../private", DOCS) is not None:
        raise RuntimeError("self-test: unsafe sitemap URL was accepted")

    if not has_delivery_boundary(
        "Checks the original destination window, but a field or browser tab "
        "change in the same window may be missed; otherwise the clipboard "
        "holds the text for manual paste."
    ):
        raise RuntimeError("self-test: valid delivery boundary was rejected")
    if has_delivery_boundary("Private dictation into any Mac app."):
        raise RuntimeError("self-test: universal delivery description was accepted")
    if has_delivery_boundary(
        "Pastes after verifying the original destination; otherwise the clipboard "
        "holds the text for manual paste."
    ):
        raise RuntimeError("self-test: missing same-window limit was accepted")

    preview_parser = DocumentParser()
    preview_parser.feed(
        '<meta name="description" content="Before opening macOS 0.3.8, read the '
        'model-download privacy decision: a missing model downloads on launch '
        'and may send an inherited token.">'
        '<meta property="og:description" content="Open and start dictating.">'
    )
    if not any(
        "og:description preview omits first-launch decision" in error
        for error in preview_decision_errors(
            "install.html", preview_parser,
            ("macos 0.3.8", "before opening", "model", "launch",
             "privacy decision", "inherited", "token"),
        )
    ):
        raise RuntimeError("self-test: unsafe social install preview was accepted")

    with tempfile.TemporaryDirectory() as tmp:
        broken = Path(tmp) / "broken.html"
        broken.write_text(
            '<script type="application/ld+json">{"@type":</script>', encoding="utf-8"
        )
        _, _, errors = document_metadata(broken)
        if not errors:
            raise RuntimeError("self-test: malformed JSON-LD was accepted")

    with tempfile.TemporaryDirectory() as tmp:
        docs = Path(tmp) / "docs"
        shutil.copytree(DOCS, docs)
        windows_page = docs / "windows.html"
        windows_page.write_text(
            windows_page.read_text(encoding="utf-8").replace(
                '"installUrl": "https://rcourtman.github.io/presspeech/windows.html",',
                '"downloadUrl": "https://example.com/unsigned.exe",\n'
                '            "installUrl": "https://rcourtman.github.io/presspeech/windows.html",',
                1,
            ), encoding="utf-8",
        )
        if not any(
            "must route discovery through installUrl" in error
            for error in metadata_errors(docs, today=date.fromisoformat("2026-09-24"))
        ):
            raise RuntimeError("self-test: direct structured download was accepted")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="run isolated parser checks")
    args = parser.parse_args()
    try:
        if args.self_test:
            run_self_test()
            print("docs metadata self-test passed")
            return 0
        errors = metadata_errors()
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        print("docs metadata is valid")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"check-docs-metadata: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
