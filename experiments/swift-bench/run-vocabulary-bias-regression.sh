#!/usr/bin/env bash
# Compare production Parakeet v3 with unbiased and vocabulary-rescored
# sliding-window v3 policies on the same multilingual dictation fixtures.

set -euo pipefail

# Metrics are parsed and emitted as dot-decimal machine-readable values.
export LC_ALL=C

SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$SCRIPT_PATH")"
REPO_ROOT="$(cd ../.. && pwd)"

INPUT_DIR="real-audio"
NEGATIVE_CONTROL_DIR=""
CROSS_LANGUAGE_CONTROL_DIR=""
OUTDIR="vocabulary-results"
VOCABULARY=""
CRITICAL_TERMS=""
LANGUAGE="auto"
CROSS_LANGUAGE_CONTROL_LANGUAGE=""
TRIALS="3"
REFERENCES_HAND_AUDITED=0
REDACT_TRANSCRIPTS=1
REDACT_PATHS=1
REQUIRE_CANDIDATE_PASS=1
MIN_CRITICAL_HIT_GAIN="1"
MAX_UNEXPECTED_INSERTION_DELTA="0"
MAX_WER_REGRESSION_POINTS="0.00"
MAX_CRITICAL_REGRESSED_CLIPS="0"
MAX_WER_REGRESSED_CLIPS="0"
MAX_UNEXPECTED_REGRESSED_CLIPS="0"
MAX_PRODUCTION_LATENCY_RATIO="2.00"
# The only real-dictation vocabulary result so far used 40 target clips,
# 1,295 reference words, and 68 critical-term occurrences. Do not let a much
# smaller, hand-picked success case clear the product-candidate screen.
MIN_TARGET_CLIPS="25"
MIN_TARGET_REFERENCE_WORDS="1000"
MIN_TARGET_CRITICAL_OCCURRENCES="50"
# Over-fire can be sparse and vocabulary-specific. Require both varied
# dictation boundaries and enough ordinary speech before a zero-insertion
# result is allowed to support a product candidate.
MIN_NEGATIVE_CONTROL_CLIPS="10"
MIN_NEGATIVE_CONTROL_REFERENCE_WORDS="1000"
SELF_TEST=0
BENCH_EXECUTABLE=".build/release/presspeech-bench"

BENCHMARK_SOURCE_PATHS=(
    "experiments/swift-bench/Package.swift"
    "experiments/swift-bench/Package.resolved"
    "experiments/swift-bench/Sources/presspeech-bench"
    "experiments/swift-bench/run-vocabulary-bias-regression.sh"
)

usage() {
    cat <<'USAGE'
usage: ./run-vocabulary-bias-regression.sh --vocabulary <path> --critical-terms <path> [options]

Options:
  --input-dir <path>       audio + same-stem .txt references (default: real-audio)
  --negative-control-dir <path>
                           same-language, same-workflow audio + references in
                           which no critical term occurs; required by the screen
  --cross-language-control-dir <path>
                           optional additional audio + references in another
                           language in which no critical term occurs
  --out-dir <path>         ignored report directory (default: vocabulary-results)
  --vocabulary <path>      FluidAudio text or JSON custom vocabulary (required)
  --critical-terms <path>  canonical surface forms, one per line (required)
  --language <auto|code>   Parakeet v3 language/script hint (default: auto)
  --cross-language-control-language <auto|code>
                           language hint for cross-language controls (required
                           with --cross-language-control-dir)
  --trials <n>             measured trials per clip/variant (default: 3)
  --references-hand-audited
                           confirm every reference was checked against its
                           audio by a human; required by the candidate screen
  --show-transcripts       include references and hypotheses in raw logs
  --show-paths             include fixture and configuration paths in reports
  --no-threshold           write the report but do not fail when no policy
                           clears the product-candidate screen
  --self-test              run parser, aggregation, and redaction tests only
  -h, --help               show this help

The nine variants run in separate processes so memory measurements stay
isolated:
  v3             production AsrManager path
  v3-vocab       production v3 plus auxiliary CTC rescoring
  v3-vocab-conservative
                  v3-vocab with short-term taper and similarity floors
  v3-vocab-no-rescue
                  v3-vocab with acoustic-only spotter rescue disabled
  v3-vocab-exact-similarity
                  v3-vocab candidate evidence, applying only legacy-selected
                  replacements with exact normalized scorer similarity
  sliding-v3     SlidingWindowAsrManager without vocabulary boosting
  sliding-vocab  the same sliding path plus the auxiliary CTC rescorer
  sliding-vocab-conservative
                  the same rescorer with FluidAudio's recommended short-term
                  taper (pivot 5) and spotter similarity floors (0.30/0.50)
  sliding-vocab-no-rescue
                  the same rescorer with acoustic-only spotter rescue disabled

Critical-term recall, precision, and unexpected insertions are exact after
case/punctuation normalization. List every canonical vocabulary form, including
forms absent from some clips, and list inflections separately; FluidAudio aliases
are alternate matches, not morphological generators. Duplicate normalized forms
are rejected rather than double-counted. After FluidAudio parses and sanitizes
the text or JSON vocabulary, its canonical forms must exactly match the critical
terms under scoring normalization. Unscored vocabulary terms and unrelated
critical terms are rejected so neither false insertions nor gains can be hidden.

The product-candidate screen compares each direct-v3 vocabulary policy with
production v3; sliding-window lanes remain mechanism diagnostics. It requires
human-audited references, complete comparable clips, at least one net
critical-term hit,
no per-clip critical-hit loss, no aggregate or per-clip increase in unexpected
insertions or WER, at least 10 same-language negative-control clips containing
at least 1,000 reference words in total, and average p50 latency no more than
2x production. The target corpus must contain at least 25 clips, 1,000
reference words, and 50 critical-term occurrences. Passing
is necessary evidence for product evaluation, not approval to ship. Thresholded
runs also require a clean Git checkout so a shared report identifies the exact
reviewable benchmark source; use --no-threshold for locally modified experiments.
Same-language negative controls always use the target --language hint. Optional
cross-language controls require their own explicit language hint and supplement,
rather than satisfy, the same-language requirement. All control references must
contain none of the critical terms under the benchmark's exact normalization.
Every target and control audio file must be distinct both as supplied and after
normalization to the benchmark's 16 kHz mono WAV format. Exact renamed,
rewrapped, or losslessly converted copies do not provide independent evidence
and are rejected.
Target IDs begin with `p`, same-language control IDs with `n`, and cross-language
control IDs with `x`.
USAGE
}

