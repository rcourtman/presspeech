#!/usr/bin/env bash
# Run the Swift benchmark over an audio fixture directory.
#
# Expected layout by default:
#
#   real-audio/
#     short-note.wav
#     short-note.txt
#     noisy-room.m4a
#     noisy-room.txt
#
# Each .txt sidecar is the reference transcript for the audio with the
# same stem. Reports default to private/redacted transcript output and land
# under real-results/, which is ignored by git.

set -euo pipefail

# Metrics are parsed and emitted as dot-decimal machine-readable values.
export LC_ALL=C

SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$SCRIPT_PATH")"

INPUT_DIR="real-audio"
OUTDIR="real-results"
BACKEND="v3"
TRIALS="5"
UNIFIED_TRAILING_SILENCE_MS="250"
NEMOTRON_MULTILINGUAL_LANGUAGE="en-US"
NEMOTRON_MULTILINGUAL_CHUNK_MS="2240"
ALLOW_MISSING_REF=0
REDACT_TRANSCRIPTS=1
REDACT_PATHS=1
CORPUS_KIND="private"
SELF_TEST=0

usage() {
    cat <<'USAGE'
usage: ./run-real-dictation-regression.sh [options]

Options:
  --input-dir <path>       directory with audio + .txt sidecars (default: real-audio)
  --out-dir <path>         report directory (default: real-results)
  --backend <name>         presspeech-bench backend: v3, unified, nemotron-en,
                           nemotron-multilingual, apple, 110m, fluid, both (default: v3)
  --trials <n>             measured trials per clip (default: 5)
  --unified-trailing-silence-ms <n>
                           Unified-only trailing silence in ms (default: 250)
  --nemotron-multilingual-language <code>
                           Nemotron 3.5 language prompt (default: en-US)
  --nemotron-multilingual-chunk-ms <560|1120|2240|4480>
                           Nemotron 3.5 exported chunk tier (default: 2240)
  --allow-missing-ref      run clips without .txt sidecars, skipping WER
  --show-transcripts       include reference/hypothesis text in the report
  --show-paths             include local fixture filenames and paths in the report
  --public-corpus          label the report as licensed public speech instead of private fixtures
  --self-test              run parser and report-redaction self-tests
  -h, --help               show this help

Supported input extensions: wav, aiff, aif, caf, m4a, mp3, flac.
Audio is normalized through afconvert into a temporary 16 kHz Float32
WAV before benchmarking; presspeech-bench then does the final mono
conversion with AVAudioConverter.
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

transcript_output_label() {
    if [[ "$REDACT_TRANSCRIPTS" -eq 1 ]]; then
        printf 'redacted'
    else
        printf 'included'
    fi
}

fixture_paths_label() {
    if [[ "$REDACT_PATHS" -eq 1 ]]; then
        printf 'redacted'
    else
        printf 'included'
    fi
}

report_title() {
    if [[ "$CORPUS_KIND" == "public" ]]; then
        printf 'Presspeech Public-Speech Regression'
    else
        printf 'Presspeech Real-Dictation Regression'
    fi
}

backend_uses_unified() {
    [[ "$BACKEND" == "unified" || "$BACKEND" == "fluid" || "$BACKEND" == "both" ]]
}

backend_uses_nemotron_multilingual() {
    [[ "$BACKEND" == "nemotron-multilingual" || "$BACKEND" == "fluid" || "$BACKEND" == "both" ]]
}

backend_is_aggregate() {
    [[ "$BACKEND" == "fluid" || "$BACKEND" == "both" ]]
}

expected_backend_count() {
    case "$1" in
        fluid) printf '5' ;;
        both) printf '6' ;;
        *) printf '1' ;;
    esac
}

