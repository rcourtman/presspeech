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
from contextlib import ExitStack
from dataclasses import dataclass
import math
import threading
import time

import config as cfg
import model_network
import model_cache
from model_integrity import MODEL_FILE_SHA256

PARAKEET_MODEL = "nvidia/parakeet-tdt-0.6b-v3"
NEMOTRON_MODEL = "nvidia/nemotron-speech-streaming-en-0.6b"
MOONSHINE_MODEL = "UsefulSensors/moonshine-streaming-medium"

# A Presspeech release should always load the model snapshots exercised by its
# native QA. Hugging Face model names otherwise resolve through mutable main
# branches, allowing a fresh install to change without a Presspeech update.
PARAKEET_REVISION = "541d1f99c6b0c3cd0b11a95167540bb8edefd82b"
NEMOTRON_REVISION = "ebe59e5a817142986528bbbee5dba8db7b38ed50"
MOONSHINE_REVISION = "57b843633a8c183cadf6699ffa761377a933a866"

# faster-whisper's short model names otherwise resolve through its mutable
# alias table and the repositories' main branches. Keep both parts explicit so
# a Presspeech release always downloads the CTranslate2 snapshots it reviewed.
WHISPER_MODELS = {
    "base.en": (
        "Systran/faster-whisper-base.en",
        "3d3d5dee26484f91867d81cb899cfcf72b96be6c",
    ),
    "small.en": (
        "Systran/faster-whisper-small.en",
        "d1d751a5f8271d482d14ca55d9e2deeebbae577f",
    ),
    "medium.en": (
        "Systran/faster-whisper-medium.en",
        "a29b04bd15381511a9af671baec01072039215e3",
    ),
    "turbo": (
        "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
    ),
}

# Exact inference files reviewed at the pinned commits above. Keep these in
# sync with revision changes. Alternate .nemo/.gguf/.bin exports are deliberately
# excluded from the Transformers path; Whisper must always have its own pinned
# tokenizer to prevent faster-whisper's unpinned fallback tokenizer download.
_TRANSFORMERS_FILES = ("config.json", "model.safetensors", "tokenizer.json")
_TRANSFORMERS_OPTIONAL = ("generation_config.json", "tokenizer_config.json",
                          "processor_config.json")
MODEL_CACHE_FILES = {
    "parakeet-tdt-0.6b-v3": _TRANSFORMERS_FILES,
    "nemotron-speech-streaming-en-0.6b": _TRANSFORMERS_FILES,
    "moonshine-streaming-medium": _TRANSFORMERS_FILES,
    **{name: ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")
       for name in ("base.en", "small.en", "medium.en")},
    "turbo": ("config.json", "model.bin", "tokenizer.json", "vocabulary.json"),
}
# The pinned loaders use existing local defaults when supplemental JSON is
# absent. Fetch these during a necessary download, but do not turn their absence
# into network activity. Validate any that are present before construction.
MODEL_CACHE_OPTIONAL_FILES = {
    "parakeet-tdt-0.6b-v3": _TRANSFORMERS_OPTIONAL,
    "nemotron-speech-streaming-en-0.6b": _TRANSFORMERS_OPTIONAL,
    "moonshine-streaming-medium": _TRANSFORMERS_OPTIONAL + (
        "preprocessor_config.json", "special_tokens_map.json"),
    "turbo": ("preprocessor_config.json",),
}
# Transformers requires feature-extractor settings: either the modern unified
# processor config or a legacy preprocessor config. Only Moonshine's reviewed
# snapshot offers both layouts; the other reviewed commits contain the former.
MODEL_CACHE_ALTERNATIVES = {
    name: (("processor_config.json", "preprocessor_config.json")
           if name == "moonshine-streaming-medium" else ("processor_config.json",),)
    for name in ("parakeet-tdt-0.6b-v3", "nemotron-speech-streaming-en-0.6b",
                 "moonshine-streaming-medium")
}


def _validate_model_integrity_manifests():
    if set(MODEL_FILE_SHA256) != set(MODEL_CACHE_FILES):
        raise RuntimeError("speech-model SHA-256 manifest coverage is incomplete")
    for name, required_files in MODEL_CACHE_FILES.items():
        allowed = set(required_files) | set(MODEL_CACHE_OPTIONAL_FILES.get(name, ()))
        manifest = MODEL_FILE_SHA256[name]
        if (set(manifest) != allowed or
                any(not isinstance(digest, str) or
                    len(digest) != 64 or
                    any(char not in "0123456789abcdef" for char in digest)
                    for digest in manifest.values())):
            raise RuntimeError(
                "speech-model SHA-256 manifest is invalid: " + name)


