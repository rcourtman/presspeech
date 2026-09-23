#!/usr/bin/env bash
# Compare production v3 and one candidate backend on the same fixture directory.
#
# Reports are private/redacted by default: clip names, paths, references,
# and transcripts stay out of generated Markdown while WER, final-word
# retention, and latency remain visible. Public-corpus wrappers can opt
# into source/report visibility.

set -euo pipefail

# Metrics are parsed and emitted as dot-decimal machine-readable values.
export LC_ALL=C

SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$SCRIPT_PATH")"
dependency_provenance="$(python3 ./dependency-provenance.py)" || exit 1
IFS=$'\t' read -r FLUID_REVISION PRODUCTION_FLUID_REVISION BASELINE_DEPENDENCY <<<"$dependency_provenance"

INPUT_DIR="real-audio"
OUTDIR="real-results"
TRIALS="3"
CANDIDATE_BACKEND="unified"
LANGUAGE="auto"
UNIFIED_TRAILING_SILENCE_MS="250"
REDACT_TRANSCRIPTS=1
REDACT_PATHS=1
CORPUS_KIND="private"
REFERENCES_HAND_AUDITED=0
SILENCE_CONTROLS_HAND_AUDITED=0
REQUIRE_CANDIDATE_PASS=0
SELF_TEST=0
EXPERIMENT_ENVIRONMENT_STATE="unreported"
BENCHMARK_INPUT_SHA256="unreported"

MIN_CANDIDATE_TRIALS=3
MIN_CANDIDATE_CLIPS=25
MIN_CANDIDATE_REFERENCE_WORDS=1000
MIN_SILENCE_CONTROL_CLIPS=5
MAX_CANDIDATE_LATENCY_RATIO="1.25"
REQUIRED_UNIFIED_TRAILING_SILENCE_MS="250"

