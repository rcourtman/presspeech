#!/usr/bin/env bash
# Reproduce and measure final-word retention on short push-to-talk clips.
#
# The script uses synthetic local TTS so the report can be shared without
# private dictation audio. It trims trailing silence, cuts the end of each
# phrase to simulate an early key release, then runs presspeech-bench with
# configurable backends, capture grace, and Unified trailing-silence padding.

set -euo pipefail

# Metrics are parsed and emitted as dot-decimal machine-readable values.
export LC_ALL=C

SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$SCRIPT_PATH")"
dependency_provenance="$(python3 ./dependency-provenance.py)" || exit 1
IFS=$'\t' read -r FLUID_REVISION PRODUCTION_FLUID_REVISION BASELINE_DEPENDENCY <<<"$dependency_provenance"

OUTDIR="tail-results"
VOICE="Samantha"
TRIALS="1"
CUT_MS_LIST="100 150 200"
CAPTURE_GRACE_MS_LIST="0"
UNIFIED_TRAILING_MS_LIST="0 250"
INCLUDE_V3_BASELINE=1
REQUIRE_CANDIDATE_PASS=1
PRODUCTION_V3_ONLY=0
CANDIDATE_UNIFIED_TRAILING_MS="250"
MAX_CANDIDATE_WER="20.0"
SELF_TEST=0
EXPERIMENT_ENVIRONMENT_STATE="unreported"
CAPTURE_GRACE_MS_LIST_SET=0
CLIPS=(
    "why|Why would anyone be not sure."
    "done|Okay, let's get that done."
)
KEEP_TEMP=0
tmpdir=""
stage_dir=""

usage() {
    cat <<'USAGE'
usage: ./run-tail-word-regression.sh [options]

Options:
  --out-dir <path>              report directory (default: tail-results)
  --voice <name>                macOS say voice (default: Samantha)
  --trials <n>                  measured trials per case (default: 1)
  --cut-ms-list <list>          simulated early-release cuts, comma or space separated (default: 100 150 200)
  --capture-grace-ms-list <list>
                                post-release capture grace to simulate, comma or space separated (default: 0)
  --unified-trailing-ms-list <list>
                                Unified silence padding sweep, comma or space separated (default: 0 250)
  --production-v3-only          record the production-v3 short-clip baseline only;
                                defaults capture-grace coverage to 80 and 400 ms
  --skip-v3-baseline            do not run the v3 baseline rows
  --no-threshold                write the report but do not fail on candidate-threshold misses
  --keep-temp                   keep generated audio and raw bench logs
  --self-test                   run parser and threshold self-tests only
  -h, --help                    show this help

The candidate threshold is checked for the Unified evaluation setting:
250 ms synthetic trailing silence, 0 ms capture grace, final word retained,
and max WER <= 20.0% on the known regression cases.
The production-v3-only mode checks evidence completeness and SDK environment,
but is report-only for WER and final-word retention; it does not qualify the
model or reproduce the app's live RMS endpointer.
USAGE
}

