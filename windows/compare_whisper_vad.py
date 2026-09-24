"""Compare two private Windows Whisper VAD benchmark reports without echoing text.

This is a diagnostic, not a product-policy gate or a native dictation test.
Only the benchmark's minimum-silence threshold may differ between reports.
"""

import argparse
import json
import math
import re
import statistics


_WHISPER_MODELS = {"base.en", "small.en", "medium.en", "turbo"}
_BENCHMARK_VERSION = 25
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_SAME_REPORT_FIELDS = (
    "benchmark_inputs_sha256", "benchmark_order_sha256", "model",
    "model_snapshot", "requested_language", "precision", "model_dtype",
    "environment", "app_minimum_audio_duration_seconds",
    "parakeet_tail_silence_ms", "parakeet_recorded_tail_probe",
)
_SAME_SAMPLE_FIELDS = (
    "audio_seconds", "source_sample_rate", "runs", "reference_reviewed",
    "task_group", "language_group", "passes_app_minimum_audio_duration",
)


def _count(value, name, *, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError("invalid " + name)
    return value


def _timings(values, runs):
    if (not isinstance(values, list) or len(values) != runs or any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0 for value in values)):
        raise ValueError("invalid inference trials")
    return values


def _validate_sample(sample):
    if not isinstance(sample, dict):
        raise ValueError("invalid sample")
    runs = _count(sample.get("runs"), "sample run count", minimum=1)
    if not isinstance(sample.get("reference_reviewed"), bool):
        raise ValueError("invalid reference review status")
    if (isinstance(sample.get("audio_seconds"), bool)
            or not isinstance(sample.get("audio_seconds"), (int, float))
            or not math.isfinite(sample["audio_seconds"])
            or sample["audio_seconds"] <= 0):
        raise ValueError("invalid sample duration")
    _count(sample.get("source_sample_rate"), "source sample rate", minimum=1)
    if not isinstance(sample.get("passes_app_minimum_audio_duration"), bool):
        raise ValueError("invalid app duration gate result")
    for field in ("task_group", "language_group"):
        if sample.get(field) is not None and not isinstance(sample[field], str):
            raise ValueError("invalid sample group")
    inference = sample.get("inference_seconds")
    _timings(inference.get("all") if isinstance(inference, dict) else None, runs)
    vad = sample.get("speech_detection")
    if not isinstance(vad, dict):
        raise ValueError("missing Whisper VAD measurements")
    measured = _count(vad.get("measured_trials"), "measured VAD trials")
    missing = _count(vad.get("missing_trials"), "missing VAD trials")
    rejected = _count(vad.get("rejected_trials"), "rejected VAD trials")
    if (_count(vad.get("trials"), "VAD trial count") != runs
            or measured + missing != runs
            or rejected > measured or not isinstance(vad.get("all_seconds"), list)
            or len(vad["all_seconds"]) != measured):
        raise ValueError("inconsistent Whisper VAD trials")
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) or value < 0
           for value in vad["all_seconds"]):
        raise ValueError("invalid VAD retained duration")
    if rejected != sum(value == 0 for value in vad["all_seconds"]):
        raise ValueError("inconsistent VAD rejection count")

    accuracy = sample.get("accuracy")
    if accuracy is not None:
        if not sample["reference_reviewed"] or not isinstance(accuracy, dict):
            raise ValueError("invalid reviewed speech accuracy")
        reference_words = _count(
            accuracy.get("reference_words"), "reference words", minimum=1)
        trial = sample.get("trial_accuracy")
        if (not isinstance(trial, dict)
                or _count(trial.get("trials"), "speech trial count") != runs):
            raise ValueError("inconsistent reviewed speech trials")
        errors = trial.get("all_word_errors")
        if not isinstance(errors, list) or len(errors) != runs:
            raise ValueError("missing reviewed speech trial errors")
        for error in errors:
            _count(error, "word error count")
        if trial.get("worst_word_errors") != max(errors):
            raise ValueError("inconsistent worst word errors")
        deletion_runs = trial.get("all_max_reference_deletion_runs")
        if not isinstance(deletion_runs, list) or len(deletion_runs) != runs:
            raise ValueError("missing reviewed deletion-run trials")
        for value in deletion_runs:
            _count(value, "deletion run")
        if trial.get("worst_max_reference_deletion_run") != max(deletion_runs):
            raise ValueError("inconsistent worst deletion run")
        for edge in ("first_word", "final_word"):
            item = sample.get(edge)
            if (not isinstance(item, dict)
                    or _count(item.get("trials"), "edge-word trial count") != runs):
                raise ValueError("missing reviewed edge-word trials")
            if _count(item.get("failed_trials"), "edge-word failures") > runs:
                raise ValueError("inconsistent edge-word failures")
        if sample.get("silence") is not None or reference_words < 1:
            raise ValueError("speech cannot also be a silence control")
    silence = sample.get("silence")
    if silence is not None:
        if not isinstance(silence, dict) or not isinstance(
                silence.get("evaluated"), bool):
            raise ValueError("invalid silence review status")
        if _count(silence.get("trials"), "silence trial count") != runs:
            raise ValueError("inconsistent silence trials")
        if silence["evaluated"]:
            if not sample["reference_reviewed"]:
                raise ValueError("unreviewed silence cannot be scored")
            if _count(silence.get("false_positive_trials"),
                      "silence false positives") > runs:
                raise ValueError("inconsistent silence false positives")


