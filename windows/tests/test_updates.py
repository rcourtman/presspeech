import hashlib
import io
import json
import os
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


def release(version, complete=True, draft=False):
    installer = "Presspeech-Setup-%s-x64.exe" % version
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

    def test_fetch_uses_fixed_privacy_safe_headers(self):
        seen = {}

        def opener(request, timeout):
            seen["request"] = request
            seen["timeout"] = timeout
            return Response(json.dumps([release("0.1.1")]).encode("utf-8"))

        selected = updates.fetch_update("0.1.0", opener=opener)
        self.assertEqual(selected["version"], "0.1.1")
        headers = {key.lower(): value for key, value in
                   seen["request"].header_items()}
        self.assertEqual(headers["user-agent"], updates.USER_AGENT)
        self.assertNotIn("x-presspeech-version", headers)


class DownloadTests(unittest.TestCase):
    def make_update(self, payload, checksum=None):
        name = "Presspeech-Setup-0.1.1-x64.exe"
        digest = checksum or hashlib.sha256(payload).hexdigest()
        return {
            "installer_name": name,
            "installer_url": "https://github.com/installer",
            "installer_size": len(payload),
            "installer_digest": digest,
            "checksum_url": "https://github.com/checksum",
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

    def test_unexpected_download_host_is_rejected(self):
        update, _digest = self.make_update(b"safe")
        update["installer_url"] = "https://example.com/installer.exe"
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(updates.UpdateError):
                updates.download_update(update, directory)


if __name__ == "__main__":
    unittest.main()