_validate_model_integrity_manifests()


def _cached_model_path(model_name, *, local_only=False, progress_callback=None):
    snapshot = model_snapshot(model_name)
    options = {}
    if local_only:
        options["local_only"] = True
    if progress_callback is not None:
        options["progress"] = progress_callback
    return model_cache.resolve_snapshot(
        snapshot["repository"], snapshot["revision"], MODEL_CACHE_FILES[model_name],
        optional_files=MODEL_CACHE_OPTIONAL_FILES.get(model_name, ()),
        required_any=MODEL_CACHE_ALTERNATIVES.get(model_name, ()),
        expected_sha256s=MODEL_FILE_SHA256[model_name],
        integrity_cache_dir=cfg.MODEL_INTEGRITY_CACHE_DIR,
        **options)


# Pin Presspeech's Silero boundary policy for push-to-talk clips. In the
# faster-whisper 1.2.1 release, WhisperModel.transcribe (used here) defaults to
# VadOptions' 2,000 ms silence split when options are omitted; the separate
# BatchedInferencePipeline defaults to 160 ms. Presspeech explicitly uses
# 160 ms, not an implicit WhisperModel default. Change this reviewed product
# policy only alongside real-dictation and silence benchmarks.
WHISPER_VAD_POLICY = {
    "threshold": 0.5,
    "neg_threshold": 0.35,
    "min_speech_duration_ms": 0,
    "min_silence_duration_ms": 160,
    "speech_pad_ms": 400,
}

NEMOTRON_NAME = "nemotron-speech-streaming-en-0.6b"
MOONSHINE_NAME = "moonshine-streaming-medium"


def whisper_vad_parameters(min_silence_duration_ms=None):
    """Return a fresh faster-whisper VAD policy for one transcription.

    ``min_silence_duration_ms`` is an opt-in benchmark parameter. Product
    transcribers omit it and retain the reviewed release policy.
    """
    if min_silence_duration_ms is not None:
        if (isinstance(min_silence_duration_ms, bool)
                or not isinstance(min_silence_duration_ms, int)
                or min_silence_duration_ms < 0):
            raise ValueError(
                "min_silence_duration_ms must be a non-negative integer")
    policy = dict(WHISPER_VAD_POLICY)
    if min_silence_duration_ms is not None:
        policy["min_silence_duration_ms"] = min_silence_duration_ms
    return policy


def whisper_vad_rejected(timing):
    """True only when Whisper explicitly reports zero audio after VAD."""
    if not isinstance(timing, dict) or timing.get("backend") != "whisper":
        return False
    speech_seconds = timing.get("speech_seconds")
    return (isinstance(speech_seconds, (int, float))
            and not isinstance(speech_seconds, bool)
            and speech_seconds == 0)


# Stable feature shapes avoid a roughly one-second CUDA/cuDNN setup cost for
# every previously unseen recording length. The attention mask ensures padded
# audio is ignored, so this does not change the decoded speech.
PARAKEET_BUCKET_SECONDS = (15, 30, 60)

# The Transformers Parakeet encoder uses full relative-position attention. Its
# memory use grows quadratically with recording length, so sending Presspeech's
# selectable five- or ten-minute captures through one tensor can exhaust a
# consumer GPU. Keep ordinary dictations on the exact existing single-pass
# path, but give longer recordings bounded overlapping windows. Each interior
# window owns at most 56 seconds and receives two seconds of acoustic context
# on either side, so the model never sees more than the already-warmed 60-second
# shape. TDT token timestamps decide which window owns overlap text; no words
# are guessed away with string de-duplication.
PARAKEET_SAMPLE_RATE = 16000
PARAKEET_MAX_WINDOW_SECONDS = 60
PARAKEET_OWNED_WINDOW_SECONDS = 56
PARAKEET_CONTEXT_SECONDS = 2


@dataclass(frozen=True)
class _ParakeetWindow:
    audio_start: int
    audio_end: int
    owned_start: int
    owned_end: int


