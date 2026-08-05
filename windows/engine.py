"""Transcription engines for Presspeech for Windows.

Backends:

- "parakeet-tdt-0.6b-v3" -> NVIDIA Parakeet-TDT-0.6B-v3 via HuggingFace
  Transformers, runs on CUDA when available. Cutting-edge accuracy, automatic
  punctuation and capitalization, 25 European languages. Same model family
  used by Presspeech on macOS.
- anything else -> faster-whisper (CTranslate2) model name, CUDA when
  available, CPU otherwise.
"""

import gc
import threading
import time

PARAKEET_MODEL = "nvidia/parakeet-tdt-0.6b-v3"
NEMOTRON_MODEL = "nvidia/nemotron-speech-streaming-en-0.6b"
MOONSHINE_MODEL = "UsefulSensors/moonshine-streaming-medium"

NEMOTRON_NAME = "nemotron-speech-streaming-en-0.6b"
MOONSHINE_NAME = "moonshine-streaming-medium"

# Stable feature shapes avoid a roughly one-second CUDA/cuDNN setup cost for
# every previously unseen recording length. The attention mask ensures padded
# audio is ignored, so this does not change the decoded speech.
PARAKEET_BUCKET_SECONDS = (15, 30, 60)


def is_parakeet(model_name):
    return model_name == "parakeet-tdt-0.6b-v3"


def is_nemotron(model_name):
    return model_name == NEMOTRON_NAME


def is_moonshine(model_name):
    return model_name == MOONSHINE_NAME


def _configure_parakeet_processor(processor):
    """Avoid repeatedly inferring the known decoder type from the full vocabulary."""
    processor.decoder_type = "tdt"
    return processor


def _parakeet_dtype(torch_module, device, precision):
    if device == "cuda" and precision == "fp16":
        return torch_module.float16
    if (device == "cuda" and precision == "bf16"
            and torch_module.cuda.is_bf16_supported()):
        return torch_module.bfloat16
    return "auto"


