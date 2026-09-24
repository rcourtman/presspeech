#!/usr/bin/env python3
"""Compare public install metadata with the releases currently on GitHub.

The static docs sync catches disagreement inside the repository. This check
closes the other half of the boundary: a Pages deployment must not advertise
an older release than GitHub, or a current release whose public assets no
longer match the documented contract. A configured version newer than the
published version is allowed for local release preparation because release
commits reach ``main`` before publication. Pages uses ``--require-published``
to keep the existing site until the advertised downloads are available.
Use ``--github-api-via-gh`` when a repository-scoped gh broker holds the API
credentials; checksum sidecars still download over public HTTPS.
With ``--check-release-notes``, also compare the latest public macOS and
Windows release descriptions with their tracked notes and check that the two
published builds with known first-use risks carry their disclosure on
their own release pages. The macOS 0.3.8 compatibility-report invitation also
needs a support-route handoff while new issue creation is restricted.
``--notes-only`` runs just that read-only audit,
without asset downloads or candidate metadata checks.
This remains useful while source metadata is preparing a newer release:
visitors can still reach the preceding release pages. It is not a Pages gate:
a source edit cannot correct an already-published GitHub release page.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import signal
import subprocess
import threading
import time
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
FIRST_USE_LEAD_CHARACTERS = 2000

# These archived release pages remain direct download entry points after newer
# versions ship. A tuple within a release's marker list accepts equivalent
# wording; this is a coarse presence check, not a semantic privacy review.
KNOWN_DISCLOSURE_MARKERS = {
    "v0.3.8": (
        "before opening",
        "model",
        "hugging face token",
        "malformed",
        "https_proxy",
        "http_proxy",
        "proxy credentials",
        ("ignore it", "ignored"),
        ("without the expected proxy", "without the proxy you expected", "connect directly"),
        "wait",
        "0.3.9",
        "privacy.html#network-calls",
        "universal clipboard",
        "privacy.html#operating-system-clipboard-services",
    ),
    "windows-v0.1.12": (
        ("before opening", "before launching"),
        "model",
        "hugging face",
        "token",
        "telemetry",
        ("routing", "route"),
        "wait",
        "0.1.13",
        "launch presspeech",
        ("checked by default", "checked initially"),
        "finish",
        "windows.html#model-download-privacy",
        "automatic local readiness check",
        "start presspeech with windows",
        "selected by default",
        "clipboard history",
        "cloud clipboard",
        "privacy.html#operating-system-clipboard-services",
    ),
}

# macOS 0.3.8 directly invites compatibility reports. Its archived page is a
# standalone entry point even when current site guidance explains that GitHub
# may restrict new issues and the worksheet only saves a local draft. Keep this
# check separate from the model-download warning so the failure is actionable.
KNOWN_REPORTING_MARKERS = {
    "v0.3.8": (
        "support.md",
        ("issue creation is restricted", "restricts new issues"),
        ("not submitted or monitored", "local, unmonitored draft"),
    ),
}
REPORT_INVITATION_MARKERS = ("help verify", "passing reports", "share only aggregate outcomes")


class ReleaseCheckError(RuntimeError):
    pass


class GithubHTTPError(ReleaseCheckError):
    def __init__(self, status: int, url: str, detail: str = "") -> None:
        self.status = status
        super().__init__(f"GitHub returned HTTP {status} for {url}{detail}")


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


def release_asset_api_url(asset: dict[str, object], tag: str, name: str) -> str:
    asset_id = asset.get("id")
    if not isinstance(asset_id, int) or isinstance(asset_id, bool) or asset_id <= 0:
        raise ReleaseCheckError(f"{tag} asset {name} has no valid GitHub asset ID")
    expected = f"{API_ROOT}/releases/assets/{asset_id}"
    if asset.get("url") != expected:
        raise ReleaseCheckError(f"{tag} asset {name} has an unexpected GitHub API URL")
    return expected


def load_release_checksum(
    asset: dict[str, object],
    *,
    tag: str,
    name: str,
    loader: Callable[[str], bytes],
) -> tuple[bytes, int | None]:
    download_url = f"{DOWNLOAD_ROOT}/{tag}/{name}"
    validate_asset(asset, tag, name)
    if asset.get("browser_download_url") != download_url:
        raise ReleaseCheckError(f"{tag} asset {name} has an unexpected download URL")
    try:
        return loader(download_url), None
    except GithubHTTPError as exc:
        # GitHub can fail the public browser-download route even while the
        # immutable asset remains available through its release-asset API.
        # Retry only server failures, against the same API-identified asset;
        # checksum content and API digest are still verified below.
        if not 500 <= exc.status < 600:
            raise
        api_url = release_asset_api_url(asset, tag, name)
        try:
            return loader(api_url), exc.status
        except ReleaseCheckError as fallback_error:
            raise ReleaseCheckError(
                f"{exc}; GitHub asset API fallback failed: {fallback_error}"
            ) from fallback_error


def validate_release(
    release: dict[str, object],
    *,
    tag: str,
    prerelease: bool,
    package_name: str,
    checksum_loader: Callable[[str], bytes],
    expected_package_size: int | None = None,
    expected_package_digest: str | None = None,
) -> int | None:
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
    checksum, fallback_status = load_release_checksum(
        assets[checksum_name],
        tag=tag,
        name=checksum_name,
        loader=checksum_loader,
    )
    validate_checksum(
        checksum,
        tag=tag,
        package_name=package_name,
        package_digest=package_digest,
        checksum_asset=assets[checksum_name],
    )
    return fallback_status


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


def release_note_parity_errors(
    mac_release: object,
    releases: object,
    *,
    root: Path = ROOT,
) -> list[str]:
    """Audit the latest public entry point for each platform, not candidate metadata.

    Pages can still advertise the preceding release while main prepares the
    next one. Never print release bodies or a diff: the audit only needs to
    identify which public entry needs an authorized correction.
    """
    checks: list[tuple[str, object, Path]] = []
    errors: list[str] = []
    if isinstance(mac_release, dict):
        tag = mac_release.get("tag_name")
        if (isinstance(tag, str) and tag.startswith("v") and
                SEMVER.fullmatch(tag[1:]) and mac_release.get("draft") is False):
            checks.append((tag, mac_release, root / "swift" / "release-notes" / f"{tag}.md"))
    if not checks:
        errors.append("latest published macOS release metadata is missing or invalid")

    found_windows = False
    if isinstance(releases, list):
        windows_releases: list[tuple[tuple[int, int, int], str, dict[str, object]]] = []
        for release in releases:
            if not isinstance(release, dict) or release.get("draft") is not False:
                continue
            tag = release.get("tag_name")
            if not isinstance(tag, str) or not tag.startswith("windows-v"):
                continue
            version = tag.removeprefix("windows-v")
            if SEMVER.fullmatch(version):
                windows_releases.append((parse_version(version, "Windows release notes"), tag, release))
        if windows_releases:
            _, tag, release = max(windows_releases, key=lambda item: item[0])
            checks.append((tag, release, root / "windows" / "release-notes" / f"{tag.removeprefix('windows-v')}.md"))
            found_windows = True
    if not found_windows:
        errors.append("latest published Windows release metadata is missing or invalid")

    for tag, release, path in checks:
        body = release.get("body") if isinstance(release, dict) else None
        if not isinstance(body, str) or not body.strip():
            errors.append(f"{tag} has no public release notes")
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"cannot read tracked notes for {tag}: {exc}")
            continue
        if source.replace("\r\n", "\n").strip() != body.replace("\r\n", "\n").strip():
            errors.append(
                f"{tag} public notes differ from {path.relative_to(root)}; "
                "editing the tracked file does not update GitHub's published notes"
            )
    return errors


def known_release_disclosure_errors(releases: object) -> list[str]:
    """Spot missing decisions on exact public builds with known first-use risks.

    This never treats a matching tracked note as proof that the public wording
    is sufficient. Marker presence still requires human review of the actual
    rendered page, including whether its links and advice make sense.
    """
    if not isinstance(releases, list):
        return ["public release list is missing; cannot audit first-use disclosures"]
    errors: list[str] = []
    for tag, markers in KNOWN_DISCLOSURE_MARKERS.items():
        matching = [release for release in releases if isinstance(release, dict)
                    and release.get("tag_name") == tag and release.get("draft") is False]
        if len(matching) != 1:
            errors.append(f"{tag} public release entry is missing or duplicated; cannot audit its first-use disclosure")
            continue
        body = matching[0].get("body")
        if not isinstance(body, str) or not body.strip():
            errors.append(f"{tag} has no public release notes; cannot verify its first-use disclosure")
            continue
        # An archived release remains a direct download page. A warning buried
        # beneath the release history does not give a first-use decision, even
        # if every marker appears somewhere in the full body. GitHub Markdown
        # often wraps the lead notice in blockquotes and lines.
        lead = body[:FIRST_USE_LEAD_CHARACTERS]
        normalized = re.sub(r"\s+", " ", re.sub(r"(?m)^\s*>\s?", "", lead).casefold())
        missing = []
        for marker in markers:
            alternatives = (marker,) if isinstance(marker, str) else marker
            if not any(phrase in normalized for phrase in alternatives):
                missing.append(" / ".join(alternatives))
        if missing:
            errors.append(
                f"{tag} public release notes lack first-use disclosure markers "
                f"in their first {FIRST_USE_LEAD_CHARACTERS} characters: "
                + ", ".join(missing)
            )
    return errors


def known_release_reporting_errors(releases: object) -> list[str]:
    """Check the archived compatibility invitation's public reporting handoff.

    This is a marker check, not proof that a GitHub form or comment route works.
    Review the rendered page and verify intake with a non-collaborator account.
    """
    if not isinstance(releases, list):
        return ["public release list is missing; cannot audit compatibility reporting guidance"]
    errors: list[str] = []
    for tag, markers in KNOWN_REPORTING_MARKERS.items():
        matching = [release for release in releases if isinstance(release, dict)
                    and release.get("tag_name") == tag and release.get("draft") is False]
        if len(matching) != 1:
            errors.append(f"{tag} public release entry is missing or duplicated; cannot audit its reporting guidance")
            continue
        body = matching[0].get("body")
        if not isinstance(body, str) or not body.strip():
            errors.append(f"{tag} has no public release notes; cannot verify its reporting guidance")
            continue
        normalized = re.sub(r"\s+", " ", body).casefold()
        if not any(marker in normalized for marker in REPORT_INVITATION_MARKERS):
            continue
        missing = []
        for marker in markers:
            alternatives = (marker,) if isinstance(marker, str) else marker
            if not any(phrase in normalized for phrase in alternatives):
                missing.append(" / ".join(alternatives))
        if missing:
            errors.append(
                f"{tag} public release notes lack compatibility-reporting guidance markers: "
                + ", ".join(missing)
            )
    return errors


class GithubRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(request, fp, code, msg, headers, newurl)
        if redirected is not None and urllib.parse.urlsplit(newurl)[:2] != ("https", "api.github.com"):
            redirected.remove_header("Authorization")
        return redirected


def github_request(
    url: str,
    *,
    token: str = "",
    limit: int = MAX_API_BYTES,
    accept: str | None = None,
) -> bytes:
    headers = {"User-Agent": "presspeech-public-release-check"}
    if url.startswith(API_ROOT):
        headers.update(
            {
                "Accept": accept or "application/vnd.github+json",
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
                detail += " or supply a read-only GITHUB_TOKEN or use --github-api-via-gh"
        elif exc.code in {401, 403}:
            detail = "; request denied; check public access and any configured token permissions"
        raise GithubHTTPError(exc.code, url, detail) from exc
    except urllib.error.URLError as exc:
        raise ReleaseCheckError(f"could not reach GitHub for {url}: {exc.reason}") from exc
    if len(data) > limit:
        raise ReleaseCheckError(f"GitHub response exceeded {limit} bytes for {url}")
    return data


def github_checksum_request(
    url: str, *, token: str = "", limit: int = MAX_CHECKSUM_BYTES
) -> bytes:
    if url.startswith(f"{API_ROOT}/releases/assets/"):
        return github_request(
            url,
            token=token,
            limit=limit,
            accept="application/octet-stream",
        )
    # Do not send an API credential to the browser-download host.
    return github_request(url, limit=limit)


def github_api_via_gh(url: str, *, limit: int = MAX_API_BYTES, timeout: float = 30) -> bytes:
    """Read release JSON through gh (including a credential-isolating broker).

    Never export credentials or ask gh to follow asset download URLs. gh owns
    authentication and its cross-host redirect policy. The explicit hostname
    prevents GH_HOST from redirecting these repository-scoped API requests.
    """
    allowed = re.fullmatch(
        re.escape(API_ROOT) + r"/releases(?:/latest|\?per_page=100&page=([1-9]\d*))", url
    )
    if not allowed or (allowed[1] is not None and int(allowed[1]) > MAX_RELEASE_PAGES):
        raise ReleaseCheckError("gh transport only accepts this repository's release JSON endpoints")
    endpoint = url.removeprefix("https://api.github.com/")
    command = [
        "gh", "api", endpoint, "--hostname", "github.com", "--method", "GET",
        "--header", "Accept: application/vnd.github+json",
        "--header", f"X-GitHub-Api-Version: {API_VERSION}",
    ]
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, start_new_session=(os.name == "posix"),
        )
    except OSError as exc:
        raise ReleaseCheckError("could not start gh; install it or use the default HTTPS transport") from exc

    data = bytearray()
    read_errors: list[OSError] = []
    finished = threading.Event()

    def read_bounded() -> None:
        try:
            with process.stdout as output:
                while len(data) <= limit:
                    chunk = output.read1(min(65536, limit + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
        except OSError as exc:
            read_errors.append(exc)
        finally:
            finished.set()

    reader = threading.Thread(target=read_bounded, daemon=True)
    reader.start()
    try:
        if not finished.wait(max(0, deadline - time.monotonic())):
            raise ReleaseCheckError(f"gh API request timed out for {url}")
        if len(data) > limit:
            raise ReleaseCheckError(f"GitHub response exceeded {limit} bytes for {url}")
        if read_errors:
            raise ReleaseCheckError(f"could not read gh API response for {url}")
        try:
            result = process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            raise ReleaseCheckError(f"gh API request timed out for {url}") from exc
        if result:
            # Do not relay subprocess diagnostics: GH_DEBUG can include headers.
            raise ReleaseCheckError(f"gh API request failed (exit {result}) for {url}; check repository read access")
        return bytes(data)
    finally:
        if process.poll() is None or not finished.is_set():
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
        process.wait()
        reader.join(timeout=1)


def github_json(url: str, token: str, *, via_gh: bool = False) -> object:
    try:
        data = github_api_via_gh(url) if via_gh else github_request(url, token=token)
        return json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ReleaseCheckError(f"GitHub returned invalid JSON for {url}") from exc


def github_releases(token: str, *, via_gh: bool = False) -> list[object]:
    releases: list[object] = []
    for page in range(1, MAX_RELEASE_PAGES + 1):
        batch = github_json(f"{API_ROOT}/releases?per_page=100&page={page}", token, via_gh=via_gh)
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
            fallback_status = validate_release(
                mac_release,
                tag=f"v{mac_version}",
                prerelease=False,
                package_name="Presspeech.zip",
                checksum_loader=checksum_loader,
                expected_package_size=size,
                expected_package_digest=digest,
            )
            fallback_note = ""
            if fallback_status is not None:
                fallback_note = (
                    " (checksum fetched via GitHub asset API after browser download "
                    f"HTTP {fallback_status})"
                )
            status.append(
                f"macOS {mac_version} matches its public immutable release{fallback_note}"
            )
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
            fallback_status = validate_release(
                windows_release,
                tag=f"windows-v{windows_version}",
                prerelease=True,
                package_name=package_name,
                checksum_loader=checksum_loader,
            )
            fallback_note = ""
            if fallback_status is not None:
                fallback_note = (
                    " (checksum fetched via GitHub asset API after browser download "
                    f"HTTP {fallback_status})"
                )
            status.append(
                f"Windows {windows_version} matches its public immutable release{fallback_note}"
            )
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

    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "swift" / "release-notes").mkdir(parents=True)
        (root / "windows" / "release-notes").mkdir(parents=True)
        (root / "swift" / "release-notes" / "v1.2.3.md").write_text("mac note\n", encoding="utf-8")
        (root / "windows" / "release-notes" / "4.5.6.md").write_text("Windows note\n", encoding="utf-8")
        mac["body"] = "mac note"
        windows["body"] = "Windows note\r\n"
        if release_note_parity_errors(mac, [mac, windows], root=root):
            raise ReleaseCheckError("self-test rejected matching public release notes")
        if release_note_parity_errors({}, [windows], root=root) != [
            "latest published macOS release metadata is missing or invalid"
        ]:
            raise ReleaseCheckError("self-test accepted missing macOS release notes")
        if release_note_parity_errors(mac, [], root=root) != [
            "latest published Windows release metadata is missing or invalid"
        ]:
            raise ReleaseCheckError("self-test accepted missing Windows release notes")
        windows["body"] = "older Windows note"
        note_errors = release_note_parity_errors(mac, [mac, windows], root=root)
        if len(note_errors) != 1 or "windows-v4.5.6" not in note_errors[0]:
            raise ReleaseCheckError("self-test did not identify stale public release notes")
        windows["body"] = "Windows note"
        newer_draft = dict(windows, tag_name="windows-v4.5.7", draft=True, body="draft")
        older_public = dict(windows, tag_name="windows-v4.5.5", body="older public notes")
        if release_note_parity_errors(mac, [newer_draft, older_public, mac, windows], root=root):
            raise ReleaseCheckError("self-test did not select the latest published notes")
        windows["body"] = "older Windows note"
        note_errors = release_note_parity_errors(mac, [newer_draft, older_public, windows], root=root)
        if len(note_errors) != 1 or "windows-v4.5.6" not in note_errors[0]:
            raise ReleaseCheckError("self-test skipped public notes during release preparation")
        windows["body"] = "Windows note"

    # A release can match its tracked notes and still omit a necessary
    # published-version decision. Check both named archived pages directly.
    disclosed = [
        {"tag_name": "v0.3.8", "draft": False, "body": (
            "> Before opening macOS 0.3.8: a missing model download may include a\n"
            "> Hugging Face token. If unsure, wait until 0.3.9. See\n"
            "> A malformed https_proxy or http_proxy URL may log proxy credentials;\n"
            "> the client can ignore it and request a model without the expected proxy.\n"
            "> https://rcourtman.github.io/presspeech/privacy.html#network-calls. "
            "Universal Clipboard may share text; see "
            "https://rcourtman.github.io/presspeech/privacy.html#operating-system-clipboard-services"
        )},
        {"tag_name": "windows-v0.1.12", "draft": False, "body": (
            "Before launching Windows 0.1.12: model downloads may send Hugging Face "
            "telemetry and a token; custom routing can change the destination. "
            "If unsure, wait until 0.1.13. Launch Presspeech is checked by default; "
            "clear it before Finish if waiting. See "
            "https://rcourtman.github.io/presspeech/windows.html#model-download-privacy. "
            "Setup opens the microphone for an automatic local readiness check; "
            "Start Presspeech with Windows is selected by default. "
            "Clipboard History or Cloud Clipboard may retain text; see "
            "https://rcourtman.github.io/presspeech/privacy.html#operating-system-clipboard-services"
        )},
    ]
    if known_release_disclosure_errors(disclosed):
        raise ReleaseCheckError("self-test rejected named release disclosures")
    missing_warning = json.loads(json.dumps(disclosed))
    missing_warning[1]["body"] = "Local recognition; download the installer."
    if not any("windows-v0.1.12" in error for error in known_release_disclosure_errors(missing_warning)):
        raise ReleaseCheckError("self-test missed a disclosure omitted from public Windows notes")
    missing_launch_warning = json.loads(json.dumps(disclosed))
    missing_launch_warning[1]["body"] = missing_launch_warning[1]["body"].replace(
        "checked by default", "optional"
    )
    if not any("checked by default / checked initially" in error
               for error in known_release_disclosure_errors(missing_launch_warning)):
        raise ReleaseCheckError("self-test missed the default-on installer launch checkbox")
    missing_clipboard = json.loads(json.dumps(disclosed))
    missing_clipboard[0]["body"] = missing_clipboard[0]["body"].replace("Universal Clipboard", "clipboard")
    if not any("universal clipboard" in error for error in known_release_disclosure_errors(missing_clipboard)):
        raise ReleaseCheckError("self-test missed the macOS clipboard boundary")
    missing_proxy = json.loads(json.dumps(disclosed))
    missing_proxy[0]["body"] = missing_proxy[0]["body"].replace(
        "A malformed https_proxy or http_proxy URL may log proxy credentials;\n"
        "> the client can ignore it and request a model without the expected proxy.\n", ""
    )
    if not any("malformed" in error for error in known_release_disclosure_errors(missing_proxy)):
        raise ReleaseCheckError("self-test missed the malformed macOS proxy warning")
    missing_proxy_effect = json.loads(json.dumps(disclosed))
    missing_proxy_effect[0]["body"] = missing_proxy_effect[0]["body"].replace(
        "without the expected proxy", "on its usual route")
    if not any("without the expected proxy" in error
               for error in known_release_disclosure_errors(missing_proxy_effect)):
        raise ReleaseCheckError("self-test missed the unexpected model request route")
    missing_microphone = json.loads(json.dumps(disclosed))
    missing_microphone[1]["body"] = missing_microphone[1]["body"].replace("automatic local readiness check", "check")
    if not any("automatic local readiness check" in error for error in known_release_disclosure_errors(missing_microphone)):
        raise ReleaseCheckError("self-test missed the Windows automatic microphone check")

    buried_warning = json.loads(json.dumps(disclosed))
    for release in buried_warning:
        release["body"] = "Release history.\n" + ("x" * FIRST_USE_LEAD_CHARACTERS) + release["body"]
    if len(known_release_disclosure_errors(buried_warning)) != len(disclosed):
        raise ReleaseCheckError("self-test accepted first-use warnings buried below release history")

    if not any("v0.3.8" in error for error in known_release_disclosure_errors(disclosed[1:])):
        raise ReleaseCheckError("self-test missed an unauditable archived macOS release")

    reporting = json.loads(json.dumps(disclosed))
    reporting[0]["body"] += (
        "\nHelp verify compatibility with aggregate outcomes. "
        "Check https://github.com/rcourtman/presspeech/blob/main/SUPPORT.md "
        "before sharing. If issue creation is restricted, keep the local draft; "
        "it is not submitted or monitored."
    )
    if known_release_reporting_errors(reporting):
        raise ReleaseCheckError("self-test rejected the compatibility reporting handoff")
    draft_wording = json.loads(json.dumps(reporting))
    draft_wording[0]["body"] = (
        "Help verify compatibility. GitHub currently restricts new issues. "
        "The worksheet saves only a local, unmonitored draft. Check "
        "https://github.com/rcourtman/presspeech/blob/main/SUPPORT.md."
    )
    if known_release_reporting_errors(draft_wording):
        raise ReleaseCheckError("self-test rejected equivalent public reporting guidance")
    if known_release_reporting_errors(disclosed):
        raise ReleaseCheckError("self-test required a reporting handoff without an invitation")
    without_handoff = json.loads(json.dumps(reporting))
    without_handoff[0]["body"] = "Help verify compatibility with aggregate outcomes."
    if not any("v0.3.8" in error for error in known_release_reporting_errors(without_handoff)):
        raise ReleaseCheckError("self-test missed the absent public reporting handoff")
    if not any("v0.3.8" in error for error in known_release_reporting_errors(reporting[1:])):
        raise ReleaseCheckError("self-test missed an unauditable archived reporting handoff")

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

    api_fallback_mac = json.loads(json.dumps(mac))
    mac_checksum_asset = api_fallback_mac["assets"][1]
    mac_checksum_asset["id"] = 12345
    mac_checksum_asset["url"] = f"{API_ROOT}/releases/assets/12345"
    mac_download_url = f"{DOWNLOAD_ROOT}/v1.2.3/Presspeech.zip.sha256"
    api_asset_url = mac_checksum_asset["url"]
    fallback_calls: list[str] = []

    def fallback_loader(url: str) -> bytes:
        fallback_calls.append(url)
        if url == mac_download_url:
            raise GithubHTTPError(500, url)
        if url == api_asset_url:
            return mac_checksum
        return checksums[url]

    errors, status = public_release_errors(
        metadata, api_fallback_mac, [api_fallback_mac, windows], fallback_loader
    )
    if (
        errors
        or len(status) != 2
        or "API after browser download HTTP 500" not in status[0]
        or fallback_calls[:2] != [mac_download_url, api_asset_url]
    ):
        raise ReleaseCheckError(
            "self-test did not verify a checksum via the same API asset after HTTP 5xx"
        )

    mismatched_api_mac = json.loads(json.dumps(api_fallback_mac))
    mismatched_api_mac["assets"][1]["url"] = (
        "https://api.github.com/repos/other/project/releases/assets/12345"
    )
    attempted: list[str] = []

    def mismatched_api_loader(url: str) -> bytes:
        attempted.append(url)
        if url == mac_download_url:
            raise GithubHTTPError(503, url)
        return checksums[url]

    errors, _ = public_release_errors(
        metadata, mismatched_api_mac, [mismatched_api_mac, windows], mismatched_api_loader
    )
    windows_download_url = f"{DOWNLOAD_ROOT}/windows-v4.5.6/{windows_name}.sha256"
    if (
        len(errors) != 1
        or "unexpected GitHub API URL" not in errors[0]
        or attempted != [mac_download_url, windows_download_url]
    ):
        raise ReleaseCheckError(
            "self-test allowed a checksum fallback outside the expected API asset"
        )

    not_found_calls: list[str] = []

    def not_found_loader(url: str) -> bytes:
        not_found_calls.append(url)
        if url == mac_download_url:
            raise GithubHTTPError(404, url)
        return checksums[url]

    errors, _ = public_release_errors(
        metadata, api_fallback_mac, [api_fallback_mac, windows], not_found_loader
    )
    if len(errors) != 1 or "HTTP 404" not in errors[0] or api_asset_url in not_found_calls:
        raise ReleaseCheckError("self-test retried a non-server checksum failure")

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
    parser.add_argument("--check-release-notes", action="store_true", help="audit public release-note parity, known first-use disclosures, and the 0.3.8 reporting handoff")
    parser.add_argument("--notes-only", action="store_true", help="audit only public release notes and known disclosures; skip package and candidate-metadata checks")
    parser.add_argument("--github-api-via-gh", action="store_true", help="read API JSON through repo-scoped gh api without exporting credentials; checksum downloads remain public HTTPS")
    args = parser.parse_args()
    if args.notes_only and (args.require_published or args.check_release_notes):
        parser.error("--notes-only cannot be combined with --require-published or --check-release-notes")
    try:
        if args.self_test:
            run_self_test()
            print("public release check self-test passed")
            return 0

        token = "" if args.github_api_via_gh else os.environ.get("GITHUB_TOKEN", "").strip()
        mac_release = github_json(f"{API_ROOT}/releases/latest", token, via_gh=args.github_api_via_gh)
        releases = github_releases(token, via_gh=args.github_api_via_gh)
        if args.notes_only:
            errors = release_note_parity_errors(mac_release, releases)
            errors.extend(known_release_disclosure_errors(releases))
            errors.extend(known_release_reporting_errors(releases))
            for error in errors:
                print(f"check-public-releases: {error}", file=sys.stderr)
            if errors:
                return 1
            print("latest public macOS and Windows release notes match tracked files; known disclosure and reporting markers are present")
            return 0
        metadata = load_metadata()
        errors, status = public_release_errors(
            metadata,
            mac_release,
            releases,
            lambda url: github_checksum_request(
                url, token=token, limit=MAX_CHECKSUM_BYTES
            ),
            require_published=args.require_published,
        )
        if args.check_release_notes:
            errors.extend(release_note_parity_errors(mac_release, releases))
            errors.extend(known_release_disclosure_errors(releases))
            errors.extend(known_release_reporting_errors(releases))
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