need_value() {
    if [[ $# -lt 2 || -z "$2" || "$2" == --* ]]; then
        echo "$1 requires a value" >&2
        usage >&2
        exit 2
    fi
}

path_label() {
    if [[ "$REDACT_PATHS" -eq 1 ]]; then
        printf '<redacted path>'
    else
        printf '%s' "$1"
    fi
}

benchmark_source_revision() {
    git -C "$REPO_ROOT" rev-parse --verify HEAD 2>/dev/null || true
}

benchmark_source_state() {
    if ! git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        printf 'unavailable'
        return
    fi
    if [[ -n "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all -- "${BENCHMARK_SOURCE_PATHS[@]}")" ]]; then
        printf 'modified'
    else
        printf 'clean'
    fi
}

fluid_audio_revision() {
    local package_file="${1:-Package.swift}"
    sed -nE 's/.*revision: "([0-9a-f]{40})".*/\1/p' "$package_file" | head -n 1
}

macos_deployment_target() {
    local package_file="$1"
    sed -nE 's/.*\.macOS\("([0-9]+\.[0-9]+)"\).*/\1/p' "$package_file" | head -n 1
}

file_sha256() {
    shasum -a 256 "$1" | awk '{print $1}'
}

fixture_set_sha256() {
    local clip
    local ref
    for clip in "$@"; do
        ref="${clip%.*}.txt"
        # Hash the paired contents, not their private names. Sorting the pair
        # digests makes a copied or renamed frozen corpus retain its identity.
        printf '%s\t%s\n' "$(file_sha256 "$clip")" "$(file_sha256 "$ref")"
    done | sort | shasum -a 256 | awk '{print $1}'
}

duplicate_content_digest() {
    local clip
    {
        for clip in "$@"; do
            file_sha256 "$clip"
        done
    } | sort | uniq -d | head -n 1
}

validate_unique_source_audio_content() {
    local duplicate_digest
    duplicate_digest="$(duplicate_content_digest "$@")"
    if [[ -n "$duplicate_digest" ]]; then
        echo "benchmark corpora contain byte-identical source audio files" >&2
        echo "each target and control clip must be an independent recording or segment" >&2
        return 1
    fi
}

validate_unique_normalized_audio_content() {
    local duplicate_digest
    duplicate_digest="$(duplicate_content_digest "$@")"
    if [[ -n "$duplicate_digest" ]]; then
        echo "benchmark corpora contain audio files that normalize to byte-identical 16 kHz mono WAV" >&2
        echo "rewrapping or losslessly converting one recording does not make an independent control" >&2
        return 1
    fi
}

benchmark_inputs_sha256() {
    local target_fixture_digest="$1"
    local negative_fixture_digest="$2"
    local cross_language_fixture_digest="$3"
    local vocabulary="$4"
    local critical_terms="$5"
    local language="$6"
    local cross_language_control_language="$7"
    local trials="$8"
    local references_hand_audited="$9"
    printf 'target-fixture-set\t%s\nnegative-control-fixture-set\t%s\ncross-language-control-fixture-set\t%s\nvocabulary\t%s\ncritical-terms\t%s\nlanguage\t%s\ncross-language-control-language\t%s\ntrials\t%s\nreferences-hand-audited\t%s\n' \
        "$target_fixture_digest" \
        "$negative_fixture_digest" \
        "$cross_language_fixture_digest" \
        "$(file_sha256 "$vocabulary")" \
        "$(file_sha256 "$critical_terms")" \
        "$language" \
        "$cross_language_control_language" \
        "$trials" \
        "$references_hand_audited" \
        | shasum -a 256 | awk '{print $1}'
}

validate_reference_audit_claim() {
    if [[ "$REQUIRE_CANDIDATE_PASS" -eq 1 && "$REFERENCES_HAND_AUDITED" -ne 1 ]]; then
        echo "thresholded vocabulary runs require human-audited reference transcripts" >&2
        echo "listen to every clip and correct its sidecar, then pass --references-hand-audited" >&2
        echo "use --no-threshold for exploratory runs with machine-generated or unaudited references" >&2
        return 1
    fi
}

clip_id_for() {
    local group="$1"
    local index="$2"
    local stem="$3"
    if [[ "$REDACT_PATHS" -eq 1 ]]; then
        printf '%s%03d' "$group" "$index"
    else
        printf '%s%03d-%s' "$group" "$index" "$stem" | tr -c '[:alnum:]_.-' '-'
    fi
}

extract_worst_wer_metrics() {
    local log_file="$1"
    sed -nE 's/.*\[WER ([0-9.]+)%\].*\[word-errors=([0-9]+) reference-words=([0-9]+)\].*/\1\t\2\t\3/p' "$log_file" \
        | awk -F '\t' '
            # Printed WER is rounded to one decimal. Select by the exact
            # fraction so two distinct trial outcomes that share a display
            # value cannot hide the true worst result.
            {
                numerator = $2
                denominator = $3
                # Match WordErrorScore.percent for an empty reference.
                if (denominator == 0) {
                    numerator = numerator == 0 ? 0 : 1
                    denominator = 1
                }
                if (!seen || numerator * worst_denominator > worst_numerator * denominator) {
                    worst = $1
                    errors = $2
                    words = $3
                    worst_numerator = numerator
                    worst_denominator = denominator
                    seen = 1
                }
            }
            END {
                if (seen) printf("%s\t%s\t%s\n", worst, errors, words)
                else print "unknown\tunknown\tunknown"
            }
        '
}

extract_critical_metrics() {
    local log_file="$1"
    sed -nE 's/.*critical-terms matched=([0-9]+) total=([0-9]+) recall=([0-9.]+)% unexpected=([0-9]+).*/\1\t\2\t\3\t\4/p' "$log_file" \
        | awk -F '\t' '
            {
                matched_ratio = $1
                total_ratio = $2
                # Match CriticalTermScore.recallPercent when the reference
                # contains no critical terms. Compare the exact fraction:
                # printed recall is rounded to one decimal and distinct trial
                # outcomes can otherwise tie at the display boundary.
                if (total_ratio == 0) {
                    matched_ratio = 1
                    total_ratio = 1
                }
                if (!seen || matched_ratio * lowest_total_ratio < lowest_matched_ratio * total_ratio) {
                    matched = $1
                    total = $2
                    lowest_recall = $3
                    lowest_matched_ratio = matched_ratio
                    lowest_total_ratio = total_ratio
                }
                if (!seen || $4 > highest_unexpected) highest_unexpected = $4
                seen = 1
            }
            END {
                if (seen) {
                    printf("%s\t%s\t%s\t%s\n", matched, total, lowest_recall, highest_unexpected)
                }
            }
        '
}

critical_precision_percent() {
    local matched="$1"
    local unexpected="$2"
    awk -v matched="$matched" -v unexpected="$unexpected" 'BEGIN {
        predicted = matched + unexpected
        if (predicted == 0) print "100.0"
        else printf "%.1f\n", matched / predicted * 100
    }'
}

extract_p50_ms() {
    local log_file="$1"
    sed -nE 's/.*latency:[[:space:]]+p50=[[:space:]]*([0-9.]+) ms.*/\1/p' "$log_file" | head -n 1
}

extract_peak_mb() {
    local log_file="$1"
    sed -nE 's/.*memory:[[:space:]]+peak=[[:space:]]*([0-9.]+) MB.*/\1/p' "$log_file" | head -n 1
}

extract_cache_mb() {
    local log_file="$1"
    sed -nE 's/.*model-cache: total=([0-9.]+) MB.*/\1/p' "$log_file" | head -n 1
}

extract_prepare_ms() {
    local log_file="$1"
    sed -nE 's/.*ready in[[:space:]]+([0-9.]+) ms.*/\1/p' "$log_file" | head -n 1
}

validate_metrics() {
    local name
    local value
    local missing=()
    while [[ $# -gt 0 ]]; do
        name="$1"
        value="$2"
        shift 2
        if [[ -z "$value" || "$value" == "unknown" ]]; then
            missing+=( "$name" )
        fi
    done
    if [[ "${#missing[@]}" -gt 0 ]]; then
        printf 'benchmark output missing required metrics: %s\n' "$(IFS=,; echo "${missing[*]}")" >&2
        return 1
    fi
}

validate_negative_control_reference() {
    local clip_id="$1"
    local critical_total="$2"
    if [[ "$critical_total" -ne 0 ]]; then
        echo "negative-control reference contains $critical_total critical-term occurrence(s) for clip $clip_id" >&2
        echo "negative controls must contain none of the configured critical terms" >&2
        return 1
    fi
}

parse_reference_metrics() {
    local output="$1"
    if [[ "$output" =~ ^reference-metrics\ reference-words=([0-9]+)\ critical-occurrences=([0-9]+)$ ]]; then
        printf '%s\t%s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
        return 0
    fi
    echo "invalid privacy-safe reference metrics output" >&2
    return 1
}

preflight_reference_corpus() {
    local group="$1"
    shift
    local clips=( "$@" )
    local words=0
    local occurrences=0
    local index=0
    local clip ref output metrics clip_words clip_occurrences clip_id stem
    for clip in "${clips[@]}"; do
        index=$((index + 1))
        ref="${clip%.*}.txt"
        if ! output="$("$BENCH_EXECUTABLE" \
            --reference-metrics \
            --reference-file "$ref" \
            --critical-terms "$CRITICAL_TERMS")"; then
            echo "reference preflight failed for ${group}$(printf '%03d' "$index")" >&2
            return 1
        fi
        if ! metrics="$(parse_reference_metrics "$output")"; then
            echo "reference preflight failed for ${group}$(printf '%03d' "$index")" >&2
            return 1
        fi
        IFS=$'\t' read -r clip_words clip_occurrences <<<"$metrics"
        words=$((words + clip_words))
        occurrences=$((occurrences + clip_occurrences))
        if [[ "$group" == "n" || "$group" == "x" ]] && \
            [[ "$clip_occurrences" -ne 0 ]]; then
            stem="$(basename "$clip")"
            stem="${stem%.*}"
            clip_id="$(clip_id_for "$group" "$index" "$stem")"
            echo "negative-control reference contains $clip_occurrences critical-term occurrence(s) for clip $clip_id" >&2
            return 1
        fi
    done
    printf '%s\t%s\n' "$words" "$occurrences"
}

publish_report_artifacts() {
    local stage_dir="$1"
    local staged_report="$2"
    local staged_tsv="$3"
    local staged_raw_dir="$4"
    local final_report="$5"
    local final_tsv="$6"
    local final_raw_dir="$7"

    if [[ ! -f "$staged_report" || ! -f "$staged_tsv" || ! -d "$staged_raw_dir" ]]; then
        echo "vocabulary-bias staging artifacts are incomplete" >&2
        return 1
    fi
    if [[ -e "$final_report" || -e "$final_tsv" || -e "$final_raw_dir" ]]; then
        echo "refusing to replace existing vocabulary-bias artifacts" >&2
        return 1
    fi

    # Publish the human-facing report last. If an unexpected move fails, roll
    # back only paths that this function created so no partial run looks final.
    local moved_raw=0
    local moved_tsv=0
    if ! mv "$staged_raw_dir" "$final_raw_dir"; then
        return 1
    fi
    moved_raw=1
    if ! mv "$staged_tsv" "$final_tsv"; then
        rm -rf -- "$final_raw_dir"
        return 1
    fi
    moved_tsv=1
    if ! mv "$staged_report" "$final_report"; then
        [[ "$moved_tsv" -eq 0 ]] || rm -f -- "$final_tsv"
        [[ "$moved_raw" -eq 0 ]] || rm -rf -- "$final_raw_dir"
        return 1
    fi
    rmdir "$stage_dir"
}

run_benchmark_to_log() {
    local log_file="$1"
    shift
    if [[ "$REDACT_TRANSCRIPTS" -eq 1 ]]; then
        # FluidAudio release builds suppress debug transcript lines, but its
        # vocabulary loader can emit warning text containing a term (Polish
        # diacritics intentionally trigger that warning). Filter the pipe
        # before it reaches disk so the default raw logs are genuinely
        # content-free rather than redacted after the fact.
        "$@" 2>&1 | sed -E \
            -e '/\[FluidAudio\.CustomVocabulary\]/s/] .*/] <redacted vocabulary diagnostic>/' \
            -e '/\[FluidAudio\.VocabularyRescorer/s/] .*/] <redacted vocabulary diagnostic>/' \
            -e '/\[FluidAudio\.SlidingWindowASR\].*Chunk [0-9]+:/s/(Chunk [0-9]+: ).*(, time:)/\1<redacted transcript>\2/' \
            -e '/\[FluidAudio\.SlidingWindowASR\].*(CONFIRMED|VOLATILE)/s/(] (CONFIRMED|VOLATILE)).*/\1 <redacted transcript state>/' \
            >"$log_file"
    else
        "$@" >"$log_file" 2>&1
    fi
}

summary_row() {
    local tsv="$1"
    local variant="$2"
    awk -F '\t' -v variant="$variant" '
        NR > 1 && $2 == variant {
            count += 1
            if ($3 != "unknown") {
                if (wer_seen == 0 || $3 > worst_wer) worst_wer = $3
                wer_seen += 1
            }
            if ($12 != "unknown" && $13 != "unknown") {
                wer_errors += $12
                reference_words += $13
            }
            if ($4 != "unknown" && $5 != "unknown") {
                critical_matched += $4
                critical_total += $5
            }
            if ($7 != "unknown") { critical_unexpected += $7 }
            if ($8 != "unknown") { p50_sum += $8; p50_seen += 1 }
            if ($9 != "unknown" && (peak_seen == 0 || $9 > max_peak)) {
                max_peak = $9; peak_seen += 1
            }
            if ($10 != "unknown" && (cache_seen == 0 || $10 > max_cache)) {
                max_cache = $10; cache_seen += 1
            }
            if ($11 != "unknown") { prep_sum += $11; prep_seen += 1 }
        }
        END {
            corpus_wer = reference_words ? sprintf("%.2f", wer_errors / reference_words * 100) : "unknown"
            worst = wer_seen ? sprintf("%.1f", worst_wer) : "unknown"
            critical = critical_total ? sprintf("%.1f", critical_matched / critical_total * 100) : "unknown"
            critical_predictions = critical_matched + critical_unexpected
            precision = critical_predictions ? sprintf("%.1f", critical_matched / critical_predictions * 100) : "100.0"
            avg_p50 = p50_seen ? sprintf("%.1f", p50_sum / p50_seen) : "unknown"
            peak = peak_seen ? sprintf("%.1f", max_peak) : "unknown"
            cache = cache_seen ? sprintf("%.1f", max_cache) : "unknown"
            prep = prep_seen ? sprintf("%.1f", prep_sum / prep_seen) : "unknown"
            printf("| `%s` | %d | %s | %s | %d/%d | %s | %s | %d | %s | %s | %s | %s |\n", variant, count, corpus_wer, worst, critical_matched, critical_total, critical, precision, critical_unexpected, avg_p50, peak, cache, prep)
        }
    ' "$tsv"
}

comparison_row() {
    local tsv="$1"
    local baseline="$2"
    local candidate="$3"
    awk -F '\t' -v baseline="$baseline" -v candidate="$candidate" '
        NR > 1 && $2 == baseline {
            baseline_wer[$1] = $3
            baseline_hits[$1] = $4
            baseline_unexpected[$1] = $7
            baseline_errors[$1] = $12
            baseline_words[$1] = $13
        }
        NR > 1 && $2 == candidate {
            candidate_wer[$1] = $3
            candidate_hits[$1] = $4
            candidate_unexpected[$1] = $7
            candidate_errors[$1] = $12
            candidate_words[$1] = $13
        }
        END {
            for (clip in candidate_wer) {
                if (!(clip in baseline_wer) ||
                    baseline_wer[clip] == "unknown" || candidate_wer[clip] == "unknown" ||
                    baseline_hits[clip] == "unknown" || candidate_hits[clip] == "unknown" ||
                    baseline_unexpected[clip] == "unknown" || candidate_unexpected[clip] == "unknown" ||
                    baseline_errors[clip] == "unknown" || candidate_errors[clip] == "unknown" ||
                    baseline_words[clip] == "unknown" || candidate_words[clip] == "unknown" ||
                    baseline_words[clip] != candidate_words[clip]) {
                    continue
                }
                comparable += 1
                hit_delta = candidate_hits[clip] - baseline_hits[clip]
                unexpected_delta += candidate_unexpected[clip] - baseline_unexpected[clip]
                total_hit_delta += hit_delta
                error_delta = candidate_errors[clip] - baseline_errors[clip]
                total_error_delta += error_delta
                total_reference_words += baseline_words[clip]

                # These categories mirror the per-clip review that exposed
                # the original vocabulary policy recall/quality tradeoff. Use
                # exact edit counts: displayed WER is rounded to one decimal
                # and can otherwise turn a small real regression into a tie.
                if (hit_delta > 0 && error_delta <= 0) clean_wins += 1
                else if (hit_delta > 0 && error_delta > 0) costly_wins += 1
                else if (hit_delta <= 0 && error_delta > 0) pure_losses += 1
                else other += 1
            }
            corpus_wer_delta = total_reference_words ? sprintf("%+.2f", total_error_delta / total_reference_words * 100) : "unknown"
            printf("| `%s` | %d | %+d | %+d | %s | %d | %d | %d | %d |\n", candidate, comparable, total_hit_delta, unexpected_delta, corpus_wer_delta, clean_wins, costly_wins, pure_losses, other)
        }
    ' "$tsv"
}

candidate_assessment() {
    local tsv="$1"
    local baseline="$2"
    local candidate="$3"
    awk -F '\t' \
        -v baseline="$baseline" \
        -v candidate="$candidate" \
        -v min_hit_gain="$MIN_CRITICAL_HIT_GAIN" \
        -v max_unexpected_delta="$MAX_UNEXPECTED_INSERTION_DELTA" \
        -v max_wer_regression="$MAX_WER_REGRESSION_POINTS" \
        -v max_critical_regressed_clips="$MAX_CRITICAL_REGRESSED_CLIPS" \
        -v max_wer_regressed_clips="$MAX_WER_REGRESSED_CLIPS" \
        -v max_unexpected_regressed_clips="$MAX_UNEXPECTED_REGRESSED_CLIPS" \
        -v max_latency_ratio="$MAX_PRODUCTION_LATENCY_RATIO" \
        -v min_target_clips="$MIN_TARGET_CLIPS" \
        -v min_target_words="$MIN_TARGET_REFERENCE_WORDS" \
        -v min_target_occurrences="$MIN_TARGET_CRITICAL_OCCURRENCES" \
        -v min_negative_controls="$MIN_NEGATIVE_CONTROL_CLIPS" \
        -v min_negative_words="$MIN_NEGATIVE_CONTROL_REFERENCE_WORDS" '
        function add_blocker(message) {
            blockers = blockers (blockers == "" ? "" : "; ") message
        }
        NR > 1 && $2 == baseline {
            baseline_count += 1
            if ($1 ~ /^p[0-9]+/) {
                baseline_target_clips += 1
                baseline_target_words += $13
                baseline_target_occurrences += $5
            }
            if ($1 ~ /^n[0-9]+/) {
                baseline_negative_controls += 1
                baseline_negative_words += $13
            }
            baseline_hits[$1] = $4
            baseline_total[$1] = $5
            baseline_unexpected[$1] = $7
            baseline_latency[$1] = $8
            baseline_errors[$1] = $12
            baseline_words[$1] = $13
        }
        NR > 1 && $2 == candidate {
            candidate_count += 1
            candidate_hits[$1] = $4
            candidate_total[$1] = $5
            candidate_unexpected[$1] = $7
            candidate_latency[$1] = $8
            candidate_errors[$1] = $12
            candidate_words[$1] = $13
        }
        END {
            for (clip in candidate_hits) {
                if (!(clip in baseline_hits) ||
                    baseline_hits[clip] == "unknown" || candidate_hits[clip] == "unknown" ||
                    baseline_total[clip] == "unknown" || candidate_total[clip] == "unknown" ||
                    baseline_total[clip] != candidate_total[clip] ||
                    baseline_unexpected[clip] == "unknown" || candidate_unexpected[clip] == "unknown" ||
                    baseline_latency[clip] == "unknown" || candidate_latency[clip] == "unknown" ||
                    baseline_errors[clip] == "unknown" || candidate_errors[clip] == "unknown" ||
                    baseline_words[clip] == "unknown" || candidate_words[clip] == "unknown" ||
                    baseline_words[clip] != candidate_words[clip] || baseline_latency[clip] <= 0) {
                    continue
                }
                comparable += 1
                hit_delta = candidate_hits[clip] - baseline_hits[clip]
                total_hit_delta += hit_delta
                if (hit_delta < 0) critical_regressed_clips += 1
                clip_unexpected_delta = candidate_unexpected[clip] - baseline_unexpected[clip]
                unexpected_delta += clip_unexpected_delta
                error_delta = candidate_errors[clip] - baseline_errors[clip]
                total_error_delta += error_delta
                total_reference_words += baseline_words[clip]
                baseline_latency_sum += baseline_latency[clip]
                candidate_latency_sum += candidate_latency[clip]
                if (clip_unexpected_delta > 0) unexpected_regressed_clips += 1
                if (error_delta > 0) wer_regressed_clips += 1
            }

            complete = baseline_count > 0 && candidate_count == baseline_count && comparable == baseline_count
            wer_delta = total_reference_words ? total_error_delta / total_reference_words * 100 : 0
            latency_ratio = comparable ? candidate_latency_sum / baseline_latency_sum : 0

            if (!complete) add_blocker("incomplete comparable clips")
            if (baseline_target_clips < min_target_clips) add_blocker("target clips below " min_target_clips)
            if (baseline_target_words < min_target_words) add_blocker("target reference words below " min_target_words)
            if (baseline_target_occurrences < min_target_occurrences) add_blocker("target critical-term occurrences below " min_target_occurrences)
            if (baseline_negative_controls < min_negative_controls) add_blocker("same-language negative-control clips below " min_negative_controls)
            if (baseline_negative_words < min_negative_words) add_blocker("same-language negative-control reference words below " min_negative_words)
            if (total_hit_delta < min_hit_gain) add_blocker("critical-hit gain below +" min_hit_gain)
            if (critical_regressed_clips > max_critical_regressed_clips) add_blocker("per-clip critical-term recall regressed")
            if (unexpected_delta > max_unexpected_delta) add_blocker("unexpected insertions increased")
            if (!total_reference_words || wer_delta > max_wer_regression + 0.0000001) add_blocker("corpus WER regressed")
            if (unexpected_regressed_clips > max_unexpected_regressed_clips) add_blocker("per-clip unexpected insertions increased")
            if (wer_regressed_clips > max_wer_regressed_clips) add_blocker("per-clip WER regressions present")
            if (!comparable || latency_ratio > max_latency_ratio + 0.0000001) add_blocker("latency exceeded " max_latency_ratio "x production")

            verdict = blockers == "" ? "passes" : "blocked"
            wer_display = total_reference_words ? sprintf("%+.2f", wer_delta) : "unknown"
            latency_display = comparable ? sprintf("%.2f", latency_ratio) : "unknown"
            printf("%s\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%+d\t%+d\t%s\t%d\t%d\t%d\t%s\t%s\t%s\n",
                candidate, comparable, baseline_count, baseline_target_clips,
                baseline_target_words, baseline_target_occurrences,
                baseline_negative_controls, baseline_negative_words, total_hit_delta,
                unexpected_delta, wer_display, critical_regressed_clips,
                unexpected_regressed_clips, wer_regressed_clips, latency_display,
                verdict, blockers)
        }
    ' "$tsv"
}

candidate_assessment_row() {
    local assessment="$1"
    local candidate comparable baseline_count target_clips target_words target_occurrences
    local negative_controls negative_words hit_delta unexpected_delta
    local wer_delta critical_regressed unexpected_regressed wer_regressed latency_ratio verdict blockers
    IFS=$'\t' read -r candidate comparable baseline_count target_clips target_words \
        target_occurrences negative_controls negative_words hit_delta unexpected_delta \
        wer_delta critical_regressed unexpected_regressed wer_regressed \
        latency_ratio verdict blockers <<<"$assessment"
    printf '| `%s` | %s/%s | %s / %s / %s | %s / %s | %s | %s | %s | %s | %s | %s | %sx | **%s** | %s |\n' \
        "$candidate" "$comparable" "$baseline_count" "$target_clips" "$target_words" \
        "$target_occurrences" "$negative_controls" "$negative_words" \
        "$hit_delta" "$unexpected_delta" "$wer_delta" "$critical_regressed" "$unexpected_regressed" \
        "$wer_regressed" "$latency_ratio" \
        "$verdict" "${blockers:---}"
}

assert_eq() {
    local actual="$1"
    local expected="$2"
    local label="$3"
    if [[ "$actual" != "$expected" ]]; then
        echo "self-test failed for $label: expected '$expected', got '$actual'" >&2
        exit 1
    fi
}

assert_contains() {
    local file="$1"
    local needle="$2"
    if ! grep -Fq -- "$needle" "$file"; then
        echo "self-test expected file to contain: $needle" >&2
        exit 1
    fi
}

assert_not_contains() {
    local file="$1"
    local needle="$2"
    if grep -Fq -- "$needle" "$file"; then
        echo "self-test found private value in report: $needle" >&2
        exit 1
    fi
}

run_self_test() {
    local tmpdir
    tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/presspeech-vocabulary-self-test.XXXXXX")"
    local quoted_tmpdir
    printf -v quoted_tmpdir '%q' "$tmpdir"
    # Expand now so cleanup retains the path after this function's locals leave scope.
    # shellcheck disable=SC2064
    trap "rm -rf -- $quoted_tmpdir" EXIT INT TERM

    local log="$tmpdir/mock.log"
    {
        echo '  ready in 1234.5 ms (model load + 1 warmup inference)'
        echo '  model-cache: total=812.3 MB components=parakeet-v3=600.0 MB,ctc-110m=212.3 MB'
        echo '    latency:  p50=  140.2 ms  min=  138.0 ms  max=  145.0 ms'
        echo '    memory:   peak=  88.4 MB  Δ-from-start=  80.0 MB'
        echo '    transcript: [WER 12.5%] [critical-terms matched=7 total=8 recall=87.5% unexpected=2] [word-errors=1 reference-words=8] <redacted 42 chars>'
    } >"$log"
    assert_eq "$(extract_worst_wer_metrics "$log")" $'12.5\t1\t8' "WER parser"
    assert_eq "$(extract_critical_metrics "$log")" $'7\t8\t87.5\t2' "critical-term parser"
    assert_eq "$(critical_precision_percent 7 2)" "77.8" "critical-term precision"
    assert_eq "$(critical_precision_percent 0 0)" "100.0" "empty critical-term precision"
    assert_eq "$(extract_p50_ms "$log")" "140.2" "latency parser"
    assert_eq "$(extract_peak_mb "$log")" "88.4" "memory parser"
    assert_eq "$(extract_cache_mb "$log")" "812.3" "cache parser"
    assert_eq "$(extract_prepare_ms "$log")" "1234.5" "prepare parser"
    validate_metrics \
        wer 12.5 word-errors 1 reference-words 8 \
        critical-matched 7 critical-total 8 critical-recall 87.5 \
        critical-unexpected 2 p50 140.2 peak 88.4 cache 812.3 prepare 1234.5
    validate_negative_control_reference n001 0
    local negative_validation_log="$tmpdir/negative-validation.log"
    if validate_negative_control_reference n002 1 >"$negative_validation_log" 2>&1; then
        echo "self-test expected a contaminated negative control to fail" >&2
        exit 1
    fi
    assert_contains "$negative_validation_log" "negative-control reference contains 1 critical-term occurrence(s) for clip n002"
    assert_eq "$(parse_reference_metrics 'reference-metrics reference-words=1295 critical-occurrences=68')" \
        $'1295\t68' "reference preflight parser"
    local invalid_reference_metrics_log="$tmpdir/invalid-reference-metrics.log"
    if parse_reference_metrics 'reference: private transcript' >"$invalid_reference_metrics_log" 2>&1; then
        echo "self-test expected transcript-bearing reference metrics to fail" >&2
        exit 1
    fi
    assert_contains "$invalid_reference_metrics_log" "invalid privacy-safe reference metrics output"

    local mock_bench="$tmpdir/mock-presspeech-bench"
    cat >"$mock_bench" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
reference=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --reference-metrics) shift ;;
        --reference-file) reference="$2"; shift 2 ;;
        --critical-terms) shift 2 ;;
        *) exit 2 ;;
    esac