need_value() {
    if [[ $# -lt 2 || -z "$2" || "$2" == --* ]]; then
        echo "$1 requires a value" >&2
        usage >&2
        exit 2
    fi
}

normalize_list() {
    printf '%s' "$1" | tr ',' ' '
}

validate_ms_list() {
    local label="$1"
    local raw="$2"
    local value
    if [[ -z "$(normalize_list "$raw" | tr -d '[:space:]')" ]]; then
        echo "$label must contain at least one non-negative integer millisecond value" >&2
        exit 2
    fi
    for value in $(normalize_list "$raw"); do
        if ! [[ "$value" =~ ^[0-9]+$ ]]; then
            echo "$label must contain only non-negative integer millisecond values" >&2
            exit 2
        fi
    done
}

effective_cut_ms() {
    local cut_ms="$1"
    local grace_ms="$2"
    if [[ "$grace_ms" -ge "$cut_ms" ]]; then
        printf '0'
    else
        printf '%d' $((cut_ms - grace_ms))
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

extract_max_wer_percent() {
    local log_file="$1"
    awk '
        match($0, /\[WER [0-9]+([.][0-9]+)?%\]/) {
            value = substr($0, RSTART + 5, RLENGTH - 7)
            if (max == "" || value > max) max = value
        }
        END { if (max == "") print "unknown"; else print max }
    ' "$log_file"
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
    local final_report="$4"
    local final_tsv="$5"

    if [[ ! -f "$staged_report" || ! -f "$staged_tsv" ]]; then
        echo "tail-word staging artifacts are incomplete" >&2
        return 1
    fi
    if [[ -e "$final_report" || -e "$final_tsv" ]]; then
        echo "refusing to replace existing tail-word artifacts" >&2
        return 1
    fi

    # Publish the human-facing report last. If its move unexpectedly fails,
    # remove only the TSV created here so no partial run looks final.
    if ! mv "$staged_tsv" "$final_tsv"; then
        return 1
    fi
    if ! mv "$staged_report" "$final_report"; then
        rm -f -- "$final_tsv"
        return 1
    fi
    rmdir "$stage_dir"
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

write_wav_variant() {
    local input="$1"
    local output="$2"
    local cut_ms="$3"
    local trim_mode="$4"

    python3 - "$input" "$output" "$cut_ms" "$trim_mode" <<'PY'
import array
import struct
import sys
from pathlib import Path

input_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
cut_ms = int(sys.argv[3])
trim_mode = sys.argv[4] == "trim"

blob = input_path.read_bytes()
if blob[:4] != b"RIFF" or blob[8:12] != b"WAVE":
    raise SystemExit(f"{input_path} is not a RIFF/WAVE file")

pos = 12
fmt = None
data = None
while pos + 8 <= len(blob):
    chunk_id = blob[pos:pos + 4]
    size = struct.unpack_from("<I", blob, pos + 4)[0]
    start = pos + 8
    end = start + size
    chunk = blob[start:end]
    if chunk_id == b"fmt ":
        fmt = chunk
    elif chunk_id == b"data":
        data = chunk
    pos = end + (size % 2)

if fmt is None or data is None:
    raise SystemExit(f"{input_path} is missing fmt or data chunks")

audio_format, channels, sample_rate, _, _, bits_per_sample = struct.unpack_from("<HHIIHH", fmt, 0)
if audio_format not in (3, 65534) or channels != 1 or bits_per_sample != 32:
    raise SystemExit(
        f"{input_path} must be 16 kHz mono Float32 WAV; got format={audio_format}, "
        f"channels={channels}, bits={bits_per_sample}"
    )

samples = array.array("f")
samples.frombytes(data[:len(data) - (len(data) % 4)])
if sys.byteorder != "little":
    samples.byteswap()

if trim_mode and samples:
    threshold = 0.0001
    active = [i for i, sample in enumerate(samples) if abs(sample) > threshold]
    if active:
        keep_before = int(sample_rate * 0.050)
        keep_after = int(sample_rate * 0.020)
        start = max(0, active[0] - keep_before)
        end = min(len(samples), active[-1] + 1 + keep_after)
        samples = samples[start:end]

if cut_ms > 0 and samples:
    cut_samples = int(round(sample_rate * cut_ms / 1000.0))
    if cut_samples >= len(samples):
        samples = array.array("f")
    else:
        samples = samples[:-cut_samples]

if sys.byteorder != "little":
    samples.byteswap()
data_out = samples.tobytes()
fmt_out = struct.pack("<HHIIHH", 3, 1, sample_rate, sample_rate * 4, 4, 32)
riff_size = 4 + (8 + len(fmt_out)) + (8 + len(data_out))
output_path.write_bytes(
    b"RIFF" + struct.pack("<I", riff_size) + b"WAVE" +
    b"fmt " + struct.pack("<I", len(fmt_out)) + fmt_out +
    b"data" + struct.pack("<I", len(data_out)) + data_out
)
PY
}

# Assess measured rows even for --no-threshold; that option changes only exit
# enforcement. Missing sweep cases must never satisfy the Unified prerequisite.
append_candidate_gate() {
    local report="$1"
    local results="$2"
    local blockers
    blockers="$(awk -F '\t' -v limit="$MAX_CANDIDATE_WER" \
        -v trailing="$CANDIDATE_UNIFIED_TRAILING_MS" '
        BEGIN { split("why:100 why:150 why:200 done:150 done:200", keys, " ")
                for (i = 1; i <= 5; i++) required[keys[i]] = 1 }
        NR > 1 && $3 == "0" && $5 == "unified" && $6 == trailing {
            key = $1 ":" $2
            if (key in required) {
                seen[key] = 1
                if ($8 != "true" || $7 !~ /^[0-9]+([.][0-9]+)?$/ || $7 + 0 > limit)
                    failed[key] = 1
            }
        }
        END {
            for (i = 1; i <= 5; i++) {
                key = keys[i]
                if (!(key in seen)) print "missing required case " key
                else if (key in failed) print "metric threshold failed for " key
            }
        }' "$results")"
    if [[ "$EXPERIMENT_ENVIRONMENT_STATE" != "default" ]]; then
        blockers="${blockers}${blockers:+$'\n'}inherited SDK environment is $EXPERIMENT_ENVIRONMENT_STATE"
    fi
    {
        echo
        echo "## Candidate prerequisite"
        echo
        echo "Inherited SDK environment: $EXPERIMENT_ENVIRONMENT_STATE."
        echo "All five known cases must satisfy the measured thresholds with default inherited SDK controls. This synthetic check is not production or whole-app qualification."
        if [[ -n "$blockers" ]]; then
            echo "Candidate prerequisite blocked:"
            printf '%s\n' "$blockers" | sed 's/^/- /'
        else
            echo "Candidate threshold passed."
        fi
        if [[ "$REQUIRE_CANDIDATE_PASS" -eq 0 ]]; then
            echo "Exploratory run: threshold exit enforcement disabled; this does not waive any prerequisite."
        fi
    } >>"$report"
    [[ -z "$blockers" ]]
}

append_production_v3_baseline() {
    local report="$1"
    local results="$2"
    local -a blockers=()
    local entry phrase cut_ms grace_ms count

    for entry in "${CLIPS[@]}"; do
        phrase="${entry%%|*}"
        for cut_ms in $(normalize_list "$CUT_MS_LIST"); do
            for grace_ms in $(normalize_list "$CAPTURE_GRACE_MS_LIST"); do
                count="$(awk -F '\t' -v phrase="$phrase" -v cut="$cut_ms" \
                    -v grace="$grace_ms" \
                    '$1 == phrase && $2 == cut && $3 == grace && $5 == "v3" && $6 == "na" { n++ }
                     END { print n + 0 }' "$results")"
                if [[ "$count" != "1" ]]; then
                    blockers+=("expected one production-v3 row for $phrase cut=$cut_ms grace=$grace_ms; found $count")
                fi
            done
        done
    done
    if [[ "$EXPERIMENT_ENVIRONMENT_STATE" != "default" ]]; then
        blockers+=("inherited SDK environment is $EXPERIMENT_ENVIRONMENT_STATE")
    fi

    {
        echo
        echo "## Production-v3 short-clip baseline"
        echo
        echo "This diagnostic records production-v3 WER, final-word retention, and p50 latency. It asserts complete requested coverage and a default inherited SDK environment, but applies no WER or retention threshold. It models retained audio with fixed grace values, not the app's live RMS endpointer."
        if [[ "${#blockers[@]}" -gt 0 ]]; then
            echo "Baseline evidence incomplete:"
            printf '%s\n' "${blockers[@]}" | sed 's/^/- /'
        else
            echo "Baseline evidence complete; report-only, not a model qualification pass."
        fi
    } >>"$report"
    [[ "${#blockers[@]}" -eq 0 ]]
}

run_self_test() {
    local self_tmp
    self_tmp="$(mktemp -d "${TMPDIR:-/tmp}/presspeech-tail-self-test.XXXXXX")"
    trap 'rm -rf "$self_tmp"' EXIT INT TERM

    local mock="$self_tmp/mock.log"
    {
        echo 'latency:  p50=  123.4 ms  min=  120.0 ms  max=  130.0 ms'
        echo 'transcript: [WER 16.7%] [final-word retained=false expected="sure" actual-last="not"] "literal [WER 99.0%]"'
    } >"$mock"

    assert_eq "$(extract_final_word_retained "$mock")" "false" "retention parser"
    assert_eq "$(extract_max_wer_percent "$mock")" "16.7" "WER parser"
    assert_eq "$(extract_p50_ms "$mock")" "123.4" "latency parser"
    assert_eq "$(extract_max_wer_percent /dev/null)" "unknown" "missing WER parser"
    validate_metrics max-WER 16.7 final-word-retained false p50 123.4

    local validation_log="$self_tmp/validation.log"
    if validate_metrics max-WER unknown p50 "" >"$validation_log" 2>&1; then
        echo "self-test expected missing metrics to fail validation" >&2
        exit 1
    fi
    if ! grep -Fq -- "benchmark output missing required metrics: max-WER,p50" "$validation_log"; then
        echo "self-test expected missing-metrics error message" >&2
        exit 1
    fi
    assert_eq "$(effective_cut_ms 150 50)" "100" "effective cut"
    assert_eq "$(effective_cut_ms 50 150)" "0" "grace caps at full tail"

    local gate_tsv="$self_tmp/gate.tsv" gate_report="$self_tmp/gate.md" state
    printf 'phrase\tcut\tgrace\teffective\tbackend\ttrailing\twer\tretained\tlatency\n' >"$gate_tsv"
    local key
    for key in why:100 why:150 why:200 done:150 done:200; do
        printf '%s\t%s\t0\t0\tunified\t250\t0.0\ttrue\t1\n' "${key%:*}" "${key#*:}" >>"$gate_tsv"
    done
    EXPERIMENT_ENVIRONMENT_STATE="default"
    append_candidate_gate "$gate_report" "$gate_tsv"
    for state in configured unreported pending; do
        EXPERIMENT_ENVIRONMENT_STATE="$state"
        : >"$gate_report"
        if append_candidate_gate "$gate_report" "$gate_tsv"; then
            echo "self-test accepted $state otherwise-passing tail metrics" >&2; exit 1
        fi
        if grep -Fq 'Candidate threshold passed.' "$gate_report"; then exit 1; fi
    done
    EXPERIMENT_ENVIRONMENT_STATE="default"
    head -n 5 "$gate_tsv" >"$self_tmp/incomplete.tsv"
    if append_candidate_gate "$gate_report" "$self_tmp/incomplete.tsv"; then
        echo 'self-test accepted an incomplete required sweep' >&2; exit 1
    fi
    sed 's/true/false/g' "$gate_tsv" >"$self_tmp/failed.tsv"
    REQUIRE_CANDIDATE_PASS=0
    : >"$gate_report"
    if append_candidate_gate "$gate_report" "$self_tmp/failed.tsv"; then
        echo 'self-test accepted failing metrics with --no-threshold' >&2; exit 1
    fi
    if grep -Fq 'Candidate threshold passed.' "$gate_report"; then exit 1; fi
    grep -Fq 'Exploratory run:' "$gate_report"
    REQUIRE_CANDIDATE_PASS=1
    EXPERIMENT_ENVIRONMENT_STATE="unreported"

    local stage_dir="$self_tmp/staged"
    local final_dir="$self_tmp/published"
    mkdir -p "$stage_dir" "$final_dir"
    printf 'complete report\n' >"$stage_dir/report.md"
    printf 'header\nrow\n' >"$stage_dir/results.tsv"
    publish_report_artifacts \
        "$stage_dir" \
        "$stage_dir/report.md" \
        "$stage_dir/results.tsv" \
        "$final_dir/report.md" \
        "$final_dir/results.tsv"
    if ! grep -Fq -- "complete report" "$final_dir/report.md" ||
        ! grep -Fq -- "row" "$final_dir/results.tsv"; then
        echo "self-test expected complete tail-word artifacts to be published" >&2
        exit 1
    fi
    if [[ -e "$stage_dir" ]]; then
        echo "self-test expected successful report staging cleanup" >&2
        exit 1
    fi

    stage_dir="$self_tmp/collision-stage"
    mkdir -p "$stage_dir"
    printf 'new report\n' >"$stage_dir/report.md"
    printf 'new results\n' >"$stage_dir/results.tsv"
    local collision_log="$self_tmp/collision.log"
    if publish_report_artifacts \
        "$stage_dir" \
        "$stage_dir/report.md" \
        "$stage_dir/results.tsv" \
        "$final_dir/report.md" \
        "$final_dir/other-results.tsv" >"$collision_log" 2>&1; then
        echo "self-test expected report publication collision to fail" >&2
        exit 1
    fi
    if ! grep -Fq -- "refusing to replace existing tail-word artifacts" "$collision_log" ||
        ! grep -Fq -- "complete report" "$final_dir/report.md" ||
        ! grep -Fq -- "new report" "$stage_dir/report.md"; then
        echo "self-test expected collision to preserve staged and published artifacts" >&2
        exit 1
    fi

    validate_ms_list "--cut-ms-list" "0,100 250"

    local bad_list_log="$self_tmp/bad-list.log"
    if bash "$SCRIPT_PATH" --cut-ms-list nope --self-test >"$bad_list_log" 2>&1; then
        echo "self-test expected bad --cut-ms-list to fail" >&2
        exit 1
    fi
    if ! grep -Fq -- "--cut-ms-list must contain only non-negative integer" "$bad_list_log"; then
        echo "self-test expected bad-list error message" >&2
        exit 1
    fi

    local baseline_tsv="$self_tmp/baseline.tsv"
    local baseline_report="$self_tmp/baseline.md"
    printf 'phrase\tcut_ms\tcapture_grace_ms\teffective_cut_ms\tbackend\tunified_trailing_ms\tmax_wer_percent\tfinal_word_retained\tp50_ms\n' >"$baseline_tsv"
    local entry phrase cut_ms grace_ms effective
    for entry in "${CLIPS[@]}"; do
        phrase="${entry%%|*}"
        for cut_ms in 100 150 200; do
            for grace_ms in 80 400; do
                effective="$(effective_cut_ms "$cut_ms" "$grace_ms")"
                printf '%s\t%s\t%s\t%s\tv3\tna\t0.0\ttrue\t1\n' \
                    "$phrase" "$cut_ms" "$grace_ms" "$effective" >>"$baseline_tsv"
            done
        done
    done
    CUT_MS_LIST="100 150 200"
    CAPTURE_GRACE_MS_LIST="80 400"
    EXPERIMENT_ENVIRONMENT_STATE="default"
    : >"$baseline_report"
    if ! append_production_v3_baseline "$baseline_report" "$baseline_tsv" ||
        ! grep -Fq 'Baseline evidence complete; report-only' "$baseline_report"; then
        echo "self-test expected complete production-v3 baseline evidence to pass" >&2
        exit 1
    fi
    sed '$d' "$baseline_tsv" >"$self_tmp/incomplete-baseline.tsv"
    if append_production_v3_baseline "$baseline_report" "$self_tmp/incomplete-baseline.tsv"; then
        echo "self-test accepted incomplete production-v3 baseline coverage" >&2
        exit 1
    fi
    EXPERIMENT_ENVIRONMENT_STATE="configured"
    if append_production_v3_baseline "$baseline_report" "$baseline_tsv"; then
        echo "self-test accepted configured SDK environment for production-v3 baseline" >&2
        exit 1
    fi
    EXPERIMENT_ENVIRONMENT_STATE="unreported"

    rm -rf "$self_tmp"
    trap - EXIT INT TERM
    echo "tail-word regression self-test passed"
}

cleanup() {
    if [[ "$KEEP_TEMP" -eq 0 && -n "$tmpdir" ]]; then
        rm -rf "$tmpdir"
    elif [[ -n "$tmpdir" ]]; then
        echo "kept temp files: $tmpdir"
    fi
    if [[ -n "$stage_dir" ]]; then
        rm -rf -- "$stage_dir"
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --out-dir)
            need_value "$@"
            OUTDIR="$2"
            shift 2
            ;;
        --voice)
            need_value "$@"
            VOICE="$2"
            shift 2
            ;;
        --trials)
            need_value "$@"
            TRIALS="$2"
            shift 2
            ;;
        --cut-ms-list)
            need_value "$@"
            CUT_MS_LIST="$2"
            shift 2
            ;;
        --capture-grace-ms-list)
            need_value "$@"
            CAPTURE_GRACE_MS_LIST="$2"
            CAPTURE_GRACE_MS_LIST_SET=1
            shift 2
            ;;
        --unified-trailing-ms-list)
            need_value "$@"
            UNIFIED_TRAILING_MS_LIST="$2"
            shift 2
            ;;
        --skip-v3-baseline)
            INCLUDE_V3_BASELINE=0
            shift
            ;;
        --production-v3-only)
            PRODUCTION_V3_ONLY=1
            shift
            ;;
        --no-threshold)
            REQUIRE_CANDIDATE_PASS=0
            shift
            ;;
        --keep-temp)
            KEEP_TEMP=1
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