usage() {
    cat <<'USAGE'
usage: ./run-real-model-comparison.sh [options]

Options:
  --input-dir <path>       directory with audio + .txt sidecars (default: real-audio)
  --out-dir <path>         report directory (default: real-results)
  --trials <n>             measured trials per clip/backend (default: 3)
  --candidate-backend <name>
                           comparison backend: unified, v2, v3-sdk-default,
                           or v3-int8-v2
                           (default: unified)
  --language <auto|code>   Parakeet language/script hint (default: auto)
  --unified-trailing-silence-ms <n>
                           Unified-only trailing silence in ms (default: 250)
  --show-transcripts       include reference/hypothesis text in raw bench logs
  --show-paths             include local fixture filenames and paths in the report
  --public-corpus          label the report as licensed public speech instead of private fixtures
  --references-hand-audited
                           declare private references checked against audio
  --silence-controls-hand-audited
                           declare that every clip with a zero-byte .txt sidecar
                           was listened to and contains no intelligible speech
  --require-candidate-pass fail unless the supported candidate evidence screen passes
  --self-test              run parser, aggregation, and redaction self-tests
  -h, --help               show this help

Supported input extensions: wav, aiff, aif, caf, m4a, mp3, flac.
Each audio file must have a same-stem .txt reference sidecar.
Generated context-variation triplets deliberately repeat source audio and can
be analysed separately, but cannot satisfy the independent-corpus candidate
screen. A thresholded candidate corpus also needs at least five independently
recorded, hand-audited non-speech controls. Mark each with a zero-byte .txt
sidecar; include realistic room/device noise rather than only digital silence.
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

redacted_log_name() {
    local clip_id="$1"
    local backend="$2"
    if [[ "$REDACT_PATHS" -eq 1 ]]; then
        printf '%s-%s.bench.txt' "$clip_id" "$backend"
    else
        printf '%s-%s.bench.txt' "$clip_id" "$backend"
    fi
}

report_title() {
    if [[ "$CORPUS_KIND" == "public" ]]; then
        printf 'Presspeech Public-Speech Model Comparison'
    else
        printf 'Presspeech Real-Dictation Model Comparison'
    fi
}

report_note() {
    if [[ "$CORPUS_KIND" == "public" ]]; then
        cat <<'MSG'
> This report is generated from licensed public speech fixtures. References,
> hypotheses, fixture filenames, and paths may be included because the corpus
> is intentionally public; use private real-dictation fixtures for product-
> specific push-to-talk behavior.
MSG
    else
        cat <<'MSG'
> This report is generated from private local fixtures. Default
> redaction keeps reference text, hypothesis text, filenames, and
> local paths out of the report while preserving model-decision metrics.
MSG
    fi
}

raw_logs_label() {
    if [[ "$REDACT_TRANSCRIPTS" -eq 1 || "$REDACT_PATHS" -eq 1 ]]; then
        printf 'Raw redacted bench logs'
    else
        printf 'Raw bench logs'
    fi
}

extract_final_word_retained() {
    local log_file="$1"
    local states
    states="$(extract_final_word_retention_states "$log_file")"
    if grep -Fxq 'false' <<<"$states"; then
        printf 'false'
    elif grep -Fxq 'true' <<<"$states"; then
        printf 'true'
    else
        printf 'unknown'
    fi
}

extract_best_final_word_retained() {
    local log_file="$1"
    local states
    states="$(extract_final_word_retention_states "$log_file")"
    if grep -Fxq 'true' <<<"$states"; then
        printf 'true'
    elif grep -Fxq 'false' <<<"$states"; then
        printf 'false'
    else
        printf 'unknown'
    fi
}

extract_final_word_retention_states() {
    local log_file="$1"
    # Only accept benchmark-owned metric tags at the start of result lines.
    # An unredacted transcript can contain text that resembles a tag and must
    # not be allowed to alter the promotion decision.
    sed -nE 's/^[[:space:]]*(transcript:|[^[:space:]]+)[[:space:]]+\[WER [^]]+\][[:space:]]+\[final-word retained=(true|false)[^]]*\].*/\2/p' \
        "$log_file"
}

extract_worst_wer_metrics() {
    local log_file="$1"
    # Match the benchmark-owned tags at the start of each result line. An
    # unredacted dictated transcript can itself contain strings resembling
    # metric tags and must not be able to spoof the report parser.
    sed -nE 's/^[[:space:]]*(transcript:|[^[:space:]]+)[[:space:]]+\[WER ([0-9.]+)%\][[:space:]]+\[final-word retained=(true|false)([^]]*)\][[:space:]]+\[word-errors=([0-9]+) reference-words=([0-9]+)\].*/\2\t\5\t\6/p' "$log_file" \
        | awk -F '\t' '
        {
            numerator = $2
            denominator = $3
            # Match WordErrorScore.percent for an empty reference.
            if (denominator == 0) {
                numerator = numerator == 0 ? 0 : 1
                denominator = 1
            }
            # Printed WER is rounded to one decimal. Compare exact edit-count
            # fractions so a display tie cannot hide the worse trial.
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

extract_best_wer_metrics() {
    local log_file="$1"
    sed -nE 's/^[[:space:]]*(transcript:|[^[:space:]]+)[[:space:]]+\[WER ([0-9.]+)%\][[:space:]]+\[final-word retained=(true|false)([^]]*)\][[:space:]]+\[word-errors=([0-9]+) reference-words=([0-9]+)\].*/\2\t\5\t\6/p' "$log_file" \
        | awk -F '\t' '
        {
            numerator = $2
            denominator = $3
            if (denominator == 0) {
                numerator = numerator == 0 ? 0 : 1
                denominator = 1
            }
            if (!seen || numerator * best_denominator < best_numerator * denominator) {
                best = $1
                errors = $2
                words = $3
                best_numerator = numerator
                best_denominator = denominator
                seen = 1
            }
        }
        END {
            if (seen) printf("%s\t%s\t%s\n", best, errors, words)
            else print "unknown\tunknown\tunknown"
        }
    '
}

extract_p50_ms() {
    local log_file="$1"
    sed -nE 's/.*latency:[[:space:]]+p50=[[:space:]]*([0-9.]+) ms.*/\1/p' "$log_file" | head -n 1
}

extract_max_ms() {
    local log_file="$1"
    sed -nE 's/.*latency:[[:space:]]+p50=[[:space:]]*[0-9.]+ ms[[:space:]]+min=[[:space:]]*[0-9.]+ ms[[:space:]]+max=[[:space:]]*([0-9.]+) ms.*/\1/p' "$log_file" | head -n 1
}

extract_output_trial_metrics() {
    local log_file="$1"
    # These benchmark-owned lines contain only bounded counts, never transcript
    # content. Requiring one line per trial avoids mistaking a Set of distinct
    # hypotheses for the frequency of an intermittent silence hallucination.
    sed -nE 's/^[[:space:]]*output: trial=([0-9]+)\/([0-9]+) empty=(true|false) characters=([0-9]+)$/\1\t\2\t\3\t\4/p' \
        "$log_file" | awk -F '\t' '
        {
            observed += 1
            if ($1 != observed || $2 < 1 || $1 > $2) invalid = 1
            if (observed == 1) expected = $2
            else if ($2 != expected) invalid = 1
            declared = $2
            if ($3 == "false") nonempty += 1
            if (($3 == "true" && $4 != 0) || ($3 == "false" && $4 == 0)) invalid = 1
        }
        END {
            if (invalid || observed < 1 || observed != declared) print "unknown\tunknown"
            else printf("%d\t%d\n", nonempty, observed)
        }
    '
}

extract_silence_wer_metrics() {
    local log_file="$1"
    # Keep the benchmark-owned tags adjacent. An unredacted hallucination can
    # itself contain metric-looking text and must not be able to spoof a gate.
    sed -nE 's/^[[:space:]]*(transcript:|[^[:space:]]+)[[:space:]]+\[WER ([0-9.]+)%\][[:space:]]+\[word-errors=([0-9]+) reference-words=0\][[:space:]]+\[max-reference-deletion-run=[0-9]+\].*/\2\t\3\t0/p' \
        "$log_file" | awk -F '\t' '
        { if (!seen || $2 > errors) { wer = $1; errors = $2; seen = 1 } }
        END { if (seen) printf("%s\t%d\t0\n", wer, errors); else print "unknown\tunknown\tunknown" }
    '
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

file_sha256() {
    shasum -a 256 "$1" 2>/dev/null | awk '{print $1}'
}

duplicate_content_digest() {
    local clip digest
    {
        for clip in "$@"; do
            if ! digest="$(file_sha256 "$clip")" || [[ ! "$digest" =~ ^[0-9a-f]{64}$ ]]; then
                return 1
            fi
            printf '%s\n' "$digest"
        done
    } | sort | uniq -d
}

validate_unique_source_audio_content() {
    local duplicate_digest
    if ! duplicate_digest="$(duplicate_content_digest "$@")"; then
        echo "could not inspect model-comparison source audio" >&2
        return 1
    fi
    if [[ -n "$duplicate_digest" ]]; then
        echo "model comparison contains byte-identical source audio files" >&2
        echo "each clip must be an independent recording or segment" >&2
        return 1
    fi
}

validate_unique_normalized_audio_content() {
    local duplicate_digest
    if ! duplicate_digest="$(duplicate_content_digest "$@")"; then
        echo "could not inspect normalized model-comparison audio" >&2
        return 1
    fi
    if [[ -n "$duplicate_digest" ]]; then
        echo "model comparison contains audio files that normalize to byte-identical 16 kHz mono WAV" >&2
        echo "rewrapping or losslessly converting one recording does not make an independent clip" >&2
        return 1
    fi
}

backend_setting() {
    local backend="$1"
    local candidate="$2"
    if [[ "$backend" == "unified" ]]; then
        printf 'trailing-silence=%sms' "$UNIFIED_TRAILING_SILENCE_MS"
    elif [[ "$candidate" == "v3-int8-v2" ]]; then
        [[ "$backend" == "v3" ]] && printf 'encoder=int8-original' || printf 'encoder=int8-v2'
    elif [[ "$candidate" == "v3-sdk-default" ]]; then
        [[ "$backend" == "v3" ]] && printf 'chunking=released-mel-context' || printf 'chunking=sdk-default'
    elif [[ "$candidate" == "v2" ]]; then
        [[ "$backend" == "v3" ]] && printf 'multilingual-v3' || printf 'english-v2'
    else
        printf 'na'
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
        echo "model comparison staging artifacts are incomplete" >&2
        return 1
    fi
    if [[ -e "$final_report" || -e "$final_tsv" || -e "$final_raw_dir" ]]; then
        echo "refusing to replace existing model comparison artifacts" >&2
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

backend_summary_row() {
    local tsv="$1"
    local backend="$2"
    awk -F '\t' -v backend="$backend" '
        NR > 1 && $2 == backend {
            count += 1
            if ($4 != "unknown" && $8 > 0) {
                if (wer_seen == 0 || $4 > worst_wer) {
                    worst_wer = $4
                }
                wer_seen += 1
            }
            if ($7 != "unknown" && $8 != "unknown" && $8 > 0) {
                word_errors += $7
                reference_words += $8
            }
            if ($5 == "false") {
                final_fail += 1
            }
            if ($6 != "unknown") {
                p50_sum += $6
                p50_seen += 1
            }
            if ($13 ~ /^[0-9]+([.][0-9]+)?$/) {
                max_sum += $13
                max_seen += 1
            }
            if ($8 == 0 && $11 != "unknown" && $12 != "unknown") {
                silence_clips += 1
                silence_nonempty += $11
                silence_trials += $12
            }
        }
        END {
            if (count == 0) {
                printf("| `%s` | 0 | unknown | unknown | unknown | unknown | unknown | unknown |\n", backend)
                exit
            }
            corpus_wer = reference_words > 0 ? sprintf("%.2f", word_errors / reference_words * 100) : "unknown"
            worst = wer_seen > 0 ? sprintf("%.1f", worst_wer) : "unknown"
            avg_p50 = p50_seen > 0 ? sprintf("%.1f", p50_sum / p50_seen) : "unknown"
            avg_max = max_seen > 0 ? sprintf("%.1f", max_sum / max_seen) : "unknown"
            silence = silence_clips > 0 ? sprintf("%d/%d", silence_nonempty, silence_trials) : "missing"
            printf("| `%s` | %d | %s | %s | %d | %s | %s | %s |\n", backend, count, corpus_wer, worst, final_fail, avg_p50, avg_max, silence)
        }
    ' "$tsv"
}

candidate_assessment() {
    local tsv="$1"
    local candidate="$2"
    awk -F '\t' -v candidate="$candidate" '
        NR > 1 && $2 == "v3" {
            baseline_best[$1] = $9
            baseline_p50[$1] = $6
            baseline_max[$1] = $13
            baseline_words[$1] = $8
            baseline_best_final_word[$1] = $10
            baseline_nonempty[$1] = $11
            baseline_output_trials[$1] = $12
        }
        NR > 1 && $2 == candidate {
            candidate_worst[$1] = $7
            candidate_p50[$1] = $6
            candidate_max[$1] = $13
            candidate_words[$1] = $8
            candidate_worst_final_word[$1] = $5
            candidate_nonempty[$1] = $11
            candidate_output_trials[$1] = $12
        }
        END {
            for (clip in baseline_best) {
                if (baseline_words[clip] == 0) {
                    if (!(clip in candidate_worst) ||
                        baseline_nonempty[clip] == "unknown" || candidate_nonempty[clip] == "unknown" ||
                        baseline_output_trials[clip] == "unknown" || candidate_output_trials[clip] == "unknown" ||
                        baseline_output_trials[clip] != candidate_output_trials[clip]) continue
                    silence_comparable += 1
                    baseline_silence_nonempty += baseline_nonempty[clip]
                    candidate_silence_nonempty += candidate_nonempty[clip]
                    if (candidate_nonempty[clip] > baseline_nonempty[clip]) silence_regressed += 1
                    continue
                }
                if (!(clip in candidate_worst) ||
                    baseline_best[clip] == "unknown" || candidate_worst[clip] == "unknown" ||
                    baseline_p50[clip] == "unknown" || candidate_p50[clip] == "unknown" ||
                    baseline_max[clip] !~ /^[0-9]+([.][0-9]+)?$/ ||
                    candidate_max[clip] !~ /^[0-9]+([.][0-9]+)?$/ ||
                    (baseline_best_final_word[clip] != "true" && baseline_best_final_word[clip] != "false") ||
                    (candidate_worst_final_word[clip] != "true" && candidate_worst_final_word[clip] != "false") ||
                    baseline_words[clip] != candidate_words[clip]) continue
                comparable += 1
                words += baseline_words[clip]
                baseline_errors += baseline_best[clip]
                candidate_errors += candidate_worst[clip]
                baseline_latency += baseline_p50[clip]
                candidate_latency += candidate_p50[clip]
                baseline_max_latency += baseline_max[clip]
                candidate_max_latency += candidate_max[clip]
                if (candidate_worst[clip] < baseline_best[clip]) improved += 1
                if (candidate_worst[clip] > baseline_best[clip]) regressed += 1
                if (baseline_best_final_word[clip] == "true" &&
                    candidate_worst_final_word[clip] == "false") final_word_regressed += 1
            }
            ratio = baseline_latency > 0 ? candidate_latency / baseline_latency : 999
            max_ratio = baseline_max_latency > 0 ? candidate_max_latency / baseline_max_latency : 999
            printf("%d\t%d\t%d\t%d\t%d\t%d\t%d\t%.3f\t%.3f\t%d\t%d\t%d\t%d\n",
                   comparable, words, baseline_errors, candidate_errors,
                   improved, regressed, final_word_regressed, ratio, max_ratio,
                   silence_comparable, baseline_silence_nonempty,
                   candidate_silence_nonempty, silence_regressed)
        }
    ' "$tsv"
}

candidate_screen() {
    local assessment="$1"
    local source_state="$2"
    local candidate="$3"
    local comparable words baseline_errors candidate_errors improved regressed
    local final_word_regressed latency_ratio max_latency_ratio
    local silence_comparable baseline_silence_nonempty candidate_silence_nonempty
    local silence_regressed
    IFS=$'\t' read -r comparable words baseline_errors candidate_errors \
        improved regressed final_word_regressed latency_ratio max_latency_ratio \
        silence_comparable baseline_silence_nonempty candidate_silence_nonempty \
        silence_regressed <<<"$assessment"

    local blockers=()
    [[ "$candidate" == "unified" || "$candidate" == "v2" || \
       "$candidate" == "v3-sdk-default" || "$candidate" == "v3-int8-v2" ]] || \
        blockers+=("screen is defined only for unified, v2, v3-sdk-default, or v3-int8-v2")
    if [[ "$candidate" == "unified" && \
            "$UNIFIED_TRAILING_SILENCE_MS" != "$REQUIRED_UNIFIED_TRAILING_SILENCE_MS" ]]; then
        blockers+=("Unified trailing silence must be ${REQUIRED_UNIFIED_TRAILING_SILENCE_MS} ms")
    fi
    if [[ ( "$candidate" == "unified" || "$candidate" == "v2" ) && "$LANGUAGE" != "en" ]]; then
        blockers+=("English-only candidate requires an English unbiased baseline")
    fi
    [[ "$TRIALS" -ge "$MIN_CANDIDATE_TRIALS" ]] || blockers+=("fewer than $MIN_CANDIDATE_TRIALS trials")
    if [[ "$CORPUS_KIND" != "public" && "$REFERENCES_HAND_AUDITED" -ne 1 ]]; then
        blockers+=("private references not declared hand-audited")
    fi
    [[ "$source_state" == "clean" ]] || blockers+=("benchmark source modified")
    [[ "$EXPERIMENT_ENVIRONMENT_STATE" == "default" ]] || blockers+=("inherited SDK environment is $EXPERIMENT_ENVIRONMENT_STATE")
    [[ "$comparable" -ge "$MIN_CANDIDATE_CLIPS" ]] || blockers+=("fewer than $MIN_CANDIDATE_CLIPS comparable clips")
    [[ "$words" -ge "$MIN_CANDIDATE_REFERENCE_WORDS" ]] || blockers+=("fewer than $MIN_CANDIDATE_REFERENCE_WORDS reference words")
    [[ "$SILENCE_CONTROLS_HAND_AUDITED" -eq 1 ]] || blockers+=("non-speech controls not declared hand-audited")
    [[ "$silence_comparable" -ge "$MIN_SILENCE_CONTROL_CLIPS" ]] || blockers+=("fewer than $MIN_SILENCE_CONTROL_CLIPS comparable non-speech controls")
    [[ "$candidate_silence_nonempty" -eq 0 ]] || blockers+=("candidate produced text in $candidate_silence_nonempty non-speech trial(s)")
    [[ "$silence_regressed" -eq 0 ]] || blockers+=("$silence_regressed non-speech control(s) regressed")
    [[ "$improved" -ge 1 ]] || blockers+=("no clip demonstrates an error reduction")
    [[ "$candidate_errors" -le "$baseline_errors" ]] || blockers+=("corpus word errors increased")
    [[ "$regressed" -eq 0 ]] || blockers+=("$regressed clip(s) regressed")
    [[ "$final_word_regressed" -eq 0 ]] || \
        blockers+=("$final_word_regressed clip(s) introduced a final-word retention failure")
    awk -v ratio="$latency_ratio" -v max="$MAX_CANDIDATE_LATENCY_RATIO" \
        'BEGIN { exit !(ratio <= max) }' || blockers+=("latency exceeds ${MAX_CANDIDATE_LATENCY_RATIO}x baseline")
    awk -v ratio="$max_latency_ratio" -v max="$MAX_CANDIDATE_LATENCY_RATIO" \
        'BEGIN { exit !(ratio <= max) }' || blockers+=("average per-clip maximum latency exceeds ${MAX_CANDIDATE_LATENCY_RATIO}x baseline")

    if [[ "${#blockers[@]}" -eq 0 ]]; then
        printf 'passes\t'
    else
        local blocker_text
        printf -v blocker_text '%s; ' "${blockers[@]}"
        printf 'blocked\t%s' "${blocker_text%; }"
    fi
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
    EXPERIMENT_ENVIRONMENT_STATE="default"
    python3 ./test-experiment-environment.py
    python3 ./benchmark-inputs.py --self-test
    local tmpdir
    tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/presspeech-real-compare-self-test.XXXXXX")"
    trap 'rm -rf "$tmpdir"' EXIT INT TERM

    local log="$tmpdir/mock.log"
    {
        echo 'latency:  p50=  123.4 ms  min=  120.0 ms  max=  130.0 ms'
        echo 'output: trial=1/2 empty=true characters=0'
        echo 'output: trial=2/2 empty=false characters=12'
        echo 'transcript: [WER 16.7%] [final-word retained=false expected="sure" actual-last="not"] [word-errors=1 reference-words=6] "literal [WER 99.0%] [word-errors=99 reference-words=1]"'
    } >"$log"
    assert_eq "$(extract_final_word_retained "$log")" "false" "final-word parser"
    assert_eq "$(extract_best_final_word_retained "$log")" "false" "best final-word parser"
    assert_eq "$(extract_worst_wer_metrics "$log")" $'16.7\t1\t6' "WER parser"
    assert_eq "$(extract_p50_ms "$log")" "123.4" "latency parser"
    assert_eq "$(extract_max_ms "$log")" "130.0" "maximum latency parser"
    assert_eq "$(extract_max_ms /dev/null)" "" "missing maximum latency parser"
    assert_eq "$(extract_output_trial_metrics "$log")" $'1\t2' "per-trial output parser"
    assert_eq "$(extract_worst_wer_metrics /dev/null)" $'unknown\tunknown\tunknown' "missing WER parser"
    validate_metrics max-WER 16.7 word-errors 1 reference-words 6 final-word-retained false p50 123.4

    local silence_log="$tmpdir/silence.log"
    {
        echo 'output: trial=1/3 empty=true characters=0'
        echo 'output: trial=2/3 empty=false characters=9'
        echo 'output: trial=3/3 empty=true characters=0'
        echo 'transcripts (2 distinct):'
        echo '  • [WER 0.0%] [word-errors=0 reference-words=0] [max-reference-deletion-run=0] <redacted 0 chars>'
        echo '  • [WER 100.0%] [word-errors=2 reference-words=0] [max-reference-deletion-run=0] "literal [word-errors=99 reference-words=0]"'
    } >"$silence_log"
    assert_eq "$(extract_silence_wer_metrics "$silence_log")" $'100.0\t2\t0' \
        "non-speech WER parser"
    assert_eq "$(extract_output_trial_metrics "$silence_log")" $'1\t3' \
        "non-speech trial frequency"
    sed 's/trial=3\/3/trial=2\/3/' "$silence_log" >"$tmpdir/duplicate-receipt.log"
    assert_eq "$(extract_output_trial_metrics "$tmpdir/duplicate-receipt.log")" $'unknown\tunknown' \
        "duplicate output receipt fails closed"
    sed 's/trial=1\/3/trial=1\/2/' "$silence_log" >"$tmpdir/inconsistent-total.log"
    assert_eq "$(extract_output_trial_metrics "$tmpdir/inconsistent-total.log")" $'unknown\tunknown' \
        "inconsistent output receipt totals fail closed"
    assert_eq "$(backend_setting v3 v3-sdk-default)" \
        "chunking=released-mel-context" "released chunking setting label"
    assert_eq "$(backend_setting v3-sdk-default v3-sdk-default)" \
        "chunking=sdk-default" "SDK-default chunking setting label"

    local rounded_wer_log="$tmpdir/rounded-wer.log"
    {
        echo 'transcript: [WER 0.1%] [final-word retained=true] [word-errors=1 reference-words=2000] <redacted 20 chars>'
        echo 'transcript: [WER 0.1%] [final-word retained=false] [word-errors=2 reference-words=2000] <redacted 22 chars>'
    } >"$rounded_wer_log"
    assert_eq "$(extract_worst_wer_metrics "$rounded_wer_log")" $'0.1\t2\t2000' "rounded WER exact worst-trial selection"
    assert_eq "$(extract_best_wer_metrics "$rounded_wer_log")" $'0.1\t1\t2000' "rounded WER exact best-trial selection"
    assert_eq "$(extract_final_word_retained "$rounded_wer_log")" "false" \
        "worst final-word trial selection"
    assert_eq "$(extract_best_final_word_retained "$rounded_wer_log")" "true" \
        "best final-word trial selection"

    local tag_spoof_log="$tmpdir/tag-spoof.log"
    echo 'transcript: [WER 0.0%] [final-word retained=true] [word-errors=0 reference-words=5] "literal [final-word retained=false]"' \
        >"$tag_spoof_log"
    assert_eq "$(extract_final_word_retained "$tag_spoof_log")" "true" \
        "transcript text cannot spoof final-word metric"

    local fixtures="$tmpdir/fixtures"
    mkdir "$fixtures"
    printf 'normalized audio one\n' >"$fixtures/first.wav"
    printf 'normalized audio two\n' >"$fixtures/second.wav"
    validate_unique_source_audio_content "$fixtures/first.wav" "$fixtures/second.wav"
    validate_unique_normalized_audio_content "$fixtures/first.wav" "$fixtures/second.wav"
    cp "$fixtures/first.wav" "$fixtures/renamed.wav"
    local duplicate_log="$tmpdir/duplicate.log"
    if validate_unique_source_audio_content \
        "$fixtures/first.wav" "$fixtures/renamed.wav" >"$duplicate_log" 2>&1; then
        echo "self-test expected identical source recordings to be rejected" >&2
        exit 1
    fi
    assert_contains "$duplicate_log" "byte-identical source audio files"
    if validate_unique_normalized_audio_content \
        "$fixtures/first.wav" "$fixtures/renamed.wav" >"$duplicate_log" 2>&1; then
        echo "self-test expected identical normalized recordings to be rejected" >&2
        exit 1
    fi
    assert_contains "$duplicate_log" "normalize to byte-identical 16 kHz mono WAV"
    assert_not_contains "$duplicate_log" "$fixtures"
    local secret_missing="$fixtures/private-missing.wav"
    if validate_unique_source_audio_content \
        "$fixtures/first.wav" "$secret_missing" >"$duplicate_log" 2>&1; then
        echo "self-test expected unreadable source audio to be rejected" >&2
        exit 1
    fi
    assert_contains "$duplicate_log" "could not inspect model-comparison source audio"
    assert_not_contains "$duplicate_log" "$secret_missing"

    local validation_log="$tmpdir/validation.log"
    if validate_metrics max-WER unknown final-word-retained "" >"$validation_log" 2>&1; then
        echo "self-test expected missing metrics to fail validation" >&2
        exit 1
    fi
    assert_contains "$validation_log" "benchmark output missing required metrics: max-WER,final-word-retained"

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
    assert_contains "$collision_log" "refusing to replace existing model comparison artifacts"
    assert_contains "$final_dir/report.md" "complete report"
    assert_contains "$stage_dir/report.md" "new report"

    local tsv="$tmpdir/results.tsv"
    {
        printf 'clip_id\tbackend\tsetting\twer\tfinal\tp50\tword-errors\twords\tbest-errors\tbest-final\tnonempty\ttrials\tmax_ms\n'
        printf '001\tv3\tna\t100.0\ttrue\t50.0\t1\t1\t1\ttrue\t0\t1\t80.0\n'
        printf '002\tv3\tna\t1.0\tfalse\t70.0\t1\t100\t1\ttrue\t0\t1\t100.0\n'
        printf '001\tunified\t250\t5.0\ttrue\t40.0\t1\t20\t1\ttrue\t0\t1\t60.0\n'
    } >"$tsv"
    local summary="$tmpdir/summary.md"
    backend_summary_row "$tsv" "v3" >"$summary"
    # Exact corpus weighting is 2/101 (1.98%), not the misleading 50.5%
    # produced by averaging the two displayed clip percentages.
    assert_contains "$summary" '| `v3` | 2 | 1.98 | 100.0 | 1 | 60.0 | 90.0 |'
    assert_not_contains "$summary" '\n'

    local legacy_tsv="$tmpdir/legacy-results.tsv"
    {
        printf 'clip_id\tbackend\tsetting\twer\tfinal\tp50\tword-errors\twords\tbest-errors\tbest-final\tnonempty\ttrials\n'
        printf '001\tv3\tna\t5.0\ttrue\t50.0\t1\t20\t1\ttrue\t0\t1\n'
    } >"$legacy_tsv"
    backend_summary_row "$legacy_tsv" "v3" >"$summary"
    assert_contains "$summary" '| `v3` | 1 | 5.00 | 5.0 | 0 | 50.0 | unknown |'

    local precision_tsv="$tmpdir/precision.tsv"
    {
        printf 'clip_id\tbackend\tbackend_setting\tmax_wer_percent\tfinal_word_retained\tp50_ms\tworst_word_errors\treference_words\tbest_word_errors\tbest_final_word_retained\n'
        printf '001\tv3\tna\t10.0\ttrue\t100.0\t2\t20\t1\ttrue\t0\t3\t120.0\n'
        printf '001\tv3-int8-v2\tna\t0.0\ttrue\t110.0\t0\t20\t0\ttrue\t0\t3\t132.0\n'
        printf '002\tv3\tna\t0.0\ttrue\t120.0\t0\t30\t0\ttrue\t0\t3\t140.0\n'
        printf '002\tv3-int8-v2\tna\t3.3\ttrue\t130.0\t1\t30\t1\ttrue\t0\t3\t154.0\n'
    } >"$precision_tsv"
    assert_eq "$(candidate_assessment "$precision_tsv" v3-int8-v2)" \
        $'2\t50\t1\t1\t1\t1\t0\t1.091\t1.100\t0\t0\t0\t0' "encoder candidate conservative assessment"

    local final_word_tsv="$tmpdir/final-word-results.tsv"
    {
        printf 'clip_id\tbackend\tbackend_setting\tmax_wer_percent\tfinal_word_retained\tp50_ms\tworst_word_errors\treference_words\tbest_word_errors\tbest_final_word_retained\n'
        printf '001\tv3\tna\t5.0\tfalse\t100.0\t1\t20\t1\ttrue\t0\t3\t100.0\n'
        # Equal total errors and one unstable baseline trial must not hide
        # that the candidate traded an interior error for a dropped final word.
        printf '001\tv2\tna\t5.0\tfalse\t105.0\t1\t20\t1\tfalse\t0\t3\t105.0\n'
    } >"$final_word_tsv"
    assert_eq "$(candidate_assessment "$final_word_tsv" v2)" \
        $'1\t20\t1\t1\t0\t0\t1\t1.050\t1.050\t0\t0\t0\t0' "equal-WER final-word regression assessment"

    local silence_tsv="$tmpdir/silence-results.tsv"
    {
        printf 'clip_id\tbackend\tbackend_setting\tmax_wer_percent\tfinal_word_retained\tp50_ms\tworst_word_errors\treference_words\tbest_word_errors\tbest_final_word_retained\tnonempty_trials\toutput_trials\n'
        local silence_index
        for silence_index in 1 2 3 4 5; do
            printf 's%s\tv3\tna\t0.0\tnot-applicable\t10.0\t0\t0\t0\tnot-applicable\t0\t3\n' "$silence_index"
            printf 's%s\tv2\tna\t0.0\tnot-applicable\t11.0\t0\t0\t0\tnot-applicable\t0\t3\n' "$silence_index"
        done
    } >"$silence_tsv"
    assert_eq "$(candidate_assessment "$silence_tsv" v2)" \
        $'0\t0\t0\t0\t0\t0\t0\t999.000\t999.000\t5\t0\t0\t0' \
        "reviewed non-speech assessment"

    local original_trials="$TRIALS"
    local original_kind="$CORPUS_KIND"
    local original_audit="$REFERENCES_HAND_AUDITED"
    local original_unified_trailing_silence_ms="$UNIFIED_TRAILING_SILENCE_MS"
    local original_language="$LANGUAGE"
    local original_silence_audit="$SILENCE_CONTROLS_HAND_AUDITED"
    TRIALS=3
    CORPUS_KIND="public"
    REFERENCES_HAND_AUDITED=0
    SILENCE_CONTROLS_HAND_AUDITED=1
    LANGUAGE=en
    assert_eq "$(candidate_screen $'25\t1200\t10\t9\t1\t0\t0\t1.100\t1.100\t5\t0\t0\t0' clean v3-int8-v2)" \
        $'passes\t' "passing encoder candidate screen"
    assert_eq "$(candidate_screen $'25\t1200\t10\t9\t1\t0\t0\t1.100\t1.100\t5\t0\t0\t0' clean v3-sdk-default)" \
        $'passes\t' "passing SDK-default chunking candidate screen"
    assert_eq "$(candidate_screen $'25\t1200\t10\t9\t1\t0\t0\t1.100\t1.100\t5\t0\t0\t0' clean v2)" \
        $'passes\t' "passing English model candidate screen"
    assert_eq "$(candidate_screen $'25\t1200\t10\t9\t1\t0\t0\t1.100\t1.100\t5\t0\t0\t0' clean unified)" \
        $'passes\t' "passing Unified model candidate screen"
    local test_environment
    for test_environment in configured unreported pending; do
        EXPERIMENT_ENVIRONMENT_STATE="$test_environment"
        local environment_screen
        environment_screen="$(candidate_screen $'25\t1200\t10\t9\t1\t0\t0\t1.100\t1.100\t5\t0\t0\t0' clean v3-int8-v2)"
        assert_contains <(printf '%s' "$environment_screen") "blocked"
        assert_contains <(printf '%s' "$environment_screen") "inherited SDK environment is $test_environment"
    done
    EXPERIMENT_ENVIRONMENT_STATE="default"
    UNIFIED_TRAILING_SILENCE_MS=0
    local raw_unified_screen
    raw_unified_screen="$(candidate_screen $'25\t1200\t10\t9\t1\t0\t0\t1.100\t1.100\t5\t0\t0\t0' clean unified)"
    assert_contains <(printf '%s' "$raw_unified_screen") \
        "Unified trailing silence must be 250 ms"
    UNIFIED_TRAILING_SILENCE_MS="$REQUIRED_UNIFIED_TRAILING_SILENCE_MS"
    LANGUAGE=auto
    raw_unified_screen="$(candidate_screen $'25\t1200\t10\t9\t1\t0\t0\t1.100\t1.100\t5\t0\t0\t0' clean unified)"
    assert_contains <(printf '%s' "$raw_unified_screen") \
        "English-only candidate requires an English unbiased baseline"
    local blocked_screen
    blocked_screen="$(candidate_screen $'25\t1200\t10\t11\t0\t1\t1\t1.300\t5\t0\t1\t1' clean v3-int8-v2)"
    assert_contains <(printf '%s' "$blocked_screen") "no clip demonstrates an error reduction"
    assert_contains <(printf '%s' "$blocked_screen") "corpus word errors increased"
    assert_contains <(printf '%s' "$blocked_screen") \
        "1 clip(s) introduced a final-word retention failure"
    assert_contains <(printf '%s' "$blocked_screen") "latency exceeds 1.25x baseline"
    local tail_latency_screen
    tail_latency_screen="$(candidate_screen $'25\t1200\t10\t9\t1\t0\t0\t1.100\t1.300\t5\t0\t0\t0' clean v3-int8-v2)"
    assert_contains <(printf '%s' "$tail_latency_screen") \
        "average per-clip maximum latency exceeds 1.25x baseline"
    assert_contains <(printf '%s' "$blocked_screen") \
        "candidate produced text in 1 non-speech trial(s)"
    SILENCE_CONTROLS_HAND_AUDITED=0
    blocked_screen="$(candidate_screen $'25\t1200\t10\t9\t1\t0\t0\t1.100\t5\t0\t0\t0' clean v3-int8-v2)"
    assert_contains <(printf '%s' "$blocked_screen") \
        "non-speech controls not declared hand-audited"
    TRIALS="$original_trials"
    CORPUS_KIND="$original_kind"
    REFERENCES_HAND_AUDITED="$original_audit"
    UNIFIED_TRAILING_SILENCE_MS="$original_unified_trailing_silence_ms"
    LANGUAGE="$original_language"
    SILENCE_CONTROLS_HAND_AUDITED="$original_silence_audit"

    CORPUS_KIND="public"
    report_title >"$summary"
    assert_contains "$summary" "Public-Speech"

    local secret_dir="$tmpdir/Private Project"
    local secret_stem="secret-client-note"
    local secret_text="private dictated reference"
    mkdir -p "$secret_dir"
    touch "$secret_dir/$secret_stem.wav"
    printf '%s\n' "$secret_text" >"$secret_dir/$secret_stem.txt"

    REDACT_PATHS=1
    local clip_id
    clip_id="$(clip_id_for 1 "$secret_stem")"
    assert_eq "$clip_id" "001" "redacted clip id"

    {
        echo "# Report"
        echo "- Input directory: <redacted path>"
        echo "- Clip: $clip_id"
    } >"$summary"
    assert_not_contains "$summary" "Private Project"
    assert_not_contains "$summary" "$secret_stem"
    assert_not_contains "$summary" "$secret_text"

    local missing_value_log="$tmpdir/missing-value.log"
    if bash "$SCRIPT_PATH" --trials >"$missing_value_log" 2>&1; then
        echo "self-test expected --trials without a value to fail" >&2
        exit 1
    fi
    assert_contains "$missing_value_log" "--trials requires a value"

    local context_gate_dir="$tmpdir/context-gate"
    mkdir -p "$context_gate_dir"
    touch "$context_gate_dir/one.wav"
    printf 'one word\n' >"$context_gate_dir/one.txt"
    printf 'Presspeech generated public context-variation speech fixtures\n' \
        >"$context_gate_dir/.presspeech-public-context-fixtures"
    local context_gate_log="$tmpdir/context-gate.log"
    if bash "$SCRIPT_PATH" \
        --input-dir "$context_gate_dir" \
        --candidate-backend v3-int8-v2 \
        --require-candidate-pass >"$context_gate_log" 2>&1; then
        echo "self-test expected repeated context fixtures to be rejected by the candidate gate" >&2
        exit 1
    fi
    assert_contains "$context_gate_log" \
        "context-variation fixtures cannot satisfy the independent-corpus candidate screen"

    local context_preflight_log="$tmpdir/context-preflight.log"
    if bash "$SCRIPT_PATH" \
        --input-dir "$context_gate_dir" \
        --candidate-backend v3-int8-v2 >"$context_preflight_log" 2>&1; then
        echo "self-test expected malformed context fixtures to fail preflight" >&2
        exit 1
    fi
    assert_contains "$context_preflight_log" "missing regular context manifest"

    rm -rf "$tmpdir"
    trap - EXIT INT TERM
    python3 ./test-context-inputs.py
    echo "real model comparison self-test passed"
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
        --trials)
            need_value "$@"
            TRIALS="$2"
            shift 2
            ;;
        --candidate-backend)
            need_value "$@"
            CANDIDATE_BACKEND="$2"
            shift 2
            ;;
        --language)
            need_value "$@"
            LANGUAGE="$2"
            shift 2
            ;;
        --unified-trailing-silence-ms)
            need_value "$@"
            UNIFIED_TRAILING_SILENCE_MS="$2"
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
        --public-corpus)
            CORPUS_KIND="public"
            shift
            ;;
        --references-hand-audited)
            REFERENCES_HAND_AUDITED=1
            shift
            ;;
        --silence-controls-hand-audited)
            SILENCE_CONTROLS_HAND_AUDITED=1
            shift
            ;;
        --require-candidate-pass)
            REQUIRE_CANDIDATE_PASS=1
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
    if ! [[ "$FLUID_REVISION" =~ ^[0-9a-f]{40}$ ]]; then
        echo "self-test could not identify the exact FluidAudio revision" >&2
        exit 1
    fi
    run_self_test
    exit 0
