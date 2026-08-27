import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

import updates


class Response(io.BytesIO):
    def __init__(self, payload, url="https://github.com/file"):
        super().__init__(payload)
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def geturl(self):
        return self.url


class CountingResponse(Response):
    def __init__(self, payload, url="https://github.com/file"):
        super().__init__(payload, url)
        self.bytes_read = 0

    def read(self, size=-1):
        block = super().read(size)
        self.bytes_read += len(block)
        return block


def release(version, complete=True, draft=False):
    installer = "Presspeech-Setup-%s-x64.exe" % version
    checksum = ("a" * 64 + "  " + installer + "\n").encode("ascii")
    assets = [{
        "name": installer,
        "state": "uploaded",
        "browser_download_url":
            "https://github.com/rcourtman/presspeech/releases/download/"
            "windows-v%s/%s" % (version, installer),
        "size": 4,
        "digest": "sha256:" + ("a" * 64),
    }]
    if complete:
        assets.append({
            "name": installer + ".sha256",
            "state": "uploaded",
            "browser_download_url":
                "https://github.com/rcourtman/presspeech/releases/download/"
                "windows-v%s/%s.sha256" % (version, installer),
            "size": len(checksum),
            "digest": "sha256:" + hashlib.sha256(checksum).hexdigest(),
        })
    return {
        "tag_name": "windows-v" + version,
        "draft": draft,
        "prerelease": True,
        "html_url": "https://github.com/release/" + version,
        "assets": assets,
    }


class UpdateSelectionTests(unittest.TestCase):
    def test_version_parser_accepts_release_tag_and_plain_version(self):
        self.assertEqual(updates.parse_version("windows-v1.2.3"), (1, 2, 3))
        self.assertEqual(updates.parse_version("1.2.3"), (1, 2, 3))

    def test_selects_newest_complete_windows_release(self):
        releases = [
            release("0.1.1"),
            release("0.1.4", complete=False),
            release("0.1.3"),
            release("9.0.0", draft=True),
            {"tag_name": "v99.0.0", "assets": []},
        ]
        selected = updates.select_update(releases, "0.1.0")
        self.assertEqual(selected["version"], "0.1.3")

    def test_selects_windows_release_beyond_old_thirty_release_window(self):
        releases = [
            {
                "tag_name": "v9.9.%d" % index,
                "draft": False,
                "prerelease": False,
                "assets": [],
            }
            for index in range(99)
        ]
        releases.append(release("0.1.1"))
        selected = updates.select_update(releases, "0.1.0")
        self.assertEqual(selected["version"], "0.1.1")

    def test_returns_none_when_current_is_newest(self):
        self.assertIsNone(updates.select_update([release("0.1.0")], "0.1.0"))

    def test_ignores_release_assets_on_untrusted_hosts(self):
        candidate = release("0.1.1")
        candidate["assets"][0]["browser_download_url"] = (
            "https://example.com/installer.exe")
        self.assertIsNone(updates.select_update([candidate], "0.1.0"))

    def test_ignores_release_without_github_sha256_digest(self):
        candidate = release("0.1.1")
        candidate["assets"][0]["digest"] = None
        self.assertIsNone(updates.select_update([candidate], "0.1.0"))

    def test_ignores_non_prerelease_or_inexact_asset_sets(self):
        stable = release("0.1.1")
        stable["prerelease"] = False
        extra = release("0.1.2")
        extra["assets"].append({
            "name": "unexpected.txt",
            "state": "uploaded",
        })
        duplicate = release("0.1.3")
        duplicate["assets"][1] = dict(duplicate["assets"][0])
        missing_state = release("0.1.4")
        del missing_state["assets"][0]["state"]
        for candidate in (stable, extra, duplicate, missing_state):
            with self.subTest(tag=candidate["tag_name"]):
                self.assertIsNone(
                    updates.select_update([candidate], "0.1.0"))

    def test_ignores_allowed_host_with_noncanonical_asset_path(self):
        candidate = release("0.1.1")
        candidate["assets"][0]["browser_download_url"] = (
            "https://github.com/rcourtman/presspeech/releases/download/"
            "windows-v0.1.0/Presspeech-Setup-0.1.1-x64.exe")
        self.assertIsNone(updates.select_update([candidate], "0.1.0"))

    def test_fetch_uses_fixed_privacy_safe_headers(self):
        self.assertTrue(updates.RELEASES_API.endswith("?per_page=100"))
        seen = {}

        def opener(request, timeout):
            seen["request"] = request
            seen["timeout"] = timeout
            return Response(
                json.dumps([release("0.1.1")]).encode("utf-8"),
                updates.RELEASES_API)

        selected = updates.fetch_update("0.1.0", opener=opener)
        self.assertEqual(selected["version"], "0.1.1")
        headers = {key.lower(): value for key, value in
                   seen["request"].header_items()}
        self.assertEqual(headers["user-agent"], updates.USER_AGENT)
        self.assertNotIn("x-presspeech-version", headers)

    def test_update_check_rejects_every_redirect(self):
        handler = updates._UpdateAPIRedirectHandler()
        request = updates._request(updates.RELEASES_API)
        with self.assertRaises(updates.UpdateError):
            handler.redirect_request(
                request, None, 302, "Found", {},
                "https://api.github.com/unexpected")

    def test_update_check_rejects_an_unexpected_final_url(self):
        def opener(request, timeout):
            return Response(b"[]", "https://example.com/releases")

        with self.assertRaises(updates.UpdateError):
            updates.fetch_update("0.1.0", opener=opener)