def _parakeet_chunk_windows(
        sample_count, sample_rate=PARAKEET_SAMPLE_RATE,
        max_window_seconds=PARAKEET_MAX_WINDOW_SECONDS,
        owned_window_seconds=PARAKEET_OWNED_WINDOW_SECONDS,
        context_seconds=PARAKEET_CONTEXT_SECONDS):
    """Plan contiguous ownership with bounded overlapping model inputs."""
    if (isinstance(sample_count, bool) or not isinstance(sample_count, int)
            or sample_count < 0):
        raise ValueError("sample_count must be a non-negative integer")
    if (isinstance(sample_rate, bool) or not isinstance(sample_rate, int)
            or sample_rate <= 0):
        raise ValueError("sample_rate must be a positive integer")
    if not (0 <= context_seconds
            and owned_window_seconds > 0
            and owned_window_seconds + (2 * context_seconds)
            <= max_window_seconds):
        raise ValueError("invalid Parakeet window policy")

    maximum_samples = int(max_window_seconds * sample_rate)
    if sample_count <= maximum_samples:
        return [_ParakeetWindow(0, sample_count, 0, sample_count)]

    owned_samples = int(owned_window_seconds * sample_rate)
    context_samples = int(context_seconds * sample_rate)
    window_count = (sample_count + owned_samples - 1) // owned_samples
    windows = []
    # Balance ownership ranges so a clip just over 60 seconds does not produce
    # one nearly empty trailing inference. Integer division also makes every
    # input sample belong to exactly one range with no rounding gaps.
    for index in range(window_count):
        owned_start = index * sample_count // window_count
        owned_end = (index + 1) * sample_count // window_count
        audio_start = max(0, owned_start - context_samples)
        audio_end = min(sample_count, owned_end + context_samples)
        if audio_end - audio_start > maximum_samples:
            raise AssertionError("Parakeet chunk planner exceeded its model bound")
        windows.append(_ParakeetWindow(
            audio_start=audio_start,
            audio_end=audio_end,
            owned_start=owned_start,
            owned_end=owned_end,
        ))
    return windows


def _decoded_parakeet_text(decoded):
    if isinstance(decoded, (list, tuple)):
        decoded = "".join(decoded)
    return decoded.strip()


def _parakeet_timestamp_text_parts(decoded, records):
    """Map streamed timestamp tokens back onto the processor's decoded text.

    Transformers builds timestamp records with ``tokenizers.DecodeStream``.
    The locked tokenizer currently includes word-boundary spaces in its stream
    chunks, but Transformers' public Parakeet TDT example documents chunks
    without the spaces present in the complete decode.  Do not depend on that
    implementation detail: keep the complete decode authoritative for text
    while using records for timing, and assign intervening whitespace to the
    following token.  Any non-whitespace disagreement still fails closed
    rather than guessing at a long-dictation seam.
    """
    cursor = 0
    parts = []
    for index, record in enumerate(records):
        token = record["token"]
        if not token:
            raise RuntimeError("Parakeet returned malformed token timestamps")

        # A leading DecodeStream space can be removed by decoded.strip().  It
        # is presentation whitespace, not part of the first spoken token.
        match_token = token.lstrip() if index == 0 else token
        if not match_token:
            raise RuntimeError("Parakeet returned malformed token timestamps")
        token_start = decoded.find(match_token, cursor)
        gap = decoded[cursor:token_start] if token_start >= 0 else ""
        if token_start < 0 or (gap and not gap.isspace()):
            raise RuntimeError(
                "Parakeet token timestamps do not match the decoded text")
        token_end = token_start + len(match_token)
        parts.append(decoded[cursor:token_end])
        cursor = token_end

    trailing = decoded[cursor:]
    if trailing and not trailing.isspace():
        raise RuntimeError(
            "Parakeet token timestamps do not match the decoded text")
    if parts:
        parts[-1] += trailing
    return parts