fi

if ! [[ "$FLUID_REVISION" =~ ^[0-9a-f]{40}$ ]]; then
    echo "could not identify the exact FluidAudio revision from Package.swift" >&2
    exit 1
fi

if ! [[ "$TRIALS" =~ ^[0-9]+$ ]] || [[ "$TRIALS" -lt 1 ]]; then
    echo "--trials must be a positive integer" >&2
    exit 2
fi

case "$CANDIDATE_BACKEND" in
    unified|v2|v3-sdk-default|v3-int8-v2) ;;
    *)
        echo "--candidate-backend must be unified, v2, v3-sdk-default, or v3-int8-v2" >&2
        exit 2
        ;;
esac

if [[ -z "$LANGUAGE" || "$LANGUAGE" == --* ]]; then
    echo "--language requires auto or a language code" >&2
    exit 2
fi

if ! [[ "$UNIFIED_TRAILING_SILENCE_MS" =~ ^[0-9]+$ ]]; then
    echo "--unified-trailing-silence-ms must be a non-negative integer" >&2
    exit 2
fi

if [[ ! -d "$INPUT_DIR" ]]; then
    echo "input directory not found: $INPUT_DIR" >&2
    exit 1
fi

if [[ "$REQUIRE_CANDIDATE_PASS" -eq 1 && \
      ( -e "$INPUT_DIR/.presspeech-public-context-fixtures" || \
        -L "$INPUT_DIR/.presspeech-public-context-fixtures" ) ]]; then
    cat >&2 <<'MSG'
