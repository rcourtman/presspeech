import sys
from contextlib import nullcontext
import types
import unittest
from unittest import mock

import engine
import config


class ParakeetConfigurationTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(engine.model_cache, "resolve_snapshot",
                                    return_value="synthetic-pinned-snapshot")
        self.resolve_snapshot = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(engine.model_cache, "whisper_snapshot",
                                    side_effect=lambda path, files, **kwargs: nullcontext(path))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_cuda_probe_fails_closed_when_torch_is_unavailable(self):
        with mock.patch.dict(sys.modules, {"torch": None}):
            self.assertFalse(engine.cuda_available())

    def test_only_explicit_whisper_vad_zero_supports_no_speech_diagnosis(self):
        self.assertTrue(engine.whisper_vad_rejected({
            "backend": "whisper", "speech_seconds": 0.0}))
        for timing in (
                {"backend": "parakeet", "speech_seconds": 0.0},
                {"backend": "whisper", "speech_seconds": 0.4},
                {"backend": "whisper", "speech_seconds": None},
                {"backend": "whisper", "speech_seconds": False},
                {}, None):
            with self.subTest(timing=timing):
                self.assertFalse(engine.whisper_vad_rejected(timing))

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

    def test_model_snapshot_reports_exact_reviewed_source(self):
        self.assertEqual(engine.model_snapshot("parakeet-tdt-0.6b-v3"), {
            "repository": engine.PARAKEET_MODEL,
            "revision": engine.PARAKEET_REVISION,
        })
        self.assertEqual(engine.model_snapshot("base.en"), {
            "repository": engine.WHISPER_MODELS["base.en"][0],
            "revision": engine.WHISPER_MODELS["base.en"][1],
        })
        for name in config.MODELS + [engine.MOONSHINE_NAME]:
            with self.subTest(model=name):
                source = engine.model_snapshot(name)
                self.assertRegex(source["revision"], r"^[0-9a-f]{40}$")
                self.assertIn("/", source["repository"])
        original = engine.model_snapshot("base.en")
        modified = engine.model_snapshot("base.en")
        modified["revision"] = "changed"
        self.assertEqual(engine.model_snapshot("base.en"), original)
        with self.assertRaisesRegex(ValueError, "unsupported speech model"):
            engine.model_snapshot("unreviewed/model")

    def test_model_cache_can_be_checked_without_network_fallback(self):
        self.assertEqual(
            engine._cached_model_path("parakeet-tdt-0.6b-v3", local_only=True),
            "synthetic-pinned-snapshot",
        )
        self.resolve_snapshot.assert_called_once()
        self.assertTrue(self.resolve_snapshot.call_args.kwargs["local_only"])

    def test_model_file_manifest_covers_every_inference_file(self):
        self.assertEqual(set(engine.MODEL_FILE_SHA256), set(engine.MODEL_CACHE_FILES))
        for name, required in engine.MODEL_CACHE_FILES.items():
            expected = (set(required) |
                        set(engine.MODEL_CACHE_OPTIONAL_FILES.get(name, ())))
            with self.subTest(model=name):
                self.assertEqual(set(engine.MODEL_FILE_SHA256[name]), expected)
                self.assertTrue(all(
                    len(digest) == 64 and
                    all(character in "0123456789abcdef" for character in digest)
                    for digest in engine.MODEL_FILE_SHA256[name].values()))

    def test_whisper_load_can_check_its_pinned_cache_without_network(self):
        faster_whisper = types.ModuleType("faster_whisper")
        faster_whisper.WhisperModel = mock.Mock()
        with mock.patch.dict(sys.modules, {"faster_whisper": faster_whisper}), \
                mock.patch.object(engine, "cuda_available", return_value=False):
            engine.Transcriber().load("base.en", local_only=True)

        self.assertEqual(
            self.resolve_snapshot.call_args.args[:2],
            (engine.WHISPER_MODELS["base.en"][0],
             engine.WHISPER_MODELS["base.en"][1]))
        self.assertTrue(self.resolve_snapshot.call_args.kwargs["local_only"])

    def test_model_cache_progress_callback_keeps_snapshot_pin_unchanged(self):
        progress = mock.Mock()
        self.assertEqual(engine._cached_model_path(
            "parakeet-tdt-0.6b-v3", progress_callback=progress),
            "synthetic-pinned-snapshot")
        snapshot = engine.model_snapshot("parakeet-tdt-0.6b-v3")
        self.resolve_snapshot.assert_called_once_with(
            snapshot["repository"], snapshot["revision"],
            engine.MODEL_CACHE_FILES["parakeet-tdt-0.6b-v3"],
            optional_files=engine.MODEL_CACHE_OPTIONAL_FILES[
                "parakeet-tdt-0.6b-v3"],
            required_any=engine.MODEL_CACHE_ALTERNATIVES[
                "parakeet-tdt-0.6b-v3"],
            expected_sha256s=engine.MODEL_FILE_SHA256[
                "parakeet-tdt-0.6b-v3"],
            integrity_cache_dir=engine.cfg.MODEL_INTEGRITY_CACHE_DIR,
            progress=progress)

    def test_parakeet_uses_smallest_pre_warmed_audio_bucket(self):
        self.assertEqual(engine._parakeet_bucket_seconds(1.0), 15)
        self.assertEqual(engine._parakeet_bucket_seconds(15.0), 15)
        self.assertEqual(engine._parakeet_bucket_seconds(15.01), 30)
        self.assertEqual(engine._parakeet_bucket_seconds(30.01), 60)
        with self.assertRaisesRegex(ValueError, "60-second bound"):
            engine._parakeet_bucket_seconds(60.01)

    def test_parakeet_long_audio_windows_are_bounded_balanced_and_complete(self):
        sample_rate = 10
        sample_count = 6001
        windows = engine._parakeet_chunk_windows(
            sample_count, sample_rate=sample_rate)

        self.assertEqual(len(windows), 11)
        self.assertEqual(windows[0].owned_start, 0)
        self.assertEqual(windows[-1].owned_end, sample_count)
        self.assertLessEqual(
            max(window.audio_end - window.audio_start for window in windows),
            engine.PARAKEET_MAX_WINDOW_SECONDS * sample_rate,
        )
        for previous, current in zip(windows, windows[1:]):
            self.assertEqual(previous.owned_end, current.owned_start)
            self.assertLessEqual(
                abs((previous.owned_end - previous.owned_start)
                    - (current.owned_end - current.owned_start)),
                1,
            )
            self.assertLess(current.audio_start, previous.audio_end)
        self.assertEqual(
            engine._parakeet_chunk_windows(600, sample_rate=sample_rate),
            [engine._ParakeetWindow(0, 600, 0, 600)],
        )

    def test_parakeet_overlap_ownership_reassembles_split_subwords_once(self):
        first = engine._ParakeetWindow(
            audio_start=0, audio_end=60, owned_start=0, owned_end=40)
        second = engine._ParakeetWindow(
            audio_start=20, audio_end=80, owned_start=40, owned_end=80)
        first_text = engine._owned_parakeet_text(
            "Hello international",
            [[
                # Transformers' documented TDT timestamps omit the spaces
                # present in the processor's complete decoded text. A token
                # is also not necessarily a whole word.
                {"token": "H", "start": 0.5, "end": 0.6},
                {"token": "ello", "start": 0.6, "end": 1.0},
                {"token": "intern", "start": 3.8, "end": 4.0},
                {"token": "ational", "start": 4.2, "end": 4.4},
            ]],
            first,
            sample_rate=10,
        )
        second_text = engine._owned_parakeet_text(
            "international work",
            [[
                {"token": "intern", "start": 1.8, "end": 2.0},
                {"token": "ational", "start": 2.2, "end": 2.4},
                {"token": "work", "start": 3.0, "end": 3.4},
            ]],
            second,
            sample_rate=10,
        )

        self.assertEqual(first_text, ("Hello intern", False))
        self.assertEqual(second_text, ("ational work", True))
        self.assertEqual(
            engine._join_owned_parakeet_text([first_text, second_text]),
            "Hello international work",
        )
        self.assertEqual(
            engine._join_owned_parakeet_text([
                ("forty", False), ("-two", False),
            ]),
            "forty-two",
        )

    def test_parakeet_timestamp_alignment_preserves_decoder_whitespace(self):
        # Reduced from the public Transformers Parakeet TDT v3 example. Its
        # timestamp token strings contain no word-boundary spaces even though
        # the complete decode does.
        decoded = "mister Quilter is the apostle."
        records = [
            {"token": "m", "start": 0.24, "end": 0.48},
            {"token": "ister", "start": 0.48, "end": 0.64},
            {"token": "Qu", "start": 0.64, "end": 0.88},
            {"token": "il", "start": 0.88, "end": 1.12},
            {"token": "ter", "start": 1.12, "end": 1.36},
            {"token": "is", "start": 1.36, "end": 1.44},
            {"token": "the", "start": 1.44, "end": 1.60},
            {"token": "ap", "start": 1.68, "end": 1.76},
            {"token": "ost", "start": 1.76, "end": 1.92},
            {"token": "le", "start": 2.00, "end": 2.16},
            {"token": ".", "start": 2.16, "end": 2.16},
        ]
        window = engine._ParakeetWindow(
            audio_start=0, audio_end=300, owned_start=0, owned_end=300)

        self.assertEqual(
            engine._owned_parakeet_text(
                decoded, [records], window, sample_rate=100),
            (decoded, False),
        )

        # The exact locked tokenizers 0.23.1 stream includes those spaces.
        # Supporting the documented shape must not regress the release shape.
        spaced_records = [dict(record) for record in records]
        for index in (2, 5, 6, 7):
            spaced_records[index]["token"] = " " + spaced_records[index]["token"]
        self.assertEqual(
            engine._owned_parakeet_text(
                decoded, [spaced_records], window, sample_rate=100),
            (decoded, False),
        )

    def test_parakeet_timestamp_alignment_rejects_non_whitespace_difference(self):
        with self.assertRaisesRegex(RuntimeError, "do not match"):
            engine._parakeet_timestamp_text_parts(
                "safe private text",
                [{"token": "safe"}, {"token": "text"}],
            )

    def test_parakeet_overlap_fails_closed_when_text_has_no_timestamps(self):
        window = engine._ParakeetWindow(
            audio_start=0, audio_end=60, owned_start=0, owned_end=40)
        with self.assertRaisesRegex(RuntimeError, "without timestamps"):
            engine._owned_parakeet_text(
                "private words", [[]], window, sample_rate=10)
        with self.assertRaisesRegex(RuntimeError, "do not match"):
            engine._owned_parakeet_text(
                "private words",
                [[{"token": "private", "start": 0.1, "end": 0.5}]],
                window,
                sample_rate=10,
            )

    def test_parakeet_long_transcription_aggregates_bounded_chunk_work(self):
        transcriber = engine.Transcriber()
        audio = list(range(8))
        windows = [
            engine._ParakeetWindow(0, 5, 0, 3),
            engine._ParakeetWindow(1, 8, 3, 8),
        ]
        chunk_results = [
            ("First", mock.sentinel.first_timestamps, {
                "bucket_seconds": 15,
                "prepare": 0.1, "transfer": 0.2,
                "generate": 0.3, "decode": 0.4,
            }),
            (" second", mock.sentinel.second_timestamps, {
                "bucket_seconds": 30,
                "prepare": 1.0, "transfer": 2.0,
                "generate": 3.0, "decode": 4.0,
            }),
        ]

        with mock.patch.object(
                engine, "_parakeet_chunk_windows", return_value=windows), \
                mock.patch.object(
                    engine, "PARAKEET_SAMPLE_RATE", 1), \
                mock.patch.object(
                    transcriber, "_transcribe_parakeet_chunk",
                    side_effect=chunk_results) as transcribe_chunk, \
                mock.patch.object(
                    engine, "_owned_parakeet_text",
                    side_effect=[("First", False), (" second", False)]):
            text = transcriber._transcribe_parakeet(
                mock.sentinel.model, mock.sentinel.processor, audio)

        self.assertEqual(text, "First second")
        self.assertEqual(transcribe_chunk.call_args_list, [
            mock.call(mock.sentinel.model, mock.sentinel.processor, audio[0:5]),
            mock.call(mock.sentinel.model, mock.sentinel.processor, audio[1:8]),
        ])
        self.assertEqual(transcriber._backend_timing, {
            "bucket_seconds": 30,
            "chunk_count": 2,
            "max_chunk_seconds": 7.0,
            "prepare": 1.1,
            "transfer": 2.2,
            "generate": 3.3,
            "decode": 4.4,
        })

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

    def test_stage_barrier_is_benchmark_only_and_cuda_only(self):
        torch = mock.Mock()

        engine.Transcriber()._parakeet_stage_barrier(torch, "cuda:0")
        engine.Transcriber(measure_stages=True)._parakeet_stage_barrier(
            torch, "cpu")
        torch.cuda.synchronize.assert_not_called()

        engine.Transcriber(measure_stages=True)._parakeet_stage_barrier(
            torch, "cuda:0")
        torch.cuda.synchronize.assert_called_once_with("cuda:0")

    def test_parakeet_benchmark_brackets_every_reported_stage(self):
        torch = types.ModuleType("torch")
        torch.no_grad = mock.MagicMock()
        features = mock.Mock()
        features.is_floating_point.return_value = True
        attention_mask = mock.Mock()
        attention_mask.is_floating_point.return_value = False
        processor = mock.Mock()
        processor.return_value = {
            "input_features": features,
            "attention_mask": attention_mask,
        }
        processor.decode.return_value = (" measured transcription ", None)
        model = mock.Mock()
        model.device = "cuda:0"
        model.dtype = "float16"
        model.generate.return_value = types.SimpleNamespace(
            sequences=mock.sentinel.sequences,
            durations=mock.sentinel.durations,
        )
        transcriber = engine.Transcriber(measure_stages=True)
        audio = [0.0] * 16000

        with mock.patch.dict(sys.modules, {"torch": torch}), \
                mock.patch.object(
                    engine, "_parakeet_max_new_tokens", return_value=123), \
                mock.patch.object(
                    transcriber, "_parakeet_stage_barrier") as barrier:
            text = transcriber._transcribe_parakeet(
                model, processor, audio)

        self.assertEqual(text, "measured transcription")
        self.assertEqual(barrier.call_count, 5)
        self.assertEqual(
            barrier.call_args_list,
            [mock.call(torch, "cuda:0")] * 5,
        )
        processor.assert_called_once_with(
            audio,
            sampling_rate=16000,
            return_tensors="pt",
            padding="max_length",
            max_length=15 * 16000,
            truncation=True,
            return_attention_mask=True,
        )
        self.assertEqual(transcriber._backend_timing["chunk_count"], 1)
        self.assertEqual(transcriber._backend_timing["max_chunk_seconds"], 1.0)

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
                "synthetic-pinned-snapshot", local_files_only=True, revision=engine.PARAKEET_REVISION,
                token=False, trust_remote_code=False)
            transformers.AutoModelForTDT.from_pretrained.assert_called_once_with(
                "synthetic-pinned-snapshot", local_files_only=True, revision=engine.PARAKEET_REVISION,
                dtype="auto", token=False, trust_remote_code=False,
                use_safetensors=True)

            transcriber._load_nemotron(None)
            transformers.AutoProcessor.from_pretrained.assert_called_with(
                "synthetic-pinned-snapshot", local_files_only=True, revision=engine.NEMOTRON_REVISION,
                token=False, trust_remote_code=False)
            transformers.AutoModelForRNNT.from_pretrained.assert_called_once_with(
                "synthetic-pinned-snapshot", local_files_only=True, revision=engine.NEMOTRON_REVISION,
                dtype="float32", token=False, trust_remote_code=False,
                use_safetensors=True)

            transcriber._load_moonshine(None)
            transformers.AutoProcessor.from_pretrained.assert_called_with(
                "synthetic-pinned-snapshot", local_files_only=True, revision=engine.MOONSHINE_REVISION,
                token=False, trust_remote_code=False)
            (transformers.MoonshineStreamingForConditionalGeneration
             .from_pretrained.assert_called_once_with(
                 "synthetic-pinned-snapshot", local_files_only=True, revision=engine.MOONSHINE_REVISION,
                 dtype="float32", token=False, trust_remote_code=False,
                 use_safetensors=True))

    def test_model_load_rejects_a_preimported_endpoint_override(self):
        torch = types.ModuleType("torch")
        torch.cuda = mock.Mock()
        torch.cuda.is_available.return_value = False
        transformers = types.ModuleType("transformers")
        transformers.AutoProcessor = mock.Mock()
        transformers.AutoModelForTDT = mock.Mock()
        hostile_constants = types.SimpleNamespace(
            ENDPOINT="https://attacker.example",
            HF_HUB_DISABLE_TELEMETRY=True,
            HF_HUB_DISABLE_IMPLICIT_TOKEN=True,
            HF_HUB_USER_AGENT_ORIGIN=None,
        )

        with mock.patch.dict(sys.modules, {
                "torch": torch,
                "transformers": transformers,
                "huggingface_hub.constants": hostile_constants,
        }):
            with self.assertRaisesRegex(
                    engine.model_network.ModelNetworkPolicyError, "endpoint"):
                engine.Transcriber()._load_parakeet(None)

        transformers.AutoProcessor.from_pretrained.assert_not_called()
        transformers.AutoModelForTDT.from_pretrained.assert_not_called()

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
            "synthetic-pinned-snapshot", revision=revision, device="cpu", compute_type="int8",
            local_files_only=True,
            use_auth_token=False)

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
            vad_parameters=engine.WHISPER_VAD_POLICY,
        )
        self.assertEqual(transcriber.last_timing["speech_seconds"], 1.25)

    def test_multilingual_whisper_detects_language_when_unspecified(self):
        model = mock.Mock()
        model.transcribe.return_value = (
            iter([types.SimpleNamespace(text=" Dzie\u0144 dobry ")]),
            types.SimpleNamespace(duration_after_vad=0.8, language="pl"),
        )
        transcriber = engine.Transcriber()
        transcriber.model = model
        transcriber.backend = "whisper"

        text = transcriber.transcribe(mock.sentinel.audio)

        self.assertEqual(text, "Dzie\u0144 dobry")
        model.transcribe.assert_called_once_with(
            mock.sentinel.audio,
            language=None,
            beam_size=1,
            vad_filter=True,
            without_timestamps=True,
            condition_on_previous_text=False,
            vad_parameters=engine.WHISPER_VAD_POLICY,
        )
        self.assertEqual(transcriber.last_timing["detected_language"], "pl")

    def test_whisper_vad_policy_is_complete_and_copied_per_request(self):
        first = engine.whisper_vad_parameters()
        second = engine.whisper_vad_parameters()

        self.assertEqual(first, {
            "threshold": 0.5,
            "neg_threshold": 0.35,
            "min_speech_duration_ms": 0,
            "min_silence_duration_ms": 160,
            "speech_pad_ms": 400,
        })
        self.assertIsNot(first, second)
        first["speech_pad_ms"] = 0
        self.assertEqual(second["speech_pad_ms"], 400)

    def test_whisper_vad_pause_override_is_validated_and_isolated(self):
        expected = dict(engine.WHISPER_VAD_POLICY)
        expected["min_silence_duration_ms"] = 2000
        self.assertEqual(engine.whisper_vad_parameters(2000), expected)
        self.assertEqual(
            engine.Transcriber()._whisper_vad_policy,
            engine.WHISPER_VAD_POLICY,
        )
        for invalid in (-1, True, 2000.0, "2000"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "non-negative integer"):
                    engine.whisper_vad_parameters(invalid)

    def test_whisper_vad_override_is_fresh_for_each_transcription(self):
        observed_policies = []

        def transcribe(_audio, **options):
            observed_policies.append(dict(options["vad_parameters"]))
            options["vad_parameters"]["min_silence_duration_ms"] = 7
            return (iter([types.SimpleNamespace(text=" speech")]),
                    types.SimpleNamespace(duration_after_vad=1.0))

        model = mock.Mock()
        model.transcribe.side_effect = transcribe
        transcriber = engine.Transcriber(whisper_vad_min_silence_ms=2000)
        transcriber.model = model
        transcriber.backend = "whisper"

        transcriber.transcribe(mock.sentinel.audio)
        transcriber.transcribe(mock.sentinel.audio)

        self.assertEqual(len(observed_policies), 2)
        self.assertTrue(all(
            policy["min_silence_duration_ms"] == 2000
            and policy["threshold"] == 0.5
            for policy in observed_policies))
        self.assertEqual(engine.WHISPER_VAD_POLICY["min_silence_duration_ms"], 160)

    def test_whisper_does_not_decode_when_vad_finds_no_speech(self):
        segments = mock.MagicMock()
        model = mock.Mock()
        model.transcribe.return_value = (
            segments,
            # Automatic detection on an empty feature window can still return
            # a code; it must not become benchmark evidence for silence.
            types.SimpleNamespace(duration_after_vad=0.0, language="pl"),
        )
        transcriber = engine.Transcriber()
        transcriber.model = model
        transcriber.backend = "whisper"

        text = transcriber.transcribe(mock.sentinel.audio)

        self.assertEqual(text, "")
        segments.__iter__.assert_not_called()
        self.assertEqual(transcriber.last_timing["speech_seconds"], 0.0)
        self.assertIsNone(transcriber.last_timing["detected_language"])

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
                    "synthetic-pinned-snapshot", local_files_only=True,
                    revision=engine.PARAKEET_REVISION,
                    dtype="float16", token=False, trust_remote_code=False,
                    use_safetensors=True),
                mock.call(
                    "synthetic-pinned-snapshot", local_files_only=True,
                    revision=engine.PARAKEET_REVISION,
                    dtype="auto", token=False, trust_remote_code=False,
                    use_safetensors=True),
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
                    "synthetic-pinned-snapshot", local_files_only=True,
                    revision=engine.PARAKEET_REVISION,
                    dtype="auto", token=False, trust_remote_code=False,
                    use_safetensors=True),
                mock.call(
                    "synthetic-pinned-snapshot", local_files_only=True,
                    revision=engine.PARAKEET_REVISION,
                    token=False, trust_remote_code=False,
                    use_safetensors=True),
            ],
        )

    def test_every_selectable_backend_has_an_exact_inference_file_contract(self):
        self.assertTrue(set(config.MODELS).issubset(engine.MODEL_CACHE_FILES))
        self.assertEqual(set(engine.MODEL_CACHE_FILES), set(engine.WHISPER_MODELS) | {
            "parakeet-tdt-0.6b-v3", engine.NEMOTRON_NAME, engine.MOONSHINE_NAME})
        for name, files in engine.MODEL_CACHE_FILES.items():
            with self.subTest(model=name):
                self.assertIn("tokenizer.json", files)
                self.assertIn("config.json", files)
                self.assertFalse(any("*" in item for item in files))
                self.assertEqual(engine._cached_model_path(name), "synthetic-pinned-snapshot")
                snapshot = engine.model_snapshot(name)
                self.resolve_snapshot.assert_called_with(
                    snapshot["repository"], snapshot["revision"], files,
                    optional_files=engine.MODEL_CACHE_OPTIONAL_FILES.get(name, ()),
                    required_any=engine.MODEL_CACHE_ALTERNATIVES.get(name, ()),
                    expected_sha256s=engine.MODEL_FILE_SHA256[name],
                    integrity_cache_dir=engine.cfg.MODEL_INTEGRITY_CACHE_DIR)

    def test_supplemental_configs_do_not_become_required_downloads(self):
        for name in ("parakeet-tdt-0.6b-v3", engine.NEMOTRON_NAME, engine.MOONSHINE_NAME):
            with self.subTest(model=name):
                self.assertNotIn("generation_config.json", engine.MODEL_CACHE_FILES[name])
                self.assertNotIn("tokenizer_config.json", engine.MODEL_CACHE_FILES[name])
                self.assertIn("generation_config.json", engine.MODEL_CACHE_OPTIONAL_FILES[name])
                self.assertTrue(engine.MODEL_CACHE_ALTERNATIVES[name])
        self.assertEqual(engine.MODEL_CACHE_ALTERNATIVES[engine.MOONSHINE_NAME],
                         (("processor_config.json", "preprocessor_config.json"),))
        self.assertNotIn("special_tokens_map.json", engine.MODEL_CACHE_FILES[engine.MOONSHINE_NAME])
        self.assertNotIn("preprocessor_config.json", engine.MODEL_CACHE_FILES["turbo"])
        self.assertIn("preprocessor_config.json", engine.MODEL_CACHE_OPTIONAL_FILES["turbo"])

    def test_all_whisper_aliases_construct_from_local_snapshot_with_pinned_flags(self):
        backend = types.SimpleNamespace(WhisperModel=mock.Mock())
        with mock.patch.dict(sys.modules, {"faster_whisper": backend}), \
                mock.patch.object(engine, "cuda_available", return_value=False):
            for name, (_repository, revision) in engine.WHISPER_MODELS.items():
                with self.subTest(model=name):
                    engine.Transcriber()._load_whisper(name, None)
                    backend.WhisperModel.assert_called_with(
                        "synthetic-pinned-snapshot", revision=revision,
                        device="cpu", compute_type="int8", local_files_only=True,
                        use_auth_token=False)

    def test_corrupt_backend_parse_does_not_trigger_another_snapshot_attempt(self):
        torch = types.SimpleNamespace(cuda=mock.Mock(), float16="float16",
                                      float32="float32", bfloat16="bfloat16")
        torch.cuda.is_available.return_value = True
        transformers = types.SimpleNamespace(AutoProcessor=mock.Mock(), AutoModelForTDT=mock.Mock())
        transformers.AutoProcessor.from_pretrained.return_value = types.SimpleNamespace(decoder_type=None)
        transformers.AutoModelForTDT.from_pretrained.side_effect = ValueError("corrupt weights")
        with mock.patch.dict(sys.modules, {"torch": torch, "transformers": transformers}):
            with self.assertRaisesRegex(ValueError, "corrupt weights"):
                engine.Transcriber(precision="fp16")._load_parakeet(None)
        self.resolve_snapshot.assert_called_once()
        transformers.AutoModelForTDT.from_pretrained.assert_called_once()

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


