#!/usr/bin/env python3
"""Default-environment qualification and private configured-run handling."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('environment', ROOT / 'experiment-environment.py')
environment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(environment)
DEFAULT = 'experiment-environment: schema=1 inherited-controls=0 ci-present=0\n'


class EnvironmentEvidenceTests(unittest.TestCase):
    def test_actual_receipt_distinguishes_default_and_configured(self):
        self.assertEqual(environment.classify(DEFAULT), 'default')
        self.assertEqual(environment.classify(DEFAULT.replace('controls=0', 'controls=1')), 'configured')
        self.assertEqual(environment.classify(DEFAULT.replace('ci-present=0', 'ci-present=1')), 'default')

    def test_missing_malformed_duplicate_and_future_receipts_never_qualify(self):
        for text in ('', DEFAULT * 2, DEFAULT + 'experiment-environment: invalid\n',
                     DEFAULT.replace('schema=1', 'schema=2'),
                     DEFAULT.replace('controls=0', 'controls=-1'),
                     DEFAULT.replace('controls=0', 'controls=000'),
                     DEFAULT.replace('controls=0', 'controls=9999999'),
                     'transcript: ' + DEFAULT, ' ' + DEFAULT,
                     DEFAULT.replace('ci-present=0', 'ci-present=unknown')):
            with self.subTest(text=text):
                self.assertEqual(environment.classify(text), 'unreported')

    def test_mixed_runs_cannot_recover_a_default_verdict(self):
        for stream, expected in ((['default'] * 3, 'default'),
                                 (['configured', 'default'], 'configured'),
                                 (['default', 'configured'], 'configured'),
                                 (['default', 'unreported', 'configured', 'default'], 'unreported')):
            state = 'pending'
            for observation in stream:
                state = environment.merge(state, observation)
            self.assertEqual(state, expected)

    def test_cli_never_emits_names_values_or_private_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'PRIVATE_PATH.log'
            for data in (b'PRIVATE_VALUE', b'PRIVATE_VALUE\xff', None):
                if data is None:
                    path.unlink()
                else:
                    path.write_bytes(data)
                process = subprocess.run([sys.executable, str(ROOT / 'experiment-environment.py'),
                    '--log', str(path), '--previous', 'default'], capture_output=True, text=True)
                self.assertEqual(process.returncode, 0)
                self.assertEqual(process.stdout, 'unreported\n')
                self.assertEqual(process.stderr, '')


if __name__ == '__main__':
    unittest.main()
