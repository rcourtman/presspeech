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
MULTILINGUAL_LONG_PUBLIC_AUDIO_DIR="public-audio/fleurs-de_de-test-long-form"
TRIALS="3"
REQUIRE_REAL_AUDIO=1
REQUIRE_PUBLIC_AUDIO=0
# Multi-window coverage is required by default. The app accepts recordings up
# to ten minutes, while its production CoreML encoder operates on 15-second
# windows; silently reducing a release check to short utterances would miss a
# distinct quality path.
REQUIRE_LONG_PUBLIC_AUDIO=1
REQUIRE_MULTILINGUAL_LONG_PUBLIC_AUDIO=1
INCLUDE_CANDIDATE_MODELS=0
SDK_UPGRADE_ONLY=0
ALLOW_CANDIDATE_DEPENDENCY=0
RUN_TAIL=1
SELF_TEST=0
LONG_PUBLIC_MAX_REFERENCE_DELETION_RUN="6"
LONG_PUBLIC_MAX_CORPUS_WER="10"
DEPENDENCY_MODE="production"
MIN_PRIVATE_SPEECH_CLIPS=25
MIN_PRIVATE_REFERENCE_WORDS=1000
MIN_PRIVATE_NON_SPEECH_CONTROLS=5
REQUIRE_NON_SPEECH_CONTROLS=1
NON_SPEECH_CONTROLS_HAND_AUDITED=0

