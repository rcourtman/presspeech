#!/usr/bin/env bash
# Run a v3-versus-candidate comparison on generated public speech fixtures.

set -euo pipefail

SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$SCRIPT_PATH")"

SOURCE="librispeech"
SPLIT="dev-clean"
COUNT="25"
START_INDEX="0"
FIXTURE_DIR=""
OUTDIR="public-results"
TRIALS="3"
CANDIDATE_BACKEND="unified"
UNIFIED_TRAILING_SILENCE_MS="250"
REQUIRE_CANDIDATE_PASS=0
FETCH=0
FORCE_FETCH=0
SELF_TEST=0

usage() {
    cat <<'USAGE'
usage: ./run-public-model-comparison.sh [options]

Options:
  --fetch                 fetch public fixtures before running comparison
  --source <name>         public corpus for --fetch: librispeech (default: librispeech)
  --split <name>          source split for --fetch (default: dev-clean)
  --count <n>             number of public clips to fetch (default: 25)
  --start-index <n>       zero-based source offset for --fetch (default: 0)
  --force-fetch           replace existing generated fixtures when fetching
  --fixture-dir <path>    public audio + .txt sidecar directory
                          (default: public-audio/librispeech-<split>)
  --out-dir <path>        report directory (default: public-results)
  --trials <n>            measured trials per clip/backend (default: 3)
  --candidate-backend <name>
                          comparison backend: unified, v2, or v3-int8-v2
                          (default: unified)
  --unified-trailing-silence-ms <n>
                          Unified-only trailing silence in ms (default: 250)
  --require-candidate-pass
                          fail unless the candidate evidence screen passes
  --self-test             run parser/detection self-tests only
  -h, --help              show this help

Examples:
  ./run-public-model-comparison.sh --fetch --count 50 --trials 3
  ./run-public-model-comparison.sh --fixture-dir public-audio/librispeech-test-other --candidate-backend v2 --trials 5
  ./run-public-model-comparison.sh --candidate-backend v2 --trials 3 --require-candidate-pass
USAGE
}