done
case "$(cat "$reference")" in
    target-one) echo 'reference-metrics reference-words=600 critical-occurrences=25' ;;
    target-two) echo 'reference-metrics reference-words=695 critical-occurrences=43' ;;
    clean-control) echo 'reference-metrics reference-words=1000 critical-occurrences=0' ;;
    contaminated-control) echo 'reference-metrics reference-words=100 critical-occurrences=1' ;;
    *) exit 1 ;;
esac
MOCK
    chmod +x "$mock_bench"
    local original_bench_executable="$BENCH_EXECUTABLE"
    BENCH_EXECUTABLE="$mock_bench"
    local preflight_fixtures="$tmpdir/preflight-fixtures"
    mkdir "$preflight_fixtures"
    printf 'target-one\n' >"$preflight_fixtures/private-positive-a.txt"
    printf 'target-two\n' >"$preflight_fixtures/private-positive-b.txt"
    printf 'clean-control\n' >"$preflight_fixtures/private-control-a.txt"
    printf 'contaminated-control\n' >"$preflight_fixtures/private-control-b.txt"
    assert_eq "$(preflight_reference_corpus p \
        "$preflight_fixtures/private-positive-a.wav" \
        "$preflight_fixtures/private-positive-b.wav")" \
        $'1295\t68' "target reference preflight aggregation"
    assert_eq "$(preflight_reference_corpus n \
        "$preflight_fixtures/private-control-a.wav")" \
        $'1000\t0' "negative-control reference preflight aggregation"
    local contaminated_preflight_log="$tmpdir/contaminated-preflight.log"
    if preflight_reference_corpus n \
        "$preflight_fixtures/private-control-b.wav" \
        >"$contaminated_preflight_log" 2>&1; then
        echo "self-test expected preflight to reject a contaminated control" >&2
        exit 1
    fi
    assert_contains "$contaminated_preflight_log" \
        "negative-control reference contains 1 critical-term occurrence(s) for clip n001"
    assert_not_contains "$contaminated_preflight_log" "private-control-b"
    BENCH_EXECUTABLE="$original_bench_executable"

    local original_threshold="$REQUIRE_CANDIDATE_PASS"
    local original_reference_audit="$REFERENCES_HAND_AUDITED"
    REQUIRE_CANDIDATE_PASS=1
    REFERENCES_HAND_AUDITED=0
    local audit_validation_log="$tmpdir/reference-audit-validation.log"
    if validate_reference_audit_claim >"$audit_validation_log" 2>&1; then
        echo "self-test expected unaudited references to fail the candidate screen" >&2
        exit 1
    fi
    assert_contains "$audit_validation_log" "require human-audited reference transcripts"
    REFERENCES_HAND_AUDITED=1
    validate_reference_audit_claim
    REQUIRE_CANDIDATE_PASS=0
    REFERENCES_HAND_AUDITED=0
    validate_reference_audit_claim
    REQUIRE_CANDIDATE_PASS="$original_threshold"
    REFERENCES_HAND_AUDITED="$original_reference_audit"

    local validation_log="$tmpdir/validation.log"
    if validate_metrics wer unknown p50 "" >"$validation_log" 2>&1; then
        echo "self-test expected missing metrics to fail validation" >&2
        exit 1
    fi
    assert_contains "$validation_log" "benchmark output missing required metrics: wer,p50"

    local stage_dir="$tmpdir/staged"
    local final_dir="$tmpdir/published"
    mkdir -p "$stage_dir/raw" "$final_dir"
    printf 'complete report\n' >"$stage_dir/report.md"
    printf 'header\nrow\n' >"$stage_dir/results.tsv"
    printf 'bench output\n' >"$stage_dir/raw/clip-v3.bench.txt"
    publish_report_artifacts \
        "$stage_dir" \
        "$stage_dir/report.md" \
        "$stage_dir/results.tsv" \
        "$stage_dir/raw" \
        "$final_dir/report.md" \
        "$final_dir/results.tsv" \
        "$final_dir/raw"
    assert_contains "$final_dir/report.md" "complete report"
    assert_contains "$final_dir/results.tsv" "row"
    assert_contains "$final_dir/raw/clip-v3.bench.txt" "bench output"
    [[ ! -e "$stage_dir" ]] || {
        echo "self-test expected successful report staging cleanup" >&2
        exit 1
    }

    stage_dir="$tmpdir/collision-stage"
    mkdir -p "$stage_dir/raw"
    printf 'new report\n' >"$stage_dir/report.md"
    printf 'new results\n' >"$stage_dir/results.tsv"
    printf 'new log\n' >"$stage_dir/raw/log.txt"
    local collision_log="$tmpdir/collision.log"
    if publish_report_artifacts \
        "$stage_dir" \
        "$stage_dir/report.md" \
        "$stage_dir/results.tsv" \
        "$stage_dir/raw" \
        "$final_dir/report.md" \
        "$final_dir/other-results.tsv" \
        "$final_dir/other-raw" >"$collision_log" 2>&1; then
        echo "self-test expected report publication collision to fail" >&2
        exit 1
    fi
    assert_contains "$collision_log" "refusing to replace existing vocabulary-bias artifacts"
    assert_contains "$final_dir/report.md" "complete report"
    assert_contains "$stage_dir/report.md" "new report"

    local variable_log="$tmpdir/variable.log"
    {
        echo '    transcripts (2 distinct):'
        echo '      • [WER 20.0%] [critical-terms matched=1 total=2 recall=50.0% unexpected=0] [word-errors=2 reference-words=10] <redacted 20 chars>'
        echo '      • [WER 10.0%] [critical-terms matched=2 total=2 recall=100.0% unexpected=3] [word-errors=1 reference-words=10] <redacted 22 chars>'
    } >"$variable_log"
    assert_eq "$(extract_critical_metrics "$variable_log")" $'1\t2\t50.0\t3' "variable-output critical-term envelope"
    assert_eq "$(extract_worst_wer_metrics "$variable_log")" $'20.0\t2\t10' "variable-output worst WER"

    local rounded_recall_log="$tmpdir/rounded-recall.log"
    {
        echo '      • [critical-terms matched=1998 total=2000 recall=99.9% unexpected=0] <redacted 20 chars>'
        echo '      • [critical-terms matched=1997 total=2000 recall=99.9% unexpected=1] <redacted 22 chars>'
    } >"$rounded_recall_log"
    assert_eq "$(extract_critical_metrics "$rounded_recall_log")" $'1997\t2000\t99.9\t1' "rounded recall exact worst-trial selection"

    local rounded_wer_log="$tmpdir/rounded-wer.log"
    {
        echo '      • [WER 0.1%] [word-errors=1 reference-words=2000] <redacted 20 chars>'
        echo '      • [WER 0.1%] [word-errors=2 reference-words=2000] <redacted 22 chars>'
    } >"$rounded_wer_log"
    assert_eq "$(extract_worst_wer_metrics "$rounded_wer_log")" $'0.1\t2\t2000' "rounded WER exact worst-trial selection"

    local filtered_log="$tmpdir/filtered.log"
    run_benchmark_to_log "$filtered_log" printf '%s\n' \
        "[10:00:00.000] [WARN] [FluidAudio.CustomVocabulary] Term 'Szypański': contains diacritics"
    assert_not_contains "$filtered_log" "Szypański"
    assert_contains "$filtered_log" "redacted vocabulary diagnostic"

    local tsv="$tmpdir/results.tsv"
    {
        printf 'clip_id\tvariant\twer_percent\tcritical_matched\tcritical_total\tcritical_recall_percent\tcritical_unexpected\tp50_ms\tpeak_mb\tcache_mb\tprepare_ms\tword_errors\treference_words\tcritical_precision_percent\n'
        printf 'p001\tv3\t10.0\t1\t2\t50.0\t2\t100.0\t40.0\t600.0\t1000.0\t1\t10\t33.3\n'
        printf 'p002\tv3\t20.0\t2\t2\t100.0\t1\t120.0\t42.0\t600.0\t1100.0\t1\t5\t66.7\n'
        printf 'p001\tsliding-v3\t10.0\t1\t2\t50.0\t0\t100.0\t40.0\t600.0\t1000.0\t1\t10\t100.0\n'
        printf 'p002\tsliding-v3\t20.0\t1\t2\t50.0\t1\t100.0\t40.0\t600.0\t1000.0\t1\t5\t50.0\n'
        printf 'p003\tsliding-v3\t5.0\t1\t1\t100.0\t0\t100.0\t40.0\t600.0\t1000.0\t1\t20\t100.0\n'
        printf 'n004\tsliding-v3\t0.1\t0\t0\t100.0\t0\t100.0\t40.0\t600.0\t1000.0\t1\t2000\t100.0\n'
        printf 'p001\tsliding-vocab\t10.0\t2\t2\t100.0\t0\t100.0\t40.0\t600.0\t1000.0\t1\t10\t100.0\n'
        printf 'p002\tsliding-vocab\t40.0\t2\t2\t100.0\t2\t100.0\t40.0\t600.0\t1000.0\t2\t5\t50.0\n'
        printf 'p003\tsliding-vocab\t10.0\t1\t1\t100.0\t1\t100.0\t40.0\t600.0\t1000.0\t2\t20\t50.0\n'
        # The displayed WER ties after rounding, but the exact count exposes
        # one additional error and must classify this clip as a pure loss.
        printf 'n004\tsliding-vocab\t0.1\t0\t0\t100.0\t0\t100.0\t40.0\t600.0\t1000.0\t2\t2000\t100.0\n'
    } >"$tsv"
    local summary="$tmpdir/summary.md"
    summary_row "$tsv" v3 >"$summary"
    assert_contains "$summary" '| `v3` | 2 | 13.33 | 20.0 | 3/4 | 75.0 | 50.0 | 3 | 110.0 | 42.0 | 600.0 | 1050.0 |'
    comparison_row "$tsv" sliding-v3 sliding-vocab >"$summary"
    assert_contains "$summary" '| `sliding-vocab` | 4 | +2 | +2 | +0.15 | 1 | 1 | 2 | 0 |'

    local blocked_assessment
    blocked_assessment="$(candidate_assessment "$tsv" sliding-v3 sliding-vocab)"
    assert_contains <(printf '%s\n' "$blocked_assessment") $'sliding-vocab\t4\t4\t3\t35\t5\t1\t2000\t+2\t+2\t+0.15\t0\t2\t3\t1.00\tblocked\ttarget clips below 25; target reference words below 1000; target critical-term occurrences below 50; same-language negative-control clips below 10; unexpected insertions increased; corpus WER regressed; per-clip unexpected insertions increased; per-clip WER regressions present'

    {
        head -n 1 "$tsv"
        awk -F '\t' 'NR > 1 && $2 == "sliding-v3"' "$tsv"
        for ((index = 4; index <= 25; index += 1)); do
            critical_total=2
            [[ "$index" -ne 4 ]] || critical_total=3
            printf 'p%03d\tsliding-v3\t0.0\t0\t%d\t0.0\t0\t100.0\t40.0\t600.0\t1000.0\t0\t50\t100.0\n' \
                "$index" "$critical_total"
        done
        for ((index = 5; index <= 13; index += 1)); do
            printf 'n%03d\tsliding-v3\t0.0\t0\t0\t100.0\t0\t100.0\t40.0\t600.0\t1000.0\t0\t0\t100.0\n' "$index"
        done
        printf 'p001\tsliding-vocab-no-rescue\t0.0\t2\t2\t100.0\t0\t150.0\t40.0\t700.0\t1000.0\t0\t10\t100.0\n'
        printf 'p002\tsliding-vocab-no-rescue\t0.0\t2\t2\t100.0\t1\t150.0\t40.0\t700.0\t1000.0\t0\t5\t66.7\n'
        printf 'p003\tsliding-vocab-no-rescue\t0.0\t1\t1\t100.0\t0\t150.0\t40.0\t700.0\t1000.0\t0\t20\t100.0\n'
        for ((index = 4; index <= 25; index += 1)); do
            critical_total=2
            [[ "$index" -ne 4 ]] || critical_total=3
            printf 'p%03d\tsliding-vocab-no-rescue\t0.0\t0\t%d\t0.0\t0\t150.0\t40.0\t700.0\t1000.0\t0\t50\t100.0\n' \
                "$index" "$critical_total"
        done
        printf 'n004\tsliding-vocab-no-rescue\t0.0\t0\t0\t100.0\t0\t150.0\t40.0\t700.0\t1000.0\t0\t2000\t100.0\n'
        for ((index = 5; index <= 13; index += 1)); do
            printf 'n%03d\tsliding-vocab-no-rescue\t0.0\t0\t0\t100.0\t0\t150.0\t40.0\t700.0\t1000.0\t0\t0\t100.0\n' "$index"
        done
    } >"$tmpdir/passing.tsv"
    local passing_assessment
    passing_assessment="$(candidate_assessment "$tmpdir/passing.tsv" sliding-v3 sliding-vocab-no-rescue)"
    assert_eq "$passing_assessment" $'sliding-vocab-no-rescue\t35\t35\t25\t1135\t50\t10\t2000\t+2\t+0\t-0.13\t0\t0\t0\t1.50\tpasses\t' "passing candidate assessment"
    candidate_assessment_row "$passing_assessment" >"$summary"
    assert_contains "$summary" '| `sliding-vocab-no-rescue` | 35/35 | 25 / 1135 / 50 | 10 / 2000 | +2 | +0 | -0.13 | 0 | 0 | 0 | 1.50x | **passes** | -- |'

    sed 's/^n/0/' "$tmpdir/passing.tsv" >"$tmpdir/no-negative-controls.tsv"
    local no_negative_assessment
    no_negative_assessment="$(candidate_assessment "$tmpdir/no-negative-controls.tsv" sliding-v3 sliding-vocab-no-rescue)"
    assert_contains <(printf '%s\n' "$no_negative_assessment") $'blocked\tsame-language negative-control clips below 10; same-language negative-control reference words below 1000'

    sed 's/^n/x/' "$tmpdir/passing.tsv" >"$tmpdir/cross-language-only.tsv"
    local cross_language_only_assessment
    cross_language_only_assessment="$(candidate_assessment "$tmpdir/cross-language-only.tsv" sliding-v3 sliding-vocab-no-rescue)"
    assert_contains <(printf '%s\n' "$cross_language_only_assessment") $'blocked\tsame-language negative-control clips below 10; same-language negative-control reference words below 1000'

    sed $'s/\t2000\t100.0$/\t999\t100.0/' \
        "$tmpdir/passing.tsv" >"$tmpdir/insufficient-negative-words.tsv"
    local insufficient_negative_words_assessment
    insufficient_negative_words_assessment="$(candidate_assessment "$tmpdir/insufficient-negative-words.tsv" sliding-v3 sliding-vocab-no-rescue)"
    assert_contains <(printf '%s\n' "$insufficient_negative_words_assessment") \
        $'blocked\tsame-language negative-control reference words below 1000'

    # Aggregate improvements must not hide a costly per-clip vocabulary win or
    # an insertion that happens to be offset on another clip.
    sed -e $'s/^p001\tsliding-vocab-no-rescue\t0.0\t2\t2\t100.0\t0\t150.0\t40.0\t700.0\t1000.0\t0\t10\t100.0$/p001\tsliding-vocab-no-rescue\t20.0\t2\t2\t100.0\t1\t150.0\t40.0\t700.0\t1000.0\t2\t10\t66.7/' \
        -e $'s/^p002\tsliding-vocab-no-rescue\t0.0\t2\t2\t100.0\t1\t150.0\t40.0\t700.0\t1000.0\t0\t5\t66.7$/p002\tsliding-vocab-no-rescue\t0.0\t2\t2\t100.0\t0\t150.0\t40.0\t700.0\t1000.0\t0\t5\t100.0/' \
        "$tmpdir/passing.tsv" >"$tmpdir/masked-regressions.tsv"
    local masked_assessment
    masked_assessment="$(candidate_assessment "$tmpdir/masked-regressions.tsv" sliding-v3 sliding-vocab-no-rescue)"
    assert_eq "$masked_assessment" $'sliding-vocab-no-rescue\t35\t35\t25\t1135\t50\t10\t2000\t+2\t+0\t-0.06\t0\t1\t1\t1.50\tblocked\tper-clip unexpected insertions increased; per-clip WER regressions present' "masked per-clip regressions"

    # Net recall gains must not hide a vocabulary term lost on another clip.
    sed -e $'s/^p001\tsliding-vocab-no-rescue\t0.0\t2\t2\t100.0/p001\tsliding-vocab-no-rescue\t0.0\t0\t2\t0.0/' \
        -e $'s/^p003\tsliding-v3\t5.0\t1\t1\t100.0/p003\tsliding-v3\t5.0\t0\t1\t0.0/' \
        "$tmpdir/passing.tsv" >"$tmpdir/masked-critical-regression.tsv"
    local masked_critical_assessment
    masked_critical_assessment="$(candidate_assessment "$tmpdir/masked-critical-regression.tsv" sliding-v3 sliding-vocab-no-rescue)"
    assert_eq "$masked_critical_assessment" $'sliding-vocab-no-rescue\t35\t35\t25\t1135\t50\t10\t2000\t+1\t+0\t-0.13\t1\t0\t0\t1.50\tblocked\tper-clip critical-term recall regressed' "masked per-clip critical-term regression"

    local secret_path="$tmpdir/Private Polish Benchmark"
    REDACT_PATHS=1
    {
        echo "Input: $(path_label "$secret_path")"
        echo "Vocabulary: $(path_label "$secret_path/vocab.txt")"
    } >"$summary"
    assert_not_contains "$summary" "Private Polish Benchmark"

    local missing_value_log="$tmpdir/missing-value.log"
    if bash "$SCRIPT_PATH" --vocabulary >"$missing_value_log" 2>&1; then
        echo "self-test expected --vocabulary without a value to fail" >&2
        exit 1
    fi
    assert_contains "$missing_value_log" "--vocabulary requires a value"

    local package_file="$tmpdir/Package.swift"
    {
        printf '%s\n' '.macOS("14.0"),'
        printf '%s\n' '.package(url: "https://example.invalid/FluidAudio.git", revision: "0123456789abcdef0123456789abcdef01234567")'
    } >"$package_file"
    assert_eq "$(fluid_audio_revision "$package_file")" \
        "0123456789abcdef0123456789abcdef01234567" "FluidAudio revision parser"
    assert_eq "$(macos_deployment_target "$package_file")" "14.0" \
        "macOS deployment target parser"
    assert_eq \
        "$(macos_deployment_target "$REPO_ROOT/experiments/swift-bench/Package.swift")" \
        "$(macos_deployment_target "$REPO_ROOT/swift/Package.swift")" \
        "vocabulary benchmark matches the product macOS floor"
    printf 'benchmark artifact\n' >"$tmpdir/artifact"
    assert_eq "$(file_sha256 "$tmpdir/artifact")" \
        "add96b142ed74d852093d6f139dc83383b18c53840cea5761e4e93353ee5f836" \
        "benchmark artifact digest"

    local fixtures="$tmpdir/Private fixtures"
    mkdir "$fixtures"
    printf 'audio one\n' >"$fixtures/first.wav"
    printf 'reference one\n' >"$fixtures/first.txt"
    printf 'audio two\n' >"$fixtures/second.wav"
    printf 'reference two\n' >"$fixtures/second.txt"
    printf 'Szypański\n' >"$fixtures/vocabulary.txt"
    printf 'Szypański\n' >"$fixtures/critical-terms.txt"
    validate_unique_source_audio_content "$fixtures/first.wav" "$fixtures/second.wav"
    cp "$fixtures/first.wav" "$fixtures/copied.wav"
    if validate_unique_source_audio_content \
        "$fixtures/first.wav" "$fixtures/second.wav" "$fixtures/copied.wav" \
        >/dev/null 2>&1; then
        echo "self-test expected renamed audio copies to be rejected" >&2
        exit 1
    fi
    rm "$fixtures/copied.wav"
    mkdir "$fixtures/normalized"
    printf 'normalized audio one\n' >"$fixtures/normalized/first.wav"
    printf 'normalized audio two\n' >"$fixtures/normalized/second.wav"
    validate_unique_normalized_audio_content \
        "$fixtures/normalized/first.wav" "$fixtures/normalized/second.wav"
    cp "$fixtures/normalized/first.wav" "$fixtures/normalized/rewrapped.wav"
    if validate_unique_normalized_audio_content \
        "$fixtures/normalized/first.wav" \
        "$fixtures/normalized/second.wav" \
        "$fixtures/normalized/rewrapped.wav" >"$tmpdir/normalized-duplicate.log" 2>&1; then
        echo "self-test expected normalized audio copies to be rejected" >&2
        exit 1
    fi
    assert_contains "$tmpdir/normalized-duplicate.log" \
        "rewrapping or losslessly converting one recording does not make an independent control"
    local fixture_digest
    fixture_digest="$(fixture_set_sha256 \
        "$fixtures/first.wav" "$fixtures/second.wav")"
    assert_eq "$fixture_digest" \
        "513130324aab465f3aa0b7db6455a134198bb0c233b4065573f5be48a99d8773" \
        "fixture-set digest"
    mv "$fixtures/first.wav" "$fixtures/renamed.wav"
    mv "$fixtures/first.txt" "$fixtures/renamed.txt"
    assert_eq "$(fixture_set_sha256 \
        "$fixtures/second.wav" "$fixtures/renamed.wav")" \
        "$fixture_digest" "fixture digest ignores names and ordering"
    assert_eq "$(benchmark_inputs_sha256 \
        "$fixture_digest" \
        "none" \
        "none" \
        "$fixtures/vocabulary.txt" \
        "$fixtures/critical-terms.txt" \
        "pl" \
        "" \
        "3" \
        "1")" \
        "e961f18f0ea9c68b315bdcf7b0d2d02e63450507f4391ba6e4d7c133add798e9" \
        "complete benchmark-input digest"
    local first_fixture_digest
    local second_fixture_digest
    first_fixture_digest="$(fixture_set_sha256 "$fixtures/renamed.wav")"
    second_fixture_digest="$(fixture_set_sha256 "$fixtures/second.wav")"
    if [[ "$(benchmark_inputs_sha256 \
        "$first_fixture_digest" "$second_fixture_digest" "none" \
        "$fixtures/vocabulary.txt" "$fixtures/critical-terms.txt" "pl" "" "3" "1")" == \
        "$(benchmark_inputs_sha256 \
        "$second_fixture_digest" "$first_fixture_digest" "none" \
        "$fixtures/vocabulary.txt" "$fixtures/critical-terms.txt" "pl" "" "3" "1")" ]]; then
        echo "self-test expected target/control assignment to affect provenance" >&2
        exit 1
    fi
    if [[ "$(benchmark_inputs_sha256 \
        "$fixture_digest" "$first_fixture_digest" "$second_fixture_digest" \
        "$fixtures/vocabulary.txt" "$fixtures/critical-terms.txt" "pl" "en" "3" "1")" == \
        "$(benchmark_inputs_sha256 \
        "$fixture_digest" "$second_fixture_digest" "$first_fixture_digest" \
        "$fixtures/vocabulary.txt" "$fixtures/critical-terms.txt" "pl" "en" "3" "1")" ]]; then
        echo "self-test expected same/cross-language control assignment to affect provenance" >&2
        exit 1
    fi
    local benchmark_configuration_digest
    benchmark_configuration_digest="$(benchmark_inputs_sha256 \
        "$fixture_digest" "$first_fixture_digest" "$second_fixture_digest" \
        "$fixtures/vocabulary.txt" "$fixtures/critical-terms.txt" "pl" "en" "3" "1")"
    if [[ "$(benchmark_inputs_sha256 \
        "$fixture_digest" "$first_fixture_digest" "$second_fixture_digest" \
        "$fixtures/vocabulary.txt" "$fixtures/critical-terms.txt" "auto" "en" "3" "1")" == \
        "$benchmark_configuration_digest" ]]; then
        echo "self-test expected target language changes to alter provenance" >&2
        exit 1
    fi
    if [[ "$(benchmark_inputs_sha256 \
        "$fixture_digest" "$first_fixture_digest" "$second_fixture_digest" \
        "$fixtures/vocabulary.txt" "$fixtures/critical-terms.txt" "pl" "de" "3" "1")" == \
        "$benchmark_configuration_digest" ]]; then
        echo "self-test expected cross-language hint changes to alter provenance" >&2
        exit 1
    fi
    if [[ "$(benchmark_inputs_sha256 \
        "$fixture_digest" "$first_fixture_digest" "$second_fixture_digest" \
        "$fixtures/vocabulary.txt" "$fixtures/critical-terms.txt" "pl" "en" "5" "1")" == \
        "$benchmark_configuration_digest" ]]; then
        echo "self-test expected trial-count changes to alter provenance" >&2
        exit 1
    fi
    if [[ "$(benchmark_inputs_sha256 \
        "$fixture_digest" "$first_fixture_digest" "$second_fixture_digest" \
        "$fixtures/vocabulary.txt" "$fixtures/critical-terms.txt" "pl" "en" "3" "0")" == \
        "$benchmark_configuration_digest" ]]; then
        echo "self-test expected reference-audit status to alter provenance" >&2
        exit 1
    fi
    assert_eq "$(clip_id_for p 7 'private name')" "p007" "redacted target clip ID"
    REDACT_PATHS=0
    assert_eq "$(clip_id_for n 4 'public clip')" "n004-public-clip" "visible negative-control clip ID"
    assert_eq "$(clip_id_for x 5 'cross clip')" "x005-cross-clip" "visible cross-language clip ID"
    REDACT_PATHS=1
    printf 'changed reference\n' >"$fixtures/renamed.txt"
    if [[ "$(fixture_set_sha256 \
        "$fixtures/renamed.wav" "$fixtures/second.wav")" == "$fixture_digest" ]]; then
        echo "self-test expected fixture content changes to alter provenance" >&2
        exit 1
    fi

    local original_repo_root="$REPO_ROOT"
    local current_source_state
    current_source_state="$(benchmark_source_state)"
    if [[ "$current_source_state" != "clean" && "$current_source_state" != "modified" ]]; then
        echo "self-test expected the checked-out benchmark source to be available" >&2
        exit 1
    fi
    if ! [[ "$(benchmark_source_revision)" =~ ^[0-9a-f]{40}$ ]]; then
        echo "self-test expected a full benchmark source revision" >&2
        exit 1
    fi
    REPO_ROOT="$tmpdir/not-a-repository"
    mkdir "$REPO_ROOT"
    assert_eq "$(benchmark_source_state)" "unavailable" "unavailable benchmark source provenance"
    REPO_ROOT="$original_repo_root"

    rm -rf "$tmpdir"
    trap - EXIT INT TERM
    echo "vocabulary-bias regression self-test passed"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --input-dir)
            need_value "$@"
            INPUT_DIR="$2"
            shift 2
            ;;
        --negative-control-dir)
            need_value "$@"
            NEGATIVE_CONTROL_DIR="$2"
            shift 2
            ;;
        --cross-language-control-dir)
            need_value "$@"
            CROSS_LANGUAGE_CONTROL_DIR="$2"
            shift 2
            ;;
        --out-dir)
            need_value "$@"
            OUTDIR="$2"
            shift 2
            ;;
        --vocabulary)
            need_value "$@"
            VOCABULARY="$2"
            shift 2
            ;;
        --critical-terms)
            need_value "$@"
            CRITICAL_TERMS="$2"
            shift 2
            ;;
        --language)
            need_value "$@"
            LANGUAGE="$2"
            shift 2
            ;;
        --cross-language-control-language)
            need_value "$@"
            CROSS_LANGUAGE_CONTROL_LANGUAGE="$2"
            shift 2
            ;;
        --negative-control-language)
            echo "--negative-control-language was replaced; same-language controls now always use --language" >&2
            echo "use --cross-language-control-dir with --cross-language-control-language for an additional corpus" >&2
            exit 2
            ;;
        --trials)
            need_value "$@"
            TRIALS="$2"
            shift 2
            ;;
        --references-hand-audited)
            REFERENCES_HAND_AUDITED=1
            shift
            ;;
        --show-transcripts)
            REDACT_TRANSCRIPTS=0
            shift
            ;;
        --show-paths)
            REDACT_PATHS=0
            shift
            ;;
        --no-threshold)
            REQUIRE_CANDIDATE_PASS=0
            shift
            ;;
        --self-test)
            SELF_TEST=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ "$SELF_TEST" -eq 1 ]]; then
    run_self_test
    exit 0
