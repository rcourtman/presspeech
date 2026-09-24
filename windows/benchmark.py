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
from benchmark_alignment import word_error_alignment
from benchmark_provenance import (
    asr_audio_sha256, benchmark_inputs_sha256, benchmark_order_sha256,
    recorded_tail_probe_inputs_sha256,
)
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
    word_errors, max_deletion_run = word_error_alignment(ref_words, hyp_words)
    ref_chars = _normalise_chars(reference)
    hyp_chars = _normalise_chars(hypothesis)
    ref_case_chars = _normalise_case_chars(reference)
    hyp_case_chars = _normalise_case_chars(hypothesis)
    return {
        "word_errors": word_errors,
        "reference_words": len(ref_words),
        "wer": (word_errors / len(ref_words)
                if ref_words else None),
        "max_reference_deletion_run": max_deletion_run,
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
    deletion_runs = [item["max_reference_deletion_run"] for item in metrics]
    case_sensitive_cers = [item["case_sensitive_cer"] for item in metrics]
    return {
        "trials": len(metrics),
        "exact_match_trials": sum(item["exact_match"] for item in metrics),
        "best_word_errors": min(word_errors),
        "median_word_errors": statistics.median(word_errors),
        "worst_word_errors": max(word_errors),
        "all_word_errors": word_errors,
        "all_max_reference_deletion_runs": deletion_runs,
        "worst_max_reference_deletion_run": max(deletion_runs),
        "best_wer": min(word_error_rates),
        "median_wer": statistics.median(word_error_rates),
        "worst_wer": max(word_error_rates),
        "all_wer": word_error_rates,
        "best_case_sensitive_cer": min(case_sensitive_cers),
        "median_case_sensitive_cer": statistics.median(case_sensitive_cers),
        "worst_case_sensitive_cer": max(case_sensitive_cers),
        "all_case_sensitive_cer": case_sensitive_cers,
    }


def _boundary_run_retained(reference_words, hypothesis_words, *, from_end):
    """Check the entire repeated boundary word, not just its last occurrence.

    A last-token match alone calls ``go`` a retained ending for ``go go``.
    Requiring the consecutive boundary run catches that shortfall without
    pretending to align individual repeated sounds to the recording.
    """
    reference_boundary = (reversed(reference_words) if from_end
                          else iter(reference_words))
    hypothesis_boundary = (reversed(hypothesis_words) if from_end
                           else iter(hypothesis_words))
    boundary_word = reference_words[-1] if from_end else reference_words[0]
    required = 0
    for word in reference_boundary:
        if word != boundary_word:
            break
        required += 1
    for word in hypothesis_boundary:
        if word != boundary_word:
            break
        required -= 1
        if required == 0:
            return True
    return False


def paired_tail_silence_metrics(reference, baseline, tailed):
    """Score each clean/tailed Parakeet trial against the same reviewed words.

    Preserve pair order: a modal transcript can conceal an intermittent blank
    final decode, and comparing independent aggregate WERs loses that signal.
    """
    if not baseline or len(baseline) != len(tailed):
        raise ValueError("tail-silence probe needs matching non-empty trials")
    reference_words = _normalise_words(reference)
    if not reference_words:
        raise ValueError("tail-silence probe needs a scoreable reference")
    pairs = []
    for clean_text, tailed_text in zip(baseline, tailed):
        clean_errors = accuracy_metrics(reference, clean_text)["word_errors"]
        tailed_errors = accuracy_metrics(reference, tailed_text)["word_errors"]
        clean_words = _normalise_words(clean_text)
        tailed_words = _normalise_words(tailed_text)
        clean_kept_first = _boundary_run_retained(
            reference_words, clean_words, from_end=False)
        tailed_kept_first = _boundary_run_retained(
            reference_words, tailed_words, from_end=False)
        clean_kept_final = _boundary_run_retained(
            reference_words, clean_words, from_end=True)
        tailed_kept_final = _boundary_run_retained(
            reference_words, tailed_words, from_end=True)
        pairs.append({
            "baseline_transcript": clean_text,
            "tailed_transcript": tailed_text,
            "baseline_word_errors": clean_errors,
            "tailed_word_errors": tailed_errors,
            "nonempty_to_empty": bool(clean_text.strip()) and not tailed_text.strip(),
            "first_word_lost": clean_kept_first and not tailed_kept_first,
            "first_word_recovered": not clean_kept_first and tailed_kept_first,
            "final_word_lost": clean_kept_final and not tailed_kept_final,
            "final_word_recovered": not clean_kept_final and tailed_kept_final,
        })
    return {
        "trial_count": len(pairs),
        "baseline_empty_trial_count": sum(not text.strip() for text in baseline),
        "tailed_empty_trial_count": sum(not text.strip() for text in tailed),
        "nonempty_to_empty_trial_count": sum(
            pair["nonempty_to_empty"] for pair in pairs),
        "first_word_lost_trial_count": sum(
            pair["first_word_lost"] for pair in pairs),
        "first_word_recovered_trial_count": sum(
            pair["first_word_recovered"] for pair in pairs),
        "final_word_lost_trial_count": sum(
            pair["final_word_lost"] for pair in pairs),
        "final_word_recovered_trial_count": sum(
            pair["final_word_recovered"] for pair in pairs),
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
        "baseline_first_word_failure_trial_count": first_word_metrics(
            reference, baseline)["failed_trials"],
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
    paired_deltas = [
        delta for probe in probes
        for delta in probe["paired_inference_delta_seconds"]["all"]
    ]
    trial_orders = [
        order for probe in probes for order in probe["trial_order"]
    ]
    return {
        "sample_count": len(probes),
        "paired_inference_delta_seconds": _paired_delta_summary(
            paired_deltas, trial_orders),
        "order_breakdown": {
            order: {
                key: sum(
                    probe["order_breakdown"][order][key] for probe in probes)
                for key in ("trial_count", "nonempty_to_empty_trial_count",
                            "first_word_lost_trial_count",
                            "first_word_recovered_trial_count",
                            "final_word_lost_trial_count",
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
                "first_word_lost_trial_count",
                "first_word_recovered_trial_count",
                "final_word_lost_trial_count",
                "final_word_recovered_trial_count",
                "changed_text_trial_count", "baseline_word_error_count",
                "tailed_word_error_count", "worsened_word_error_trial_count",
                "improved_word_error_trial_count",
                "baseline_first_word_failure_trial_count",
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
            "first_word_lost_trial_count": sum(
                value == order and pair["first_word_lost"]
                for pair, value in zip(pairs, trial_order)),
            "first_word_recovered_trial_count": sum(
                value == order and pair["first_word_recovered"]
                for pair, value in zip(pairs, trial_order)),
            "final_word_lost_trial_count": sum(
                value == order and pair["final_word_lost"]
                for pair, value in zip(pairs, trial_order)),
            "worsened_word_error_trial_count": sum(
                value == order
                and pair["tailed_word_errors"] > pair["baseline_word_errors"]
                for pair, value in zip(pairs, trial_order)),
        }
        for order in ("baseline-first", "tailed-first")
    }


def paired_recorded_tail_metrics(reference, full, trimmed):
    """Compare a real captured tail with a reviewed speech-end crop.

    The full capture remains the product baseline. A better trimmed result is
    only a candidate signal, not proof that trimming is safe for other speech.
    """
    if not full or len(full) != len(trimmed):
        raise ValueError("recorded-tail probe needs matching non-empty trials")
    reference_words = _normalise_words(reference)
    if not reference_words:
        raise ValueError("recorded-tail probe needs a scoreable reference")
    pairs = []
    for full_text, trimmed_text in zip(full, trimmed):
        full_errors = accuracy_metrics(reference, full_text)["word_errors"]
        trimmed_errors = accuracy_metrics(reference, trimmed_text)["word_errors"]
        full_words = _normalise_words(full_text)
        trimmed_words = _normalise_words(trimmed_text)
        full_kept_first = _boundary_run_retained(
            reference_words, full_words, from_end=False)
        trimmed_kept_first = _boundary_run_retained(
            reference_words, trimmed_words, from_end=False)
        full_kept_final = _boundary_run_retained(
            reference_words, full_words, from_end=True)
        trimmed_kept_final = _boundary_run_retained(
            reference_words, trimmed_words, from_end=True)
        pairs.append({
            "full_transcript": full_text,
            "trimmed_transcript": trimmed_text,
            "full_word_errors": full_errors,
            "trimmed_word_errors": trimmed_errors,
            "trimmed_nonempty_to_full_empty": (
                bool(trimmed_text.strip()) and not full_text.strip()),
            "full_nonempty_to_trimmed_empty": (
                bool(full_text.strip()) and not trimmed_text.strip()),
            "full_kept_first_word": full_kept_first,
            "trimmed_kept_first_word": trimmed_kept_first,
            "first_word_lost": full_kept_first and not trimmed_kept_first,
            "first_word_recovered": not full_kept_first and trimmed_kept_first,
            # A crop can lose an edge word while fixing another word, leaving
            # WER unchanged. Track both boundaries independently.
            "full_kept_final_word": full_kept_final,
            "trimmed_kept_final_word": trimmed_kept_final,
            "final_word_lost": full_kept_final and not trimmed_kept_final,
            "final_word_recovered": not full_kept_final and trimmed_kept_final,
        })
    return {
        "trial_count": len(pairs),
        "full_empty_trial_count": sum(not value.strip() for value in full),
        "trimmed_empty_trial_count": sum(not value.strip() for value in trimmed),
        "trimmed_nonempty_to_full_empty_trial_count": sum(
            pair["trimmed_nonempty_to_full_empty"] for pair in pairs),
        "full_nonempty_to_trimmed_empty_trial_count": sum(
            pair["full_nonempty_to_trimmed_empty"] for pair in pairs),
        "full_first_word_failure_trial_count": sum(
            not pair["full_kept_first_word"] for pair in pairs),
        "trimmed_first_word_failure_trial_count": sum(
            not pair["trimmed_kept_first_word"] for pair in pairs),
        "first_word_lost_trial_count": sum(
            pair["first_word_lost"] for pair in pairs),
        "first_word_recovered_trial_count": sum(
            pair["first_word_recovered"] for pair in pairs),
        "full_final_word_failure_trial_count": sum(
            not pair["full_kept_final_word"] for pair in pairs),
        "trimmed_final_word_failure_trial_count": sum(
            not pair["trimmed_kept_final_word"] for pair in pairs),
        "final_word_lost_trial_count": sum(
            pair["final_word_lost"] for pair in pairs),
        "final_word_recovered_trial_count": sum(
            pair["final_word_recovered"] for pair in pairs),
        "changed_text_trial_count": sum(
            _canonical_text(full_text) != _canonical_text(trimmed_text)
            for full_text, trimmed_text in zip(full, trimmed)),
        "full_word_error_count": sum(pair["full_word_errors"] for pair in pairs),
        "trimmed_word_error_count": sum(
            pair["trimmed_word_errors"] for pair in pairs),
        "full_worsened_word_error_trial_count": sum(
            pair["full_word_errors"] > pair["trimmed_word_errors"]
            for pair in pairs),
        "trimmed_worsened_word_error_trial_count": sum(
            pair["trimmed_word_errors"] > pair["full_word_errors"]
            for pair in pairs),
        "pairs": pairs,
    }


def recorded_tail_order_breakdown(pairs, trial_order):
    if len(pairs) != len(trial_order) or any(
            order not in ("full-first", "trimmed-first")
            for order in trial_order):
        raise ValueError("recorded-tail probe needs one valid order per pair")
    return {
        order: {
            "trial_count": sum(value == order for value in trial_order),
            "trimmed_nonempty_to_full_empty_trial_count": sum(
                value == order and pair["trimmed_nonempty_to_full_empty"]
                for pair, value in zip(pairs, trial_order)),
            "full_nonempty_to_trimmed_empty_trial_count": sum(
                value == order and pair["full_nonempty_to_trimmed_empty"]
                for pair, value in zip(pairs, trial_order)),
            "first_word_lost_trial_count": sum(
                value == order and pair["first_word_lost"]
                for pair, value in zip(pairs, trial_order)),
            "first_word_recovered_trial_count": sum(
                value == order and pair["first_word_recovered"]
                for pair, value in zip(pairs, trial_order)),
            "final_word_lost_trial_count": sum(
                value == order and pair["final_word_lost"]
                for pair, value in zip(pairs, trial_order)),
            "final_word_recovered_trial_count": sum(
                value == order and pair["final_word_recovered"]
                for pair, value in zip(pairs, trial_order)),
            "full_worsened_word_error_trial_count": sum(
                value == order
                and pair["full_word_errors"] > pair["trimmed_word_errors"]
                for pair, value in zip(pairs, trial_order)),
            "trimmed_worsened_word_error_trial_count": sum(
                value == order
                and pair["trimmed_word_errors"] > pair["full_word_errors"]
                for pair, value in zip(pairs, trial_order)),
        }
        for order in ("full-first", "trimmed-first")
    }


def summarise_recorded_tail_probe(samples):
    probes = [sample["recorded_tail_probe"] for sample in samples
              if sample.get("recorded_tail_probe") is not None]
    paired_deltas = [
        delta for probe in probes
        for delta in probe["paired_inference_delta_seconds"]["all"]
    ]
    trial_orders = [order for probe in probes for order in probe["trial_order"]]
    count_keys = (
        "trial_count", "full_empty_trial_count", "trimmed_empty_trial_count",
        "trimmed_nonempty_to_full_empty_trial_count",
        "full_nonempty_to_trimmed_empty_trial_count", "changed_text_trial_count",
        "full_first_word_failure_trial_count",
        "trimmed_first_word_failure_trial_count",
        "first_word_lost_trial_count", "first_word_recovered_trial_count",
        "full_final_word_failure_trial_count",
        "trimmed_final_word_failure_trial_count",
        "final_word_lost_trial_count", "final_word_recovered_trial_count",
        "full_word_error_count", "trimmed_word_error_count",
        "full_worsened_word_error_trial_count",
        "trimmed_worsened_word_error_trial_count",
    )
    return {
        "sample_count": len(probes),
        "full_first_trial_count": sum(
            order == "full-first"
            for probe in probes for order in probe["trial_order"]),
        "trimmed_first_trial_count": sum(
            order == "trimmed-first"
            for probe in probes for order in probe["trial_order"]),
        "order_breakdown": {
            order: {
                key: sum(probe["order_breakdown"][order][key] for probe in probes)
                for key in ("trial_count",
                            "trimmed_nonempty_to_full_empty_trial_count",
                            "full_nonempty_to_trimmed_empty_trial_count",
                            "first_word_lost_trial_count",
                            "first_word_recovered_trial_count",
                            "final_word_lost_trial_count",
                            "final_word_recovered_trial_count",
                            "full_worsened_word_error_trial_count",
                            "trimmed_worsened_word_error_trial_count")
            }
            for order in ("full-first", "trimmed-first")
        },
        "paired_inference_delta_seconds": _paired_delta_summary(
            paired_deltas, trial_orders, ("full-first", "trimmed-first")),
        **{key: sum(probe[key] for probe in probes) for key in count_keys},
    }



def probe_group_metrics(samples, probe_field, summarise):
    """Stratify paired speech probes without counting unprobed controls.

    Keep each group's pair count and decode-order breakdown: a pooled result
    can hide harm to a smaller language/task group, and an uneven order can
    make a small stratum misleading. Unlabelled clips remain in the corpus.
    """
    tasks = collections.defaultdict(list)
    languages = collections.defaultdict(list)
    intersections = collections.defaultdict(lambda: collections.defaultdict(list))
    for sample in samples:
        if sample.get(probe_field) is None:
            continue
        task = sample.get("task_group")
        language = sample.get("language_group")
        task = task.strip() if isinstance(task, str) else None
        language = language.strip() if isinstance(language, str) else None
        if task:
            tasks[task].append(sample)
        if language:
            languages[language].append(sample)
        if task and language:
            intersections[language][task].append(sample)
    return {
        "task_groups": {
            name: summarise(members) for name, members in sorted(tasks.items())
        },
        "language_groups": {
            name: summarise(members)
            for name, members in sorted(languages.items())
        },
        "language_task_groups": {
            language: {
                task: summarise(members)
                for task, members in sorted(task_groups.items())
            }
            for language, task_groups in sorted(intersections.items())
        },
    }


def _paired_delta_summary(deltas, trial_order,
                          orders=("baseline-first", "tailed-first")):
    """Summarise signed variant-minus-baseline time by execution order."""
    if len(deltas) != len(trial_order) or any(
            order not in orders
            for order in trial_order):
        raise ValueError("paired latency needs one valid order per trial")

    def distribution(values):
        return {
            "trial_count": len(values),
            "median": statistics.median(values) if values else None,
            "p95": _percentile(values, 0.95) if values else None,
            "all": values,
        }

    return {
        **distribution(deltas),
        "by_order": {
            order: distribution([
                delta for delta, observed_order in zip(deltas, trial_order)
                if observed_order == order
            ])
            for order in orders
        },
    }


def paired_tail_latency_metrics(baseline_seconds, tailed_seconds, trial_order):
    """Keep each tail cost paired with its clean decode, including signed wins."""
    return _paired_latency_metrics(
        baseline_seconds, tailed_seconds, trial_order,
        ("baseline-first", "tailed-first"))


def paired_recorded_tail_latency_metrics(full_seconds, trimmed_seconds,
                                         trial_order):
    """Keep each crop cost paired with its captured baseline decode."""
    return _paired_latency_metrics(
        full_seconds, trimmed_seconds, trial_order,
        ("full-first", "trimmed-first"))


def _paired_latency_metrics(baseline_seconds, variant_seconds, trial_order,
                            orders):
    if (not baseline_seconds or len(baseline_seconds) != len(variant_seconds)
            or len(baseline_seconds) != len(trial_order)):
        raise ValueError("paired latency needs matching non-empty trials")
    if any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0
            for value in (*baseline_seconds, *variant_seconds)):
        raise ValueError("paired latency needs finite non-negative timings")
    deltas = [variant - baseline for baseline, variant in zip(
        baseline_seconds, variant_seconds)]
    return _paired_delta_summary(deltas, trial_order, orders)


def final_word_metrics(reference, hypotheses):
    """Score terminal-word-run retention across every reviewed trial."""
    reference_words = _normalise_words(reference)
    if not reference_words:
        return None
    retained_trials = sum(
        _boundary_run_retained(
            reference_words, _normalise_words(hypothesis), from_end=True)
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
    """Score initial-word-run retention across every reviewed trial."""
    reference_words = _normalise_words(reference)
    if not reference_words:
        return None
    retained_trials = sum(
        _boundary_run_retained(
            reference_words, _normalise_words(hypothesis), from_end=False)
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


def language_identification_metrics(language_group, backend_timings):
    """Compare auto-detected codes with a reviewed clip's language label.

    Only a BCP-47-like label with a two/three-letter primary language can be
    compared with faster-whisper's short codes. A VAD-rejected trial has no
    meaningful language result; keep it visible rather than calling it a
    correct detection or a mismatch. Missing/invalid codes on other trials
    likewise cannot count as successful identification.
    """
    if (not isinstance(language_group, str) or not re.fullmatch(
            r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", language_group)):
        return None
    expected = language_group.split("-", 1)[0]
    rejected = 0
    matched = 0
    mismatched = 0
    for timing in backend_timings:
        if not isinstance(timing, dict):
            continue
        speech_seconds = timing.get("speech_seconds")
        if (isinstance(speech_seconds, (int, float))
                and not isinstance(speech_seconds, bool)
                and math.isfinite(speech_seconds) and speech_seconds == 0):
            rejected += 1
            continue
        detected = timing.get("detected_language")
        if isinstance(detected, str) and re.fullmatch(r"[a-z]{2,3}", detected):
            if detected == expected:
                matched += 1
            else:
                mismatched += 1
    observed = matched + mismatched
    missing = len(backend_timings) - rejected - observed
    return {
        "expected_language": expected,
        "trials": len(backend_timings),
        "vad_rejected_trials": rejected,
        "observed_trials": observed,
        "matched_trials": matched,
        "mismatched_trials": mismatched,
        "missing_trials": missing,
        "coverage_complete": rejected == 0 and missing == 0,
    }


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


def _reviewed_language_identification_metrics(reviewed):
    """Aggregate only reviewed auto-language trials with comparable labels."""
    labelled = [sample["language_identification"] for sample in reviewed
                if isinstance(sample.get("language_identification"), dict)]
    return {
        "reviewed_language_id_sample_count": len(labelled),
        "reviewed_language_id_trial_count": sum(
            item["trials"] for item in labelled),
        "reviewed_language_id_observed_trial_count": sum(
            item["observed_trials"] for item in labelled),
        "reviewed_language_id_matched_trial_count": sum(
            item["matched_trials"] for item in labelled),
        "reviewed_language_id_mismatched_trial_count": sum(
            item["mismatched_trials"] for item in labelled),
        "reviewed_language_id_missing_trial_count": sum(
            item["missing_trials"] for item in labelled),
        "reviewed_language_id_vad_rejected_trial_count": sum(
            item["vad_rejected_trials"] for item in labelled),
        "reviewed_language_id_coverage_complete": (
            all(item["coverage_complete"] for item in labelled)
            if labelled else None),
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
        **_reviewed_language_identification_metrics(reviewed),
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


def _recorded_tail_probe_applies(sample, enabled):
    return (enabled and "speech_end_ms" in sample
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


def _validate_recorded_tail_probe_bucket(sample_count, speech_end_ms):
    """Validate a listened-to endpoint against the *effective* 16 kHz audio."""
    trim_at = speech_end_ms * engine.PARAKEET_SAMPLE_RATE // 1000
    removed = sample_count - trim_at
    minimum_removed = engine.PARAKEET_SAMPLE_RATE // 1000
    maximum_removed = int(app.POST_ROLL_MAX_SEC * engine.PARAKEET_SAMPLE_RATE)
    if trim_at < 1 or removed < minimum_removed or removed > maximum_removed:
        raise ValueError(
            "recorded-tail probe needs 1–%d ms of audio after speech_end_ms"
            % int(app.POST_ROLL_MAX_SEC * 1000))
    maximum = engine.PARAKEET_MAX_WINDOW_SECONDS * engine.PARAKEET_SAMPLE_RATE
    if sample_count > maximum:
        raise ValueError(
            "recorded-tail probe needs both variants in one Parakeet window")
    trimmed_bucket = engine._parakeet_bucket_seconds(
        trim_at / engine.PARAKEET_SAMPLE_RATE)
    full_bucket = engine._parakeet_bucket_seconds(
        sample_count / engine.PARAKEET_SAMPLE_RATE)
    if trimmed_bucket != full_bucket:
        raise ValueError(
            "recorded-tail probe needs both variants in the same Parakeet "
            "feature bucket; use a shorter clip")
    return trim_at


def _preflight_audio(manifest_dir, samples, *, parakeet_tail_silence_ms=None,
                     parakeet_recorded_tail_probe=False):
    """Decode every fixture before costly model setup without retaining audio.

    The second read for inference must match the exact signal checked here;
    otherwise an edited fixture could make a partially paired report appear
    valid. Distinct IDs must not count the same effective recording twice as
    independent evidence. Only digests and non-sensitive audio metadata remain
    in memory.
    """
    checked = []
    audio_digests = set()
    for sample in samples:
        path = sample["audio"]
        if not os.path.isabs(path):
            path = os.path.join(manifest_dir, path)
        audio, seconds, source_rate, digest = load_audio(path)
        if digest in audio_digests:
            # Do not include paths or IDs: local benchmark names can reveal
            # private dictation content, even in an error message.
            raise ValueError(
                "benchmark contains duplicate effective ASR audio fixtures")
        audio_digests.add(digest)
        if _tail_probe_applies(sample, parakeet_tail_silence_ms):
            _validate_tail_probe_bucket(len(audio), parakeet_tail_silence_ms)
        if _recorded_tail_probe_applies(sample, parakeet_recorded_tail_probe):
            _validate_recorded_tail_probe_bucket(
                len(audio), sample["speech_end_ms"])
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
        # Reports are routinely copied for review. Upstream error text can
        # contain private installation paths or environment details.
        result["torch_error"] = type(exc).__name__
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
                  parakeet_tail_silence_ms=None,
                  parakeet_recorded_tail_probe=False):
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
        if "speech_end_ms" in sample:
            endpoint = sample["speech_end_ms"]
            if (isinstance(endpoint, bool) or not isinstance(endpoint, int)
                    or endpoint < 1):
                raise ValueError("sample speech_end_ms must be a positive integer")
            if (sample.get("expected_silence", False)
                    or not sample.get("reference_reviewed", False)
                    or not _normalise_words(reference)):
                raise ValueError(
                    "sample speech_end_ms needs reviewed, scoreable speech")
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
    if not isinstance(parakeet_recorded_tail_probe, bool):
        raise ValueError("recorded-tail probe must be a boolean")
    if parakeet_recorded_tail_probe and parakeet_tail_silence_ms is not None:
        raise ValueError("choose only one Parakeet tail probe")
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
        if any("speech_end_ms" in sample for sample in samples):
            raise ValueError(
                "synthetic tail-silence probe requires endpoint-cropped clips; "
                "use the recorded-tail probe for annotated full captures")
        if not any(sample.get("reference_reviewed", False)
                   and not sample.get("expected_silence", False)
                   and _normalise_words(sample.get("reference", ""))
                   for sample in samples):
            raise ValueError(
                "tail-silence probe needs reviewed speech with scoreable words")
    if parakeet_recorded_tail_probe:
        if not engine.is_parakeet(model_name):
            raise ValueError("recorded-tail probe requires a Parakeet model")
        if not any(_recorded_tail_probe_applies(sample, True)
                   for sample in samples):
            raise ValueError(
                "recorded-tail probe needs reviewed speech with speech_end_ms")
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
        parakeet_tail_silence_ms=parakeet_tail_silence_ms,
        parakeet_recorded_tail_probe=parakeet_recorded_tail_probe)

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
    recorded_probe_rows = []
    probe_pair_index = 0
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
        synthetic_probe = _tail_probe_applies(
            sample, parakeet_tail_silence_ms)
        recorded_probe = _recorded_tail_probe_applies(
            sample, parakeet_recorded_tail_probe)
        variant_audio = (np.concatenate((audio, np.zeros(
            parakeet_tail_silence_ms * engine.PARAKEET_SAMPLE_RATE // 1000,
            dtype=np.float32)))
            if synthetic_probe else None)
        trim_at = None
        if recorded_probe:
            # Already validated before model load. The preflight digest above
            # proves this is still the same full captured signal.
            trim_at = _validate_recorded_tail_probe_bucket(
                len(audio), sample["speech_end_ms"])
            variant_audio = audio[:trim_at]
            recorded_probe_rows.append({
                "asr_audio_sha256": audio_digest,
                "trim_at_sample": trim_at,
            })
        probe_this_sample = synthetic_probe or recorded_probe
        variant_timings = []
        variant_transcripts = []
        trial_order = []
        for _run in range(runs):
            # Alternate across *all* reviewed probe pairs, not just within a
            # clip. This keeps corpus order counts within one even when runs
            # is odd. Otherwise the variant is always measured second.
            variant_first = probe_this_sample and probe_pair_index % 2 == 1
            if probe_this_sample:
                if synthetic_probe:
                    trial_order.append(
                        "tailed-first" if variant_first else "baseline-first")
                else:
                    trial_order.append(
                        "trimmed-first" if variant_first else "full-first")
                probe_pair_index += 1
            if variant_first:
                variant_text, variant_seconds, _ = _timed_transcription(
                    transcriber, variant_audio, language_hint)
                variant_transcripts.append(variant_text)
                variant_timings.append(variant_seconds)
            transcript, seconds, backend_timing = _timed_transcription(
                transcriber, audio, language_hint)
            timings.append(seconds)
            transcripts.append(transcript)
            backend_timings.append(backend_timing)
            if probe_this_sample and not variant_first:
                variant_text, variant_seconds, _ = _timed_transcription(
                    transcriber, variant_audio, language_hint)
                variant_transcripts.append(variant_text)
                variant_timings.append(variant_seconds)
        consensus = collections.Counter(transcripts).most_common(1)[0][0]
        median_seconds = statistics.median(timings)
        # The benchmark may deliberately exercise sub-threshold model inputs,
        # but the app discards such captures before invoking any recognizer.
        # Keep their model-only scores while never inventing delivery latency.
        # The real loader returns effective 16 kHz samples; the source-file
        # duration can round differently at the 250 ms boundary after resampling.
        # Lightweight injected loaders may supply only a reported duration.
        effective_sample_count = (
            len(audio) if hasattr(audio, "__len__")
            else round(audio_seconds * 16000))
        passes_app_minimum = (
            effective_sample_count >= app.MIN_TRANSCRIPTION_AUDIO_SAMPLES)
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
            "passes_app_minimum_audio_duration": passes_app_minimum,
            "estimated_release_to_paste_seconds": (
                app.POST_ROLL_SEC + median_seconds + app.PASTE_DELAY_SEC
                if passes_app_minimum else None),
            "estimated_adaptive_release_to_paste_seconds": (
                app.POST_ROLL_MIN_SEC + median_seconds + app.PASTE_DELAY_SEC
                if passes_app_minimum else None),
            "reference_reviewed": sample.get("reference_reviewed", False),
            "speech_detection": speech_detection_metrics(
                audio_seconds, backend_timings,
                expected_trials=(runs if model_name in engine.WHISPER_MODELS
                                 else None)),
            "detected_languages": detected_language_metrics(backend_timings),
            "backend_stages": backend_stage_metrics(backend_timings),
            "parakeet_windowing": parakeet_window_metrics(backend_timings),
        }
        if synthetic_probe:
            paired_metrics = paired_tail_silence_metrics(
                reference, transcripts, variant_transcripts)
            result["tail_silence_probe"] = {
                "appended_silence_ms": parakeet_tail_silence_ms,
                # Indexed like pairs and both timing arrays below.
                "trial_order": trial_order,
                **paired_metrics,
                "order_breakdown": tail_probe_order_breakdown(
                    paired_metrics["pairs"], trial_order),
                "tailed_inference_seconds": {
                    "min": min(variant_timings),
                    "median": statistics.median(variant_timings),
                    "p95": _percentile(variant_timings, 0.95),
                    "all": variant_timings,
                },
                # Signed per-trial deltas distinguish a tail cost from the
                # ordinary first/second decode effect in a paired probe.
                "paired_inference_delta_seconds": paired_tail_latency_metrics(
                    timings, variant_timings, trial_order),
            }
        if recorded_probe:
            paired_metrics = paired_recorded_tail_metrics(
                reference, transcripts, variant_transcripts)
            result["recorded_tail_probe"] = {
                "speech_end_ms": sample["speech_end_ms"],
                "trim_at_sample": trim_at,
                "removed_tail_ms": (
                    (len(audio) - trim_at) * 1000
                    / engine.PARAKEET_SAMPLE_RATE),
                "trial_order": trial_order,
                **paired_metrics,
                "order_breakdown": recorded_tail_order_breakdown(
                    paired_metrics["pairs"], trial_order),
                "trimmed_inference_seconds": {
                    "min": min(variant_timings),
                    "median": statistics.median(variant_timings),
                    "p95": _percentile(variant_timings, 0.95),
                    "all": variant_timings,
                },
                "paired_inference_delta_seconds": (
                    paired_recorded_tail_latency_metrics(
                        timings, variant_timings, trial_order)),
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
        if (model_name == "turbo" and requested_language == "auto"
                and result["accuracy"] is not None):
            language_id = language_identification_metrics(
                result.get("language_group"), backend_timings)
            if language_id is not None:
                result["language_identification"] = language_id
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
        "benchmark_version": 25,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "benchmark_inputs_sha256": benchmark_inputs_sha256(input_rows),
        "benchmark_order_sha256": benchmark_order_sha256(input_rows),
        "recorded_tail_probe_inputs_sha256": (
            recorded_tail_probe_inputs_sha256(recorded_probe_rows)
            if parakeet_recorded_tail_probe else None),
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
        "parakeet_recorded_tail_probe": parakeet_recorded_tail_probe,
        "tail_silence_probe": (
            summarise_tail_silence_probe(sample_results)
            if parakeet_tail_silence_ms is not None else None),
        "tail_silence_probe_groups": (
            probe_group_metrics(sample_results, "tail_silence_probe",
                                summarise_tail_silence_probe)
            if parakeet_tail_silence_ms is not None else None),
        "recorded_tail_probe": (
            summarise_recorded_tail_probe(sample_results)
            if parakeet_recorded_tail_probe else None),
        "recorded_tail_probe_groups": (
            probe_group_metrics(sample_results, "recorded_tail_probe",
                                summarise_recorded_tail_probe)
            if parakeet_recorded_tail_probe else None),
        "precision": precision,
        "model_dtype": model_dtype,
        "cuda_allocated_mib": cuda_allocated_mib,
        "environment": _environment(),
        "load_seconds": load_seconds,
        "warmup_seconds": warmup_seconds,
        "sample_count": len(sample_results),
        "app_minimum_audio_duration_seconds": app.MIN_TRANSCRIPTION_AUDIO_SECONDS,
        "below_app_minimum_audio_duration_count": sum(
            not item["passes_app_minimum_audio_duration"]
            for item in sample_results),
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
        "worst_reference_deletion_run": (
            max(item["trial_accuracy"]["worst_max_reference_deletion_run"]
                for item in reviewed) if reviewed else None),
        "reviewed_silence_sample_count": len(reviewed_silence),
        "silence_false_positive_count": sum(
            item["silence"]["false_positive"] for item in reviewed_silence),
        "reviewed_silence_trial_count": sum(
            item["silence"]["trials"] for item in reviewed_silence),
        "silence_false_positive_trial_count": sum(
            item["silence"]["false_positive_trials"]
            for item in reviewed_silence),
        **_reviewed_speech_vad_metrics(reviewed),
        **_reviewed_language_identification_metrics(reviewed),
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
    print("Benchmark order SHA-256: %s" % result["benchmark_order_sha256"])
    if result.get("recorded_tail_probe_inputs_sha256") is not None:
        print("Recorded-tail probe inputs SHA-256: %s" %
              result["recorded_tail_probe_inputs_sha256"])
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
    if result["below_app_minimum_audio_duration_count"]:
        print("Model-only clips below the app's %.0f ms transcription gate: "
              "%d/%d; included in model scores, not delivery estimates" % (
                  result["app_minimum_audio_duration_seconds"] * 1000,
                  result["below_app_minimum_audio_duration_count"],
                  result["sample_count"],
              ))
    tail_probe = result.get("tail_silence_probe")
    if tail_probe is not None:
        print("Parakeet +%d ms silence (benchmark-only): nonempty-to-empty "
              "%d/%d paired trials; first word lost %d, recovered %d "
              "(clean missing %d, tailed missing %d); "
              "final word lost %d, recovered %d; "
              "word errors %d -> %d; worsened %d, "
              "improved %d trials across %d reviewed clips; order "
              "baseline-first %d, tailed-first %d" % (
                  result["parakeet_tail_silence_ms"],
                  tail_probe["nonempty_to_empty_trial_count"],
                  tail_probe["trial_count"],
                  tail_probe["first_word_lost_trial_count"],
                  tail_probe["first_word_recovered_trial_count"],
                  tail_probe["baseline_first_word_failure_trial_count"],
                  tail_probe["tailed_first_word_failure_trial_count"],
                  tail_probe["final_word_lost_trial_count"],
                  tail_probe["final_word_recovered_trial_count"],
                  tail_probe["baseline_word_error_count"],
                  tail_probe["tailed_word_error_count"],
                  tail_probe["worsened_word_error_trial_count"],
                  tail_probe["improved_word_error_trial_count"],
                  tail_probe["sample_count"],
                  tail_probe["baseline_first_trial_count"],
                  tail_probe["tailed_first_trial_count"],
              ))
        for order, counts in tail_probe["order_breakdown"].items():
            print("  %s: nonempty-to-empty %d/%d; first word lost %d/%d, "
                  "recovered %d/%d; final word lost %d/%d; "
                  "worsened word errors "
                  "%d/%d trials" % (
                      order,
                      counts["nonempty_to_empty_trial_count"],
                      counts["trial_count"],
                      counts["first_word_lost_trial_count"],
                      counts["trial_count"],
                      counts["first_word_recovered_trial_count"],
                      counts["trial_count"],
                      counts["final_word_lost_trial_count"],
                      counts["trial_count"],
                      counts["worsened_word_error_trial_count"],
                      counts["trial_count"],
                  ))
        paired_latency = tail_probe["paired_inference_delta_seconds"]
        if paired_latency["trial_count"]:
            print("  Paired inference tail-minus-clean median %+.3fs "
                  "(positive is slower; benchmark-only)" % paired_latency["median"])
            for order, latency in paired_latency["by_order"].items():
                if latency["trial_count"]:
                    print("    %s: median %+.3fs across %d pairs" % (
                        order, latency["median"], latency["trial_count"]))
        else:
            print("  Paired inference tail-minus-clean: no reviewed speech pairs")
    recorded_probe = result.get("recorded_tail_probe")
    if recorded_probe is not None:
        print("Parakeet recorded tail (benchmark-only): trimmed nonempty to "
              "full empty %d/%d paired trials, full nonempty to trimmed "
              "empty %d/%d; first word lost %d, recovered %d "
              "(full missing %d, trimmed missing %d); "
              "final word lost %d, recovered %d "
              "(full missing %d, trimmed missing %d); "
              "word errors trimmed %d -> full %d; full worse "
              "%d, trimmed worse %d trials across %d "
              "reviewed clips; order full-first %d, trimmed-first %d" % (
                  recorded_probe[
                      "trimmed_nonempty_to_full_empty_trial_count"],
                  recorded_probe["trial_count"],
                  recorded_probe[
                      "full_nonempty_to_trimmed_empty_trial_count"],
                  recorded_probe["trial_count"],
                  recorded_probe["first_word_lost_trial_count"],
                  recorded_probe["first_word_recovered_trial_count"],
                  recorded_probe["full_first_word_failure_trial_count"],
                  recorded_probe["trimmed_first_word_failure_trial_count"],
                  recorded_probe["final_word_lost_trial_count"],
                  recorded_probe["final_word_recovered_trial_count"],
                  recorded_probe["full_final_word_failure_trial_count"],
                  recorded_probe["trimmed_final_word_failure_trial_count"],
                  recorded_probe["trimmed_word_error_count"],
                  recorded_probe["full_word_error_count"],
                  recorded_probe["full_worsened_word_error_trial_count"],
                  recorded_probe["trimmed_worsened_word_error_trial_count"],
                  recorded_probe["sample_count"],
                  recorded_probe["full_first_trial_count"],
                  recorded_probe["trimmed_first_trial_count"],
              ))
        for order, counts in recorded_probe["order_breakdown"].items():
            print("  %s: trimmed nonempty to full empty %d/%d; full "
                  "nonempty to trimmed empty %d/%d; first word lost %d/%d, "
                  "recovered %d/%d; final word lost %d/%d, "
                  "recovered %d/%d; full worsened word "
                  "errors %d/%d, trimmed worsened %d/%d trials" % (
                      order,
                      counts["trimmed_nonempty_to_full_empty_trial_count"],
                      counts["trial_count"],
                      counts["full_nonempty_to_trimmed_empty_trial_count"],
                      counts["trial_count"],
                      counts["first_word_lost_trial_count"],
                      counts["trial_count"],
                      counts["first_word_recovered_trial_count"],
                      counts["trial_count"],
                      counts["final_word_lost_trial_count"],
                      counts["trial_count"],
                      counts["final_word_recovered_trial_count"],
                      counts["trial_count"],
                      counts["full_worsened_word_error_trial_count"],
                      counts["trial_count"],
                      counts["trimmed_worsened_word_error_trial_count"],
                      counts["trial_count"],
                  ))
        paired_latency = recorded_probe["paired_inference_delta_seconds"]
        if paired_latency["trial_count"]:
            print("  Paired inference trimmed-minus-full median %+.3fs "
                  "(positive is slower; benchmark-only)" %
                  paired_latency["median"])
            for order, latency in paired_latency["by_order"].items():
                if latency["trial_count"]:
                    print("    %s: median %+.3fs across %d pairs" % (
                        order, latency["median"], latency["trial_count"]))
        else:
            print("  Paired inference trimmed-minus-full: no reviewed speech pairs")
    for probe_name, groups in (
            ("Synthetic tail", result.get("tail_silence_probe_groups")),
            ("Recorded tail", result.get("recorded_tail_probe_groups"))):
        if groups is None:
            continue
        labelled = [
            ("task", name, metrics)
            for name, metrics in groups["task_groups"].items()
        ] + [
            ("language", name, metrics)
            for name, metrics in groups["language_groups"].items()
        ] + [
            ("language/task", "%s / %s" % (language, task), metrics)
            for language, tasks in groups["language_task_groups"].items()
            for task, metrics in tasks.items()
        ]
        for dimension, label, metrics in labelled:
            if probe_name == "Synthetic tail":
                harm = "blanked %d, first lost %d, final lost %d, WER-worsened %d" % (
                    metrics["nonempty_to_empty_trial_count"],
                    metrics["first_word_lost_trial_count"],
                    metrics["final_word_lost_trial_count"],
                    metrics["worsened_word_error_trial_count"])
                order = "baseline-first %d, tailed-first %d" % (
                    metrics["baseline_first_trial_count"],
                    metrics["tailed_first_trial_count"])
            else:
                harm = ("full blanked %d, trim blanked %d, first lost %d, "
                        "final lost %d, "
                        "full WER-worsened %d, trim WER-worsened %d" % (
                            metrics["trimmed_nonempty_to_full_empty_trial_count"],
                            metrics["full_nonempty_to_trimmed_empty_trial_count"],
                            metrics["first_word_lost_trial_count"],
                            metrics["final_word_lost_trial_count"],
                            metrics["full_worsened_word_error_trial_count"],
                            metrics["trimmed_worsened_word_error_trial_count"]))
                order = "full-first %d, trimmed-first %d" % (
                    metrics["full_first_trial_count"],
                    metrics["trimmed_first_trial_count"])
            print("%s %s %s: %d clips / %d pairs; %s; %s" % (
                probe_name, dimension, label, metrics["sample_count"],
                metrics["trial_count"], harm, order))
    if result["aggregate_wer"] is not None:
        print("Reviewed corpus WER: %.2f%% consensus | %.2f%% all trials | "
              "%.2f/%.2f%% best/worst trial envelope" % (
                  result["aggregate_wer"] * 100,
                  result["aggregate_trial_wer"] * 100,
                  result["aggregate_best_trial_wer"] * 100,
                  result["aggregate_worst_trial_wer"] * 100,
              ))
        print("Worst consecutive reference-word deletion: %d words across "
              "reviewed trials" % result["worst_reference_deletion_run"])
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

    def print_group_language_id(metrics):
        if not metrics["reviewed_language_id_sample_count"]:
            return
        print("  Reviewed auto-language ID: matched %d/%d observed trials; "
              "%d mismatched, %d missing, %d VAD-rejected across %d clips "
              "(%d total trials; %s coverage)" % (
                  metrics["reviewed_language_id_matched_trial_count"],
                  metrics["reviewed_language_id_observed_trial_count"],
                  metrics["reviewed_language_id_mismatched_trial_count"],
                  metrics["reviewed_language_id_missing_trial_count"],
                  metrics["reviewed_language_id_vad_rejected_trial_count"],
                  metrics["reviewed_language_id_sample_count"],
                  metrics["reviewed_language_id_trial_count"],
                  "complete" if metrics["reviewed_language_id_coverage_complete"]
                  else "incomplete",
              ))

    print_group_vad(result)
    print_group_language_id(result)

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
            print_group_language_id(metrics)

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
            print_group_language_id(metrics)
    for sample in result["samples"]:
        timing = sample["inference_seconds"]
        if sample["passes_app_minimum_audio_duration"]:
            print("\n%s: %.3fs median (%.1fx realtime; inference + min/max post-roll "
                  "+ paste delay %.3f/%.3fs, not measured delivery)" % (
                sample["id"], timing["median"], sample["realtime_speedup"],
                sample["estimated_adaptive_release_to_paste_seconds"],
                sample["estimated_release_to_paste_seconds"],
            ))
        else:
            print("\n%s: %.3fs median (%.1fx realtime; model-only clip below "
                  "the app's %.0f ms transcription gate; no delivery estimate)" % (
                      sample["id"], timing["median"],
                      sample["realtime_speedup"],
                      result["app_minimum_audio_duration_seconds"] * 1000,
                  ))
        print("  %s" % sample["transcript"])
        sample_tail_probe = sample.get("tail_silence_probe")
        if sample_tail_probe is not None:
            print("  +%d ms silence: nonempty-to-empty %d/%d paired trials; "
                  "first word lost %d, recovered %d; "
                  "final word lost %d, recovered %d; word errors %d -> %d; "
                  "tailed inference %.3fs median" % (
                      sample_tail_probe["appended_silence_ms"],
                      sample_tail_probe["nonempty_to_empty_trial_count"],
                      sample_tail_probe["trial_count"],
                      sample_tail_probe["first_word_lost_trial_count"],
                      sample_tail_probe["first_word_recovered_trial_count"],
                      sample_tail_probe["final_word_lost_trial_count"],
                      sample_tail_probe["final_word_recovered_trial_count"],
                      sample_tail_probe["baseline_word_error_count"],
                      sample_tail_probe["tailed_word_error_count"],
                      sample_tail_probe["tailed_inference_seconds"]["median"],
                  ))
        sample_recorded_probe = sample.get("recorded_tail_probe")
        if sample_recorded_probe is not None:
            print("  Recorded-tail crop: first word lost %d, recovered %d; "
                  "final word lost %d, recovered %d "
                  "across %d pairs; full/trimmed final-word failures %d/%d; "
                  "word errors full %d -> trimmed %d" % (
                      sample_recorded_probe["first_word_lost_trial_count"],
                      sample_recorded_probe["first_word_recovered_trial_count"],
                      sample_recorded_probe["final_word_lost_trial_count"],
                      sample_recorded_probe["final_word_recovered_trial_count"],
                      sample_recorded_probe["trial_count"],
                      sample_recorded_probe["full_final_word_failure_trial_count"],
                      sample_recorded_probe["trimmed_final_word_failure_trial_count"],
                      sample_recorded_probe["full_word_error_count"],
                      sample_recorded_probe["trimmed_word_error_count"],
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
        language_id = sample.get("language_identification")
        if language_id is not None:
            print("  Reviewed auto-language ID (%s): matched %d/%d observed "
                  "trials; %d mismatched, %d missing, %d VAD-rejected "
                  "(%s coverage)" % (
                      language_id["expected_language"],
                      language_id["matched_trials"],
                      language_id["observed_trials"],
                      language_id["mismatched_trials"],
                      language_id["missing_trials"],
                      language_id["vad_rejected_trials"],
                      "complete" if language_id["coverage_complete"]
                      else "incomplete",
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
            print("  Longest reference-word deletion: %d consensus / %d "
                  "worst trial" % (
                      sample["accuracy"]["max_reference_deletion_run"],
                      trial_accuracy["worst_max_reference_deletion_run"],
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
    parser.add_argument(
        "--parakeet-recorded-tail-probe", action="store_true",
        help=("benchmark-only paired probe: compare full captured audio to "
              "human-marked speech_end_ms crops (1–400 ms tail); does not "
              "change capture or recognition"))
    parser.add_argument("--output", help="JSON output path")
    args = parser.parse_args()
    result = run_benchmark(
        args.manifest, model_name=args.model, runs=args.runs,
        precision=args.precision, language=args.language,
        whisper_vad_min_silence_ms=args.whisper_vad_min_silence_ms,
        parakeet_tail_silence_ms=args.parakeet_tail_silence_ms,
        parakeet_recorded_tail_probe=args.parakeet_recorded_tail_probe)
    _print_summary(result)
    if args.output:
        output_path = os.path.abspath(args.output)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, ensure_ascii=False)
        print("\nSaved %s" % output_path)


if __name__ == "__main__":
    main()