need_value() {
    if [[ $# -lt 2 || -z "$2" || "$2" == --* ]]; then
        echo "$1 requires a value" >&2
        usage >&2
        exit 2
    fi
}

is_positive_integer() {
    [[ "$1" =~ ^[0-9]+$ ]] && [[ "$1" -ge 1 ]]
}

is_nonnegative_integer() {
    [[ "$1" =~ ^[0-9]+$ ]]
}

default_fixture_dir() {
    local source="$1"
    local split="$2"
    case "$source" in
        librispeech) printf 'public-audio/librispeech-%s' "$split" ;;
        *) printf 'public-audio/%s-%s' "$source" "$split" ;;
    esac
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

build_compare_args() {
    COMPARE_ARGS=(
        "./run-real-model-comparison.sh"
        "--input-dir" "$FIXTURE_DIR"
        "--out-dir" "$OUTDIR"
        "--trials" "$TRIALS"
        "--candidate-backend" "$CANDIDATE_BACKEND"
        "--unified-trailing-silence-ms" "$UNIFIED_TRAILING_SILENCE_MS"
        "--public-corpus"
        "--show-transcripts"
        "--show-paths"
    )
    # The product would select either English-only model only for an explicit
    # English dictation setting, so compare it with production v3 under the
    # same hint rather than giving the candidate an easier auto-detected
    # baseline.
    if [[ "$CANDIDATE_BACKEND" == "unified" || "$CANDIDATE_BACKEND" == "v2" ]]; then
        COMPARE_ARGS+=( "--language" "en" )
    fi
    if [[ "$REQUIRE_CANDIDATE_PASS" -eq 1 ]]; then
        COMPARE_ARGS+=( "--require-candidate-pass" )
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

run_self_test() {
    local tmpdir
    tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/presspeech-public-compare-self-test.XXXXXX")"
    trap 'rm -rf "$tmpdir"' EXIT INT TERM

    assert_eq "$(default_fixture_dir librispeech dev-clean)" "public-audio/librispeech-dev-clean" "default fixture dir"
    assert_eq "$(default_fixture_dir other split-a)" "public-audio/other-split-a" "fallback fixture dir"

    mkdir -p "$tmpdir/fixtures/nested"
    touch "$tmpdir/fixtures/one.wav"
    touch "$tmpdir/fixtures/two.flac"
    touch "$tmpdir/fixtures/nested/three.mp3"
    touch "$tmpdir/fixtures/ignore.txt"
    assert_eq "$(supported_audio_count "$tmpdir/fixtures")" "3" "supported audio detection"
    assert_eq "$(supported_audio_count "$tmpdir/missing")" "0" "missing audio directory detection"

    FIXTURE_DIR="$tmpdir/fixtures"
    OUTDIR="$tmpdir/results"
    CANDIDATE_BACKEND="v2"
    REQUIRE_CANDIDATE_PASS=1
    build_compare_args
    assert_eq "${COMPARE_ARGS[4]}" "$OUTDIR" "comparison output forwarding"
    assert_eq "${COMPARE_ARGS[8]}" "v2" "candidate backend forwarding"
    assert_eq "${COMPARE_ARGS[${#COMPARE_ARGS[@]} - 3]}" "--language" "English hint forwarding"
    assert_eq "${COMPARE_ARGS[${#COMPARE_ARGS[@]} - 2]}" "en" "English language forwarding"
    assert_eq "${COMPARE_ARGS[${#COMPARE_ARGS[@]} - 1]}" "--require-candidate-pass" "candidate gate forwarding"

    local missing_value_log="$tmpdir/missing-value.log"
    if bash "$SCRIPT_PATH" --trials >"$missing_value_log" 2>&1; then
        echo "self-test expected --trials without a value to fail" >&2
        exit 1
    fi
    assert_contains "$missing_value_log" "--trials requires a value"

    rm -rf "$tmpdir"
    trap - EXIT INT TERM
    echo "public model comparison self-test passed"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --fetch)
            FETCH=1
            shift
            ;;
        --source)
            need_value "$@"
            SOURCE="$2"
            shift 2
            ;;
        --split)
            need_value "$@"
            SPLIT="$2"
            shift 2
            ;;
        --count)
            need_value "$@"
            COUNT="$2"
            shift 2
            ;;
        --start-index)
            need_value "$@"
            START_INDEX="$2"
            shift 2
            ;;
        --force-fetch)
            FORCE_FETCH=1
            shift
            ;;
        --fixture-dir)
            need_value "$@"
            FIXTURE_DIR="$2"
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
        --unified-trailing-silence-ms)
            need_value "$@"
            UNIFIED_TRAILING_SILENCE_MS="$2"
            shift 2
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
    run_self_test
    exit 0
fi

if ! is_positive_integer "$COUNT"; then
    echo "--count must be a positive integer" >&2
    exit 2
fi

if ! is_nonnegative_integer "$START_INDEX"; then
    echo "--start-index must be a non-negative integer" >&2
    exit 2
fi

if ! is_positive_integer "$TRIALS"; then
    echo "--trials must be a positive integer" >&2
    exit 2
fi

case "$CANDIDATE_BACKEND" in
    unified|v2|v3-int8-v2) ;;
    *)
        echo "--candidate-backend must be unified, v2, or v3-int8-v2" >&2
        exit 2
        ;;
esac

if ! is_nonnegative_integer "$UNIFIED_TRAILING_SILENCE_MS"; then
    echo "--unified-trailing-silence-ms must be a non-negative integer" >&2
    exit 2
fi

if [[ -z "$FIXTURE_DIR" ]]; then
    FIXTURE_DIR="$(default_fixture_dir "$SOURCE" "$SPLIT")"
fi

if [[ "$FETCH" -eq 1 ]]; then
    fetch_args=( "./fetch-public-speech-fixtures.sh" "--source" "$SOURCE" "--split" "$SPLIT" "--count" "$COUNT" "--start-index" "$START_INDEX" )
    fetch_args+=( "--fixture-dir" "$FIXTURE_DIR" )
    if [[ "$FORCE_FETCH" -eq 1 ]]; then
        fetch_args+=( "--force" )
    fi
    "${fetch_args[@]}"
fi

clip_count="$(supported_audio_count "$FIXTURE_DIR")"
if [[ "$clip_count" -eq 0 ]]; then
    cat >&2 <<MSG
no public audio clips found in $FIXTURE_DIR

Fetch a public fixture set first:
  ./run-public-model-comparison.sh --fetch --count $COUNT
MSG
    exit 1
fi

echo "running public v3-vs-$CANDIDATE_BACKEND ASR comparison on $clip_count clip(s)..."
build_compare_args
"${COMPARE_ARGS[@]}"