def _owned_parakeet_text(decoded, timestamps, window,
                         sample_rate=PARAKEET_SAMPLE_RATE):
    """Return one window's timestamp-owned token text and boundary state."""
    decoded = _decoded_parakeet_text(decoded)
    if not isinstance(timestamps, (list, tuple)) or len(timestamps) != 1:
        raise RuntimeError(
            "Parakeet did not return token timestamps for bounded long-form transcription")
    records = timestamps[0]
    if not isinstance(records, (list, tuple)):
        raise RuntimeError("Parakeet returned malformed token timestamps")
    if not records:
        if decoded:
            raise RuntimeError(
                "Parakeet returned text without timestamps for bounded long-form transcription")
        return "", False

    owned_start = (window.owned_start - window.audio_start) / sample_rate
    owned_end = (window.owned_end - window.audio_start) / sample_rate
    selected_indexes = []
    previous_midpoint = None
    for index, record in enumerate(records):
        if not isinstance(record, dict) or not isinstance(record.get("token"), str):
            raise RuntimeError("Parakeet returned malformed token timestamps")
        start = record.get("start")
        end = record.get("end")
        if (isinstance(start, bool) or isinstance(end, bool)
                or not isinstance(start, (int, float))
                or not isinstance(end, (int, float))
                or not math.isfinite(start) or not math.isfinite(end)
                or start < 0 or end < start):
            raise RuntimeError("Parakeet returned malformed token timestamps")
        midpoint = (float(start) + float(end)) / 2
        # Text is decoded in token order. If timestamps run backwards, the
        # ownership filter can select non-contiguous tokens and silently drop
        # words between them. Reject that output rather than splice a damaged
        # transcript at the long-dictation seam. Equal times are valid for
        # zero-duration punctuation and tokens emitted on the same frame.
        # Independently rounded start/end values can differ by a few ULPs
        # even when tokens occupy the same frame.
        if (previous_midpoint is not None
                and midpoint < previous_midpoint - 1e-9):
            raise RuntimeError("Parakeet returned out-of-order token timestamps")
        previous_midpoint = midpoint
        # A token exactly on a seam belongs to the earlier range. This makes
        # adjacent ownership deterministic even for zero-duration punctuation.
        after_start = (midpoint >= owned_start if window.owned_start == 0
                       else midpoint > owned_start)
        if after_start and midpoint <= owned_end:
            selected_indexes.append(index)

    if (selected_indexes and
            selected_indexes[-1] - selected_indexes[0] + 1 != len(selected_indexes)):
        raise RuntimeError("Parakeet returned out-of-order token timestamps")

    text_parts = _parakeet_timestamp_text_parts(decoded, records)

    if not selected_indexes:
        return "", False
    first_index = selected_indexes[0]
    return "".join(text_parts[index] for index in selected_indexes), first_index > 0


def _join_owned_parakeet_text(parts):
    """Join timestamp-cropped windows without splitting subword continuations."""
    result = ""
    attached_punctuation = "',.;:!?%-/)]}\u00bb\u2019\u201d"
    # A window can end just after punctuation that binds the following token.
    # The next window may emit that token first, leaving no preceding context
    # token from which _owned_parakeet_text could infer a continuation. Keep
    # whitespace present in the retained text; only supply a separator when
    # neither fragment marks the boundary as attached.
    trailing_joiners = "-/'\u2019"
    for text, continues_previous_word in parts:
        if not text:
            continue
        if not result:
            result = text.lstrip()
            continue
        if (not continues_previous_word
                and not result[-1].isspace()
                and not text[0].isspace()
                and result[-1] not in trailing_joiners
                and text[0] not in attached_punctuation):
            result += " "
        result += text
    return result.strip()


def is_parakeet(model_name):
    return model_name == "parakeet-tdt-0.6b-v3"


def is_nemotron(model_name):
    return model_name == NEMOTRON_NAME


def is_moonshine(model_name):
    return model_name == MOONSHINE_NAME


def model_snapshot(model_name):
    """Return the pinned source requested by the loader (not a file attestation)."""
    if is_parakeet(model_name):
        repository, revision = PARAKEET_MODEL, PARAKEET_REVISION
    elif is_nemotron(model_name):
        repository, revision = NEMOTRON_MODEL, NEMOTRON_REVISION
    elif is_moonshine(model_name):
        repository, revision = MOONSHINE_MODEL, MOONSHINE_REVISION
    else:
        try:
            repository, revision = WHISPER_MODELS[model_name]
        except KeyError:
            raise ValueError("unsupported speech model: %s" % model_name) from None
    return {"repository": repository, "revision": revision}


def cuda_available():
    """Return whether the packaged Torch runtime can use NVIDIA CUDA."""
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


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
    # Long dictation must be split before feature extraction. Failing here
    # protects the memory bound if a future caller bypasses the planner.
    raise ValueError("Parakeet model input exceeds the 60-second bound")