context-variation fixtures cannot satisfy the independent-corpus candidate screen

The probe and context utterances are intentionally repeated inside each
combined clip. Run this comparison without --require-candidate-pass, analyse
the resulting TSV with analyze-context-variation.py, and use a separate
general/public and human-dictation corpus for the product-candidate gate.
MSG
    exit 2
fi

CONTEXT_CORPUS=0
CONTEXT_MANIFEST_SHA256=""
if [[ -e "$INPUT_DIR/.presspeech-public-context-fixtures" || \
      -L "$INPUT_DIR/.presspeech-public-context-fixtures" ]]; then
    CONTEXT_CORPUS=1
    python3 ./compose-public-context-fixtures.py \
        --output-dir "$INPUT_DIR" --validate-output-dir >/dev/null
fi

BENCHMARK_SOURCE_STATE="clean"
if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
    BENCHMARK_SOURCE_STATE="modified"
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
    if [[ ! -f "$ref" ]]; then
        missing_refs+=( "$ref" )
    fi
done

if [[ "${#missing_refs[@]}" -gt 0 ]]; then
    echo "missing reference transcript sidecars:" >&2
    printf '  %s\n' "${missing_refs[@]}" >&2
    exit 1
fi

if ! validate_unique_source_audio_content "${clips[@]}"; then
    exit 1
