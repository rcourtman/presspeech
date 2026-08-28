#!/usr/bin/env bash
# Run presspeech-bench while sampling SoC power rails with powermetrics.
#
# powermetrics requires sudo and reports estimates, not lab-grade energy
# numbers. Treat these reports as same-Mac, same-OS comparisons between
# backends or dependency versions, not as cross-device measurements.

set -euo pipefail

# Metrics are parsed and emitted as dot-decimal machine-readable values.
export LC_ALL=C

SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$SCRIPT_PATH")"

FILE=""
BACKEND="v3"
TRIALS="20"
UNIFIED_TRAILING_SILENCE_MS="250"
NEMOTRON_MULTILINGUAL_LANGUAGE="en-US"
NEMOTRON_MULTILINGUAL_CHUNK_MS="2240"
SAMPLE_MS="250"
OUTDIR="power-results"
REDACT_TRANSCRIPTS=1
REDACT_PATHS=1
SELF_TEST=0
power_pid=""
tmpdir=""
bench_file=""
stage_dir=""

usage() {
    cat <<'USAGE'
usage: ./bench-power.sh --file <audio> [options]

Options:
  --backend <name>       presspeech-bench backend: v3, unified, nemotron-en,
                         nemotron-multilingual, apple, 110m, fluid, both (default: v3)
  --trials <n>           measured transcription trials (default: 20)
  --unified-trailing-silence-ms <n>
                         Unified-only trailing silence in ms (default: 250)
  --nemotron-multilingual-language <code>
                         Nemotron 3.5 language prompt (default: en-US)
  --nemotron-multilingual-chunk-ms <560|1120|2240|4480>
                         Nemotron 3.5 exported chunk tier (default: 2240)
  --sample-ms <n>        powermetrics sample interval in ms (default: 250)
  --out-dir <path>       report directory (default: power-results)
  --show-transcripts     include reference/hypothesis text in the bench log
  --show-paths           include local audio filenames and paths in the report
  --self-test            run parser and report-redaction self-tests
  -h, --help             show this help

The script writes:
  <out-dir>/*.md                 human-readable summary
  <out-dir>/*.bench.txt          raw presspeech-bench output
  <out-dir>/*.powermetrics.txt   raw powermetrics output
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

artifact_path_label() {
    if [[ "$REDACT_PATHS" -eq 1 ]]; then
        # Generated filenames contain only the timestamp, redacted fixture
        # stem, and sanitized backend. Keep those useful while withholding a
        # caller-supplied output directory that may itself identify a fixture.
        printf '%s' "${1##*/}"
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

report_stem_for() {
    local stem="$1"
    if [[ "$REDACT_PATHS" -eq 1 ]]; then
        printf 'audio'
    else
        printf '%s' "$stem"
    fi
}

backend_uses_unified() {
    [[ "$BACKEND" == "unified" || "$BACKEND" == "fluid" || "$BACKEND" == "both" ]]
}

backend_uses_nemotron_multilingual() {
    [[ "$BACKEND" == "nemotron-multilingual" || "$BACKEND" == "fluid" || "$BACKEND" == "both" ]]
}

prepare_bench_file() {
    bench_file="$FILE"
    if [[ "$REDACT_PATHS" -eq 1 ]]; then
        tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/presspeech-power.XXXXXX")"
        local extension="${FILE##*.}"
        if [[ "$extension" == "$FILE" ]]; then
            bench_file="$tmpdir/audio"
        else
            bench_file="$tmpdir/audio.$extension"
        fi
        local file_dir
        file_dir="$(cd "$(dirname "$FILE")" && pwd)"
        ln -s "$file_dir/$(basename "$FILE")" "$bench_file"
        local ref="${FILE%.*}.txt"
        if [[ -f "$ref" ]]; then
            cp "$ref" "${bench_file%.*}.txt"
        fi
    fi
}