usage() {
    cat <<'USAGE'
usage: ./run-release-asr-checks.sh [options]

Options:
  --real-audio-dir <path>   private real-dictation fixtures (default: real-audio)
  --public-audio-dir <path> public speech fixtures (default: public-audio/librispeech-dev-clean)
  --long-public-audio-dir <path>
                            composed multi-window public fixtures
                            (default: public-audio/librispeech-dev-clean-long-form)
  --multilingual-long-public-audio-dir <path>
                            composed German FLEURS test fixtures
                            (default: public-audio/fleurs-de_de-test-long-form)
  --trials <n>              trials per clip/backend (default: 3)
  --require-real-audio      require private dictation references, at least
                            25 non-empty clips and 1,000 words (default)
  --allow-missing-real-audio
                            allow a lightweight run without private dictation;
                            this cannot report a production release-gate pass
  --non-speech-controls-hand-audited
                            attest that zero-byte-reference non-speech controls
                            were listened to and contain no intelligible speech
  --allow-missing-non-speech-controls
                            run without the required audited non-speech gate;
                            this cannot report a production release-gate pass
  --require-public-audio    fail if no public speech clips are present
  --require-long-public-audio
                            fail if no composed multi-window fixtures are present (default)
  --allow-missing-long-public-audio
                            allow a lightweight run to skip multi-window coverage
  --allow-missing-multilingual-long-public-audio
                            allow a lightweight run to skip German long-form coverage
  --long-public-max-reference-deletion-run <n>
                            fail the multi-window gate above this consecutive
                            dropped-reference-word count (default: 6)
  --long-public-max-corpus-wer <percent>
                            fail if conservative multi-window corpus WER exceeds
                            this percentage (default: 10)
  --include-candidate-models
                            on the production pin, also run explicit no-mel v3
                            checks on available private and short public clips,
                            plus Parakeet v2, Unified, and current Nemotron;
                            the required long-form no-mel comparison runs by
                            default. A candidate dependency run also compares
                            its SDK-default v3 chunking and includes the
                            opt-in linear-int8 v3 encoder
  --sdk-upgrade-only        with a candidate dependency, compare explicit
                            released v3 chunking with that SDK's v3 default;
                            skip unrelated model and encoder candidates
  --allow-candidate-dependency
                            permit the benchmark package to differ from the
                            production app pin; requires --include-candidate-models
                            or --sdk-upgrade-only and produces candidate
                            evidence, not a release pass
  --skip-tail               skip the short-clip tail diagnostic (and candidate gate when enabled)
  --self-test               run wrapper parser/detection tests only
  -h, --help                show this help

The default run performs:
  1. helper parser/self-tests,
  2. a report-only production-v3 short-clip diagnostic at 80 and 400 ms
     synthetic capture grace, plus complete-speech input-tail checks at 0,
     80, and 400 ms of appended silence,
  3. required production v3 regression over private real-dictation fixtures,
     including at least five audited non-speech controls with zero emitted text,
  4. production v3 regression if public speech fixtures exist,
  5. required production v3 multi-window regression over validated composed fixtures,
  6. required production v3 German FLEURS multi-window regression over validated
     German-source fixtures,
  7. non-gating same-pin production-v3 vs explicit no-mel comparison over English
     multi-window fixtures.

Candidate models and chunking policies are not shipped by the app. Use
--include-candidate-models only for explicit candidate evaluation. Use
--sdk-upgrade-only when the model is unchanged and the candidate is a newer
FluidAudio revision.
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

validated_fluid_revision() {
    python3 ./dependency-provenance.py --package "$1"
}

validate_fluid_dependency_alignment() {
    local production_package="$1"
    local benchmark_package="$2"
    local allow_candidate="$3"
    local candidate_scope="$4"
    local production_revision benchmark_revision

    production_revision="$(validated_fluid_revision "$production_package")" || return 1
    benchmark_revision="$(validated_fluid_revision "$benchmark_package")" || return 1

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
Restore benchmark Package.swift and Package.resolved to the production pin, or use
--include-candidate-models --allow-candidate-dependency for an explicit
candidate-only comparison.
MSG
        return 1
    fi
    if [[ "$candidate_scope" -ne 1 ]]; then
        echo "--allow-candidate-dependency requires --include-candidate-models or --sdk-upgrade-only" >&2
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

tail_word_check_mode() {
    if [[ "$INCLUDE_CANDIDATE_MODELS" -eq 1 ]]; then
        printf 'candidate'
    else
        printf 'production-v3-only'
    fi
}

final_verdict() {
    if [[ "$DEPENDENCY_MODE" != "production" ]]; then
        echo "candidate ASR evaluation completed"
        echo "not a production release-gate pass: benchmark and app FluidAudio pins differ"
    elif [[ "$REQUIRE_REAL_AUDIO" -ne 1 ]]; then
        echo "lightweight ASR checks completed"
        echo "not a production release-gate pass: private real-dictation coverage was optional"
    elif [[ "$REQUIRE_NON_SPEECH_CONTROLS" -ne 1 || \
            "$NON_SPEECH_CONTROLS_HAND_AUDITED" -ne 1 ]]; then
        echo "lightweight ASR checks completed"
        echo "not a production release-gate pass: audited non-speech controls were optional"
    elif [[ "$REQUIRE_LONG_PUBLIC_AUDIO" -ne 1 || \
            "$REQUIRE_MULTILINGUAL_LONG_PUBLIC_AUDIO" -ne 1 ]]; then
        echo "lightweight ASR checks completed"
        echo "not a production release-gate pass: English or German multi-window coverage was optional"
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
    python3 ./benchmark-inputs.py --self-test
    ./run-spanish-language-probe.sh --self-test
    # Long-form fixtures are a default release-gate precondition, not an
    # optional candidate helper. Keep their composition and validation tests
    # inside this wrapper's self-test so CI cannot report the release boundary
    # healthy while its required multi-window corpus helper has rotted.
    python3 ./compose-public-long-form-fixtures.py --self-test
    # The shifted-copy probe itself is report-only, but its pairing and
    # safe-replacement checks should remain runnable for SDK evaluations.
    python3 ./compose-public-window-shift-fixtures.py --self-test
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
    printf '.package(url: "https://github.com/FluidInference/FluidAudio.git", revision: "%s")\n' \
        "$production_sha" >"$tmpdir/production-package.swift"
    cp "$tmpdir/production-package.swift" "$tmpdir/matching-package.swift"
    printf '.package(url: "https://github.com/FluidInference/FluidAudio.git", revision: "%s")\n' \
        "$candidate_sha" >"$tmpdir/candidate-package.swift"
    printf '{"pins":[{"identity":"fluidaudio","location":"https://github.com/FluidInference/FluidAudio.git","state":{"revision":"%s"}}]}\n' \
        "$production_sha" >"$tmpdir/Package.resolved"
    mkdir -p "$tmpdir/matching" "$tmpdir/candidate" "$tmpdir/mismatched-lock"
    cp "$tmpdir/matching-package.swift" "$tmpdir/matching/Package.swift"
    cp "$tmpdir/candidate-package.swift" "$tmpdir/candidate/Package.swift"
    cp "$tmpdir/production-package.swift" "$tmpdir/mismatched-lock/Package.swift"
    printf '{"pins":[{"identity":"fluidaudio","location":"https://github.com/FluidInference/FluidAudio.git","state":{"revision":"%s"}}]}\n' \
        "$production_sha" >"$tmpdir/matching/Package.resolved"
    printf '{"pins":[{"identity":"fluidaudio","location":"https://github.com/FluidInference/FluidAudio.git","state":{"revision":"%s"}}]}\n' \
        "$candidate_sha" >"$tmpdir/candidate/Package.resolved"
    printf '{"pins":[{"identity":"fluidaudio","location":"https://github.com/FluidInference/FluidAudio.git","state":{"revision":"%s"}}]}\n' \
        "$candidate_sha" >"$tmpdir/mismatched-lock/Package.resolved"
    assert_eq "$(fluid_revision_from_package "$tmpdir/production-package.swift")" \
        "$production_sha" "FluidAudio revision extraction"
    python3 ./dependency-provenance.py --self-test
    assert_eq "$(validated_fluid_revision "$tmpdir/production-package.swift")" \
        "$production_sha" "locked FluidAudio revision extraction"

    local mismatched_lock_log="$tmpdir/mismatched-lock.log"
    if validate_fluid_dependency_alignment \
        "$tmpdir/mismatched-lock/Package.swift" "$tmpdir/matching/Package.swift" 0 0 \
        >"$mismatched_lock_log" 2>&1; then
        echo "self-test expected a manifest/lock mismatch to fail closed" >&2
        exit 1
    fi
    assert_contains "$mismatched_lock_log" \
        "FluidAudio manifest and resolved revisions differ"

    DEPENDENCY_MODE="unset"
    validate_fluid_dependency_alignment \
        "$tmpdir/matching/Package.swift" "$tmpdir/matching/Package.swift" 0 0
    assert_eq "$DEPENDENCY_MODE" "production" "matching production dependency mode"

    local mismatch_log="$tmpdir/dependency-mismatch.log"
    if validate_fluid_dependency_alignment \
        "$tmpdir/matching/Package.swift" "$tmpdir/candidate/Package.swift" 0 0 \
        >"$mismatch_log" 2>&1; then
        echo "self-test expected a benchmark dependency mismatch to fail closed" >&2
        exit 1
    fi
    assert_contains "$mismatch_log" \
        "Refusing to label benchmark-revision transcripts as production release evidence."

    local unscoped_candidate_log="$tmpdir/unscoped-candidate.log"
    if validate_fluid_dependency_alignment \
        "$tmpdir/matching/Package.swift" "$tmpdir/candidate/Package.swift" 1 0 \
        >"$unscoped_candidate_log" 2>&1; then
        echo "self-test expected candidate dependency mode without candidate models to fail" >&2
        exit 1
    fi
    assert_contains "$unscoped_candidate_log" \
        "--allow-candidate-dependency requires --include-candidate-models or --sdk-upgrade-only"

    DEPENDENCY_MODE="unset"
    validate_fluid_dependency_alignment \
        "$tmpdir/matching/Package.swift" "$tmpdir/candidate/Package.swift" 1 1 \
        >"$tmpdir/candidate-mode.log" 2>&1
    assert_eq "$DEPENDENCY_MODE" "candidate" "explicit candidate dependency mode"
    assert_contains "$tmpdir/candidate-mode.log" \
        "Results from this run do not validate the production app's FluidAudio code."

    DEPENDENCY_MODE="production"
    REQUIRE_REAL_AUDIO=1
    REQUIRE_NON_SPEECH_CONTROLS=1
    NON_SPEECH_CONTROLS_HAND_AUDITED=1
    REQUIRE_LONG_PUBLIC_AUDIO=1
    REQUIRE_MULTILINGUAL_LONG_PUBLIC_AUDIO=1
    assert_eq "$(final_verdict)" "release ASR checks passed" "release verdict"
    REQUIRE_REAL_AUDIO=0
    assert_contains <(final_verdict) \
        "not a production release-gate pass: private real-dictation coverage was optional"
    REQUIRE_REAL_AUDIO=1
    REQUIRE_NON_SPEECH_CONTROLS=0
    assert_contains <(final_verdict) \
        "not a production release-gate pass: audited non-speech controls were optional"
    REQUIRE_NON_SPEECH_CONTROLS=1
    NON_SPEECH_CONTROLS_HAND_AUDITED=0
    assert_contains <(final_verdict) \
        "not a production release-gate pass: audited non-speech controls were optional"
    NON_SPEECH_CONTROLS_HAND_AUDITED=1
    REQUIRE_LONG_PUBLIC_AUDIO=0
    assert_contains <(final_verdict) \
        "not a production release-gate pass: English or German multi-window coverage was optional"
    REQUIRE_LONG_PUBLIC_AUDIO=0
    REQUIRE_MULTILINGUAL_LONG_PUBLIC_AUDIO=0
    assert_contains <(final_verdict) \
        "not a production release-gate pass: English or German multi-window coverage was optional"
    REQUIRE_LONG_PUBLIC_AUDIO=1
    REQUIRE_MULTILINGUAL_LONG_PUBLIC_AUDIO=0
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

    local unapproved_sdk_upgrade_log="$tmpdir/unapproved-sdk-upgrade.log"
    if bash "$SCRIPT_PATH" --sdk-upgrade-only \
        >"$unapproved_sdk_upgrade_log" 2>&1; then
        echo "self-test expected an SDK-only run without candidate dependency approval to fail" >&2
        exit 1
    fi
    assert_contains "$unapproved_sdk_upgrade_log" \
        "--sdk-upgrade-only requires --allow-candidate-dependency"

    local conflicting_candidate_scope_log="$tmpdir/conflicting-candidate-scope.log"
    if bash "$SCRIPT_PATH" --include-candidate-models --sdk-upgrade-only \
        --allow-candidate-dependency >"$conflicting_candidate_scope_log" 2>&1; then
        echo "self-test expected conflicting candidate scopes to fail" >&2
        exit 1
    fi
    assert_contains "$conflicting_candidate_scope_log" \
        "--include-candidate-models and --sdk-upgrade-only are mutually exclusive"

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

    local default_missing_real_log="$tmpdir/default-missing-real.log"
    if bash "$SCRIPT_PATH" \
        --real-audio-dir "$tmpdir/missing-real" \
        --long-public-audio-dir "$tmpdir/missing-long-public" \
        >"$default_missing_real_log" 2>&1; then
        echo "self-test expected the default missing private corpus to fail" >&2
        exit 1
    fi
    assert_contains "$default_missing_real_log" \
        "no private real-dictation clips found in $tmpdir/missing-real"
    assert_not_contains "$default_missing_real_log" "running helper self-tests"

    local underfilled_real="$tmpdir/underfilled-real"
    mkdir -p "$underfilled_real"
    touch "$underfilled_real/one.wav"
    printf 'synthetic private fixture marker\n' >"$underfilled_real/one.txt"
    local underfilled_real_log="$tmpdir/underfilled-real.log"
    if bash "$SCRIPT_PATH" \
        --real-audio-dir "$underfilled_real" \
        --public-audio-dir "$tmpdir/missing-public" \
        --long-public-audio-dir "$tmpdir/missing-long-public" \
        --multilingual-long-public-audio-dir "$tmpdir/missing-multilingual-long-public" \
        --allow-missing-long-public-audio \
        --allow-missing-multilingual-long-public-audio \
        >"$underfilled_real_log" 2>&1; then
        echo "self-test expected an underfilled private speech corpus to fail" >&2
        exit 1
    fi
    assert_contains "$underfilled_real_log" \
        "private dictation corpus is below its evidence floor: 1 non-empty references (minimum 25)"
    assert_not_contains "$underfilled_real_log" "synthetic private fixture marker"
    assert_not_contains "$underfilled_real_log" "running helper self-tests"

    local speech_only_real="$tmpdir/speech-only-real"
    mkdir -p "$speech_only_real"
    local clip_index
    for ((clip_index = 0; clip_index < 25; clip_index++)); do
        printf 'independent synthetic source %s\n' "$clip_index" \
            >"$speech_only_real/note-$clip_index.wav"
        printf 'word %.0s' {1..40} >"$speech_only_real/note-$clip_index.txt"
    done
    local unaudited_log="$tmpdir/unaudited-controls.log"
    if bash "$SCRIPT_PATH" --real-audio-dir "$speech_only_real" \
        --long-public-audio-dir "$tmpdir/missing-long-public" \
        >"$unaudited_log" 2>&1; then
        echo "self-test expected missing non-speech audit attestation to fail" >&2
        exit 1
    fi
    assert_contains "$unaudited_log" \
        "release ASR checks require --non-speech-controls-hand-audited"
    assert_not_contains "$unaudited_log" "running helper self-tests"

    for ((clip_index = 0; clip_index < 4; clip_index++)); do
        printf 'synthetic room noise %s\n' "$clip_index" \
            >"$speech_only_real/control-$clip_index.wav"
        : >"$speech_only_real/control-$clip_index.txt"
    done
    local insufficient_controls_log="$tmpdir/insufficient-controls.log"
    if bash "$SCRIPT_PATH" --real-audio-dir "$speech_only_real" \
        --long-public-audio-dir "$tmpdir/missing-long-public" \
        --non-speech-controls-hand-audited \
        >"$insufficient_controls_log" 2>&1; then
        echo "self-test expected too few non-speech controls to fail" >&2
        exit 1
    fi
    assert_contains "$insufficient_controls_log" \
        "4 zero-byte references (minimum 5)"
    assert_not_contains "$insufficient_controls_log" "running helper self-tests"

    local waived_controls_log="$tmpdir/waived-controls.log"
    if bash "$SCRIPT_PATH" --real-audio-dir "$speech_only_real" \
        --long-public-audio-dir "$tmpdir/missing-long-public" \
        --allow-missing-non-speech-controls \
        >"$waived_controls_log" 2>&1; then
        echo "self-test expected the waived run to stop at its missing long-form corpus" >&2
        exit 1
    fi
    assert_contains "$waived_controls_log" \
        "no long-form public speech clips found in $tmpdir/missing-long-public"
    assert_not_contains "$waived_controls_log" \
        "release ASR checks require --non-speech-controls-hand-audited"

    local allowed_missing_real_log="$tmpdir/allowed-missing-real.log"
    if bash "$SCRIPT_PATH" \
        --real-audio-dir "$tmpdir/missing-real" \
        --public-audio-dir "$tmpdir/missing-public" \
        --long-public-audio-dir "$tmpdir/missing-long-public" \
        --allow-missing-real-audio >"$allowed_missing_real_log" 2>&1; then
        echo "self-test expected the lightweight run to stop at its missing long-form corpus" >&2
        exit 1
    fi
    assert_not_contains "$allowed_missing_real_log" \
        "no private real-dictation clips found in $tmpdir/missing-real"
    assert_contains "$allowed_missing_real_log" \
        "no long-form public speech clips found in $tmpdir/missing-long-public"

    local missing_public_log="$tmpdir/missing-public.log"
    if bash "$SCRIPT_PATH" \
        --real-audio-dir "$tmpdir/missing-real" \
        --public-audio-dir "$tmpdir/missing-public" \
        --allow-missing-real-audio \
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
        --allow-missing-real-audio \
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
        --allow-missing-real-audio \
        >"$default_missing_long_public_log" 2>&1; then
        echo "self-test expected default missing long-form public corpus to fail" >&2
        exit 1
    fi
    assert_contains "$default_missing_long_public_log" \
        "no long-form public speech clips found in $tmpdir/missing-long-public"
    assert_not_contains "$default_missing_long_public_log" "running helper self-tests"

    local missing_multilingual_long_public_log="$tmpdir/missing-multilingual-long-public.log"
    if bash "$SCRIPT_PATH" \
        --real-audio-dir "$tmpdir/missing-real" \
        --public-audio-dir "$tmpdir/missing-public" \
        --long-public-audio-dir "$tmpdir/missing-long-public" \
        --multilingual-long-public-audio-dir "$tmpdir/missing-multilingual-long-public" \
        --allow-missing-real-audio \
        --allow-missing-long-public-audio \
        >"$missing_multilingual_long_public_log" 2>&1; then
        echo "self-test expected default missing German long-form corpus to fail" >&2
        exit 1
    fi
    assert_contains "$missing_multilingual_long_public_log" \
        "no German long-form FLEURS speech clips found in $tmpdir/missing-multilingual-long-public"
    assert_not_contains "$missing_multilingual_long_public_log" "running helper self-tests"

    local invalid_long_public="$tmpdir/invalid-long-public"
    mkdir -p "$invalid_long_public"
    touch "$invalid_long_public/not-a-composite.wav"
    local invalid_long_public_log="$tmpdir/invalid-long-public.log"
    if bash "$SCRIPT_PATH" \
        --real-audio-dir "$tmpdir/missing-real" \
        --public-audio-dir "$tmpdir/missing-public" \
        --long-public-audio-dir "$invalid_long_public" \
        --allow-missing-real-audio \
        --allow-missing-multilingual-long-public-audio \
        >"$invalid_long_public_log" 2>&1; then
        echo "self-test expected invalid long-form public corpus to fail" >&2
        exit 1
    fi
    assert_contains "$invalid_long_public_log" \
        "long-form output is not owned by this composer"
    assert_not_contains "$invalid_long_public_log" "running helper self-tests"

    local original_candidate_models="$INCLUDE_CANDIDATE_MODELS"
    INCLUDE_CANDIDATE_MODELS=0
    assert_eq "$(tail_word_check_mode)" "production-v3-only" \
        "default short-clip tail diagnostic mode"
    INCLUDE_CANDIDATE_MODELS=1
    assert_eq "$(tail_word_check_mode)" "candidate" \
        "candidate short-clip tail gate mode"
    INCLUDE_CANDIDATE_MODELS="$original_candidate_models"

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
        --multilingual-long-public-audio-dir)
            need_value "$@"
            MULTILINGUAL_LONG_PUBLIC_AUDIO_DIR="$2"
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
        --allow-missing-real-audio)
            REQUIRE_REAL_AUDIO=0
            shift
            ;;
        --non-speech-controls-hand-audited)
            NON_SPEECH_CONTROLS_HAND_AUDITED=1
            shift
            ;;
        --allow-missing-non-speech-controls)
            REQUIRE_NON_SPEECH_CONTROLS=0
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
        --allow-missing-multilingual-long-public-audio)
            REQUIRE_MULTILINGUAL_LONG_PUBLIC_AUDIO=0
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
        --sdk-upgrade-only)
            SDK_UPGRADE_ONLY=1
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