class WhisperSnapshotLifecycleTests(unittest.TestCase):
    def backend(self):
        backend = types.SimpleNamespace(WhisperModel=mock.Mock())
        staging = mock.MagicMock()
        staging.__enter__.return_value = 'private-snapshot'
        return backend, staging

    def test_constructor_failure_releases_private_snapshot(self):
        backend, staging = self.backend()
        backend.WhisperModel.side_effect = ValueError('corrupt model')
        with mock.patch.dict(sys.modules, {'faster_whisper': backend}), \
                mock.patch.object(engine, 'cuda_available', return_value=False), \
                mock.patch.object(engine, '_cached_model_path', return_value='cached-snapshot'), \
                mock.patch.object(engine.model_cache, 'whisper_snapshot', return_value=staging):
            with self.assertRaisesRegex(ValueError, 'corrupt model'):
                engine.Transcriber()._load_whisper('base.en', None)
        staging.__exit__.assert_called_once()
        self.assertIs(staging.__exit__.call_args.args[-3], ValueError)

    def test_success_keeps_private_snapshot_until_model_unload(self):
        backend, staging = self.backend()
        with mock.patch.dict(sys.modules, {'faster_whisper': backend}), \
                mock.patch.object(engine, 'cuda_available', return_value=False), \
                mock.patch.object(engine, '_cached_model_path', return_value='cached-snapshot'), \
                mock.patch.object(engine.model_cache, 'whisper_snapshot', return_value=staging):
            instance = engine.Transcriber(); instance._load_whisper('base.en', None)
        staging.__exit__.assert_not_called()
        instance._unload_locked()
        staging.__exit__.assert_called_once()
        self.assertEqual(staging.__exit__.call_args.args[-3:], (None, None, None))
        self.assertIsNone(instance._model_files)
