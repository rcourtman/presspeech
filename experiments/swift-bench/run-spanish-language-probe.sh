#!/usr/bin/env bash
# Paired, report-only Spanish language-selection diagnostic on public FLEURS.

set -euo pipefail

SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$SCRIPT_PATH")"

INPUT_DIR="public-audio/fleurs-es_419-test"
OUT_DIR="public-results/fleurs-es_419-test-probe"
TRIALS="3"
MINIMUM_CLIPS=60
SHOW_TRANSCRIPTS=0
SELF_TEST=0

usage() {
    cat <<'USAGE'
usage: ./run-spanish-language-probe.sh [options]

Options:
  --input-dir <path>       pinned FLEURS es_419 test fixtures
                           (default: public-audio/fleurs-es_419-test)
  --out-dir <path>         paired reports and summary (default: public-results/fleurs-es_419-test-probe)
  --trials <n>             measured trials per clip and language setting (default: 3)
  --show-transcripts       include public references and hypotheses in reports
  --self-test              test provenance and report-pair validation without ASR
  -h, --help               show this help

Runs production Parakeet v3 once with automatic language selection and once
with the Spanish script-filter hint. Both reports must have identical frozen
input digests, app/benchmark SDK revisions, clip counts, and trial counts.
This is a report-only read-speech probe, not a release gate or spontaneous-
speech qualification. The Spanish hint filters output script; it does not
force language identification.
USAGE
}