if [[ "$INCLUDE_CANDIDATE_MODELS" -eq 1 && "$SDK_UPGRADE_ONLY" -eq 1 ]]; then
    echo "--include-candidate-models and --sdk-upgrade-only are mutually exclusive" >&2
    exit 2
fi
if [[ "$SDK_UPGRADE_ONLY" -eq 1 && "$ALLOW_CANDIDATE_DEPENDENCY" -ne 1 ]]; then
    echo "--sdk-upgrade-only requires --allow-candidate-dependency" >&2
    exit 2
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
multilingual_long_public_count="$(supported_audio_count "$MULTILINGUAL_LONG_PUBLIC_AUDIO_DIR")"
if [[ "$REQUIRE_REAL_AUDIO" -eq 1 && "$real_count" -eq 0 ]]; then
    echo "no private real-dictation clips found in $REAL_AUDIO_DIR" >&2
    exit 1
fi
if [[ "$REQUIRE_REAL_AUDIO" -eq 1 ]]; then
    # Match the existing product-candidate sample floors. This checks corpus
    # volume only; it does not attest reference quality or acceptable WER.
    python3 ./benchmark-inputs.py validate-private-corpus \
        --directory "$REAL_AUDIO_DIR" \
        --minimum-clips "$MIN_PRIVATE_SPEECH_CLIPS" \
        --minimum-words "$MIN_PRIVATE_REFERENCE_WORDS"
    if [[ "$REQUIRE_NON_SPEECH_CONTROLS" -eq 1 ]]; then
        if [[ "$NON_SPEECH_CONTROLS_HAND_AUDITED" -ne 1 ]]; then
            echo "release ASR checks require --non-speech-controls-hand-audited; use --allow-missing-non-speech-controls for a lightweight run" >&2
            exit 1
        fi
        python3 ./benchmark-inputs.py validate-private-controls \
            --directory "$REAL_AUDIO_DIR" \
            --minimum-clips "$MIN_PRIVATE_NON_SPEECH_CONTROLS"
    fi