fi

if [[ -z "$VOCABULARY" || ! -f "$VOCABULARY" ]]; then
    echo "--vocabulary must name an existing file" >&2
    exit 2
fi
if [[ -z "$CRITICAL_TERMS" || ! -f "$CRITICAL_TERMS" ]]; then
    echo "--critical-terms must name an existing file" >&2
    exit 2
fi
if [[ ! -d "$INPUT_DIR" ]]; then
    echo "input directory not found: $INPUT_DIR" >&2
    exit 1
fi
if [[ -n "$NEGATIVE_CONTROL_DIR" && ! -d "$NEGATIVE_CONTROL_DIR" ]]; then
    echo "negative-control directory not found: $NEGATIVE_CONTROL_DIR" >&2
    exit 1
fi
if [[ -n "$CROSS_LANGUAGE_CONTROL_DIR" && ! -d "$CROSS_LANGUAGE_CONTROL_DIR" ]]; then
    echo "cross-language control directory not found: $CROSS_LANGUAGE_CONTROL_DIR" >&2
    exit 1
fi
if [[ -n "$CROSS_LANGUAGE_CONTROL_DIR" && -z "$CROSS_LANGUAGE_CONTROL_LANGUAGE" ]]; then
    echo "--cross-language-control-language is required with --cross-language-control-dir" >&2
    exit 2