validate_benchmark_output() {
    local log_file="$1"
    local expected_backends="$2"
    local require_reference="$3"
    awk -v expected="$expected_backends" -v require_ref="$require_reference" '
        function inspect_result(line) {
            results += 1
            if (require_ref == 1 &&
                (line !~ /\[WER [0-9]+([.][0-9]+)?%\]/ ||
                 line !~ /\[final-word retained=(true|false)([[:space:]]|\])/ ||
                 line !~ /\[word-errors=[0-9]+ reference-words=[0-9]+\]/)) {
                incomplete_results += 1
            }
        }
        /^    latency:[[:space:]]+p50=[[:space:]]*[0-9]+([.][0-9]+)? ms/ {
            latencies += 1
        }
        /^    transcript:/ {
            groups += 1
            inspect_result($0)
        }
        /^    transcripts \([0-9]+ distinct\):/ {
            groups += 1
        }
        /^      [^[:space:]]/ {
            inspect_result($0)
        }
        END {
            if (latencies != expected || groups != expected ||
                results < groups || incomplete_results > 0) {
                printf("benchmark output missing required metrics: expected-backends=%d latency-groups=%d transcript-groups=%d transcript-results=%d incomplete-reference-results=%d\n",
                       expected, latencies, groups, results, incomplete_results) > "/dev/stderr"
                exit 1
            }
        }
    ' "$log_file"
}

