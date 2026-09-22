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


if __name__ == '__main__':
    unittest.main()