fi
if [[ -z "$CROSS_LANGUAGE_CONTROL_DIR" && -n "$CROSS_LANGUAGE_CONTROL_LANGUAGE" ]]; then
    echo "--cross-language-control-language requires --cross-language-control-dir" >&2
    exit 2
fi
if ! [[ "$TRIALS" =~ ^[0-9]+$ ]] || [[ "$TRIALS" -lt 1 ]]; then
    echo "--trials must be a positive integer" >&2
    exit 2
fi
if ! validate_reference_audit_claim; then
    exit 2
fi
if ! command -v afconvert >/dev/null 2>&1; then
    echo "afconvert is required to normalize audio" >&2
    exit 1
fi
if ! command -v git >/dev/null 2>&1; then
    echo "git is required to record benchmark source provenance" >&2
    exit 1
fi
if ! command -v shasum >/dev/null 2>&1; then
    echo "shasum is required to record benchmark artifact provenance" >&2
    exit 1
fi

source_revision="$(benchmark_source_revision)"
source_state="$(benchmark_source_state)"
if [[ ! "$source_revision" =~ ^[0-9a-f]{40}$ ]]; then
    source_revision="unavailable"
fi
if [[ "$REQUIRE_CANDIDATE_PASS" -eq 1 && "$source_state" != "clean" ]]; then
    echo "thresholded vocabulary runs require a clean Git checkout (source state: $source_state)" >&2
    echo "commit or restore benchmark source changes, or use --no-threshold for exploration" >&2
    exit 1