need_value() {
    if [[ $# -lt 2 || -z "$2" || "$2" == --* ]]; then
        echo "$1 requires a value" >&2
        usage >&2
        exit 2
    fi
}

fleurs_revision() {
    local revisions
    revisions="$(sed -nE 's/^FLEURS_REVISION="([0-9a-f]{40})"$/\1/p' ./fetch-public-speech-fixtures.sh)"
    if [[ "$(printf '%s\n' "$revisions" | sed '/^$/d' | wc -l | tr -d '[:space:]')" != "1" ]]; then
        echo "could not identify the unique pinned FLEURS revision" >&2
        return 1
    fi
    printf '%s' "$revisions"
}

supported_audio_count() {
    local dir="$1"
    find "$dir" -type f \
        \( -iname '*.wav' -o -iname '*.aiff' -o -iname '*.aif' -o -iname '*.caf' -o -iname '*.m4a' -o -iname '*.mp3' -o -iname '*.flac' \) \
        | wc -l | tr -d '[:space:]'
}

validate_fleurs_fixture() {
    local dir="$1" pinned_revision readme_revision archive_hash row_count audio_count wav_count reference_count
    local manifest="$dir/manifest.tsv" readme="$dir/README.txt"

    if [[ -L "$dir" || ! -d "$dir" || -L "$manifest" || ! -f "$manifest" || \
          -L "$readme" || ! -f "$readme" || -L "$dir/.presspeech-public-fixtures" || \
          ! -f "$dir/.presspeech-public-fixtures" ]]; then
        echo "Spanish probe requires a generated FLEURS fixture directory with regular provenance files" >&2
        return 1
    fi

    pinned_revision="$(fleurs_revision)" || return 1
    if ! grep -Fqx 'Source: Google FLEURS speech corpus, locale es_419, split test' "$readme" || \
       ! grep -Fqx 'License: CC BY 4.0' "$readme" || \
       ! grep -Fqx "Dataset revision: $pinned_revision" "$readme"; then
        echo "Spanish probe fixture is not the pinned FLEURS es_419 test corpus" >&2
        return 1
    fi

    readme_revision="$(sed -nE 's/^Dataset revision: ([0-9a-f]{40})$/\1/p' "$readme")"
    archive_hash="$(sed -nE 's/^Archive SHA-256: ([0-9a-f]{64})$/\1/p' "$readme")"
    if [[ "$readme_revision" != "$pinned_revision" || \
          "$(printf '%s\n' "$archive_hash" | sed '/^$/d' | wc -l | tr -d '[:space:]')" != "1" ]]; then
        echo "Spanish probe fixture has invalid FLEURS revision or archive-hash metadata" >&2
        return 1
    fi

    if ! row_count="$(awk -F '\t' '
        NR == 1 {
            if ($0 != "clip_id\tsource\tsplit\toriginal_id\toriginal_audio\tlicense\treference") bad = 1
            next
        }
        NF != 7 || $1 !~ /^fleurs-es_419-test-[0-9][0-9][0-9][0-9]-[0-9]+$/ ||
        $2 != "FLEURS-es_419" || $3 != "test" || $4 == "" || $5 == "" ||
        $6 != "CC BY 4.0" || $7 == "" || seen[$1]++ { bad = 1 }
        END {
            if (NR < 2 || bad) exit 1
            print NR - 1
        }
    ' "$manifest")"; then
        echo "Spanish probe manifest has invalid schema, locale, license, references, or duplicate rows" >&2
        return 1
    fi

    audio_count="$(supported_audio_count "$dir")"
    wav_count="$(find "$dir" -type f -iname '*.wav' | wc -l | tr -d '[:space:]')"
    reference_count="$(find "$dir" -type f -name '*.txt' ! -name 'README.txt' | wc -l | tr -d '[:space:]')"
    if [[ "$row_count" -lt "$MINIMUM_CLIPS" || "$audio_count" != "$row_count" || \
          "$wav_count" != "$row_count" || "$reference_count" != "$row_count" ]]; then
        echo "Spanish probe requires at least $MINIMUM_CLIPS matched WAV/reference rows; got $row_count manifest rows, $audio_count audio, and $reference_count references" >&2
        return 1
    fi

    while IFS=$'\t' read -r clip_id source split original_id original_audio license reference; do
        [[ "$clip_id" == "clip_id" ]] && continue
        if [[ -L "$dir/$clip_id.wav" || ! -s "$dir/$clip_id.wav" || \
              -L "$dir/$clip_id.txt" || ! -f "$dir/$clip_id.txt" ]] || \
           ! printf '%s\n' "$reference" | cmp -s - "$dir/$clip_id.txt"; then
            echo "Spanish probe fixture audio/reference files do not match their manifest" >&2
            return 1
        fi
    done <"$manifest"

    printf '%s\t%s\t%s\n' "$row_count" "$pinned_revision" "$archive_hash"
}

REPORT_SHA=""
REPORT_FLUID_REVISION=""
REPORT_APP_REVISION=""
REPORT_CLIPS=""
REPORT_TRIALS=""

load_report_metadata() {
    local report="$1" expected_language="$2"
    local values
    if [[ -L "$report" || ! -f "$report" ]] || \
       ! grep -Fqx -- '- Backend: v3' "$report" || \
       ! grep -Fqx -- "- Parakeet TDT v3 language/script hint: $expected_language" "$report"; then
        echo "Spanish probe report is missing its expected backend or language hint" >&2
        return 1
    fi

    values="$(awk '
        /^- Benchmark inputs SHA-256: / { sha = $5; sha_count++ }
        /^- FluidAudio revision: / { fluid = $4; fluid_count++ }
        /^- App FluidAudio revision: / { app = $5; app_count++ }
        /^- Trials per clip: / { trials = $5; trials_count++ }
        /^- Clips: / { clips = $3; clips_count++ }
        END {
            if (sha_count != 1 || fluid_count != 1 || app_count != 1 ||
                trials_count != 1 || clips_count != 1 ||
                sha !~ /^[0-9a-f]+$/ || length(sha) != 64 ||
                fluid !~ /^[0-9a-f]+$/ || length(fluid) != 40 ||
                app !~ /^[0-9a-f]+$/ || length(app) != 40 ||
                trials !~ /^[0-9]+$/ || clips !~ /^[0-9]+$/) exit 1
            print sha "\t" fluid "\t" app "\t" clips "\t" trials
        }
    ' "$report")" || {
        echo "Spanish probe report has incomplete input or dependency identity" >&2
        return 1
    }
    IFS=$'\t' read -r REPORT_SHA REPORT_FLUID_REVISION REPORT_APP_REVISION REPORT_CLIPS REPORT_TRIALS <<<"$values"
    if [[ "$REPORT_FLUID_REVISION" != "$REPORT_APP_REVISION" ]]; then
        echo "Spanish probe requires the benchmark and app to use the same production FluidAudio revision" >&2
        return 1
    fi
}

validate_report_pair() {
    local first_sha="$1" first_fluid="$2" first_app="$3" first_clips="$4" first_trials="$5"
    local second_sha="$6" second_fluid="$7" second_app="$8" second_clips="$9" second_trials="${10}"
    local expected_clips="${11}" expected_trials="${12}"
    if [[ "$first_sha" == "$second_sha" && \
          "$first_fluid" == "$second_fluid" && "$first_app" == "$second_app" && \
          "$first_clips" == "$second_clips" && "$first_trials" == "$second_trials" && \
          "$first_clips" == "$expected_clips" && "$first_trials" == "$expected_trials" ]]; then
        return 0
    fi
    echo "paired Spanish reports differ in frozen inputs, SDK revisions, clip count, or trials" >&2
    return 1
}

RUN_REPORT=""
RUN_SHA=""
RUN_FLUID_REVISION=""
RUN_APP_REVISION=""
RUN_CLIPS=""
RUN_TRIALS=""
SELF_TEST_TMPDIR=""

run_probe_case() {
    local label="$1" language="$2" out_dir="$3" output
    local -a command=(./run-real-dictation-regression.sh
        --input-dir "$INPUT_DIR" --out-dir "$out_dir" --backend v3
        --language "$language" --trials "$TRIALS" --public-corpus)
    if [[ "$SHOW_TRANSCRIPTS" -eq 1 ]]; then
        command+=(--show-transcripts)
    fi

    echo "running Spanish FLEURS diagnostic ($label; language hint=$language)..."
    if ! output="$("${command[@]}" 2>&1)"; then
        printf '%s\n' "$output" >&2
        return 1
    fi
    printf '%s\n' "$output"
    RUN_REPORT="$(printf '%s\n' "$output" | sed -n 's/^report: //p' | tail -n 1)"
    if [[ -z "$RUN_REPORT" ]]; then
        echo "Spanish probe run did not publish a report" >&2
        return 1
    fi
    load_report_metadata "$RUN_REPORT" "$language" || return 1
    RUN_SHA="$REPORT_SHA"
    RUN_FLUID_REVISION="$REPORT_FLUID_REVISION"
    RUN_APP_REVISION="$REPORT_APP_REVISION"
    RUN_CLIPS="$REPORT_CLIPS"
    RUN_TRIALS="$REPORT_TRIALS"
}

write_pair_summary() {
    local path="$1" row_count="$2" revision="$3" archive_hash="$4"
    local auto_report="$5" hinted_report="$6" input_sha="$7" fluid_revision="$8" app_revision="$9"
    {
        echo '# Spanish language-selection probe (report-only)'
        echo
        echo '- Corpus: Google FLEURS es_419 test (read speech)'
        echo "- Corpus clips: $row_count"
        echo "- FLEURS dataset revision: $revision"
        echo "- FLEURS audio archive SHA-256: $archive_hash"
        echo "- Benchmark inputs SHA-256: $input_sha"
        echo "- FluidAudio benchmark revision: $fluid_revision"
        echo "- Presspeech app FluidAudio revision: $app_revision"
        echo "- Trials per clip: $TRIALS"
        if [[ "$SHOW_TRANSCRIPTS" -eq 1 ]]; then
            echo '- Transcript output: included (licensed public corpus)'
        else
            echo '- Transcript output: redacted in the paired reports'
        fi
        echo "- Automatic language selection report: $auto_report"
        echo "- Spanish script-filter hint report: $hinted_report"
        echo
        echo 'The paired reports use matching frozen inputs, SDK revisions, clip counts, and trials. Compare their WER, worst-clip errors, final-word failures, and p50 latency; these values are diagnostic and have no pass threshold.'
        echo
        echo 'The Spanish hint filters output script; it does not force language identification. FLEURS is read speech and does not qualify conversational/spontaneous Spanish dictation. Keep any private Spanish speech, references, and raw reports local.'
    } >"$path"
}

run_self_test() {
    local revision archive_hash row i id
    SELF_TEST_TMPDIR="$(mktemp -d "${TMPDIR:-/tmp}/presspeech-spanish-probe-test.XXXXXX")"
    trap '[[ -z "$SELF_TEST_TMPDIR" ]] || rm -rf -- "$SELF_TEST_TMPDIR"' EXIT INT TERM
    revision="$(fleurs_revision)"
    archive_hash="$(printf 'a%.0s' {1..64})"
    mkdir -p "$SELF_TEST_TMPDIR/fixtures"
    touch "$SELF_TEST_TMPDIR/fixtures/.presspeech-public-fixtures"
    {
        echo 'Generated public Presspeech benchmark fixtures.'
        echo 'Source: Google FLEURS speech corpus, locale es_419, split test'
        echo 'License: CC BY 4.0'
        echo "Dataset revision: $revision"
        echo "Archive SHA-256: $archive_hash"
    } >"$SELF_TEST_TMPDIR/fixtures/README.txt"
    printf 'clip_id\tsource\tsplit\toriginal_id\toriginal_audio\tlicense\treference\n' >"$SELF_TEST_TMPDIR/fixtures/manifest.tsv"
    for ((i = 1; i <= MINIMUM_CLIPS; i++)); do
        printf -v id 'fleurs-es_419-test-%04d-%d' "$i" "$((100000 + i))"
        printf 'synthetic test reference %d\n' "$i" >"$SELF_TEST_TMPDIR/fixtures/$id.txt"
        printf 'synthetic wav %d\n' "$i" >"$SELF_TEST_TMPDIR/fixtures/$id.wav"
        printf '%s\tFLEURS-es_419\ttest\t%d\t%s.wav\tCC BY 4.0\tsynthetic test reference %d\n' \
            "$id" "$((100000 + i))" "$((100000 + i))" "$i" >>"$SELF_TEST_TMPDIR/fixtures/manifest.tsv"
    done

    row="$(validate_fleurs_fixture "$SELF_TEST_TMPDIR/fixtures")"
    [[ "${row%%$'\t'*}" == "$MINIMUM_CLIPS" ]] || {
        echo 'self-test expected a valid Spanish FLEURS fixture set' >&2
        exit 1
    }
    if sed '2s/FLEURS-es_419/FLEURS-de_de/' "$SELF_TEST_TMPDIR/fixtures/manifest.tsv" >"$SELF_TEST_TMPDIR/manifest.bad"; then
        cp "$SELF_TEST_TMPDIR/manifest.bad" "$SELF_TEST_TMPDIR/fixtures/manifest.tsv"
        if validate_fleurs_fixture "$SELF_TEST_TMPDIR/fixtures" >/dev/null 2>&1; then
            echo 'self-test accepted a fixture manifest with the wrong language' >&2
            exit 1
        fi
        printf 'clip_id\tsource\tsplit\toriginal_id\toriginal_audio\tlicense\treference\n' >"$SELF_TEST_TMPDIR/fixtures/manifest.tsv"
        for ((i = 1; i <= MINIMUM_CLIPS; i++)); do
            printf -v id 'fleurs-es_419-test-%04d-%d' "$i" "$((100000 + i))"
            printf '%s\tFLEURS-es_419\ttest\t%d\t%s.wav\tCC BY 4.0\tsynthetic test reference %d\n' \
                "$id" "$((100000 + i))" "$((100000 + i))" "$i" >>"$SELF_TEST_TMPDIR/fixtures/manifest.tsv"
        done
    fi

    for language in auto es; do
        cat >"$SELF_TEST_TMPDIR/$language.md" <<REPORT
# Synthetic self-test report
- Backend: v3
- FluidAudio revision: 0123456789abcdef0123456789abcdef01234567
- App FluidAudio revision: 0123456789abcdef0123456789abcdef01234567
- Benchmark inputs SHA-256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
- Trials per clip: 3
- Parakeet TDT v3 language/script hint: $language
- Clips: $MINIMUM_CLIPS
REPORT
        load_report_metadata "$SELF_TEST_TMPDIR/$language.md" "$language"
    done
    local auto_sha="$REPORT_SHA" auto_fluid="$REPORT_FLUID_REVISION"
    local auto_app="$REPORT_APP_REVISION" auto_clips="$REPORT_CLIPS" auto_trials="$REPORT_TRIALS"
    load_report_metadata "$SELF_TEST_TMPDIR/es.md" es
    validate_report_pair "$auto_sha" "$auto_fluid" "$auto_app" "$auto_clips" "$auto_trials" \
        "$REPORT_SHA" "$REPORT_FLUID_REVISION" "$REPORT_APP_REVISION" "$REPORT_CLIPS" "$REPORT_TRIALS" \
        "$MINIMUM_CLIPS" 3
    if load_report_metadata "$SELF_TEST_TMPDIR/es.md" auto >/dev/null 2>&1; then
        echo 'self-test accepted a report with the wrong language hint' >&2
        exit 1
    fi
    sed 's/^- App FluidAudio revision: .*/- App FluidAudio revision: ffffffffffffffffffffffffffffffffffffffff/' \
        "$SELF_TEST_TMPDIR/es.md" >"$SELF_TEST_TMPDIR/candidate-sdk.md"
    if load_report_metadata "$SELF_TEST_TMPDIR/candidate-sdk.md" es >/dev/null 2>&1; then
        echo 'self-test accepted a report from a non-production SDK pin' >&2
        exit 1
    fi
    sed 's/0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef/ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff/' \
        "$SELF_TEST_TMPDIR/es.md" >"$SELF_TEST_TMPDIR/mismatch.md"
    load_report_metadata "$SELF_TEST_TMPDIR/mismatch.md" es
    if validate_report_pair "$auto_sha" "$auto_fluid" "$auto_app" "$auto_clips" "$auto_trials" \
        "$REPORT_SHA" "$REPORT_FLUID_REVISION" "$REPORT_APP_REVISION" "$REPORT_CLIPS" "$REPORT_TRIALS" \
        "$MINIMUM_CLIPS" 3 >/dev/null 2>&1; then
        echo 'self-test accepted paired reports with changed input digests' >&2
        exit 1
    fi

    rm -rf -- "$SELF_TEST_TMPDIR"
    SELF_TEST_TMPDIR=""
    trap - EXIT INT TERM
    echo 'Spanish language-probe self-test passed'
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
            OUT_DIR="$2"
            shift 2
            ;;
        --trials)
            need_value "$@"
            TRIALS="$2"
            shift 2
            ;;
        --show-transcripts)
            SHOW_TRANSCRIPTS=1
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