def _parakeet_max_new_tokens(model, input_features, torch_module):
    """Size generation from Parakeet's encoder capacity, not a library default."""
    encoder_length = model.encoder._get_subsampling_output_length(
        torch_module.tensor(
            [input_features.shape[1]], device=input_features.device)
    ).item()
    return max(1, model.max_symbols_per_step * encoder_length)


class Transcriber:
    def __init__(self, precision="auto", measure_stages=False,
                 whisper_vad_min_silence_ms=None):
        self.lock = threading.Lock()
        self.inference_lock = threading.Lock()
        self.precision = precision
        # CUDA launches are asynchronous. Product diagnostics avoid inserting
        # barriers into the hot path, while the benchmark opts in so its
        # prepare/transfer/generate/decode values describe completed work
        # instead of submission time.
        self.measure_stages = measure_stages
        # This is only overridden by the local benchmark. App construction
        # keeps the reviewed product policy (160 ms) unchanged.
        self._whisper_vad_policy = whisper_vad_parameters(
            whisper_vad_min_silence_ms)
        self.model = None
        self.processor = None
        self.backend = None
        self.model_name = None
        self.last_timing = {}
        self._model_files = None

    def loaded(self, model_name):
        return self.model is not None and self.model_name == model_name

    def load(self, model_name, notify=None, *, local_only=False,
             progress_callback=None):
        # A Whisper transcription consumes a lazy segment generator while
        # holding inference_lock. Match unload()'s lock order so changing the
        # model cannot close its staged files or dispose native resources
        # before that decode finishes.
        with self.inference_lock:
            with self.lock:
                if self.loaded(model_name):
                    return
                self._unload_locked()
                if is_parakeet(model_name):
                    self._load_parakeet(
                        notify, local_only=local_only,
                        progress_callback=progress_callback)
                elif is_nemotron(model_name):
                    self._load_nemotron(notify, progress_callback)
                elif is_moonshine(model_name):
                    self._load_moonshine(notify, progress_callback)
                else:
                    self._load_whisper(
                        model_name, notify, progress_callback,
                        local_only=local_only)
                self.model_name = model_name
                if notify is not None:
                    notify("Presspeech", "Model %s ready." % model_name)

    def _load_parakeet(self, notify, *, local_only=False,
                       progress_callback=None):
        import torch
        from transformers import AutoModelForTDT, AutoProcessor
        model_network.harden_loaded_runtime()
        device = "cuda" if torch.cuda.is_available() else "cpu"
        if notify is not None:
            notify("Presspeech",
                   "Preparing Parakeet-TDT v3 on %s; a missing first-run model is about 2.5 GB." % device)
        model_path = _cached_model_path(
            "parakeet-tdt-0.6b-v3", local_only=local_only,
            progress_callback=progress_callback)
        self.processor = _configure_parakeet_processor(
            AutoProcessor.from_pretrained(
                model_path, local_files_only=True, revision=PARAKEET_REVISION,
                token=False, trust_remote_code=False))
        requested_dtype = _parakeet_dtype(torch, device, self.precision)
        try:
            self.model = AutoModelForTDT.from_pretrained(
                model_path, local_files_only=True, revision=PARAKEET_REVISION,
                dtype=requested_dtype, token=False, trust_remote_code=False,
                use_safetensors=True)
        except TypeError:
            self.model = AutoModelForTDT.from_pretrained(
                model_path, local_files_only=True, revision=PARAKEET_REVISION,
                token=False, trust_remote_code=False,
                use_safetensors=True)
        except RuntimeError:
            if requested_dtype == "auto":
                raise
            if notify is not None:
                # Backend error text can contain a local model path or other
                # private state. A retry notice needs no raw exception detail.
                notify("Presspeech", "Half-precision load failed; retrying FP32.")
            self.model = AutoModelForTDT.from_pretrained(
                model_path, local_files_only=True, revision=PARAKEET_REVISION, dtype="auto",
                token=False, trust_remote_code=False,
                use_safetensors=True)
        if device != "cpu":
            self.model.to(device)
        self.backend = "parakeet"
        self._device = device

    def _load_nemotron(self, notify, progress_callback=None):
        import torch
        from transformers import AutoModelForRNNT, AutoProcessor
        model_network.harden_loaded_runtime()
        device = "cuda" if torch.cuda.is_available() else "cpu"
        if notify is not None:
            notify("Presspeech", "Loading Nemotron English ASR on %s..." % device)
        dtype = torch.float16 if device == "cuda" else torch.float32
        model_path = _cached_model_path(
            "nemotron-speech-streaming-en-0.6b",
            progress_callback=progress_callback)
        self.processor = AutoProcessor.from_pretrained(
            model_path, local_files_only=True, revision=NEMOTRON_REVISION,
            token=False, trust_remote_code=False)
        self.model = AutoModelForRNNT.from_pretrained(
            model_path, local_files_only=True, revision=NEMOTRON_REVISION,
            dtype=dtype, token=False, trust_remote_code=False,
            use_safetensors=True).to(device)
        self.backend = "nemotron"
        self._device = device

    def _load_moonshine(self, notify, progress_callback=None):
        import torch
        from transformers import AutoProcessor, MoonshineStreamingForConditionalGeneration
        model_network.harden_loaded_runtime()
        device = "cuda" if torch.cuda.is_available() else "cpu"
        if notify is not None:
            notify("Presspeech", "Loading Moonshine Medium on %s..." % device)
        dtype = torch.float16 if device == "cuda" else torch.float32
        model_path = _cached_model_path(
            "moonshine-streaming-medium", progress_callback=progress_callback)
        self.processor = AutoProcessor.from_pretrained(
            model_path, local_files_only=True, revision=MOONSHINE_REVISION,
            token=False, trust_remote_code=False)
        self.model = MoonshineStreamingForConditionalGeneration.from_pretrained(
            model_path, local_files_only=True, revision=MOONSHINE_REVISION,
            dtype=dtype, token=False, trust_remote_code=False,
            use_safetensors=True).to(device)
        self.backend = "moonshine"
        self._device = device

    def _load_whisper(self, model_name, notify, progress_callback=None,
                      *, local_only=False):
        try:
            repository, revision = WHISPER_MODELS[model_name]
        except KeyError:
            raise ValueError("unsupported Whisper model: %s" % model_name) from None
        from faster_whisper import WhisperModel
        model_network.harden_loaded_runtime()
        device = "cuda" if cuda_available() else "cpu"
        compute = "float16" if device == "cuda" else "int8"
        if notify is not None:
            notify("Presspeech", "Loading Whisper %s on %s..." % (model_name, device))
        model_path = _cached_model_path(
            model_name, local_only=local_only,
            progress_callback=progress_callback)
        # faster-whisper may otherwise download an unpinned tokenizer when a
        # cache reset removes tokenizer.json between validation and construction.
        with ExitStack() as staging:
            private_path = staging.enter_context(model_cache.whisper_snapshot(
                model_path, MODEL_CACHE_FILES[model_name],
                optional_files=MODEL_CACHE_OPTIONAL_FILES.get(model_name, ())))
            self.model = WhisperModel(
                private_path, revision=revision, device=device, compute_type=compute,
                local_files_only=True, use_auth_token=False)
            self._model_files = staging.pop_all()
        self.backend = "whisper"
        self._device = device

    def _unload_locked(self):
        if self.model is not None:
            del self.model
            self.model = None
        self.processor = None
        self.backend = None
        self.model_name = None
        model_files = getattr(self, "_model_files", None)
        self._model_files = None
        if model_files is not None:
            model_files.close()

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

    def transcribe(self, audio, language=None, _filter_silence=True):
        """Transcribe one independent clip, detecting language when unspecified.

        faster-whisper resolves ``None`` to English without running language
        detection for an English-only model.  Its multilingual models instead
        detect the clip language before decoding.  Keep warm-up and controlled
        benchmarks free to pass an explicit language.
        """
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
                whisper_options = {}
                if _filter_silence:
                    # faster-whisper currently mutates some caller-provided
                    # VAD dictionaries while normalising options. Give each
                    # request an isolated copy of its product or test policy.
                    whisper_options["vad_parameters"] = dict(
                        self._whisper_vad_policy)
                segments, info = model.transcribe(
                    audio, language=language, beam_size=1,
                    vad_filter=_filter_silence,
                    without_timestamps=True, condition_on_previous_text=False,
                    **whisper_options,
                )
                speech_seconds = getattr(info, "duration_after_vad", None)
                detected_language = (
                    getattr(info, "language", None)
                    if speech_seconds is None or speech_seconds > 0 else None
                )
                self._backend_timing = {
                    "speech_seconds": speech_seconds,
                    # Kept out of product logs, but available to the local
                    # benchmark so an automatic-language run is auditable.
                    # A code inferred from VAD-rejected silence is meaningless.
                    "detected_language": detected_language,
                }
                # Whisper can decode plausible text from silence. faster-whisper's
                # standard API still returns a lazy segment generator when Silero
                # VAD found no speech, so do not consume that generator at all.
                text = ("" if (_filter_silence and speech_seconds is not None
                               and speech_seconds <= 0)
                        else "".join(seg.text for seg in segments).strip())
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
        silence = np.zeros(
            max(1, int(seconds * 16000)), dtype=np.float32)
        if backend == "whisper":
            # Warm Silero first: its ONNX session is created lazily on the first
            # filtered call, which should not be deferred to real dictation.
            self.transcribe(silence, language="en")
            # VAD correctly rejects this waveform before the lazy Whisper
            # generator runs, so use a second pass to warm CTranslate2 too.
            self.transcribe(
                silence, language="en", _filter_silence=False)
            return
        self.transcribe(silence, language="en")

    def _transcribe_parakeet(self, model, processor, audio):
        windows = _parakeet_chunk_windows(len(audio))
        results = []
        timings = []
        for window in windows:
            chunk = audio[window.audio_start:window.audio_end]
            decoded, timestamps, timing = self._transcribe_parakeet_chunk(
                model, processor, chunk)
            timings.append(timing)
            if len(windows) == 1:
                results.append((_decoded_parakeet_text(decoded), False))
            else:
                results.append(_owned_parakeet_text(
                    decoded, timestamps, window))

        self._backend_timing = {
            "bucket_seconds": max(timing["bucket_seconds"] for timing in timings),
            "chunk_count": len(windows),
            "max_chunk_seconds": max(
                window.audio_end - window.audio_start for window in windows
            ) / PARAKEET_SAMPLE_RATE,
            **{
                stage: sum(timing[stage] for timing in timings)
                for stage in ("prepare", "transfer", "generate", "decode")
            },
        }
        return _join_owned_parakeet_text(results)

    def _transcribe_parakeet_chunk(self, model, processor, audio):
        import torch
        bucket_seconds = _parakeet_bucket_seconds(
            len(audio) / float(PARAKEET_SAMPLE_RATE))
        self._parakeet_stage_barrier(torch, model.device)
        started = time.perf_counter()
        inputs = processor(
            audio,
            sampling_rate=PARAKEET_SAMPLE_RATE,
            return_tensors="pt",
            padding="max_length",
            max_length=bucket_seconds * PARAKEET_SAMPLE_RATE,
            truncation=True,
            return_attention_mask=True,
        )
        self._parakeet_stage_barrier(torch, model.device)
        processed = time.perf_counter()
        inputs = {
            k: (v.to(device=model.device, dtype=model.dtype)
                if v.is_floating_point() else v.to(device=model.device))
            for k, v in inputs.items()
        }
        self._parakeet_stage_barrier(torch, model.device)
        transferred = time.perf_counter()
        with torch.no_grad():
            max_new_tokens = _parakeet_max_new_tokens(
                model, inputs["input_features"], torch)
            output = model.generate(
                **inputs,
                return_dict_in_generate=True,
                max_new_tokens=max_new_tokens,
            )
        self._parakeet_stage_barrier(torch, model.device)
        generated = time.perf_counter()
        decoded, timestamps = processor.decode(
            output.sequences, durations=output.durations, skip_special_tokens=True)
        self._parakeet_stage_barrier(torch, model.device)
        decoded_at = time.perf_counter()
        timing = {
            "bucket_seconds": bucket_seconds,
            "prepare": processed - started,
            "transfer": transferred - processed,
            "generate": generated - transferred,
            "decode": decoded_at - generated,
        }
        return decoded, timestamps, timing

    def _parakeet_stage_barrier(self, torch_module, device):
        """Synchronize CUDA only for explicit benchmark stage measurement."""
        if self.measure_stages and str(device).startswith("cuda"):
            torch_module.cuda.synchronize(device)

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
