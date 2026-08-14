"""Privacy-safe, checksum-verified Windows release updates."""

import hashlib
import json
import os
import re
import tempfile
import urllib.parse
import urllib.request


RELEASES_API = (
    "https://api.github.com/repos/rcourtman/presspeech/releases?per_page=30")
USER_AGENT = "presspeech-windows-update-check"
API_VERSION = "2026-03-10"
TAG_RE = re.compile(r"^windows-v(\d+)\.(\d+)\.(\d+)$")
CHECKSUM_RE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$")
ALLOWED_DOWNLOAD_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}


class UpdateError(RuntimeError):
    pass


def parse_version(value):
    """Return an X.Y.Z tuple from a version or windows-vX.Y.Z tag."""
    value = str(value or "").strip()
    match = TAG_RE.fullmatch(value)
    if match:
        return tuple(int(part) for part in match.groups())
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        raise ValueError("invalid version: %s" % value)
    return tuple(int(part) for part in match.groups())


def _assets_by_name(release):
    return {
        asset.get("name", ""): asset
        for asset in release.get("assets", [])
        if asset.get("state", "uploaded") == "uploaded"
    }


def select_update(releases, current_version):
    """Select the newest complete Windows release newer than current."""
    current = parse_version(current_version)
    candidates = []
    for release in releases:
        if release.get("draft"):
            continue
        try:
            version = parse_version(release.get("tag_name", ""))
        except ValueError:
            continue
        if version <= current:
            continue
        version_text = ".".join(str(part) for part in version)
        installer_name = "Presspeech-Setup-%s-x64.exe" % version_text
        checksum_name = installer_name + ".sha256"
        assets = _assets_by_name(release)
        installer = assets.get(installer_name)
        checksum = assets.get(checksum_name)
        if not installer or not checksum:
            continue
        installer_url = installer.get("browser_download_url", "")
        checksum_url = checksum.get("browser_download_url", "")
        try:
            _checked_download_url(installer_url)
            _checked_download_url(checksum_url)
        except UpdateError:
            continue
        installer_size = int(installer.get("size", 0) or 0)
        if installer_size <= 0:
            continue
        digest = str(installer.get("digest", ""))
        if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
            continue
        candidates.append((version, {
            "version": version_text,
            "tag": release.get("tag_name", ""),
            "release_url": release.get("html_url", ""),
            "body": release.get("body", ""),
            "installer_name": installer_name,
            "installer_url": installer_url,
            "installer_size": installer_size,
            "installer_digest": digest.partition(":")[2].lower(),
            "checksum_url": checksum_url,
        }))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _request(url):
    return urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": API_VERSION,
        },
    )


def _checked_download_url(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_DOWNLOAD_HOSTS:
        raise UpdateError("release asset uses an unexpected download host")
    return url


class _ReleaseRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep every release-asset redirect on an approved HTTPS origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _checked_download_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _UpdateAPIRedirectHandler(urllib.request.HTTPRedirectHandler):
    """The fixed GitHub API request has no legitimate redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise UpdateError("GitHub redirected the update check unexpectedly")


def _open_release_asset(request, opener, timeout):
    # urlopen validates neither intermediate redirect schemes nor hosts. Use a
    # dedicated opener in production; injected openers keep unit tests offline.
    if opener is None:
        opener = urllib.request.build_opener(_ReleaseRedirectHandler()).open
    return opener(request, timeout=timeout)


def _open_update_api(request, opener, timeout):
    if opener is None:
        opener = urllib.request.build_opener(_UpdateAPIRedirectHandler()).open
    return opener(request, timeout=timeout)


def fetch_update(current_version, opener=None, timeout=15):
    """Query public GitHub releases without sending app or device identity."""
    try:
        with _open_update_api(_request(RELEASES_API), opener, timeout) as response:
            final_url = getattr(response, "geturl", lambda: RELEASES_API)()
            if final_url != RELEASES_API:
                raise UpdateError("GitHub redirected the update check unexpectedly")
            payload = response.read(2 * 1024 * 1024 + 1)
    except Exception as exc:
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError("could not check GitHub releases: %s" % exc) from exc
    if len(payload) > 2 * 1024 * 1024:
        raise UpdateError("GitHub release response was unexpectedly large")
    try:
        releases = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise UpdateError("GitHub returned an invalid release response") from exc
    if not isinstance(releases, list):
        raise UpdateError("GitHub release response was not a list")
    return select_update(releases, current_version)


def parse_checksum(text, expected_name):
    for line in text.splitlines():
        match = CHECKSUM_RE.fullmatch(line.strip())
        if match and match.group(2) == expected_name:
            return match.group(1).lower()
    raise UpdateError("release checksum did not name the expected installer")


def _read_checksum(update, opener, timeout):
    url = _checked_download_url(update["checksum_url"])
    try:
        with _open_release_asset(_request(url), opener, timeout) as response:
            final_url = getattr(response, "geturl", lambda: url)()
            _checked_download_url(final_url)
            payload = response.read(8193)
    except Exception as exc:
        raise UpdateError("could not download the release checksum: %s" % exc) from exc
    if len(payload) > 8192:
        raise UpdateError("release checksum file was unexpectedly large")
    try:
        return parse_checksum(payload.decode("ascii"), update["installer_name"])
    except UnicodeDecodeError as exc:
        raise UpdateError("release checksum was not plain text") from exc


def download_update(update, destination=None, progress=None,
                    opener=None, timeout=30):
    """Download an installer atomically and verify its published SHA-256."""
    url = _checked_download_url(update["installer_url"])
    _checked_download_url(update["checksum_url"])
    destination = destination or tempfile.gettempdir()
    os.makedirs(destination, exist_ok=True)
    final_path = os.path.join(destination, update["installer_name"])
    partial_path = final_path + ".part"
    checksum = _read_checksum(update, opener, timeout)
    api_digest = update.get("installer_digest", "").lower()
    if api_digest and api_digest != checksum:
        raise UpdateError("GitHub asset digest and checksum file disagree")
    expected_size = int(update.get("installer_size", 0) or 0)
    digest = hashlib.sha256()
    downloaded = 0
    try:
        with _open_release_asset(_request(url), opener, timeout) as response, \
                open(partial_path, "wb") as output:
            final_url = getattr(response, "geturl", lambda: url)()
            _checked_download_url(final_url)
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                output.write(block)
                digest.update(block)
                downloaded += len(block)
                if progress is not None:
                    progress(downloaded, expected_size)
        if expected_size and downloaded != expected_size:
            raise UpdateError("installer download size did not match the release")
        if digest.hexdigest().lower() != checksum:
            raise UpdateError("installer SHA-256 verification failed")
        os.replace(partial_path, final_path)
        return final_path
    except Exception as exc:
        try:
            os.remove(partial_path)
        except OSError:
            pass
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError("could not download the installer: %s" % exc) from exc
