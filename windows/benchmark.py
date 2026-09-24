"""Local, repeatable accuracy and latency benchmarks for Presspeech."""

import argparse
import collections
import datetime as dt
import json
import math
import os
import platform
import re
import statistics
import time
import unicodedata

import numpy as np
import soundfile as sf

import app
from benchmark_provenance import asr_audio_sha256, benchmark_inputs_sha256
import config as cfg
import engine


def _canonical_text(text):
    # An accent can be stored as one code point or as a base plus combining
    # mark.  Score those canonically equivalent spellings alike without the
    # broader compatibility folding of NFKC (which can erase real differences).
    return unicodedata.normalize("NFC", text.replace("\u2019", "'"))


def _normalise_words(text):
    text = _canonical_text(text.lower())
    # Preserve the historical \w/apostrophe token boundary while keeping a
    # combining mark attached to its word when NFC has no composed character.
    # Python's re \w excludes marks, which could silently ignore a real accent
    # difference or split one spoken word into several scoring tokens.
    words = []
    current = []
    for character in text:
        if character.isalnum() or character in "_'":
            current.append(character)
        elif current and unicodedata.category(character).startswith("M"):
            current.append(character)
        elif current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    return words


def _normalise_chars(text):
    text = _canonical_text(text.lower())
    return " ".join(text.split())


def _normalise_case_chars(text):
    """Normalise spacing without erasing capitalization differences."""
    return " ".join(_canonical_text(text).split())


def edit_distance(reference, hypothesis):
    """Levenshtein distance for token or character sequences."""
    previous = list(range(len(hypothesis) + 1))
    for i, ref_item in enumerate(reference, 1):
        current = [i]
        for j, hyp_item in enumerate(hypothesis, 1):
            current.append(min(
                current[-1] + 1,
                previous[j] + 1,
                previous[j - 1] + (ref_item != hyp_item),
            ))
        previous = current
    return previous[-1]


def accuracy_metrics(reference, hypothesis):
    ref_words = _normalise_words(reference)
    hyp_words = _normalise_words(hypothesis)
    ref_chars = _normalise_chars(reference)
    hyp_chars = _normalise_chars(hypothesis)
    ref_case_chars = _normalise_case_chars(reference)
    hyp_case_chars = _normalise_case_chars(hypothesis)
    return {
        "word_errors": edit_distance(ref_words, hyp_words),
        "reference_words": len(ref_words),
        "wer": (edit_distance(ref_words, hyp_words) / len(ref_words)
                if ref_words else None),
        "character_errors": edit_distance(ref_chars, hyp_chars),
        "reference_characters": len(ref_chars),
        "cer": (edit_distance(ref_chars, hyp_chars) / len(ref_chars)
                if ref_chars else None),
        "case_sensitive_character_errors": edit_distance(
            ref_case_chars, hyp_case_chars),
        "case_sensitive_cer": (
            edit_distance(ref_case_chars, hyp_case_chars) / len(ref_case_chars)
            if ref_case_chars else None
        ),
        "exact_match": _normalise_chars(reference) == _normalise_chars(hypothesis),
    }


def trial_accuracy_metrics(reference, hypotheses):
    """Expose repeated-trial variation instead of hiding it in a consensus."""
    metrics = [accuracy_metrics(reference, hypothesis) for hypothesis in hypotheses]
    if not metrics or not metrics[0]["reference_words"]:
        return None
    word_errors = [item["word_errors"] for item in metrics]
    word_error_rates = [item["wer"] for item in metrics]
    case_sensitive_cers = [item["case_sensitive_cer"] for item in metrics]
    return {
        "trials": len(metrics),
        "exact_match_trials": sum(item["exact_match"] for item in metrics),
        "best_word_errors": min(word_errors),
        "median_word_errors": statistics.median(word_errors),
        "worst_word_errors": max(word_errors),
        "all_word_errors": word_errors,
        "best_wer": min(word_error_rates),
        "median_wer": statistics.median(word_error_rates),
        "worst_wer": max(word_error_rates),
        "all_wer": word_error_rates,
        "best_case_sensitive_cer": min(case_sensitive_cers),
        "median_case_sensitive_cer": statistics.median(case_sensitive_cers),
        "worst_case_sensitive_cer": max(case_sensitive_cers),
        "all_case_sensitive_cer": case_sensitive_cers,
    }


def paired_tail_silence_metrics(reference, baseline, tailed):
    """Score each clean/tailed Parakeet trial against the same reviewed words.

    Preserve pair order: a modal transcript can conceal an intermittent blank
    final decode, and comparing independent aggregate WERs loses that signal.
    """
    if not baseline or len(baseline) != len(tailed):
        raise ValueError("tail-silence probe needs matching non-empty trials")
    if not _normalise_words(reference):
        raise ValueError("tail-silence probe needs a scoreable reference")
    pairs = []
    for clean_text, tailed_text in zip(baseline, tailed):
        clean_errors = accuracy_metrics(reference, clean_text)["word_errors"]
        tailed_errors = accuracy_metrics(reference, tailed_text)["word_errors"]
        pairs.append({
            "baseline_transcript": clean_text,
            "tailed_transcript": tailed_text,
            "baseline_word_errors": clean_errors,
            "tailed_word_errors": tailed_errors,
            "nonempty_to_empty": bool(clean_text.strip()) and not tailed_text.strip(),
        })
    return {
        "trial_count": len(pairs),
        "baseline_empty_trial_count": sum(not text.strip() for text in baseline),
        "tailed_empty_trial_count": sum(not text.strip() for text in tailed),
        "nonempty_to_empty_trial_count": sum(
            pair["nonempty_to_empty"] for pair in pairs),
        "changed_text_trial_count": sum(
            _canonical_text(clean_text) != _canonical_text(tailed_text)
            for clean_text, tailed_text in zip(baseline, tailed)),
        "baseline_word_error_count": sum(
            pair["baseline_word_errors"] for pair in pairs),
        "tailed_word_error_count": sum(
            pair["tailed_word_errors"] for pair in pairs),
        "worsened_word_error_trial_count": sum(
            pair["tailed_word_errors"] > pair["baseline_word_errors"]
            for pair in pairs),
        "improved_word_error_trial_count": sum(
            pair["tailed_word_errors"] < pair["baseline_word_errors"]
            for pair in pairs),
        "tailed_first_word_failure_trial_count": first_word_metrics(
            reference, tailed)["failed_trials"],
        "tailed_final_word_failure_trial_count": final_word_metrics(
            reference, tailed)["failed_trials"],
        "pairs": pairs,
    }