single_backend_summary_row() {
    local report="$1"
    awk -v backend="$BACKEND" '
        function flush_wer() {
            if (clip_wer_seen == 0) return
            wer_sum += clip_worst_wer
            if (wer_seen == 0 || clip_worst_wer > worst_wer) {
                worst_wer = clip_worst_wer
            }
            wer_seen += 1
            final_fail += clip_final_fail
            clip_wer_seen = 0
            clip_worst_wer = 0
            clip_final_fail = 0
        }
        /latency:.*p50=/ {
            # A backend emits latency before either one stable transcript or
            # several distinct transcript bullets. Close the preceding clip
            # here so variability still contributes one conservative WER row.
            flush_wer()
            p50 = $0
            sub(/^.*p50=[[:space:]]*/, "", p50)
            sub(/ ms.*$/, "", p50)
            p50_sum += p50
            p50_seen += 1
        }
        /\[WER [0-9.]+%\]/ {
            match($0, /\[WER [0-9.]+%\]/)
            wer = substr($0, RSTART + 5, RLENGTH - 7) + 0
            if (clip_wer_seen == 0 || wer > clip_worst_wer) clip_worst_wer = wer
            clip_wer_seen += 1
            if ($0 ~ /\[WER [0-9.]+%\] \[final-word retained=false/) {
                clip_final_fail = 1
            }
        }
        END {
            flush_wer()
            rows = p50_seen > wer_seen ? p50_seen : wer_seen
            avg_wer = wer_seen > 0 ? sprintf("%.2f", wer_sum / wer_seen) : "unknown"
            worst = wer_seen > 0 ? sprintf("%.1f", worst_wer) : "unknown"
            failures = wer_seen > 0 ? final_fail : "unknown"
            avg_p50 = p50_seen > 0 ? sprintf("%.1f", p50_sum / p50_seen) : "unknown"
            printf("| `%s` | %d | %s | %s | %s | %s |\n", backend, rows, avg_wer, worst, failures, avg_p50)
        }
    ' "$report"
}

append_single_backend_summary() {
    local report="$1"
    if backend_is_aggregate; then
        return
    fi
    local summary_row
    summary_row="$(single_backend_summary_row "$report")"
    {
        echo
        echo "## Summary"
        echo
        echo "| Backend | Clip rows | Average WER % | Worst WER % | Final-word failures | Average p50 ms |"
        echo "|---|---:|---:|---:|---:|---:|"
        printf '%s\n' "$summary_row"
    } >>"$report"
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
> This report is generated from private local fixtures. The default
> redacted mode keeps reference text, hypothesis text, filenames, and
> local paths out of the report while preserving WER, latency, and
> memory numbers.
MSG
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

write_report_header() {
    local report="$1"
    local timestamp="$2"
    local clip_count="$3"
    {
        echo "# $(report_title)"
        echo
        echo "- Date: $timestamp"
        echo "- Input directory: $(path_label "$INPUT_DIR")"
        echo "- Backend: $BACKEND"
        echo "- Trials per clip: $TRIALS"
        if backend_uses_unified; then
            echo "- Unified trailing silence: ${UNIFIED_TRAILING_SILENCE_MS} ms"
        fi
        if backend_uses_nemotron_multilingual; then
            echo "- Nemotron multilingual language: $NEMOTRON_MULTILINGUAL_LANGUAGE"
            echo "- Nemotron multilingual chunk: ${NEMOTRON_MULTILINGUAL_CHUNK_MS} ms"
        fi
        echo "- Transcript output: $(transcript_output_label)"
        echo "- Fixture paths: $(fixture_paths_label)"
        echo "- Clips: $clip_count"
        echo
        report_note
    } >"$report"
}

write_clip_section_header() {
    local report="$1"
    local clip_number="$2"
    local clip_id="$3"
    local stem="$4"
    local clip="$5"
    local ref="$6"

    {
        echo
        if [[ "$REDACT_PATHS" -eq 1 ]]; then
            echo "## Clip $clip_number"
        else
            echo "## $clip_id"
        fi
        echo
        echo "- Clip name: $([[ "$REDACT_PATHS" -eq 1 ]] && echo '<redacted>' || echo "$stem")"
        echo "- Source: $(path_label "$clip")"
        if [[ -f "$ref" ]]; then
            echo "- Reference: $(path_label "$ref") (WER enabled)"
        else
            echo "- Reference: missing (WER skipped)"
        fi
        echo
        echo '```text'
    } >>"$report"
}

assert_contains() {
    local file="$1"
    local needle="$2"
    if ! grep -Fq -- "$needle" "$file"; then
        echo "self-test expected report to contain: $needle" >&2
        exit 1
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
    tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/presspeech-real-self-test.XXXXXX")"
    trap 'rm -rf "$tmpdir"' EXIT INT TERM

    local secret_dir="$tmpdir/Private Client Project"
    local secret_stem="confidential-medical-note"
    local secret_transcript="patient alpha private transcript"
    mkdir -p "$secret_dir"
    touch "$secret_dir/$secret_stem.wav"
    printf '%s\n' "$secret_transcript" >"$secret_dir/$secret_stem.txt"

    INPUT_DIR="$secret_dir"
    OUTDIR="$tmpdir/out"
    BACKEND="v3"
    TRIALS="2"
    UNIFIED_TRAILING_SILENCE_MS="250"
    NEMOTRON_MULTILINGUAL_LANGUAGE="en-US"
    NEMOTRON_MULTILINGUAL_CHUNK_MS="2240"
    REDACT_TRANSCRIPTS=1
    REDACT_PATHS=1

    local report="$tmpdir/report.md"
    local clip_number="001"
    local clip_id
    clip_id="$(clip_id_for 1 "$secret_stem")"
    write_report_header "$report" "20260101T000000Z" 1
    assert_not_contains "$report" "Unified trailing silence"

    BACKEND="unified"
    write_report_header "$report" "20260101T000000Z" 1
    assert_contains "$report" "- Unified trailing silence: 250 ms"
    BACKEND="nemotron-multilingual"
    write_report_header "$report" "20260101T000000Z" 1
    assert_contains "$report" "- Nemotron multilingual language: en-US"
    assert_contains "$report" "- Nemotron multilingual chunk: 2240 ms"
    BACKEND="v3"
    write_report_header "$report" "20260101T000000Z" 1

    write_clip_section_header "$report" "$clip_number" "$clip_id" "$secret_stem" "$secret_dir/$secret_stem.wav" "$secret_dir/$secret_stem.txt"
    {
        echo "presspeech-bench: $clip_id.wav, 1 trials, backend=v3"
        echo "reference: <redacted ${#secret_transcript} chars>"
        echo "transcript: [WER 0.0%] <redacted ${#secret_transcript} chars>"
        echo '```'
    } >>"$report"

    assert_contains "$report" "- Input directory: <redacted path>"
    assert_contains "$report" "- Clip name: <redacted>"
    assert_contains "$report" "presspeech-bench: 001.wav"
    assert_not_contains "$report" "Private Client Project"
    assert_not_contains "$report" "$secret_stem"
    assert_not_contains "$report" "$secret_transcript"

    local summary_source="$tmpdir/summary-source.md"
    {
        echo '    latency:  p50=  50.0 ms  min=  49.0 ms  max=  51.0 ms'
        echo '    transcripts (2 distinct):'
        echo '      • [WER 0.0%] [final-word retained=true expected="one" actual-last="one"] [word-errors=0 reference-words=1] <redacted 3 chars>'
        echo '      • [WER 4.0%] [final-word retained=false expected="one" actual-last="none"] [word-errors=1 reference-words=25] "literal [WER 99.0%]"'
        echo '    latency:  p50=  70.0 ms  min=  69.0 ms  max=  71.0 ms'
        echo '    transcript: [WER 10.0%] [final-word retained=false expected="two" actual-last="one"] [word-errors=1 reference-words=10] <redacted 3 chars>'
    } >"$summary_source"
    BACKEND="v3"
    # shellcheck disable=SC2016 # Markdown backticks are intentional literals.
    local expected_summary='| `v3` | 2 | 7.00 | 10.0 | 2 | 60.0 |'
    assert_eq "$(single_backend_summary_row "$summary_source")" "$expected_summary" "variable-output single-backend summary"
    append_single_backend_summary "$summary_source"
    assert_contains "$summary_source" "## Summary"
    assert_contains "$summary_source" "$expected_summary"
    validate_benchmark_output "$summary_source" 2 1
    assert_eq "$(expected_backend_count v3)" "1" "single backend count"
    assert_eq "$(expected_backend_count fluid)" "5" "fluid backend count"
    assert_eq "$(expected_backend_count both)" "6" "all backend count"

    local no_reference_source="$tmpdir/no-reference-source.log"
    {
        echo '    latency:  p50=  80.0 ms  min=  79.0 ms  max=  81.0 ms'
        echo '    transcript: <redacted 3 chars>'
    } >"$no_reference_source"
    validate_benchmark_output "$no_reference_source" 1 0

    local incomplete_source="$tmpdir/incomplete-source.log"
    {
        echo '    latency:  p50=  80.0 ms  min=  79.0 ms  max=  81.0 ms'
        echo '    transcript: [WER 0.0%] <redacted 3 chars>'
    } >"$incomplete_source"
    local validation_log="$tmpdir/validation.log"
    if validate_benchmark_output "$incomplete_source" 1 1 >"$validation_log" 2>&1; then
        echo "self-test expected incomplete benchmark metrics to fail validation" >&2
        exit 1
    fi
    assert_contains "$validation_log" "benchmark output missing required metrics:"

    local missing_value_log="$tmpdir/missing-value.log"
    if bash "$SCRIPT_PATH" --trials >"$missing_value_log" 2>&1; then
        echo "self-test expected --trials without a value to fail" >&2
        exit 1
    fi
    assert_contains "$missing_value_log" "--trials requires a value"

    rm -rf "$tmpdir"
    trap - EXIT INT TERM
    echo "real-dictation regression self-test passed"
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
        --backend)
            need_value "$@"
            BACKEND="$2"
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
        --nemotron-multilingual-language)
            need_value "$@"
            NEMOTRON_MULTILINGUAL_LANGUAGE="$2"
            shift 2
            ;;
        --nemotron-multilingual-chunk-ms)
            need_value "$@"
            NEMOTRON_MULTILINGUAL_CHUNK_MS="$2"
            shift 2
            ;;
        --allow-missing-ref)
            ALLOW_MISSING_REF=1
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

