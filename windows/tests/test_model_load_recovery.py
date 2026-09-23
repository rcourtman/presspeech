"""Application model-load failures must not authorize a different download."""
import unittest
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

    def test_loaded_parakeet_decode_failure_uses_only_cached_fallback(self):
        instance = self.make_app()
        instance.transcriber.loaded.return_value = True
        instance.transcriber.transcribe.side_effect = [RuntimeError('GPU decode failed'), 'synthetic text']
        audio = object()
        instance._transcribe_worker_inner(audio)
        instance.transcriber.load.assert_called_once_with(
            'base.en', notify=instance.notify, local_only=True)
        self.assertEqual(instance.transcriber.transcribe.call_args_list,
                         [mock.call(audio), mock.call(audio)])
        instance._deliver_text.assert_called_once()

    def test_missing_cached_fallback_does_not_download_or_decode_again(self):
        instance = self.make_app()
        instance.transcriber.loaded.return_value = True
        instance.transcriber.transcribe.side_effect = RuntimeError('private hypothesis')
        instance.transcriber.load.side_effect = model_cache.ModelCacheMissingError(
            'private cache path')
        audio = object()

        instance._transcribe_worker_inner(audio)

        instance.transcriber.load.assert_called_once_with(
            'base.en', notify=instance.notify, local_only=True)
        instance.transcriber.transcribe.assert_called_once_with(audio)
        instance._deliver_text.assert_not_called()
        messages = str(instance.notify.mock_calls) + str(instance._log.mock_calls)
        self.assertNotIn('private hypothesis', messages)
        self.assertNotIn('private cache path', messages)
        self.assertIn('no model was downloaded', messages)

    def test_corrupt_cached_fallback_does_not_expose_error_or_decode_again(self):
        instance = self.make_app()
        instance.transcriber.loaded.return_value = True
        instance.transcriber.transcribe.side_effect = RuntimeError('private hypothesis')
        instance.transcriber.load.side_effect = model_cache.ModelCacheCorruptError(
            'private cache path')

        instance._transcribe_worker_inner(object())

        instance.transcriber.load.assert_called_once_with(
            'base.en', notify=instance.notify, local_only=True)
        instance.transcriber.transcribe.assert_called_once()
        instance._deliver_text.assert_not_called()
        self.assertEqual(
            instance._log.call_args.args[0],
            'fallback model load failed; error details suppressed')
        messages = str(instance.notify.mock_calls) + str(instance._log.mock_calls)
        self.assertNotIn('private hypothesis', messages)
        self.assertNotIn('private cache path', messages)

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