fi
if [[ "$REQUIRE_PUBLIC_AUDIO" -eq 1 && "$public_count" -eq 0 ]]; then
    echo "no public speech clips found in $PUBLIC_AUDIO_DIR" >&2
    exit 1
fi
if [[ "$REQUIRE_LONG_PUBLIC_AUDIO" -eq 1 && "$long_public_count" -eq 0 ]]; then
    echo "no long-form public speech clips found in $LONG_PUBLIC_AUDIO_DIR" >&2
    exit 1
fi
if [[ "$REQUIRE_MULTILINGUAL_LONG_PUBLIC_AUDIO" -eq 1 && \
      "$multilingual_long_public_count" -eq 0 ]]; then
    echo "no German long-form FLEURS speech clips found in $MULTILINGUAL_LONG_PUBLIC_AUDIO_DIR" >&2
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
if [[ "$multilingual_long_public_count" -gt 0 ]]; then
    # Multilingual chunks need separate coverage: validate both the long-form
    # composition and the inherited checked-FLEURS locale/split provenance.
    python3 ./compose-public-long-form-fixtures.py \
        --validate-output-dir \
        --output-dir "$MULTILINGUAL_LONG_PUBLIC_AUDIO_DIR" \
        --require-fleurs-locale de_de
fi

validate_fluid_dependency_alignment \
    "../../swift/Package.swift" "Package.swift" \
    "$ALLOW_CANDIDATE_DEPENDENCY" "$(( INCLUDE_CANDIDATE_MODELS || SDK_UPGRADE_ONLY ))"