def _validate_language_evidence(report, sample):
    """Do not mistake absent or inconsistent auto-language data for a pass."""
    runs = sample["runs"]
    detected = sample.get("detected_languages")
    if detected is not None:
        if not isinstance(detected, dict) or any(
                not isinstance(code, str)
                or not re.fullmatch(r"[a-z]{2,3}", code)
                or _count(count, "detected language count", minimum=1) > runs
                for code, count in detected.items()):
            raise ValueError("invalid detected language counts")
        if sum(detected.values()) > runs:
            raise ValueError("inconsistent detected language counts")

    label = sample.get("language_group")
    comparable = (report["model"] == "turbo"
                  and report["requested_language"] == "auto"
                  and sample.get("accuracy") is not None
                  and isinstance(label, str)
                  and re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", label))
    identification = sample.get("language_identification")
    if not comparable:
        if identification is not None:
            raise ValueError("unexpected language identification evidence")
        return
    if not isinstance(identification, dict):
        raise ValueError("missing reviewed language identification evidence")
    expected = label.split("-", 1)[0]
    if identification.get("expected_language") != expected:
        raise ValueError("inconsistent expected language")
    if _count(identification.get("trials"), "language trials") != runs:
        raise ValueError("inconsistent language trials")
    rejected = _count(identification.get("vad_rejected_trials"),
                      "language VAD rejections")
    observed = _count(identification.get("observed_trials"),
                      "observed language trials")
    matched = _count(identification.get("matched_trials"),
                     "matched language trials")
    mismatched = _count(identification.get("mismatched_trials"),
                        "mismatched language trials")
    missing = _count(identification.get("missing_trials"),
                     "missing language trials")
    if (rejected + observed + missing != runs
            or matched + mismatched != observed
            or rejected != sample["speech_detection"]["rejected_trials"]
            or observed != sum((detected or {}).values())
            or matched != (detected or {}).get(expected, 0)
            or not isinstance(identification.get("coverage_complete"), bool)
            or identification["coverage_complete"] != (rejected == 0 and missing == 0)):
        raise ValueError("inconsistent language identification evidence")


def _validate_report(report):
    if not isinstance(report, dict) or report.get("benchmark_version") != (
            _BENCHMARK_VERSION):
        raise ValueError("reports must use Windows benchmark version 25")
    if not isinstance(report.get("model"), str) or report["model"] not in (
            _WHISPER_MODELS):
        raise ValueError("only reviewed Windows Whisper models are supported")
    snapshot = report.get("model_snapshot")
    if (not isinstance(snapshot, dict)
            or not isinstance(snapshot.get("repository"), str)
            or not isinstance(snapshot.get("revision"), str)
            or not re.fullmatch(r"[0-9a-f]{40}", snapshot["revision"])):
        raise ValueError("missing reviewed model snapshot")
    if (not isinstance(report.get("requested_language"), str)
            or not isinstance(report.get("precision"), str)
            or not isinstance(report.get("model_dtype"), str)
            or not isinstance(report.get("environment"), dict)):
        raise ValueError("missing model or environment settings")
    minimum_audio = report.get("app_minimum_audio_duration_seconds")
    if (isinstance(minimum_audio, bool)
            or not isinstance(minimum_audio, (int, float))
            or not math.isfinite(minimum_audio) or minimum_audio <= 0):
        raise ValueError("missing app duration gate setting")
    if (report.get("parakeet_tail_silence_ms") is not None
            or report.get("parakeet_recorded_tail_probe") is not False):
        raise ValueError("Parakeet probe settings are not supported")
    for field in ("benchmark_inputs_sha256", "benchmark_order_sha256"):
        value = report.get(field)
        if not isinstance(value, str) or not _DIGEST.fullmatch(value):
            raise ValueError("missing benchmark input or order digest")
    policy = report.get("whisper_vad_policy")
    if not isinstance(policy, dict) or set(policy) != {
            "threshold", "neg_threshold", "min_speech_duration_ms",
            "min_silence_duration_ms", "speech_pad_ms"}:
        raise ValueError("missing effective Whisper VAD policy")
    for field in ("threshold", "neg_threshold"):
        value = policy[field]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not 0 <= value <= 1):
            raise ValueError("invalid VAD probability threshold")
    for field in ("min_speech_duration_ms", "speech_pad_ms"):
        _count(policy[field], "VAD duration")
    _count(policy.get("min_silence_duration_ms"), "VAD silence threshold")
    samples = report.get("samples")
    if (not isinstance(samples, list) or not samples
            or _count(report.get("sample_count"), "sample count", minimum=1)
            != len(samples)):
        raise ValueError("missing or inconsistent sample count")
    runs = samples[0].get("runs") if isinstance(samples[0], dict) else None
    for sample in samples:
        _validate_sample(sample)
        _validate_language_evidence(report, sample)
        if sample["runs"] != runs:
            raise ValueError("inconsistent report run counts")