fi

target_clips=()
while IFS= read -r clip; do
    target_clips+=( "$clip" )
done < <(
    find "$INPUT_DIR" -type f \
        \( -iname '*.wav' -o -iname '*.aiff' -o -iname '*.aif' -o -iname '*.caf' -o -iname '*.m4a' -o -iname '*.mp3' -o -iname '*.flac' \) \
        | sort
)
if [[ "${#target_clips[@]}" -eq 0 ]]; then
    echo "no supported audio files found in $INPUT_DIR" >&2
    exit 1
fi

negative_clips=()
if [[ -n "$NEGATIVE_CONTROL_DIR" ]]; then
    while IFS= read -r clip; do
        negative_clips+=( "$clip" )
    done < <(
        find "$NEGATIVE_CONTROL_DIR" -type f \
            \( -iname '*.wav' -o -iname '*.aiff' -o -iname '*.aif' -o -iname '*.caf' -o -iname '*.m4a' -o -iname '*.mp3' -o -iname '*.flac' \) \
            | sort
    )
    if [[ "${#negative_clips[@]}" -eq 0 ]]; then
        echo "no supported audio files found in $NEGATIVE_CONTROL_DIR" >&2
        exit 1
    fi
fi

cross_language_clips=()
if [[ -n "$CROSS_LANGUAGE_CONTROL_DIR" ]]; then
    while IFS= read -r clip; do
        cross_language_clips+=( "$clip" )
    done < <(
        find "$CROSS_LANGUAGE_CONTROL_DIR" -type f \
            \( -iname '*.wav' -o -iname '*.aiff' -o -iname '*.aif' -o -iname '*.caf' -o -iname '*.m4a' -o -iname '*.mp3' -o -iname '*.flac' \) \
            | sort
    )
    if [[ "${#cross_language_clips[@]}" -eq 0 ]]; then
        echo "no supported audio files found in $CROSS_LANGUAGE_CONTROL_DIR" >&2
        exit 1
    fi