if [[ ! -d "$INPUT_DIR" ]]; then
    cat >&2 <<MSG
input directory not found: $INPUT_DIR

Create it and add private audio files plus matching .txt reference files.
See real-audio/README.md.
MSG
    exit 1
fi

if ! [[ "$TRIALS" =~ ^[0-9]+$ ]] || [[ "$TRIALS" -lt 1 ]]; then
    echo "--trials must be a positive integer" >&2
    exit 2
fi

if ! [[ "$UNIFIED_TRAILING_SILENCE_MS" =~ ^[0-9]+$ ]]; then
    echo "--unified-trailing-silence-ms must be a non-negative integer" >&2
    exit 2
fi

if [[ -z "${NEMOTRON_MULTILINGUAL_LANGUAGE//[[:space:]]/}" ]]; then
    echo "--nemotron-multilingual-language must not be empty" >&2
    exit 2
fi

case "$NEMOTRON_MULTILINGUAL_CHUNK_MS" in
    560|1120|2240|4480) ;;
    *)
        echo "--nemotron-multilingual-chunk-ms must be one of 560, 1120, 2240, or 4480" >&2
        exit 2
        ;;
esac

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

if [[ "${#missing_refs[@]}" -gt 0 && "$ALLOW_MISSING_REF" -eq 0 ]]; then
    echo "missing reference transcript sidecars:" >&2
    printf '  %s\n' "${missing_refs[@]}" >&2
    echo "add .txt sidecars or pass --allow-missing-ref to skip WER for those clips" >&2
    exit 1
