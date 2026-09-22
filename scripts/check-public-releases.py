#!/usr/bin/env python3
"""Compare public install metadata with the releases currently on GitHub.

The static docs sync catches disagreement inside the repository. This check
closes the other half of the boundary: a Pages deployment must not advertise
an older release than GitHub, or a current release whose public assets no
longer match the documented contract. A configured version newer than the
published version is allowed for local release preparation because release
commits reach ``main`` before publication. Pages uses ``--require-published``
to keep the existing site until the advertised downloads are available.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
METADATA_PATH = ROOT / "docs" / "site-metadata.json"
API_ROOT = "https://api.github.com/repos/rcourtman/presspeech"
DOWNLOAD_ROOT = "https://github.com/rcourtman/presspeech/releases/download"
API_VERSION = "2026-03-10"
SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
SHA256 = re.compile(r"[0-9a-f]{64}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
MAX_API_BYTES = 10 * 1024 * 1024
MAX_CHECKSUM_BYTES = 4096
MAX_RELEASE_PAGES = 100


class ReleaseCheckError(RuntimeError):
    pass


def parse_version(value: object, label: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not SEMVER.fullmatch(value):
        raise ReleaseCheckError(f"{label} is not a canonical X.Y.Z version: {value!r}")
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def load_metadata(path: Path = METADATA_PATH) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseCheckError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseCheckError(f"{path} must contain a JSON object")
    return value


def release_version(release: object, prefix: str, label: str) -> tuple[str, tuple[int, int, int]]:
    if not isinstance(release, dict):
        raise ReleaseCheckError(f"{label} metadata is not an object")
    tag = release.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith(prefix):
        raise ReleaseCheckError(f"{label} has unexpected tag {tag!r}")
    version = tag.removeprefix(prefix)
    return version, parse_version(version, f"{label} tag")


def assets_by_name(release: dict[str, object], tag: str) -> dict[str, dict[str, object]]:
    raw_assets = release.get("assets")
    if not isinstance(raw_assets, list):
        raise ReleaseCheckError(f"{tag} has no asset list")
    assets: dict[str, dict[str, object]] = {}
    for raw_asset in raw_assets:
        if not isinstance(raw_asset, dict) or not isinstance(raw_asset.get("name"), str):
            raise ReleaseCheckError(f"{tag} contains an asset without a name")
        name = raw_asset["name"]
        if name in assets:
            raise ReleaseCheckError(f"{tag} contains duplicate asset {name}")
        assets[name] = raw_asset
    return assets


def validate_asset(asset: dict[str, object], tag: str, name: str) -> str:
    if asset.get("state") != "uploaded":
        raise ReleaseCheckError(f"{tag} asset {name} is not fully uploaded")
    size = asset.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ReleaseCheckError(f"{tag} asset {name} has invalid size {size!r}")
    digest = asset.get("digest")
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise ReleaseCheckError(f"{tag} asset {name} has no valid SHA-256 digest")
    expected_url = f"{DOWNLOAD_ROOT}/{tag}/{name}"
    if asset.get("browser_download_url") != expected_url:
        raise ReleaseCheckError(f"{tag} asset {name} has an unexpected download URL")
    return digest.removeprefix("sha256:")


def validate_checksum(
    checksum: bytes,
    *,
    tag: str,
    package_name: str,
    package_digest: str,
    checksum_asset: dict[str, object],
) -> None:
    expected = f"{package_digest}  {package_name}\n".encode("ascii")
    if checksum != expected:
        raise ReleaseCheckError(
            f"{tag} checksum does not exactly bind {package_name} to its published digest"
        )
    if checksum_asset.get("size") != len(checksum):
        raise ReleaseCheckError(f"{tag} checksum download size disagrees with GitHub metadata")
    expected_digest = validate_asset(checksum_asset, tag, f"{package_name}.sha256")
    if hashlib.sha256(checksum).hexdigest() != expected_digest:
        raise ReleaseCheckError(f"{tag} checksum download disagrees with its published digest")


def validate_release(
    release: dict[str, object],
    *,
    tag: str,
    prerelease: bool,
    package_name: str,
    checksum: bytes,
    expected_package_size: int | None = None,
    expected_package_digest: str | None = None,
) -> None:
    if release.get("tag_name") != tag or release.get("draft") is not False:
        raise ReleaseCheckError(f"public metadata does not describe published release {tag}")
    if release.get("prerelease") is not prerelease:
        kind = "a prerelease" if prerelease else "a stable release"
        raise ReleaseCheckError(f"{tag} is not published as {kind}")
    if release.get("immutable") is not True:
        raise ReleaseCheckError(f"{tag} is not reported as immutable")

    checksum_name = f"{package_name}.sha256"
    assets = assets_by_name(release, tag)
    if set(assets) != {package_name, checksum_name}:
        raise ReleaseCheckError(
            f"{tag} must publish exactly {package_name} and {checksum_name}"
        )
    package = assets[package_name]
    package_digest = validate_asset(package, tag, package_name)
    if expected_package_size is not None and package.get("size") != expected_package_size:
        raise ReleaseCheckError(f"{tag} {package_name} size disagrees with site metadata")
    if expected_package_digest is not None and package_digest != expected_package_digest:
        raise ReleaseCheckError(f"{tag} {package_name} digest disagrees with site metadata")
    validate_checksum(
        checksum,
        tag=tag,
        package_name=package_name,
        package_digest=package_digest,
        checksum_asset=assets[checksum_name],
    )


def compare_versions(
    configured: str, published: str, platform: str
) -> str:
    configured_value = parse_version(configured, f"configured {platform} version")
    published_value = parse_version(published, f"published {platform} version")
    if published_value > configured_value:
        raise ReleaseCheckError(
            f"public docs advertise {platform} {configured}, but GitHub has newer release {published}"
        )
    return "current" if published_value == configured_value else "upcoming"


class GithubRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(request, fp, code, msg, headers, newurl)
        if redirected is not None and urllib.parse.urlsplit(newurl)[:2] != ("https", "api.github.com"):
            redirected.remove_header("Authorization")
        return redirected


def github_request(url: str, *, token: str = "", limit: int = MAX_API_BYTES) -> bytes:
    headers = {"User-Agent": "presspeech-public-release-check"}
    if url.startswith(API_ROOT):
        headers.update(
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
            }
        )
        if token:
            headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.build_opener(GithubRedirectHandler()).open(request, timeout=30) as response:
            data = response.read(limit + 1)
    except urllib.error.HTTPError as exc:
        detail = ""
        if exc.code in {403, 429} and exc.headers.get("X-RateLimit-Remaining") == "0":
            detail = "; API rate limit exhausted; retry after reset"
            if not token:
                detail += " or supply a read-only GITHUB_TOKEN"
        elif exc.code in {401, 403}:
            detail = "; request denied; check public access and any configured token permissions"
        raise ReleaseCheckError(f"GitHub returned HTTP {exc.code} for {url}{detail}") from exc
    except urllib.error.URLError as exc:
        raise ReleaseCheckError(f"could not reach GitHub for {url}: {exc.reason}") from exc
    if len(data) > limit:
        raise ReleaseCheckError(f"GitHub response exceeded {limit} bytes for {url}")
    return data


def github_json(url: str, token: str) -> object:
    try:
        return json.loads(github_request(url, token=token))
    except json.JSONDecodeError as exc:
        raise ReleaseCheckError(f"GitHub returned invalid JSON for {url}") from exc


def github_releases(token: str) -> list[object]:
    releases: list[object] = []
    for page in range(1, MAX_RELEASE_PAGES + 1):
        batch = github_json(f"{API_ROOT}/releases?per_page=100&page={page}", token)
        if not isinstance(batch, list):
            raise ReleaseCheckError("GitHub release list is not an array")
        releases.extend(batch)
        if len(batch) < 100:
            return releases
    raise ReleaseCheckError("GitHub release list exceeded the pagination limit")


def public_release_errors(
    metadata: dict[str, object],
    mac_release: object,
    releases: object,
    checksum_loader: Callable[[str], bytes],
    *,
    require_published: bool = False,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    status: list[str] = []
    try:
        mac_version = metadata.get("version")
        if not isinstance(mac_version, str):
            raise ReleaseCheckError("site metadata has no macOS version")
        published_mac, _ = release_version(mac_release, "v", "latest macOS release")
        disposition = compare_versions(mac_version, published_mac, "macOS")
        if disposition == "current":
            if not isinstance(mac_release, dict):
                raise ReleaseCheckError("latest macOS release metadata is not an object")
            digest = metadata.get("release_zip_sha256")
            size = metadata.get("release_zip_bytes")
            if not isinstance(digest, str) or not SHA256.fullmatch(digest):
                raise ReleaseCheckError("site metadata has no valid macOS release digest")
            if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
                raise ReleaseCheckError("site metadata has no valid macOS release size")
            checksum_url = f"{DOWNLOAD_ROOT}/v{mac_version}/Presspeech.zip.sha256"
            validate_release(
                mac_release,
                tag=f"v{mac_version}",
                prerelease=False,
                package_name="Presspeech.zip",
                checksum=checksum_loader(checksum_url),
                expected_package_size=size,
                expected_package_digest=digest,
            )
            status.append(f"macOS {mac_version} matches its public immutable release")
        else:
            if require_published:
                raise ReleaseCheckError(f"configured macOS {mac_version} is not published; keep the current site until publication completes")
            status.append(
                f"macOS {mac_version} is configured ahead of published {published_mac}"
            )
    except ReleaseCheckError as exc:
        errors.append(str(exc))

    try:
        windows_version = metadata.get("windows_version")
        if not isinstance(windows_version, str):
            raise ReleaseCheckError("site metadata has no Windows version")
        if not isinstance(releases, list):
            raise ReleaseCheckError("GitHub release list is not an array")
        windows_releases: list[tuple[tuple[int, int, int], str, dict[str, object]]] = []
        for release in releases:
            if not isinstance(release, dict):
                continue
            # An authenticated Actions token may be able to see drafts. They
            # are not public releases and must not make deployed docs look old.
            if release.get("draft") is not False:
                continue
            tag = release.get("tag_name")
            if not isinstance(tag, str) or not tag.startswith("windows-v"):
                continue
            version, parsed = release_version(release, "windows-v", "Windows release")
            windows_releases.append((parsed, version, release))
        if not windows_releases:
            raise ReleaseCheckError("GitHub has no published Windows release")
        _, published_windows, windows_release = max(windows_releases, key=lambda item: item[0])
        disposition = compare_versions(windows_version, published_windows, "Windows")
        if disposition == "current":
            package_name = f"Presspeech-Setup-{windows_version}-x64.exe"
            checksum_url = (
                f"{DOWNLOAD_ROOT}/windows-v{windows_version}/{package_name}.sha256"
            )
            validate_release(
                windows_release,
                tag=f"windows-v{windows_version}",
                prerelease=True,
                package_name=package_name,
                checksum=checksum_loader(checksum_url),
            )
            status.append(f"Windows {windows_version} matches its public immutable release")
        else:
            if require_published:
                raise ReleaseCheckError(f"configured Windows {windows_version} is not published; keep the current site until publication completes")
            status.append(
                f"Windows {windows_version} is configured ahead of published {published_windows}"
            )
    except ReleaseCheckError as exc:
        errors.append(str(exc))
    return errors, status


def run_self_test() -> None:
    def fixture_release(tag: str, prerelease: bool, package: str, contents: bytes) -> tuple[dict[str, object], bytes]:
        package_digest = hashlib.sha256(contents).hexdigest()
        checksum = f"{package_digest}  {package}\n".encode("ascii")
        base = f"{DOWNLOAD_ROOT}/{tag}"
        return (
            {
                "tag_name": tag,
                "draft": False,
                "prerelease": prerelease,
                "immutable": True,
                "assets": [
                    {
                        "name": package,
                        "state": "uploaded",
                        "size": len(contents),
                        "digest": f"sha256:{package_digest}",
                        "browser_download_url": f"{base}/{package}",
                    },
                    {
                        "name": f"{package}.sha256",
                        "state": "uploaded",
                        "size": len(checksum),
                        "digest": f"sha256:{hashlib.sha256(checksum).hexdigest()}",
                        "browser_download_url": f"{base}/{package}.sha256",
                    },
                ],
            },
            checksum,
        )

    mac_bytes = b"signed app fixture"
    mac, mac_checksum = fixture_release("v1.2.3", False, "Presspeech.zip", mac_bytes)
    windows_name = "Presspeech-Setup-4.5.6-x64.exe"
    windows, windows_checksum = fixture_release(
        "windows-v4.5.6", True, windows_name, b"installer fixture"
    )
    checksums = {
        f"{DOWNLOAD_ROOT}/v1.2.3/Presspeech.zip.sha256": mac_checksum,
        f"{DOWNLOAD_ROOT}/windows-v4.5.6/{windows_name}.sha256": windows_checksum,
    }
    metadata = {
        "version": "1.2.3",
        "windows_version": "4.5.6",
        "release_zip_bytes": len(mac_bytes),
        "release_zip_sha256": hashlib.sha256(mac_bytes).hexdigest(),
    }
    errors, status = public_release_errors(metadata, mac, [mac, windows], checksums.__getitem__)
    if errors or len(status) != 2:
        raise ReleaseCheckError(f"self-test rejected valid releases: {errors!r}")

    draft = json.loads(json.dumps(windows))
    draft["tag_name"] = "windows-v9.9.9"
    draft["draft"] = True
    errors, _ = public_release_errors(
        metadata, mac, [draft, windows], checksums.__getitem__
    )
    if errors:
        raise ReleaseCheckError(f"self-test treated a private draft as public: {errors!r}")

    stale_metadata = dict(metadata, windows_version="4.5.5")
    errors, _ = public_release_errors(stale_metadata, mac, [windows], checksums.__getitem__)
    if len(errors) != 1 or "newer release 4.5.6" not in errors[0]:
        raise ReleaseCheckError("self-test did not reject docs older than Windows release")

    ahead_metadata = dict(metadata, version="1.2.4", windows_version="4.5.7")
    errors, status = public_release_errors(ahead_metadata, mac, [windows], checksums.__getitem__)
    if errors or not all("configured ahead" in line for line in status):
        raise ReleaseCheckError("self-test rejected valid pre-release metadata")
    errors, _ = public_release_errors(
        ahead_metadata, mac, [windows], checksums.__getitem__, require_published=True
    )
    if len(errors) != 2 or not all("is not published" in error for error in errors):
        raise ReleaseCheckError("self-test allowed unpublished download links through the Pages gate")

    broken_mac = json.loads(json.dumps(mac))
    broken_mac["assets"][0]["digest"] = "sha256:" + "0" * 64
    errors, _ = public_release_errors(metadata, broken_mac, [windows], checksums.__getitem__)
    if len(errors) != 1 or "digest disagrees" not in errors[0]:
        raise ReleaseCheckError("self-test did not reject a changed macOS digest")

    broken_checksums = dict(checksums)
    broken_checksums[
        f"{DOWNLOAD_ROOT}/windows-v4.5.6/{windows_name}.sha256"
    ] = ("0" * 64 + f"  {windows_name}\n").encode("ascii")
    errors, _ = public_release_errors(metadata, mac, [windows], broken_checksums.__getitem__)
    if len(errors) != 1 or "does not exactly bind" not in errors[0]:
        raise ReleaseCheckError("self-test did not reject a mismatched checksum")

    from unittest import mock

    with mock.patch(__name__ + ".github_json", side_effect=[[mac] * 100, [windows]]) as loader:
        all_releases = github_releases("")
        if len(all_releases) != 101 or all_releases[-1] != windows:
            raise ReleaseCheckError("self-test omitted a Windows release after the first API page")
        if loader.call_args_list[-1].args[0] != f"{API_ROOT}/releases?per_page=100&page=2":
            raise ReleaseCheckError("self-test did not request the next release page")
    headers = {"X-RateLimit-Remaining": "0"}
    rate_limit = urllib.error.HTTPError(API_ROOT, 403, "Forbidden", headers, None)
    with mock.patch("urllib.request.OpenerDirector.open", side_effect=rate_limit):
        try:
            github_request(f"{API_ROOT}/releases/latest")
        except ReleaseCheckError as exc:
            if "rate limit exhausted" not in str(exc) or "GITHUB_TOKEN" not in str(exc):
                raise ReleaseCheckError("self-test lost the unauthenticated rate-limit diagnostic")
        else:
            raise ReleaseCheckError("self-test treated HTTP 403 as a successful release check")
    request = urllib.request.Request(f"{API_ROOT}/releases/latest", headers={"Authorization": "Bearer test-only"})
    redirected = GithubRedirectHandler().redirect_request(
        request, None, 302, "Found", {}, "https://release-assets.githubusercontent.com/checksum"
    )
    if redirected is None or redirected.has_header("Authorization"):
        raise ReleaseCheckError("self-test leaked authorization through a cross-host redirect")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="run without network access")
    parser.add_argument("--require-published", action="store_true", help="block deployment while configured downloads are not public yet")
    args = parser.parse_args()
    try:
        if args.self_test:
            run_self_test()
            print("public release check self-test passed")
            return 0

        token = os.environ.get("GITHUB_TOKEN", "").strip()
        metadata = load_metadata()
        mac_release = github_json(f"{API_ROOT}/releases/latest", token)
        releases = github_releases(token)
        errors, status = public_release_errors(
            metadata,
            mac_release,
            releases,
            lambda url: github_request(url, limit=MAX_CHECKSUM_BYTES),
            require_published=args.require_published,
        )
        for line in status:
            print(line)
        if errors:
            for error in errors:
                print(f"check-public-releases: {error}", file=sys.stderr)
            return 1
        if any("configured ahead" in line for line in status):
            print("published release checks passed; configured upcoming versions are not yet public")
        else:
            print("public releases match the documentation contract")
        return 0
    except (ReleaseCheckError, KeyError) as exc:
        print(f"check-public-releases: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