def _parakeet_bucket_seconds(audio_seconds):
    for bucket in PARAKEET_BUCKET_SECONDS:
        if audio_seconds <= bucket:
            return bucket
    # Very long dictation remains supported in 30-second increments. Its first
    # uncommon shape may pay a one-time setup cost; normal speech uses a warmed
    # bucket above.
    return int((audio_seconds + 29) // 30 * 30)


class Transcriber:
    def __init__(self, precision="auto"):
        self.lock = threading.Lock()
        self.inference_lock = threading.Lock()
        self.precision = precision
        self.model = None
        self.processor = None
        self.backend = None
        self.model_name = None
        self.last_timing = {}

    def loaded(self, model_name):
        return self.model is not None and self.model_name == model_name

    def load(self, model_name, notify=None):
        with self.lock:
            if self.loaded(model_name):
                return
            self._unload_locked()
            if is_parakeet(model_name):
                self._load_parakeet(notify)
            elif is_nemotron(model_name):
                self._load_nemotron(notify)
            elif is_moonshine(model_name):
                self._load_moonshine(notify)
            else:
                self._load_whisper(model_name, notify)
            self.model_name = model_name
            if notify is not None:
                notify("Presspeech", "Model %s ready." % model_name)

    def _load_parakeet(self, notify):
        import torch
        from transformers import AutoModelForTDT, AutoProcessor
        device = "cuda" if torch.cuda.is_available() else "cpu"
        if notify is not None:
            notify("Presspeech",
                   "Loading Parakeet-TDT v3 on %s (first run downloads ~2.5 GB)..." % device)
        self.processor = _configure_parakeet_processor(
            AutoProcessor.from_pretrained(PARAKEET_MODEL))
        requested_dtype = _parakeet_dtype(torch, device, self.precision)
        try:
            self.model = AutoModelForTDT.from_pretrained(
                PARAKEET_MODEL, dtype=requested_dtype)
        except TypeError:
            self.model = AutoModelForTDT.from_pretrained(PARAKEET_MODEL)
        except Exception as exc:
            if requested_dtype == "auto":
                raise
            if notify is not None:
                notify("Presspeech", "Half-precision load failed; retrying FP32 (%s)"
                       % str(exc)[:100])
            self.model = AutoModelForTDT.from_pretrained(PARAKEET_MODEL, dtype="auto")
        if device != "cpu":
            self.model.to(device)
        self.backend = "parakeet"
        self._device = device

    def _load_nemotron(self, notify):
        import torch
        from transformers import AutoModelForRNNT, AutoProcessor
        device = "cuda" if torch.cuda.is_available() else "cpu"
        if notify is not None:
            notify("Presspeech", "Loading Nemotron English ASR on %s..." % device)
        dtype = torch.float16 if device == "cuda" else torch.float32
        self.processor = AutoProcessor.from_pretrained(NEMOTRON_MODEL)
        self.model = AutoModelForRNNT.from_pretrained(
            NEMOTRON_MODEL, dtype=dtype).to(device)
        self.backend = "nemotron"
        self._device = device

    def _load_moonshine(self, notify):
        import torch
        from transformers import AutoProcessor, MoonshineStreamingForConditionalGeneration
        device = "cuda" if torch.cuda.is_available() else "cpu"
        if notify is not None:
            notify("Presspeech", "Loading Moonshine Medium on %s..." % device)
        dtype = torch.float16 if device == "cuda" else torch.float32
        self.processor = AutoProcessor.from_pretrained(MOONSHINE_MODEL)
        self.model = MoonshineStreamingForConditionalGeneration.from_pretrained(
            MOONSHINE_MODEL, dtype=dtype).to(device)
        self.backend = "moonshine"
        self._device = device

    def _load_whisper(self, model_name, notify):
        from faster_whisper import WhisperModel
        device = "cuda" if self._cuda_available() else "cpu"
        compute = "float16" if device == "cuda" else "int8"
        if notify is not None:
            notify("Presspeech", "Loading Whisper %s on %s..." % (model_name, device))
        self.model = WhisperModel(model_name, device=device, compute_type=compute)
        self.backend = "whisper"
        self._device = device

    def _unload_locked(self):
        if self.model is not None:
            del self.model
            self.model = None
        self.processor = None
        self.backend = None
        self.model_name = None

    def unload(self):
        """Release the active model and its native resources while keeping the app alive."""
        with self.inference_lock:
            with self.lock:
                backend = self.backend
                self._unload_locked()
            gc.collect()
            if backend != "whisper":
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:
                    pass

    def transcribe(self, audio, language="en"):
        requested_at = time.perf_counter()
        with self.inference_lock:
            acquired_at = time.perf_counter()
            with self.lock:
                model = self.model
                processor = self.processor
                backend = self.backend
            if model is None or backend is None:
                raise RuntimeError("no model loaded")
            self._backend_timing = {}
            if backend == "parakeet":
                text = self._transcribe_parakeet(model, processor, audio)
            elif backend == "nemotron":
                text = self._transcribe_nemotron(model, processor, audio)
            elif backend == "moonshine":
                text = self._transcribe_moonshine(model, processor, audio)
            else:
                segments, _info = model.transcribe(
                    audio, language=language, beam_size=1, vad_filter=False,
                    without_timestamps=True,
                )
                text = "".join(seg.text for seg in segments).strip()
            finished_at = time.perf_counter()
            self.last_timing = {
                "backend": backend,
                "lock_wait": acquired_at - requested_at,
                "inference": finished_at - acquired_at,
                **self._backend_timing,
            }
            return text

    def warmup(self, seconds=8.0, all_buckets=False):
        """Run representative inference so the next real dictation is ready."""
        import numpy as np
        with self.lock:
            backend = self.backend
        if backend == "parakeet" and all_buckets:
            for bucket in PARAKEET_BUCKET_SECONDS:
                self.transcribe(
                    np.zeros(bucket * 16000, dtype=np.float32), language="en")
            return
        self.transcribe(
            np.zeros(max(1, int(seconds * 16000)), dtype=np.float32), language="en")

    def _transcribe_parakeet(self, model, processor, audio):
        import torch
        bucket_seconds = _parakeet_bucket_seconds(len(audio) / 16000.0)
        started = time.perf_counter()
        inputs = processor(
            audio,
            sampling_rate=16000,
            return_tensors="pt",
            padding="max_length",
            max_length=bucket_seconds * 16000,
            truncation=True,
            return_attention_mask=True,
        )
        processed = time.perf_counter()
        inputs = {
            k: (v.to(device=model.device, dtype=model.dtype)
                if v.is_floating_point() else v.to(device=model.device))
            for k, v in inputs.items()
        }
        transferred = time.perf_counter()
        with torch.no_grad():
            output = model.generate(**inputs, return_dict_in_generate=True)
        generated = time.perf_counter()
        decoded, _timestamps = processor.decode(
            output.sequences, durations=output.durations, skip_special_tokens=True)
        decoded_at = time.perf_counter()
        self._backend_timing = {
            "bucket_seconds": bucket_seconds,
            "prepare": processed - started,
            "transfer": transferred - processed,
            "generate": generated - transferred,
            "decode": decoded_at - generated,
        }
        if isinstance(decoded, (list, tuple)):
            decoded = "".join(decoded)
        return decoded.strip()

    @staticmethod
    def _transcribe_nemotron(model, processor, audio):
        import torch
        inputs = processor(
            audio, sampling_rate=16000, return_tensors="pt"
        ).to(model.device, dtype=model.dtype)
        with torch.inference_mode():
            output = model.generate(**inputs, return_dict_in_generate=True)
        decoded = processor.decode(output.sequences, skip_special_tokens=True)
        if isinstance(decoded, (list, tuple)):
            decoded = "".join(decoded)
        return decoded.strip()

    @staticmethod
    def _transcribe_moonshine(model, processor, audio):
        import torch
        inputs = processor(
            audio, sampling_rate=16000, return_tensors="pt"
        ).to(model.device, dtype=model.dtype)
        # The model card recommends this audio-relative ceiling to prevent
        # autoregressive hallucination loops on short or noisy recordings.
        seq_lens = inputs.attention_mask.sum(dim=-1)
        factor = 6.5 / processor.feature_extractor.sampling_rate
        max_length = max(8, int((seq_lens * factor).max().item()))
        with torch.inference_mode():
            output = model.generate(**inputs, max_length=max_length)
        decoded = processor.decode(output[0], skip_special_tokens=True)
        return decoded.strip()

    @staticmethod
    def _cuda_available():
        try:
            import torch
            return torch.cuda.is_available()
        except Exception:
            return False