if [[ "$PRODUCTION_V3_ONLY" -eq 1 ]]; then
    if [[ "$INCLUDE_V3_BASELINE" -eq 0 ]]; then
        echo "--production-v3-only cannot be combined with --skip-v3-baseline" >&2
        exit 2
    fi
    if [[ "$CAPTURE_GRACE_MS_LIST_SET" -eq 0 ]]; then
        CAPTURE_GRACE_MS_LIST="80 400"
    fi
fi

validate_ms_list "--cut-ms-list" "$CUT_MS_LIST"
validate_ms_list "--capture-grace-ms-list" "$CAPTURE_GRACE_MS_LIST"
validate_ms_list "--unified-trailing-ms-list" "$UNIFIED_TRAILING_MS_LIST"

if [[ "$SELF_TEST" -eq 1 ]]; then
    run_self_test
    exit 0
fi

if ! [[ "$TRIALS" =~ ^[0-9]+$ ]] || [[ "$TRIALS" -lt 1 ]]; then
    echo "--trials must be a positive integer" >&2
    exit 2
fi

if ! command -v say >/dev/null 2>&1; then
    echo "macOS say is required to synthesize fixtures" >&2
    exit 1
fi

if ! command -v afconvert >/dev/null 2>&1; then
    echo "afconvert is required to normalize fixtures" >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required to trim and cut Float32 WAV fixtures" >&2
    exit 1