echo "running helper self-tests..."
python3 ./benchmark-inputs.py --self-test
./run-tail-word-regression.sh --self-test
./add-real-dictation-fixture.sh --self-test
./fetch-public-speech-fixtures.sh --self-test
python3 ./compose-public-long-form-fixtures.py --self-test
python3 ./compose-public-window-shift-fixtures.py --self-test
python3 ./compose-public-context-fixtures.py --self-test
python3 ./analyze-context-variation.py --self-test
./run-real-dictation-regression.sh --self-test
./run-real-model-comparison.sh --self-test
./run-spanish-language-probe.sh --self-test
./run-vocabulary-bias-regression.sh --self-test
./run-public-model-comparison.sh --self-test
./bench-power.sh --self-test

if [[ "$RUN_TAIL" -eq 1 ]]; then
    echo
    case "$(tail_word_check_mode)" in
        candidate)
            echo "running candidate synthetic tail-word ASR gate..."
            ./run-tail-word-regression.sh
            ;;
        production-v3-only)
            echo "recording production-v3 short-clip tail baseline (report-only)..."
            ./run-tail-word-regression.sh --production-v3-only
            ;;
    esac
else
    echo
    echo "skipping short-clip tail diagnostic (--skip-tail)"
fi

if [[ "$real_count" -eq 0 ]]; then
    echo
    echo "no private real-dictation clips found in $REAL_AUDIO_DIR; skipped real-audio WER gates"
