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
dependency_provenance="$(python3 ./dependency-provenance.py)" || exit 1
IFS=$'\t' read -r FLUID_REVISION PRODUCTION_FLUID_REVISION BASELINE_DEPENDENCY <<<"$dependency_provenance"

INPUT_DIR="real-audio"
OUTDIR="real-results"
BACKEND="v3"
LANGUAGE="auto"
TRIALS="5"
UNIFIED_TRAILING_SILENCE_MS="250"
NEMOTRON_MULTILINGUAL_LANGUAGE="en-US"
NEMOTRON_MULTILINGUAL_CHUNK_MS="2240"
ALLOW_MISSING_REF=0
REDACT_TRANSCRIPTS=1
REDACT_PATHS=1
CORPUS_KIND="private"
SELF_TEST=0
EXPERIMENT_ENVIRONMENT_STATE="unreported"
MAX_REFERENCE_DELETION_RUN=""
MAX_CORPUS_WER=""
MAX_NON_SPEECH_EMISSIONS=""
BENCHMARK_INPUT_SHA256="unreported"
BENCHMARK_ORDER_SHA256="unreported"
WINDOW_SHIFT_CORPUS=0

usage() {
    cat <<'USAGE'
usage: ./run-real-dictation-regression.sh [options]

Options:
  --input-dir <path>       directory with audio + .txt sidecars (default: real-audio)
  --out-dir <path>         report directory (default: real-results)
  --backend <name>         presspeech-bench backend: v2, v3, v3-no-mel,
                           v3-sdk-default, v3-int8-v2, unified, nemotron-en, nemotron-multilingual,
                           apple, 110m, fluid, both (default: v3)
  --language <auto|code>   Parakeet TDT v3 language/script hint (default: auto;
                           non-auto values require a v3 backend)
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
  --max-reference-deletion-run <n>
                           fail if any hypothesis drops more than n consecutive
                           reference words on the minimum-edit alignment
  --max-corpus-wer <percent>
                           fail if the conservative corpus WER exceeds this
                           percentage (worst observed transcript per clip)
  --max-non-speech-emissions <n>
                           fail if more than n measured trials on zero-byte
                           non-speech references emit deliverable text
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

backend_uses_parakeet_v3() {
    case "$BACKEND" in
        v3|v3-no-mel|v3-sdk-default|v3-int8-v2|v3-vocab|v3-vocab-conservative|v3-vocab-no-rescue|v3-vocab-exact-similarity|sliding-v3|sliding-vocab|sliding-vocab-conservative|sliding-vocab-no-rescue|fluid|both)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
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

build_bench_args() {
    local audio_file="$1"
    BENCH_ARGS=(
        ".build/release/presspeech-bench"
        "--file" "$audio_file"
        "--backend" "$BACKEND"
        "--trials" "$TRIALS"
        "--unified-trailing-silence-ms" "$UNIFIED_TRAILING_SILENCE_MS"
        "--nemotron-multilingual-language" "$NEMOTRON_MULTILINGUAL_LANGUAGE"
        "--nemotron-multilingual-chunk-ms" "$NEMOTRON_MULTILINGUAL_CHUNK_MS"
    )
    if backend_uses_parakeet_v3; then
        BENCH_ARGS+=( "--language" "$LANGUAGE" )
    fi
    if [[ "$REDACT_TRANSCRIPTS" -eq 1 ]]; then
        BENCH_ARGS+=( "--redact-transcripts" )
    fi
}

validate_benchmark_output() {
    local log_file="$1"
    local expected_backends="$2"
    local reference_kind="$3"
    awk -v expected="$expected_backends" -v reference_kind="$reference_kind" '
        function inspect_result(line) {
            results += 1
            if (reference_kind == "speech" &&
                (line !~ /\[WER [0-9]+([.][0-9]+)?%\]/ ||
                 line !~ /\[final-word retained=(true|false)([[:space:]]|\])/ ||
                 line !~ /\[word-errors=[0-9]+ reference-words=[0-9]+\]/ ||
                 line !~ /\[max-reference-deletion-run=[0-9]+\]/)) {
                incomplete_results += 1
            }
            if (reference_kind == "control" &&
                (line !~ /\[WER [0-9]+([.][0-9]+)?%\]/ ||
                 line !~ /\[word-errors=[0-9]+ reference-words=0\]/ ||
                 line !~ /\[max-reference-deletion-run=0\]/)) {
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

non_speech_trial_metrics() {
    local log_file="$1"
    # Use only benchmark-owned per-trial receipts, never text or a set of
    # distinct transcripts. Validate numbering so an omitted trial fails closed.
    awk '
        /^[[:space:]]*output: trial=[0-9]+\/[0-9]+ empty=(true|false) characters=[0-9]+$/ {
            line = $0
            sub(/^[[:space:]]*output: trial=/, "", line)
            gsub(/[\/=]/, " ", line)
            split(line, fields, /[[:space:]]+/)
            trial = fields[1]
            total = fields[2]
            empty = fields[4]
            characters = fields[6]
            observed += 1
            if (trial != observed || total < 1 || trial > total) invalid = 1
            if (observed == 1) expected = total
            else if (total != expected) invalid = 1
            if (empty == "false") nonempty += 1
            if ((empty == "true" && characters != 0) ||
                (empty == "false" && characters == 0)) invalid = 1
        }
        END {
            if (invalid || observed < 1 || observed != expected)
                print "unknown\tunknown"
            else printf("%d\t%d\n", nonempty, observed)
        }
    ' "$log_file"
}

worst_reference_deletion_run() {
    local report="$1"
    sed -nE 's/.*\[max-reference-deletion-run=([0-9]+)\].*/\1/p' "$report" \
        | awk 'BEGIN { found = 0; worst = 0 }
               { found = 1; if ($1 > worst) worst = $1 }
               END { if (found) print worst; else print "unknown" }'
}

conservative_corpus_metrics() {
    local report="$1"
    awk '
        function flush_clip() {
            if (!clip_seen) return
            # Zero-byte references are non-speech controls, not speech WER.
            if (clip_reference_words > 0) {
                total_errors += clip_worst_errors
                total_words += clip_reference_words
            }
            clip_seen = 0
            clip_worst_errors = 0
            clip_reference_words = 0
        }
        /^    latency:/ {
            flush_clip()
        }
        /\[word-errors=[0-9]+ reference-words=[0-9]+\]/ {
            metric = $0
            sub(/^.*\[word-errors=/, "", metric)
            errors = metric
            sub(/ reference-words=.*$/, "", errors)
            words = metric
            sub(/^[0-9]+ reference-words=/, "", words)
            sub(/\].*$/, "", words)
            errors += 0
            words += 0
            if (!clip_seen) {
                clip_seen = 1
                clip_reference_words = words
                clip_worst_errors = errors
            } else {
                if (words != clip_reference_words) invalid = 1
                if (errors > clip_worst_errors) clip_worst_errors = errors
            }
        }
        END {
            flush_clip()
            if (invalid || total_words < 1) print "unknown\tunknown\tunknown"
            else printf("%.2f\t%d\t%d\n", 100 * total_errors / total_words,
                        total_errors, total_words)
        }
    ' "$report"
}

conservative_corpus_wer() {
    conservative_corpus_metrics "$1" | cut -f1
}

# Keep the requested numeric checks visible, but never let them qualify a
# configured or unreported runtime as default-environment ASR evidence.
append_environment_gate() {
    local report="$1"
    {
        echo
        echo "## Inherited SDK environment"
        echo
        echo "- State: $EXPERIMENT_ENVIRONMENT_STATE"
        echo "Numeric gate verdicts describe measured errors only; they do not establish production or whole-app qualification."
        if [[ "$EXPERIMENT_ENVIRONMENT_STATE" == "default" ]]; then
            echo "- Default-environment prerequisite: passes"
        else
            echo "- Default-environment prerequisite: blocked"
        fi
        if [[ -z "$MAX_REFERENCE_DELETION_RUN" && -z "$MAX_CORPUS_WER" && \
              -z "$MAX_NON_SPEECH_EMISSIONS" ]]; then
            echo "Exploratory run: no quality gate requested."
        fi
    } >>"$report"
    # Ungated exploration preserves intentionally configured environments.
    [[ "$EXPERIMENT_ENVIRONMENT_STATE" == "default" || \
       ( -z "$MAX_REFERENCE_DELETION_RUN" && -z "$MAX_CORPUS_WER" && \
         -z "$MAX_NON_SPEECH_EMISSIONS" ) ]]
}

append_non_speech_gate() {
    local report="$1"
    local controls="$2"
    local nonempty="$3"
    local trials="$4"
    local verdict="passes"
    if [[ "$controls" -lt 1 || "$trials" -ne "$((controls * TRIALS))" || \
          "$nonempty" -gt "$MAX_NON_SPEECH_EMISSIONS" ]]; then
        verdict="fails"
    fi
    {
        echo
        echo "## Non-speech emission gate"
        echo
        echo "- Zero-byte non-speech references: $controls"
        echo "- Measured trials: $trials"
        echo "- Trials with deliverable text: $nonempty"
        echo "- Maximum allowed emitting trials: $MAX_NON_SPEECH_EMISSIONS"
        echo "- Verdict: $verdict"
    } >>"$report"
    [[ "$verdict" == "passes" ]]
}

append_quality_gate() {
    local report="$1"
    local observed="$2"
    local verdict="passes"
    if [[ "$observed" == "unknown" || "$observed" -gt "$MAX_REFERENCE_DELETION_RUN" ]]; then
        verdict="fails"
    fi
    {
        echo
        echo "## Consecutive-deletion gate"
        echo
        echo "- Maximum allowed consecutive reference-word deletions: $MAX_REFERENCE_DELETION_RUN"
        echo "- Worst observed: $observed"
        echo "- Verdict: $verdict"
    } >>"$report"
    [[ "$verdict" == "passes" ]]
}

append_corpus_wer_gate() {
    local report="$1"
    local observed="$2"
    local verdict="passes"
    if [[ "$observed" == "unknown" ]] ||
       ! awk -v observed="$observed" -v maximum="$MAX_CORPUS_WER" \
           'BEGIN { exit !(observed <= maximum) }'; then
        verdict="fails"
    fi
    {
        echo
        echo "## Corpus-WER gate"
        echo
        echo "- Maximum allowed conservative corpus WER: ${MAX_CORPUS_WER}%"
        echo "- Worst-observation corpus WER: ${observed}%"
        echo "- Verdict: $verdict"
    } >>"$report"
    [[ "$verdict" == "passes" ]]
}

publish_report_artifact() {
    local stage_dir="$1"
    local staged_report="$2"
    local final_report="$3"

    if [[ ! -f "$staged_report" ]]; then
        echo "real-dictation report staging artifact is incomplete" >&2
        return 1
    fi
    if [[ -e "$final_report" ]]; then
        echo "refusing to replace existing real-dictation report" >&2
        return 1
    fi

    # Keep the timestamped report hidden until every clip and the summary have
    # succeeded. A failed run is diagnostic output, not a benchmark artifact.
    if ! mv "$staged_report" "$final_report"; then
        return 1
    fi
    rmdir "$stage_dir"
}

single_backend_summary_row() {
    local report="$1"
    awk -v backend="$BACKEND" '
        function flush_wer() {
            if (clip_wer_seen == 0) return
            wer_sum += clip_worst_wer
            p50_sum += clip_p50
            speech_p50_seen += 1
            if (wer_seen == 0 || clip_worst_wer > worst_wer) {
                worst_wer = clip_worst_wer
            }
            wer_seen += 1
            final_fail += clip_final_fail
            clip_wer_seen = 0
            clip_worst_wer = 0
            clip_final_fail = 0
            clip_p50 = 0
        }
        /latency:.*p50=/ {
            # A backend emits latency before either one stable transcript or
            # several distinct transcript bullets. Close the preceding clip
            # here so variability still contributes one conservative WER row.
            flush_wer()
            p50 = $0
            sub(/^.*p50=[[:space:]]*/, "", p50)
            sub(/ ms.*$/, "", p50)
            clip_p50 = p50
            p50_seen += 1
        }
        /\[WER [0-9.]+%\]/ && /\[word-errors=[0-9]+ reference-words=[1-9][0-9]*\]/ {
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
            avg_p50 = speech_p50_seen > 0 ? sprintf("%.1f", p50_sum / speech_p50_seen) : "unknown"
            printf("| `%s` | %d | %s | %s | %s | %s |\n", backend, rows, avg_wer, worst, failures, avg_p50)
        }
    ' "$report"
}

append_single_backend_summary() {
    local report="$1"
    if backend_is_aggregate; then
        return
    fi
    local summary_row corpus_wer corpus_errors corpus_words
    summary_row="$(single_backend_summary_row "$report")"
    IFS=$'\t' read -r corpus_wer corpus_errors corpus_words \
        < <(conservative_corpus_metrics "$report")
    {
        echo
        echo "## Summary"
        echo
        echo "| Backend | Clip rows | Mean worst-speech-clip WER % | Worst speech WER % | Speech final-word failures | Average speech p50 ms |"
        echo "|---|---:|---:|---:|---:|---:|"
        printf '%s\n' "$summary_row"
        echo
        if [[ "$corpus_wer" == "unknown" ]]; then
            echo "Conservative corpus WER (worst observed transcript per clip): unknown"
        else
            echo "Conservative corpus WER (worst observed transcript per clip): ${corpus_wer}% (${corpus_errors} errors / ${corpus_words} reference words)"
        fi
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
        echo "- FluidAudio revision: $FLUID_REVISION"
        echo "- App FluidAudio revision: $PRODUCTION_FLUID_REVISION"
        echo "- Baseline dependency: $BASELINE_DEPENDENCY (not whole-app qualification)"
        echo "- Benchmark inputs SHA-256: $BENCHMARK_INPUT_SHA256"
        echo "- Benchmark order SHA-256: $BENCHMARK_ORDER_SHA256"
        echo "- Trials per clip: $TRIALS"
        if backend_uses_parakeet_v3; then
            echo "- Parakeet TDT v3 language/script hint: $LANGUAGE"
        fi
        if backend_uses_unified; then
            echo "- Unified trailing silence: ${UNIFIED_TRAILING_SILENCE_MS} ms"
        fi
        if backend_uses_nemotron_multilingual; then
            echo "- Nemotron multilingual language: $NEMOTRON_MULTILINGUAL_LANGUAGE"
            echo "- Nemotron multilingual chunk: ${NEMOTRON_MULTILINGUAL_CHUNK_MS} ms"
        fi
        if [[ -n "$MAX_REFERENCE_DELETION_RUN" ]]; then
            echo "- Maximum consecutive reference-word deletions: $MAX_REFERENCE_DELETION_RUN"
        fi
        if [[ -n "$MAX_CORPUS_WER" ]]; then
            echo "- Maximum conservative corpus WER: ${MAX_CORPUS_WER}%"
        fi
        if [[ -n "$MAX_NON_SPEECH_EMISSIONS" ]]; then
            echo "- Maximum non-speech emitting trials: $MAX_NON_SPEECH_EMISSIONS"
        fi
        echo "- Transcript output: $(transcript_output_label)"
        echo "- Fixture paths: $(fixture_paths_label)"
        echo "- Clips: $clip_count"
        if [[ "$WINDOW_SHIFT_CORPUS" -eq 1 ]]; then
            echo "- Evidence scope: repeated-speech window-position diagnostic; not an independent release or candidate gate"
        fi
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
    local reference_available="$7"

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
        if [[ "$reference_available" -eq 1 ]]; then
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
    python3 ./benchmark-inputs.py --self-test
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
    LANGUAGE="auto"
    TRIALS="2"
    UNIFIED_TRAILING_SILENCE_MS="250"
    NEMOTRON_MULTILINGUAL_LANGUAGE="en-US"
    NEMOTRON_MULTILINGUAL_CHUNK_MS="2240"
    BENCHMARK_INPUT_SHA256="$(printf 'a%.0s' {1..64})"
    BENCHMARK_ORDER_SHA256="$(printf 'b%.0s' {1..64})"
    REDACT_TRANSCRIPTS=1
    REDACT_PATHS=1
    MAX_REFERENCE_DELETION_RUN=""
    MAX_CORPUS_WER=""

    local report="$tmpdir/report.md"
    local clip_number="001"
    local clip_id
    clip_id="$(clip_id_for 1 "$secret_stem")"
    write_report_header "$report" "20260101T000000Z" 1
    assert_contains "$report" "- FluidAudio revision: $FLUID_REVISION"
    assert_contains "$report" "- Parakeet TDT v3 language/script hint: auto"
    assert_contains "$report" "- Benchmark inputs SHA-256: $BENCHMARK_INPUT_SHA256"
    assert_contains "$report" "- Benchmark order SHA-256: $BENCHMARK_ORDER_SHA256"
    assert_not_contains "$report" "Unified trailing silence"
    WINDOW_SHIFT_CORPUS=1
    write_report_header "$report" "20260101T000000Z" 1
    assert_contains "$report" "repeated-speech window-position diagnostic"
    WINDOW_SHIFT_CORPUS=0

    BACKEND="unified"
    write_report_header "$report" "20260101T000000Z" 1
    assert_contains "$report" "- Unified trailing silence: 250 ms"
    assert_not_contains "$report" "Parakeet TDT v3 language/script hint"
    LANGUAGE="de"
    build_bench_args "fixture.wav"
    assert_not_contains <(printf '%s' "${BENCH_ARGS[*]}") "--language"
    BACKEND="nemotron-multilingual"
    LANGUAGE="auto"
    write_report_header "$report" "20260101T000000Z" 1
    assert_contains "$report" "- Nemotron multilingual language: en-US"
    assert_contains "$report" "- Nemotron multilingual chunk: 2240 ms"
    BACKEND="v3"
    LANGUAGE="de"
    write_report_header "$report" "20260101T000000Z" 1
    assert_contains "$report" "- Parakeet TDT v3 language/script hint: de"

    build_bench_args "fixture.wav"
    assert_eq "${BENCH_ARGS[*]}" \
        ".build/release/presspeech-bench --file fixture.wav --backend v3 --trials 2 --unified-trailing-silence-ms 250 --nemotron-multilingual-language en-US --nemotron-multilingual-chunk-ms 2240 --language de --redact-transcripts" \
        "Parakeet language hint forwarding"
    LANGUAGE="auto"

    write_clip_section_header "$report" "$clip_number" "$clip_id" "$secret_stem" "$secret_dir/$secret_stem.wav" "$secret_dir/$secret_stem.txt" 1
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
        echo '      • [WER 0.0%] [final-word retained=true expected="one" actual-last="one"] [word-errors=0 reference-words=25] [max-reference-deletion-run=0] <redacted 3 chars>'
        echo '      • [WER 4.0%] [final-word retained=false expected="one" actual-last="none"] [word-errors=1 reference-words=25] [max-reference-deletion-run=1] "literal [WER 99.0%]"'
        echo '    latency:  p50=  70.0 ms  min=  69.0 ms  max=  71.0 ms'
        echo '    transcript: [WER 10.0%] [final-word retained=false expected="two" actual-last="one"] [word-errors=1 reference-words=10] [max-reference-deletion-run=4] <redacted 3 chars>'
    } >"$summary_source"
    BACKEND="v3"
    # shellcheck disable=SC2016 # Markdown backticks are intentional literals.
    local expected_summary='| `v3` | 2 | 7.00 | 10.0 | 2 | 60.0 |'
    assert_eq "$(single_backend_summary_row "$summary_source")" "$expected_summary" "variable-output single-backend summary"
    assert_eq "$(conservative_corpus_metrics "$summary_source")" $'5.71\t2\t35' "conservative exact-count corpus metrics"
    assert_eq "$(conservative_corpus_wer "$summary_source")" "5.71" "conservative exact-count corpus WER"
    append_single_backend_summary "$summary_source"
    assert_contains "$summary_source" "## Summary"
    assert_contains "$summary_source" "$expected_summary"
    assert_contains "$summary_source" \
        "Conservative corpus WER (worst observed transcript per clip): 5.71% (2 errors / 35 reference words)"
    validate_benchmark_output "$summary_source" 2 speech
    assert_eq "$(worst_reference_deletion_run "$summary_source")" "4" "worst consecutive deletion parser"
    MAX_REFERENCE_DELETION_RUN="4"
    append_quality_gate "$summary_source" "$(worst_reference_deletion_run "$summary_source")"
    assert_contains "$summary_source" "- Verdict: passes"
    MAX_REFERENCE_DELETION_RUN="3"
    if append_quality_gate "$summary_source" "4"; then
        echo "self-test expected an excessive consecutive deletion run to fail" >&2
        exit 1
    fi
    assert_contains "$summary_source" "- Verdict: fails"
    MAX_REFERENCE_DELETION_RUN=""
    MAX_CORPUS_WER="5.71"
    append_corpus_wer_gate "$summary_source" "$(conservative_corpus_wer "$summary_source")"
    assert_contains "$summary_source" "- Worst-observation corpus WER: 5.71%"
    assert_contains "$summary_source" "- Verdict: passes"
    MAX_CORPUS_WER="5.70"
    if append_corpus_wer_gate "$summary_source" "5.71"; then
        echo "self-test expected excessive corpus WER to fail" >&2
        exit 1
    fi
    assert_contains "$summary_source" "- Verdict: fails"
    local state environment_report="$tmpdir/environment-gate.md"
    MAX_REFERENCE_DELETION_RUN="0"
    MAX_CORPUS_WER="0"
    for state in default configured unreported pending; do
        EXPERIMENT_ENVIRONMENT_STATE="$state"
        : >"$environment_report"
        # Both actual numeric gates pass; only runtime provenance differs.
        append_quality_gate "$environment_report" "0"
        append_corpus_wer_gate "$environment_report" "0"
        if append_environment_gate "$environment_report"; then
            assert_eq "$state" "default" "default environment quality prerequisite"
        else
            if [[ "$state" == "default" ]]; then exit 1; fi
            assert_contains "$environment_report" "Default-environment prerequisite: blocked"
        fi
    done
    MAX_REFERENCE_DELETION_RUN=""
    MAX_CORPUS_WER=""
    EXPERIMENT_ENVIRONMENT_STATE="configured"
    append_environment_gate "$environment_report"
    assert_contains "$environment_report" "Exploratory run: no quality gate requested."
    EXPERIMENT_ENVIRONMENT_STATE="unreported"
    assert_eq "$(expected_backend_count v3)" "1" "single backend count"
    assert_eq "$(expected_backend_count fluid)" "5" "fluid backend count"
    assert_eq "$(expected_backend_count both)" "6" "all backend count"

    local no_reference_source="$tmpdir/no-reference-source.log"
    {
        echo '    latency:  p50=  80.0 ms  min=  79.0 ms  max=  81.0 ms'
        echo '    transcript: <redacted 3 chars>'
    } >"$no_reference_source"
    validate_benchmark_output "$no_reference_source" 1 missing

    local incomplete_source="$tmpdir/incomplete-source.log"
    {
        echo '    latency:  p50=  80.0 ms  min=  79.0 ms  max=  81.0 ms'
        echo '    transcript: [WER 0.0%] <redacted 3 chars>'
    } >"$incomplete_source"
    local validation_log="$tmpdir/validation.log"
    if validate_benchmark_output "$incomplete_source" 1 speech >"$validation_log" 2>&1; then
        echo "self-test expected incomplete benchmark metrics to fail validation" >&2
        exit 1
    fi
    assert_contains "$validation_log" "benchmark output missing required metrics:"

    local control_source="$tmpdir/control-source.log"
    {
        echo '    latency:  p50=  80.0 ms  min=  79.0 ms  max=  81.0 ms'
        echo '    output: trial=1/3 empty=true characters=0'
        echo '    output: trial=2/3 empty=false characters=9'
        echo '    output: trial=3/3 empty=true characters=0'
        echo '    transcript: [WER 100.0%] [word-errors=1 reference-words=0] [max-reference-deletion-run=0] <redacted 9 chars>'
    } >"$control_source"
    validate_benchmark_output "$control_source" 1 control
    assert_eq "$(non_speech_trial_metrics "$control_source")" $'1\t3' \
        "non-speech per-trial receipts"
    assert_eq "$(conservative_corpus_metrics "$control_source")" \
        $'unknown\tunknown\tunknown' "non-speech excluded from corpus WER"
    cat "$control_source" >>"$summary_source"
    assert_eq "$(conservative_corpus_metrics "$summary_source")" \
        $'5.71\t2\t35' "non-speech does not dilute speech WER"
    assert_eq "$(single_backend_summary_row "$summary_source")" \
        '| `v3` | 3 | 7.00 | 10.0 | 2 | 60.0 |' \
        "non-speech excluded from speech WER summary"
    if validate_benchmark_output "$summary_source" 3 speech >"$validation_log" 2>&1; then
        echo "self-test expected a non-speech result to fail speech validation" >&2
        exit 1
    fi
    sed 's/trial=3\/3/trial=2\/3/' "$control_source" >"$tmpdir/duplicate-control.log"
    assert_eq "$(non_speech_trial_metrics "$tmpdir/duplicate-control.log")" \
        $'unknown\tunknown' "duplicate non-speech trial rejected"
    sed 's/reference-words=0/reference-words=1/' "$control_source" \
        >"$tmpdir/invalid-control.log"
    if validate_benchmark_output "$tmpdir/invalid-control.log" 1 control \
        >"$validation_log" 2>&1; then
        echo "self-test expected an invalid non-speech result to fail validation" >&2
        exit 1
    fi
    TRIALS=3
    MAX_NON_SPEECH_EMISSIONS=0
    if append_non_speech_gate "$control_source" 1 1 3; then
        echo "self-test expected an emitting non-speech trial to fail" >&2
        exit 1
    fi
    MAX_NON_SPEECH_EMISSIONS=1
    append_non_speech_gate "$control_source" 1 1 3
    if append_non_speech_gate "$control_source" 0 0 0; then
        echo "self-test expected missing controls to fail" >&2
        exit 1
    fi
    MAX_NON_SPEECH_EMISSIONS=""

    local stage_dir="$tmpdir/staged"
    local final_dir="$tmpdir/published"
    mkdir -p "$stage_dir" "$final_dir"
    printf 'complete report\n' >"$stage_dir/report.md"
    [[ ! -e "$final_dir/report.md" ]] || {
        echo "self-test found a report before publication" >&2
        exit 1
    }
    publish_report_artifact \
        "$stage_dir" \
        "$stage_dir/report.md" \
        "$final_dir/report.md"
    assert_contains "$final_dir/report.md" "complete report"
    [[ ! -e "$stage_dir" ]] || {
        echo "self-test expected successful report staging cleanup" >&2
        exit 1
    }

    stage_dir="$tmpdir/collision-stage"
    mkdir -p "$stage_dir"
    printf 'new report\n' >"$stage_dir/report.md"
    local collision_log="$tmpdir/collision.log"
    if publish_report_artifact \
        "$stage_dir" \
        "$stage_dir/report.md" \
        "$final_dir/report.md" >"$collision_log" 2>&1; then
        echo "self-test expected report publication collision to fail" >&2
        exit 1
    fi
    assert_contains "$collision_log" "refusing to replace existing real-dictation report"
    assert_contains "$final_dir/report.md" "complete report"
    assert_contains "$stage_dir/report.md" "new report"

    local missing_value_log="$tmpdir/missing-value.log"
    local shift_dir="$tmpdir/window-shift"
    mkdir -p "$shift_dir"
    printf 'Presspeech generated public window-position speech fixtures\n' \
        >"$shift_dir/.presspeech-public-window-shift-fixtures"
    local shift_log="$tmpdir/window-shift.log"
    if bash "$SCRIPT_PATH" --input-dir "$shift_dir" \
        --max-corpus-wer 10 >"$shift_log" 2>&1; then
        echo "self-test expected repeated speech to reject an independent-corpus gate" >&2
        exit 1
    fi
    assert_contains "$shift_log" "window-position fixtures are report-only"
    if bash "$SCRIPT_PATH" --input-dir "$shift_dir" >"$shift_log" 2>&1; then
        echo "self-test expected malformed window-position corpus to fail preflight" >&2
        exit 1
    fi
    assert_contains "$shift_log" "missing regular window-position manifest"

    if bash "$SCRIPT_PATH" --trials >"$missing_value_log" 2>&1; then
        echo "self-test expected --trials without a value to fail" >&2
        exit 1
    fi
    assert_contains "$missing_value_log" "--trials requires a value"

    local invalid_language_log="$tmpdir/invalid-language.log"
    if bash "$SCRIPT_PATH" --input-dir "$secret_dir" --language "en-US" \
        >"$invalid_language_log" 2>&1; then
        echo "self-test expected an invalid Parakeet language hint to fail" >&2
        exit 1
    fi
    assert_contains "$invalid_language_log" \
        "--language must be auto or a two-letter lowercase language code"

    local unsupported_backend_language_log="$tmpdir/unsupported-backend-language.log"
    if bash "$SCRIPT_PATH" --input-dir "$secret_dir" --backend unified \
        --language de >"$unsupported_backend_language_log" 2>&1; then
        echo "self-test expected a v3 language hint on Unified to fail" >&2
        exit 1
    fi
    assert_contains "$unsupported_backend_language_log" \
        "--language is available only with Parakeet TDT v3 backends"

    local invalid_wer_log="$tmpdir/invalid-wer.log"
    if bash "$SCRIPT_PATH" --input-dir "$secret_dir" --max-corpus-wer nope \
        >"$invalid_wer_log" 2>&1; then
        echo "self-test expected a non-decimal corpus-WER bound to fail" >&2
        exit 1
    fi
    assert_contains "$invalid_wer_log" \
        "--max-corpus-wer must be a non-negative decimal percentage"

    local invalid_non_speech_log="$tmpdir/invalid-non-speech.log"
    if bash "$SCRIPT_PATH" --input-dir "$secret_dir" \
        --max-non-speech-emissions nope >"$invalid_non_speech_log" 2>&1; then
        echo "self-test expected a non-integer emission bound to fail" >&2
        exit 1
    fi
    assert_contains "$invalid_non_speech_log" \
        "--max-non-speech-emissions must be a non-negative integer"

    local aggregate_non_speech_log="$tmpdir/aggregate-non-speech.log"
    if bash "$SCRIPT_PATH" --input-dir "$secret_dir" --backend fluid \
        --max-non-speech-emissions 0 >"$aggregate_non_speech_log" 2>&1; then
        echo "self-test expected a non-speech gate over aggregate backends to fail" >&2
        exit 1
    fi
    assert_contains "$aggregate_non_speech_log" \
        "--max-non-speech-emissions requires a single benchmark backend"

    local aggregate_wer_log="$tmpdir/aggregate-wer.log"
    if bash "$SCRIPT_PATH" --input-dir "$secret_dir" --backend fluid \
        --max-corpus-wer 10 >"$aggregate_wer_log" 2>&1; then
        echo "self-test expected a corpus-WER gate over aggregate backends to fail" >&2
        exit 1
    fi
    assert_contains "$aggregate_wer_log" \
        "--max-corpus-wer requires a single benchmark backend"

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
        --max-reference-deletion-run)
            need_value "$@"
            MAX_REFERENCE_DELETION_RUN="$2"
            shift 2
            ;;
        --max-corpus-wer)
            need_value "$@"
            MAX_CORPUS_WER="$2"
            shift 2
            ;;
        --max-non-speech-emissions)
            need_value "$@"
            MAX_NON_SPEECH_EMISSIONS="$2"
            shift 2
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

if [[ ! -d "$INPUT_DIR" ]]; then
    cat >&2 <<MSG
input directory not found: $INPUT_DIR

Create it and add private audio files plus matching .txt reference files.
See real-audio/README.md.
MSG
    exit 1
fi

if [[ -e "$INPUT_DIR/.presspeech-public-window-shift-fixtures" || \
      -L "$INPUT_DIR/.presspeech-public-window-shift-fixtures" ]]; then
    if [[ -n "$MAX_REFERENCE_DELETION_RUN" || -n "$MAX_CORPUS_WER" ||
          -n "$MAX_NON_SPEECH_EMISSIONS" ]]; then
        echo "window-position fixtures are report-only; do not apply independent-corpus quality gates" >&2
        exit 2
    fi
    python3 ./compose-public-window-shift-fixtures.py \
        --output-dir "$INPUT_DIR" --validate-output-dir >/dev/null
    WINDOW_SHIFT_CORPUS=1
fi

if ! [[ "$TRIALS" =~ ^[0-9]+$ ]]; then
    echo "--trials must be a positive integer" >&2
    exit 2
fi
TRIALS=$((10#$TRIALS))
if [[ "$TRIALS" -lt 1 ]]; then
    echo "--trials must be a positive integer" >&2
    exit 2
fi

if ! [[ "$LANGUAGE" =~ ^(auto|[a-z]{2})$ ]]; then
    echo "--language must be auto or a two-letter lowercase language code" >&2
    exit 2
fi
if [[ "$LANGUAGE" != "auto" ]] && ! backend_uses_parakeet_v3; then
    echo "--language is available only with Parakeet TDT v3 backends" >&2
    exit 2
fi

if [[ -n "$MAX_REFERENCE_DELETION_RUN" ]] &&
   ! [[ "$MAX_REFERENCE_DELETION_RUN" =~ ^[0-9]+$ ]]; then
    echo "--max-reference-deletion-run must be a non-negative integer" >&2
    exit 2
fi

if [[ -n "$MAX_CORPUS_WER" ]] &&
   ! [[ "$MAX_CORPUS_WER" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    echo "--max-corpus-wer must be a non-negative decimal percentage" >&2
    exit 2
fi

if [[ -n "$MAX_CORPUS_WER" ]] && backend_is_aggregate; then
    echo "--max-corpus-wer requires a single benchmark backend" >&2
    exit 2
fi

if [[ -n "$MAX_NON_SPEECH_EMISSIONS" ]] &&
   ! [[ "$MAX_NON_SPEECH_EMISSIONS" =~ ^[0-9]+$ ]]; then
    echo "--max-non-speech-emissions must be a non-negative integer" >&2
    exit 2
fi
if [[ -n "$MAX_NON_SPEECH_EMISSIONS" ]] && backend_is_aggregate; then
    echo "--max-non-speech-emissions requires a single benchmark backend" >&2
    exit 2
fi
if [[ -n "$MAX_NON_SPEECH_EMISSIONS" ]]; then
    MAX_NON_SPEECH_EMISSIONS=$((10#$MAX_NON_SPEECH_EMISSIONS))
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

control_count=0
for clip in "${clips[@]}"; do
    ref="${clip%.*}.txt"
    if [[ -f "$ref" && ! -s "$ref" ]]; then
        control_count=$((control_count + 1))
    fi
done
if [[ -n "$MAX_NON_SPEECH_EMISSIONS" && "$control_count" -eq 0 ]]; then
    echo "--max-non-speech-emissions requires zero-byte non-speech reference sidecars" >&2
    exit 1
fi

mkdir -p "$OUTDIR"
tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/presspeech-real-dictation.XXXXXX")"
stage_dir=""
cleanup() {
    rm -rf "$tmpdir"
    if [[ -n "$stage_dir" ]]; then
        rm -rf -- "$stage_dir"
    fi
}
trap cleanup EXIT INT TERM

# Freeze audio and references before the build or any model work. Production
# and candidate SDK revisions require separate builds, so this folded digest
# is what lets their reports prove that they consumed the same private corpus.
original_clips=( "${clips[@]}" )
snapshot_args=( snapshot --output-dir "$tmpdir/inputs" )
if [[ "$ALLOW_MISSING_REF" -eq 1 ]]; then
    snapshot_args+=( --allow-missing-reference )
fi
if ! BENCHMARK_INPUT_SHA256="$(
    python3 ./benchmark-inputs.py "${snapshot_args[@]}" -- "${clips[@]}"
)" || ! [[ "$BENCHMARK_INPUT_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
    echo "could not freeze and fingerprint real-dictation inputs" >&2
    exit 1
fi
if ! benchmark_receipt="$(
    python3 ./benchmark-inputs.py receipt --snapshot-dir "$tmpdir/inputs"
)"; then
    echo "could not fingerprint real-dictation input order" >&2
    exit 1
fi
IFS=$'\t' read -r verified_input_sha256 BENCHMARK_ORDER_SHA256 <<< "$benchmark_receipt"
if [[ "$verified_input_sha256" != "$BENCHMARK_INPUT_SHA256" || \
      ! "$BENCHMARK_ORDER_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
    echo "could not fingerprint real-dictation input order" >&2
    exit 1
fi
for index in "${!clips[@]}"; do
    extension="${clips[$index]##*.}"
    clips[index]="$tmpdir/inputs/$(printf '%06d' "$((index + 1))")/audio.$extension"
done

echo "building presspeech-bench..."
swift_build_args=( -c release )
if [[ "$BACKEND" == "v3-int8-v2" ]]; then
    # This backend is intentionally absent from the production FluidAudio pin.
    # Defining it only for an explicit encoder-v2 run keeps normal and release
    # builds compatible with the exact dependency shipped by the app.
    swift_build_args+=( -Xswiftc -D -Xswiftc PRESSPEECH_ENCODER_INT8_V2 )
fi
swift build "${swift_build_args[@]}" >/dev/null
built_dependency_provenance="$(python3 ./dependency-provenance.py --verify-built)" || exit 1
if [[ "$built_dependency_provenance" != "$dependency_provenance" ]]; then
    echo "dependency provenance changed during benchmark build" >&2
    exit 1
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
safe_backend="$(printf '%s' "$BACKEND" | tr -c '[:alnum:]_.-' '-')"
final_report="$OUTDIR/$timestamp-$safe_backend.md"
if [[ -e "$final_report" ]]; then
    echo "real-dictation report already exists for timestamp $timestamp and backend $safe_backend" >&2
    exit 1
fi
reserved_stage_dir="$OUTDIR/.$timestamp-$safe_backend.incomplete"
if ! mkdir "$reserved_stage_dir"; then
    echo "could not reserve real-dictation output for timestamp $timestamp and backend $safe_backend" >&2
    exit 1
fi
stage_dir="$reserved_stage_dir"
report="$stage_dir/report.md"
backend_count="$(expected_backend_count "$BACKEND")"

write_report_header "$report" "$timestamp" "${#clips[@]}"

EXPERIMENT_ENVIRONMENT_STATE="pending"
control_nonempty_total=0
control_trial_total=0
observed_control_count=0
clip_index=0
for clip in "${clips[@]}"; do
    clip_index=$((clip_index + 1))
    clip_number="$(printf '%03d' "$clip_index")"
    source_clip="${original_clips[$((clip_index - 1))]}"
    stem="$(basename "$source_clip")"
    stem="${stem%.*}"
    clip_id="$(clip_id_for "$clip_index" "$stem")"
    normalized="$tmpdir/$clip_id.wav"
    ref="${clip%.*}.txt"
    source_ref="${source_clip%.*}.txt"

    echo "normalizing clip $clip_number..."
    afconvert -f WAVE -d LEF32@16000 "$clip" "$normalized"
    python3 ./audio-input-evidence.py --audio "$normalized" >/dev/null
    if [[ -f "$ref" ]]; then
        cp "$ref" "$tmpdir/$clip_id.txt"
    fi

    build_bench_args "$normalized"
    bench_args=( "${BENCH_ARGS[@]}" )

    reference_available=0
    if [[ -f "$ref" ]]; then
        reference_available=1
    fi
    write_clip_section_header "$report" "$clip_number" "$clip_id" "$stem" "$source_clip" "$source_ref" "$reference_available"

    echo "benchmarking clip $clip_number..."
    log_file="$tmpdir/$clip_id.log"
    if ! "${bench_args[@]}" >"$log_file" 2>&1; then
        cat "$log_file" >&2
        echo "benchmark failed for clip $clip_number" >&2
        exit 1
    fi
    python3 ./audio-input-evidence.py --audio "$normalized" --log "$log_file" >>"$log_file"
    EXPERIMENT_ENVIRONMENT_STATE="$(python3 ./experiment-environment.py --log "$log_file" --previous "$EXPERIMENT_ENVIRONMENT_STATE")"
    cat "$log_file" >>"$report"

    reference_kind="missing"
    if [[ -f "$ref" ]]; then
        reference_kind="speech"
        if [[ ! -s "$ref" ]]; then
            reference_kind="control"
        fi
    fi
    if ! validate_benchmark_output "$log_file" "$backend_count" "$reference_kind"; then
        cat "$log_file" >&2
        echo "invalid benchmark output for clip $clip_number" >&2
        exit 1
    fi
    if [[ "$reference_kind" == "control" && "$backend_count" -eq 1 ]]; then
        IFS=$'\t' read -r nonempty_trials observed_trials \
            < <(non_speech_trial_metrics "$log_file")
        if [[ "$nonempty_trials" == "unknown" || "$observed_trials" -ne "$TRIALS" ]]; then
            echo "invalid non-speech trial receipts for clip $clip_number" >&2
            exit 1
        fi
        control_nonempty_total=$((control_nonempty_total + nonempty_trials))
        control_trial_total=$((control_trial_total + observed_trials))
        observed_control_count=$((observed_control_count + 1))
    fi

    echo '```' >>"$report"
done

observed_receipt="$(python3 ./benchmark-inputs.py receipt --snapshot-dir "$tmpdir/inputs")"
IFS=$'\t' read -r observed_input_sha256 observed_order_sha256 <<< "$observed_receipt"
if [[ "$observed_input_sha256" != "$BENCHMARK_INPUT_SHA256" || \
      "$observed_order_sha256" != "$BENCHMARK_ORDER_SHA256" ]]; then
    echo "frozen real-dictation inputs changed during the benchmark" >&2
    exit 1
fi

append_single_backend_summary "$report"
if [[ "$backend_count" -eq 1 && "$control_count" -gt 0 ]]; then
    {
        echo
        echo "Non-speech controls (zero-byte references): $observed_control_count; deliverable text in $control_nonempty_total/$control_trial_total measured trials."
    } >>"$report"
fi

quality_gate_passed=1
if ! append_environment_gate "$report"; then
    quality_gate_passed=0
fi
if [[ -n "$MAX_REFERENCE_DELETION_RUN" ]]; then
    observed_deletion_run="$(worst_reference_deletion_run "$report")"
    if ! append_quality_gate "$report" "$observed_deletion_run"; then
        quality_gate_passed=0
    fi
fi
if [[ -n "$MAX_CORPUS_WER" ]]; then
    observed_corpus_wer="$(conservative_corpus_wer "$report")"
    if ! append_corpus_wer_gate "$report" "$observed_corpus_wer"; then
        quality_gate_passed=0
    fi
fi
if [[ -n "$MAX_NON_SPEECH_EMISSIONS" ]]; then
    if ! append_non_speech_gate "$report" "$observed_control_count" \
        "$control_nonempty_total" "$control_trial_total"; then
        quality_gate_passed=0
    fi
fi

publish_report_artifact "$stage_dir" "$report" "$final_report"
stage_dir=""

echo "report: $final_report"
if [[ "$quality_gate_passed" -ne 1 ]]; then
    echo "ASR quality gate failed; inspect the report's gate verdicts" >&2
    exit 1
fi