fi

mkdir -p "$OUTDIR"
tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/presspeech-real-compare.XXXXXX")"
stage_dir=""
cleanup() {
    rm -rf "$tmpdir"
    if [[ -n "$stage_dir" ]]; then
        rm -rf -- "$stage_dir"
    fi
}
trap cleanup EXIT INT TERM

# Keep the caller-facing names for optional public/path-visible reports. The
# benchmark itself will consume only the immutable generic-name snapshot.
display_clips=( "${clips[@]}" )

if [[ "$CONTEXT_CORPUS" -eq 1 ]]; then
    CONTEXT_MANIFEST_SHA256="$(python3 ./compose-public-context-fixtures.py \
        --output-dir "$INPUT_DIR" --snapshot-output-dir "$tmpdir/context-inputs")"
    [[ "$CONTEXT_MANIFEST_SHA256" =~ ^[0-9a-f]{64}$ ]] || exit 1
    frozen_clips=()
    for clip in "${clips[@]}"; do
        frozen_clips+=( "$tmpdir/context-inputs/$(basename "$clip")" )
    done
    clips=( "${frozen_clips[@]}" )
fi

snapshot_args=( snapshot --output-dir "$tmpdir/benchmark-inputs" )
if ! BENCHMARK_INPUT_SHA256="$(
    python3 ./benchmark-inputs.py "${snapshot_args[@]}" -- "${clips[@]}"
)" || ! [[ "$BENCHMARK_INPUT_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
    echo "could not freeze and fingerprint model-comparison inputs" >&2
    exit 1
