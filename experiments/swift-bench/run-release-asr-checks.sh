#!/usr/bin/env bash
# Release-oriented ASR quality checks.
#
# This intentionally lives outside ship-swift.sh: private real-dictation
# fixtures are local maintainer data, not a release-script dependency.

set -euo pipefail

SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$SCRIPT_PATH")"

REAL_AUDIO_DIR="real-audio"
PUBLIC_AUDIO_DIR="public-audio/librispeech-dev-clean"
LONG_PUBLIC_AUDIO_DIR="public-audio/librispeech-dev-clean-long-form"
TRIALS="3"
REQUIRE_REAL_AUDIO=0
REQUIRE_PUBLIC_AUDIO=0
# Multi-window coverage is the one public corpus this release-oriented wrapper
# requires by default. The app accepts recordings up to ten minutes, while the
# production CoreML encoder operates on 15-second windows; silently reducing a
# release check to short utterances would miss a distinct quality path.
REQUIRE_LONG_PUBLIC_AUDIO=1
INCLUDE_CANDIDATE_MODELS=0
ALLOW_CANDIDATE_DEPENDENCY=0
RUN_TAIL=1
SELF_TEST=0
LONG_PUBLIC_MAX_REFERENCE_DELETION_RUN="6"
LONG_PUBLIC_MAX_CORPUS_WER="10"
DEPENDENCY_MODE="production"

usage() {
    cat <<'USAGE'
usage: ./run-release-asr-checks.sh [options]

Options:
  --real-audio-dir <path>   private real-dictation fixtures (default: real-audio)
  --public-audio-dir <path> public speech fixtures (default: public-audio/librispeech-dev-clean)
  --long-public-audio-dir <path>
                            composed multi-window public fixtures
                            (default: public-audio/librispeech-dev-clean-long-form)
  --trials <n>              trials per clip/backend (default: 3)
  --require-real-audio      fail if no private real-dictation clips are present
  --require-public-audio    fail if no public speech clips are present
  --require-long-public-audio
                            fail if no composed multi-window fixtures are present (default)
  --allow-missing-long-public-audio
                            allow a lightweight run to skip multi-window coverage
  --long-public-max-reference-deletion-run <n>
                            fail the multi-window gate above this consecutive
                            dropped-reference-word count (default: 6)
  --long-public-max-corpus-wer <percent>
                            fail if conservative multi-window corpus WER exceeds
                            this percentage (default: 10)
  --include-candidate-models
                            also run Parakeet v2, linear-int8 v3, Unified,
                            and current Nemotron candidate checks
  --allow-candidate-dependency
                            permit the benchmark package to differ from the
                            production app pin; requires --include-candidate-models
                            and produces candidate evidence, not a release pass
  --skip-tail               with --include-candidate-models, skip the synthetic tail-word gate
  --self-test               run wrapper parser/detection tests only
  -h, --help                show this help

The default run performs:
  1. helper parser/self-tests,
  2. production v3 regression if private real-dictation fixtures exist,
  3. production v3 regression if public speech fixtures exist,
  4. required production v3 multi-window regression over validated composed fixtures.

Candidate models are not shipped by the app. Use --include-candidate-models
only when evaluating whether a future model is good enough to expose.
By default, the benchmark and production app must pin the exact same
FluidAudio revision. This prevents a candidate API experiment from silently
turning the production-v3 release gate into a test of different library code.
USAGE
}