else
    echo
    echo "running private $(v3_baseline_label) ASR regression on $real_count clip(s)..."
    private_regression_args=(
        --input-dir "$REAL_AUDIO_DIR" --backend v3 --trials "$TRIALS"
    )
    if [[ "$REQUIRE_REAL_AUDIO" -eq 1 && "$REQUIRE_NON_SPEECH_CONTROLS" -eq 1 ]]; then
        private_regression_args+=( --max-non-speech-emissions 0 )
    fi
    ./run-real-dictation-regression.sh "${private_regression_args[@]}"
    if [[ "$DEPENDENCY_MODE" == "candidate" && \
          ( "$INCLUDE_CANDIDATE_MODELS" -eq 1 || "$SDK_UPGRADE_ONLY" -eq 1 ) ]]; then
        echo
        echo "running private released-v3 vs candidate SDK-default chunking comparison on $real_count clip(s)..."
        ./run-real-model-comparison.sh \
            --input-dir "$REAL_AUDIO_DIR" \
            --candidate-backend v3-sdk-default \
            --trials "$TRIALS"
    fi

    if [[ "$DEPENDENCY_MODE" == "production" && \
          "$INCLUDE_CANDIDATE_MODELS" -eq 1 ]]; then
        echo
        echo "running private released-v3 vs explicit no-mel chunking comparison on $real_count clip(s)..."
        ./run-real-model-comparison.sh \
            --input-dir "$REAL_AUDIO_DIR" \
            --candidate-backend v3-no-mel \
            --trials "$TRIALS"
    fi

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

        if [[ "$DEPENDENCY_MODE" == "candidate" ]]; then
            echo
            echo "running private v3 linear-int8 encoder candidate comparison on $real_count clip(s)..."
            ./run-real-model-comparison.sh \
                --input-dir "$REAL_AUDIO_DIR" \
                --candidate-backend v3-int8-v2 \
                --trials "$TRIALS"
        else
            echo
            echo "skipping linear-int8 encoder candidate (not exposed by the production FluidAudio pin)"
        fi

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

    if [[ "$DEPENDENCY_MODE" == "candidate" && \
          ( "$INCLUDE_CANDIDATE_MODELS" -eq 1 || "$SDK_UPGRADE_ONLY" -eq 1 ) ]]; then
        echo
        echo "running public released-v3 vs candidate SDK-default chunking comparison on $public_count clip(s)..."
        ./run-public-model-comparison.sh \
            --fixture-dir "$PUBLIC_AUDIO_DIR" \
            --candidate-backend v3-sdk-default \
            --trials "$TRIALS"
    fi

    if [[ "$DEPENDENCY_MODE" == "production" && \
          "$INCLUDE_CANDIDATE_MODELS" -eq 1 ]]; then
        echo
        echo "running public released-v3 vs explicit no-mel chunking comparison on $public_count clip(s)..."
        ./run-public-model-comparison.sh \
            --fixture-dir "$PUBLIC_AUDIO_DIR" \
            --candidate-backend v3-no-mel \
            --trials "$TRIALS"
    fi

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

        if [[ "$DEPENDENCY_MODE" == "candidate" ]]; then
            echo
            echo "running public v3 linear-int8 encoder candidate comparison on $public_count clip(s)..."
            ./run-public-model-comparison.sh \
                --fixture-dir "$PUBLIC_AUDIO_DIR" \
                --candidate-backend v3-int8-v2 \
                --trials "$TRIALS"
        else
            echo
            echo "skipping linear-int8 encoder candidate (not exposed by the production FluidAudio pin)"
        fi

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

    if [[ "$DEPENDENCY_MODE" == "production" ]]; then
        echo
        echo "running long-form public production-v3 vs same-pin explicit no-mel chunking comparison (candidate evidence only)..."
        if ! ./run-public-model-comparison.sh \
            --fixture-dir "$LONG_PUBLIC_AUDIO_DIR" \
            --out-dir public-results/long-form \
            --candidate-backend v3-no-mel \
            --trials "$TRIALS"; then
            echo "warning: no-mel candidate comparison failed; production release verdict is unchanged" >&2
        fi
    fi

    if [[ "$DEPENDENCY_MODE" == "production" && \
          "$INCLUDE_CANDIDATE_MODELS" -eq 1 ]]; then
        echo
        echo "running long-form public explicit no-mel absolute ASR regression..."
        ./run-real-dictation-regression.sh \
            --input-dir "$LONG_PUBLIC_AUDIO_DIR" \
            --out-dir public-results/long-form \
            --backend v3-no-mel \
            --trials "$TRIALS" \
            --public-corpus \
            --show-transcripts \
            --show-paths \
            --max-reference-deletion-run "$LONG_PUBLIC_MAX_REFERENCE_DELETION_RUN" \
            --max-corpus-wer "$LONG_PUBLIC_MAX_CORPUS_WER"
    fi

    if [[ ( "$INCLUDE_CANDIDATE_MODELS" -eq 1 || "$SDK_UPGRADE_ONLY" -eq 1 ) && \
          "$DEPENDENCY_MODE" == "candidate" ]]; then
        echo
        echo "running long-form public candidate SDK-default absolute ASR regression..."
        ./run-real-dictation-regression.sh \
            --input-dir "$LONG_PUBLIC_AUDIO_DIR" \
            --out-dir public-results/long-form \
            --backend v3-sdk-default \
            --trials "$TRIALS" \
            --public-corpus \
            --show-transcripts \
            --show-paths \
            --max-reference-deletion-run "$LONG_PUBLIC_MAX_REFERENCE_DELETION_RUN" \
            --max-corpus-wer "$LONG_PUBLIC_MAX_CORPUS_WER"

        echo
        echo "running long-form public released-v3 vs candidate SDK-default chunking comparison..."
        ./run-public-model-comparison.sh \
            --fixture-dir "$LONG_PUBLIC_AUDIO_DIR" \
            --out-dir public-results/long-form \
            --candidate-backend v3-sdk-default \
            --trials "$TRIALS"

        if [[ "$INCLUDE_CANDIDATE_MODELS" -eq 1 ]]; then
            echo
            echo "running long-form public v3 linear-int8 encoder candidate comparison..."
            ./run-public-model-comparison.sh \
                --fixture-dir "$LONG_PUBLIC_AUDIO_DIR" \
                --out-dir public-results/long-form \
                --candidate-backend v3-int8-v2 \
                --trials "$TRIALS"
        fi
    elif [[ "$INCLUDE_CANDIDATE_MODELS" -eq 1 ]]; then
        echo
        echo "skipping linear-int8 encoder candidate (not exposed by the production FluidAudio pin)"
    fi