def summarise_tail_silence_probe(samples):
    """Aggregate only explicitly probed, reviewed speech; never count silence."""
    probes = [sample["tail_silence_probe"] for sample in samples
              if sample.get("tail_silence_probe") is not None]
    return {
        "sample_count": len(probes),
        "order_breakdown": {
            order: {
                key: sum(
                    probe["order_breakdown"][order][key] for probe in probes)
                for key in ("trial_count", "nonempty_to_empty_trial_count",
                            "worsened_word_error_trial_count")
            }
            for order in ("baseline-first", "tailed-first")
        },
        "baseline_first_trial_count": sum(
            order == "baseline-first"
            for probe in probes for order in probe["trial_order"]),
        "tailed_first_trial_count": sum(
            order == "tailed-first"
            for probe in probes for order in probe["trial_order"]),
        **{
            key: sum(probe[key] for probe in probes)
            for key in (
                "trial_count", "baseline_empty_trial_count",
                "tailed_empty_trial_count", "nonempty_to_empty_trial_count",
                "changed_text_trial_count", "baseline_word_error_count",
                "tailed_word_error_count", "worsened_word_error_trial_count",
                "improved_word_error_trial_count",
                "tailed_first_word_failure_trial_count",
                "tailed_final_word_failure_trial_count",
            )
        },
    }


def tail_probe_order_breakdown(pairs, trial_order):
    """Stratify paired regressions by decode order, not pooled trial count."""
    if len(pairs) != len(trial_order) or any(
            order not in ("baseline-first", "tailed-first")
            for order in trial_order):
        raise ValueError("tail-silence probe needs one valid order per pair")
    return {
        order: {
            "trial_count": sum(value == order for value in trial_order),
            "nonempty_to_empty_trial_count": sum(
                value == order and pair["nonempty_to_empty"]
                for pair, value in zip(pairs, trial_order)),
            "worsened_word_error_trial_count": sum(
                value == order
                and pair["tailed_word_errors"] > pair["baseline_word_errors"]
                for pair, value in zip(pairs, trial_order)),
        }
        for order in ("baseline-first", "tailed-first")
    }


def final_word_metrics(reference, hypotheses):
    """Score final-word retention across every trial of a reviewed clip."""
    reference_words = _normalise_words(reference)
    if not reference_words:
        return None
    expected = reference_words[-1]
    retained_trials = sum(
        bool(words := _normalise_words(hypothesis)) and words[-1] == expected
        for hypothesis in hypotheses
    )
    trials = len(hypotheses)
    return {
        # One intermittent clipped ending matters even when the consensus
        # transcript is complete, so require every measured trial to retain it.
        "retained": retained_trials == trials,
        "retained_trials": retained_trials,
        "failed_trials": trials - retained_trials,
        "trials": trials,
    }


def first_word_metrics(reference, hypotheses):
    """Score first-word retention across every trial of a reviewed clip."""
    reference_words = _normalise_words(reference)
    if not reference_words:
        return None
    expected = reference_words[0]
    retained_trials = sum(
        bool(words := _normalise_words(hypothesis)) and words[0] == expected
        for hypothesis in hypotheses
    )
    trials = len(hypotheses)
    return {
        "retained": retained_trials == trials,
        "retained_trials": retained_trials,
        "failed_trials": trials - retained_trials,
        "trials": trials,
    }


def silence_metrics(expected_silence, reference_reviewed, hypotheses):
    """Score a human-reviewed non-speech fixture without inventing a WER."""
    if not expected_silence:
        return None
    if not reference_reviewed:
        return {
            "evaluated": False,
            "false_positive": None,
            "false_positive_trials": None,
            "trials": len(hypotheses),
        }
    false_positive_trials = sum(
        bool(_normalise_chars(hypothesis)) for hypothesis in hypotheses)
    return {
        "evaluated": True,
        # One intermittent hallucination matters even if the modal transcript
        # is empty, so score every trial rather than only the consensus.
        "false_positive": false_positive_trials > 0,
        "false_positive_trials": false_positive_trials,
        "trials": len(hypotheses),
    }


def speech_detection_metrics(audio_seconds, backend_timings, expected_trials=None):
    """Summarise the privacy-safe VAD duration reported by faster-whisper."""
    values = []
    for timing in backend_timings:
        value = timing.get("speech_seconds") if isinstance(timing, dict) else None
        if (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and value >= 0):
            values.append(float(value))
    if not values and expected_trials is None:
        return None
    trial_count = (len(backend_timings) if expected_trials is None
                   else expected_trials)
    measured_trials = len(values)
    median_seconds = statistics.median(values) if values else None
    return {
        "min_seconds": min(values) if values else None,
        "median_seconds": median_seconds,
        "max_seconds": max(values) if values else None,
        "median_audio_ratio": (median_seconds / audio_seconds
                               if values and audio_seconds > 0 else None),
        "rejected_trials": sum(value <= 0 for value in values),
        "trials": trial_count,
        "measured_trials": measured_trials,
        "missing_trials": max(0, trial_count - measured_trials),
        "all_seconds": values,
    }


def detected_language_metrics(backend_timings):
    """Count privacy-safe faster-whisper language results across trials."""
    values = []
    for timing in backend_timings:
        value = (timing.get("detected_language")
                 if isinstance(timing, dict) else None)
        if isinstance(value, str) and re.fullmatch(r"[a-z]{2,3}", value):
            values.append(value)
    return dict(collections.Counter(values)) if values else None


BACKEND_STAGE_NAMES = ("prepare", "transfer", "generate", "decode")


def backend_stage_metrics(backend_timings):
    """Summarise synchronized model stages without transcript content."""
    result = {}
    for name in BACKEND_STAGE_NAMES:
        values = []
        for timing in backend_timings:
            value = timing.get(name) if isinstance(timing, dict) else None
            if (isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(value) and value >= 0):
                values.append(float(value))
        if not values:
            continue
        result[name] = {
            "min": min(values),
            "median": statistics.median(values),
            "p95": _percentile(values, 0.95),
            "all": values,
        }
    return result or None