class DownloadTests(unittest.TestCase):
    def make_update(self, payload, checksum=None):
        name = "Presspeech-Setup-0.1.1-x64.exe"
        digest = checksum or hashlib.sha256(payload).hexdigest()
        checksum_payload = ("%s  %s\n" % (digest, name)).encode("ascii")
        return {
            "installer_name": name,
            "installer_url": "https://github.com/installer",
            "installer_size": len(payload),
            "installer_digest": digest,
            "checksum_url": "https://github.com/checksum",
            "checksum_size": len(checksum_payload),
            "checksum_digest": hashlib.sha256(checksum_payload).hexdigest(),
        }, digest

    def test_download_is_verified_and_moved_atomically(self):
        payload = b"safe installer"
        update, digest = self.make_update(payload)

        def opener(request, timeout):
            if request.full_url.endswith("checksum"):
                text = "%s  %s\n" % (digest, update["installer_name"])
                return Response(text.encode("ascii"))
            return Response(payload)

        progress = []
        with tempfile.TemporaryDirectory() as directory:
            path = updates.download_update(
                update, directory, lambda done, total: progress.append((done, total)),
                opener=opener)
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), payload)
            self.assertFalse(os.path.exists(path + ".part"))
        self.assertEqual(progress[-1], (len(payload), len(payload)))

    def test_download_does_not_open_the_predictable_legacy_partial_path(self):
        payload = b"safe installer"
        update, digest = self.make_update(payload)

        def opener(request, timeout):
            if request.full_url.endswith("checksum"):
                text = "%s  %s\n" % (digest, update["installer_name"])
                return Response(text.encode("ascii"))
            return Response(payload)

        with tempfile.TemporaryDirectory() as directory:
            legacy_partial = os.path.join(
                directory, update["installer_name"] + ".part")
            marker = b"unrelated user data"
            with open(legacy_partial, "wb") as handle:
                handle.write(marker)

            updates.download_update(update, directory, opener=opener)

            with open(legacy_partial, "rb") as handle:
                self.assertEqual(handle.read(), marker)
            leftovers = [
                name for name in os.listdir(directory)
                if (name.endswith(".part") and
                    name != os.path.basename(legacy_partial))
            ]
            self.assertEqual(leftovers, [])

    def test_mismatched_checksum_is_rejected(self):
        payload = b"tampered"
        update, _digest = self.make_update(payload, checksum="a" * 64)

        def opener(request, timeout):
            if request.full_url.endswith("checksum"):
                text = "%s  %s\n" % ("a" * 64, update["installer_name"])
                return Response(text.encode("ascii"))
            return Response(payload)

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(updates.UpdateError):
                updates.download_update(update, directory, opener=opener)
            self.assertEqual(os.listdir(directory), [])

    def test_checksum_asset_metadata_is_verified_before_parsing(self):
        payload = b"safe installer"
        update, digest = self.make_update(payload)

        def opener(request, timeout):
            if request.full_url.endswith("checksum"):
                text = "%s  %s\n" % (digest, update["installer_name"])
                return Response(text.encode("ascii") + b"x")
            return Response(payload)

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                    updates.UpdateError, "checksum size did not match"):
                updates.download_update(update, directory, opener=opener)
            self.assertEqual(os.listdir(directory), [])

    def test_checksum_asset_digest_is_verified_before_parsing(self):
        payload = b"safe installer"
        update, digest = self.make_update(payload)

        def opener(request, timeout):
            if request.full_url.endswith("checksum"):
                text = "%s *%s\n" % (digest, update["installer_name"])
                return Response(text.encode("ascii"))
            return Response(payload)

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                    updates.UpdateError, "checksum SHA-256 verification failed"):
                updates.download_update(update, directory, opener=opener)
            self.assertEqual(os.listdir(directory), [])

    def test_oversized_download_stops_after_the_first_excess_byte(self):
        payload = b"unexpectedly large installer payload"
        update, digest = self.make_update(payload)
        update["installer_size"] = 4
        installer_response = CountingResponse(payload)

        def opener(request, timeout):
            if request.full_url.endswith("checksum"):
                text = "%s  %s\n" % (digest, update["installer_name"])
                return Response(text.encode("ascii"))
            return installer_response

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                    updates.UpdateError, "exceeded the release size"):
                updates.download_update(update, directory, opener=opener)
            self.assertEqual(installer_response.bytes_read, 5)
            self.assertEqual(os.listdir(directory), [])

    def test_unexpected_download_host_is_rejected(self):
        update, _digest = self.make_update(b"safe")
        update["installer_url"] = "https://example.com/installer.exe"
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(updates.UpdateError):
                updates.download_update(update, directory)

    def test_every_redirect_hop_requires_https_and_an_approved_host(self):
        handler = updates._ReleaseRedirectHandler()
        request = updates._request("https://github.com/installer")
        allowed = "https://release-assets.githubusercontent.com/installer"
        redirected = handler.redirect_request(
            request, None, 302, "Found", {}, allowed)
        self.assertEqual(redirected.full_url, allowed)
        rejected = (
            "https://example.com/intermediate",
            "http://release-assets.githubusercontent.com/intermediate",
        )
        for redirect_url in rejected:
            with self.subTest(redirect_url=redirect_url), \
                    self.assertRaises(updates.UpdateError):
                handler.redirect_request(
                    request, None, 302, "Found", {}, redirect_url)

    def test_installer_is_revalidated_before_launch(self):
        payload = b"safe installer"
        update, _digest = self.make_update(payload)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, update["installer_name"])
            with open(path, "wb") as handle:
                handle.write(payload)
            updates.verify_installer(update, path)

            with open(path, "wb") as handle:
                handle.write(b"evil installer")
            with self.assertRaisesRegex(
                    updates.UpdateError, "changed after verification"):
                updates.verify_installer(update, path)

    def test_launch_revalidation_requires_the_exact_asset_name(self):
        payload = b"safe installer"
        update, _digest = self.make_update(payload)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "renamed-installer.exe")
            with open(path, "wb") as handle:
                handle.write(payload)
            with self.assertRaisesRegex(
                    updates.UpdateError, "metadata was invalid"):
                updates.verify_installer(update, path)

    @unittest.skipUnless(os.name == "nt", "Windows sharing semantics")
    def test_launch_lock_denies_replacement_until_process_creation(self):
        payload = b"safe installer"
        update, _digest = self.make_update(payload)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, update["installer_name"])
            replacement = os.path.join(directory, "replacement.exe")
            with open(path, "wb") as handle:
                handle.write(payload)
            with open(replacement, "wb") as handle:
                handle.write(payload)

            with updates.locked_verified_installer(update, path):
                with self.assertRaises(PermissionError):
                    os.replace(replacement, path)

            os.replace(replacement, path)

    @unittest.skipUnless(os.name == "nt", "Windows sharing semantics")
    def test_launch_lock_allows_windows_to_load_the_executable(self):
        executable = sys.executable
        digest = hashlib.sha256()
        with open(executable, "rb") as handle:
            while True:
                block = handle.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
        update = {
            "installer_name": os.path.basename(executable),
            "installer_size": os.path.getsize(executable),
            "installer_digest": digest.hexdigest(),
        }

        with updates.locked_verified_installer(update, executable):
            result = subprocess.run(
                [executable, "-c", "pass"], check=False, timeout=15)

        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
