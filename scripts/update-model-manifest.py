#!/usr/bin/env python3
"""Regenerate Presspeech's pinned speech-model SHA-256 manifests.

The v3 Parakeet CoreML repository contains a mix of LFS-backed files
and small Git blobs. Hugging Face exposes SHA-256 directly for the LFS
objects via X-Linked-ETag; small Git blobs expose only Git object IDs,
so this script downloads those small files and hashes the bytes.

With ``--windows``, the same process covers every explicit inference input for
all reviewed Windows model revisions. LFS/Xet object identities come from the
exact-revision tree response; remaining small files are downloaded and hashed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import runpy
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "swift" / "Sources" / "Presspeech" / "main.swift"
WINDOWS_SOURCE = ROOT / "windows" / "model_manifest.py"

DEFAULT_REPO = "FluidInference/parakeet-tdt-0.6b-v3-coreml"
# The pinned revision is NOT duplicated here: main.swift's
# parakeetV3RepositoryCommit is the single source of truth, parsed at
# runtime when --revision is not passed (see revision_from_source).
DEFAULT_BUNDLES = [
    "Decoder.mlmodelc",
    "Encoder.mlmodelc",
    "JointDecisionv3.mlmodelc",
    "Preprocessor.mlmodelc",
]
DEFAULT_EXTRA_FILES = ["parakeet_vocab.json"]

BEGIN_MARKER = "// BEGIN GENERATED PARAKEET_V3_MODEL_MANIFEST"
END_MARKER = "// END GENERATED PARAKEET_V3_MODEL_MANIFEST"
WINDOWS_BEGIN_MARKER = "# BEGIN GENERATED WINDOWS_MODEL_MANIFEST"
WINDOWS_END_MARKER = "# END GENERATED WINDOWS_MODEL_MANIFEST"
HEX64 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
HEX40 = re.compile(r"^[0-9a-f]{40}$")
SAFE_MODEL_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
SAFE_MODEL_PATH = re.compile(r"^[A-Za-z0-9._/-]+$")
SAFE_REPO = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
SAFE_REVISION = re.compile(r"^[A-Za-z0-9._/-]+$")
SWIFT_REPO_RE = re.compile(r'(static let parakeetV3Repository = ")([^"]+)(")')
SWIFT_REVISION_RE = re.compile(r'(static let parakeetV3RepositoryCommit = ")([^"]+)(")')


class ManifestError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


NO_REDIRECT_OPENER = urllib.request.build_opener(NoRedirect)

LINK_HEADER_RE = re.compile(r"<([^<>]+)>([^<]*)")
LINK_REL_NEXT_RE = re.compile(r'rel\s*=\s*"?next"?')
MAX_TREE_PAGES = 100
MAX_WINDOWS_GIT_FILE_BYTES = 16 * 1024 * 1024


def next_page_url(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for match in LINK_HEADER_RE.finditer(link_header):
        if LINK_REL_NEXT_RE.search(match.group(2)):
            return match.group(1)
    return None


def fetch_tree_page(url: str) -> tuple[object, str | None]:
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
        next_url = next_page_url(response.headers.get("Link"))
    return payload, next_url


def quoted_repo(repo: str) -> str:
    return urllib.parse.quote(repo, safe="/")


def quoted_path(path: str) -> str:
    return urllib.parse.quote(path, safe="/")


def validate_slash_path(value: str, label: str, pattern: re.Pattern[str]) -> None:
    if not value or value.startswith("/") or not pattern.fullmatch(value):
        raise ManifestError(f"unsafe {label}: {value!r}")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ManifestError(f"unsafe {label}: {value!r}")


def validate_repo(repo: str) -> None:
    if not SAFE_REPO.fullmatch(repo):
        raise ManifestError(f"unsafe Hugging Face repo id: {repo!r}")
    owner, name = repo.split("/", 1)
    if owner in {"", ".", ".."} or name in {"", ".", ".."}:
        raise ManifestError(f"unsafe Hugging Face repo id: {repo!r}")


def validate_revision(revision: str) -> None:
    validate_slash_path(revision, "revision", SAFE_REVISION)


def validate_model_path(path: str) -> None:
    validate_slash_path(path, "model path", SAFE_MODEL_PATH)


def validate_sha256(digest: str, path: str) -> None:
    if not HEX64.fullmatch(digest):
        raise ManifestError(f"invalid SHA-256 for {path}: {digest!r}")


def tree_url(repo: str, revision: str) -> str:
    return f"https://huggingface.co/api/models/{quoted_repo(repo)}/tree/{revision}?recursive=true"


def resolve_url(repo: str, revision: str, path: str) -> str:
    return f"https://huggingface.co/{quoted_repo(repo)}/resolve/{revision}/{quoted_path(path)}"


def tree_entries(repo: str, revision: str, fetch_page) -> list[object]:
    entries: list[object] = []
    url = tree_url(repo, revision)
    for _ in range(MAX_TREE_PAGES):
        page, next_url = fetch_page(url)
        if not isinstance(page, list):
            raise ManifestError("unexpected Hugging Face tree response")
        entries.extend(page)
        if next_url is None:
            return entries
        if not next_url.startswith("https://huggingface.co/"):
            raise ManifestError(f"unsafe Hugging Face tree pagination URL: {next_url!r}")
        url = next_url
    raise ManifestError(f"Hugging Face tree listing exceeded {MAX_TREE_PAGES} pages")


def listed_model_files(
    repo: str,
    revision: str,
    bundles: list[str],
    extra_files: list[str],
    fetch_page=fetch_tree_page,
) -> list[str]:
    tree = tree_entries(repo, revision, fetch_page)

    bundle_prefixes = tuple(f"{bundle}/" for bundle in bundles)
    wanted = []
    for item in tree:
        if not isinstance(item, dict) or item.get("type") != "file":
            continue
        path = item.get("path")
        if not isinstance(path, str):
            continue
        if path.startswith(bundle_prefixes) or path in extra_files:
            validate_model_path(path)
            wanted.append(path)

    bundle_order = {name: index for index, name in enumerate(bundles)}

    def sort_key(path: str) -> tuple[int, str]:
        bundle = path.split("/", 1)[0]
        return (bundle_order.get(bundle, len(bundle_order)), path)

    return sorted(wanted, key=sort_key)


def linked_etag(repo: str, revision: str, path: str) -> str | None:
    request = urllib.request.Request(resolve_url(repo, revision, path), method="HEAD")
    try:
        with NO_REDIRECT_OPENER.open(request, timeout=30) as response:
            etag = response.headers.get("X-Linked-ETag")
    except urllib.error.HTTPError as error:
        if error.code not in {301, 302, 303, 307, 308}:
            raise
        etag = error.headers.get("X-Linked-ETag")

    if not etag:
        return None
    etag = etag.strip().strip('"')
    return etag.lower() if HEX64.fullmatch(etag) else None


def downloaded_identity(
    repo: str,
    revision: str,
    path: str,
    max_bytes: int | None = None,
) -> tuple[int, str]:
    h = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(resolve_url(repo, revision, path), timeout=120) as response:
        while True:
            read_size = 1024 * 1024
            if max_bytes is not None:
                read_size = min(read_size, max_bytes - size + 1)
            chunk = response.read(read_size)
            if not chunk:
                break
            size += len(chunk)
            if max_bytes is not None and size > max_bytes:
                raise ManifestError(f"download exceeded the byte limit for {path}")
            h.update(chunk)
    return size, h.hexdigest()


def downloaded_sha256(repo: str, revision: str, path: str) -> str:
    return downloaded_identity(repo, revision, path)[1]


def digest_for_path(repo: str, revision: str, path: str) -> str:
    digest = linked_etag(repo, revision, path) or downloaded_sha256(repo, revision, path)
    validate_sha256(digest, path)
    return digest


def windows_model_specs(source_path: Path) -> dict[str, dict[str, object]]:
    """Load the inert Windows source/contract module and validate its shape."""
    values = runpy.run_path(str(source_path))
    sources = values.get("MODEL_SOURCES")
    required = values.get("MODEL_REQUIRED_FILES")
    optional = values.get("MODEL_OPTIONAL_FILES")
    alternatives = values.get("MODEL_ALTERNATIVE_FILES")
    if not all(isinstance(value, dict)
               for value in (sources, required, optional, alternatives)):
        raise ManifestError("Windows model source does not define manifest dictionaries")
    if not sources or set(sources) != set(required):
        raise ManifestError("every Windows model source must have a required-file contract")
    if not set(optional).issubset(sources) or not set(alternatives).issubset(sources):
        raise ManifestError("Windows optional/alternative contracts name an unknown model")

    specs: dict[str, dict[str, object]] = {}
    for model in sorted(sources):
        if not isinstance(model, str) or not SAFE_MODEL_NAME.fullmatch(model):
            raise ManifestError(f"unsafe Windows model name: {model!r}")
        source = sources[model]
        if not isinstance(source, tuple) or len(source) != 2:
            raise ManifestError(f"invalid source tuple for Windows model {model}")
        repo, revision = source
        if not isinstance(repo, str) or not isinstance(revision, str):
            raise ManifestError(f"invalid source values for Windows model {model}")
        validate_repo(repo)
        if not HEX40.fullmatch(revision):
            raise ManifestError(f"Windows model {model} must use a full commit revision")

        required_files = required[model]
        optional_files = optional.get(model, ())
        if (not isinstance(required_files, tuple) or not required_files or
                not isinstance(optional_files, tuple)):
            raise ManifestError(f"invalid file contract for Windows model {model}")
        files = required_files + optional_files
        if len(set(files)) != len(files):
            raise ManifestError(f"duplicate file contract entry for Windows model {model}")
        for path in files:
            if not isinstance(path, str):
                raise ManifestError(f"non-string file contract entry for Windows model {model}")
            validate_model_path(path)

        groups = alternatives.get(model, ())
        if not isinstance(groups, tuple):
            raise ManifestError(f"invalid alternative-file contract for Windows model {model}")
        for group in groups:
            if (not isinstance(group, tuple) or not group or
                    not set(group).issubset(files)):
                raise ManifestError(f"invalid alternative-file group for Windows model {model}")
        specs[model] = {
            "repository": repo,
            "revision": revision,
            "files": files,
        }
    return specs


def windows_file_identities(
    repo: str,
    revision: str,
    files: tuple[str, ...],
    tree: list[object],
    download=None,
) -> dict[str, tuple[int, str]]:
    entries: dict[str, dict[str, object]] = {}
    for value in tree:
        if not isinstance(value, dict) or value.get("type") != "file":
            continue
        path = value.get("path")
        if isinstance(path, str):
            if path in entries:
                raise ManifestError(f"duplicate Hugging Face tree entry: {path}")
            entries[path] = value

    identities: dict[str, tuple[int, str]] = {}
    for path in files:
        item = entries.get(path)
        if item is None:
            raise ManifestError(f"pinned Hugging Face revision is missing {path}")
        size = item.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise ManifestError(f"invalid byte size for {path}: {size!r}")
        lfs = item.get("lfs")
        digest = lfs.get("oid") if isinstance(lfs, dict) else None
        if isinstance(lfs, dict) and lfs.get("size") not in (None, size):
            raise ManifestError(f"inconsistent LFS byte size for {path}")
        if digest is None:
            if size > MAX_WINDOWS_GIT_FILE_BYTES:
                raise ManifestError(
                    f"large Windows model input has no LFS SHA-256: {path}")
            if download is None:
                downloaded_size, digest = downloaded_identity(
                    repo, revision, path, max_bytes=size)
            else:
                downloaded_size, digest = download(repo, revision, path)
            if downloaded_size != size:
                raise ManifestError(
                    f"downloaded size for {path} was {downloaded_size}, expected {size}")
        if not isinstance(digest, str):
            raise ManifestError(f"missing SHA-256 for {path}")
        digest = digest.lower()
        validate_sha256(digest, path)
        identities[path] = (size, digest)
    return identities


def render_windows_manifest(
    manifests: dict[str, dict[str, tuple[int, str]]],
) -> str:
    lines = ["MODEL_FILE_MANIFESTS = {"]
    for model in sorted(manifests):
        if not SAFE_MODEL_NAME.fullmatch(model):
            raise ManifestError(f"unsafe Windows model name: {model!r}")
        lines.append(f'    "{model}": {{')
        for path in sorted(manifests[model]):
            validate_model_path(path)
            size, digest = manifests[model][path]
            if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
                raise ManifestError(f"invalid byte size for {path}: {size!r}")
            if not isinstance(digest, str):
                raise ManifestError(f"invalid SHA-256 for {path}: {digest!r}")
            digest = digest.lower()
            validate_sha256(digest, path)
            lines.append(f'        "{path}": ({size}, "{digest}"),')
        lines.append("    },")
    lines.append("}")
    return "\n".join(lines)


def replace_windows_generated_block(source: str, manifest: str) -> str:
    begin = source.find(WINDOWS_BEGIN_MARKER)
    end = source.find(WINDOWS_END_MARKER)
    if begin == -1 or end == -1 or end < begin:
        raise ManifestError(f"could not find generated manifest markers in {WINDOWS_SOURCE}")
    return (
        source[:begin + len(WINDOWS_BEGIN_MARKER)]
        + "\n" + manifest + "\n"
        + source[end:]
    )


def current_windows_generated_block(source: str) -> str:
    begin = source.find(WINDOWS_BEGIN_MARKER)
    end = source.find(WINDOWS_END_MARKER)
    if begin == -1 or end == -1 or end < begin:
        raise ManifestError(f"could not find generated manifest markers in {WINDOWS_SOURCE}")
    return source[begin + len(WINDOWS_BEGIN_MARKER):end].strip()


def build_windows_manifest(source_path: Path) -> str:
    manifests: dict[str, dict[str, tuple[int, str]]] = {}
    for model, spec in windows_model_specs(source_path).items():
        repo = str(spec["repository"])
        revision = str(spec["revision"])
        files = tuple(spec["files"])
        tree = tree_entries(repo, revision, fetch_tree_page)
        print(f"reading pinned Windows model {model}", file=sys.stderr)
        manifests[model] = windows_file_identities(
            repo, revision, files, tree)
    return render_windows_manifest(manifests)


def render_manifest(paths: list[str], digests: dict[str, str]) -> str:
    lines = []
    for path in paths:
        validate_model_path(path)
        validate_sha256(digests[path], path)
        lines.append(
            f'        ModelFileDigest(relativePath: "{path}", sha256: "{digests[path]}"),'
        )
    return "\n".join(lines)


def replace_generated_block(source: str, manifest: str) -> str:
    begin = source.find(BEGIN_MARKER)
    end = source.find(END_MARKER)
    if begin == -1 or end == -1 or end < begin:
        raise ManifestError(f"could not find generated manifest markers in {SOURCE}")

    line_start = source.rfind("\n", 0, begin) + 1
    marker_indent = source[line_start:begin]
    replacement = (
        f"{BEGIN_MARKER}\n"
        f"{manifest}\n"
        f"{marker_indent}{END_MARKER}"
    )
    return source[:begin] + replacement + source[end + len(END_MARKER):]


def current_generated_block(source: str) -> str:
    begin = source.find(BEGIN_MARKER)
    end = source.find(END_MARKER)
    if begin == -1 or end == -1 or end < begin:
        raise ManifestError(f"could not find generated manifest markers in {SOURCE}")
    block = source[begin + len(BEGIN_MARKER):end]
    if block.startswith("\n"):
        block = block[1:]
    return block.rstrip()


def swift_constant(source: str, pattern: re.Pattern[str], label: str) -> str:
    match = pattern.search(source)
    if not match:
        raise ManifestError(f"could not find Swift {label} constant in {SOURCE}")
    return match.group(2)


def revision_from_source(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    revision = swift_constant(source, SWIFT_REVISION_RE, "repository commit")
    validate_revision(revision)
    return revision


def replace_swift_constant(source: str, pattern: re.Pattern[str], label: str, value: str) -> str:
    def replacement(match: re.Match[str]) -> str:
        return f"{match.group(1)}{value}{match.group(3)}"

    replaced, count = pattern.subn(replacement, source, count=1)
    if count != 1:
        raise ManifestError(f"could not update Swift {label} constant in {SOURCE}")
    return replaced


def update_source(source: str, manifest: str, repo: str, revision: str) -> str:
    validate_repo(repo)
    validate_revision(revision)
    source = replace_generated_block(source, manifest)
    source = replace_swift_constant(source, SWIFT_REPO_RE, "repository", repo)
    source = replace_swift_constant(source, SWIFT_REVISION_RE, "repository commit", revision)
    return source


def source_matches(source: str, manifest: str, repo: str, revision: str) -> bool:
    validate_repo(repo)
    validate_revision(revision)
    return (
        current_generated_block(source) == manifest
        and swift_constant(source, SWIFT_REPO_RE, "repository") == repo
        and swift_constant(source, SWIFT_REVISION_RE, "repository commit") == revision
    )


def build_manifest(args: argparse.Namespace) -> str:
    validate_repo(args.repo)
    validate_revision(args.revision)
    paths = listed_model_files(args.repo, args.revision, DEFAULT_BUNDLES, DEFAULT_EXTRA_FILES)
    if not paths:
        raise ManifestError("no model files found")

    digests: dict[str, str] = {}
    for path in paths:
        print(f"hashing {path}", file=sys.stderr)
        digests[path] = digest_for_path(args.repo, args.revision, path)
    return render_manifest(paths, digests)


def assert_raises(func, message: str) -> None:
    try:
        func()
    except ManifestError:
        return
    raise ManifestError(message)


def run_self_test() -> None:
    digest = "a" * 64
    revision = "b" * 40
    manifest = render_manifest(["Toy.mlmodelc/model.mil"], {"Toy.mlmodelc/model.mil": digest})
    source = """enum ModelIntegrity {
    static let parakeetV3Repository = "old/repo"
    static let parakeetV3RepositoryCommit = "oldref"

    private static let parakeetV3Files = [
        // BEGIN GENERATED PARAKEET_V3_MODEL_MANIFEST
        ModelFileDigest(relativePath: "Old.mlmodelc/model.mil", sha256: "0000000000000000000000000000000000000000000000000000000000000000"),
        // END GENERATED PARAKEET_V3_MODEL_MANIFEST
    ]
}
"""
    updated = update_source(source, manifest, DEFAULT_REPO, revision)
    if not source_matches(updated, manifest, DEFAULT_REPO, revision):
        raise ManifestError("self-test source update did not round-trip")
    if current_generated_block(updated) != manifest:
        raise ManifestError("self-test manifest block mismatch")

    with tempfile.TemporaryDirectory() as tmp:
        fixture = Path(tmp) / "main.swift"
        fixture.write_text(updated, encoding="utf-8")
        if revision_from_source(fixture) != revision:
            raise ManifestError("self-test did not read pinned revision from Swift source")
        fixture.write_text("enum ModelIntegrity {}\n", encoding="utf-8")
        assert_raises(lambda: revision_from_source(fixture),
                      "self-test accepted Swift source without revision constant")
        fixture.write_text('static let parakeetV3RepositoryCommit = "../main"\n', encoding="utf-8")
        assert_raises(lambda: revision_from_source(fixture),
                      "self-test accepted malformed revision constant")

    page_one = [{"type": "file", "path": "Toy.mlmodelc/model.mil"}]
    page_two = [{"type": "file", "path": "vocab.json"}]
    first_url = tree_url(DEFAULT_REPO, revision)
    second_url = f"https://huggingface.co/api/models/{DEFAULT_REPO}/tree/{revision}?recursive=true&cursor=abc"

    def paged_fetch(url: str) -> tuple[object, str | None]:
        if url == first_url:
            return page_one, second_url
        if url == second_url:
            return page_two, None
        raise ManifestError(f"self-test fetched unexpected URL: {url!r}")

    paths = listed_model_files(DEFAULT_REPO, revision, ["Toy.mlmodelc"], ["vocab.json"], paged_fetch)
    if paths != ["Toy.mlmodelc/model.mil", "vocab.json"]:
        raise ManifestError(f"self-test pagination dropped tree entries: {paths!r}")

    if next_page_url('<https://huggingface.co/page2>; rel="next"') != "https://huggingface.co/page2":
        raise ManifestError("self-test did not parse Link rel=next header")
    if next_page_url('<https://huggingface.co/page0>; rel="prev"') is not None:
        raise ManifestError("self-test treated rel=prev as a next page")
    if next_page_url(None) is not None:
        raise ManifestError("self-test invented a next page for a missing Link header")

    assert_raises(
        lambda: tree_entries(DEFAULT_REPO, revision,
                             lambda url: ([], "https://evil.example/tree")),
        "self-test accepted pagination URL outside huggingface.co",
    )
    assert_raises(
        lambda: tree_entries(DEFAULT_REPO, revision,
                             lambda url: ([], first_url)),
        "self-test accepted unbounded pagination",
    )
    assert_raises(
        lambda: tree_entries(DEFAULT_REPO, revision, lambda url: ({}, None)),
        "self-test accepted non-list tree response",
    )

    assert_raises(lambda: validate_model_path("../model.mil"),
                  "self-test accepted parent traversal in model path")
    assert_raises(lambda: validate_model_path("Toy.mlmodelc//model.mil"),
                  "self-test accepted empty model path segment")
    assert_raises(lambda: validate_model_path("Toy.mlmodelc/./model.mil"),
                  "self-test accepted dot model path segment")
    assert_raises(lambda: validate_model_path('Toy.mlmodelc/"model".mil'),
                  "self-test accepted unsafe model path character")
    assert_raises(lambda: validate_repo("FluidInference"),
                  "self-test accepted repo without owner/name")
    assert_raises(lambda: validate_revision("../main"),
                  "self-test accepted parent traversal in revision")
    assert_raises(lambda: validate_sha256("not-a-digest", "Toy.mlmodelc/model.mil"),
                  "self-test accepted malformed digest")
    assert_raises(lambda: current_generated_block("missing markers"),
                  "self-test accepted source without manifest markers")

    with tempfile.TemporaryDirectory() as tmp:
        windows_source = Path(tmp) / "model_manifest.py"
        windows_source.write_text(
            f'''MODEL_SOURCES = {{"toy": ("owner/model", "{revision}")}}
MODEL_REQUIRED_FILES = {{"toy": ("config.json", "model.bin")}}
MODEL_OPTIONAL_FILES = {{"toy": ("tokenizer.json",)}}
MODEL_ALTERNATIVE_FILES = {{"toy": (("tokenizer.json",),)}}
{WINDOWS_BEGIN_MARKER}
MODEL_FILE_MANIFESTS = {{}}
{WINDOWS_END_MARKER}
''',
            encoding="utf-8",
        )
        specs = windows_model_specs(windows_source)
        if specs["toy"]["files"] != ("config.json", "model.bin", "tokenizer.json"):
            raise ManifestError("self-test changed the Windows inference-file contract")

        small_digest = hashlib.sha256(b"{}").hexdigest()
        weight_digest = "c" * 64
        tree = [
            {"type": "file", "path": "config.json", "size": 2},
            {"type": "file", "path": "model.bin", "size": 7,
             "lfs": {"oid": weight_digest}},
            {"type": "file", "path": "tokenizer.json", "size": 2},
        ]
        downloaded = []

        def fake_download(repo: str, selected_revision: str, path: str) -> tuple[int, str]:
            downloaded.append((repo, selected_revision, path))
            return 2, small_digest

        identities = windows_file_identities(
            "owner/model", revision,
            ("config.json", "model.bin", "tokenizer.json"), tree,
            fake_download)
        if identities != {
                "config.json": (2, small_digest),
                "model.bin": (7, weight_digest),
                "tokenizer.json": (2, small_digest)}:
            raise ManifestError("self-test produced incorrect Windows file identities")
        if downloaded != [
                ("owner/model", revision, "config.json"),
                ("owner/model", revision, "tokenizer.json")]:
            raise ManifestError("self-test downloaded an LFS model or skipped a Git blob")

        rendered = render_windows_manifest({"toy": identities})
        updated = replace_windows_generated_block(
            windows_source.read_text(encoding="utf-8"), rendered)
        if current_windows_generated_block(updated) != rendered:
            raise ManifestError("self-test Windows manifest update did not round-trip")

        malformed = windows_source.read_text(encoding="utf-8").replace(revision, "main")
        windows_source.write_text(malformed, encoding="utf-8")
        assert_raises(lambda: windows_model_specs(windows_source),
                      "self-test accepted a mutable Windows model revision")

    assert_raises(
        lambda: windows_file_identities(
            "owner/model", revision, ("missing.bin",), [],
            lambda repo, selected_revision, path: (1, digest)),
        "self-test accepted a missing Windows inference file",
    )
    assert_raises(
        lambda: windows_file_identities(
            "owner/model", revision, ("config.json",),
            [{"type": "file", "path": "config.json", "size": 2}],
            lambda repo, selected_revision, path: (3, digest)),
        "self-test accepted a downloaded size mismatch",
    )
    large_downloads: list[str] = []
    assert_raises(
        lambda: windows_file_identities(
            "owner/model", revision, ("model.bin",),
            [{"type": "file", "path": "model.bin",
              "size": MAX_WINDOWS_GIT_FILE_BYTES + 1}],
            lambda repo, selected_revision, path: (
                large_downloads.append(path) or (1, digest))),
        "self-test accepted a large file without an LFS SHA-256",
    )
    if large_downloads:
        raise ManifestError("self-test attempted a large fallback download")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--windows",
        action="store_true",
        help="regenerate the Windows multi-model byte manifest instead of the macOS manifest",
    )
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument(
        "--revision",
        default=None,
        help="Hugging Face revision; defaults to parakeetV3RepositoryCommit parsed from --source",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="source file to update (defaults to the selected platform manifest source)",
    )
    parser.add_argument(
        "--write", action="store_true", help="rewrite the selected generated manifest block")
    parser.add_argument(
        "--check", action="store_true", help="fail if the selected manifest is not up to date")
    parser.add_argument("--self-test", action="store_true", help="run offline updater self-tests")
    args = parser.parse_args()

    selected_modes = sum([args.write, args.check, args.self_test])
    if selected_modes > 1:
        raise ManifestError("--write, --check, and --self-test are mutually exclusive")
    if args.self_test:
        run_self_test()
        return 0

    if args.source is None:
        args.source = WINDOWS_SOURCE if args.windows else SOURCE

    if args.windows:
        if args.repo != DEFAULT_REPO or args.revision is not None:
            raise ManifestError(
                "--repo and --revision apply only to the macOS single-model manifest")
        manifest = build_windows_manifest(args.source)
        source = args.source.read_text(encoding="utf-8")
        if args.write:
            args.source.write_text(
                replace_windows_generated_block(source, manifest), encoding="utf-8")
            return 0
        if args.check:
            if current_windows_generated_block(source) != manifest:
                print(
                    "Windows model manifest is stale; run "
                    "scripts/update-model-manifest.py --windows --write",
                    file=sys.stderr,
                )
                return 1
            return 0
        print(manifest)
        return 0

    if args.revision is None:
        args.revision = revision_from_source(args.source)

    manifest = build_manifest(args)

    if args.write:
        source = args.source.read_text(encoding="utf-8")
        args.source.write_text(update_source(source, manifest, args.repo, args.revision), encoding="utf-8")
        return 0

    if args.check:
        source = args.source.read_text(encoding="utf-8")
        if not source_matches(source, manifest, args.repo, args.revision):
            print("model manifest is stale; run scripts/update-model-manifest.py --write", file=sys.stderr)
            return 1
        return 0

    print(manifest)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ManifestError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
