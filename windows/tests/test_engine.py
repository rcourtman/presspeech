import sys
import types
import unittest
from unittest import mock

import engine


class ParakeetConfigurationTests(unittest.TestCase):
    def test_cuda_probe_fails_closed_when_torch_is_unavailable(self):
        with mock.patch.dict(sys.modules, {"torch": None}):
            self.assertFalse(engine.cuda_available())

    def test_cuda_probe_uses_packaged_torch_capability(self):
        torch = types.ModuleType("torch")
        torch.cuda = mock.Mock()
        torch.cuda.is_available.return_value = True
        with mock.patch.dict(sys.modules, {"torch": torch}):
            self.assertTrue(engine.cuda_available())

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

    def test_parakeet_generation_limit_follows_encoder_capacity(self):
        torch = mock.Mock()
        torch.tensor.return_value = "input length"
        features = mock.Mock()
        features.shape = (1, 1501, 128)
        features.device = "cuda"
        model = mock.Mock()
        model.max_symbols_per_step = 10
        model.encoder._get_subsampling_output_length.return_value.item.return_value = 188

        limit = engine._parakeet_max_new_tokens(model, features, torch)

        self.assertEqual(limit, 1880)
        torch.tensor.assert_called_once_with([1501], device="cuda")
        model.encoder._get_subsampling_output_length.assert_called_once_with(
            "input length")

    def test_processor_is_explicitly_configured_for_tdt(self):
        processor = types.SimpleNamespace(decoder_type=None)
        returned = engine._configure_parakeet_processor(processor)
        self.assertIs(returned, processor)
        self.assertEqual(processor.decoder_type, "tdt")

    def test_transformers_models_use_reviewed_immutable_revisions(self):
        torch = types.ModuleType("torch")
        torch.float16 = "float16"
        torch.float32 = "float32"
        torch.bfloat16 = "bfloat16"
        torch.cuda = mock.Mock()
        torch.cuda.is_available.return_value = False

        transformers = types.ModuleType("transformers")
        transformers.AutoProcessor = mock.Mock()
        transformers.AutoProcessor.from_pretrained.return_value = \
            types.SimpleNamespace(decoder_type=None)
        transformers.AutoModelForTDT = mock.Mock()
        transformers.AutoModelForRNNT = mock.Mock()
        transformers.MoonshineStreamingForConditionalGeneration = mock.Mock()

        with mock.patch.dict(sys.modules, {
                "torch": torch,
                "transformers": transformers,
        }):
            transcriber = engine.Transcriber(precision="auto")
            transcriber._load_parakeet(None)
            transformers.AutoProcessor.from_pretrained.assert_called_with(
                engine.PARAKEET_MODEL, revision=engine.PARAKEET_REVISION)
            transformers.AutoModelForTDT.from_pretrained.assert_called_once_with(
                engine.PARAKEET_MODEL, revision=engine.PARAKEET_REVISION,
                dtype="auto")

            transcriber._load_nemotron(None)
            transformers.AutoProcessor.from_pretrained.assert_called_with(
                engine.NEMOTRON_MODEL, revision=engine.NEMOTRON_REVISION)
            transformers.AutoModelForRNNT.from_pretrained.assert_called_once_with(
                engine.NEMOTRON_MODEL, revision=engine.NEMOTRON_REVISION,
                dtype="float32")

            transcriber._load_moonshine(None)
            transformers.AutoProcessor.from_pretrained.assert_called_with(
                engine.MOONSHINE_MODEL, revision=engine.MOONSHINE_REVISION)
            (transformers.MoonshineStreamingForConditionalGeneration
             .from_pretrained.assert_called_once_with(
                 engine.MOONSHINE_MODEL, revision=engine.MOONSHINE_REVISION,
                 dtype="float32"))

    def test_parakeet_load_fallbacks_keep_the_reviewed_revision(self):
        torch = types.ModuleType("torch")
        torch.float16 = "float16"
        torch.float32 = "float32"
        torch.bfloat16 = "bfloat16"
        torch.cuda = mock.Mock()
        torch.cuda.is_available.return_value = True
        torch.cuda.is_bf16_supported.return_value = True

        transformers = types.ModuleType("transformers")
        transformers.AutoProcessor = mock.Mock()
        transformers.AutoProcessor.from_pretrained.return_value = \
            types.SimpleNamespace(decoder_type=None)
        transformers.AutoModelForTDT = mock.Mock()
        model = mock.Mock()
        transformers.AutoModelForTDT.from_pretrained.side_effect = [
            RuntimeError("half precision unavailable"), model,
        ]

        with mock.patch.dict(sys.modules, {
                "torch": torch,
                "transformers": transformers,
        }):
            engine.Transcriber(precision="fp16")._load_parakeet(None)

        self.assertEqual(
            transformers.AutoModelForTDT.from_pretrained.call_args_list,
            [
                mock.call(
                    engine.PARAKEET_MODEL,
                    revision=engine.PARAKEET_REVISION,
                    dtype="float16"),
                mock.call(
                    engine.PARAKEET_MODEL,
                    revision=engine.PARAKEET_REVISION,
                    dtype="auto"),
            ],
        )

        transformers.AutoModelForTDT.from_pretrained.reset_mock()
        transformers.AutoModelForTDT.from_pretrained.side_effect = [
            TypeError("dtype is unsupported"), model,
        ]
        torch.cuda.is_available.return_value = False
        with mock.patch.dict(sys.modules, {
                "torch": torch,
                "transformers": transformers,
        }):
            engine.Transcriber(precision="auto")._load_parakeet(None)

        self.assertEqual(
            transformers.AutoModelForTDT.from_pretrained.call_args_list,
            [
                mock.call(
                    engine.PARAKEET_MODEL,
                    revision=engine.PARAKEET_REVISION,
                    dtype="auto"),
                mock.call(
                    engine.PARAKEET_MODEL,
                    revision=engine.PARAKEET_REVISION),
            ],
        )

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