need_value() {
    if [[ $# -lt 2 || -z "$2" || "$2" == --* ]]; then
        echo "$1 requires a value" >&2
        usage >&2
        exit 2
    fi
}

supported_audio_count() {
    local dir="$1"
    if [[ ! -d "$dir" ]]; then
        printf '0'
        return
    fi
    find "$dir" -type f \
        \( -iname '*.wav' -o -iname '*.aiff' -o -iname '*.aif' -o -iname '*.caf' -o -iname '*.m4a' -o -iname '*.mp3' -o -iname '*.flac' \) \
        | wc -l | tr -d '[:space:]'
}

fluid_revision_from_package() {
    local package_file="$1"
    local revisions
    revisions="$(sed -nE 's/.*revision: "([0-9a-f]{40})".*/\1/p' "$package_file")"
    if [[ "$(printf '%s\n' "$revisions" | sed '/^$/d' | wc -l | tr -d '[:space:]')" != "1" ]]; then
        echo "expected exactly one pinned FluidAudio revision in $package_file" >&2
        return 1
    fi
    printf '%s' "$revisions"
}

validate_fluid_dependency_alignment() {
    local production_package="$1"
    local benchmark_package="$2"
    local allow_candidate="$3"
    local include_candidates="$4"
    local production_revision benchmark_revision

    production_revision="$(fluid_revision_from_package "$production_package")" || return 1
    benchmark_revision="$(fluid_revision_from_package "$benchmark_package")" || return 1

    if [[ "$production_revision" == "$benchmark_revision" ]]; then
        if [[ "$allow_candidate" -eq 1 ]]; then
            echo "--allow-candidate-dependency was supplied, but benchmark and production pins already match" >&2
            return 2
        fi
        DEPENDENCY_MODE="production"
        return 0
    fi

    if [[ "$allow_candidate" -ne 1 ]]; then
        cat >&2 <<MSG
benchmark FluidAudio pin does not match the production app
  production: $production_revision
  benchmark:  $benchmark_revision
Refusing to label benchmark-revision transcripts as production release evidence.
Restore benchmark Package.swift to the production pin, or use
--include-candidate-models --allow-candidate-dependency for an explicit
candidate-only comparison.
MSG
        return 1
    fi
    if [[ "$include_candidates" -ne 1 ]]; then
        echo "--allow-candidate-dependency requires --include-candidate-models" >&2
        return 2
    fi

    DEPENDENCY_MODE="candidate"
    cat >&2 <<MSG
warning: candidate dependency mode
  production: $production_revision
  benchmark:  $benchmark_revision
Results from this run do not validate the production app's FluidAudio code.
MSG
}

v3_baseline_label() {
    if [[ "$DEPENDENCY_MODE" == "production" ]]; then
        printf 'production v3'
    else
        printf 'candidate-revision v3 baseline'
    fi
}

final_verdict() {
    if [[ "$DEPENDENCY_MODE" != "production" ]]; then
        echo "candidate ASR evaluation completed"
        echo "not a production release-gate pass: benchmark and app FluidAudio pins differ"
    elif [[ "$REQUIRE_LONG_PUBLIC_AUDIO" -ne 1 ]]; then
        echo "lightweight ASR checks completed"
        echo "not a production release-gate pass: multi-window coverage was optional"
    else
        echo "release ASR checks passed"
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
        echo "self-test expected output to contain: $needle" >&2
        exit 1
    fi
}

assert_not_contains() {
    local file="$1"
    local needle="$2"
    if grep -Fq -- "$needle" "$file"; then
        echo "self-test expected output not to contain: $needle" >&2
        exit 1
    fi
}

run_self_test() {
    local tmpdir
    tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/presspeech-release-asr-self-test.XXXXXX")"
    trap 'rm -rf "$tmpdir"' EXIT INT TERM

    mkdir -p "$tmpdir/fixtures/nested"
    touch "$tmpdir/fixtures/one.wav"
    touch "$tmpdir/fixtures/two.m4a"
    touch "$tmpdir/fixtures/two-and-a-half.flac"
    touch "$tmpdir/fixtures/ignore.txt"
    touch "$tmpdir/fixtures/nested/three.caf"

    assert_eq "$(supported_audio_count "$tmpdir/fixtures")" "4" "supported audio detection"
    assert_eq "$(supported_audio_count "$tmpdir/missing")" "0" "missing audio directory detection"

    local production_sha="1111111111111111111111111111111111111111"
    local candidate_sha="2222222222222222222222222222222222222222"
    printf '.package(url: "https://example.invalid/FluidAudio.git", revision: "%s")\n' \
        "$production_sha" >"$tmpdir/production-package.swift"
    cp "$tmpdir/production-package.swift" "$tmpdir/matching-package.swift"
    printf '.package(url: "https://example.invalid/FluidAudio.git", revision: "%s")\n' \
        "$candidate_sha" >"$tmpdir/candidate-package.swift"
    assert_eq "$(fluid_revision_from_package "$tmpdir/production-package.swift")" \
        "$production_sha" "FluidAudio revision extraction"

    DEPENDENCY_MODE="unset"
    validate_fluid_dependency_alignment \
        "$tmpdir/production-package.swift" "$tmpdir/matching-package.swift" 0 0
    assert_eq "$DEPENDENCY_MODE" "production" "matching production dependency mode"

    local mismatch_log="$tmpdir/dependency-mismatch.log"
    if validate_fluid_dependency_alignment \
        "$tmpdir/production-package.swift" "$tmpdir/candidate-package.swift" 0 0 \
        >"$mismatch_log" 2>&1; then
        echo "self-test expected a benchmark dependency mismatch to fail closed" >&2
        exit 1
    fi
    assert_contains "$mismatch_log" \
        "Refusing to label benchmark-revision transcripts as production release evidence."

    local unscoped_candidate_log="$tmpdir/unscoped-candidate.log"
    if validate_fluid_dependency_alignment \
        "$tmpdir/production-package.swift" "$tmpdir/candidate-package.swift" 1 0 \
        >"$unscoped_candidate_log" 2>&1; then
        echo "self-test expected candidate dependency mode without candidate models to fail" >&2
        exit 1
    fi
    assert_contains "$unscoped_candidate_log" \
        "--allow-candidate-dependency requires --include-candidate-models"

    DEPENDENCY_MODE="unset"
    validate_fluid_dependency_alignment \
        "$tmpdir/production-package.swift" "$tmpdir/candidate-package.swift" 1 1 \
        >"$tmpdir/candidate-mode.log" 2>&1
    assert_eq "$DEPENDENCY_MODE" "candidate" "explicit candidate dependency mode"
    assert_contains "$tmpdir/candidate-mode.log" \
        "Results from this run do not validate the production app's FluidAudio code."

    DEPENDENCY_MODE="production"
    REQUIRE_LONG_PUBLIC_AUDIO=1
    assert_eq "$(final_verdict)" "release ASR checks passed" "release verdict"
    REQUIRE_LONG_PUBLIC_AUDIO=0
    assert_contains <(final_verdict) \
        "not a production release-gate pass: multi-window coverage was optional"
    DEPENDENCY_MODE="candidate"
    assert_contains <(final_verdict) \
        "not a production release-gate pass: benchmark and app FluidAudio pins differ"

    local missing_value_log="$tmpdir/missing-value.log"
    if bash "$SCRIPT_PATH" --trials >"$missing_value_log" 2>&1; then
        echo "self-test expected --trials without a value to fail" >&2
        exit 1
    fi
    assert_contains "$missing_value_log" "--trials requires a value"

    local missing_deletion_value_log="$tmpdir/missing-deletion-value.log"
    if bash "$SCRIPT_PATH" --long-public-max-reference-deletion-run >"$missing_deletion_value_log" 2>&1; then
        echo "self-test expected the deletion-run option without a value to fail" >&2
        exit 1
    fi
    assert_contains "$missing_deletion_value_log" \
        "--long-public-max-reference-deletion-run requires a value"

    local missing_wer_value_log="$tmpdir/missing-wer-value.log"
    if bash "$SCRIPT_PATH" --long-public-max-corpus-wer >"$missing_wer_value_log" 2>&1; then
        echo "self-test expected the corpus-WER option without a value to fail" >&2
        exit 1
    fi
    assert_contains "$missing_wer_value_log" \
        "--long-public-max-corpus-wer requires a value"

    local missing_real_log="$tmpdir/missing-real.log"
    if bash "$SCRIPT_PATH" \
        --real-audio-dir "$tmpdir/missing-real" \
        --public-audio-dir "$tmpdir/missing-public" \
        --require-real-audio >"$missing_real_log" 2>&1; then
        echo "self-test expected a required missing real corpus to fail" >&2
        exit 1
    fi
    assert_contains "$missing_real_log" \
        "no private real-dictation clips found in $tmpdir/missing-real"
    assert_not_contains "$missing_real_log" "running helper self-tests"

    local missing_public_log="$tmpdir/missing-public.log"
    if bash "$SCRIPT_PATH" \
        --real-audio-dir "$tmpdir/missing-real" \
        --public-audio-dir "$tmpdir/missing-public" \
        --require-public-audio >"$missing_public_log" 2>&1; then
        echo "self-test expected a required missing public corpus to fail" >&2
        exit 1
    fi
    assert_contains "$missing_public_log" \
        "no public speech clips found in $tmpdir/missing-public"
    assert_not_contains "$missing_public_log" "running helper self-tests"

    local missing_long_public_log="$tmpdir/missing-long-public.log"
    if bash "$SCRIPT_PATH" \
        --real-audio-dir "$tmpdir/missing-real" \
        --public-audio-dir "$tmpdir/missing-public" \
        --long-public-audio-dir "$tmpdir/missing-long-public" \
        --require-long-public-audio >"$missing_long_public_log" 2>&1; then
        echo "self-test expected required missing long-form public corpus to fail" >&2
        exit 1
    fi
    assert_contains "$missing_long_public_log" \
        "no long-form public speech clips found in $tmpdir/missing-long-public"
    assert_not_contains "$missing_long_public_log" "running helper self-tests"

    local default_missing_long_public_log="$tmpdir/default-missing-long-public.log"
    if bash "$SCRIPT_PATH" \
        --real-audio-dir "$tmpdir/missing-real" \
        --public-audio-dir "$tmpdir/missing-public" \
        --long-public-audio-dir "$tmpdir/missing-long-public" \
        >"$default_missing_long_public_log" 2>&1; then
        echo "self-test expected default missing long-form public corpus to fail" >&2
        exit 1
    fi
    assert_contains "$default_missing_long_public_log" \
        "no long-form public speech clips found in $tmpdir/missing-long-public"
    assert_not_contains "$default_missing_long_public_log" "running helper self-tests"

    local invalid_long_public="$tmpdir/invalid-long-public"
    mkdir -p "$invalid_long_public"
    touch "$invalid_long_public/not-a-composite.wav"
    local invalid_long_public_log="$tmpdir/invalid-long-public.log"
    if bash "$SCRIPT_PATH" \
        --real-audio-dir "$tmpdir/missing-real" \
        --public-audio-dir "$tmpdir/missing-public" \
        --long-public-audio-dir "$invalid_long_public" \
        >"$invalid_long_public_log" 2>&1; then
        echo "self-test expected invalid long-form public corpus to fail" >&2
        exit 1
    fi
    assert_contains "$invalid_long_public_log" \
        "long-form output is not owned by this composer"
    assert_not_contains "$invalid_long_public_log" "running helper self-tests"

    rm -rf "$tmpdir"
    trap - EXIT INT TERM
    echo "release ASR checks self-test passed"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --real-audio-dir)
            need_value "$@"
            REAL_AUDIO_DIR="$2"
            shift 2
            ;;
        --public-audio-dir)
            need_value "$@"
            PUBLIC_AUDIO_DIR="$2"
            shift 2
            ;;
        --long-public-audio-dir)
            need_value "$@"
            LONG_PUBLIC_AUDIO_DIR="$2"
            shift 2
            ;;
        --trials)
            need_value "$@"
            TRIALS="$2"
            shift 2
            ;;
        --require-real-audio)
            REQUIRE_REAL_AUDIO=1
            shift
            ;;
        --require-public-audio)
            REQUIRE_PUBLIC_AUDIO=1
            shift
            ;;
        --require-long-public-audio)
            REQUIRE_LONG_PUBLIC_AUDIO=1
            shift
            ;;
        --allow-missing-long-public-audio)
            REQUIRE_LONG_PUBLIC_AUDIO=0
            shift
            ;;
        --long-public-max-reference-deletion-run)
            need_value "$@"
            LONG_PUBLIC_MAX_REFERENCE_DELETION_RUN="$2"
            shift 2
            ;;
        --long-public-max-corpus-wer)
            need_value "$@"
            LONG_PUBLIC_MAX_CORPUS_WER="$2"
            shift 2
            ;;
        --include-candidate-models)
            INCLUDE_CANDIDATE_MODELS=1
            shift
            ;;
        --allow-candidate-dependency)
            ALLOW_CANDIDATE_DEPENDENCY=1
            shift
            ;;
        --skip-tail)
            RUN_TAIL=0
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
if ! [[ "$LONG_PUBLIC_MAX_REFERENCE_DELETION_RUN" =~ ^[0-9]+$ ]]; then
    echo "--long-public-max-reference-deletion-run must be a non-negative integer" >&2
    exit 2