fixture_metadata="$(validate_fleurs_fixture "$INPUT_DIR")" || exit 1
IFS=$'\t' read -r fixture_count fixture_revision fixture_archive_sha <<<"$fixture_metadata"
python3 ./dependency-provenance.py --require-production >/dev/null || exit 1
mkdir -p "$OUT_DIR/auto" "$OUT_DIR/es-hint"

run_probe_case 'automatic language selection' auto "$OUT_DIR/auto"
auto_report="$RUN_REPORT"
auto_sha="$RUN_SHA"
auto_fluid_revision="$RUN_FLUID_REVISION"
auto_app_revision="$RUN_APP_REVISION"
auto_clips="$RUN_CLIPS"
auto_trials="$RUN_TRIALS"

run_probe_case 'Spanish script-filter hint' es "$OUT_DIR/es-hint"
hinted_report="$RUN_REPORT"

validate_report_pair "$auto_sha" "$auto_fluid_revision" "$auto_app_revision" "$auto_clips" "$auto_trials" \
    "$RUN_SHA" "$RUN_FLUID_REVISION" "$RUN_APP_REVISION" "$RUN_CLIPS" "$RUN_TRIALS" \
    "$fixture_count" "$TRIALS"

summary_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
summary="$OUT_DIR/spanish-language-probe-$summary_timestamp.md"
if [[ -e "$summary" || -L "$summary" ]]; then
    echo "Spanish probe summary already exists: $summary" >&2
    exit 1
fi
write_pair_summary "$summary" "$fixture_count" "$fixture_revision" \
    "$fixture_archive_sha" "$auto_report" "$hinted_report" "$auto_sha" \
    "$auto_fluid_revision" "$auto_app_revision"

echo "paired Spanish diagnostic complete (report-only): $summary"