fi
for index in "${!clips[@]}"; do
    extension="${clips[$index]##*.}"
    clips[index]="$tmpdir/benchmark-inputs/$(printf '%06d' "$((index + 1))")/audio.$extension"
done

# Normalize the complete immutable snapshot before building or loading a model,
# then reject rewrapped or losslessly converted copies. This keeps clip and word
# floors tied to independent audio evidence without exposing private paths.
clip_ids=()
normalized_clips=()
frozen_refs=()
for index in "${!clips[@]}"; do
    clip_index=$((index + 1))
    display_clip="${display_clips[$index]}"
    stem="$(basename "$display_clip")"
    stem="${stem%.*}"
    clip_id="$(clip_id_for "$clip_index" "$stem")"
    normalized="$tmpdir/$clip_id.wav"
    frozen_ref="$tmpdir/$clip_id.txt"
    echo "normalizing clip $clip_id..."
    afconvert -f WAVE -d LEF32@16000 "${clips[$index]}" "$normalized"
    python3 ./audio-input-evidence.py --audio "$normalized" >/dev/null
    cp "${clips[$index]%.*}.txt" "$frozen_ref"
    clip_ids+=( "$clip_id" )
    normalized_clips+=( "$normalized" )
    frozen_refs+=( "$frozen_ref" )
