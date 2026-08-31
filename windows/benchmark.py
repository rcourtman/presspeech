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

import numpy as np
import soundfile as sf

import app
import config as cfg
import engine


WORD_RE = re.compile(r"[\w']+", re.UNICODE)


def _normalise_words(text):
    text = text.lower().replace("\u2019", "'")
    return WORD_RE.findall(text)


def _normalise_chars(text):
    text = text.lower().replace("\u2019", "'")
    return " ".join(text.split())


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
    return {
        "word_errors": edit_distance(ref_words, hyp_words),
        "reference_words": len(ref_words),
        "wer": (edit_distance(ref_words, hyp_words) / len(ref_words)
                if ref_words else None),
        "character_errors": edit_distance(ref_chars, hyp_chars),
        "reference_characters": len(ref_chars),
        "cer": (edit_distance(ref_chars, hyp_chars) / len(ref_chars)
                if ref_chars else None),
        "exact_match": _normalise_chars(reference) == _normalise_chars(hypothesis),
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


def load_audio(path):
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = np.asarray(audio, dtype=np.float32)
    original_seconds = len(audio) / float(sample_rate)
    if sample_rate != 16000:
        audio = app._resample_to_16k(audio, sample_rate)
    return audio, original_seconds, sample_rate


def _sync_cuda():
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.synchronize()
    except Exception:
        pass


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


def run_benchmark(manifest_path, model_name=None, runs=None, precision="auto"):
    manifest_path = os.path.abspath(manifest_path)
    manifest_dir = os.path.dirname(manifest_path)
    with open(manifest_path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    model_name = model_name or manifest.get("model") or cfg.DEFAULTS["model"]
    runs = runs or int(manifest.get("runs", 3))
    if runs < 1:
        raise ValueError("runs must be at least 1")

    transcriber = engine.Transcriber()
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
    for sample in manifest.get("samples", []):
        audio_path = sample["audio"]
        if not os.path.isabs(audio_path):
            audio_path = os.path.join(manifest_dir, audio_path)
        audio, audio_seconds, source_rate = load_audio(audio_path)
        timings = []
        transcripts = []
        for _run in range(runs):
            _sync_cuda()
            started = time.perf_counter()
            transcript = transcriber.transcribe(audio, language="en")
            _sync_cuda()
            timings.append(time.perf_counter() - started)
            transcripts.append(transcript)
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
            "reference_reviewed": bool(sample.get("reference_reviewed", False)),
        }
        result["silence"] = silence_metrics(
            bool(sample.get("expected_silence", False)),
            result["reference_reviewed"],
            transcripts,
        )
        reference = sample.get("reference", "")
        if reference and result["reference_reviewed"]:
            result["reference"] = reference
            result["accuracy"] = accuracy_metrics(reference, consensus)
        else:
            result["accuracy"] = None
            result["accuracy_note"] = "Reference transcript requires human review."
        sample_results.append(result)

    reviewed = [item for item in sample_results if item["accuracy"] is not None]
    total_words = sum(item["accuracy"]["reference_words"] for item in reviewed)
    total_word_errors = sum(item["accuracy"]["word_errors"] for item in reviewed)
    reviewed_silence = [
        item for item in sample_results
        if item["silence"] is not None and item["silence"]["evaluated"]
    ]
    model_dtype = str(getattr(transcriber.model, "dtype", "unknown"))
    cuda_allocated_mib = None
    try:
        import torch
        if torch.cuda.is_available():
            cuda_allocated_mib = torch.cuda.memory_allocated() / 1024 / 1024
    except Exception:
        pass
    return {
        "benchmark_version": 1,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "model": model_name,
        "precision": precision,
        "model_dtype": model_dtype,
        "cuda_allocated_mib": cuda_allocated_mib,
        "environment": _environment(),
        "load_seconds": load_seconds,
        "warmup_seconds": warmup_seconds,
        "sample_count": len(sample_results),
        "reviewed_sample_count": len(reviewed),
        "aggregate_wer": total_word_errors / total_words if total_words else None,
        "reviewed_silence_sample_count": len(reviewed_silence),
        "silence_false_positive_count": sum(
            item["silence"]["false_positive"] for item in reviewed_silence),
        "reviewed_silence_trial_count": sum(
            item["silence"]["trials"] for item in reviewed_silence),
        "silence_false_positive_trial_count": sum(
            item["silence"]["false_positive_trials"]
            for item in reviewed_silence),
        "samples": sample_results,
    }


def _print_summary(result):
    print("Model: %s | precision: %s (%s)" %
          (result["model"], result["precision"], result["model_dtype"]))
    if result["cuda_allocated_mib"] is not None:
        print("CUDA tensors: %.1f MiB" % result["cuda_allocated_mib"])
    print("Load: %.3fs | warm-up: %.3fs" %
          (result["load_seconds"], result["warmup_seconds"]))
    for sample in result["samples"]:
        timing = sample["inference_seconds"]
        print("\n%s: %.3fs median (%.1fx realtime, adaptive/max release-to-paste %.3f/%.3fs)" % (
            sample["id"], timing["median"], sample["realtime_speedup"],
            sample["estimated_adaptive_release_to_paste_seconds"],
            sample["estimated_release_to_paste_seconds"],
        ))
        print("  %s" % sample["transcript"])
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
            print("  Accuracy: pending reviewed reference")
        else:
            print("  WER: %.2f%% | CER: %.2f%%" % (
                sample["accuracy"]["wer"] * 100,
                sample["accuracy"]["cer"] * 100,
            ))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=os.path.join("benchmarks", "manifest.json"))
    parser.add_argument("--model")
    parser.add_argument("--runs", type=int)
    parser.add_argument(
        "--precision", choices=("auto", "tf32", "fp16", "bf16"), default="auto")
    parser.add_argument("--output", help="JSON output path")
    args = parser.parse_args()
    result = run_benchmark(
        args.manifest, model_name=args.model, runs=args.runs, precision=args.precision)
    _print_summary(result)
    if args.output:
        output_path = os.path.abspath(args.output)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, ensure_ascii=False)
        print("\nSaved %s" % output_path)


if __name__ == "__main__":
    main()
