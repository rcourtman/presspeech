#!/usr/bin/env bash
# Compare production Parakeet v3 with unbiased and vocabulary-rescored
# sliding-window v3 policies on the same multilingual dictation fixtures.

set -euo pipefail

# Metrics are parsed and emitted as dot-decimal machine-readable values.
export LC_ALL=C

SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$SCRIPT_PATH")"

INPUT_DIR="real-audio"
OUTDIR="vocabulary-results"
VOCABULARY=""
CRITICAL_TERMS=""
LANGUAGE="auto"
TRIALS="3"
REDACT_TRANSCRIPTS=1
REDACT_PATHS=1
REQUIRE_CANDIDATE_PASS=1
MIN_CRITICAL_HIT_GAIN="1"
MAX_UNEXPECTED_INSERTION_DELTA="0"
MAX_WER_REGRESSION_POINTS="0.00"
MAX_WER_REGRESSED_CLIPS="0"
MAX_UNEXPECTED_REGRESSED_CLIPS="0"
MAX_PRODUCTION_LATENCY_RATIO="2.00"
SELF_TEST=0

usage() {
    cat <<'USAGE'
usage: ./run-vocabulary-bias-regression.sh --vocabulary <path> --critical-terms <path> [options]

Options:
  --input-dir <path>       audio + same-stem .txt references (default: real-audio)
  --out-dir <path>         ignored report directory (default: vocabulary-results)
  --vocabulary <path>      FluidAudio text or JSON custom vocabulary (required)
  --critical-terms <path>  canonical surface forms, one per line (required)
  --language <auto|code>   Parakeet v3 language/script hint (default: auto)
  --trials <n>             measured trials per clip/variant (default: 3)
  --show-transcripts       include references and hypotheses in raw logs
  --show-paths             include fixture and configuration paths in reports
  --no-threshold           write the report but do not fail when no policy
                           clears the product-candidate screen
  --self-test              run parser, aggregation, and redaction tests only
  -h, --help               show this help

The five variants run in separate processes so memory measurements stay
isolated:
  v3             production AsrManager path
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
are rejected rather than double-counted.

The product-candidate screen compares each vocabulary policy with production
v3. It requires complete comparable clips, at least one net critical-term hit,
no aggregate or per-clip increase in unexpected insertions or WER, and average
p50 latency no more than 2x production. Passing is necessary evidence for
product evaluation, not approval to ship.
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

clip_id_for() {
    local index="$1"
    local stem="$2"
    if [[ "$REDACT_PATHS" -eq 1 ]]; then
        printf '%03d' "$index"
    else
        printf '%03d-%s' "$index" "$stem" | tr -c '[:alnum:]_.-' '-'
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
        -v max_wer_regressed_clips="$MAX_WER_REGRESSED_CLIPS" \
        -v max_unexpected_regressed_clips="$MAX_UNEXPECTED_REGRESSED_CLIPS" \
        -v max_latency_ratio="$MAX_PRODUCTION_LATENCY_RATIO" '
        function add_blocker(message) {
            blockers = blockers (blockers == "" ? "" : "; ") message
        }
        NR > 1 && $2 == baseline {
            baseline_count += 1
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
            if (total_hit_delta < min_hit_gain) add_blocker("critical-hit gain below +" min_hit_gain)
            if (unexpected_delta > max_unexpected_delta) add_blocker("unexpected insertions increased")
            if (!total_reference_words || wer_delta > max_wer_regression + 0.0000001) add_blocker("corpus WER regressed")
            if (unexpected_regressed_clips > max_unexpected_regressed_clips) add_blocker("per-clip unexpected insertions increased")
            if (wer_regressed_clips > max_wer_regressed_clips) add_blocker("per-clip WER regressions present")
            if (!comparable || latency_ratio > max_latency_ratio + 0.0000001) add_blocker("latency exceeded " max_latency_ratio "x production")

            verdict = blockers == "" ? "passes" : "blocked"
            wer_display = total_reference_words ? sprintf("%+.2f", wer_delta) : "unknown"
            latency_display = comparable ? sprintf("%.2f", latency_ratio) : "unknown"
            printf("%s\t%d\t%d\t%+d\t%+d\t%s\t%d\t%d\t%s\t%s\t%s\n",
                candidate, comparable, baseline_count, total_hit_delta,
                unexpected_delta, wer_display, unexpected_regressed_clips,
                wer_regressed_clips, latency_display,
                verdict, blockers)
        }
    ' "$tsv"
}

candidate_assessment_row() {
    local assessment="$1"
    local candidate comparable baseline_count hit_delta unexpected_delta
    local wer_delta unexpected_regressed wer_regressed latency_ratio verdict blockers
    IFS=$'\t' read -r candidate comparable baseline_count hit_delta \
        unexpected_delta wer_delta unexpected_regressed wer_regressed \
        latency_ratio verdict blockers <<<"$assessment"
    printf '| `%s` | %s/%s | %s | %s | %s | %s | %s | %sx | **%s** | %s |\n' \
        "$candidate" "$comparable" "$baseline_count" "$hit_delta" \
        "$unexpected_delta" "$wer_delta" "$unexpected_regressed" \
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
    trap 'rm -rf "$tmpdir"' EXIT INT TERM

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
        printf '001\tv3\t10.0\t1\t2\t50.0\t2\t100.0\t40.0\t600.0\t1000.0\t1\t10\t33.3\n'
        printf '002\tv3\t20.0\t2\t2\t100.0\t1\t120.0\t42.0\t600.0\t1100.0\t1\t5\t66.7\n'
        printf '001\tsliding-v3\t10.0\t1\t2\t50.0\t0\t100.0\t40.0\t600.0\t1000.0\t1\t10\t100.0\n'
        printf '002\tsliding-v3\t20.0\t1\t2\t50.0\t1\t100.0\t40.0\t600.0\t1000.0\t1\t5\t50.0\n'
        printf '003\tsliding-v3\t5.0\t1\t1\t100.0\t0\t100.0\t40.0\t600.0\t1000.0\t1\t20\t100.0\n'
        printf '004\tsliding-v3\t0.1\t1\t1\t100.0\t0\t100.0\t40.0\t600.0\t1000.0\t1\t2000\t100.0\n'
        printf '001\tsliding-vocab\t10.0\t2\t2\t100.0\t0\t100.0\t40.0\t600.0\t1000.0\t1\t10\t100.0\n'
        printf '002\tsliding-vocab\t40.0\t2\t2\t100.0\t2\t100.0\t40.0\t600.0\t1000.0\t2\t5\t50.0\n'
        printf '003\tsliding-vocab\t10.0\t1\t1\t100.0\t1\t100.0\t40.0\t600.0\t1000.0\t2\t20\t50.0\n'
        # The displayed WER ties after rounding, but the exact count exposes
        # one additional error and must classify this clip as a pure loss.
        printf '004\tsliding-vocab\t0.1\t1\t1\t100.0\t0\t100.0\t40.0\t600.0\t1000.0\t2\t2000\t100.0\n'
    } >"$tsv"
    local summary="$tmpdir/summary.md"
    summary_row "$tsv" v3 >"$summary"
    assert_contains "$summary" '| `v3` | 2 | 13.33 | 20.0 | 3/4 | 75.0 | 50.0 | 3 | 110.0 | 42.0 | 600.0 | 1050.0 |'
    comparison_row "$tsv" sliding-v3 sliding-vocab >"$summary"
    assert_contains "$summary" '| `sliding-vocab` | 4 | +2 | +2 | +0.15 | 1 | 1 | 2 | 0 |'

    local blocked_assessment
    blocked_assessment="$(candidate_assessment "$tsv" sliding-v3 sliding-vocab)"
    assert_contains <(printf '%s\n' "$blocked_assessment") $'sliding-vocab\t4\t4\t+2\t+2\t+0.15\t2\t3\t1.00\tblocked\tunexpected insertions increased; corpus WER regressed; per-clip unexpected insertions increased; per-clip WER regressions present'

    {
        head -n 1 "$tsv"
        awk -F '\t' 'NR > 1 && $2 == "sliding-v3"' "$tsv"
        printf '001\tsliding-vocab-no-rescue\t0.0\t2\t2\t100.0\t0\t150.0\t40.0\t700.0\t1000.0\t0\t10\t100.0\n'
        printf '002\tsliding-vocab-no-rescue\t0.0\t2\t2\t100.0\t1\t150.0\t40.0\t700.0\t1000.0\t0\t5\t66.7\n'
        printf '003\tsliding-vocab-no-rescue\t0.0\t1\t1\t100.0\t0\t150.0\t40.0\t700.0\t1000.0\t0\t20\t100.0\n'
        printf '004\tsliding-vocab-no-rescue\t0.0\t1\t1\t100.0\t0\t150.0\t40.0\t700.0\t1000.0\t0\t2000\t100.0\n'
    } >"$tmpdir/passing.tsv"
    local passing_assessment
    passing_assessment="$(candidate_assessment "$tmpdir/passing.tsv" sliding-v3 sliding-vocab-no-rescue)"
    assert_eq "$passing_assessment" $'sliding-vocab-no-rescue\t4\t4\t+2\t+0\t-0.20\t0\t0\t1.50\tpasses\t' "passing candidate assessment"
    candidate_assessment_row "$passing_assessment" >"$summary"
    assert_contains "$summary" '| `sliding-vocab-no-rescue` | 4/4 | +2 | +0 | -0.20 | 0 | 0 | 1.50x | **passes** | -- |'

    # Aggregate improvements must not hide a costly per-clip vocabulary win or
    # an insertion that happens to be offset on another clip.
    sed -e $'s/^001\tsliding-vocab-no-rescue\t0.0\t2\t2\t100.0\t0\t150.0\t40.0\t700.0\t1000.0\t0\t10\t100.0$/001\tsliding-vocab-no-rescue\t20.0\t2\t2\t100.0\t1\t150.0\t40.0\t700.0\t1000.0\t2\t10\t66.7/' \
        -e $'s/^002\tsliding-vocab-no-rescue\t0.0\t2\t2\t100.0\t1\t150.0\t40.0\t700.0\t1000.0\t0\t5\t66.7$/002\tsliding-vocab-no-rescue\t0.0\t2\t2\t100.0\t0\t150.0\t40.0\t700.0\t1000.0\t0\t5\t100.0/' \
        "$tmpdir/passing.tsv" >"$tmpdir/masked-regressions.tsv"
    local masked_assessment
    masked_assessment="$(candidate_assessment "$tmpdir/masked-regressions.tsv" sliding-v3 sliding-vocab-no-rescue)"
    assert_eq "$masked_assessment" $'sliding-vocab-no-rescue\t4\t4\t+2\t+0\t-0.10\t1\t1\t1.50\tblocked\tper-clip unexpected insertions increased; per-clip WER regressions present' "masked per-clip regressions"

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
        --trials)
            need_value "$@"
            TRIALS="$2"
            shift 2
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
if ! [[ "$TRIALS" =~ ^[0-9]+$ ]] || [[ "$TRIALS" -lt 1 ]]; then
    echo "--trials must be a positive integer" >&2
    exit 2