def _sample_kind(sample):
    if sample.get("accuracy") is not None:
        return "speech"
    silence = sample.get("silence")
    if silence is not None and silence["evaluated"]:
        return "silence"
    return "unscored"


def _score(samples):
    speech = [item for item in samples if _sample_kind(item) == "speech"]
    silence = [item for item in samples if _sample_kind(item) == "silence"]
    language_ids = [item["language_identification"] for item in speech
                    if isinstance(item.get("language_identification"), dict)]
    words = sum(item["accuracy"]["reference_words"] * item["runs"]
                for item in speech)
    errors = sum(sum(item["trial_accuracy"]["all_word_errors"])
                 for item in speech)
    latency = [value for item in samples
               for value in item["inference_seconds"]["all"]]
    return {
        "sample_count": len(samples),
        "reviewed_speech_clips": len(speech),
        "reviewed_silence_clips": len(silence),
        "below_app_gate_clips": sum(
            not item["passes_app_minimum_audio_duration"] for item in samples),
        "trial_word_errors": errors if speech else None,
        "trial_reference_words": words if speech else None,
        "trial_wer": errors / words if words else None,
        # Count clean decodes without pretending trial positions in separate
        # benchmark invocations are controlled pairs.
        "error_free_trials": (sum(
            error == 0 for item in speech
            for error in item["trial_accuracy"]["all_word_errors"])
            if speech else None),
        "worst_deletion_run": max((item["trial_accuracy"][
            "worst_max_reference_deletion_run"] for item in speech), default=None),
        "first_word_failures": (sum(item["first_word"]["failed_trials"]
                                    for item in speech) if speech else None),
        "final_word_failures": (sum(item["final_word"]["failed_trials"]
                                    for item in speech) if speech else None),
        "vad_rejections": (sum(item["speech_detection"]["rejected_trials"]
                               for item in speech) if speech else None),
        "vad_missing": (sum(item["speech_detection"]["missing_trials"]
                            for item in speech) if speech else None),
        "language_id_clips": len(language_ids),
        "language_id_matched": sum(item["matched_trials"]
                                   for item in language_ids),
        "language_id_mismatched": sum(item["mismatched_trials"]
                                      for item in language_ids),
        "language_id_missing": sum(item["missing_trials"]
                                   for item in language_ids),
        "language_id_vad_rejected": sum(item["vad_rejected_trials"]
                                        for item in language_ids),
        "silence_false_positives": (sum(item["silence"][
            "false_positive_trials"] for item in silence) if silence else None),
        "inference_median_seconds": (
            statistics.median(latency) if latency else None),
    }