fi
if ! [[ "$LONG_PUBLIC_MAX_CORPUS_WER" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    echo "--long-public-max-corpus-wer must be a non-negative decimal percentage" >&2
    exit 2
fi

# Required fixture sets are invocation preconditions. Validate both before
# helper checks or benchmarks can create reports, so a release gate cannot do
# partial work and only then discover that its requested coverage is absent.
real_count="$(supported_audio_count "$REAL_AUDIO_DIR")"
public_count="$(supported_audio_count "$PUBLIC_AUDIO_DIR")"
long_public_count="$(supported_audio_count "$LONG_PUBLIC_AUDIO_DIR")"
if [[ "$REQUIRE_REAL_AUDIO" -eq 1 && "$real_count" -eq 0 ]]; then
    echo "no private real-dictation clips found in $REAL_AUDIO_DIR" >&2
    exit 1
fi
if [[ "$REQUIRE_PUBLIC_AUDIO" -eq 1 && "$public_count" -eq 0 ]]; then
    echo "no public speech clips found in $PUBLIC_AUDIO_DIR" >&2
    exit 1
fi
if [[ "$REQUIRE_LONG_PUBLIC_AUDIO" -eq 1 && "$long_public_count" -eq 0 ]]; then
    echo "no long-form public speech clips found in $LONG_PUBLIC_AUDIO_DIR" >&2
    exit 1
fi
if [[ "$long_public_count" -gt 0 ]]; then
    # Do not let a mislabeled short clip satisfy the multi-window gate. The
    # composer verifies ownership, paired references, manifest provenance,
    # multiple composites, and at least two 15-second windows per clip.
    python3 ./compose-public-long-form-fixtures.py \
        --validate-output-dir \
        --output-dir "$LONG_PUBLIC_AUDIO_DIR"
fi

validate_fluid_dependency_alignment \
    "../../swift/Package.swift" "Package.swift" \
    "$ALLOW_CANDIDATE_DEPENDENCY" "$INCLUDE_CANDIDATE_MODELS"

echo "running helper self-tests..."
./run-tail-word-regression.sh --self-test
./add-real-dictation-fixture.sh --self-test
./fetch-public-speech-fixtures.sh --self-test
python3 ./compose-public-long-form-fixtures.py --self-test
./run-real-dictation-regression.sh --self-test
./run-real-model-comparison.sh --self-test
./run-vocabulary-bias-regression.sh --self-test
./run-public-model-comparison.sh --self-test
./bench-power.sh --self-test

if [[ "$INCLUDE_CANDIDATE_MODELS" -eq 1 ]]; then
    if [[ "$RUN_TAIL" -eq 1 ]]; then
        echo
        echo "running candidate synthetic tail-word ASR gate..."
        ./run-tail-word-regression.sh
    else
        echo
        echo "skipping candidate synthetic tail-word ASR gate (--skip-tail)"
    fi
fi

if [[ "$real_count" -eq 0 ]]; then
    echo
    echo "no private real-dictation clips found in $REAL_AUDIO_DIR; skipped real-audio WER gates"
else
    echo
    echo "running private $(v3_baseline_label) ASR regression on $real_count clip(s)..."
    ./run-real-dictation-regression.sh --input-dir "$REAL_AUDIO_DIR" --backend v3 --trials "$TRIALS"
    if [[ "$INCLUDE_CANDIDATE_MODELS" -eq 1 ]]; then
        echo
        echo "running private v3-vs-Unified candidate comparison on $real_count clip(s)..."
        ./run-real-model-comparison.sh \
            --input-dir "$REAL_AUDIO_DIR" \
            --language en \
            --trials "$TRIALS" \
            --unified-trailing-silence-ms 250

        echo
        echo "running private v3-vs-Parakeet-v2 English candidate comparison on $real_count clip(s)..."
        ./run-real-model-comparison.sh \
            --input-dir "$REAL_AUDIO_DIR" \
            --candidate-backend v2 \
            --language en \
            --trials "$TRIALS"

        echo
        echo "running private v3 linear-int8 encoder candidate comparison on $real_count clip(s)..."
        ./run-real-model-comparison.sh \
            --input-dir "$REAL_AUDIO_DIR" \
            --candidate-backend v3-int8-v2 \
            --trials "$TRIALS"

        echo
        echo "running private repaired Nemotron English candidate regression on $real_count clip(s)..."
        ./run-real-dictation-regression.sh --input-dir "$REAL_AUDIO_DIR" --backend nemotron-en --trials "$TRIALS"

        echo
        echo "running private Nemotron 3.5 multilingual candidate regression on $real_count clip(s)..."
        ./run-real-dictation-regression.sh \
            --input-dir "$REAL_AUDIO_DIR" \
            --backend nemotron-multilingual \
            --nemotron-multilingual-language en-US \
            --nemotron-multilingual-chunk-ms 2240 \
            --trials "$TRIALS"
    fi
fi

if [[ "$public_count" -eq 0 ]]; then
    echo
    echo "no public speech clips found in $PUBLIC_AUDIO_DIR; skipped public WER gates"
else
    echo
    echo "running public $(v3_baseline_label) ASR regression on $public_count clip(s)..."
    ./run-real-dictation-regression.sh \
        --input-dir "$PUBLIC_AUDIO_DIR" \
        --out-dir public-results \
        --backend v3 \
        --trials "$TRIALS" \
        --public-corpus \
        --show-transcripts \
        --show-paths

    if [[ "$INCLUDE_CANDIDATE_MODELS" -eq 1 ]]; then
        echo
        echo "running public v3-vs-Unified candidate comparison on $public_count clip(s)..."
        ./run-public-model-comparison.sh --fixture-dir "$PUBLIC_AUDIO_DIR" --trials "$TRIALS" --unified-trailing-silence-ms 250

        echo
        echo "running public v3-vs-Parakeet-v2 English candidate comparison on $public_count clip(s)..."
        ./run-public-model-comparison.sh \
            --fixture-dir "$PUBLIC_AUDIO_DIR" \
            --candidate-backend v2 \
            --trials "$TRIALS"

        echo
        echo "running public v3 linear-int8 encoder candidate comparison on $public_count clip(s)..."
        ./run-public-model-comparison.sh \
            --fixture-dir "$PUBLIC_AUDIO_DIR" \
            --candidate-backend v3-int8-v2 \
            --trials "$TRIALS"

        echo
        echo "running public repaired Nemotron English candidate regression on $public_count clip(s)..."
        ./run-real-dictation-regression.sh \
            --input-dir "$PUBLIC_AUDIO_DIR" \
            --out-dir public-results \
            --backend nemotron-en \
            --trials "$TRIALS" \
            --public-corpus \
            --show-transcripts \
            --show-paths

        echo
        echo "running public Nemotron 3.5 multilingual candidate regression on $public_count clip(s)..."
        ./run-real-dictation-regression.sh \
            --input-dir "$PUBLIC_AUDIO_DIR" \
            --out-dir public-results \
            --backend nemotron-multilingual \
            --nemotron-multilingual-language en-US \
            --nemotron-multilingual-chunk-ms 2240 \
            --trials "$TRIALS" \
            --public-corpus \
            --show-transcripts \
            --show-paths
    fi
fi

if [[ "$long_public_count" -eq 0 ]]; then
    echo
    echo "no long-form public speech clips found in $LONG_PUBLIC_AUDIO_DIR; skipped multi-window WER gate (--allow-missing-long-public-audio)"
else
    echo
    echo "running long-form public $(v3_baseline_label) ASR regression on $long_public_count composite clip(s)..."
    ./run-real-dictation-regression.sh \
        --input-dir "$LONG_PUBLIC_AUDIO_DIR" \
        --out-dir public-results/long-form \
        --backend v3 \
        --trials "$TRIALS" \
        --public-corpus \
        --show-transcripts \
        --show-paths \
        --max-reference-deletion-run "$LONG_PUBLIC_MAX_REFERENCE_DELETION_RUN" \
        --max-corpus-wer "$LONG_PUBLIC_MAX_CORPUS_WER"

    if [[ "$INCLUDE_CANDIDATE_MODELS" -eq 1 ]]; then
        echo
        echo "running long-form public v3 linear-int8 encoder candidate comparison..."
        ./run-public-model-comparison.sh \
            --fixture-dir "$LONG_PUBLIC_AUDIO_DIR" \
            --out-dir public-results/long-form \
            --candidate-backend v3-int8-v2 \
            --trials "$TRIALS"
    fi
fi

echo
final_verdict