fi

if [[ "$multilingual_long_public_count" -eq 0 ]]; then
    echo
    echo "no German long-form FLEURS speech clips found in $MULTILINGUAL_LONG_PUBLIC_AUDIO_DIR; skipped multilingual seam WER gate (--allow-missing-multilingual-long-public-audio)"
else
    echo
    echo "running German FLEURS long-form $(v3_baseline_label) ASR regression on $multilingual_long_public_count composite clip(s)..."
    ./run-real-dictation-regression.sh \
        --input-dir "$MULTILINGUAL_LONG_PUBLIC_AUDIO_DIR" \
        --out-dir public-results/fleurs-de_de-long-form \
        --backend v3 \
        --language de \
        --trials "$TRIALS" \
        --public-corpus \
        --show-transcripts \
        --show-paths \
        --max-reference-deletion-run "$LONG_PUBLIC_MAX_REFERENCE_DELETION_RUN" \
        --max-corpus-wer "$LONG_PUBLIC_MAX_CORPUS_WER"

    if [[ "$DEPENDENCY_MODE" == "candidate" && \
          ( "$INCLUDE_CANDIDATE_MODELS" -eq 1 || "$SDK_UPGRADE_ONLY" -eq 1 ) ]]; then
        echo
        echo "running German FLEURS candidate SDK-default absolute ASR regression..."
        ./run-real-dictation-regression.sh \
            --input-dir "$MULTILINGUAL_LONG_PUBLIC_AUDIO_DIR" \
            --out-dir public-results/fleurs-de_de-long-form \
            --backend v3-sdk-default \
            --language de \
            --trials "$TRIALS" \
            --public-corpus \
            --show-transcripts \
            --show-paths \
            --max-reference-deletion-run "$LONG_PUBLIC_MAX_REFERENCE_DELETION_RUN" \
            --max-corpus-wer "$LONG_PUBLIC_MAX_CORPUS_WER"

        echo
        echo "running German FLEURS released-v3 vs candidate SDK-default chunking comparison..."
        ./run-public-model-comparison.sh \
            --fixture-dir "$MULTILINGUAL_LONG_PUBLIC_AUDIO_DIR" \
            --out-dir public-results/fleurs-de_de-long-form \
            --candidate-backend v3-sdk-default \
            --language de \
            --trials "$TRIALS"
    fi
fi

echo
final_verdict
