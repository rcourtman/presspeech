"""Application model-load failures must not authorize a different download."""
import gc
import sys
import threading
import types
import unittest
import weakref
from unittest import mock

import app
import model_cache


class ModelLoadRecoveryTests(unittest.TestCase):
    def make_app(self):
        instance = app.PresspeechApp.__new__(app.PresspeechApp)
        instance.settings = {'model':'parakeet-tdt-0.6b-v3'}
        instance.transcriber = mock.Mock()
        instance.transcriber.loaded.return_value = False
        instance.transcriber.last_timing = {}
        instance.notify = mock.Mock()
        instance._log = mock.Mock()
        instance._deliver_text = mock.Mock()
        instance._apply_text = lambda text:text
        return instance

    def test_cache_and_constructor_errors_do_not_switch_to_another_model(self):
        for error in (model_cache.ModelCacheCorruptError('corrupt cache'),
                      PermissionError('private staging denied'),
                      ValueError('invalid tokenizer'),
                      RuntimeError('constructor failed')):
            with self.subTest(error=type(error).__name__):
                instance = self.make_app()
                instance.transcriber.load.side_effect = error
                instance._transcribe_worker_inner(object())
                instance.transcriber.load.assert_called_once_with(
                    'parakeet-tdt-0.6b-v3', notify=instance.notify)
                instance.transcriber.transcribe.assert_not_called()
                instance._deliver_text.assert_not_called()
                self.assertEqual(instance.notify.call_args.args[0], 'Model load failed')

    def failed_parakeet_decode(self, fallback):
        """Run one recording whose Parakeet decode fails, with this fallback."""
        instance = self.make_app()
        instance.transcriber.loaded.return_value = True
        instance.transcriber.transcribe.side_effect = RuntimeError('private hypothesis')
        audio = object()
        with mock.patch.object(app.engine, 'Transcriber', return_value=fallback):
            outcome = instance._transcribe_worker_inner(audio)
        return instance, audio, outcome

    @staticmethod
    def mock_fallback():
        fallback = mock.Mock()
        fallback.last_timing = {}
        return fallback

    def assert_parakeet_untouched(self, instance):
        instance.transcriber.load.assert_not_called()
        instance.transcriber.unload.assert_not_called()

    def test_loaded_parakeet_decode_failure_uses_only_cached_fallback(self):
        fallback = self.mock_fallback()
        fallback.transcribe.return_value = 'synthetic text'

        instance, audio, _ = self.failed_parakeet_decode(fallback)

        fallback.load.assert_called_once_with('base.en', local_only=True)
        fallback.transcribe.assert_called_once_with(audio)
        fallback.unload.assert_called_once_with()
        self.assert_parakeet_untouched(instance)
        instance.transcriber.transcribe.assert_called_once_with(audio)
        instance._deliver_text.assert_called_once()
        self.assertEqual(instance._deliver_text.call_args.args[0], 'synthetic text')

    def test_missing_cached_fallback_does_not_download_or_decode_again(self):
        fallback = self.mock_fallback()
        fallback.load.side_effect = model_cache.ModelCacheMissingError(
            'private cache path')

        instance, audio, _ = self.failed_parakeet_decode(fallback)

        fallback.load.assert_called_once_with('base.en', local_only=True)
        fallback.transcribe.assert_not_called()
        fallback.unload.assert_called_once_with()
        self.assert_parakeet_untouched(instance)
        instance.transcriber.transcribe.assert_called_once_with(audio)
        instance._deliver_text.assert_not_called()
        messages = str(instance.notify.mock_calls) + str(instance._log.mock_calls)
        self.assertNotIn('private hypothesis', messages)
        self.assertNotIn('private cache path', messages)
        self.assertIn('no model was downloaded', messages)

    def test_corrupt_cached_fallback_does_not_expose_error_or_decode_again(self):
        for error in (model_cache.ModelCacheCorruptError('private cache path'),
                      RuntimeError('private cache path in constructor')):
            with self.subTest(error=type(error).__name__):
                fallback = self.mock_fallback()
                fallback.load.side_effect = error

                instance, _, _ = self.failed_parakeet_decode(fallback)

                fallback.transcribe.assert_not_called()
                fallback.unload.assert_called_once_with()
                self.assert_parakeet_untouched(instance)
                instance.transcriber.transcribe.assert_called_once()
                instance._deliver_text.assert_not_called()
                self.assertEqual(
                    instance._log.call_args.args[0],
                    'fallback model load failed; error details suppressed')
                messages = str(instance.notify.mock_calls) + str(instance._log.mock_calls)
                self.assertNotIn('private hypothesis', messages)
                self.assertNotIn('private cache path', messages)

    def test_fallback_release_failure_still_delivers_decoded_text(self):
        fallback = self.mock_fallback()
        fallback.transcribe.return_value = 'synthetic text'
        fallback.unload.side_effect = OSError('private staging path')

        instance, _, _ = self.failed_parakeet_decode(fallback)

        instance._deliver_text.assert_called_once()
        self.assertEqual(instance._deliver_text.call_args.args[0], 'synthetic text')
        instance._log.assert_any_call(
            'fallback model release failed; error details suppressed')
        self.assertNotIn('private staging path', str(instance._log.mock_calls))

    def test_fallback_vad_rejection_uses_the_fallback_timing(self):
        fallback = self.mock_fallback()
        fallback.transcribe.return_value = ''
        fallback.last_timing = {'backend': 'whisper', 'speech_seconds': 0.0}
        instance = self.make_app()
        instance.transcriber.last_timing = {'backend': 'parakeet'}
        instance.transcriber.loaded.return_value = True
        instance.transcriber.transcribe.side_effect = RuntimeError('decode failed')

        with mock.patch.object(app.engine, 'Transcriber', return_value=fallback):
            outcome = instance._transcribe_worker_inner(object())

        self.assertEqual(outcome, app.NO_SPEECH_OUTCOME)

    def test_every_fallback_outcome_keeps_parakeet_ready_on_the_model_thread(self):
        # Real engine Transcribers on both sides; only the cache resolver,
        # snapshot staging and CTranslate2 model are synthetic. The model
        # executor thread owns every CUDA/CTranslate2 object, so the fallback
        # is built, decoded and released on the calling thread.
        failed = ['Parakeet failed', 'Transcription failed']
        defaults = {
            'resolve_error': None, 'construct_error': None, 'decode_error': None,
            'speech_seconds': 1.0, 'release_error': None, 'outcome': None,
            'events': ['construct', 'decode', 'release'],
            'titles': ['Parakeet failed'], 'delivered': True,
        }
        scenarios = {
            'cached': {},
            'missing': {
                'resolve_error': model_cache.ModelCacheMissingError('private cache path'),
                'events': [], 'titles': failed, 'delivered': False},
            'constructor': {
                'construct_error': RuntimeError('private constructor detail'),
                'events': ['construct', 'release'], 'titles': failed,
                'delivered': False},
            'decode': {
                'decode_error': RuntimeError('private decode detail'),
                'titles': failed, 'delivered': False},
            'no speech': {
                'speech_seconds': 0.0, 'outcome': app.NO_SPEECH_OUTCOME,
                'delivered': False},
            'release': {'release_error': OSError('private staging path')},
        }
        for name, overrides in scenarios.items():
            case = types.SimpleNamespace(**{**defaults, **overrides})
            with self.subTest(fallback=name):
                events = []

                def record(event, error):
                    events.append((event, threading.get_ident()))
                    if error is not None:
                        raise error

                def resolve(*args, **kwargs):
                    if case.resolve_error is not None:
                        raise case.resolve_error
                    return 'cached-snapshot'

                def decode(*args, **kwargs):
                    record('decode', case.decode_error)
                    return (iter([types.SimpleNamespace(text=' synthetic fallback text')]),
                            types.SimpleNamespace(duration_after_vad=case.speech_seconds))

                def construct(*args, **kwargs):
                    record('construct', case.construct_error)
                    return mock.Mock(**{'transcribe.side_effect': decode})

                def release(*exc_info):
                    record('release', case.release_error)
                    return False

                instance = self.make_app()
                instance.transcriber = app.engine.Transcriber()
                instance.transcriber.model = mock.sentinel.parakeet_model
                instance.transcriber.backend = 'parakeet'
                instance.transcriber.model_name = 'parakeet-tdt-0.6b-v3'
                instance.model_status = 'ready'
                instance.retry_model = mock.Mock()
                instance._set_indicator = mock.Mock()
                whisper = types.ModuleType('faster_whisper')
                whisper.WhisperModel = mock.Mock(side_effect=construct)
                staging = mock.MagicMock()
                staging.__enter__.return_value = 'private-snapshot'
                staging.__exit__.side_effect = release
                with mock.patch.object(instance.transcriber, 'transcribe',
                                       side_effect=RuntimeError('private hypothesis')), \
                        mock.patch.object(model_cache, 'resolve_snapshot',
                                          side_effect=resolve) as resolution, \
                        mock.patch.object(model_cache, 'whisper_snapshot',
                                          return_value=staging), \
                        mock.patch.dict(sys.modules, {'faster_whisper': whisper}), \
                        mock.patch.object(app.engine, 'cuda_available',
                                          return_value=False):
                    outcome = instance._transcribe_worker_inner(object())

                self.assertEqual(outcome, case.outcome)
                self.assertTrue(resolution.called)
                for call in resolution.call_args_list:
                    self.assertIs(call.kwargs['local_only'], True)
                # A staged fallback snapshot never outlives its recording.
                self.assertEqual([event for event, _ in events], case.events)
                self.assertEqual([thread for _, thread in events],
                                 [threading.get_ident()] * len(events))
                self.assertTrue(instance.transcriber.loaded('parakeet-tdt-0.6b-v3'))
                self.assertIs(instance.transcriber.model, mock.sentinel.parakeet_model)
                self.assertTrue(instance._dictation_model_ready())
                instance.retry_model.assert_not_called()
                self.assertEqual(
                    [call.args[0] for call in instance.notify.call_args_list],
                    case.titles)
                self.assertEqual(instance._deliver_text.called, case.delivered)
                if case.delivered:
                    self.assertEqual(instance._deliver_text.call_args.args[0],
                                     'synthetic fallback text')
                messages = str(instance.notify.mock_calls) + str(instance._log.mock_calls)
                self.assertNotIn('private', messages)

    def test_failed_decode_is_released_before_the_fallback_loads(self):
        # Inside the except block, the traceback keeps the failed decode's
        # frames alive, with any input or activation buffers they reference.
        class DecodeBuffers:
            pass

        decode_buffers = []
        alive_at_fallback_load = []

        def failed_decode(audio):
            buffers = DecodeBuffers()
            decode_buffers.append(weakref.ref(buffers))
            raise RuntimeError('private hypothesis')

        def load(*args, **kwargs):
            gc.collect()
            alive_at_fallback_load.append(decode_buffers[0]() is not None)

        fallback = self.mock_fallback()
        fallback.load.side_effect = load
        fallback.transcribe.return_value = 'synthetic text'
        instance = self.make_app()
        instance.transcriber.loaded.return_value = True
        instance.transcriber.transcribe.side_effect = failed_decode

        with mock.patch.object(app.engine, 'Transcriber', return_value=fallback):
            instance._transcribe_worker_inner(object())

        self.assertEqual(alive_at_fallback_load, [False])
        instance._deliver_text.assert_called_once()

    def test_nonparakeet_decode_failure_does_not_switch_models(self):
        instance = self.make_app()
        instance.settings['model']='small.en'
        instance.transcriber.loaded.return_value = True
        instance.transcriber.transcribe.side_effect = RuntimeError('decode failed')
        instance._transcribe_worker_inner(object())
        instance.transcriber.load.assert_not_called()
        instance._deliver_text.assert_not_called()
        self.assertEqual(instance.notify.call_args.args[0], 'Transcription failed')

    def test_successful_selected_model_load_and_decode_delivers_once(self):
        instance = self.make_app()
        instance.transcriber.transcribe.return_value = 'synthetic text'
        instance._transcribe_worker_inner(object())
        instance.transcriber.load.assert_called_once_with('parakeet-tdt-0.6b-v3', notify=instance.notify)
        instance._deliver_text.assert_called_once()
