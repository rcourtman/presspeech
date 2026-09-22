#!/usr/bin/env python3
"""Validate generated public visuals and social-preview metadata.

Raster images and videos cannot be inspected as text, so their manifests bind
each checked-in output to the exact generator sources from which it was last
rendered.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SOCIAL_MANIFEST = ROOT / "icon" / "social-preview-manifest.json"
DEMO_MANIFEST = ROOT / "marketing" / "demo" / "asset-manifest.json"
SOCIAL_IMAGE_URL = (
    "https://raw.githubusercontent.com/rcourtman/presspeech/"
    "main/icon/social-preview.png"
)
SOCIAL_IMAGE_ALT = (
    "Presspeech microphone logo: private local dictation for Mac and Windows; "
    "macOS release and Windows prerelease."
)
SOCIAL_SOURCES = ("icon/social-preview.svg", "icon/make-icons.sh")
SOCIAL_OUTPUTS = ("icon/social-preview.png",)
DEMO_SOURCES = (
    "marketing/demo/index.html",
    "marketing/demo/package.json",
    "marketing/demo/render.mjs",
)
DEMO_OUTPUTS = (
    "docs/demo-poster.jpg",
    "docs/demo-video.mp4",
    "docs/demo-video.webm",
    "marketing/demo/dist/presspeech-demo.gif",
    "marketing/demo/dist/presspeech-demo.mp4",
    "marketing/demo/dist/presspeech-demo.webm",
)
SHA256 = re.compile(r"[0-9a-f]{64}")


class MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.properties: dict[str, list[str]] = {}
        self.names: dict[str, list[str]] = {}
        self.canonicals: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "meta":
            content = attributes.get("content")
            if content is None:
                return
            if attributes.get("property"):
                self.properties.setdefault(attributes["property"], []).append(content)
            if attributes.get("name"):
                self.names.setdefault(attributes["name"].lower(), []).append(content)
        if tag == "link" and "canonical" in (attributes.get("rel") or "").split():
            if attributes.get("href"):
                self.canonicals.append(attributes["href"])


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def file_record(path: Path) -> dict[str, object]:
    return {"bytes": path.stat().st_size, "sha256": digest(path)}


def write_manifest(path: Path, sources: tuple[str, ...], outputs: tuple[str, ...]) -> None:
    missing = [name for name in (*sources, *outputs) if not (ROOT / name).is_file()]
    if missing:
        raise RuntimeError(f"cannot update {path.relative_to(ROOT)}; missing {', '.join(missing)}")
    value = {
        "outputs": {name: file_record(ROOT / name) for name in outputs},
        "schema": 1,
        "sources": {name: digest(ROOT / name) for name in sources},
    }
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"updated {path.relative_to(ROOT)}")


def manifest_errors(
    path: Path,
    expected_sources: tuple[str, ...],
    expected_outputs: tuple[str, ...],
    *,
    root: Path = ROOT,
) -> list[str]:
    display = path.relative_to(root) if path.is_relative_to(root) else path.name
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{display}: cannot read generated-asset manifest: {exc}"]
    if not isinstance(value, dict) or value.get("schema") != 1:
        return [f"{display}: expected generated-asset manifest schema 1"]

    errors: list[str] = []
    sources = value.get("sources")
    outputs = value.get("outputs")
    if not isinstance(sources, dict) or set(sources) != set(expected_sources):
        errors.append(f"{display}: source list does not match the generator contract")
        sources = {}
    if not isinstance(outputs, dict) or set(outputs) != set(expected_outputs):
        errors.append(f"{display}: output list does not match the public-asset contract")
        outputs = {}

    for name in expected_sources:
        asset = root / name
        expected = sources.get(name)
        if not asset.is_file():
            errors.append(f"{name}: source is missing")
        elif not isinstance(expected, str) or not SHA256.fullmatch(expected):
            errors.append(f"{display}: invalid source digest for {name}")
        elif digest(asset) != expected:
            errors.append(
                f"{name}: generated outputs are stale; rerender and update {display}"
            )

    for name in expected_outputs:
        asset = root / name
        record = outputs.get(name)
        if not asset.is_file():
            errors.append(f"{name}: generated output is missing")
            continue
        if not isinstance(record, dict):
            errors.append(f"{display}: invalid output record for {name}")
            continue
        expected_digest = record.get("sha256")
        expected_bytes = record.get("bytes")
        if not isinstance(expected_digest, str) or not SHA256.fullmatch(expected_digest):
            errors.append(f"{display}: invalid output digest for {name}")
        elif digest(asset) != expected_digest:
            errors.append(f"{name}: bytes differ from {display}")
        if not isinstance(expected_bytes, int) or expected_bytes <= 0:
            errors.append(f"{display}: invalid output size for {name}")
        elif asset.stat().st_size != expected_bytes:
            errors.append(f"{name}: size differs from {display}")
    return errors


def png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError("not a PNG with an IHDR header")
    return struct.unpack(">II", header[16:24])


def gif_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:10]
    if len(header) != 10 or header[:6] not in {b"GIF87a", b"GIF89a"}:
        raise ValueError("not a GIF")
    return struct.unpack("<HH", header[6:10])


def jpeg_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("not a JPEG")
    offset = 2
    while offset + 9 <= len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        marker = data[offset + 1]
        offset += 2
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(data):
            break
        length = struct.unpack(">H", data[offset : offset + 2])[0]
        if length < 2 or offset + length > len(data):
            break
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            height, width = struct.unpack(">HH", data[offset + 3 : offset + 7])
            return width, height
        offset += length
    raise ValueError("JPEG has no supported start-of-frame marker")


def media_errors(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    dimensions = {
        "icon/social-preview.png": (png_size, (1280, 640)),
        "docs/demo-poster.jpg": (jpeg_size, (1920, 1080)),
        "marketing/demo/dist/presspeech-demo.gif": (gif_size, (1080, 608)),
    }
    for name, (reader, expected) in dimensions.items():
        try:
            actual = reader(root / name)
            if actual != expected:
                errors.append(f"{name}: dimensions are {actual[0]}x{actual[1]}; expected {expected[0]}x{expected[1]}")
        except (OSError, ValueError, struct.error) as exc:
            errors.append(f"{name}: invalid public image: {exc}")

    preview = root / "icon/social-preview.png"
    if preview.is_file() and preview.stat().st_size >= 1024 * 1024:
        errors.append("icon/social-preview.png: social preview must remain below 1 MiB")

    magic = {
        "docs/demo-video.mp4": (4, b"ftyp"),
        "marketing/demo/dist/presspeech-demo.mp4": (4, b"ftyp"),
        "docs/demo-video.webm": (0, b"\x1a\x45\xdf\xa3"),
        "marketing/demo/dist/presspeech-demo.webm": (0, b"\x1a\x45\xdf\xa3"),
    }
    for name, (offset, expected) in magic.items():
        try:
            if (root / name).read_bytes()[offset : offset + len(expected)] != expected:
                errors.append(f"{name}: media signature is invalid")
        except OSError as exc:
            errors.append(f"{name}: cannot read media output: {exc}")

    for suffix in ("mp4", "webm"):
        docs_copy = root / "docs" / f"demo-video.{suffix}"
        dist_copy = root / "marketing" / "demo" / "dist" / f"presspeech-demo.{suffix}"
        if docs_copy.is_file() and dist_copy.is_file() and digest(docs_copy) != digest(dist_copy):
            errors.append(f"docs/demo-video.{suffix}: differs from the rendered distribution copy")
    return errors


def one(values: dict[str, list[str]], name: str) -> str | None:
    matches = values.get(name, [])
    return matches[0] if len(matches) == 1 else None


def social_metadata_errors(docs: Path = DOCS) -> list[str]:
    errors: list[str] = []
    required_properties = {
        "og:type": "website",
        "og:image": SOCIAL_IMAGE_URL,
        "og:image:type": "image/png",
        "og:image:width": "1280",
        "og:image:height": "640",
        "og:image:alt": SOCIAL_IMAGE_ALT,
    }
    required_names = {
        "twitter:card": "summary_large_image",
        "twitter:image": SOCIAL_IMAGE_URL,
        "twitter:image:alt": SOCIAL_IMAGE_ALT,
    }
    for path in sorted(docs.rglob("*.html")):
        if path.name == "404.html":
            continue
        parser = MetadataParser()
        parser.feed(path.read_text(encoding="utf-8"))
        parser.close()
        display = path.relative_to(docs)
        for name in ("og:title", "og:description"):
            value = one(parser.properties, name)
            if value is None or not value.strip():
                errors.append(f"{display}: expected one non-empty {name}")
        canonical = parser.canonicals[0] if len(parser.canonicals) == 1 else None
        if one(parser.properties, "og:url") != canonical:
            errors.append(f"{display}: og:url must equal its one canonical URL")
        for name, expected in required_properties.items():
            values = parser.properties.get(name, [])
            if values != [expected]:
                errors.append(f"{display}: expected one {name}={expected!r}, found {values!r}")
        for name, expected in required_names.items():
            values = parser.names.get(name, [])
            if values != [expected]:
                errors.append(f"{display}: expected one {name}={expected!r}, found {values!r}")
    return errors


def all_errors() -> list[str]:
    return [
        *manifest_errors(SOCIAL_MANIFEST, SOCIAL_SOURCES, SOCIAL_OUTPUTS),
        *manifest_errors(DEMO_MANIFEST, DEMO_SOURCES, DEMO_OUTPUTS),
        *media_errors(),
        *social_metadata_errors(),
    ]


def minimal_png(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I4sII", 13, b"IHDR", width, height)


def run_self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "source.svg").write_text("<svg/>\n", encoding="utf-8")
        (root / "output.png").write_bytes(minimal_png(1280, 640))
        manifest = root / "manifest.json"
        value = {
            "outputs": {"output.png": file_record(root / "output.png")},
            "schema": 1,
            "sources": {"source.svg": digest(root / "source.svg")},
        }
        manifest.write_text(json.dumps(value), encoding="utf-8")
        if manifest_errors(manifest, ("source.svg",), ("output.png",), root=root):
            raise RuntimeError("self-test: current generated asset was rejected")
        (root / "source.svg").write_text("<svg><rect/></svg>\n", encoding="utf-8")
        if not any("stale" in error for error in manifest_errors(
            manifest, ("source.svg",), ("output.png",), root=root
        )):
            raise RuntimeError("self-test: changed generator source did not make output stale")
        if png_size(root / "output.png") != (1280, 640):
            raise RuntimeError("self-test: PNG dimensions were not parsed")

        docs = root / "docs"
        docs.mkdir()
        complete = (
            '<link rel="canonical" href="https://example.test/">'
            '<meta property="og:title" content="Title">'
            '<meta property="og:description" content="Description">'
            '<meta property="og:url" content="https://example.test/">'
            + "".join(
                f'<meta property="{name}" content="{value}">'
                for name, value in {
                    "og:type": "website",
                    "og:image": SOCIAL_IMAGE_URL,
                    "og:image:type": "image/png",
                    "og:image:width": "1280",
                    "og:image:height": "640",
                    "og:image:alt": SOCIAL_IMAGE_ALT,
                }.items()
            )
            + "".join(
                f'<meta name="{name}" content="{value}">'
                for name, value in {
                    "twitter:card": "summary_large_image",
                    "twitter:image": SOCIAL_IMAGE_URL,
                    "twitter:image:alt": SOCIAL_IMAGE_ALT,
                }.items()
            )
        )
        page = docs / "index.html"
        page.write_text(complete, encoding="utf-8")
        if social_metadata_errors(docs):
            raise RuntimeError("self-test: complete social metadata was rejected")
        page.write_text(complete.replace("og:image:alt", "og:image:caption"), encoding="utf-8")
        if not any("og:image:alt" in error for error in social_metadata_errors(docs)):
            raise RuntimeError("self-test: missing social image alt was accepted")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--update-social-preview", action="store_true")
    parser.add_argument("--update-demo", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            run_self_test()
            print("public asset self-test passed")
            return 0
        if args.update_social_preview:
            write_manifest(SOCIAL_MANIFEST, SOCIAL_SOURCES, SOCIAL_OUTPUTS)
        if args.update_demo:
            write_manifest(DEMO_MANIFEST, DEMO_SOURCES, DEMO_OUTPUTS)
        if args.update_social_preview or args.update_demo:
            return 0
        errors = all_errors()
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        print("public generated assets and social metadata are current")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"check-public-assets: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
