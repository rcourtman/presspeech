#!/usr/bin/env python3
"""Offline transport regressions; never use real GitHub credentials or network."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('release_check', Path(__file__).with_name('check-public-releases.py'))
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class GhTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.command = self.directory / 'gh'
        self.env = patch.dict(os.environ, {'PATH': str(self.directory), 'GH_HOST': 'wrong.example'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.url = check.API_ROOT + '/releases/latest'

    def executable(self, body):
        self.command.write_text('#!' + sys.executable + '\n' + body)
        self.command.chmod(0o755)

    def test_fixed_host_repository_get_and_headers(self):
        self.executable('import json,sys\nprint(json.dumps(sys.argv[1:]))\n')
        args = json.loads(check.github_api_via_gh(self.url))
        self.assertEqual(args, ['api', 'repos/rcourtman/presspeech/releases/latest',
                               '--hostname', 'github.com', '--method', 'GET',
                               '--header', 'Accept: application/vnd.github+json',
                               '--header', f'X-GitHub-Api-Version: {check.API_VERSION}'])

    def test_rejects_other_repositories_hosts_assets_and_pagination_escape(self):
        for url in [self.url.replace('presspeech', 'other'), self.url.replace('https:', 'http:'),
                    self.url.replace('api.github.com', 'evil.example'),
                    check.DOWNLOAD_ROOT + '/v1.0.0/Presspeech.zip.sha256',
                    check.API_ROOT + '/releases/../issues', self.url + '#fragment',
                    check.API_ROOT + '/releases?per_page=100&page=101',
                    check.API_ROOT + '/releases?per_page=100&page=1&redirect=evil',
                    check.API_ROOT + '/releases?per_page=100&page=0']:
            with self.subTest(url=url), patch.object(check.subprocess, 'Popen') as spawn:
                with self.assertRaisesRegex(check.ReleaseCheckError, 'only accepts'):
                    check.github_api_via_gh(url)
                spawn.assert_not_called()

    def test_response_exact_limit_allowed(self):
        self.executable('import sys\nsys.stdout.write("x" * 128)\n')
        self.assertEqual(check.github_api_via_gh(self.url, limit=128), b'x' * 128)

    def test_output_is_bounded_and_process_reaped(self):
        self.executable('import os\nwhile True: os.write(1, b"x" * 65536)\n')
        spawn = check.subprocess.Popen
        processes = []
        def tracked(*args, **kwargs):
            process = spawn(*args, **kwargs)
            processes.append(process)
            return process
        with patch.object(check.subprocess, 'Popen', side_effect=tracked):
            with self.assertRaisesRegex(check.ReleaseCheckError, 'exceeded 128 bytes'):
                check.github_api_via_gh(self.url, limit=128, timeout=3)
        self.assertIsNotNone(processes[0].poll())

    def test_timeout_with_open_stdout(self):
        self.executable('import time\ntime.sleep(10)\n')
        start = time.monotonic()
        with self.assertRaisesRegex(check.ReleaseCheckError, 'timed out'):
            check.github_api_via_gh(self.url, timeout=0.1)
        self.assertLess(time.monotonic() - start, 2)

    def test_timeout_after_stdout_closes(self):
        self.executable('import os,time\nos.close(1)\ntime.sleep(10)\n')
        with self.assertRaisesRegex(check.ReleaseCheckError, 'timed out'):
            check.github_api_via_gh(self.url, timeout=0.1)

    def test_nonzero_does_not_relay_diagnostics_or_fallback(self):
        self.executable('import sys\nprint("sensitive-header", file=sys.stderr)\nprint("{}")\nsys.exit(7)\n')
        with patch.object(check, 'github_request') as https:
            with self.assertRaisesRegex(check.ReleaseCheckError, 'exit 7') as failure:
                check.github_json(self.url, '', via_gh=True)
            self.assertNotIn('sensitive-header', str(failure.exception))
            https.assert_not_called()

    def test_missing_gh_is_actionable(self):
        with self.assertRaisesRegex(check.ReleaseCheckError, 'could not start gh'):
            check.github_api_via_gh(self.url)

    def test_invalid_json_and_encoding_fail_closed(self):
        for data in [b'not json', b'\xff']:
            with patch.object(check, 'github_api_via_gh', return_value=data):
                with self.assertRaisesRegex(check.ReleaseCheckError, 'invalid JSON'):
                    check.github_json(self.url, '', via_gh=True)

    def test_default_https_path_preserves_token(self):
        with patch.object(check, 'github_request', return_value=b'{}') as https, \
             patch.object(check, 'github_api_via_gh') as gh:
            self.assertEqual(check.github_json(self.url, 'test-token'), {})
            https.assert_called_once_with(self.url, token='test-token')
            gh.assert_not_called()

    def test_checksum_asset_api_fallback_uses_octet_stream_without_cross_host_auth(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            @staticmethod
            def read(_limit):
                return b'checksum'

        requests = []

        class Opener:
            @staticmethod
            def open(request, timeout):
                self.assertEqual(timeout, 30)
                requests.append(request)
                return Response()

        api_asset_url = check.API_ROOT + '/releases/assets/12345'
        download_url = check.DOWNLOAD_ROOT + '/v1.2.3/Presspeech.zip.sha256'
        with patch.object(check.urllib.request, 'build_opener', return_value=Opener()):
            self.assertEqual(
                check.github_checksum_request(api_asset_url, token='fixture-token'),
                b'checksum',
            )
            self.assertEqual(check.github_checksum_request(download_url, token='fixture-token'),
                             b'checksum')

        self.assertEqual(requests[0].get_header('Accept'), 'application/octet-stream')
        self.assertEqual(requests[0].get_header('Authorization'), 'Bearer fixture-token')
        self.assertIsNone(requests[1].get_header('Authorization'))
        self.assertIsNone(requests[1].get_header('Accept'))

    def test_gh_pagination_uses_backend_for_every_page(self):
        with patch.object(check, 'github_api_via_gh', side_effect=[json.dumps([{}]*100).encode(), b'[]']) as gh:
            self.assertEqual(len(check.github_releases('', via_gh=True)), 100)
            self.assertEqual([call.args[0] for call in gh.call_args_list],
                             [check.API_ROOT + f'/releases?per_page=100&page={page}' for page in (1, 2)])

    def test_pagination_limit_and_non_array_rejected(self):
        with patch.object(check, 'MAX_RELEASE_PAGES', 2), \
             patch.object(check, 'github_api_via_gh', return_value=json.dumps([{}]*100).encode()) as gh:
            with self.assertRaisesRegex(check.ReleaseCheckError, 'pagination limit'):
                check.github_releases('', via_gh=True)
            self.assertEqual(gh.call_count, 2)
        with patch.object(check, 'github_api_via_gh', return_value=b'{}'):
            with self.assertRaisesRegex(check.ReleaseCheckError, 'not an array'):
                check.github_releases('', via_gh=True)

    def test_cli_only_routes_json_via_gh_and_checksums_stay_public(self):
        def validate(metadata, mac, releases, checksum_loader, *, require_published):
            self.assertTrue(require_published)
            self.assertEqual(checksum_loader(check.DOWNLOAD_ROOT + '/checksum'), b'checksum')
            return [], []
        with patch.object(sys, 'argv', ['check', '--github-api-via-gh', '--require-published']), \
             patch.object(check, 'load_metadata', return_value={}), \
             patch.object(check, 'github_api_via_gh', side_effect=[b'{}', b'[]']) as gh, \
             patch.object(check, 'github_request', return_value=b'checksum') as https, \
             patch.object(check, 'public_release_errors', side_effect=validate), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(check.main(), 0)
            self.assertEqual(gh.call_count, 2)
            https.assert_called_once_with(check.DOWNLOAD_ROOT + '/checksum', limit=check.MAX_CHECKSUM_BYTES)

    def test_notes_only_audits_public_bodies_without_asset_or_candidate_checks(self):
        mac = {'tag_name': 'v1.2.3', 'draft': False, 'body': 'mac note'}
        releases = [{'tag_name': 'windows-v4.5.6', 'draft': False, 'body': 'Windows note'}]
        for note_errors, expected_status in [([], 0), (['v1.2.3 public notes differ'], 1)]:
            with self.subTest(note_errors=note_errors), \
                 patch.object(sys, 'argv', ['check', '--notes-only']), \
                 patch.object(check, 'github_json', return_value=mac) as latest, \
                 patch.object(check, 'github_releases', return_value=releases) as listed, \
                 patch.object(check, 'release_note_parity_errors', return_value=note_errors) as parity, \
                 patch.object(check, 'known_release_disclosure_errors', return_value=[]) as disclosure, \
                 patch.object(check, 'load_metadata') as metadata, \
                 patch.object(check, 'public_release_errors') as assets, \
                 contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(check.main(), expected_status)
                latest.assert_called_once()
                listed.assert_called_once()
                parity.assert_called_once_with(mac, releases)
                disclosure.assert_called_once_with(releases)
                metadata.assert_not_called()
                assets.assert_not_called()

    def test_notes_only_fails_when_public_disclosure_is_missing_despite_parity(self):
        mac = {'tag_name': 'v0.3.8', 'draft': False, 'body': 'mac note'}
        releases = [mac, {'tag_name': 'windows-v0.1.12', 'draft': False, 'body': 'Windows note'}]
        with patch.object(sys, 'argv', ['check', '--notes-only']), \
             patch.object(check, 'github_json', return_value=mac), \
             patch.object(check, 'github_releases', return_value=releases), \
             patch.object(check, 'release_note_parity_errors', return_value=[]), \
             contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(check.main(), 1)
        self.assertIn('v0.3.8 public release notes lack', stderr.getvalue())
        self.assertIn('windows-v0.1.12 public release notes lack', stderr.getvalue())

    def test_tracked_known_risk_notes_satisfy_disclosure_markers(self):
        root = Path(__file__).resolve().parents[1]
        releases = [
            {'tag_name': 'v0.3.8', 'draft': False,
             'body': (root / 'swift/release-notes/v0.3.8.md').read_text()},
            {'tag_name': 'windows-v0.1.12', 'draft': False,
             'body': (root / 'windows/release-notes/0.1.12.md').read_text()},
        ]
        self.assertEqual(check.known_release_disclosure_errors(releases), [])

    def test_windows_disclosure_accepts_launch_and_route_alternatives(self):
        mac = {'tag_name': 'v0.3.8', 'draft': False,
               'body': 'Before opening model Hugging Face token wait 0.3.9 privacy.html#network-calls'}
        windows = {'tag_name': 'windows-v0.1.12', 'draft': False,
                   'body': 'Before launching model Hugging Face token telemetry custom route wait '
                           '0.1.13 windows.html#model-download-privacy'}
        self.assertEqual(check.known_release_disclosure_errors([mac, windows]), [])
        windows['body'] = windows['body'].replace('Before launching', 'Ready to use').replace('route', 'path')
        errors = check.known_release_disclosure_errors([mac, windows])
        self.assertEqual(len(errors), 1)
        self.assertIn('before opening / before launching', errors[0])
        self.assertIn('routing / route', errors[0])


if __name__ == '__main__':
    unittest.main()
