import types
import unittest
from unittest import mock

import engine


class ParakeetConfigurationTests(unittest.TestCase):
    def test_candidate_model_names_use_their_transformers_backends(self):
        self.assertTrue(engine.is_nemotron("nemotron-speech-streaming-en-0.6b"))
        self.assertTrue(engine.is_moonshine("moonshine-streaming-medium"))
        self.assertFalse(engine.is_nemotron("small.en"))

    def test_parakeet_uses_smallest_pre_warmed_audio_bucket(self):
        self.assertEqual(engine._parakeet_bucket_seconds(1.0), 15)
        self.assertEqual(engine._parakeet_bucket_seconds(15.0), 15)
        self.assertEqual(engine._parakeet_bucket_seconds(15.01), 30)
        self.assertEqual(engine._parakeet_bucket_seconds(30.01), 60)
        self.assertEqual(engine._parakeet_bucket_seconds(61.0), 90)

    def test_processor_is_explicitly_configured_for_tdt(self):
        processor = types.SimpleNamespace(decoder_type=None)
        returned = engine._configure_parakeet_processor(processor)
        self.assertIs(returned, processor)
        self.assertEqual(processor.decoder_type, "tdt")

    def test_fp16_is_selected_only_for_cuda(self):
        torch = types.SimpleNamespace(float16="fp16", bfloat16="bf16")
        torch.cuda = mock.Mock()
        torch.cuda.is_bf16_supported.return_value = True
        self.assertEqual(engine._parakeet_dtype(torch, "cuda", "fp16"), "fp16")
        self.assertEqual(engine._parakeet_dtype(torch, "cpu", "fp16"), "auto")

    def test_unload_clears_active_model(self):
        transcriber = engine.Transcriber()
        transcriber.model = object()
        transcriber.processor = object()
        transcriber.backend = "whisper"
        transcriber.model_name = "base.en"
        transcriber.unload()
        self.assertIsNone(transcriber.model)
        self.assertIsNone(transcriber.processor)
        self.assertIsNone(transcriber.backend)
        self.assertIsNone(transcriber.model_name)


if __name__ == "__main__":
    unittest.main()