fi
if ! command -v afconvert >/dev/null 2>&1; then
    echo "afconvert is required to normalize audio" >&2
    exit 1
fi

clips=()
while IFS= read -r clip; do
    clips+=( "$clip" )
done < <(
    find "$INPUT_DIR" -type f \
        \( -iname '*.wav' -o -iname '*.aiff' -o -iname '*.aif' -o -iname '*.caf' -o -iname '*.m4a' -o -iname '*.mp3' -o -iname '*.flac' \) \
        | sort
)
if [[ "${#clips[@]}" -eq 0 ]]; then
    echo "no supported audio files found in $INPUT_DIR" >&2
    exit 1
fi

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
    echo "- Input directory: $(path_label "$INPUT_DIR")"
    echo "- Vocabulary: $(path_label "$VOCABULARY")"
    echo "- Critical terms: $(path_label "$CRITICAL_TERMS")"
    echo "- Language hint: $LANGUAGE"
    echo "- Trials per clip/variant: $TRIALS"
    echo "- Clips: ${#clips[@]}"
    echo "- Transcript output: $([[ "$REDACT_TRANSCRIPTS" -eq 1 ]] && echo redacted || echo included)"
    echo
    echo "> Production v3, unbiased sliding v3, and all three CTC-rescored policies run"
    echo "> in separate processes. Critical-term recall and unexpected insertions"
    echo "> count exact canonical surface forms after case/punctuation normalization."
    echo "> An unexpected insertion is an occurrence beyond the reference count. Model cache is"
    echo "> logical on-disk size after preparation, not measured network traffic."
    echo "> Variable trial output is summarized conservatively per clip: worst WER, lowest"
    echo "> critical-term recall, highest unexpected-insertion count, and the resulting"
    echo "> lower-bound critical-term precision."
    echo
    echo "## Per-Clip Results"
    echo
    echo "| Clip | Variant | WER % | Critical hits | Critical recall % | Critical precision % | Unexpected critical insertions | p50 ms | Peak MB | Cache MB | Prepare ms |"
    echo "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
} >"$report"

