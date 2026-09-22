#!/usr/bin/env python3
"""Exercise production cask preflight functions with disposable Git repositories."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / 'ship-swift.sh').read_text()
# Load the actual implementation, not a second model of its decisions. No
# release entry point is evaluated and all Git writes stay in temporary repos.
FUNCTIONS = SOURCE[SOURCE.index('# Only the canonical tap'):SOURCE.index('# Cask update + verification')]
CANONICAL = 'https://github.com/rcourtman/homebrew-presspeech.git'


class CaskPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull,
                    'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0',
                    'LC_ALL': 'C'}
        # Tests must not inherit caller Git repository/config selection.
        for key in list(self.env):
            if key.startswith('GIT_') and key not in {
                    'GIT_CONFIG_GLOBAL', 'GIT_CONFIG_NOSYSTEM', 'GIT_TERMINAL_PROMPT'}:
                del self.env[key]
        self.remote = self.root / 'remote.git'
        self.seed = self.root / 'seed'
        self.local = self.root / 'local'
        self.git(self.root, 'init', '--bare', '--initial-branch=main', str(self.remote))
        self.git(self.root, 'clone', str(self.remote), str(self.seed))
        self.identity(self.seed)
        (self.seed / 'Casks').mkdir()
        (self.seed / 'Casks/presspeech.rb').write_text('version "0.3.6"\n')
        self.commit(self.seed, 'initial cask')
        self.git(self.seed, 'push', 'origin', 'main')
        self.git(self.root, 'clone', str(self.remote), str(self.local))
        self.identity(self.local)
        self.before = self.head(self.local)
        self.driver = self.root / 'driver.sh'
        self.driver.write_text('set -euo pipefail\n'
                               'die() { printf "%s\\n" "$*" >&2; exit 1; }\n'
                               'say() { :; }\n' + FUNCTIONS + '\n"$@"\n')

    def tearDown(self):
        self.temp.cleanup()

    def git(self, repo, *args, check=True):
        return subprocess.run(['git', '-C', str(repo), *args], env=self.env,
                              text=True, capture_output=True, check=check)

    def identity(self, repo):
        self.git(repo, 'config', 'user.name', 'Test Maintainer')
        self.git(repo, 'config', 'user.email', 'test@example.invalid')

    def commit(self, repo, subject):
        self.git(repo, 'add', '.')
        self.git(repo, 'commit', '-m', subject)

    def head(self, repo):
        return self.git(repo, 'rev-parse', 'HEAD').stdout.strip()

    def advance_remote(self):
        (self.seed / 'Casks/presspeech.rb').write_text('version "0.3.7"\n')
        self.commit(self.seed, 'update remote cask')
        self.git(self.seed, 'push', 'origin', 'main')
        return self.head(self.seed)

    def run_function(self, name, *, success, extra=None):
        env = {**self.env, 'CASK_TAP': str(self.local),
               'CASK_FILE': str(self.local / 'Casks/presspeech.rb'), **(extra or {})}
        result = subprocess.run(['bash', str(self.driver), name], env=env,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, success, result.stderr)
        return result

    def test_old_stale_checkout_passed_then_push_failed_after_commit(self):
        self.advance_remote()
        # Old preflight only tested clean tracked changes, so this passed.
        self.git(self.local, 'diff-index', '--quiet', 'HEAD', '--')
        (self.local / 'Casks/presspeech.rb').write_text('version "0.3.8"\n')
        self.commit(self.local, 'attempted post-publication cask update')
        self.assertNotEqual(self.git(self.local, 'push', 'origin', 'main', check=False).returncode, 0)
        self.assertNotEqual(self.head(self.local), self.before)

    def test_clean_behind_fast_forwards_before_later_push(self):
        wanted = self.advance_remote()
        self.run_function('synchronize_cask_main', success=True)
        self.assertEqual(self.head(self.local), wanted)
        (self.local / 'Casks/presspeech.rb').write_text('version "0.3.8"\n')
        self.commit(self.local, 'prepared cask update')
        self.git(self.local, 'push', 'origin', 'main')
        self.assertEqual(self.git(self.remote, 'rev-parse', 'main').stdout.strip(), self.head(self.local))

    def test_already_published_cask_only_sync_is_idempotent(self):
        self.run_function('synchronize_cask_main', success=True, extra={'CASK_ONLY': '1'})
        self.run_function('synchronize_cask_main', success=True, extra={'CASK_ONLY': '1'})
        self.assertEqual(self.head(self.local), self.before)
        self.assertEqual(self.git(self.local, 'status', '--porcelain').stdout, '')

    def test_cask_only_existing_published_stage_does_not_create_commit(self):
        digest = 'b' * 64
        (self.seed / 'Casks/presspeech.rb').write_text(
            'cask "presspeech" do\n  version "0.3.8"\n  sha256 "' + digest + '"\nend\n')
        self.commit(self.seed, 'published cask')
        self.git(self.seed, 'push', 'origin', 'main')
        published = self.head(self.seed)
        # Execute the production synchronization and complete existing cask
        # stage twice. Only Homebrew download/cache commands are stubbed;
        # clone/fetch/fast-forward/diff/push/remote verification use real Git.
        prefix = SOURCE[:SOURCE.index('if [[ "$SELF_TEST" -eq 1 ]]; then')]
        driver = self.root / 'resume-stage.sh'
        driver.write_text(prefix + '\nCASK_ONLY=1\n'
                          'brew() { if [[ "$1" == "info" ]]; then echo "Presspeech 0.3.8"; fi; }\n'
                          'synchronize_cask_main\nrun_cask_stage 0.3.8 ' + digest + '\n')
        env = {**self.env, 'PRESSPEECH_HOMEBREW_TAP': str(self.local)}
        for _ in range(2):
            result = subprocess.run(['bash', str(driver)], env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.head(self.local), published)
            self.assertEqual(self.git(self.local, 'status', '--porcelain').stdout, '')

    def test_ahead_and_divergent_preserve_commits_and_work(self):
        (self.local / 'local-work.txt').write_text('owned work\n')
        self.commit(self.local, 'unpublished local work')
        ahead = self.head(self.local)
        for diverged in [False, True]:
            if diverged:
                self.advance_remote()
            for mode in ['0', '1']:
                result = self.run_function('synchronize_cask_main', success=False, extra={'CASK_ONLY': mode})
                self.assertIn('ahead of or diverged', result.stderr)
                self.assertEqual(self.head(self.local), ahead)
                self.assertEqual((self.local / 'local-work.txt').read_text(), 'owned work\n')

    def test_dirty_staged_untracked_and_wrong_branch_refused_before_fetch(self):
        self.advance_remote()
        for kind in ['dirty', 'staged', 'untracked', 'branch', 'detached']:
            with self.subTest(kind=kind):
                # Reset affects only this disposable test repository.
                self.git(self.local, 'reset', '--hard', self.before)
                self.git(self.local, 'clean', '-fd')
                self.git(self.local, 'checkout', 'main')
                if kind in ['dirty', 'staged']:
                    (self.local / 'Casks/presspeech.rb').write_text('owned change\n')
                    if kind == 'staged':
                        self.git(self.local, 'add', 'Casks/presspeech.rb')
                elif kind == 'untracked':
                    (self.local / 'private-notes').write_text('owned notes\n')
                elif kind == 'branch':
                    self.git(self.local, 'checkout', '-b', 'topic')
                else:
                    self.git(self.local, 'checkout', '--detach')
                state = self.git(self.local, 'status', '--porcelain').stdout
                self.run_function('synchronize_cask_main', success=False)
                self.assertEqual(self.head(self.local), self.before)
                self.assertEqual(self.git(self.local, 'status', '--porcelain').stdout, state)
                self.assertEqual(self.git(self.local, 'rev-parse', 'origin/main').stdout.strip(), self.before)

    def test_in_progress_and_missing_remote_main_refused(self):
        marker = self.local / '.git/MERGE_HEAD'
        marker.write_text(self.before + '\n')
        self.run_function('synchronize_cask_main', success=False)
        self.assertTrue(marker.exists())
        marker.unlink()
        self.git(self.remote, 'symbolic-ref', 'HEAD', 'refs/heads/elsewhere')
        self.git(self.remote, 'update-ref', '-d', 'refs/heads/main')
        self.run_function('synchronize_cask_main', success=False)
        self.assertEqual(self.head(self.local), self.before)

    def test_canonical_urls_and_wrong_push_credentials_redacted(self):
        for url in [CANONICAL, 'git@github.com:rcourtman/homebrew-presspeech.git',
                    'ssh://git@github.com/rcourtman/homebrew-presspeech']:
            self.git(self.local, 'remote', 'set-url', 'origin', url)
            self.run_function('validate_cask_origin', success=True)
        bad = 'https://PRIVATE_USER:PRIVATE_TOKEN@github.com/another/tap.git'
        self.git(self.local, 'remote', 'set-url', '--push', 'origin', bad)
        result = self.run_function('validate_cask_origin', success=False)
        self.assertNotIn('PRIVATE', result.stdout + result.stderr)
        self.assertEqual(self.git(self.local, 'config', 'remote.origin.pushurl').stdout.strip(), bad)
        self.assertEqual(self.head(self.local), self.before)

    def test_multiple_destinations_rewrites_and_mirror_refused(self):
        self.git(self.local, 'remote', 'set-url', 'origin', CANONICAL)
        self.git(self.local, 'config', '--add', 'remote.origin.pushurl', CANONICAL)
        self.git(self.local, 'config', '--add', 'remote.origin.pushurl', CANONICAL)
        self.run_function('validate_cask_origin', success=False)
        self.git(self.local, 'config', '--unset-all', 'remote.origin.pushurl')
        self.git(self.local, 'config', 'url.file:///PRIVATE_PATH/.insteadOf', 'https://github.com/')
        result = self.run_function('validate_cask_origin', success=False)
        self.assertNotIn('PRIVATE', result.stderr)
        self.git(self.local, 'config', '--unset-all', 'url.file:///PRIVATE_PATH/.insteadOf')
        self.git(self.local, 'config', 'remote.origin.mirror', 'true')
        self.run_function('validate_cask_origin', success=False)
        self.git(self.local, 'config', 'remote.origin.mirror', 'PRIVATE_INVALID')
        result = self.run_function('validate_cask_origin', success=False)
        self.assertNotIn('PRIVATE', result.stderr)

    def test_preflight_invoked_before_signing_and_in_cask_only(self):
        self.assertLess(SOURCE.index('    cask_preflight', SOURCE.index('# ---- 1. Pre-flight')),
                        SOURCE.index('# ---- 6. Codesign'))
        self.assertIn('    cask_preflight', SOURCE[SOURCE.index('# ---- 0.5 Cask-only'):SOURCE.index('# ---- 1. Pre-flight')])


if __name__ == '__main__':
    unittest.main()
