import sys
import types
import unittest
from unittest import mock

import engine
import config


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

    def test_selectable_whisper_models_have_immutable_snapshots(self):
        transformers_models = {
            engine.NEMOTRON_NAME,
            "parakeet-tdt-0.6b-v3",
        }
        self.assertEqual(
            set(config.MODELS) - transformers_models,
            set(engine.WHISPER_MODELS),
        )
        for repository, revision in engine.WHISPER_MODELS.values():
            self.assertIn("/", repository)
            self.assertRegex(revision, r"^[0-9a-f]{40}$")

    def test_whisper_uses_reviewed_repository_and_revision(self):
        faster_whisper = types.ModuleType("faster_whisper")
        faster_whisper.WhisperModel = mock.Mock()
        with mock.patch.dict(sys.modules, {"faster_whisper": faster_whisper}), \
                mock.patch.object(engine, "cuda_available", return_value=False):
            transcriber = engine.Transcriber()
            transcriber._load_whisper("base.en", None)

        repository, revision = engine.WHISPER_MODELS["base.en"]
        faster_whisper.WhisperModel.assert_called_once_with(
            repository, revision=revision, device="cpu", compute_type="int8")

    def test_unknown_whisper_model_fails_before_backend_import(self):
        with mock.patch.dict(sys.modules, {"faster_whisper": None}):
            with self.assertRaisesRegex(ValueError, "unsupported Whisper model"):
                engine.Transcriber()._load_whisper("unreviewed/model", None)

    def test_whisper_transcribes_each_recording_without_previous_window_prompt(self):
        model = mock.Mock()
        model.transcribe.return_value = (
            iter([
                types.SimpleNamespace(text=" Standalone"),
                types.SimpleNamespace(text=" dictation "),
            ]),
            types.SimpleNamespace(duration_after_vad=1.25),
        )
        transcriber = engine.Transcriber()
        transcriber.model = model
        transcriber.backend = "whisper"

        text = transcriber.transcribe(mock.sentinel.audio, language="en")

        self.assertEqual(text, "Standalone dictation")
        model.transcribe.assert_called_once_with(
            mock.sentinel.audio,
            language="en",
            beam_size=1,
            vad_filter=True,
            without_timestamps=True,
            condition_on_previous_text=False,
        )
        self.assertEqual(transcriber.last_timing["speech_seconds"], 1.25)

    def test_whisper_does_not_decode_when_vad_finds_no_speech(self):
        segments = mock.MagicMock()
        model = mock.Mock()
        model.transcribe.return_value = (
            segments,
            types.SimpleNamespace(duration_after_vad=0.0),
        )
        transcriber = engine.Transcriber()
        transcriber.model = model
        transcriber.backend = "whisper"

        text = transcriber.transcribe(mock.sentinel.audio, language="en")

        self.assertEqual(text, "")
        segments.__iter__.assert_not_called()
        self.assertEqual(transcriber.last_timing["speech_seconds"], 0.0)

    def test_whisper_silence_warmup_exercises_vad_and_decode_kernels(self):
        numpy = types.ModuleType("numpy")
        numpy.float32 = "float32"
        numpy.zeros = mock.Mock(return_value=mock.sentinel.silence)
        transcriber = engine.Transcriber()
        transcriber.backend = "whisper"

        with mock.patch.dict(sys.modules, {"numpy": numpy}), \
                mock.patch.object(transcriber, "transcribe") as transcribe:
            transcriber.warmup(seconds=2.0)

        numpy.zeros.assert_called_once_with(32000, dtype="float32")
        self.assertEqual(
            transcribe.call_args_list,
            [
                mock.call(mock.sentinel.silence, language="en"),
                mock.call(
                    mock.sentinel.silence,
                    language="en",
                    _filter_silence=False,
                ),
            ],
        )

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