cleanup() {
    if [[ -n "$power_pid" ]] && kill -0 "$power_pid" >/dev/null 2>&1; then
        sudo kill -TERM "$power_pid" >/dev/null 2>&1 || true
        wait "$power_pid" 2>/dev/null || true
    fi
    if [[ -n "$tmpdir" ]]; then
        rm -rf "$tmpdir"
    fi
}

cleanup_all() {
    cleanup
    if [[ -n "$stage_dir" ]]; then
        rm -rf -- "$stage_dir"
    fi
}

publish_power_artifacts() {
    local publish_stage_dir="$1"
    local staged_report="$2"
    local staged_bench_log="$3"
    local staged_power_log="$4"
    local final_report="$5"
    local final_bench_log="$6"
    local final_power_log="$7"

    if [[ ! -f "$staged_report" || ! -f "$staged_bench_log" || ! -f "$staged_power_log" ]]; then
        echo "power benchmark staging artifacts are incomplete" >&2
        return 1
    fi
    if [[ -e "$final_report" || -e "$final_bench_log" || -e "$final_power_log" ]]; then
        echo "refusing to replace existing power benchmark artifacts" >&2
        return 1
    fi

    # The Markdown report is the completion marker. Publish it only after both
    # raw logs are in place, rolling back paths created by a failed move.
    local moved_bench=0
    local moved_power=0
    if ! mv "$staged_bench_log" "$final_bench_log"; then
        return 1
    fi
    moved_bench=1
    if ! mv "$staged_power_log" "$final_power_log"; then
        rm -f -- "$final_bench_log"
        return 1
    fi
    moved_power=1
    if ! mv "$staged_report" "$final_report"; then
        [[ "$moved_power" -eq 0 ]] || rm -f -- "$final_power_log"
        [[ "$moved_bench" -eq 0 ]] || rm -f -- "$final_bench_log"
        return 1
    fi
    rmdir "$publish_stage_dir"
}

write_power_report() {
    local report="$1"
    local timestamp="$2"
    local power_summary="$3"
    local bench_log="$4"
    {
        echo "# Presspeech Power Benchmark"
        echo
        echo "- Date: $timestamp"
        echo "- Audio: $(path_label "$FILE")"
        echo "- Backend: $BACKEND"
        echo "- Trials: $TRIALS"
        if backend_uses_unified; then
            echo "- Unified trailing silence: ${UNIFIED_TRAILING_SILENCE_MS} ms"
        fi
        if backend_uses_nemotron_multilingual; then
            echo "- Nemotron multilingual language: $NEMOTRON_MULTILINGUAL_LANGUAGE"
            echo "- Nemotron multilingual chunk: ${NEMOTRON_MULTILINGUAL_CHUNK_MS} ms"
        fi
        echo "- powermetrics sample interval: ${SAMPLE_MS} ms"
        echo "- Transcript output: $(transcript_output_label)"
        echo "- Fixture paths: $(fixture_paths_label)"
        echo
        echo "## Power Summary"
        echo
        echo '```text'
        printf '%s\n' "$power_summary"
        echo '```'
        echo
        echo "## Benchmark Output"
        echo
        echo '```text'
        cat "$bench_log"
        echo '```'
        echo
        echo "Raw files:"
        echo
        echo "- $(artifact_path_label "$bench_log")"
        echo "- $(artifact_path_label "$power_log")"
    } >"$report"
}