fi

mkdir -p "$OUTDIR"
tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/presspeech-tail-word.XXXXXX")"
trap cleanup EXIT INT TERM

echo "building presspeech-bench..."
swift build -c release >/dev/null
built_dependency_provenance="$(python3 ./dependency-provenance.py --verify-built)" || exit 1
if [[ "$built_dependency_provenance" != "$dependency_provenance" ]]; then
    echo "dependency provenance changed during benchmark build" >&2
    exit 1
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
final_report="$OUTDIR/$timestamp-tail-word.md"
final_tsv="$OUTDIR/$timestamp-tail-word.tsv"
if [[ -e "$final_report" || -e "$final_tsv" ]]; then
    echo "tail-word artifacts already exist for timestamp $timestamp" >&2
    exit 1
fi
reserved_stage_dir="$OUTDIR/.$timestamp-tail-word.incomplete"
if ! mkdir "$reserved_stage_dir"; then
    echo "could not reserve tail-word output for timestamp $timestamp" >&2
    exit 1
fi
stage_dir="$reserved_stage_dir"
report="$stage_dir/report.md"
tsv="$stage_dir/results.tsv"

{
    echo "# Presspeech Tail-Word Regression"
    echo
    echo "- Date: $timestamp"
    echo "- Benchmark FluidAudio revision: $FLUID_REVISION"
    echo "- Application FluidAudio revision: $PRODUCTION_FLUID_REVISION"
    echo "- Baseline dependency: $BASELINE_DEPENDENCY (not whole-app qualification)"
    echo "- Voice: $VOICE"
    echo "- Trials per case: $TRIALS"
    echo "- Cut ms list: $CUT_MS_LIST"
    echo "- Capture grace ms list: $CAPTURE_GRACE_MS_LIST"
    if [[ "$PRODUCTION_V3_ONLY" -eq 1 ]]; then
        echo "- Unified trailing silence ms list: not evaluated"
    else
        echo "- Unified trailing silence ms list: $UNIFIED_TRAILING_MS_LIST"
    fi
    echo "- v3 baseline: $([[ "$INCLUDE_V3_BASELINE" -eq 1 ]] && echo included || echo skipped)"
    echo "- Run scope: $([[ "$PRODUCTION_V3_ONLY" -eq 1 ]] && echo production-v3-only || echo candidate comparison)"
    echo
    echo "The cut column simulates releasing the key before the phrase finishes."
    echo "Capture grace simulates Presspeech continuing to record briefly after release."
    if [[ "$PRODUCTION_V3_ONLY" -eq 0 ]]; then
        echo "Unified trailing silence is synthetic zero padding added before the Unified model sees the audio."
    fi
    echo
    if [[ "$PRODUCTION_V3_ONLY" -eq 1 ]]; then
        echo "Production-v3 baseline: requested cut/grace matrix, report-only for WER and final-word retention."
    else
        echo "Candidate threshold: Unified @ ${CANDIDATE_UNIFIED_TRAILING_MS} ms, 0 ms capture grace, final word retained, max WER <= ${MAX_CANDIDATE_WER}% on the known regression cases."
    fi
    echo
    echo "| Phrase | Cut ms | Grace ms | Effective cut ms | Backend | Unified trailing ms | Max WER % | Final word retained | p50 ms |"
    echo "|---|---:|---:|---:|---|---:|---:|---|---:|"
} >"$report"