def _worsened(base, candidate):
    kind = _sample_kind(base)
    if kind == "speech":
        worsened = {
            "word_errors": sum(candidate["trial_accuracy"]["all_word_errors"])
            > sum(base["trial_accuracy"]["all_word_errors"]),
            "error_free_trials": sum(
                error == 0 for error in candidate["trial_accuracy"]["all_word_errors"]
            ) < sum(
                error == 0 for error in base["trial_accuracy"]["all_word_errors"]
            ),
            "worst_trial_errors": candidate["trial_accuracy"][
                "worst_word_errors"] > base["trial_accuracy"]["worst_word_errors"],
            "worst_deletion_run": candidate["trial_accuracy"][
                "worst_max_reference_deletion_run"] > base["trial_accuracy"][
                    "worst_max_reference_deletion_run"],
            "first_word": candidate["first_word"]["failed_trials"]
            > base["first_word"]["failed_trials"],
            "final_word": candidate["final_word"]["failed_trials"]
            > base["final_word"]["failed_trials"],
            "vad_rejections": candidate["speech_detection"]["rejected_trials"]
            > base["speech_detection"]["rejected_trials"],
            "vad_missing": candidate["speech_detection"]["missing_trials"]
            > base["speech_detection"]["missing_trials"],
        }
        if base.get("language_identification") is not None:
            before = base["language_identification"]
            after = candidate["language_identification"]
            worsened.update({
                "language_id_matched": after["matched_trials"]
                < before["matched_trials"],
                "language_id_mismatched": after["mismatched_trials"]
                > before["mismatched_trials"],
                "language_id_missing": after["missing_trials"]
                > before["missing_trials"],
            })
        return worsened
    if kind == "silence":
        return {"silence_false_positives": candidate["silence"][
            "false_positive_trials"] > base["silence"]["false_positive_trials"]}
    return {}


def _vad_retention_diagnostics(baseline, candidate, *, app_eligible_only=False):
    """Compare per-clip median VAD duration, not unpaired trial positions.

    A shorter retained duration on speech is not proof of a lost word, and a
    longer duration on silence is not proof of hallucination. Still, either
    direction warrants inspecting the private audio when assessing a policy.
    Partial VAD coverage cannot support a duration comparison.
    """
    result = {
        kind: {"lower": [], "higher": [], "unchanged": 0,
               "unmeasured": []}
        for kind in ("speech", "silence")
    }
    for position, (base, changed) in enumerate(zip(baseline, candidate), 1):
        if app_eligible_only and not base["passes_app_minimum_audio_duration"]:
            continue
        kind = _sample_kind(base)
        if kind not in result:
            continue
        before = base["speech_detection"]
        after = changed["speech_detection"]
        if before["missing_trials"] or after["missing_trials"]:
            result[kind]["unmeasured"].append(position)
            continue
        before_median = statistics.median(before["all_seconds"])
        after_median = statistics.median(after["all_seconds"])
        if after_median < before_median:
            result[kind]["lower"].append(position)
        elif after_median > before_median:
            result[kind]["higher"].append(position)
        else:
            result[kind]["unchanged"] += 1
    return result


def _regressed_strata(samples, regressions, *, app_eligible_only=False):
    """Count labelled strata without treating latency alone as a quality loss."""
    measured_regression_positions = {
        position for name, positions in regressions.items()
        if name != "inference_median_seconds" for position in positions
    }
    positioned = [
        (position, sample) for position, sample in enumerate(samples, 1)
        if not app_eligible_only or sample["passes_app_minimum_audio_duration"]
    ]
    strata = {}
    for field in ("task_group", "language_group", "language_task_group"):
        def label(sample):
            if field == "language_task_group":
                language = sample.get("language_group")
                task = sample.get("task_group")
                return (language, task) if language and task else None
            return sample.get(field)

        groups = {label(sample) for _, sample in positioned
                  if label(sample) is not None}
        regressed = sum(
            any(position in measured_regression_positions
                for position, sample in positioned if label(sample) == group)
            for group in groups
        )
        strata[field] = {"count": len(groups), "regressed": regressed}
    return strata


