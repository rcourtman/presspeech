#!/usr/bin/env bash
# Compare production Parakeet v3 with unbiased and vocabulary-rescored
# sliding-window v3 on the same multilingual dictation fixtures.

set -euo pipefail

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
  --self-test              run parser, aggregation, and redaction tests only
  -h, --help               show this help

The three variants run in separate processes so memory measurements stay
isolated:
  v3             production AsrManager path
  sliding-v3     SlidingWindowAsrManager without vocabulary boosting
  sliding-vocab  the same sliding path plus the auxiliary CTC rescorer

Critical-term recall is exact after case/punctuation normalization. List each
inflected output form separately; FluidAudio aliases are alternate matches,
not morphological generators.
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

extract_max_wer_percent() {
    local log_file="$1"
    grep -Eo 'WER [0-9]+([.][0-9]+)?%' "$log_file" \
        | sed -E 's/WER ([0-9.]+)%/\1/' \
        | awk 'BEGIN { max = "" } { if (max == "" || $1 > max) max = $1 } END { if (max == "") print "unknown"; else print max }'
}

extract_critical_metrics() {
    local log_file="$1"
    sed -nE 's/.*critical-terms matched=([0-9]+) total=([0-9]+) recall=([0-9.]+)%.*/\1\t\2\t\3/p' "$log_file" \
        | sort -t $'\t' -k3,3n \
        | head -n 1
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
                wer_sum += $3
                if (wer_seen == 0 || $3 > worst_wer) worst_wer = $3
                wer_seen += 1
            }
            if ($4 != "unknown" && $5 != "unknown") {
                critical_matched += $4
                critical_total += $5
            }
            if ($7 != "unknown") { p50_sum += $7; p50_seen += 1 }
            if ($8 != "unknown" && (peak_seen == 0 || $8 > max_peak)) {
                max_peak = $8; peak_seen += 1
            }
            if ($9 != "unknown" && (cache_seen == 0 || $9 > max_cache)) {
                max_cache = $9; cache_seen += 1
            }
            if ($10 != "unknown") { prep_sum += $10; prep_seen += 1 }
        }
        END {
            avg_wer = wer_seen ? sprintf("%.2f", wer_sum / wer_seen) : "unknown"
            worst = wer_seen ? sprintf("%.1f", worst_wer) : "unknown"
            critical = critical_total ? sprintf("%.1f", critical_matched / critical_total * 100) : "unknown"
            avg_p50 = p50_seen ? sprintf("%.1f", p50_sum / p50_seen) : "unknown"
            peak = peak_seen ? sprintf("%.1f", max_peak) : "unknown"
            cache = cache_seen ? sprintf("%.1f", max_cache) : "unknown"
            prep = prep_seen ? sprintf("%.1f", prep_sum / prep_seen) : "unknown"
            printf("| `%s` | %d | %s | %s | %d/%d | %s | %s | %s | %s | %s |\n", variant, count, avg_wer, worst, critical_matched, critical_total, critical, avg_p50, peak, cache, prep)
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
    tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/presspeech-vocabulary-self-test.XXXXXX")"
    trap 'rm -rf "$tmpdir"' EXIT INT TERM

    local log="$tmpdir/mock.log"
    {
        echo '  ready in 1234.5 ms (model load + 1 warmup inference)'
        echo '  model-cache: total=812.3 MB components=parakeet-v3=600.0 MB,ctc-110m=212.3 MB'
        echo '    latency:  p50=  140.2 ms  min=  138.0 ms  max=  145.0 ms'
        echo '    memory:   peak=  88.4 MB  Δ-from-start=  80.0 MB'
        echo '    transcript: [WER 12.5%] [critical-terms matched=7 total=8 recall=87.5%] <redacted 42 chars>'
    } >"$log"
    assert_eq "$(extract_max_wer_percent "$log")" "12.5" "WER parser"
    assert_eq "$(extract_critical_metrics "$log")" $'7\t8\t87.5' "critical-term parser"
    assert_eq "$(extract_p50_ms "$log")" "140.2" "latency parser"
    assert_eq "$(extract_peak_mb "$log")" "88.4" "memory parser"
    assert_eq "$(extract_cache_mb "$log")" "812.3" "cache parser"
    assert_eq "$(extract_prepare_ms "$log")" "1234.5" "prepare parser"

    local filtered_log="$tmpdir/filtered.log"
    run_benchmark_to_log "$filtered_log" printf '%s\n' \
        "[10:00:00.000] [WARN] [FluidAudio.CustomVocabulary] Term 'Szypański': contains diacritics"
    assert_not_contains "$filtered_log" "Szypański"
    assert_contains "$filtered_log" "redacted vocabulary diagnostic"

    local tsv="$tmpdir/results.tsv"
    {
        printf 'clip_id\tvariant\twer_percent\tcritical_matched\tcritical_total\tcritical_recall_percent\tp50_ms\tpeak_mb\tcache_mb\tprepare_ms\n'
        printf '001\tv3\t10.0\t1\t2\t50.0\t100.0\t40.0\t600.0\t1000.0\n'
        printf '002\tv3\t20.0\t2\t2\t100.0\t120.0\t42.0\t600.0\t1100.0\n'
    } >"$tsv"
    local summary="$tmpdir/summary.md"
    summary_row "$tsv" v3 >"$summary"
    assert_contains "$summary" '| `v3` | 2 | 15.00 | 20.0 | 3/4 | 75.0 | 110.0 | 42.0 | 600.0 | 1050.0 |'

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
cleanup() { rm -rf "$tmpdir"; }
trap cleanup EXIT INT TERM

echo "building presspeech-bench..."
swift build -c release >/dev/null

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
report="$OUTDIR/$timestamp-vocabulary-bias.md"
tsv="$OUTDIR/$timestamp-vocabulary-bias.tsv"
raw_dir="$OUTDIR/$timestamp-vocabulary-bias-logs"
mkdir -p "$raw_dir"

printf 'clip_id\tvariant\twer_percent\tcritical_matched\tcritical_total\tcritical_recall_percent\tp50_ms\tpeak_mb\tcache_mb\tprepare_ms\n' >"$tsv"

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
    echo "> Production v3, unbiased sliding v3, and CTC-rescored sliding v3 run"
    echo "> in separate processes. Critical-term recall counts exact canonical"
    echo "> surface forms after case/punctuation normalization. Model cache is"
    echo "> logical on-disk size after preparation, not measured network traffic."
    echo
    echo "## Per-Clip Results"
    echo
    echo "| Clip | Variant | WER % | Critical hits | Critical recall % | p50 ms | Peak MB | Cache MB | Prepare ms |"
    echo "|---|---|---:|---:|---:|---:|---:|---:|---:|"
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

    for variant in v3 sliding-v3 sliding-vocab; do
        log_file="$raw_dir/$clip_id-$variant.bench.txt"
        bench_args=(
            ".build/release/presspeech-bench"
            "--file" "$normalized"
            "--backend" "$variant"
            "--language" "$LANGUAGE"
            "--critical-terms" "$CRITICAL_TERMS"
            "--trials" "$TRIALS"
        )
        if [[ "$variant" == "sliding-vocab" ]]; then
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

        wer="$(extract_max_wer_percent "$log_file")"
        critical="$(extract_critical_metrics "$log_file")"
        if [[ -n "$critical" ]]; then
            IFS=$'\t' read -r critical_matched critical_total critical_recall <<<"$critical"
        else
            critical_matched="unknown"
            critical_total="unknown"
            critical_recall="unknown"
        fi
        p50="$(extract_p50_ms "$log_file")"
        peak="$(extract_peak_mb "$log_file")"
        cache="$(extract_cache_mb "$log_file")"
        prepare="$(extract_prepare_ms "$log_file")"
        [[ -n "$p50" ]] || p50="unknown"
        [[ -n "$peak" ]] || peak="unknown"
        [[ -n "$cache" ]] || cache="unknown"
        [[ -n "$prepare" ]] || prepare="unknown"

        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$clip_id" "$variant" "$wer" "$critical_matched" "$critical_total" \
            "$critical_recall" "$p50" "$peak" "$cache" "$prepare" >>"$tsv"
        printf '| `%s` | `%s` | %s | %s/%s | %s | %s | %s | %s | %s |\n' \
            "$clip_id" "$variant" "$wer" "$critical_matched" "$critical_total" \
            "$critical_recall" "$p50" "$peak" "$cache" "$prepare" >>"$report"
    done
done

{
    echo
    echo "## Summary"
    echo
    echo "| Variant | Clips | Avg WER % | Worst WER % | Critical hits | Critical recall % | Avg p50 ms | Max peak MB | Cache MB | Avg prepare ms |"
    echo "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
    summary_row "$tsv" v3
    summary_row "$tsv" sliding-v3
    summary_row "$tsv" sliding-vocab
    echo
    echo "Raw bench logs: $(path_label "$raw_dir")"
    echo "Machine-readable TSV: $(path_label "$tsv")"
} >>"$report"

echo "report: $report"
echo "tsv: $tsv"
