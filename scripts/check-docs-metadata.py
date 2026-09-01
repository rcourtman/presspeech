#!/usr/bin/env python3
"""Validate public-site canonicals, sitemap entries, and release structured data."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SITE_ROOT = "https://rcourtman.github.io/presspeech/"
MAC_APP_ID = f"{SITE_ROOT}#software"
WINDOWS_APP_ID = f"{SITE_ROOT}windows.html#software"
SEMVER = re.compile(r"\d+\.\d+\.\d+")


class DocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.canonicals: list[str] = []
        self.structured_data: list[str] = []
        self._json_ld: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "link" and "canonical" in (attributes.get("rel") or "").split():
            href = attributes.get("href")
            if href is not None:
                self.canonicals.append(href)
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


def app_by_id(apps: list[dict[str, object]], app_id: str) -> dict[str, object] | None:
    matches = [app for app in apps if app.get("@id") == app_id]
    return matches[0] if len(matches) == 1 else None


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

    documents: dict[Path, tuple[DocumentParser, list[dict[str, object]]]] = {}
    canonical_paths: dict[str, Path] = {}
    for path in sorted(docs.rglob("*.html")):
        parser, nodes, parse_errors = document_metadata(path)
        display = path.relative_to(docs)
        errors.extend(f"{display}: {error}" for error in parse_errors)
        if len(parser.canonicals) != 1:
            errors.append(f"{display}: expected one canonical URL, found {parser.canonicals!r}")
        else:
            canonical = parser.canonicals[0]
            if not canonical.startswith(SITE_ROOT) or urlsplit(canonical).fragment:
                errors.append(f"{display}: invalid canonical URL {canonical!r}")
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

    index_path = docs / "index.html"
    windows_path = docs / "windows.html"
    index_apps = app_nodes(documents.get(index_path, (DocumentParser(), []))[1])
    windows_apps = app_nodes(documents.get(windows_path, (DocumentParser(), []))[1])
    if len(index_apps) != 2:
        errors.append(f"index.html: expected macOS and Windows app metadata, found {len(index_apps)} app(s)")
    if len(windows_apps) != 1:
        errors.append(f"windows.html: expected one app metadata object, found {len(windows_apps)}")

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
        for field in ("name", "operatingSystem", "applicationCategory", "downloadUrl", "installUrl"):
            if not isinstance(app.get(field), str) or not app[field]:
                errors.append(f"{display}: {app_id} is missing {field}")
        for field in ("downloadUrl", "installUrl"):
            value = app.get(field)
            if isinstance(value, str) and urlsplit(value).scheme != "https":
                errors.append(f"{display}: {app_id} has non-HTTPS {field} {value!r}")
        offer = app.get("offers")
        if not isinstance(offer, dict) or offer.get("price") != "0" or offer.get("priceCurrency") != "USD":
            errors.append(f"{display}: {app_id} must carry the free USD offer")

        if app_id == MAC_APP_ID:
            expected_urls = {
                "downloadUrl": "https://github.com/rcourtman/presspeech/releases/latest",
                "installUrl": f"{SITE_ROOT}install.html",
            }
        else:
            expected_urls = {
                "releaseNotes": (
                    "https://github.com/rcourtman/presspeech/releases/tag/"
                    f"windows-v{expected_version}"
                ),
                "downloadUrl": (
                    "https://github.com/rcourtman/presspeech/releases/download/"
                    f"windows-v{expected_version}/Presspeech-Setup-{expected_version}-x64.exe"
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

    with tempfile.TemporaryDirectory() as tmp:
        broken = Path(tmp) / "broken.html"
        broken.write_text(
            '<script type="application/ld+json">{"@type":</script>', encoding="utf-8"
        )
        _, _, errors = document_metadata(broken)
        if not errors:
            raise RuntimeError("self-test: malformed JSON-LD was accepted")


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