def compare_reports(baseline, candidate):
    """Reject non-paired runs and return content-free comparison diagnostics."""
    _validate_report(baseline)
    _validate_report(candidate)
    for field in _SAME_REPORT_FIELDS:
        if baseline.get(field) != candidate.get(field):
            raise ValueError("reports differ in " + field)
    base_policy = baseline["whisper_vad_policy"]
    candidate_policy = candidate["whisper_vad_policy"]
    if ({key: value for key, value in base_policy.items()
         if key != "min_silence_duration_ms"} !=
            {key: value for key, value in candidate_policy.items()
             if key != "min_silence_duration_ms"}):
        raise ValueError("VAD policy differs beyond minimum silence duration")
    if (base_policy["min_silence_duration_ms"] ==
            candidate_policy["min_silence_duration_ms"]):
        raise ValueError("VAD minimum silence duration did not change")
    if len(baseline["samples"]) != len(candidate["samples"]):
        raise ValueError("reports have different sample counts")

    regressions = {}
    for index, (base, changed) in enumerate(zip(
            baseline["samples"], candidate["samples"]), 1):
        for field in _SAME_SAMPLE_FIELDS:
            if base.get(field) != changed.get(field):
                raise ValueError("sample metadata changed at position %d" % index)
        if _sample_kind(base) != _sample_kind(changed):
            raise ValueError("review status changed at position %d" % index)
        if (base.get("accuracy") is not None and
                base["accuracy"]["reference_words"] != changed[
                    "accuracy"]["reference_words"]):
            raise ValueError("reference word count changed at position %d" % index)
        for name, worsened in _worsened(base, changed).items():
            if worsened:
                regressions.setdefault(name, []).append(index)
        if statistics.median(changed["inference_seconds"]["all"]) > (
                statistics.median(base["inference_seconds"]["all"])):
            regressions.setdefault("inference_median_seconds", []).append(index)

    base_score = _score(baseline["samples"])
    candidate_score = _score(candidate["samples"])
    if (not base_score["reviewed_speech_clips"]
            or not base_score["reviewed_silence_clips"]):
        raise ValueError("comparison needs reviewed speech and silence controls")
    # The benchmark intentionally allows clips the app discards before ASR.
    # Preserve their model-only diagnostics, but do not fold their errors or
    # silence false positives into the app-duration-gate-eligible comparison.
    eligible_baseline = [sample for sample in baseline["samples"]
                         if sample["passes_app_minimum_audio_duration"]]
    eligible_candidate = [sample for sample in candidate["samples"]
                          if sample["passes_app_minimum_audio_duration"]]
    eligible_positions = {
        position for position, sample in enumerate(baseline["samples"], 1)
        if sample["passes_app_minimum_audio_duration"]
    }
    eligible_regressions = {
        name: [position for position in positions
               if position in eligible_positions]
        for name, positions in regressions.items()
    }
    eligible_regressions = {
        name: positions for name, positions in eligible_regressions.items()
        if positions
    }
    return {
        "baseline_ms": base_policy["min_silence_duration_ms"],
        "candidate_ms": candidate_policy["min_silence_duration_ms"],
        "sample_count": len(baseline["samples"]),
        "baseline": base_score,
        "candidate": candidate_score,
        "regressions": regressions,
        "strata": _regressed_strata(baseline["samples"], regressions),
        "app_eligible": {
            "baseline": _score(eligible_baseline),
            "candidate": _score(eligible_candidate),
            "regressions": eligible_regressions,
            "strata": _regressed_strata(
                baseline["samples"], eligible_regressions,
                app_eligible_only=True),
            "vad_retention": _vad_retention_diagnostics(
                baseline["samples"], candidate["samples"],
                app_eligible_only=True),
        },
        "vad_retention": _vad_retention_diagnostics(
            baseline["samples"], candidate["samples"]),
    }


_PRINTED_SCORE_FIELDS = (
    "trial_word_errors", "trial_reference_words", "trial_wer",
    "error_free_trials",
    "worst_deletion_run", "first_word_failures", "final_word_failures",
    "vad_rejections", "vad_missing", "silence_false_positives",
    "inference_median_seconds",
)


def _print_scores(before, after, *, prefix=""):
    for key in _PRINTED_SCORE_FIELDS:
        old = before[key]
        new = after[key]
        if key == "inference_median_seconds" and old is not None:
            old, new = "%.3fs" % old, "%.3fs" % new
        elif key == "trial_wer" and old is not None:
            old, new = "%.2f%%" % (old * 100), "%.2f%%" % (new * 100)
        print("%s%s: %s -> %s" % (
            prefix, key, "not evaluated" if old is None else old,
            "not evaluated" if new is None else new))