summarize_power_log() {
    local log_file="$1"
    awk '
        function numeric(value) {
            return value ~ /^[0-9]+([.][0-9]+)?$/
        }
        /^CPU Power:/ && numeric($3) { cpu_sum += $3; cpu_n += 1 }
        /^GPU Power:/ && numeric($3) { gpu_sum += $3; gpu_n += 1 }
        /^ANE Power:/ && numeric($3) { ane_sum += $3; ane_n += 1 }
        END {
            if (cpu_n > 0) printf("CPU Power avg: %.1f mW (%d samples)\n", cpu_sum / cpu_n, cpu_n);
            if (gpu_n > 0) printf("GPU Power avg: %.1f mW (%d samples)\n", gpu_sum / gpu_n, gpu_n);
            if (ane_n > 0) printf("ANE Power avg: %.1f mW (%d samples)\n", ane_sum / ane_n, ane_n);
            if (cpu_n + gpu_n + ane_n == 0) {
                print "No CPU/GPU/ANE power lines parsed; inspect raw powermetrics output.";
                exit 1;
            }
        }
    ' "$log_file"
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
        echo "self-test expected report to contain: $needle" >&2
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
    local self_tmp
    self_tmp="$(mktemp -d "${TMPDIR:-/tmp}/presspeech-power-self-test.XXXXXX")"
    trap 'rm -rf "$self_tmp"; cleanup' EXIT INT TERM

    local secret_dir="$self_tmp/Private Battery Client"
    local secret_stem="confidential-board-demo"
    local secret_transcript="private battery transcript"
    mkdir -p "$secret_dir"
    touch "$secret_dir/$secret_stem.wav"
    printf '%s\n' "$secret_transcript" >"$secret_dir/$secret_stem.txt"

    FILE="$secret_dir/$secret_stem.wav"
    OUTDIR="$secret_dir/$secret_stem-results"
    mkdir -p "$OUTDIR"
    BACKEND="v3"
    TRIALS="2"
    UNIFIED_TRAILING_SILENCE_MS="250"
    NEMOTRON_MULTILINGUAL_LANGUAGE="en-US"
    NEMOTRON_MULTILINGUAL_CHUNK_MS="2240"
    SAMPLE_MS="100"
    REDACT_TRANSCRIPTS=1
    REDACT_PATHS=1

    local timestamp="20260101T000000Z"
    local stem="$secret_stem"
    local safe_backend="v3"
    local prefix
    prefix="$OUTDIR/$timestamp-$(report_stem_for "$stem")-$safe_backend"
    local report="$prefix.md"
    bench_log="$prefix.bench.txt"
    power_log="$prefix.powermetrics.txt"

    prepare_bench_file
    if [[ "$(basename "$bench_file")" != "audio.wav" ]]; then
        echo "self-test expected redacted bench filename, got: $bench_file" >&2
        exit 1
    fi

    {
        echo "presspeech-bench: $(basename "$bench_file"), 1 trials, backend=v3"
        echo "reference: <redacted ${#secret_transcript} chars>"
        echo "transcript: [WER 0.0%] <redacted ${#secret_transcript} chars>"
    } >"$bench_log"
    printf 'CPU Power avg: 123.0 mW (2 samples)\n' >"$power_log"

    local parser_log="$self_tmp/parser.powermetrics.txt"
    {
        echo 'CPU Power: 100 mW'
        echo 'GPU Power: unavailable mW'
        echo 'ANE Power: 50.5 mW'
        echo 'CPU Power: 200 mW'
    } >"$parser_log"
    local expected_summary
    expected_summary=$'CPU Power avg: 150.0 mW (2 samples)\nANE Power avg: 50.5 mW (1 samples)'
    assert_eq "$(summarize_power_log "$parser_log")" "$expected_summary" "power summary parser"

    local empty_parser_log="$self_tmp/empty.powermetrics.txt"
    echo 'GPU Power: unavailable mW' >"$empty_parser_log"
    local empty_summary
    if empty_summary="$(summarize_power_log "$empty_parser_log")"; then
        echo "self-test expected missing power metrics to fail validation" >&2
        exit 1
    fi
    assert_eq "$empty_summary" "No CPU/GPU/ANE power lines parsed; inspect raw powermetrics output." "missing power metrics"

    write_power_report "$report" "$timestamp" "CPU Power avg: 123.0 mW (2 samples)" "$bench_log"
    assert_contains "$report" "- Audio: <redacted path>"
    assert_contains "$report" "- Fixture paths: redacted"
    assert_contains "$report" "presspeech-bench: audio.wav"
    assert_not_contains "$report" "Private Battery Client"
    assert_not_contains "$report" "$secret_stem"
    assert_not_contains "$report" "$secret_transcript"
    assert_not_contains "$report" "Unified trailing silence"
    assert_contains "$report" "- $timestamp-audio-v3.bench.txt"
    assert_contains "$report" "- $timestamp-audio-v3.powermetrics.txt"

    BACKEND="nemotron-multilingual"
    write_power_report "$report" "$timestamp" "CPU Power avg: 123.0 mW (2 samples)" "$bench_log"
    assert_contains "$report" "- Nemotron multilingual language: en-US"
    assert_contains "$report" "- Nemotron multilingual chunk: 2240 ms"

    stage_dir="$self_tmp/staged"
    local final_dir="$self_tmp/published"
    mkdir -p "$stage_dir" "$final_dir"
    local staged_report="$stage_dir/result.md"
    local staged_bench="$stage_dir/result.bench.txt"
    local staged_power="$stage_dir/result.powermetrics.txt"
    local final_report="$final_dir/result.md"
    local final_bench="$final_dir/result.bench.txt"
    local final_power="$final_dir/result.powermetrics.txt"
    printf 'complete report\n' >"$staged_report"
    printf 'bench output\n' >"$staged_bench"
    printf 'power output\n' >"$staged_power"
    publish_power_artifacts \
        "$stage_dir" \
        "$staged_report" "$staged_bench" "$staged_power" \
        "$final_report" "$final_bench" "$final_power"
    stage_dir=""
    assert_contains "$final_report" "complete report"
    assert_contains "$final_bench" "bench output"
    assert_contains "$final_power" "power output"

    stage_dir="$self_tmp/collision-stage"
    mkdir -p "$stage_dir"
    printf 'new report\n' >"$stage_dir/result.md"
    printf 'new bench output\n' >"$stage_dir/result.bench.txt"
    printf 'new power output\n' >"$stage_dir/result.powermetrics.txt"
    local collision_log="$self_tmp/collision.log"
    if publish_power_artifacts \
        "$stage_dir" \
        "$stage_dir/result.md" \
        "$stage_dir/result.bench.txt" \
        "$stage_dir/result.powermetrics.txt" \
        "$final_report" \
        "$final_dir/other.bench.txt" \
        "$final_dir/other.powermetrics.txt" >"$collision_log" 2>&1; then
        echo "self-test expected power artifact collision to fail" >&2
        exit 1
    fi
    assert_contains "$collision_log" "refusing to replace existing power benchmark artifacts"
    assert_contains "$final_report" "complete report"
    assert_contains "$stage_dir/result.md" "new report"
    rm -rf "$stage_dir"
    stage_dir=""

    local missing_value_log="$self_tmp/missing-value.log"
    if bash "$SCRIPT_PATH" --out-dir >"$missing_value_log" 2>&1; then
        echo "self-test expected --out-dir without a value to fail" >&2
        exit 1
    fi
    assert_contains "$missing_value_log" "--out-dir requires a value"

    cleanup
    rm -rf "$self_tmp"
    trap - EXIT INT TERM
    echo "power benchmark self-test passed"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --file)
            need_value "$@"
            FILE="$2"
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
        --sample-ms)
            need_value "$@"
            SAMPLE_MS="$2"
            shift 2
            ;;
        --out-dir)
            need_value "$@"
            OUTDIR="$2"
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

