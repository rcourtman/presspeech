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

    def test_loaded_parakeet_decode_failure_preserves_existing_fallback(self):
        instance = self.make_app()
        instance.transcriber.loaded.return_value = True
        instance.transcriber.transcribe.side_effect = [RuntimeError('GPU decode failed'), 'synthetic text']
        audio = object()
        instance._transcribe_worker_inner(audio)
        instance.transcriber.load.assert_called_once_with('base.en', notify=instance.notify)
        self.assertEqual(instance.transcriber.transcribe.call_args_list,
                         [mock.call(audio), mock.call(audio)])
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