def _print_regressions(regressions, strata, *, prefix=""):
    for key, indexes in sorted(regressions.items()):
        print("%s%s worsened: %d clips (positions %s)" % (
            prefix, key, len(indexes), ",".join(map(str, indexes))))
    for key, item in strata.items():
        print("%s%s strata with any measured quality or language-ID regression: %d/%d" % (
            prefix, key, item["regressed"], item["count"]))


def _print_vad_retention(diagnostics, *, prefix=""):
    for kind, counts in diagnostics.items():
        print("%sVAD-retained duration on reviewed %s: %d lower, %d higher, "
              "%d unchanged, %d unmeasured (median per clip)" % (
                  prefix, kind, len(counts["lower"]), len(counts["higher"]),
                  counts["unchanged"], len(counts["unmeasured"])))
        for direction in ("lower", "higher", "unmeasured"):
            if counts[direction]:
                print("  %s positions: %s" % (
                    direction, ",".join(map(str, counts[direction]))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", help="private baseline benchmark JSON")
    parser.add_argument("candidate", help="private candidate benchmark JSON")
    args = parser.parse_args()
    try:
        with open(args.baseline, encoding="utf-8") as handle:
            baseline = json.load(handle)
        with open(args.candidate, encoding="utf-8") as handle:
            candidate = json.load(handle)
        result = compare_reports(baseline, candidate)
    except (OSError, UnicodeError, json.JSONDecodeError):
        parser.exit(2, "comparison unavailable: could not read benchmark JSON\n")
    except ValueError as exc:
        parser.exit(2, "comparison unavailable: %s\n" % exc)

    print("Whisper VAD %d -> %d ms; %d paired clips (diagnostic only)" % (
        result["baseline_ms"], result["candidate_ms"], result["sample_count"]))
    print("Reviewed: %d speech clips, %d silence controls" % (
        result["baseline"]["reviewed_speech_clips"],
        result["baseline"]["reviewed_silence_clips"]))
    if result["baseline"]["below_app_gate_clips"]:
        print("Model-only clips below the app duration gate: %d; included in "
              "the all-clips scores below, not product delivery evidence" % (
                  result["baseline"]["below_app_gate_clips"]))
    _print_scores(result["baseline"], result["candidate"])
    if result["baseline"]["language_id_clips"]:
        print("Reviewed automatic language identification: %d clips" % (
            result["baseline"]["language_id_clips"]))
        for key in ("language_id_matched", "language_id_mismatched",
                    "language_id_missing", "language_id_vad_rejected"):
            print("%s: %d -> %d" % (
                key, result["baseline"][key], result["candidate"][key]))
    elif baseline["model"] == "turbo" and baseline["requested_language"] == "auto":
        print("Automatic language identification not evaluated: no reviewed "
              "speech clips with comparable language labels")
    _print_vad_retention(result["vad_retention"])
    _print_regressions(result["regressions"], result["strata"])
    if result["baseline"]["below_app_gate_clips"]:
        eligible = result["app_eligible"]
        before = eligible["baseline"]
        after = eligible["candidate"]
        print("App-duration-gate-eligible subset: %d clips; %d reviewed speech, "
              "%d reviewed silence (still model-only, not native delivery)" % (
                  before["sample_count"], before["reviewed_speech_clips"],
                  before["reviewed_silence_clips"]))
        if not before["reviewed_speech_clips"] or not before["reviewed_silence_clips"]:
            print("App-eligible quality comparison incomplete: reviewed speech "
                  "and silence controls must both pass the duration gate")
        _print_scores(before, after, prefix="app_eligible_")
        if before["language_id_clips"]:
            for key in ("language_id_matched", "language_id_mismatched",
                        "language_id_missing", "language_id_vad_rejected"):
                print("app_eligible_%s: %d -> %d" % (
                    key, before[key], after[key]))
        elif baseline["model"] == "turbo" and baseline["requested_language"] == "auto":
            print("App-eligible automatic language identification not evaluated: "
                  "no reviewed eligible speech clips with comparable labels")
        _print_vad_retention(eligible["vad_retention"],
                             prefix="App-eligible ")
        _print_regressions(eligible["regressions"], eligible["strata"],
                           prefix="App-eligible ")
    print("VAD-retained duration is not acoustic speech recall or a quality "
          "verdict. Not a pass/fail result; review individual private reports, "
          "audio, thermal load, and native dictation before a policy change.")


if __name__ == "__main__":
    main()