fi

mkdir -p "$OUTDIR"
tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/presspeech-real-dictation.XXXXXX")"
cleanup() { rm -rf "$tmpdir"; }
trap cleanup EXIT INT TERM

echo "building presspeech-bench..."
swift build -c release >/dev/null

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
safe_backend="$(printf '%s' "$BACKEND" | tr -c '[:alnum:]_.-' '-')"
report="$OUTDIR/$timestamp-$safe_backend.md"
backend_count="$(expected_backend_count "$BACKEND")"

write_report_header "$report" "$timestamp" "${#clips[@]}"

clip_index=0
for clip in "${clips[@]}"; do
    clip_index=$((clip_index + 1))
    clip_number="$(printf '%03d' "$clip_index")"
    stem="$(basename "$clip")"
    stem="${stem%.*}"
    clip_id="$(clip_id_for "$clip_index" "$stem")"
    normalized="$tmpdir/$clip_id.wav"
    ref="${clip%.*}.txt"

    echo "normalizing clip $clip_number..."
    afconvert -f WAVE -d LEF32@16000 "$clip" "$normalized"
    if [[ -f "$ref" ]]; then
        cp "$ref" "$tmpdir/$clip_id.txt"
    fi

    bench_args=(
        ".build/release/presspeech-bench"
        "--file" "$normalized"
        "--backend" "$BACKEND"
        "--trials" "$TRIALS"
        "--unified-trailing-silence-ms" "$UNIFIED_TRAILING_SILENCE_MS"
        "--nemotron-multilingual-language" "$NEMOTRON_MULTILINGUAL_LANGUAGE"
        "--nemotron-multilingual-chunk-ms" "$NEMOTRON_MULTILINGUAL_CHUNK_MS"
    )
    if [[ "$REDACT_TRANSCRIPTS" -eq 1 ]]; then
        bench_args+=( "--redact-transcripts" )
    fi

    write_clip_section_header "$report" "$clip_number" "$clip_id" "$stem" "$clip" "$ref"

    echo "benchmarking clip $clip_number..."
    log_file="$tmpdir/$clip_id.log"
    if ! "${bench_args[@]}" >"$log_file" 2>&1; then
        cat "$log_file" >>"$report"
        {
            echo '```'
            echo
            echo "Benchmark failed for clip $clip_number."
        } >>"$report"
        echo "benchmark failed for clip $clip_number; see $report" >&2
        exit 1
    fi
    cat "$log_file" >>"$report"

    require_reference=0
    if [[ -f "$ref" ]]; then
        require_reference=1
    fi
    if ! validate_benchmark_output "$log_file" "$backend_count" "$require_reference"; then
        {
            echo '```'
            echo
            echo "Benchmark output was incomplete for clip $clip_number."
        } >>"$report"
        echo "invalid benchmark output for clip $clip_number; see $report" >&2
        exit 1
    fi

    echo '```' >>"$report"
done

append_single_backend_summary "$report"

echo "report: $report"