clip_index=0
for clip in "${clips[@]}"; do
    clip_index=$((clip_index + 1))
    stem="$(basename "$clip")"
    stem="${stem%.*}"
    clip_id="$(clip_id_for "$clip_index" "$stem")"
    normalized="$tmpdir/$clip_id.wav"
    ref="${clip%.*}.txt"

    echo "normalizing clip $clip_id..."
    afconvert -f WAVE -d LEF32@16000 "$clip" "$normalized"
    cp "$ref" "$tmpdir/$clip_id.txt"

    for variant in v3 sliding-v3 sliding-vocab sliding-vocab-conservative sliding-vocab-no-rescue; do
        log_file="$raw_dir/$clip_id-$variant.bench.txt"
        bench_args=(
            ".build/release/presspeech-bench"
            "--file" "$normalized"
            "--backend" "$variant"
            "--language" "$LANGUAGE"
            "--critical-terms" "$CRITICAL_TERMS"
            "--trials" "$TRIALS"
        )
        if [[ "$variant" == "sliding-vocab" ||
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
    summary_row "$tsv" sliding-v3
    summary_row "$tsv" sliding-vocab
    summary_row "$tsv" sliding-vocab-conservative
    summary_row "$tsv" sliding-vocab-no-rescue
    echo
    echo "## Vocabulary Policy Deltas"
    echo
    echo "Compared with unbiased \`sliding-v3\` using the per-clip conservative envelopes; lower WER and fewer unexpected insertions are better."
    echo
    echo "| Candidate | Comparable clips | Critical-hit delta | Unexpected-insertion delta | Corpus WER delta (points) | Clean wins | Costly wins | Pure losses | Other |"
    echo "|---|---:|---:|---:|---:|---:|---:|---:|---:|"
    comparison_row "$tsv" sliding-v3 sliding-vocab
    comparison_row "$tsv" sliding-v3 sliding-vocab-conservative
    comparison_row "$tsv" sliding-v3 sliding-vocab-no-rescue
    echo
    echo "Clean wins gain critical hits without worse WER; costly wins gain hits with worse WER; pure losses worsen WER without gaining hits. Other results do not fit those three decision categories."
    echo
    echo "## Product Candidate Screen"
    echo
    echo "Compared directly with production \`v3\`. A policy passes only with complete comparable clips, at least +${MIN_CRITICAL_HIT_GAIN} net critical hit, no aggregate or per-clip increase in unexpected insertions or WER, and average p50 latency <= ${MAX_PRODUCTION_LATENCY_RATIO}x production. This is a necessary evidence screen, not approval to ship."
    echo
    echo "| Candidate | Comparable clips | Critical-hit delta | Unexpected-insertion delta | Corpus WER delta (points) | Clips with more insertions | Clips with worse WER | p50 / production | Verdict | Blockers |"
    echo "|---|---:|---:|---:|---:|---:|---:|---:|---|---|"
} >>"$report"

candidate_passes=0
candidate_blockers=()
for candidate in sliding-vocab sliding-vocab-conservative sliding-vocab-no-rescue; do
    assessment="$(candidate_assessment "$tsv" v3 "$candidate")"
    candidate_assessment_row "$assessment" >>"$report"
    IFS=$'\t' read -r assessed_candidate _ _ _ _ _ _ _ _ verdict blockers <<<"$assessment"
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
