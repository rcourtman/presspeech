"""Synthetic Hub snapshots only: no model downloads or real cache mutation."""
import hashlib
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

    def checksums(self):
        return {
            name: hashlib.sha256(
                (b'{}' if name.endswith('.json') else b'synthetic weights')
            ).hexdigest()
            for name in self.files
        }

    def resolve_verified(self):
        return model_cache.resolve_snapshot(
            'fixture/public', self.revision, self.files,
            expected_sha256s=self.checksums(),
            integrity_cache_dir=Path(self.temp.name) / 'integrity-cache')

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

    def test_local_only_cache_miss_never_falls_back_to_a_download(self):
        self.download.side_effect = self.missing('no cache')
        with self.assertRaises(model_cache.ModelCacheMissingError):
            model_cache.resolve_snapshot(
                'fixture/public', self.revision, self.files, local_only=True)
        self.assertEqual(self.flags(), [True])
        self.assert_pinned_anonymous()

    def test_local_only_incomplete_snapshot_never_falls_back_to_a_download(self):
        (self.snapshot / 'tokenizer.json').unlink()
        with self.assertRaises(model_cache.ModelCacheMissingError):
            model_cache.resolve_snapshot(
                'fixture/public', self.revision, self.files, local_only=True)
        self.assertEqual(self.flags(), [True])

    def test_missing_snapshot_falls_back_once_with_same_pin_and_privacy(self):
        self.download.side_effect = [self.missing('no cache'), str(self.snapshot)]
        self.assertEqual(self.resolve(), str(self.snapshot))
        self.assertEqual(self.flags(), [True, False]); self.assert_pinned_anonymous()

    def test_download_progress_reports_only_online_transfer_bytes(self):
        events = []
        class FakeTqdm:
            def __init__(self, *args, **kwargs):
                self.n = kwargs.get('initial', 0)
                self.total = kwargs.get('total')
                self.unit = kwargs.get('unit')
                self.desc = kwargs.get('desc')
                self.display()

            def update(self, amount):
                self.n += amount
                self.display()

            def display(self, *_args, **_kwargs):
                pass

            def close(self):
                pass

        tqdm_package = types.ModuleType('tqdm')
        tqdm_package.__path__ = []
        tqdm_auto = types.ModuleType('tqdm.auto')
        tqdm_auto.tqdm = FakeTqdm

        def download(*_args, **kwargs):
            if kwargs['local_files_only']:
                raise self.missing('no cache')
            progress_class = kwargs['tqdm_class']
            transfer = progress_class(
                total=1024, initial=0, unit='B', desc='Downloading bytes',
                mininterval=0)
            transfer._presspeech_last_report = None
            transfer.update(256)
            transfer.close()
            # The same class is used for other Hub progress bars; they must
            # stay silent and not masquerade as downloaded model bytes.
            other = progress_class(
                total=4, initial=0, unit='it', desc='Downloading files',
                mininterval=0)
            other._presspeech_last_report = None
            other.update(1)
            other.close()
            return str(self.snapshot)

        self.download.side_effect = download
        with mock.patch.dict(sys.modules, {
                'tqdm': tqdm_package, 'tqdm.auto': tqdm_auto}):
            self.assertEqual(model_cache.resolve_snapshot(
                'fixture/public', self.revision, self.files,
                progress=lambda *event: events.append(event)), str(self.snapshot))

        self.assertEqual(events[0], ('downloading', None, None))
        self.assertIn(('downloading', 256, 1024), events)
        self.assertEqual(events[-1], ('loading', None, None))
        self.assertEqual(self.flags(), [True, False])
        self.assertIs(self.download.call_args_list[0].kwargs.get('tqdm_class'), None)
        self.assertIn('tqdm_class', self.download.call_args_list[1].kwargs)
        self.assert_pinned_anonymous()

    def test_local_snapshot_reports_load_phase_without_network_progress_bar(self):
        events = []
        self.assertEqual(model_cache.resolve_snapshot(
            'fixture/public', self.revision, self.files,
            progress=lambda *event: events.append(event)), str(self.snapshot))
        self.assertEqual(events, [('loading', None, None)])
        self.assertEqual(self.flags(), [True])
        self.assertNotIn('tqdm_class', self.download.call_args.kwargs)

    def test_partial_snapshot_without_tree_metadata_fetches_missing_files(self):
        (self.snapshot / 'tokenizer.json').unlink()
        def download(*args, **kwargs):
            if not kwargs['local_files_only']: self.complete()
            return str(self.snapshot)
        self.download.side_effect = download
        self.assertEqual(self.resolve(), str(self.snapshot))
        self.assertEqual(self.flags(), [True, False]); self.assert_pinned_anonymous()

    def test_hub_incomplete_snapshot_path_is_checked_before_online_retry(self):
        (self.snapshot / 'model.safetensors').unlink()
        (self.snapshot / 'config.json').write_text('{invalid')
        incomplete = self.missing('cached snapshot is incomplete')
        incomplete.snapshot_path = str(self.snapshot)
        self.download.side_effect = [incomplete, AssertionError('online retry')]
        with self.assertRaises(model_cache.ModelCacheCorruptError):
            self.resolve()
        self.assertEqual(self.flags(), [True])

    def test_hub_incomplete_snapshot_with_only_missing_required_file_can_fetch(self):
        (self.snapshot / 'model.safetensors').unlink()
        incomplete = self.missing('cached snapshot is incomplete')
        incomplete.snapshot_path = str(self.snapshot)
        def download(*_args, **kwargs):
            if kwargs['local_files_only']:
                raise incomplete
            self.complete()
            return str(self.snapshot)
        self.download.side_effect = download
        self.assertEqual(self.resolve(), str(self.snapshot))
        self.assertEqual(self.flags(), [True, False])
        self.assert_pinned_anonymous()

    def test_broken_cached_required_symlink_is_corruption_not_download_consent(self):
        (self.snapshot / 'model.safetensors').unlink()
        try:
            (self.snapshot / 'model.safetensors').symlink_to('missing-blob')
        except OSError as exc:
            self.skipTest('symlinks unavailable: ' + type(exc).__name__)
        with self.assertRaises(model_cache.ModelCacheCorruptError):
            self.resolve()
        self.assertEqual(self.flags(), [True])

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
        self.assertEqual(self.download.call_args.kwargs['allow_patterns'], list(self.files))

    def test_missing_optional_cache_entry_does_not_authorize_network(self):
        optional = ('generation_config.json',)

        def hub_download(*_args, **kwargs):
            # Pinned Hub 1.29.0 checks all requested patterns against a cached
            # tree listing, so an absent optional pattern would raise here.
            # Its presence must not be necessary for the local-only probe.
            if optional[0] in kwargs['allow_patterns']:
                if kwargs['local_files_only']:
                    raise self.missing('requested optional file is uncached')
                self.fail('optional-only cache miss initiated a network request')
            return str(self.snapshot)

        self.download.side_effect = hub_download
        self.assertEqual(model_cache.resolve_snapshot(
            'fixture/public', self.revision, self.files,
            optional_files=optional), str(self.snapshot))
        self.assertEqual(self.flags(), [True])
        self.assert_pinned_anonymous()

    def test_missing_required_file_fetches_reviewed_optional_files_too(self):
        optional = ('generation_config.json',)
        self.download.side_effect = [self.missing('no cache'), str(self.snapshot)]
        model_cache.resolve_snapshot('fixture/public', self.revision, self.files, optional_files=optional)
        self.assertEqual(self.flags(), [True, False])
        self.assertEqual(self.download.call_args_list[0].kwargs['allow_patterns'], list(self.files))
        self.assertEqual(self.download.call_args_list[1].kwargs['allow_patterns'], list(self.files + optional))
        for call in self.download.call_args_list:
            self.assertEqual(call.kwargs['revision'], self.revision)
            self.assertIs(call.kwargs['token'], False)

    def test_present_corrupt_optional_json_does_not_authorize_fetch(self):
        (self.snapshot/'generation_config.json').write_text('{bad')
        (self.snapshot/'model.safetensors').unlink()
        with self.assertRaises(model_cache.ModelCacheCorruptError):
            model_cache.resolve_snapshot('fixture/public', self.revision, self.files,
                                         optional_files=('generation_config.json',))
        self.assertEqual(self.flags(), [True])

    def test_present_optional_file_still_requires_its_manifest_digest(self):
        optional = 'generation_config.json'
        (self.snapshot / optional).write_text('{"changed":true}')
        manifest = self.checksums()
        manifest[optional] = hashlib.sha256(b'{}').hexdigest()
        with self.assertRaises(model_cache.ModelCacheIntegrityError):
            model_cache.resolve_snapshot(
                'fixture/public', self.revision, self.files,
                optional_files=(optional,), expected_sha256s=manifest)
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

    def test_sha256_manifest_is_checked_before_hub_access(self):
        for manifest in (
                {'config.json': 'a' * 64},
                dict(self.checksums(), **{'model.safetensors': 'A' * 64})):
            with self.subTest(manifest=manifest), self.assertRaises(ValueError):
                model_cache.resolve_snapshot(
                    'fixture/public', self.revision, self.files,
                    expected_sha256s=manifest)
        self.download.assert_not_called()

    def test_valid_snapshot_marker_speeds_only_supported_platforms(self):
        self.assertEqual(self.resolve_verified(), str(self.snapshot))
        marker_files = list((Path(self.temp.name) / 'integrity-cache').glob('*.json'))
        self.assertEqual(len(marker_files), int(model_cache._can_reuse_integrity_marker()))
        with mock.patch.object(
                model_cache, '_calculate_sha256',
                wraps=model_cache._calculate_sha256) as calculate:
            self.assertEqual(self.resolve_verified(), str(self.snapshot))
        self.assertEqual(
            calculate.call_count,
            0 if model_cache._can_reuse_integrity_marker() else len(self.files))

    def test_windows_policy_never_reuses_metadata_only_integrity_markers(self):
        with mock.patch.object(model_cache.os, 'name', 'nt'):
            self.assertFalse(model_cache._can_reuse_integrity_marker())
        with mock.patch.object(model_cache.os, 'name', 'posix'):
            self.assertTrue(model_cache._can_reuse_integrity_marker())
        with mock.patch.object(model_cache, '_can_reuse_integrity_marker',
                               return_value=False), \
                mock.patch.object(model_cache, '_calculate_sha256',
                                  wraps=model_cache._calculate_sha256) as calculate:
            self.assertEqual(self.resolve_verified(), str(self.snapshot))
            self.assertEqual(self.resolve_verified(), str(self.snapshot))
        self.assertEqual(calculate.call_count, 2 * len(self.files))
        self.assertEqual(
            list((Path(self.temp.name) / 'integrity-cache').glob('*.json')), [])

    def test_windows_rehash_rejects_same_size_rewrite_with_restored_mtime(self):
        # Python 3.12's Windows st_ctime_ns is creation time, not change time.
        # Simulate that value so the old metadata-only marker would appear to
        # match after an in-place rewrite and restored last-write timestamp.
        original_fingerprint = model_cache._file_fingerprint

        def windows_fingerprint(path, metadata):
            result = original_fingerprint(path, metadata)
            result['ctime_ns'] = 123456789
            return result

        weights = self.snapshot / 'model.safetensors'
        with mock.patch.object(model_cache, '_file_fingerprint',
                               side_effect=windows_fingerprint), \
                mock.patch.object(model_cache, '_can_reuse_integrity_marker',
                                  return_value=True):
            self.assertEqual(self.resolve_verified(), str(self.snapshot))
            before = windows_fingerprint(weights, weights.stat())

        old_stat = weights.stat()
        weights.write_bytes(b'X' * len(b'synthetic weights'))
        os.utime(weights, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))

        with mock.patch.object(model_cache, '_file_fingerprint',
                               side_effect=windows_fingerprint), \
                mock.patch.object(model_cache, '_can_reuse_integrity_marker',
                                  return_value=False):
            self.assertEqual(before, windows_fingerprint(weights, weights.stat()))
            marker = next((Path(self.temp.name) / 'integrity-cache').glob('*.json'))
            fingerprints = {
                name: windows_fingerprint(self.snapshot / name,
                                          (self.snapshot / name).stat())
                for name in self.files
            }
            self.assertTrue(model_cache._integrity_marker_matches(
                marker, self.snapshot,
                model_cache._manifest_digest(self.checksums()), fingerprints))
            with self.assertRaises(model_cache.ModelCacheIntegrityError):
                self.resolve_verified()
        self.assertEqual(self.flags(), [True, True])

    def test_changed_file_invalidates_marker_and_fails_closed_without_retry(self):
        self.assertEqual(self.resolve_verified(), str(self.snapshot))
        (self.snapshot / 'model.safetensors').write_bytes(b'X' * len(b'synthetic weights'))
        with self.assertRaises(model_cache.ModelCacheIntegrityError):
            self.resolve_verified()
        self.assertEqual(self.flags(), [True, True])
        self.assertNotIn(False, self.flags())

    def test_downloaded_snapshot_must_match_the_manifest_before_return(self):
        self.download.side_effect = [self.missing('no cache'), str(self.snapshot)]
        (self.snapshot / 'model.safetensors').write_bytes(b'X' * len(b'synthetic weights'))
        with self.assertRaises(model_cache.ModelCacheIntegrityError):
            model_cache.resolve_snapshot(
                'fixture/public', self.revision, self.files,
                expected_sha256s=self.checksums(),
                integrity_cache_dir=Path(self.temp.name) / 'integrity-cache')
        self.assertEqual(self.flags(), [True, False])

    def test_corrupt_verification_marker_is_only_a_cache_miss(self):
        with mock.patch.object(model_cache, '_can_reuse_integrity_marker',
                               return_value=True):
            self.assertEqual(self.resolve_verified(), str(self.snapshot))
            marker = next((Path(self.temp.name) / 'integrity-cache').glob('*.json'))
            marker.write_text('{broken', encoding='utf-8')
            with mock.patch.object(
                    model_cache, '_calculate_sha256',
                    wraps=model_cache._calculate_sha256) as calculate:
                self.assertEqual(self.resolve_verified(), str(self.snapshot))
        self.assertEqual(calculate.call_count, len(self.files))


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
