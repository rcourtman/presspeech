#!/usr/bin/env bash
# Compare v3 and Unified on the same audio fixture directory.
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

INPUT_DIR="real-audio"
OUTDIR="real-results"
TRIALS="3"
UNIFIED_TRAILING_SILENCE_MS="250"
REDACT_TRANSCRIPTS=1
REDACT_PATHS=1
CORPUS_KIND="private"
SELF_TEST=0

usage() {
    cat <<'USAGE'
usage: ./run-real-model-comparison.sh [options]

Options:
  --input-dir <path>       directory with audio + .txt sidecars (default: real-audio)
  --out-dir <path>         report directory (default: real-results)
  --trials <n>             measured trials per clip/backend (default: 3)
  --unified-trailing-silence-ms <n>
                           Unified-only trailing silence in ms (default: 250)
  --show-transcripts       include reference/hypothesis text in raw bench logs
  --show-paths             include local fixture filenames and paths in the report
  --public-corpus          label the report as licensed public speech instead of private fixtures
  --self-test              run parser, aggregation, and redaction self-tests
  -h, --help               show this help

Supported input extensions: wav, aiff, aif, caf, m4a, mp3, flac.
Each audio file must have a same-stem .txt reference sidecar.
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
    if grep -Eq 'final-word retained=false' "$log_file"; then
        printf 'false'
    elif grep -Eq 'final-word retained=true' "$log_file"; then
        printf 'true'
    else
        printf 'unknown'
    fi
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

extract_p50_ms() {
    local log_file="$1"
    sed -nE 's/.*latency:[[:space:]]+p50=[[:space:]]*([0-9.]+) ms.*/\1/p' "$log_file" | head -n 1
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
            if ($4 != "unknown") {
                if (wer_seen == 0 || $4 > worst_wer) {
                    worst_wer = $4
                }
                wer_seen += 1
            }
            if ($7 != "unknown" && $8 != "unknown") {
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
        }
        END {
            if (count == 0) {
                printf("| `%s` | 0 | unknown | unknown | unknown | unknown |\n", backend)
                exit
            }
            corpus_wer = reference_words > 0 ? sprintf("%.2f", word_errors / reference_words * 100) : "unknown"
            worst = wer_seen > 0 ? sprintf("%.1f", worst_wer) : "unknown"
            avg_p50 = p50_seen > 0 ? sprintf("%.1f", p50_sum / p50_seen) : "unknown"
            printf("| `%s` | %d | %s | %s | %d | %s |\n", backend, count, corpus_wer, worst, final_fail, avg_p50)
        }
    ' "$tsv"
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
    tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/presspeech-real-compare-self-test.XXXXXX")"
    trap 'rm -rf "$tmpdir"' EXIT INT TERM

    local log="$tmpdir/mock.log"
    {
        echo 'latency:  p50=  123.4 ms  min=  120.0 ms  max=  130.0 ms'
        echo 'transcript: [WER 16.7%] [final-word retained=false expected="sure" actual-last="not"] [word-errors=1 reference-words=6] "literal [WER 99.0%] [word-errors=99 reference-words=1]"'
    } >"$log"
    assert_eq "$(extract_final_word_retained "$log")" "false" "final-word parser"
    assert_eq "$(extract_worst_wer_metrics "$log")" $'16.7\t1\t6' "WER parser"
    assert_eq "$(extract_p50_ms "$log")" "123.4" "latency parser"
    assert_eq "$(extract_worst_wer_metrics /dev/null)" $'unknown\tunknown\tunknown' "missing WER parser"
    validate_metrics max-WER 16.7 word-errors 1 reference-words 6 final-word-retained false p50 123.4

    local rounded_wer_log="$tmpdir/rounded-wer.log"
    {
        echo 'transcript: [WER 0.1%] [final-word retained=true] [word-errors=1 reference-words=2000] <redacted 20 chars>'
        echo 'transcript: [WER 0.1%] [final-word retained=false] [word-errors=2 reference-words=2000] <redacted 22 chars>'
    } >"$rounded_wer_log"
    assert_eq "$(extract_worst_wer_metrics "$rounded_wer_log")" $'0.1\t2\t2000' "rounded WER exact worst-trial selection"

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
        printf 'clip_id\tbackend\tunified_trailing_ms\tmax_wer_percent\tfinal_word_retained\tp50_ms\tword_errors\treference_words\n'
        printf '001\tv3\tna\t100.0\ttrue\t50.0\t1\t1\n'
        printf '002\tv3\tna\t1.0\tfalse\t70.0\t1\t100\n'
        printf '001\tunified\t250\t5.0\ttrue\t40.0\t1\t20\n'
    } >"$tsv"
    local summary="$tmpdir/summary.md"
    backend_summary_row "$tsv" "v3" >"$summary"
    # Exact corpus weighting is 2/101 (1.98%), not the misleading 50.5%
    # produced by averaging the two displayed clip percentages.
    assert_contains "$summary" '| `v3` | 2 | 1.98 | 100.0 | 1 | 60.0 |'
    assert_not_contains "$summary" '\n'

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

    rm -rf "$tmpdir"
    trap - EXIT INT TERM
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

if ! [[ "$TRIALS" =~ ^[0-9]+$ ]] || [[ "$TRIALS" -lt 1 ]]; then
    echo "--trials must be a positive integer" >&2
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

echo "building presspeech-bench..."
swift build -c release >/dev/null

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

printf 'clip_id\tbackend\tunified_trailing_ms\tmax_wer_percent\tfinal_word_retained\tp50_ms\tword_errors\treference_words\n' >"$tsv"

{
    echo "# $(report_title)"
    echo
    echo "- Date: $timestamp"
    echo "- Input directory: $(path_label "$INPUT_DIR")"
    echo "- Trials per clip/backend: $TRIALS"
    echo "- Unified trailing silence: ${UNIFIED_TRAILING_SILENCE_MS} ms"
    echo "- Transcript output: $([[ "$REDACT_TRANSCRIPTS" -eq 1 ]] && echo redacted || echo included)"
    echo "- Fixture paths: $([[ "$REDACT_PATHS" -eq 1 ]] && echo redacted || echo included)"
    echo "- Clips: ${#clips[@]}"
    echo
    report_note
    echo
    echo "## Per-Clip Results"
    echo
    echo "| Clip | Backend | Unified trailing ms | Max WER % | Final word retained | p50 ms |"
    echo "|---|---|---:|---:|---|---:|"
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

    for backend in v3 unified; do
        log_file="$raw_dir/$(redacted_log_name "$clip_id" "$backend")"
        bench_args=( ".build/release/presspeech-bench" "--file" "$normalized" "--backend" "$backend" "--trials" "$TRIALS" )
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

        wer_metrics="$(extract_worst_wer_metrics "$log_file")"
        IFS=$'\t' read -r wer word_errors reference_words <<<"$wer_metrics"
        retained="$(extract_final_word_retained "$log_file")"
        p50="$(extract_p50_ms "$log_file")"
        [[ -n "$p50" ]] || p50="unknown"
        if ! validate_metrics \
            max-WER "$wer" word-errors "$word_errors" reference-words "$reference_words" \
            final-word-retained "$retained" p50 "$p50"; then
            cat "$log_file" >&2
            echo "invalid benchmark output for clip $clip_id backend=$backend" >&2
            exit 1
        fi
        trailing="na"
        if [[ "$backend" == "unified" ]]; then
            trailing="$UNIFIED_TRAILING_SILENCE_MS"
        fi

        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$clip_id" "$backend" "$trailing" "$wer" "$retained" "$p50" \
            "$word_errors" "$reference_words" >>"$tsv"
        printf '| `%s` | `%s` | %s | %s | %s | %s |\n' \
            "$clip_id" "$backend" "$trailing" "$wer" "$retained" "$p50" >>"$report"
    done
done

{
    echo
    echo "## Summary"
    echo
    echo "| Backend | Clip rows | Corpus WER % | Worst WER % | Final-word failures | Average p50 ms |"
    echo "|---|---:|---:|---:|---:|---:|"
    backend_summary_row "$tsv" "v3"
    backend_summary_row "$tsv" "unified"
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