if [[ -z "$FILE" ]]; then
    echo "--file is required" >&2
    usage >&2
    exit 2
fi

if [[ ! -f "$FILE" ]]; then
    echo "audio file not found: $FILE" >&2
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

if ! [[ "$SAMPLE_MS" =~ ^[0-9]+$ ]] || [[ "$SAMPLE_MS" -lt 50 ]]; then
    echo "--sample-ms must be an integer >= 50" >&2
    exit 2
fi

if ! command -v powermetrics >/dev/null 2>&1; then
    echo "powermetrics is not available on this Mac" >&2
    exit 1
fi

if ! sudo -n true >/dev/null 2>&1; then
    cat >&2 <<'MSG'
powermetrics requires sudo. Run one of:

    sudo -v
    ./bench-power.sh --file <audio>

or invoke this script through sudo from a shell where you trust the repo.
MSG
    exit 1
fi

mkdir -p "$OUTDIR"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
stem="$(basename "$FILE")"
stem="${stem%.*}"
report_stem="$(report_stem_for "$stem")"
safe_backend="$(printf '%s' "$BACKEND" | tr -c '[:alnum:]_.-' '-')"
artifact_stem="$timestamp-$report_stem-$safe_backend"
final_prefix="$OUTDIR/$artifact_stem"
final_bench_log="$final_prefix.bench.txt"
final_power_log="$final_prefix.powermetrics.txt"
final_report="$final_prefix.md"
if [[ -e "$final_report" || -e "$final_bench_log" || -e "$final_power_log" ]]; then
    echo "power benchmark artifacts already exist for timestamp $timestamp" >&2
    exit 1