done
if ! validate_unique_normalized_audio_content "${normalized_clips[@]}"; then
    exit 1
fi

silence_control_count=0
for ref in "${frozen_refs[@]}"; do
    # A zero-byte sidecar is an explicit non-speech marker. Whitespace-only
    # references are not silently reclassified: make the evaluator choose and
    # record an unambiguous reviewed reference.
    if [[ ! -s "$ref" ]]; then
        silence_control_count=$((silence_control_count + 1))
    fi
done

echo "building presspeech-bench..."
swift_build_args=( -c release )
if [[ "$CANDIDATE_BACKEND" == "v3-int8-v2" ]]; then
    # The opt-in enum case exists only on the documented candidate FluidAudio
    # revision. Keep it out of ordinary builds so this package can stay pinned
    # to and validate the dependency used by the production app.
    swift_build_args+=( -Xswiftc -D -Xswiftc PRESSPEECH_ENCODER_INT8_V2 )
fi
swift build "${swift_build_args[@]}" >/dev/null
built_dependency_provenance="$(python3 ./dependency-provenance.py --verify-built)" || exit 1
if [[ "$built_dependency_provenance" != "$dependency_provenance" ]]; then
    echo "dependency provenance changed during benchmark build" >&2
    exit 1
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
final_report="$OUTDIR/$timestamp-model-comparison.md"
final_tsv="$OUTDIR/$timestamp-model-comparison.tsv"
final_raw_dir="$OUTDIR/$timestamp-model-comparison-logs"
if [[ -e "$final_report" || -e "$final_tsv" || -e "$final_raw_dir" ]]; then
    echo "model comparison artifacts already exist for timestamp $timestamp" >&2
    exit 1
fi
reserved_stage_dir="$OUTDIR/.$timestamp-model-comparison.incomplete"
if ! mkdir "$reserved_stage_dir"; then
    echo "could not reserve model comparison output for timestamp $timestamp" >&2
    exit 1
fi
stage_dir="$reserved_stage_dir"
report="$stage_dir/report.md"
tsv="$stage_dir/results.tsv"
raw_dir="$stage_dir/logs"
mkdir -p "$raw_dir"

{
    printf 'clip_id\tbackend\tbackend_setting\tmax_wer_percent\tfinal_word_retained\tp50_ms\tworst_word_errors\treference_words\tbest_word_errors\tbest_final_word_retained\tnonempty_trials\toutput_trials\tmax_ms'
    if [[ "$CONTEXT_CORPUS" -eq 1 ]]; then printf '\tcontext_manifest_sha256'; fi
    printf '\n'
} >"$tsv"

{
    echo "# $(report_title)"
    echo
    echo "- Date: $timestamp"
    echo "- Input directory: $(path_label "$INPUT_DIR")"
    echo "- Trials per clip/backend: $TRIALS"
    echo "- Candidate backend: $CANDIDATE_BACKEND"
    echo "- FluidAudio revision: $FLUID_REVISION"
    echo "- App FluidAudio revision: $PRODUCTION_FLUID_REVISION"
    echo "- Baseline dependency: $BASELINE_DEPENDENCY (not whole-app qualification)"
    echo "- Benchmark inputs SHA-256: $BENCHMARK_INPUT_SHA256"
    echo "- Parakeet language hint: $LANGUAGE"
    if [[ "$CANDIDATE_BACKEND" == "unified" ]]; then
        echo "- Unified trailing silence: ${UNIFIED_TRAILING_SILENCE_MS} ms"
    fi
    echo "- Transcript output: $([[ "$REDACT_TRANSCRIPTS" -eq 1 ]] && echo redacted || echo included)"
    echo "- Fixture paths: $([[ "$REDACT_PATHS" -eq 1 ]] && echo redacted || echo included)"
    echo "- Clips: ${#clips[@]}"
    echo "- Benchmark source: $BENCHMARK_SOURCE_STATE"
    if [[ "$CONTEXT_CORPUS" -eq 1 ]]; then
        echo "- Context manifest SHA-256: $CONTEXT_MANIFEST_SHA256"
    fi
    echo "- Private references declared hand-audited: $([[ "$REFERENCES_HAND_AUDITED" -eq 1 ]] && echo yes || echo no)"
    echo "- Reviewed non-speech controls: $silence_control_count"
    echo "- Non-speech controls declared hand-audited: $([[ "$SILENCE_CONTROLS_HAND_AUDITED" -eq 1 ]] && echo yes || echo no)"
    echo
    report_note
    echo
    echo "## Per-Clip Results"
    echo
    echo "| Clip | Backend | Backend setting | Max WER % | Final word retained | Non-empty trials | p50 ms | Max observed trial ms |"
    echo "|---|---|---:|---:|---|---:|---:|---:|"
} >"$report"

