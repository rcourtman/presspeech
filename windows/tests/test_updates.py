import base64
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import updates


class UpdaterProxyPolicyTests(unittest.TestCase):
    def test_both_default_transports_honor_configured_proxy_without_network(self):
        # A synthetic proxy exercises routing configuration, not a request.
        proxy_url = "http://synthetic-proxy.invalid:8080"
        with mock.patch.object(
                updates.urllib.request, "getproxies",
                return_value={"https": proxy_url}) as discover:
            for handler in (updates._UpdateAPIRedirectHandler(),
                            updates._ReleaseRedirectHandler()):
                with self.subTest(handler=type(handler).__name__):
                    open_method = updates._configured_proxy_opener(handler)
                    configured = [
                        item for item in open_method.__self__.handlers
                        if isinstance(item, updates.urllib.request.ProxyHandler)
                    ]
                    self.assertEqual(len(configured), 1)
                    self.assertEqual(configured[0].proxies, {"https": proxy_url})
                    self.assertIn(handler, open_method.__self__.handlers)
        self.assertEqual(discover.call_count, 2)

    def test_network_inventory_discloses_both_windows_update_proxy_paths(self):
        root = Path(__file__).resolve().parents[2]
        inventory = json.loads((root / "docs/privacy/network-calls.json")
                               .read_text(encoding="utf-8"))
        calls = {entry["name"]: entry for entry in inventory["network_calls"]}
        for name in ("windows_update_check", "user_triggered_install_or_update"):
            with self.subTest(name=name):
                routing = calls[name]["proxy_routing"]
                self.assertIn("urllib.request.ProxyHandler", routing)
                self.assertIn("https_proxy/HTTPS_PROXY", routing)
                self.assertIn("Windows Internet Settings", routing)
                self.assertIn("TLS-inspecting", routing)
        page = (root / "docs/privacy.html").read_text(encoding="utf-8")
        self.assertIn('id="windows-updater-proxy"', page)
        self.assertIn("does <strong>not</strong> independently verify", page)


class Response(io.BytesIO):
    def __init__(self, payload, url="https://github.com/file", headers=None):
        super().__init__(payload)
        self.url = url
        self.headers = headers or {}

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
        "immutable": not draft,
        "html_url": "https://github.com/release/" + version,
        "assets": assets,
    }