fi
reserved_stage_dir="$OUTDIR/.$artifact_stem.incomplete"
if ! mkdir "$reserved_stage_dir"; then
    echo "could not reserve power benchmark output for timestamp $timestamp" >&2
    exit 1
fi
stage_dir="$reserved_stage_dir"
bench_log="$stage_dir/$artifact_stem.bench.txt"
power_log="$stage_dir/$artifact_stem.powermetrics.txt"
report="$stage_dir/$artifact_stem.md"
trap cleanup_all EXIT INT TERM

echo "building presspeech-bench..."
swift build -c release >/dev/null

prepare_bench_file

bench_args=(
    ".build/release/presspeech-bench"
    "--file" "$bench_file"
    "--backend" "$BACKEND"
    "--trials" "$TRIALS"
    "--unified-trailing-silence-ms" "$UNIFIED_TRAILING_SILENCE_MS"
    "--nemotron-multilingual-language" "$NEMOTRON_MULTILINGUAL_LANGUAGE"
    "--nemotron-multilingual-chunk-ms" "$NEMOTRON_MULTILINGUAL_CHUNK_MS"
)
if [[ "$REDACT_TRANSCRIPTS" -eq 1 ]]; then
    bench_args+=( "--redact-transcripts" )
fi

echo "sampling power for $(artifact_path_label "$final_power_log")..."
sudo powermetrics \
    --sample-rate "$SAMPLE_MS" \
    --sample-count -1 \
    --buffer-size 1 \
    --samplers cpu_power,gpu_power,ane_power \
    --output-file "$power_log" &
power_pid="$!"

sleep 1
if ! kill -0 "$power_pid" >/dev/null 2>&1; then
    power_status=1
    wait "$power_pid" 2>/dev/null || power_status="$?"
    echo "powermetrics exited before the benchmark started" >&2
    exit "$power_status"
fi

echo "running benchmark..."
bench_status=0
"${bench_args[@]}" >"$bench_log" 2>&1 || bench_status="$?"

cleanup

power_metrics_valid=1
if ! power_summary="$(summarize_power_log "$power_log")"; then
    power_metrics_valid=0
fi

if [[ "$bench_status" -ne 0 ]]; then
    cat "$bench_log" >&2
    echo "benchmark failed with status $bench_status" >&2
    exit "$bench_status"
fi
if [[ "$power_metrics_valid" -ne 1 ]]; then
    echo "power benchmark produced no parseable samples" >&2
    exit 1
fi

write_power_report "$report" "$timestamp" "$power_summary" "$bench_log"
publish_power_artifacts \
    "$stage_dir" \
    "$report" \
    "$bench_log" \
    "$power_log" \
    "$final_report" \
    "$final_bench_log" \
    "$final_power_log"
stage_dir=""
trap - EXIT INT TERM

echo "report: $final_report"