fi

if [[ "$REQUIRE_CANDIDATE_PASS" -eq 1 && "${#target_clips[@]}" -lt "$MIN_TARGET_CLIPS" ]]; then
    echo "thresholded vocabulary runs require at least $MIN_TARGET_CLIPS target clips" >&2
    echo "supply a varied positive corpus, or use --no-threshold for exploration" >&2
    exit 1
fi
if [[ "$REQUIRE_CANDIDATE_PASS" -eq 1 && "${#negative_clips[@]}" -lt "$MIN_NEGATIVE_CONTROL_CLIPS" ]]; then
    echo "thresholded vocabulary runs require at least $MIN_NEGATIVE_CONTROL_CLIPS same-language negative-control clips" >&2
    echo "supply --negative-control-dir from the target workflow, or use --no-threshold for exploration" >&2
    exit 1
fi

clips=( "${target_clips[@]}" "${negative_clips[@]}" "${cross_language_clips[@]}" )
missing_refs=()
for clip in "${clips[@]}"; do
    ref="${clip%.*}.txt"
    [[ -f "$ref" ]] || missing_refs+=( "$ref" )
done
if [[ "${#missing_refs[@]}" -gt 0 ]]; then
    echo "missing reference transcript sidecars:" >&2
    printf '  %s\n' "${missing_refs[@]}" >&2
    exit 1
fi

duplicate_clip="$(printf '%s\n' "${clips[@]}" | sort | uniq -d | head -n 1)"
if [[ -n "$duplicate_clip" ]]; then
    echo "target and control corpora overlap: $duplicate_clip" >&2
    exit 1
fi
if ! validate_unique_source_audio_content "${clips[@]}"; then
    exit 1
fi

target_fixture_sha256="$(fixture_set_sha256 "${target_clips[@]}")"
negative_fixture_sha256="none"
if [[ "${#negative_clips[@]}" -gt 0 ]]; then
    negative_fixture_sha256="$(fixture_set_sha256 "${negative_clips[@]}")"
fi
cross_language_fixture_sha256="none"
if [[ "${#cross_language_clips[@]}" -gt 0 ]]; then
    cross_language_fixture_sha256="$(fixture_set_sha256 "${cross_language_clips[@]}")"
fi
benchmark_input_sha256="$(benchmark_inputs_sha256 \
    "$target_fixture_sha256" "$negative_fixture_sha256" "$cross_language_fixture_sha256" \
    "$VOCABULARY" "$CRITICAL_TERMS" "$LANGUAGE" \
    "$CROSS_LANGUAGE_CONTROL_LANGUAGE" "$TRIALS" "$REFERENCES_HAND_AUDITED")"
if [[ ! "$benchmark_input_sha256" =~ ^[0-9a-f]{64}$ ]]; then
    echo "could not fingerprint benchmark inputs" >&2
    exit 1
fi

mkdir -p "$OUTDIR"
tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/presspeech-vocabulary-bias.XXXXXX")"
stage_dir=""
cleanup() {
    rm -rf "$tmpdir"
    if [[ -n "$stage_dir" ]]; then
        rm -rf -- "$stage_dir"
    fi
}
trap cleanup EXIT INT TERM

echo "building presspeech-bench..."
swift build -c release >/dev/null

fluid_revision="$(fluid_audio_revision)"
if [[ ! "$fluid_revision" =~ ^[0-9a-f]{40}$ ]]; then
    echo "could not identify the exact FluidAudio revision from Package.swift" >&2
    exit 1
fi
benchmark_sha256="$(file_sha256 "$BENCH_EXECUTABLE")"
if [[ ! "$benchmark_sha256" =~ ^[0-9a-f]{64}$ ]]; then
    echo "could not identify the benchmark executable SHA-256" >&2
    exit 1
fi
platform_description="$(sw_vers -productName) $(sw_vers -productVersion) ($(uname -m))"
swift_toolchain="$(swift --version | sed -n '1p')"
if [[ -z "$platform_description" || -z "$swift_toolchain" ]]; then
    echo "could not identify the benchmark platform and Swift toolchain" >&2
    exit 1
fi

# Reject corpus blockers before loading either ASR model or starting the nine
# policy lanes. The helper uses the benchmark executable's exact WER and
# critical-term normalization and prints counts only, keeping private reference
# text and paths out of shared logs.
target_preflight="$(preflight_reference_corpus p "${target_clips[@]}")" || exit 1
IFS=$'\t' read -r target_preflight_words target_preflight_occurrences <<<"$target_preflight"
negative_preflight_words=0
if [[ "${#negative_clips[@]}" -gt 0 ]]; then
    negative_preflight="$(preflight_reference_corpus n "${negative_clips[@]}")" || exit 1
    IFS=$'\t' read -r negative_preflight_words _ <<<"$negative_preflight"
fi
if [[ "${#cross_language_clips[@]}" -gt 0 ]]; then
    preflight_reference_corpus x "${cross_language_clips[@]}" >/dev/null || exit 1
fi
if [[ "$REQUIRE_CANDIDATE_PASS" -eq 1 && \
      "$target_preflight_words" -lt "$MIN_TARGET_REFERENCE_WORDS" ]]; then
    echo "thresholded vocabulary runs require at least $MIN_TARGET_REFERENCE_WORDS target reference words (found $target_preflight_words)" >&2
    echo "supply a broader positive corpus, or use --no-threshold for exploration" >&2
    exit 1
fi
if [[ "$REQUIRE_CANDIDATE_PASS" -eq 1 && \
      "$target_preflight_occurrences" -lt "$MIN_TARGET_CRITICAL_OCCURRENCES" ]]; then
    echo "thresholded vocabulary runs require at least $MIN_TARGET_CRITICAL_OCCURRENCES target critical-term occurrences (found $target_preflight_occurrences)" >&2
    echo "supply a broader positive corpus, or use --no-threshold for exploration" >&2
    exit 1
fi
if [[ "$REQUIRE_CANDIDATE_PASS" -eq 1 && \
      "$negative_preflight_words" -lt "$MIN_NEGATIVE_CONTROL_REFERENCE_WORDS" ]]; then
    echo "thresholded vocabulary runs require at least $MIN_NEGATIVE_CONTROL_REFERENCE_WORDS same-language negative-control reference words (found $negative_preflight_words)" >&2
    echo "supply broader same-language controls, or use --no-threshold for exploration" >&2
    exit 1
fi
echo "reference preflight: target=$target_preflight_words words/$target_preflight_occurrences critical occurrences; same-language controls=$negative_preflight_words words"