def parakeet_window_metrics(backend_timings):
    """Summarise bounded long-form execution without exposing audio or text."""
    plans = []
    for timing in backend_timings:
        if not isinstance(timing, dict):
            continue
        chunk_count = timing.get("chunk_count")
        max_chunk_seconds = timing.get("max_chunk_seconds")
        if (isinstance(chunk_count, bool) or not isinstance(chunk_count, int)
                or chunk_count < 1
                or isinstance(max_chunk_seconds, bool)
                or not isinstance(max_chunk_seconds, (int, float))
                or not math.isfinite(max_chunk_seconds)
                or max_chunk_seconds < 0):
            continue
        plans.append((chunk_count, float(max_chunk_seconds)))
    if not plans:
        return None
    return {
        "trials": len(plans),
        "windowed_trials": sum(chunk_count > 1 for chunk_count, _seconds in plans),
        "min_chunk_count": min(chunk_count for chunk_count, _seconds in plans),
        "max_chunk_count": max(chunk_count for chunk_count, _seconds in plans),
        "max_chunk_seconds": max(seconds for _chunk_count, seconds in plans),
        "all_chunk_counts": [chunk_count for chunk_count, _seconds in plans],
    }


def _reviewed_speech_vad_metrics(reviewed):
    """Keep Whisper VAD coverage and rejection visible in every stratum.

    A retained-audio ratio is a duration measure, not a speech-recall score:
    natural pauses also lower it. Only scored, human-reviewed speech belongs
    here; silence controls and unreviewed references have separate metrics.
    """
    detected = [sample for sample in reviewed
                if sample.get("silence") is None
                and isinstance(sample.get("speech_detection"), dict)]
    missing = sum(sample["speech_detection"]["missing_trials"]
                  for sample in detected)
    ratios = [seconds / sample["audio_seconds"]
              for sample in detected
              for seconds in sample["speech_detection"]["all_seconds"]]
    return {
        "reviewed_speech_vad_sample_count": len(detected),
        "reviewed_speech_vad_trial_count": sum(
            sample["speech_detection"]["trials"] for sample in detected),
        "reviewed_speech_vad_measured_trial_count": sum(
            sample["speech_detection"]["measured_trials"]
            for sample in detected),
        "reviewed_speech_vad_missing_trial_count": sum(
            sample["speech_detection"]["missing_trials"]
            for sample in detected),
        "reviewed_speech_vad_complete": (
            missing == 0 if detected else None),
        "reviewed_speech_vad_rejection_count": sum(
            sample["speech_detection"]["rejected_trials"] > 0
            for sample in detected),
        "reviewed_speech_vad_rejection_trial_count": sum(
            sample["speech_detection"]["rejected_trials"]
            for sample in detected),
        "reviewed_speech_vad_retained_audio_ratio": {
            "min": min(ratios) if ratios else None,
            "median": statistics.median(ratios) if ratios else None,
            "max": max(ratios) if ratios else None,
        },
    }


def _summarise_group(members):
    """Summarise accuracy, delivery boundaries, silence, and latency."""
    reviewed = [sample for sample in members
                if sample.get("accuracy") is not None]
    reference_words = sum(
        sample["accuracy"]["reference_words"] for sample in reviewed)
    word_errors = sum(
        sample["accuracy"]["word_errors"] for sample in reviewed)
    trial_reference_words = sum(
        sample["accuracy"]["reference_words"]
        * sample["trial_accuracy"]["trials"] for sample in reviewed)
    trial_word_errors = sum(
        sum(sample["trial_accuracy"]["all_word_errors"])
        for sample in reviewed)
    worst_trial_word_errors = sum(
        sample["trial_accuracy"]["worst_word_errors"]
        for sample in reviewed)
    latencies = [
        latency
        for sample in members
        for latency in sample.get("inference_seconds", {}).get("all", [])
        if isinstance(latency, (int, float))
        and not isinstance(latency, bool)
        and math.isfinite(latency)
        and latency >= 0
    ]
    silences = [sample["silence"] for sample in members
                if isinstance(sample.get("silence"), dict)
                and sample["silence"].get("evaluated")]
    first_words = [sample["first_word"] for sample in members
                   if isinstance(sample.get("first_word"), dict)]
    final_words = [sample["final_word"] for sample in members
                   if isinstance(sample.get("final_word"), dict)]
    return {
        "sample_count": len(members),
        "reviewed_sample_count": len(reviewed),
        **_reviewed_speech_vad_metrics(reviewed),
        "reviewed_reference_word_count": reference_words,
        "reviewed_word_error_count": word_errors,
        "aggregate_wer": (word_errors / reference_words
                           if reference_words else None),
        "reviewed_trial_reference_word_count": trial_reference_words,
        "reviewed_trial_word_error_count": trial_word_errors,
        "aggregate_trial_wer": (
            trial_word_errors / trial_reference_words
            if trial_reference_words else None
        ),
        "aggregate_worst_trial_wer": (
            worst_trial_word_errors / reference_words
            if reference_words else None
        ),
        "inference_seconds": {
            "measured_trials": len(latencies),
            "median": statistics.median(latencies) if latencies else None,
            "p95": _percentile(latencies, 0.95) if latencies else None,
        },
        "reviewed_silence_sample_count": len(silences),
        "reviewed_silence_trial_count": sum(
            sample["trials"] for sample in silences),
        "silence_false_positive_trial_count": sum(
            sample["false_positive_trials"] for sample in silences),
        "reviewed_first_word_sample_count": len(first_words),
        "reviewed_first_word_trial_count": sum(
            sample["trials"] for sample in first_words),
        "first_word_failure_count": sum(
            not sample["retained"] for sample in first_words),
        "first_word_failure_trial_count": sum(
            sample["failed_trials"] for sample in first_words),
        "reviewed_final_word_sample_count": len(final_words),
        "reviewed_final_word_trial_count": sum(
            sample["trials"] for sample in final_words),
        "final_word_failure_count": sum(
            not sample["retained"] for sample in final_words),
        "final_word_failure_trial_count": sum(
            sample["failed_trials"] for sample in final_words),
    }


def _group_metrics(samples, field):
    """Summarise one explicitly labelled benchmark dimension."""
    groups = collections.defaultdict(list)
    for sample in samples:
        group = sample.get(field)
        if isinstance(group, str) and group.strip():
            groups[group.strip()].append(sample)
    return {
        group: _summarise_group(members)
        for group, members in sorted(groups.items())
    }


def language_task_group_metrics(samples):
    """Summarise language/task intersections when both labels are present."""
    groups = collections.defaultdict(lambda: collections.defaultdict(list))
    for sample in samples:
        language = sample.get("language_group")
        task = sample.get("task_group")
        if (isinstance(language, str) and language.strip()
                and isinstance(task, str) and task.strip()):
            groups[language.strip()][task.strip()].append(sample)
    return {
        language: {
            task: _summarise_group(members)
            for task, members in sorted(task_groups.items())
        }
        for language, task_groups in sorted(groups.items())
    }


def task_group_metrics(samples):
    """Summarise task strata without transcript text."""
    return _group_metrics(samples, "task_group")


