#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import plistlib
import struct
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('macos_build_artifact', Path(__file__).with_name('macos-build-artifact.py'))
artifact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifact)


class BuildArtifactTests(unittest.TestCase):
    def fixture(self, root):
        for name in artifact.INPUTS:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic approved source')
        (root / 'swift/Info.plist').write_bytes(plistlib.dumps(dict(CFBundleShortVersionString='0.3.10', CFBundleVersion='33')))
        directory = root / 'payload'
        directory.mkdir()
        binary = directory / 'Presspeech'
        binary.write_bytes(struct.pack('<II', 0xFEEDFACF, 0x0100000C) + b'\0' * 2048)
        manifest = dict(schema=1, source_sha='a' * 40, run_id=123, version='0.3.11', build='34', architecture='arm64',
                        inputs_sha256={name: artifact.digest(root / name) for name in artifact.INPUTS},
                        binary_sha256=artifact.digest(binary))
        (directory / 'build-manifest.json').write_text(json.dumps(manifest))
        run = dict(id=123, conclusion='success', event='workflow_dispatch', head_sha='a' * 40, head_branch='main',
                   path=artifact.WORKFLOW, repository=dict(full_name='rcourtman/presspeech'))
        return directory, manifest, run

    def test_accepts_matching_build_and_restores_transport_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory, _, run = self.fixture(root)
            (directory / 'Presspeech').chmod(0o644)
            result = artifact.verify(root, directory, run, 'a' * 40, '0.3.11', '34')
            self.assertEqual(result, directory / 'Presspeech')
            self.assertTrue(result.stat().st_mode & 0o100)

    def test_refuses_unapproved_source_workflow_or_run(self):
        for field, value in [('head_sha', 'b' * 40), ('conclusion', 'failure'), ('head_branch', 'other'),
                             ('event', 'pull_request'), ('path', 'other.yml'), ('id', 456),
                             ('repository', {'full_name': 'other/repository'})]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                directory, _, run = self.fixture(root)
                run[field] = value
                with self.assertRaises(ValueError): artifact.verify(root, directory, run, 'a' * 40, '0.3.11', '34')

    def test_refuses_changed_payload_inputs_and_release_metadata(self):
        for fault in ['binary', 'input', 'version', 'build', 'extra', 'symlink']:
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                directory, manifest, run = self.fixture(root)
                if fault == 'binary': (directory / 'Presspeech').write_bytes(b'changed')
                if fault == 'input': (root / artifact.INPUTS[0]).write_text('changed')
                if fault in ['version', 'build']:
                    manifest[fault] = '9.9.9' if fault == 'version' else '99'
                    (directory / 'build-manifest.json').write_text(json.dumps(manifest))
                if fault == 'extra': (directory / 'extra').write_text('unexpected')
                if fault == 'symlink':
                    binary = directory / 'Presspeech'
                    binary.rename(root / 'outside')
                    binary.symlink_to(root / 'outside')
                with self.assertRaises(ValueError): artifact.verify(root, directory, run, 'a' * 40, '0.3.11', '34')


if __name__ == '__main__':
    unittest.main()
