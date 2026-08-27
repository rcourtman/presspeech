"""Privacy-safe, checksum-verified Windows release updates."""

import contextlib
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


def _published_assets(release, expected_names):
    """Return the exact uploaded asset set, or None for malformed metadata."""
    assets = release.get("assets")
    if not isinstance(assets, list) or len(assets) != len(expected_names):
        return None
    assets_by_name = {}
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("state") != "uploaded":
            return None
        name = asset.get("name")
        if (not isinstance(name, str) or name not in expected_names or
                name in assets_by_name):
            return None
        assets_by_name[name] = asset
    return assets_by_name if set(assets_by_name) == expected_names else None


def _canonical_asset_url(tag, name):
    return (
        "https://github.com/rcourtman/presspeech/releases/download/%s/%s"
        % (tag, name)
    )


def select_update(releases, current_version):
    """Select the newest complete Windows release newer than current."""
    current = parse_version(current_version)
    candidates = []
    for release in releases:
        if (not isinstance(release, dict) or release.get("draft") or
                release.get("prerelease") is not True):
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
        assets = _published_assets(release, {installer_name, checksum_name})
        if assets is None:
            continue
        installer = assets.get(installer_name)
        checksum = assets.get(checksum_name)
        installer_url = installer.get("browser_download_url", "")
        checksum_url = checksum.get("browser_download_url", "")
        tag = release.get("tag_name", "")
        if (installer_url != _canonical_asset_url(tag, installer_name) or
                checksum_url != _canonical_asset_url(tag, checksum_name)):
            continue
        try:
            _checked_download_url(installer_url)
            _checked_download_url(checksum_url)
            installer_size = int(installer.get("size", 0) or 0)
            checksum_size = int(checksum.get("size", 0) or 0)
        except (UpdateError, TypeError, ValueError, OverflowError):
            continue
        installer_digest = str(installer.get("digest", ""))
        checksum_digest = str(checksum.get("digest", ""))
        if (installer_size <= 0 or checksum_size <= 0 or checksum_size > 8192 or
                not re.fullmatch(
                    r"sha256:[0-9a-fA-F]{64}", installer_digest) or
                not re.fullmatch(
                    r"sha256:[0-9a-fA-F]{64}", checksum_digest)):
            continue
        candidates.append((version, {
            "version": version_text,
            "tag": tag,
            "release_url": release.get("html_url", ""),
            "body": release.get("body", ""),
            "installer_name": installer_name,
            "installer_url": installer_url,
            "installer_size": installer_size,
            "installer_digest": installer_digest.partition(":")[2].lower(),
            "checksum_url": checksum_url,
            "checksum_size": checksum_size,
            "checksum_digest": checksum_digest.partition(":")[2].lower(),
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
        expected_size = int(update.get("checksum_size", 0) or 0)
    except (TypeError, ValueError, OverflowError) as exc:
        raise UpdateError("release checksum metadata was invalid") from exc
    expected_digest = str(update.get("checksum_digest", "")).lower()
    if (expected_size <= 0 or expected_size > 8192 or
            not re.fullmatch(r"[0-9a-f]{64}", expected_digest)):
        raise UpdateError("release checksum metadata was invalid")
    try:
        with _open_release_asset(_request(url), opener, timeout) as response:
            final_url = getattr(response, "geturl", lambda: url)()
            _checked_download_url(final_url)
            payload = response.read(expected_size + 1)
    except Exception as exc:
        raise UpdateError("could not download the release checksum: %s" % exc) from exc
    if len(payload) != expected_size:
        raise UpdateError("release checksum size did not match the release")
    if hashlib.sha256(payload).hexdigest().lower() != expected_digest:
        raise UpdateError("release checksum SHA-256 verification failed")
    try:
        return parse_checksum(payload.decode("ascii"), update["installer_name"])
    except UnicodeDecodeError as exc:
        raise UpdateError("release checksum was not plain text") from exc


def _installer_metadata(update, installer_path):
    expected_name = str(update.get("installer_name", ""))
    expected_digest = str(update.get("installer_digest", "")).lower()
    try:
        expected_size = int(update.get("installer_size", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise UpdateError("installer metadata was invalid") from exc
    if (not expected_name or os.path.basename(installer_path) != expected_name or
            expected_size <= 0 or
            not re.fullmatch(r"[0-9a-f]{64}", expected_digest)):
        raise UpdateError("installer metadata was invalid")
    return expected_size, expected_digest


def _open_locked_installer(installer_path):
    """Open a Windows file while denying write/delete sharing."""
    if os.name != "nt":
        return open(installer_path, "rb")

    # Keep the native handle open across CreateProcess. FILE_SHARE_READ lets
    # Windows load the executable while the missing WRITE and DELETE share
    # flags prevent another process from changing or replacing the approved
    # path between the final hash check and process creation.
    import ctypes
    import msvcrt
    from ctypes import wintypes

    generic_read = 0x80000000
    file_share_read = 0x00000001
    open_existing = 3
    file_attribute_normal = 0x00000080
    file_flag_open_reparse_point = 0x00200000
    file_attribute_directory = 0x00000010
    file_attribute_reparse_point = 0x00000400
    invalid_handle_value = ctypes.c_void_p(-1).value

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("file_attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ByHandleFileInformation),
    ]
    get_information.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    handle = create_file(
        installer_path,
        generic_read,
        file_share_read,
        None,
        open_existing,
        file_attribute_normal | file_flag_open_reparse_point,
        None,
    )
    if handle == invalid_handle_value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        information = ByHandleFileInformation()
        if not get_information(handle, ctypes.byref(information)):
            raise ctypes.WinError(ctypes.get_last_error())
        if information.file_attributes & (
                file_attribute_directory | file_attribute_reparse_point):
            raise UpdateError("verified installer is no longer available")
        file_descriptor = msvcrt.open_osfhandle(
            handle, os.O_RDONLY | os.O_BINARY)
        handle = None
        return os.fdopen(file_descriptor, "rb")
    finally:
        if handle is not None:
            close_handle(handle)


@contextlib.contextmanager
def locked_verified_installer(update, installer_path):
    """Hold a verified installer immutable through process creation."""
    expected_size, expected_digest = _installer_metadata(
        update, installer_path)
    try:
        if os.name != "nt" and os.path.islink(installer_path):
            raise UpdateError("verified installer is no longer available")
        with _open_locked_installer(installer_path) as handle:
            if os.fstat(handle.fileno()).st_size != expected_size:
                raise UpdateError("installer changed after verification")
            digest = hashlib.sha256()
            while True:
                block = handle.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
            if digest.hexdigest().lower() != expected_digest:
                raise UpdateError("installer changed after verification")
            yield
    except UpdateError:
        raise
    except OSError as exc:
        raise UpdateError("could not revalidate the installer") from exc


def verify_installer(update, installer_path):
    """Revalidate an approved installer path without launching it."""
    with locked_verified_installer(update, installer_path):
        pass


def download_update(update, destination=None, progress=None,
                    opener=None, timeout=30):
    """Download an installer atomically and verify its published SHA-256."""
    url = _checked_download_url(update["installer_url"])
    _checked_download_url(update["checksum_url"])
    destination = destination or tempfile.gettempdir()
    os.makedirs(destination, exist_ok=True)
    final_path = os.path.join(destination, update["installer_name"])
    checksum = _read_checksum(update, opener, timeout)
    api_digest = update.get("installer_digest", "").lower()
    if api_digest and api_digest != checksum:
        raise UpdateError("GitHub asset digest and checksum file disagree")
    expected_size = int(update.get("installer_size", 0) or 0)
    digest = hashlib.sha256()
    downloaded = 0
    partial_path = None
    partial_fd = None
    try:
        with _open_release_asset(_request(url), opener, timeout) as response:
            final_url = getattr(response, "geturl", lambda: url)()
            _checked_download_url(final_url)
            # An exclusive random staging file prevents a pre-created link at
            # the old predictable .part path from redirecting/truncating some
            # other user-writable file when the download starts.
            partial_fd, partial_path = tempfile.mkstemp(
                prefix=update["installer_name"] + ".",
                suffix=".part",
                dir=destination,
            )
            output = os.fdopen(partial_fd, "wb")
            partial_fd = None
            with output:
                while True:
                    # The release API's size is part of the verified asset
                    # metadata. Read one sentinel byte beyond it so a broken or
                    # hostile response cannot fill the temp drive before the size
                    # mismatch is rejected.
                    read_size = 1024 * 1024
                    if expected_size:
                        read_size = min(read_size, expected_size - downloaded + 1)
                    block = response.read(read_size)
                    if not block:
                        break
                    downloaded += len(block)
                    if expected_size and downloaded > expected_size:
                        raise UpdateError(
                            "installer download exceeded the release size")
                    output.write(block)
                    digest.update(block)
                    if progress is not None:
                        progress(downloaded, expected_size)
        if expected_size and downloaded != expected_size:
            raise UpdateError("installer download size did not match the release")
        if digest.hexdigest().lower() != checksum:
            raise UpdateError("installer SHA-256 verification failed")
        os.replace(partial_path, final_path)
        return final_path
    except Exception as exc:
        if partial_fd is not None:
            try:
                os.close(partial_fd)
            except OSError:
                pass
        try:
            if partial_path is not None:
                os.remove(partial_path)
        except OSError:
            pass
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError("could not download the installer: %s" % exc) from exc