def language_group_metrics(samples):
    """Summarise human-labelled language strata independently of task."""
    return _group_metrics(samples, "language_group")


def load_audio(path):
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    if sample_rate <= 0 or len(audio) == 0:
        raise ValueError("benchmark audio must have a positive duration and sample rate")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = np.asarray(audio, dtype=np.float32)
    if not np.isfinite(audio).all():
        raise ValueError("benchmark audio contains non-finite samples")
    original_seconds = len(audio) / float(sample_rate)
    if sample_rate != 16000:
        audio = app._resample_to_16k(audio, sample_rate)
    if len(audio) == 0 or not np.isfinite(audio).all():
        raise ValueError("resampled benchmark audio is empty or non-finite")
    # Hash the exact mono, resampled signal used for inference, not the file
    # container. A changed recording cannot masquerade as a model regression.
    audio = np.ascontiguousarray(audio, dtype=np.float32)
    return audio, original_seconds, sample_rate, asr_audio_sha256(audio)


def _tail_probe_applies(sample, appended_silence_ms):
    """Only score a reviewed speech reference in the paired tail experiment."""
    return (appended_silence_ms is not None
            and sample.get("reference_reviewed", False)
            and not sample.get("expected_silence", False)
            and bool(_normalise_words(sample.get("reference", ""))))


def _validate_tail_probe_bucket(sample_count, appended_silence_ms):
    """Keep the paired inputs on one identical Parakeet feature shape.

    Comparing across a bucket or the 60-second windowing boundary would mix
    the effect of appended silence with a different inference path.
    """
    tail_count = appended_silence_ms * engine.PARAKEET_SAMPLE_RATE // 1000
    tailed_count = sample_count + tail_count
    maximum = engine.PARAKEET_MAX_WINDOW_SECONDS * engine.PARAKEET_SAMPLE_RATE
    if tailed_count > maximum:
        raise ValueError(
            "tail-silence probe needs both variants in one Parakeet window")
    clean_bucket = engine._parakeet_bucket_seconds(
        sample_count / engine.PARAKEET_SAMPLE_RATE)
    tailed_bucket = engine._parakeet_bucket_seconds(
        tailed_count / engine.PARAKEET_SAMPLE_RATE)
    if clean_bucket != tailed_bucket:
        raise ValueError(
            "tail-silence probe needs both variants in the same Parakeet "
            "feature bucket; shorten the clip or reduce appended silence")


def _preflight_audio(manifest_dir, samples, *, parakeet_tail_silence_ms=None):
    """Decode every fixture before costly model setup without retaining audio.

    The second read for inference must match the exact signal checked here;
    otherwise an edited fixture could make a partially paired report appear
    valid. Only digests and non-sensitive audio metadata remain in memory.
    """
    checked = []
    for sample in samples:
        path = sample["audio"]
        if not os.path.isabs(path):
            path = os.path.join(manifest_dir, path)
        audio, seconds, source_rate, digest = load_audio(path)
        if _tail_probe_applies(sample, parakeet_tail_silence_ms):
            _validate_tail_probe_bucket(len(audio), parakeet_tail_silence_ms)
        checked.append((path, seconds, source_rate, digest))
    return checked


def _sync_cuda():
    try:
        import torch
    except ModuleNotFoundError as exc:
        # CPU-only Whisper benchmarks need no PyTorch. A broken installed
        # runtime, or a failed CUDA barrier, must not produce latency figures.
        if exc.name != "torch":
            raise
        return
    if torch.cuda.is_available():
        torch.cuda.synchronize()


def _timed_transcription(transcriber, audio, language_hint):
    """Measure completed inference and snapshot the matching backend stages."""
    _sync_cuda()
    started = time.perf_counter()
    transcript = transcriber.transcribe(audio, language=language_hint)
    _sync_cuda()
    elapsed = time.perf_counter() - started
    backend_timing = getattr(transcriber, "last_timing", {})
    return (transcript, elapsed,
            dict(backend_timing) if isinstance(backend_timing, dict) else {})