EXPERIMENT_ENVIRONMENT_STATE="pending"
for index in "${!normalized_clips[@]}"; do
    clip_id="${clip_ids[$index]}"
    normalized="${normalized_clips[$index]}"
    silence_control=0
    if [[ ! -s "${frozen_refs[$index]}" ]]; then silence_control=1; fi

    for backend in v3 "$CANDIDATE_BACKEND"; do
        log_file="$raw_dir/$(redacted_log_name "$clip_id" "$backend")"
        bench_args=( ".build/release/presspeech-bench" "--file" "$normalized" "--backend" "$backend" "--trials" "$TRIALS" )
        bench_args+=( "--language" "$LANGUAGE" )
        if [[ "$backend" == "unified" ]]; then
            bench_args+=( "--unified-trailing-silence-ms" "$UNIFIED_TRAILING_SILENCE_MS" )
        fi
        if [[ "$REDACT_TRANSCRIPTS" -eq 1 ]]; then
            bench_args+=( "--redact-transcripts" )
        fi

        echo "benchmarking clip $clip_id backend=$backend..."
        if ! "${bench_args[@]}" >"$log_file" 2>&1; then
            cat "$log_file" >&2
            echo "benchmark failed for clip $clip_id backend=$backend" >&2
            exit 1
        fi

        python3 ./audio-input-evidence.py --audio "$normalized" --log "$log_file" >>"$log_file"
        EXPERIMENT_ENVIRONMENT_STATE="$(python3 ./experiment-environment.py --log "$log_file" --previous "$EXPERIMENT_ENVIRONMENT_STATE")"

        if [[ "$silence_control" -eq 1 ]]; then
            wer_metrics="$(extract_silence_wer_metrics "$log_file")"
            best_wer_metrics="$wer_metrics"
            retained="not-applicable"
            best_retained="not-applicable"
        else
            wer_metrics="$(extract_worst_wer_metrics "$log_file")"
            best_wer_metrics="$(extract_best_wer_metrics "$log_file")"
            retained="$(extract_final_word_retained "$log_file")"
            best_retained="$(extract_best_final_word_retained "$log_file")"
        fi
        IFS=$'\t' read -r wer word_errors reference_words <<<"$wer_metrics"
        IFS=$'\t' read -r best_wer best_word_errors best_reference_words <<<"$best_wer_metrics"
        output_metrics="$(extract_output_trial_metrics "$log_file")"
        IFS=$'\t' read -r nonempty_trials output_trials <<<"$output_metrics"
        p50="$(extract_p50_ms "$log_file")"
        [[ -n "$p50" ]] || p50="unknown"
        max_ms="$(extract_max_ms "$log_file")"
        [[ -n "$max_ms" ]] || max_ms="unknown"
        if ! validate_metrics \
            max-WER "$wer" word-errors "$word_errors" reference-words "$reference_words" \
            best-WER "$best_wer" best-word-errors "$best_word_errors" \
            best-reference-words "$best_reference_words" \
            final-word-retained "$retained" best-final-word-retained "$best_retained" \
            nonempty-trials "$nonempty_trials" output-trials "$output_trials" p50 "$p50"; then
            cat "$log_file" >&2
            echo "invalid benchmark output for clip $clip_id backend=$backend" >&2
            exit 1
        fi
        setting="$(backend_setting "$backend" "$CANDIDATE_BACKEND")"

        if [[ "$best_reference_words" != "$reference_words" ]]; then
            echo "inconsistent reference metrics for clip $clip_id backend=$backend" >&2
            exit 1
        fi
        if [[ "$output_trials" != "$TRIALS" ]]; then
            echo "benchmark output trial count mismatch for clip $clip_id backend=$backend" >&2
            exit 1
        fi

        {
            printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s' \
                "$clip_id" "$backend" "$setting" "$wer" "$retained" "$p50" \
                "$word_errors" "$reference_words" "$best_word_errors" "$best_retained" \
                "$nonempty_trials" "$output_trials" "$max_ms"
            if [[ "$CONTEXT_CORPUS" -eq 1 ]]; then printf '\t%s' "$CONTEXT_MANIFEST_SHA256"; fi
            printf '\n'
        } >>"$tsv"
        printf '| `%s` | `%s` | %s | %s | %s | %s/%s | %s | %s |\n' \
            "$clip_id" "$backend" "$setting" "$wer" "$retained" \
            "$nonempty_trials" "$output_trials" "$p50" "$max_ms" >>"$report"
    done
done

observed_input_sha256="$(python3 ./benchmark-inputs.py verify --snapshot-dir "$tmpdir/benchmark-inputs")"
if [[ "$observed_input_sha256" != "$BENCHMARK_INPUT_SHA256" ]]; then
    echo "frozen model-comparison inputs changed during the benchmark" >&2
    exit 1
fi

if [[ "$CONTEXT_CORPUS" -eq 1 ]]; then
    python3 ./compose-public-context-fixtures.py \
        --output-dir "$tmpdir/context-inputs" --validate-output-dir >/dev/null
    observed_context_digest="$(python3 -c 'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' \
        "$tmpdir/context-inputs/manifest.tsv")"
    if [[ "$observed_context_digest" != "$CONTEXT_MANIFEST_SHA256" ]]; then
        echo "context snapshot changed during comparison" >&2
        exit 1
    fi
fi

assessment="$(candidate_assessment "$tsv" "$CANDIDATE_BACKEND")"
IFS=$'\t' read -r comparable reference_words baseline_errors candidate_errors \
    improved regressed final_word_regressed latency_ratio max_latency_ratio \
    silence_comparable baseline_silence_nonempty candidate_silence_nonempty \
    silence_regressed <<<"$assessment"
screen="$(candidate_screen "$assessment" "$BENCHMARK_SOURCE_STATE" "$CANDIDATE_BACKEND")"
IFS=$'\t' read -r verdict blockers <<<"$screen"

{
    echo
    echo "Inherited SDK environment: $EXPERIMENT_ENVIRONMENT_STATE (configured or unreported runs cannot qualify)."
    echo
    echo "## Summary"
    echo
    echo "| Backend | Clip rows | Corpus WER % | Worst WER % | Final-word failures | Average p50 ms | Average per-clip max trial ms | Non-speech false-positive trials |"
    echo "|---|---:|---:|---:|---:|---:|---:|---:|"
    backend_summary_row "$tsv" "v3"
    backend_summary_row "$tsv" "$CANDIDATE_BACKEND"
    if [[ "$CANDIDATE_BACKEND" == "unified" || "$CANDIDATE_BACKEND" == "v2" || \
          "$CANDIDATE_BACKEND" == "v3-sdk-default" || "$CANDIDATE_BACKEND" == "v3-int8-v2" ]]; then
        echo
        echo "## Model Candidate Evidence Screen"
        echo
        echo "The candidate's worst observed transcript is compared with baseline's best observed transcript on each speech clip; a noisy baseline trial therefore cannot hide a candidate regression. Passing requires a clean benchmark source, default inherited SDK controls in every native invocation, at least ${MIN_CANDIDATE_TRIALS} trials, ${MIN_CANDIDATE_CLIPS} speech clips, ${MIN_CANDIDATE_REFERENCE_WORDS} reference words, at least one demonstrated improvement, no per-clip or corpus error increase, no new final-word retention failure, at least ${MIN_SILENCE_CONTROL_CLIPS} independently recorded hand-audited non-speech controls with no candidate text in any trial, average p50 latency within ${MAX_CANDIDATE_LATENCY_RATIO}x baseline, and average per-clip maximum observed latency within the same ratio. The max comparison is an observed-tail guard, not a statistical percentile guarantee. English-only candidates require an English unbiased baseline. Unified additionally requires ${REQUIRED_UNIFIED_TRAILING_SILENCE_MS} ms trailing silence and the separate tail-word gate. Private speech references must be hand-audited; licensed public speech references are accepted. This is a per-corpus prerequisite, not approval to ship."
        echo
        echo "| Candidate | Comparable speech clips | Reference words | Baseline best errors | Candidate worst errors | Improved clips | Regressed clips | New final-word failures | Non-speech controls | Baseline / candidate false-positive trials | p50 / baseline | avg max / baseline | Verdict | Blockers |"
        echo "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|"
        printf '| `%s` | %s | %s | %s | %s | %s | %s | %s | %s | %s / %s | %.3f | %.3f | %s | %s |\n' \
            "$CANDIDATE_BACKEND" "$comparable" "$reference_words" "$baseline_errors" \
            "$candidate_errors" "$improved" "$regressed" "$final_word_regressed" \
            "$silence_comparable" "$baseline_silence_nonempty" "$candidate_silence_nonempty" "$latency_ratio" "$max_latency_ratio" \
            "$verdict" "${blockers:-}"
    fi
    echo
    echo "$(raw_logs_label): $(path_label "$final_raw_dir")"
    echo "Machine-readable TSV: $(path_label "$final_tsv")"
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

if [[ "$REQUIRE_CANDIDATE_PASS" -eq 1 && "$verdict" != "passes" ]]; then
    echo "candidate evidence screen blocked: ${blockers:-unknown blocker}" >&2
    exit 1
fi