class UpdateSelectionTests(unittest.TestCase):
    def test_user_facing_errors_hide_arbitrary_exception_details(self):
        private_detail = "proxy-password=synthetic-private-marker"
        self.assertEqual(
            updates.user_facing_error(
                RuntimeError(private_detail), "Update operation failed."),
            "Update operation failed.",
        )
        self.assertEqual(
            updates.user_facing_error(
                updates.UpdateError("release checksum verification failed"),
                "Update operation failed."),
            "release checksum verification failed",
        )

    def test_version_parser_accepts_release_tag_and_plain_version(self):
        self.assertEqual(updates.parse_version("windows-v1.2.3"), (1, 2, 3))
        self.assertEqual(updates.parse_version("1.2.3"), (1, 2, 3))

    def test_version_parser_rejects_noncanonical_leading_zeroes(self):
        for value in ("windows-v01.2.3", "1.02.3", "1.2.03"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                updates.parse_version(value)

    def test_ignores_noncanonical_windows_release_tag(self):
        candidate = release("0.1.1")
        candidate["tag_name"] = "windows-v00.1.1"
        for asset in candidate["assets"]:
            asset["browser_download_url"] = asset["browser_download_url"].replace(
                "windows-v0.1.1", "windows-v00.1.1")
        self.assertIsNone(updates.select_update([candidate], "0.1.0"))

    def test_ignores_plain_release_tag_even_with_matching_assets(self):
        candidate = release("0.1.1")
        candidate["tag_name"] = "0.1.1"
        for asset in candidate["assets"]:
            asset["browser_download_url"] = (
                asset["browser_download_url"].replace(
                    "windows-v0.1.1/", "0.1.1/"))
        self.assertIsNone(updates.select_update([candidate], "0.1.0"))

    def test_version_parser_rejects_unicode_digits(self):
        for version in ("1١.1.1", "windows-v1١.1.1"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                updates.parse_version(version)

    def test_ignores_release_without_explicit_published_draft_state(self):
        for state in (None, 0, ""):
            with self.subTest(draft=state):
                candidate = release("0.1.1")
                candidate["draft"] = state
                self.assertIsNone(
                    updates.select_update([candidate], "0.1.0"))
        candidate = release("0.1.1")
        del candidate["draft"]
        self.assertIsNone(updates.select_update([candidate], "0.1.0"))

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

    def test_ignores_mutable_or_unreported_release(self):
        mutable = release("0.1.1")
        mutable["immutable"] = False
        unreported = release("0.1.2")
        del unreported["immutable"]
        self.assertIsNone(
            updates.select_update([mutable, unreported], "0.1.0"))

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
        self.assertEqual(headers["x-github-api-version"], updates.API_VERSION)
        self.assertNotIn("x-presspeech-version", headers)

    def test_fetch_failure_does_not_expose_exception_text(self):
        private_detail = "proxy-password=synthetic-private-marker"

        def opener(_request, timeout):
            raise RuntimeError(private_detail)

        with self.assertRaises(updates.UpdateError) as raised:
            updates.fetch_update("0.1.0", opener=opener)
        self.assertEqual(
            str(raised.exception), "could not check GitHub releases")
        self.assertNotIn(private_detail, str(raised.exception))

    def test_fetch_traverses_canonical_release_pages(self):
        first_page = [
            {
                "tag_name": "v9.9.%d" % index,
                "draft": False,
                "prerelease": False,
                "assets": [],
            }
            for index in range(100)
        ]
        second_url = updates.RELEASES_API + "&page=2"
        seen = []

        def opener(request, timeout):
            seen.append(request.full_url)
            if request.full_url == updates.RELEASES_API:
                return Response(
                    json.dumps(first_page).encode("utf-8"),
                    updates.RELEASES_API,
                    {"Link": (
                        '<%s>; rel="next", <%s>; rel="last"' %
                        (second_url, second_url)
                    )},
                )
            return Response(
                json.dumps([release("0.1.1")]).encode("utf-8"), second_url)

        selected = updates.fetch_update("0.1.0", opener=opener)

        self.assertEqual(selected["version"], "0.1.1")
        self.assertEqual(seen, [updates.RELEASES_API, second_url])

    def test_fetch_rejects_untrusted_or_skipped_pagination(self):
        page = json.dumps([release("0.1.1")]).encode("utf-8")
        bad_targets = (
            "https://example.com/releases?per_page=100&page=2",
            updates.RELEASES_API + "&page=3",
            updates.RELEASES_API + "&page=2&token=secret",
            "http://api.github.com" + updates.RELEASES_API_PATH +
            "?per_page=100&page=2",
        )
        for target in bad_targets:
            with self.subTest(target=target):
                def opener(request, timeout):
                    return Response(
                        page, updates.RELEASES_API,
                        {"Link": '<%s>; rel="next"' % target},
                    )

                with self.assertRaisesRegex(
                        updates.UpdateError, "pagination URL"):
                    updates.fetch_update("0.1.0", opener=opener)

    def test_fetch_rejects_ambiguous_or_unbounded_pagination(self):
        payload = json.dumps([]).encode("utf-8")

        def ambiguous(request, timeout):
            target = updates.RELEASES_API + "&page=2"
            return Response(
                payload, updates.RELEASES_API,
                {"Link": '<%s>; rel="next", <%s>; rel="next"' %
                 (target, target)},
            )

        with self.assertRaisesRegex(updates.UpdateError, "ambiguous"):
            updates.fetch_update("0.1.0", opener=ambiguous)

        def unbounded(request, timeout):
            page = len(seen) + 1
            seen.append(page)
            next_url = updates.RELEASES_API + "&page=%d" % (page + 1)
            return Response(
                payload, request.full_url,
                {"Link": '<%s>; rel="next"' % next_url},
            )

        seen = []
        with self.assertRaisesRegex(updates.UpdateError, "safe page limit"):
            updates.fetch_update("0.1.0", opener=unbounded)
        self.assertEqual(len(seen), updates.MAX_RELEASE_PAGES)

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
    def wait_for_cleanup_helper(self, helper, timeout=60):
        # Hosted runners have taken almost 15 seconds for an unlocked cleanup.
        # Keep a bounded native check, and always reap our PowerShell helper
        # before its private files are removed, including on assertion failure.
        try:
            try:
                return_code = helper.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.fail("update cleanup helper exceeded its %s-second deadline"
                          % timeout)
            self.assertEqual(return_code, 0, "update cleanup helper failed")
        finally:
            if helper.poll() is None:
                helper.kill()
            helper.wait(timeout=10)

    def test_cleanup_fixture_reaps_a_helper_that_exceeds_its_deadline(self):
        helper = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        with self.assertRaisesRegex(AssertionError, "exceeded its .* deadline"):
            self.wait_for_cleanup_helper(helper, timeout=0.1)
        self.assertIsNotNone(helper.returncode)

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

    def test_checksum_download_error_does_not_expose_exception_text(self):
        private_detail = "proxy-password=synthetic-private-marker"
        update, _digest = self.make_update(b"safe installer")

        def opener(_request, timeout):
            raise RuntimeError(private_detail)

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(updates.UpdateError) as raised:
                updates.download_update(
                    update, directory, opener=opener)
        self.assertEqual(
            str(raised.exception), "could not download the release checksum")
        self.assertNotIn(private_detail, str(raised.exception))

    def test_installer_download_error_does_not_expose_exception_text(self):
        private_detail = "proxy-password=synthetic-private-marker"
        payload = b"safe installer"
        update, digest = self.make_update(payload)

        def opener(request, timeout):
            if request.full_url.endswith("checksum"):
                return Response(
                    ("%s  %s\n" %
                     (digest, update["installer_name"])).encode("ascii"))
            raise RuntimeError(private_detail)

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(updates.UpdateError) as raised:
                updates.download_update(
                    update, directory, opener=opener)
        self.assertEqual(
            str(raised.exception), "could not download the installer")
        self.assertNotIn(private_detail, str(raised.exception))

    def test_download_is_verified_and_moved_atomically(self):
        payload = b"safe installer"
        update, digest = self.make_update(payload)

        def opener(request, timeout):
            if request.full_url.endswith("checksum"):
                text = "%s  %s\n" % (digest, update["installer_name"])
                return Response(text.encode("ascii"))
            return Response(payload)

        progress = []
        staging = []
        with tempfile.TemporaryDirectory() as directory:
            path = updates.download_update(
                update, directory, lambda done, total: progress.append((done, total)),
                opener=opener,
                staging=lambda partial: staging.append(
                    (partial, os.path.exists(partial))))
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), payload)
            self.assertFalse(os.path.exists(path + ".part"))
        self.assertEqual(progress[-1], (len(payload), len(payload)))
        self.assertEqual(len(staging), 1)
        self.assertTrue(staging[0][1])
        self.assertTrue(staging[0][0].endswith(".part"))

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

    def test_cancelled_download_stops_after_the_current_read(self):
        payload = b"x" * (2 * 1024 * 1024)
        update, digest = self.make_update(payload)
        installer_response = CountingResponse(payload)

        def opener(request, timeout):
            if request.full_url.endswith("checksum"):
                text = "%s  %s\n" % (digest, update["installer_name"])
                return Response(text.encode("ascii"))
            return installer_response

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                    updates.UpdateError, "download was cancelled"):
                updates.download_update(
                    update, directory, opener=opener,
                    cancelled=lambda: installer_response.bytes_read > 0)
            self.assertEqual(installer_response.bytes_read, 1024 * 1024)
            self.assertEqual(os.listdir(directory), [])

    def test_cancelled_download_does_not_create_a_staging_file(self):
        payload = b"safe installer"
        update, digest = self.make_update(payload)
        cancelled = {"value": False}

        class CancellingResponse(Response):
            def geturl(self):
                cancelled["value"] = True
                return super().geturl()

        def opener(request, timeout):
            if request.full_url.endswith("checksum"):
                text = "%s  %s\n" % (digest, update["installer_name"])
                return Response(text.encode("ascii"))
            return CancellingResponse(payload)

        staged = []
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                    updates.UpdateError, "download was cancelled"):
                updates.download_update(
                    update, directory, opener=opener,
                    cancelled=lambda: cancelled["value"],
                    staging=staged.append)
            self.assertEqual(staged, [])
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
            "https://user@release-assets.githubusercontent.com/intermediate",
            "https://user:secret@release-assets.githubusercontent.com/intermediate",
            "https://release-assets.githubusercontent.com:444/intermediate",
            "https://release-assets.githubusercontent.com:not-a-port/intermediate",
        )
        for redirect_url in rejected:
            with self.subTest(redirect_url=redirect_url), \
                    self.assertRaises(updates.UpdateError):
                handler.redirect_request(
                    request, None, 302, "Found", {}, redirect_url)

        standard_port = "https://release-assets.githubusercontent.com:443/file"
        self.assertEqual(
            updates._checked_download_url(standard_port), standard_port)

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

    def test_cleanup_helper_is_limited_to_private_update_directories(self):
        launched = mock.Mock()
        with tempfile.TemporaryDirectory(
                prefix="Presspeech user's ") as temp_root, \
                mock.patch.object(updates.tempfile, "gettempdir",
                                  return_value=temp_root):
            directory = tempfile.mkdtemp(
                prefix=updates.UPDATE_DIRECTORY_PREFIX, dir=temp_root)
            path = os.path.join(
                directory, "Presspeech-Setup-1.2.3-x64.exe")

            updates.schedule_installer_cleanup(path, launcher=launched)

            command = launched.call_args.args[0]
            script = base64.b64decode(
                command[-1]).decode("utf-16le")
            self.assertEqual(command[0], "powershell.exe")
            self.assertIn("-EncodedCommand", command)
            self.assertIn("Presspeech user''s ", script)
            self.assertIn("Remove-Item -LiteralPath $installer", script)
            self.assertIn("Remove-Item -LiteralPath $directory", script)
            self.assertNotIn("-Recurse", script)
            self.assertTrue(launched.call_args.kwargs["close_fds"])

            outside = os.path.join(
                temp_root, "ordinary", "Presspeech-Setup-1.2.3-x64.exe")
            with self.assertRaisesRegex(
                    updates.UpdateError, "unexpected installer directory"):
                updates.schedule_installer_cleanup(
                    outside, launcher=launched)

    def test_abandoned_download_cleanup_targets_only_its_exact_paths(self):
        launched = mock.Mock()
        with tempfile.TemporaryDirectory(
                prefix="Presspeech user's ") as temp_root, \
                mock.patch.object(updates.tempfile, "gettempdir",
                                  return_value=temp_root):
            directory = tempfile.mkdtemp(
                prefix=updates.UPDATE_DIRECTORY_PREFIX, dir=temp_root)
            partial = os.path.join(
                directory,
                "Presspeech-Setup-1.2.3-x64.exe.random123.part")

            updates.schedule_abandoned_download_cleanup(
                partial, launcher=launched)

            command = launched.call_args.args[0]
            script = base64.b64decode(
                command[-1]).decode("utf-16le")
            self.assertIn("Presspeech user''s ", script)
            self.assertIn("$partial = ", script)
            self.assertIn("$installer = ", script)
            self.assertIn("random123.part", script)
            self.assertNotIn("-Recurse", script)
            self.assertTrue(launched.call_args.kwargs["close_fds"])

            unexpected = os.path.join(directory, "unrelated.part")
            with self.assertRaisesRegex(
                    updates.UpdateError, "unexpected download path"):
                updates.schedule_abandoned_download_cleanup(
                    unexpected, launcher=launched)

    @unittest.skipUnless(os.name == "nt", "Windows cleanup helper")
    def test_abandoned_download_cleanup_removes_partial_and_directory(self):
        directory = tempfile.mkdtemp(prefix=updates.UPDATE_DIRECTORY_PREFIX)
        partial = os.path.join(
            directory,
            "Presspeech-Setup-1.2.3-x64.exe.random123.part")
        try:
            with open(partial, "wb") as handle:
                handle.write(b"partial installer")

            helper = updates.schedule_abandoned_download_cleanup(partial)
            self.wait_for_cleanup_helper(helper)
            deadline = time.monotonic() + 2
            while os.path.exists(directory) and time.monotonic() < deadline:
                time.sleep(0.05)

            self.assertFalse(os.path.exists(partial))
            self.assertFalse(os.path.exists(directory))
        finally:
            try:
                os.remove(partial)
            except OSError:
                pass
            try:
                os.rmdir(directory)
            except OSError:
                pass

    @unittest.skipUnless(os.name == "nt", "Windows cleanup helper")
    def test_cleanup_helper_removes_released_installer_and_directory(self):
        directory = tempfile.mkdtemp(prefix=updates.UPDATE_DIRECTORY_PREFIX)
        path = os.path.join(directory, "Presspeech-Setup-1.2.3-x64.exe")
        try:
            with open(path, "wb") as handle:
                handle.write(b"verified installer")

            helper = updates.schedule_installer_cleanup(path)
            self.wait_for_cleanup_helper(helper)
            deadline = time.monotonic() + 2
            while os.path.exists(directory) and time.monotonic() < deadline:
                time.sleep(0.05)

            self.assertFalse(os.path.exists(path))
            self.assertFalse(os.path.exists(directory))
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
            try:
                os.rmdir(directory)
            except OSError:
                pass

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
    def test_launch_lock_denies_parent_path_swap_until_process_creation(self):
        payload = b"safe installer"
        update, _digest = self.make_update(payload)
        with tempfile.TemporaryDirectory() as root:
            directory = os.path.join(root, "approved")
            moved = os.path.join(root, "moved")
            os.mkdir(directory)
            path = os.path.join(directory, update["installer_name"])
            with open(path, "wb") as handle:
                handle.write(payload)

            with updates.locked_verified_installer(update, path):
                with self.assertRaises(PermissionError):
                    os.replace(directory, moved)

            os.replace(directory, moved)

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