printf 'phrase\tcut_ms\tcapture_grace_ms\teffective_cut_ms\tbackend\tunified_trailing_ms\tmax_wer_percent\tfinal_word_retained\tp50_ms\n' >"$tsv"

EXPERIMENT_ENVIRONMENT_STATE="pending"

for entry in "${CLIPS[@]}"; do
    phrase="${entry%%|*}"
    text="${entry#*|}"
    raw_aiff="$tmpdir/$phrase.raw.aiff"
    raw_wav="$tmpdir/$phrase.raw.wav"
    trimmed_wav="$tmpdir/$phrase.trimmed.wav"

    echo "synthesizing $phrase..."
    say -v "$VOICE" -o "$raw_aiff" "$text"
    afconvert -f WAVE -d LEF32@16000 "$raw_aiff" "$raw_wav"
    write_wav_variant "$raw_wav" "$trimmed_wav" "0" "trim"

    for cut_ms in $(normalize_list "$CUT_MS_LIST"); do
        for grace_ms in $(normalize_list "$CAPTURE_GRACE_MS_LIST"); do
            effective_cut="$(effective_cut_ms "$cut_ms" "$grace_ms")"
            case_wav="$tmpdir/$phrase-cut${cut_ms}-grace${grace_ms}.wav"
            write_wav_variant "$trimmed_wav" "$case_wav" "$effective_cut" "notrim"
            python3 ./audio-input-evidence.py --audio "$case_wav" >/dev/null

            backends=()
            if [[ "$PRODUCTION_V3_ONLY" -eq 1 ]]; then
                backends+=( "v3:na" )
            else
                if [[ "$INCLUDE_V3_BASELINE" -eq 1 ]]; then
                    backends+=( "v3:na" )
                fi
                for trailing_ms in $(normalize_list "$UNIFIED_TRAILING_MS_LIST"); do
                    backends+=( "unified:$trailing_ms" )
                done
            fi

            for backend_entry in "${backends[@]}"; do
                backend="${backend_entry%%:*}"
                trailing_ms="${backend_entry#*:}"
                log_file="$tmpdir/$phrase-cut${cut_ms}-grace${grace_ms}-${backend}-${trailing_ms}.log"
                bench_args=( ".build/release/presspeech-bench" "--file" "$case_wav" "--backend" "$backend" "--trials" "$TRIALS" "--ref" "$text" )
                if [[ "$backend" == "unified" ]]; then
                    bench_args+=( "--unified-trailing-silence-ms" "$trailing_ms" )
                fi

                echo "benchmarking $phrase cut=${cut_ms}ms grace=${grace_ms}ms backend=$backend trailing=${trailing_ms}..."
                if ! "${bench_args[@]}" >"$log_file" 2>&1; then
                    cat "$log_file" >&2
                    echo "benchmark failed for $phrase cut=$cut_ms grace=$grace_ms backend=$backend trailing=$trailing_ms" >&2
                    exit 1
                fi

                python3 ./audio-input-evidence.py --audio "$case_wav" --log "$log_file" >>"$log_file"
                EXPERIMENT_ENVIRONMENT_STATE="$(python3 ./experiment-environment.py --log "$log_file" --previous "$EXPERIMENT_ENVIRONMENT_STATE")"

                wer="$(extract_max_wer_percent "$log_file")"
                retained="$(extract_final_word_retained "$log_file")"
                p50="$(extract_p50_ms "$log_file")"
                [[ -n "$p50" ]] || p50="unknown"
                if ! validate_metrics max-WER "$wer" final-word-retained "$retained" p50 "$p50"; then
                    echo "invalid benchmark output for $phrase cut=$cut_ms grace=$grace_ms backend=$backend trailing=$trailing_ms" >&2
                    exit 1
                fi

                printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
                    "$phrase" "$cut_ms" "$grace_ms" "$effective_cut" "$backend" "$trailing_ms" "$wer" "$retained" "$p50" >>"$tsv"
                printf '| `%s` | %s | %s | %s | `%s` | %s | %s | %s | %s |\n' \
                    "$phrase" "$cut_ms" "$grace_ms" "$effective_cut" "$backend" "$trailing_ms" "$wer" "$retained" "$p50" >>"$report"

            done
        done
    done
done

candidate_gate_passed=1
if [[ "$PRODUCTION_V3_ONLY" -eq 1 ]]; then
    if ! append_production_v3_baseline "$report" "$tsv"; then
        candidate_gate_passed=0
    fi
elif ! append_candidate_gate "$report" "$tsv"; then
    candidate_gate_passed=0
fi

publish_report_artifacts \
    "$stage_dir" \
    "$report" \
    "$tsv" \
    "$final_report" \
    "$final_tsv"
stage_dir=""

echo "report: $final_report"
echo "tsv: $final_tsv"

if [[ "$candidate_gate_passed" -ne 1 &&
      ( "$PRODUCTION_V3_ONLY" -eq 1 || "$REQUIRE_CANDIDATE_PASS" -eq 1 ) ]]; then
    if [[ "$PRODUCTION_V3_ONLY" -eq 1 ]]; then
        echo "production-v3 tail baseline evidence incomplete; inspect the report" >&2
    else
        echo "tail-word candidate prerequisite blocked; inspect the report" >&2
    fi
    exit 1
fi