# Normalize the complete corpus before running any ASR lane. Source-file hashes
# catch renamed copies, while canonical output hashes also catch the same audio
# rewrapped or losslessly converted into a different supported container.
normalized_clips=()
clip_ids=()
clip_groups=()
clip_languages=()
clip_index=0
target_index=0
negative_index=0
cross_language_index=0
target_clip_count="${#target_clips[@]}"
same_language_end=$((target_clip_count + ${#negative_clips[@]}))
for clip in "${clips[@]}"; do
    clip_index=$((clip_index + 1))
    if [[ "$clip_index" -le "$target_clip_count" ]]; then
        clip_group="p"
        target_index=$((target_index + 1))
        group_index="$target_index"
        clip_language="$LANGUAGE"
    elif [[ "$clip_index" -le "$same_language_end" ]]; then
        clip_group="n"
        negative_index=$((negative_index + 1))
        group_index="$negative_index"
        clip_language="$LANGUAGE"
    else
        clip_group="x"
        cross_language_index=$((cross_language_index + 1))
        group_index="$cross_language_index"
        clip_language="$CROSS_LANGUAGE_CONTROL_LANGUAGE"
    fi
    stem="$(basename "$clip")"
    stem="${stem%.*}"
    clip_id="$(clip_id_for "$clip_group" "$group_index" "$stem")"
    normalized="$tmpdir/$clip_id.wav"
    ref="${clip%.*}.txt"

    echo "normalizing clip $clip_id..."
    afconvert -f WAVE -d LEF32@16000 "$clip" "$normalized"
    cp "$ref" "$tmpdir/$clip_id.txt"
    normalized_clips+=( "$normalized" )
    clip_ids+=( "$clip_id" )
    clip_groups+=( "$clip_group" )
    clip_languages+=( "$clip_language" )
done
if ! validate_unique_normalized_audio_content "${normalized_clips[@]}"; then
    exit 1
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
final_report="$OUTDIR/$timestamp-vocabulary-bias.md"
final_tsv="$OUTDIR/$timestamp-vocabulary-bias.tsv"
final_raw_dir="$OUTDIR/$timestamp-vocabulary-bias-logs"
if [[ -e "$final_report" || -e "$final_tsv" || -e "$final_raw_dir" ]]; then
    echo "vocabulary-bias artifacts already exist for timestamp $timestamp" >&2
    exit 1
fi
reserved_stage_dir="$OUTDIR/.$timestamp-vocabulary-bias.incomplete"
if ! mkdir "$reserved_stage_dir"; then
    echo "could not reserve vocabulary-bias output for timestamp $timestamp" >&2
    exit 1
fi
stage_dir="$reserved_stage_dir"
report="$stage_dir/report.md"
tsv="$stage_dir/results.tsv"
raw_dir="$stage_dir/logs"
mkdir -p "$raw_dir"

printf 'clip_id\tvariant\twer_percent\tcritical_matched\tcritical_total\tcritical_recall_percent\tcritical_unexpected\tp50_ms\tpeak_mb\tcache_mb\tprepare_ms\tword_errors\treference_words\tcritical_precision_percent\n' >"$tsv"

{
    echo "# Presspeech Vocabulary-Bias Comparison"
    echo
    echo "- Date: $timestamp"
    echo "- Presspeech source revision: $source_revision"
    echo "- Benchmark source state: $source_state"
    echo "- FluidAudio revision: $fluid_revision"
    echo "- Benchmark executable SHA-256: $benchmark_sha256"
    echo "- Platform: $platform_description"
    echo "- Swift toolchain: $swift_toolchain"
    echo "- Target input directory: $(path_label "$INPUT_DIR")"
    if [[ -n "$NEGATIVE_CONTROL_DIR" ]]; then
        echo "- Same-language negative-control input directory: $(path_label "$NEGATIVE_CONTROL_DIR")"
    else
        echo "- Same-language negative-control input directory: not supplied"
    fi
    if [[ -n "$CROSS_LANGUAGE_CONTROL_DIR" ]]; then
        echo "- Cross-language control input directory: $(path_label "$CROSS_LANGUAGE_CONTROL_DIR")"
    else
        echo "- Cross-language control input directory: not supplied"
    fi
    echo "- Vocabulary: $(path_label "$VOCABULARY")"
    echo "- Critical terms: $(path_label "$CRITICAL_TERMS")"
    echo "- Benchmark inputs SHA-256: $benchmark_input_sha256"
    echo "- Language hint: $LANGUAGE"
    echo "- Same-language negative-control language hint: $LANGUAGE"
    if [[ -n "$CROSS_LANGUAGE_CONTROL_DIR" ]]; then
        echo "- Cross-language control language hint: $CROSS_LANGUAGE_CONTROL_LANGUAGE"
    else
        echo "- Cross-language control language hint: not supplied"
    fi
    echo "- Trials per clip/variant: $TRIALS"
    if [[ "$REFERENCES_HAND_AUDITED" -eq 1 ]]; then
        echo "- Reference transcripts: hand-audited against the audio"
    else
        echo "- Reference transcripts: not declared hand-audited (exploratory run)"
    fi
    echo "- Target clips: ${#target_clips[@]}"
    echo "- Same-language negative-control clips: ${#negative_clips[@]}"
    echo "- Cross-language control clips: ${#cross_language_clips[@]}"
    echo "- Total clips: ${#clips[@]}"
    echo "- Transcript output: $([[ "$REDACT_TRANSCRIPTS" -eq 1 ]] && echo redacted || echo included)"
    echo
    echo "> Production v3, four direct-v3 vocabulary policies, unbiased sliding v3,"
    echo "> and three sliding-window vocabulary policies run"
    echo "> in separate processes. Critical-term recall and unexpected insertions"
    echo "> count exact canonical surface forms after case/punctuation normalization."
    echo "> An unexpected insertion is an occurrence beyond the reference count. Model cache is"
    echo "> logical on-disk size after preparation, not measured network traffic."
    echo "> Variable trial output is summarized conservatively per clip: worst WER, lowest"
    echo "> critical-term recall, highest unexpected-insertion count, and the resulting"
    echo "> lower-bound critical-term precision. Target clip IDs begin with \`p\`,"
    echo "> same-language control IDs with \`n\`, and cross-language control IDs with \`x\`."
    echo "> Every control reference must have zero critical-term occurrences."
    echo
    echo "## Per-Clip Results"
    echo
    echo "| Clip | Variant | WER % | Critical hits | Critical recall % | Critical precision % | Unexpected critical insertions | p50 ms | Peak MB | Cache MB | Prepare ms |"
    echo "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
} >"$report"

for ((clip_offset = 0; clip_offset < ${#normalized_clips[@]}; clip_offset += 1)); do
    normalized="${normalized_clips[$clip_offset]}"
    clip_id="${clip_ids[$clip_offset]}"
    clip_group="${clip_groups[$clip_offset]}"
    clip_language="${clip_languages[$clip_offset]}"
    for variant in v3 v3-vocab v3-vocab-conservative v3-vocab-no-rescue v3-vocab-exact-similarity sliding-v3 sliding-vocab sliding-vocab-conservative sliding-vocab-no-rescue; do
        log_file="$raw_dir/$clip_id-$variant.bench.txt"
        bench_args=(
            "$BENCH_EXECUTABLE"
            "--file" "$normalized"
            "--backend" "$variant"
            "--language" "$clip_language"
            "--critical-terms" "$CRITICAL_TERMS"
            "--trials" "$TRIALS"
        )
        if [[ "$variant" == "v3-vocab" ||
              "$variant" == "v3-vocab-conservative" ||
              "$variant" == "v3-vocab-no-rescue" ||
              "$variant" == "v3-vocab-exact-similarity" ||
              "$variant" == "sliding-vocab" ||
              "$variant" == "sliding-vocab-conservative" ||
              "$variant" == "sliding-vocab-no-rescue" ]]; then
            bench_args+=( "--custom-vocabulary" "$VOCABULARY" )
        fi
        if [[ "$REDACT_TRANSCRIPTS" -eq 1 ]]; then
            bench_args+=( "--redact-transcripts" )
        fi

        echo "benchmarking clip $clip_id variant=$variant..."
        if ! run_benchmark_to_log "$log_file" "${bench_args[@]}"; then
            cat "$log_file" >&2
            echo "benchmark failed for clip $clip_id variant=$variant; see $log_file" >&2
            exit 1
        fi

        wer_metrics="$(extract_worst_wer_metrics "$log_file")"
        IFS=$'\t' read -r wer word_errors reference_words <<<"$wer_metrics"
        critical="$(extract_critical_metrics "$log_file")"
        if [[ -n "$critical" ]]; then
            IFS=$'\t' read -r critical_matched critical_total critical_recall critical_unexpected <<<"$critical"
        else
            critical_matched="unknown"
            critical_total="unknown"
            critical_recall="unknown"
            critical_unexpected="unknown"
        fi
        p50="$(extract_p50_ms "$log_file")"
        peak="$(extract_peak_mb "$log_file")"
        cache="$(extract_cache_mb "$log_file")"
        prepare="$(extract_prepare_ms "$log_file")"
        [[ -n "$p50" ]] || p50="unknown"
        [[ -n "$peak" ]] || peak="unknown"
        [[ -n "$cache" ]] || cache="unknown"
        [[ -n "$prepare" ]] || prepare="unknown"

        if ! validate_metrics \
            wer "$wer" word-errors "$word_errors" reference-words "$reference_words" \
            critical-matched "$critical_matched" critical-total "$critical_total" \
            critical-recall "$critical_recall" critical-unexpected "$critical_unexpected" \
            p50 "$p50" peak "$peak" cache "$cache" prepare "$prepare"; then
            echo "invalid benchmark output for clip $clip_id variant=$variant; see $(path_label "$log_file")" >&2
            exit 1
        fi
        if [[ "$clip_group" == "n" || "$clip_group" == "x" ]] && \
            ! validate_negative_control_reference "$clip_id" "$critical_total"; then
            exit 1
        fi
        critical_precision="$(critical_precision_percent "$critical_matched" "$critical_unexpected")"

        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$clip_id" "$variant" "$wer" "$critical_matched" "$critical_total" \
            "$critical_recall" "$critical_unexpected" "$p50" "$peak" "$cache" "$prepare" \
            "$word_errors" "$reference_words" "$critical_precision" >>"$tsv"
        printf '| `%s` | `%s` | %s | %s/%s | %s | %s | %s | %s | %s | %s | %s |\n' \
            "$clip_id" "$variant" "$wer" "$critical_matched" "$critical_total" \
            "$critical_recall" "$critical_precision" "$critical_unexpected" "$p50" "$peak" "$cache" "$prepare" >>"$report"
    done
done

{
    echo
    echo "## Summary"
    echo
    echo "| Variant | Clips | Corpus WER % | Worst WER % | Critical hits | Critical recall % | Critical precision % | Unexpected critical insertions | Avg p50 ms | Max peak MB | Cache MB | Avg prepare ms |"
    echo "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
    summary_row "$tsv" v3
    summary_row "$tsv" v3-vocab
    summary_row "$tsv" v3-vocab-conservative
    summary_row "$tsv" v3-vocab-no-rescue
    summary_row "$tsv" v3-vocab-exact-similarity
    summary_row "$tsv" sliding-v3
    summary_row "$tsv" sliding-vocab
    summary_row "$tsv" sliding-vocab-conservative
    summary_row "$tsv" sliding-vocab-no-rescue
    echo
    echo "## Vocabulary Policy Deltas"
    echo
    echo "Direct-v3 policies are compared with production \`v3\`; sliding-window policies are compared with unbiased \`sliding-v3\`. All rows use per-clip conservative envelopes; lower WER and fewer unexpected insertions are better."
    echo
    echo "| Candidate | Comparable clips | Critical-hit delta | Unexpected-insertion delta | Corpus WER delta (points) | Clean wins | Costly wins | Pure losses | Other |"
    echo "|---|---:|---:|---:|---:|---:|---:|---:|---:|"
    comparison_row "$tsv" v3 v3-vocab
    comparison_row "$tsv" v3 v3-vocab-conservative
    comparison_row "$tsv" v3 v3-vocab-no-rescue
    comparison_row "$tsv" v3 v3-vocab-exact-similarity
    comparison_row "$tsv" sliding-v3 sliding-vocab
    comparison_row "$tsv" sliding-v3 sliding-vocab-conservative
    comparison_row "$tsv" sliding-v3 sliding-vocab-no-rescue
    echo
    echo "Clean wins gain critical hits without worse WER; costly wins gain hits with worse WER; pure losses worsen WER without gaining hits. Other results do not fit those three decision categories."
    echo
    echo "## Product Candidate Screen"
    echo
    echo "Compared directly with production \`v3\`. A policy passes only with human-audited references, complete comparable clips, at least ${MIN_TARGET_CLIPS} target clips containing at least ${MIN_TARGET_REFERENCE_WORDS} reference words and ${MIN_TARGET_CRITICAL_OCCURRENCES} critical-term occurrences, at least +${MIN_CRITICAL_HIT_GAIN} net critical hit, at least ${MIN_NEGATIVE_CONTROL_CLIPS} same-language negative-control clips containing at least ${MIN_NEGATIVE_CONTROL_REFERENCE_WORDS} reference words, no per-clip critical-hit loss, no aggregate or per-clip increase in unexpected insertions or WER, and average p50 latency <= ${MAX_PRODUCTION_LATENCY_RATIO}x production. Cross-language controls are additional evidence and never satisfy the same-language requirement. This is a necessary evidence screen, not approval to ship."
    echo
    echo "| Candidate | Comparable clips | Target evidence (clips / words / critical occurrences) | Same-language controls (clips / words) | Critical-hit delta | Unexpected-insertion delta | Corpus WER delta (points) | Clips with fewer critical hits | Clips with more insertions | Clips with worse WER | p50 / production | Verdict | Blockers |"
    echo "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|"
} >>"$report"

candidate_passes=0
candidate_blockers=()
for candidate in v3-vocab v3-vocab-conservative v3-vocab-no-rescue v3-vocab-exact-similarity; do
    assessment="$(candidate_assessment "$tsv" v3 "$candidate")"
    candidate_assessment_row "$assessment" >>"$report"
    IFS=$'\t' read -r assessed_candidate assessed_comparable assessed_baseline_count \
        assessed_target_clips assessed_target_words assessed_target_occurrences \
        assessed_negative_controls assessed_negative_words assessed_hit_delta \
        assessed_unexpected_delta assessed_wer_delta assessed_critical_regressed \
        assessed_unexpected_regressed assessed_wer_regressed assessed_latency_ratio \
        verdict blockers <<<"$assessment"
    if [[ "$verdict" == "passes" ]]; then
        candidate_passes=$((candidate_passes + 1))
    else
        candidate_blockers+=( "$assessed_candidate: $blockers" )
    fi
done

{
    echo
    echo "Raw bench logs: $(path_label "$final_raw_dir")"
    echo "Machine-readable TSV: $(path_label "$final_tsv")"
} >>"$report"

if [[ "$REQUIRE_CANDIDATE_PASS" -eq 1 && "$candidate_passes" -eq 0 ]]; then
    {
        echo
        echo "## Threshold Result"
        echo
        echo "No vocabulary policy cleared the product-candidate screen."
    } >>"$report"
    publish_report_artifacts \
        "$stage_dir" \
        "$report" \
        "$tsv" \
        "$raw_dir" \
        "$final_report" \
        "$final_tsv" \
        "$final_raw_dir"
    stage_dir=""
    echo "vocabulary-bias regression: no policy cleared the product-candidate screen" >&2
    printf '  %s\n' "${candidate_blockers[@]}" >&2
    echo "report: $final_report" >&2
    echo "tsv: $final_tsv" >&2
    exit 1
fi

{
    echo
    echo "## Threshold Result"
    echo
    if [[ "$candidate_passes" -gt 0 ]]; then
        echo "$candidate_passes vocabulary policy candidate(s) cleared the product-candidate screen."
    else
        echo "Threshold enforcement disabled; no vocabulary policy cleared the product-candidate screen."
    fi
} >>"$report"

publish_report_artifacts \
    "$stage_dir" \
    "$report" \
    "$tsv" \
    "$raw_dir" \
    "$final_report" \
    "$final_tsv" \
    "$final_raw_dir"
stage_dir=""

echo "report: $final_report"
echo "tsv: $final_tsv"