def _percentile(values, percentile):
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _environment():
    result = {
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    try:
        import torch
        result.update({
            "torch": torch.__version__,
            "cuda_available": torch.cuda.is_available(),
            "cuda_device": (torch.cuda.get_device_name(0)
                            if torch.cuda.is_available() else None),
        })
    except Exception as exc:
        result["torch_error"] = str(exc)
    return result


def _apply_precision(transcriber, precision):
    if precision == "auto":
        return
    if transcriber.backend != "parakeet":
        raise ValueError("precision experiments currently support Parakeet only")
    import torch
    if precision == "tf32":
        torch.set_float32_matmul_precision("high")
        return
    if not torch.cuda.is_available():
        raise ValueError("half-precision experiments require CUDA")
    dtype = {"fp16": torch.float16, "bf16": torch.bfloat16}[precision]
    transcriber.model.to(dtype=dtype)


def _benchmark_language(manifest, override):
    """Return the report label and backend hint for one controlled run."""
    value = manifest.get("language", "en") if override is None else override
    if not isinstance(value, str) or not (
            value == "auto" or re.fullmatch(r"[a-z]{2,3}", value)):
        raise ValueError(
            "language must be 'auto' or a lowercase two/three-letter code")
    return value, None if value == "auto" else value


def run_benchmark(manifest_path, model_name=None, runs=None, precision="auto",
                  language=None, whisper_vad_min_silence_ms=None,
                  parakeet_tail_silence_ms=None):
    manifest_path = os.path.abspath(manifest_path)
    manifest_dir = os.path.dirname(manifest_path)
    with open(manifest_path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    model_name = model_name or manifest.get("model") or cfg.DEFAULTS["model"]
    runs = manifest.get("runs", 3) if runs is None else runs
    if isinstance(runs, bool) or not isinstance(runs, int) or runs < 1:
        raise ValueError("runs must be a positive integer")
    requested_language, language_hint = _benchmark_language(manifest, language)
    samples = manifest.get("samples", [])
    if not isinstance(samples, list):
        raise ValueError("samples must be a list")
    sample_ids = set()
    for sample in samples:
        if not isinstance(sample, dict):
            raise ValueError("each sample must be an object")
        for field in ("task_group", "language_group"):
            label = sample.get(field)
            if label is not None and (
                    not isinstance(label, str) or not label.strip()):
                raise ValueError("sample %s must be a non-empty string" % field)
        # Do not coerce JSON strings such as "false" to True: that would count
        # an unreviewed reference as reviewed or score speech as silence.
        for field in ("reference_reviewed", "expected_silence"):
            if field in sample and not isinstance(sample[field], bool):
                raise ValueError("sample %s must be a boolean" % field)
        reference = sample.get("reference", "")
        if not isinstance(reference, str):
            raise ValueError("sample reference must be a string")
        if sample.get("expected_silence", False):
            if reference.strip():
                raise ValueError("silence sample must not have reference text")
        elif sample.get("reference_reviewed", False) and not reference.strip():
            raise ValueError("reviewed speech sample needs reference text")
        sample_id = sample.get("id")
        if not isinstance(sample_id, str) or not sample_id.strip():
            raise ValueError("sample id must be a non-empty string")
        if sample_id in sample_ids:
            raise ValueError("sample ids must be unique")
        sample_ids.add(sample_id)
        audio_path = sample.get("audio")
        if not isinstance(audio_path, str) or not audio_path.strip():
            raise ValueError("sample audio must be a non-empty path")
    # Validate and freeze report provenance before loading any model. This
    # describes the requested pinned source, not a fresh integrity attestation.
    snapshot = engine.model_snapshot(model_name)
    if (whisper_vad_min_silence_ms is not None
            and model_name not in engine.WHISPER_MODELS):
        raise ValueError(
            "Whisper VAD experiments require a faster-whisper model")
    if parakeet_tail_silence_ms is not None:
        maximum_ms = int(app.POST_ROLL_MAX_SEC * 1000)
        if (isinstance(parakeet_tail_silence_ms, bool)
                or not isinstance(parakeet_tail_silence_ms, int)
                or not 1 <= parakeet_tail_silence_ms <= maximum_ms):
            raise ValueError(
                "Parakeet tail-silence probe needs an integer from 1 to %d ms"
                % maximum_ms)
        if not engine.is_parakeet(model_name):
            raise ValueError("tail-silence probe requires a Parakeet model")
        if not any(sample.get("reference_reviewed", False)
                   and not sample.get("expected_silence", False)
                   and _normalise_words(sample.get("reference", ""))
                   for sample in samples):
            raise ValueError(
                "tail-silence probe needs reviewed speech with scoreable words")
    whisper_vad_policy = (
        engine.whisper_vad_parameters(whisper_vad_min_silence_ms)
        if model_name in engine.WHISPER_MODELS else None
    )
    if not samples:
        raise ValueError("samples must contain at least one audio fixture")
    if precision not in ("auto", "tf32", "fp16", "bf16"):
        raise ValueError("unsupported benchmark precision")
    if precision != "auto" and not engine.is_parakeet(model_name):
        raise ValueError("precision experiments currently support Parakeet only")
    checked_audio = _preflight_audio(
        manifest_dir, samples,
        parakeet_tail_silence_ms=parakeet_tail_silence_ms)

    # Stage barriers are benchmark-only: they make CUDA timings factual while
    # keeping synchronization overhead out of interactive dictation.
    transcriber_options = {"measure_stages": True}
    if whisper_vad_min_silence_ms is not None:
        transcriber_options["whisper_vad_min_silence_ms"] = (
            whisper_vad_min_silence_ms)
    transcriber = engine.Transcriber(**transcriber_options)
    _sync_cuda()
    started = time.perf_counter()
    transcriber.load(model_name)
    _apply_precision(transcriber, precision)
    _sync_cuda()
    load_seconds = time.perf_counter() - started

    # Warm generation kernels separately from model loading and measured samples.
    _sync_cuda()
    started = time.perf_counter()
    transcriber.warmup(seconds=1.0, all_buckets=True)
    _sync_cuda()
    warmup_seconds = time.perf_counter() - started

    sample_results = []
    input_rows = []
    tail_pair_index = 0
    for sample, (audio_path, checked_seconds, checked_rate, checked_digest) in zip(
            samples, checked_audio):
        task_group = sample.get("task_group")
        language_group = sample.get("language_group")
        audio, audio_seconds, source_rate, audio_digest = load_audio(audio_path)
        if (audio_seconds, source_rate, audio_digest) != (
                checked_seconds, checked_rate, checked_digest):
            raise RuntimeError("benchmark audio changed after preflight")
        input_rows.append({
            "asr_audio_sha256": audio_digest,
            "audio_seconds": audio_seconds,
            "source_sample_rate": source_rate,
            "reference": sample.get("reference", ""),
            "reference_reviewed": sample.get("reference_reviewed", False),
            "expected_silence": sample.get("expected_silence", False),
            "task_group": task_group.strip() if task_group is not None else None,
            "language_group": (
                language_group.strip() if language_group is not None else None),
        })
        timings = []
        transcripts = []
        backend_timings = []
        reference = sample.get("reference", "")
        probe_this_sample = _tail_probe_applies(
            sample, parakeet_tail_silence_ms)
        tail_audio = (np.concatenate((audio, np.zeros(
            parakeet_tail_silence_ms * engine.PARAKEET_SAMPLE_RATE // 1000,
            dtype=np.float32)))
            if probe_this_sample else None)
        tail_timings = []
        tail_transcripts = []
        trial_order = []
        for _run in range(runs):
            # Alternate across *all* reviewed probe pairs, not just within a
            # clip. This keeps corpus order counts within one even when runs
            # is odd. Otherwise the tailed condition is always measured second.
            tailed_first = probe_this_sample and tail_pair_index % 2 == 1
            if probe_this_sample:
                trial_order.append(
                    "tailed-first" if tailed_first else "baseline-first")
                tail_pair_index += 1
            if tailed_first:
                tail_text, tail_seconds, _ = _timed_transcription(
                    transcriber, tail_audio, language_hint)
                tail_transcripts.append(tail_text)
                tail_timings.append(tail_seconds)
            transcript, seconds, backend_timing = _timed_transcription(
                transcriber, audio, language_hint)
            timings.append(seconds)
            transcripts.append(transcript)
            backend_timings.append(backend_timing)
            if probe_this_sample and not tailed_first:
                tail_text, tail_seconds, _ = _timed_transcription(
                    transcriber, tail_audio, language_hint)
                tail_transcripts.append(tail_text)
                tail_timings.append(tail_seconds)
        consensus = collections.Counter(transcripts).most_common(1)[0][0]
        median_seconds = statistics.median(timings)
        result = {
            "id": sample["id"],
            "audio": os.path.relpath(audio_path, manifest_dir),
            "audio_seconds": audio_seconds,
            "source_sample_rate": source_rate,
            "runs": runs,
            "transcript": consensus,
            "transcript_variants": dict(collections.Counter(transcripts)),
            "inference_seconds": {
                "min": min(timings),
                "median": median_seconds,
                "p95": _percentile(timings, 0.95),
                "all": timings,
            },
            "realtime_factor": median_seconds / audio_seconds,
            "realtime_speedup": audio_seconds / median_seconds,
            "estimated_release_to_paste_seconds": (
                app.POST_ROLL_SEC + median_seconds + app.PASTE_DELAY_SEC
            ),
            "estimated_adaptive_release_to_paste_seconds": (
                app.POST_ROLL_MIN_SEC + median_seconds + app.PASTE_DELAY_SEC
            ),
            "reference_reviewed": sample.get("reference_reviewed", False),
            "speech_detection": speech_detection_metrics(
                audio_seconds, backend_timings,
                expected_trials=(runs if model_name in engine.WHISPER_MODELS
                                 else None)),
            "detected_languages": detected_language_metrics(backend_timings),
            "backend_stages": backend_stage_metrics(backend_timings),
            "parakeet_windowing": parakeet_window_metrics(backend_timings),
        }
        if probe_this_sample:
            paired_metrics = paired_tail_silence_metrics(
                reference, transcripts, tail_transcripts)
            result["tail_silence_probe"] = {
                "appended_silence_ms": parakeet_tail_silence_ms,
                # Indexed like pairs and both timing arrays below.
                "trial_order": trial_order,
                **paired_metrics,
                "order_breakdown": tail_probe_order_breakdown(
                    paired_metrics["pairs"], trial_order),
                "tailed_inference_seconds": {
                    "min": min(tail_timings),
                    "median": statistics.median(tail_timings),
                    "p95": _percentile(tail_timings, 0.95),
                    "all": tail_timings,
                },
            }
        if task_group is not None:
            result["task_group"] = task_group.strip()
        if language_group is not None:
            result["language_group"] = language_group.strip()
        result["silence"] = silence_metrics(
            sample.get("expected_silence", False),
            result["reference_reviewed"],
            transcripts,
        )
        if reference and result["reference_reviewed"]:
            trial_accuracy = trial_accuracy_metrics(reference, transcripts)
            if trial_accuracy is not None:
                result["reference"] = reference
                result["accuracy"] = accuracy_metrics(reference, consensus)
                result["trial_accuracy"] = trial_accuracy
                result["first_word"] = first_word_metrics(reference, transcripts)
                result["final_word"] = final_word_metrics(reference, transcripts)
            else:
                result["accuracy"] = None
                result["trial_accuracy"] = None
                result["first_word"] = None
                result["final_word"] = None
                result["accuracy_note"] = (
                    "Reviewed reference contains no scoreable words."
                )
        else:
            result["accuracy"] = None
            result["trial_accuracy"] = None
            result["first_word"] = None
            result["final_word"] = None
            result["accuracy_note"] = "Reference transcript requires human review."
        sample_results.append(result)

    reviewed = [item for item in sample_results if item["accuracy"] is not None]
    total_words = sum(item["accuracy"]["reference_words"] for item in reviewed)
    total_word_errors = sum(item["accuracy"]["word_errors"] for item in reviewed)
    reviewed_silence = [
        item for item in sample_results
        if item["silence"] is not None and item["silence"]["evaluated"]
    ]
    reviewed_final_words = [
        item for item in reviewed if item["final_word"] is not None
    ]
    reviewed_first_words = [
        item for item in reviewed if item["first_word"] is not None
    ]
    total_trial_words = sum(
        item["accuracy"]["reference_words"] * item["trial_accuracy"]["trials"]
        for item in reviewed
    )
    total_trial_errors = sum(
        sum(item["trial_accuracy"]["all_word_errors"])
        for item in reviewed
    )
    total_best_trial_errors = sum(
        item["trial_accuracy"]["best_word_errors"] for item in reviewed
    )
    total_worst_trial_errors = sum(
        item["trial_accuracy"]["worst_word_errors"] for item in reviewed
    )
    model_dtype = str(getattr(transcriber.model, "dtype", "unknown"))
    cuda_allocated_mib = None
    try:
        import torch
        if torch.cuda.is_available():
            cuda_allocated_mib = torch.cuda.memory_allocated() / 1024 / 1024
    except Exception:
        pass
    return {
        "benchmark_version": 13,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "benchmark_inputs_sha256": benchmark_inputs_sha256(input_rows),
        "model": model_name,
        "model_snapshot": snapshot,
        # "auto" means faster-whisper received no language hint. The detected
        # codes, when available, are retained per sample above.
        "requested_language": requested_language,
        # Keep reports interpretable across faster-whisper updates. The
        # boundary policy can affect both WER and silence false positives.
        "whisper_vad_policy": whisper_vad_policy,
        "whisper_vad_policy_origin": (
            "benchmark-only override"
            if whisper_vad_min_silence_ms is not None
            else "Presspeech product default"
        ) if whisper_vad_policy is not None else None,
        "parakeet_tail_silence_ms": parakeet_tail_silence_ms,
        "tail_silence_probe": (
            summarise_tail_silence_probe(sample_results)
            if parakeet_tail_silence_ms is not None else None),
        "precision": precision,
        "model_dtype": model_dtype,
        "cuda_allocated_mib": cuda_allocated_mib,
        "environment": _environment(),
        "load_seconds": load_seconds,
        "warmup_seconds": warmup_seconds,
        "sample_count": len(sample_results),
        "reviewed_sample_count": len(reviewed),
        # The best/worst envelope combines each clip's extremum, potentially
        # from different repetitions; it is not an observed whole-corpus trial
        # or a confidence interval. Preserve consensus WER for existing readers.
        "reviewed_reference_word_count": total_words,
        "reviewed_trial_reference_word_count": total_trial_words,
        "reviewed_trial_word_error_count": total_trial_errors,
        "aggregate_wer": total_word_errors / total_words if total_words else None,
        "aggregate_trial_wer": (
            total_trial_errors / total_trial_words if total_trial_words else None
        ),
        "aggregate_best_trial_wer": (
            total_best_trial_errors / total_words if total_words else None
        ),
        "aggregate_worst_trial_wer": (
            total_worst_trial_errors / total_words if total_words else None
        ),
        "reviewed_silence_sample_count": len(reviewed_silence),
        "silence_false_positive_count": sum(
            item["silence"]["false_positive"] for item in reviewed_silence),
        "reviewed_silence_trial_count": sum(
            item["silence"]["trials"] for item in reviewed_silence),
        "silence_false_positive_trial_count": sum(
            item["silence"]["false_positive_trials"]
            for item in reviewed_silence),
        **_reviewed_speech_vad_metrics(reviewed),
        "reviewed_final_word_sample_count": len(reviewed_final_words),
        "final_word_failure_count": sum(
            not item["final_word"]["retained"]
            for item in reviewed_final_words),
        "reviewed_final_word_trial_count": sum(
            item["final_word"]["trials"] for item in reviewed_final_words),
        "final_word_failure_trial_count": sum(
            item["final_word"]["failed_trials"]
            for item in reviewed_final_words),
        "reviewed_first_word_sample_count": len(reviewed_first_words),
        "first_word_failure_count": sum(
            not item["first_word"]["retained"]
            for item in reviewed_first_words),
        "reviewed_first_word_trial_count": sum(
            item["first_word"]["trials"] for item in reviewed_first_words),
        "first_word_failure_trial_count": sum(
            item["first_word"]["failed_trials"]
            for item in reviewed_first_words),
        "task_groups": task_group_metrics(sample_results),
        "language_groups": language_group_metrics(sample_results),
        "language_task_groups": language_task_group_metrics(sample_results),
        "samples": sample_results,
    }


def _print_summary(result):
    print("Model: %s | precision: %s (%s)" %
          (result["model"], result["precision"], result["model_dtype"]))
    print("Language: %s" % (
        "automatic detection" if result.get("requested_language") == "auto"
        else result.get("requested_language", "en")))
    snapshot = result["model_snapshot"]
    print("Snapshot: %s@%s" %
          (snapshot["repository"], snapshot["revision"]))
    print("Benchmark inputs SHA-256: %s" % result["benchmark_inputs_sha256"])
    vad_policy = result.get("whisper_vad_policy")
    if vad_policy is not None:
        origin = result.get("whisper_vad_policy_origin")
        origin = " (%s)" % origin if origin else ""
        print(
            "Whisper VAD%s: threshold %.2f / negative %.2f / speech >= %d ms / "
            "silence split %d ms / edge padding %d ms" % (
                origin,
                vad_policy["threshold"],
                vad_policy["neg_threshold"],
                vad_policy["min_speech_duration_ms"],
                vad_policy["min_silence_duration_ms"],
                vad_policy["speech_pad_ms"],
            )
        )
    if result["cuda_allocated_mib"] is not None:
        print("CUDA tensors: %.1f MiB" % result["cuda_allocated_mib"])
    print("Load: %.3fs | warm-up: %.3fs" %
          (result["load_seconds"], result["warmup_seconds"]))
    tail_probe = result.get("tail_silence_probe")
    if tail_probe is not None:
        print("Parakeet +%d ms silence (benchmark-only): nonempty-to-empty "
              "%d/%d paired trials; word errors %d -> %d; worsened %d, "
              "improved %d trials across %d reviewed clips; order "
              "baseline-first %d, tailed-first %d" % (
                  result["parakeet_tail_silence_ms"],
                  tail_probe["nonempty_to_empty_trial_count"],
                  tail_probe["trial_count"],
                  tail_probe["baseline_word_error_count"],
                  tail_probe["tailed_word_error_count"],
                  tail_probe["worsened_word_error_trial_count"],
                  tail_probe["improved_word_error_trial_count"],
                  tail_probe["sample_count"],
                  tail_probe["baseline_first_trial_count"],
                  tail_probe["tailed_first_trial_count"],
              ))
        for order, counts in tail_probe["order_breakdown"].items():
            print("  %s: nonempty-to-empty %d/%d; worsened word errors "
                  "%d/%d trials" % (
                      order,
                      counts["nonempty_to_empty_trial_count"],
                      counts["trial_count"],
                      counts["worsened_word_error_trial_count"],
                      counts["trial_count"],
                  ))
    if result["aggregate_wer"] is not None:
        print("Reviewed corpus WER: %.2f%% consensus | %.2f%% all trials | "
              "%.2f/%.2f%% best/worst trial envelope" % (
                  result["aggregate_wer"] * 100,
                  result["aggregate_trial_wer"] * 100,
                  result["aggregate_best_trial_wer"] * 100,
                  result["aggregate_worst_trial_wer"] * 100,
              ))
        print("Reviewed edge words: first not retained %d/%d trials; "
              "final not retained %d/%d trials" % (
                  result["first_word_failure_trial_count"],
                  result["reviewed_first_word_trial_count"],
                  result["final_word_failure_trial_count"],
                  result["reviewed_final_word_trial_count"],
              ))
    def percentage(value):
        return "n/a" if value is None else "%.2f%%" % (value * 100)

    def seconds(value):
        return "n/a" if value is None else "%.3fs" % value

    def print_group_vad(metrics):
        if not metrics["reviewed_speech_vad_sample_count"]:
            return
        print("  Reviewed speech VAD: observed rejections %d/%d measured "
              "trials in %d/%d clips; retained-audio median %s; duration "
              "coverage %d/%d trials (%d missing; %s)" % (
                  metrics["reviewed_speech_vad_rejection_trial_count"],
                  metrics["reviewed_speech_vad_measured_trial_count"],
                  metrics["reviewed_speech_vad_rejection_count"],
                  metrics["reviewed_speech_vad_sample_count"],
                  percentage(metrics[
                      "reviewed_speech_vad_retained_audio_ratio"]["median"]),
                  metrics["reviewed_speech_vad_measured_trial_count"],
                  metrics["reviewed_speech_vad_trial_count"],
                  metrics["reviewed_speech_vad_missing_trial_count"],
                  "complete" if metrics["reviewed_speech_vad_complete"]
                  else "incomplete",
              ))

    print_group_vad(result)

    for dimension, groups in (
            ("Task group", result.get("task_groups", {})),
            ("Language group", result.get("language_groups", {}))):
        for group, metrics in groups.items():
            latency = metrics["inference_seconds"]
            print("%s %s: %d samples, %d reviewed; WER %s consensus / "
                  "%s all trials / %s worst-trial envelope; inference "
                  "%s median / %s p95 (%d trials); "
                  "edge-word failures first %d/%d, final %d/%d trials; "
                  "reviewed silence false positives %d/%d trials" % (
                      dimension, group, metrics["sample_count"],
                      metrics["reviewed_sample_count"],
                      percentage(metrics["aggregate_wer"]),
                      percentage(metrics["aggregate_trial_wer"]),
                      percentage(metrics["aggregate_worst_trial_wer"]),
                      seconds(latency["median"]), seconds(latency["p95"]),
                      latency["measured_trials"],
                      metrics["first_word_failure_trial_count"],
                      metrics["reviewed_first_word_trial_count"],
                      metrics["final_word_failure_trial_count"],
                      metrics["reviewed_final_word_trial_count"],
                      metrics["silence_false_positive_trial_count"],
                      metrics["reviewed_silence_trial_count"]))
            print_group_vad(metrics)

    for language, tasks in result.get("language_task_groups", {}).items():
        for task, metrics in tasks.items():
            latency = metrics["inference_seconds"]
            print("Language/task %s / %s: %d samples, %d reviewed; WER %s "
                  "consensus / %s all trials / %s worst-trial envelope; "
                  "inference %s median / %s p95 (%d trials); "
                  "edge-word failures first %d/%d, final %d/%d trials; "
                  "reviewed silence false positives %d/%d trials" % (
                      language, task, metrics["sample_count"],
                      metrics["reviewed_sample_count"],
                      percentage(metrics["aggregate_wer"]),
                      percentage(metrics["aggregate_trial_wer"]),
                      percentage(metrics["aggregate_worst_trial_wer"]),
                      seconds(latency["median"]), seconds(latency["p95"]),
                      latency["measured_trials"],
                      metrics["first_word_failure_trial_count"],
                      metrics["reviewed_first_word_trial_count"],
                      metrics["final_word_failure_trial_count"],
                      metrics["reviewed_final_word_trial_count"],
                      metrics["silence_false_positive_trial_count"],
                      metrics["reviewed_silence_trial_count"]))
            print_group_vad(metrics)
    for sample in result["samples"]:
        timing = sample["inference_seconds"]
        print("\n%s: %.3fs median (%.1fx realtime; inference + min/max post-roll "
              "+ paste delay %.3f/%.3fs, not measured delivery)" % (
            sample["id"], timing["median"], sample["realtime_speedup"],
            sample["estimated_adaptive_release_to_paste_seconds"],
            sample["estimated_release_to_paste_seconds"],
        ))
        print("  %s" % sample["transcript"])
        sample_tail_probe = sample.get("tail_silence_probe")
        if sample_tail_probe is not None:
            print("  +%d ms silence: nonempty-to-empty %d/%d paired trials; "
                  "word errors %d -> %d; tailed inference %.3fs median" % (
                      sample_tail_probe["appended_silence_ms"],
                      sample_tail_probe["nonempty_to_empty_trial_count"],
                      sample_tail_probe["trial_count"],
                      sample_tail_probe["baseline_word_error_count"],
                      sample_tail_probe["tailed_word_error_count"],
                      sample_tail_probe["tailed_inference_seconds"]["median"],
                  ))
        stages = sample.get("backend_stages")
        if stages is not None:
            print("  Model stages (median): %s" % " | ".join(
                "%s %.3fs" % (name, stages[name]["median"])
                for name in BACKEND_STAGE_NAMES if name in stages
            ))
        windowing = sample.get("parakeet_windowing")
        if windowing is not None:
            print("  Parakeet windows: %d-%d per trial; longest input %.3fs" % (
                windowing["min_chunk_count"],
                windowing["max_chunk_count"],
                windowing["max_chunk_seconds"],
            ))
        detection = sample["speech_detection"]
        if detection is not None:
            if detection["median_seconds"] is None:
                print("  VAD speech: not measured (%d/%d trials; %d missing)" % (
                    detection["measured_trials"], detection["trials"],
                    detection["missing_trials"],
                ))
            else:
                print("  VAD speech: %.3fs median of %.3fs; rejected %d/%d measured "
                      "trials; %d missing" % (
                          detection["median_seconds"], sample["audio_seconds"],
                          detection["rejected_trials"],
                          detection["measured_trials"],
                          detection["missing_trials"],
                      ))
        detected_languages = sample.get("detected_languages")
        if detected_languages is not None:
            print("  Detected language trials: %s" % " | ".join(
                "%s %d" % item for item in sorted(detected_languages.items())
            ))
        if sample["silence"] is not None:
            if sample["silence"]["evaluated"]:
                if sample["silence"]["false_positive"]:
                    status = "FALSE POSITIVE (%d/%d trials)" % (
                        sample["silence"]["false_positive_trials"],
                        sample["silence"]["trials"],
                    )
                else:
                    status = "empty as expected"
                print("  Reviewed silence: %s" % status)
            else:
                print("  Silence check: pending human review")
        elif sample["accuracy"] is None:
            print("  Accuracy: %s" % sample["accuracy_note"])
        else:
            trial_accuracy = sample["trial_accuracy"]
            print("  WER: %.2f%% consensus | %.2f/%.2f/%.2f%% "
                  "best/median/worst across %d trials | CER: %.2f%%" % (
                sample["accuracy"]["wer"] * 100,
                trial_accuracy["best_wer"] * 100,
                trial_accuracy["median_wer"] * 100,
                trial_accuracy["worst_wer"] * 100,
                trial_accuracy["trials"],
                sample["accuracy"]["cer"] * 100,
            ))
            first_word = sample["first_word"]
            print("  First word: %s (%d/%d trials retained)" % (
                "retained" if first_word["retained"] else "FAILED",
                first_word["retained_trials"], first_word["trials"],
            ))
            final_word = sample["final_word"]
            print("  Final word: %s (%d/%d trials retained)" % (
                "retained" if final_word["retained"] else "FAILED",
                final_word["retained_trials"], final_word["trials"],
            ))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=os.path.join("benchmarks", "manifest.json"))
    parser.add_argument("--model")
    parser.add_argument("--runs", type=int)
    parser.add_argument(
        "--precision", choices=("auto", "tf32", "fp16", "bf16"), default="auto")
    parser.add_argument(
        "--language",
        help="lowercase language code, or 'auto' for multilingual Whisper detection")
    parser.add_argument(
        "--whisper-vad-min-silence-ms", type=int,
        help=("benchmark-only Whisper VAD pause threshold override in "
              "milliseconds; does not change the app policy"))
    parser.add_argument(
        "--parakeet-tail-silence-ms", type=int,
        help=("benchmark-only paired speech probe: append up to the app's "
              "maximum post-roll duration of zero-valued samples; does not "
              "change capture or recognition"))
    parser.add_argument("--output", help="JSON output path")
    args = parser.parse_args()
    result = run_benchmark(
        args.manifest, model_name=args.model, runs=args.runs,
        precision=args.precision, language=args.language,
        whisper_vad_min_silence_ms=args.whisper_vad_min_silence_ms,
        parakeet_tail_silence_ms=args.parakeet_tail_silence_ms)
    _print_summary(result)
    if args.output:
        output_path = os.path.abspath(args.output)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, ensure_ascii=False)
        print("\nSaved %s" % output_path)


if __name__ == "__main__":
    main()
