"""Synthetic Hub snapshots only: no model downloads or real cache mutation."""
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

import model_cache


class CacheFirstTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.revision = 'a' * 40
        self.snapshot = Path(self.temp.name) / self.revision
        self.snapshot.mkdir()
        self.files = ('config.json', 'tokenizer.json', 'model.safetensors')
        self.complete()
        self.missing = type('LocalEntryNotFoundError', (FileNotFoundError,), {})
        self.download = mock.Mock(return_value=str(self.snapshot))
        modules = {
            'huggingface_hub': types.SimpleNamespace(snapshot_download=self.download),
            'huggingface_hub.errors': types.SimpleNamespace(LocalEntryNotFoundError=self.missing),
            'huggingface_hub.constants': types.SimpleNamespace(HF_HUB_OFFLINE=False),
        }
        patcher = mock.patch.dict(sys.modules, modules); patcher.start(); self.addCleanup(patcher.stop)
        patcher = mock.patch.dict(os.environ, {'HF_HUB_OFFLINE':'0','TRANSFORMERS_OFFLINE':'0'})
        patcher.start(); self.addCleanup(patcher.stop)
        patcher = mock.patch.object(model_cache.model_network, 'harden_loaded_runtime')
        self.harden = patcher.start(); self.addCleanup(patcher.stop)

    def complete(self):
        for name in self.files:
            (self.snapshot / name).write_bytes(b'{}' if name.endswith('.json') else b'synthetic weights')

    def resolve(self):
        return model_cache.resolve_snapshot('fixture/public', self.revision, self.files)

    def flags(self):
        return [call.kwargs['local_files_only'] for call in self.download.call_args_list]

    def assert_pinned_anonymous(self):
        for call in self.download.call_args_list:
            self.assertEqual(call.args, ('fixture/public',))
            self.assertEqual(call.kwargs['revision'], self.revision)
            self.assertIs(call.kwargs['token'], False)
            self.assertEqual(call.kwargs['allow_patterns'], list(self.files))
        self.assertEqual(self.harden.call_count, self.download.call_count)

    def test_complete_snapshot_without_tree_metadata_stays_local(self):
        self.assertEqual(self.resolve(), str(self.snapshot))
        self.assertEqual(self.flags(), [True]); self.assert_pinned_anonymous()

    def test_missing_snapshot_falls_back_once_with_same_pin_and_privacy(self):
        self.download.side_effect = [self.missing('no cache'), str(self.snapshot)]
        self.assertEqual(self.resolve(), str(self.snapshot))
        self.assertEqual(self.flags(), [True, False]); self.assert_pinned_anonymous()

    def test_partial_snapshot_without_tree_metadata_fetches_missing_files(self):
        (self.snapshot / 'tokenizer.json').unlink()
        def download(*args, **kwargs):
            if not kwargs['local_files_only']: self.complete()
            return str(self.snapshot)
        self.download.side_effect = download
        self.assertEqual(self.resolve(), str(self.snapshot))
        self.assertEqual(self.flags(), [True, False]); self.assert_pinned_anonymous()

    def test_incomplete_online_result_is_not_retried_or_returned(self):
        (self.snapshot / 'model.safetensors').unlink()
        with self.assertRaises(model_cache.ModelCacheMissingError): self.resolve()
        self.assertEqual(self.flags(), [True, False])

    def test_explicit_offline_and_import_time_offline_never_fall_back(self):
        for flag, value in (('HF_HUB_OFFLINE','YES'),('TRANSFORMERS_OFFLINE','true'),('cached',True)):
            with self.subTest(flag=flag):
                self.download.reset_mock(); self.download.side_effect = self.missing('missing')
                with mock.patch.dict(os.environ, {flag: value} if flag != 'cached' else {}):
                    constants = sys.modules['huggingface_hub.constants']
                    constants.HF_HUB_OFFLINE = flag == 'cached'
                    try:
                        with self.assertRaises(self.missing): self.resolve()
                        self.assertEqual(self.flags(), [True])
                    finally: constants.HF_HUB_OFFLINE = False

    def test_corrupt_json_is_not_hidden_by_missing_weights(self):
        (self.snapshot / 'config.json').write_text('{invalid')
        (self.snapshot / 'model.safetensors').unlink()
        with self.assertRaises(model_cache.ModelCacheCorruptError): self.resolve()
        self.assertEqual(self.flags(), [True])

    def test_empty_existing_weights_are_corruption_not_missing_cache(self):
        (self.snapshot / 'model.safetensors').write_bytes(b'')
        with self.assertRaises(model_cache.ModelCacheCorruptError): self.resolve()
        self.assertEqual(self.flags(), [True])

    def test_permission_and_unexpected_io_failures_do_not_trigger_network(self):
        for error in (PermissionError('denied'), OSError('disk problem')):
            with self.subTest(error=type(error).__name__):
                self.download.reset_mock(); self.download.side_effect = error
                with self.assertRaises(type(error)): self.resolve()
                self.assertEqual(self.flags(), [True])

    def test_wrong_snapshot_revision_is_not_adopted(self):
        self.download.return_value = str(self.snapshot.with_name('b'*40))
        with self.assertRaises(model_cache.ModelCacheCorruptError): self.resolve()
        self.assertEqual(self.flags(), [True])

    def test_privacy_is_revalidated_before_online_attempt(self):
        self.download.side_effect = self.missing('missing')
        self.harden.side_effect = [None, model_cache.model_network.ModelNetworkPolicyError('changed')]
        with self.assertRaises(model_cache.model_network.ModelNetworkPolicyError): self.resolve()
        self.assertEqual(self.flags(), [True])

    def test_unpinned_or_pattern_contract_is_rejected_before_hub_calls(self):
        for revision, files in [('main', self.files), (self.revision, ('../config.json',)),
                                (self.revision, ('*.json',)), (self.revision, ())]:
            with self.subTest(revision=revision,files=files):
                with self.assertRaises(ValueError):
                    model_cache.resolve_snapshot('fixture/public', revision, files)
        self.download.assert_not_called()

    def test_absent_optional_json_uses_complete_cache_without_fetch(self):
        optional = ('generation_config.json', 'tokenizer_config.json')
        self.assertEqual(model_cache.resolve_snapshot(
            'fixture/public', self.revision, self.files, optional_files=optional), str(self.snapshot))
        self.assertEqual(self.flags(), [True])
        self.assertEqual(self.download.call_args.kwargs['allow_patterns'], list(self.files + optional))

    def test_missing_required_file_fetches_reviewed_optional_files_too(self):
        optional = ('generation_config.json',)
        self.download.side_effect = [self.missing('no cache'), str(self.snapshot)]
        model_cache.resolve_snapshot('fixture/public', self.revision, self.files, optional_files=optional)
        self.assertEqual(self.flags(), [True, False])
        for call in self.download.call_args_list:
            self.assertEqual(call.kwargs['allow_patterns'], list(self.files + optional))
            self.assertEqual(call.kwargs['revision'], self.revision)
            self.assertIs(call.kwargs['token'], False)

    def test_present_corrupt_optional_json_does_not_authorize_fetch(self):
        (self.snapshot/'generation_config.json').write_text('{bad')
        (self.snapshot/'model.safetensors').unlink()
        with self.assertRaises(model_cache.ModelCacheCorruptError):
            model_cache.resolve_snapshot('fixture/public', self.revision, self.files,
                                         optional_files=('generation_config.json',))
        self.assertEqual(self.flags(), [True])

    def test_either_feature_config_layout_satisfies_local_contract(self):
        alternatives = ('processor_config.json', 'preprocessor_config.json')
        for name in alternatives:
            with self.subTest(layout=name):
                self.download.reset_mock()
                (self.snapshot/name).write_text('{}')
                self.assertEqual(model_cache.resolve_snapshot(
                    'fixture/public', self.revision, self.files,
                    optional_files=alternatives, required_any=(alternatives,)), str(self.snapshot))
                self.assertEqual(self.flags(), [True])
                (self.snapshot/name).unlink()

    def test_missing_all_feature_config_layouts_is_incomplete_cache(self):
        alternatives = ('processor_config.json', 'preprocessor_config.json')
        with mock.patch.dict(os.environ, {'HF_HUB_OFFLINE':'1'}):
            with self.assertRaisesRegex(model_cache.ModelCacheMissingError, 'one of'):
                model_cache.resolve_snapshot('fixture/public', self.revision, self.files,
                                             optional_files=alternatives, required_any=(alternatives,))
        self.assertEqual(self.flags(), [True])

    def test_optional_patterns_and_unreviewed_alternatives_are_rejected(self):
        for options in ({'optional_files':('*.json',)},
                        {'optional_files':('config.json',)},
                        {'required_any':(('other.json',),)}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                model_cache.resolve_snapshot('fixture/public', self.revision, self.files, **options)
        self.download.assert_not_called()


class WhisperPrivateSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.source = root / ('a' * 40); self.source.mkdir()
        self.scratch = root / 'scratch'; self.scratch.mkdir()
        self.files = ('config.json', 'tokenizer.json', 'model.bin', 'vocabulary.txt')
        for name in self.files:
            (self.source / name).write_bytes(b'{}' if name.endswith('.json') else b'synthetic bytes')

    def view(self):
        return model_cache.whisper_snapshot(self.source, self.files, scratch_root=self.scratch)

    def test_private_tokenizer_survives_hub_path_deletion_and_normal_cleanup(self):
        with self.view() as folder:
            private = Path(folder)
            self.assertEqual((private/'model.bin').stat().st_ino, (self.source/'model.bin').stat().st_ino)
            self.assertNotEqual((private/'tokenizer.json').stat().st_ino, (self.source/'tokenizer.json').stat().st_ino)
            for file in self.source.iterdir(): file.unlink()
            self.source.rmdir()
            self.assertEqual((private/'tokenizer.json').read_text(), '{}')
            self.assertEqual((private/'model.bin').read_bytes(), b'synthetic bytes')
        self.assertFalse(private.exists()); self.assertEqual(list(self.scratch.iterdir()), [])

    def test_resolved_weight_link_survives_snapshot_symlink_and_parent_removal(self):
        blob = self.source.parent / 'blob'; blob.write_bytes(b'synthetic blob')
        (self.source/'model.bin').unlink()
        try: (self.source/'model.bin').symlink_to(blob)
        except OSError as exc: self.skipTest('symlinks unavailable: ' + type(exc).__name__)
        with self.view() as folder:
            (self.source/'model.bin').unlink(); blob.unlink()
            self.assertEqual((Path(folder)/'model.bin').read_bytes(), b'synthetic blob')

    def test_cross_device_link_failure_copies_weights_and_cleans_up(self):
        with mock.patch.object(model_cache.os, 'link', side_effect=OSError(model_cache.errno.EXDEV, 'cross device')):
            with self.view() as folder:
                private = Path(folder)
                self.assertNotEqual((private/'model.bin').stat().st_ino, (self.source/'model.bin').stat().st_ino)
                self.assertEqual((private/'model.bin').read_bytes(), b'synthetic bytes')
        self.assertFalse(private.exists())

    def test_link_permission_failure_does_not_silently_copy_or_leak_scratch(self):
        with mock.patch.object(model_cache.os, 'link', side_effect=PermissionError(model_cache.errno.EACCES, 'denied')), \
                mock.patch.object(model_cache.shutil, 'copyfile', wraps=model_cache.shutil.copyfile) as copy:
            with self.assertRaises(PermissionError):
                with self.view(): self.fail('must fail before constructor')
        self.assertFalse(any(Path(call.args[0]).name == 'model.bin' for call in copy.call_args_list))
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_missing_tokenizer_during_staging_aborts_without_download_or_leak(self):
        original = model_cache.shutil.copyfile
        def copying(source, target):
            if Path(source).name == 'tokenizer.json': Path(source).unlink()
            return original(source, target)
        with mock.patch.object(model_cache.shutil, 'copyfile', side_effect=copying):
            with self.assertRaises(FileNotFoundError):
                with self.view(): self.fail('must fail before constructor')
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_body_failure_removes_private_files(self):
        with self.assertRaisesRegex(RuntimeError, 'constructor failure'):
            with self.view() as folder:
                private = Path(folder)
                raise RuntimeError('constructor failure')
        self.assertFalse(private.exists()); self.assertEqual(list(self.scratch.iterdir()), [])

    def test_optional_feature_config_is_copied_when_present_and_absence_is_local(self):
        optional = ('preprocessor_config.json',)
        with model_cache.whisper_snapshot(self.source, self.files,
                                         optional_files=optional, scratch_root=self.scratch) as folder:
            self.assertFalse((Path(folder)/optional[0]).exists())
        (self.source/optional[0]).write_text('{"feature_size":128}')
        with model_cache.whisper_snapshot(self.source, self.files,
                                         optional_files=optional, scratch_root=self.scratch) as folder:
            self.assertEqual((Path(folder)/optional[0]).read_text(), '{"feature_size":128}')
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_corrupt_optional_private_config_aborts_and_cleans_up(self):
        (self.source/'preprocessor_config.json').write_text('{bad')
        with self.assertRaises(model_cache.ModelCacheCorruptError):
            with model_cache.whisper_snapshot(self.source, self.files,
                                             optional_files=('preprocessor_config.json',),
                                             scratch_root=self.scratch):
                self.fail('corrupt config must not reach constructor')
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_optional_config_removed_during_copy_is_io_failure_not_silent_default(self):
        optional = 'preprocessor_config.json'
        (self.source/optional).write_text('{}')
        original = model_cache.shutil.copyfile
        def copying(source, target):
            if Path(source).name == optional:
                Path(source).unlink()
            return original(source, target)
        with mock.patch.object(model_cache.shutil, 'copyfile', side_effect=copying):
            with self.assertRaises(FileNotFoundError):
                with model_cache.whisper_snapshot(self.source, self.files,
                                                 optional_files=(optional,), scratch_root=self.scratch):
                    self.fail('staging I/O errors must stay visible')
        self.assertEqual(list(self.scratch.iterdir()), [])
