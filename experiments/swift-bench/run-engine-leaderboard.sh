#!/usr/bin/env bash
# Reproduce the cross-engine ASR leaderboard end to end on one Mac.
#
# Fetches and verifies the public corpora, builds the Presspeech benchmark
# and a whisper.cpp batch driver, runs every engine on identical 16 kHz mono
# PCM16 audio one engine at a time, then scores all of them with the same
# text normalisation into docs/benchmarks/engine-leaderboard.json.
# README and docs/benchmarks.html tables are rendered from that JSON by
# scripts/sync-docs.py.

set -euo pipefail

SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
HERE="$(dirname "$SCRIPT_PATH")"
ROOT="$(cd "$HERE/../.." && pwd)"
cd "$HERE"

CACHE_DIR="public-downloads"
WORK_DIR="public-results/engine-leaderboard"
OUTPUT="$ROOT/docs/benchmarks/engine-leaderboard.json"
MANIFEST="$HERE/engine-leaderboard-manifest.json"
ENGINES_FILE="$HERE/engine-leaderboard-engines.json"
ENGINES="parakeet-v3 parakeet-ultra whisper-large-v3-turbo whisper-small whisper-base apple-dictation apple-speech"
MODES="explicit auto"
CORPORA=""
FORCE=0
SKIP_PREPARE=0
SKIP_RUN=0
SELF_TEST=0
LIBRISPEECH_COUNT=500
FLEURS_COUNT=100
FULL_LIBRISPEECH=0

# Pinned whisper.cpp runtime and official ggml models
# (huggingface.co/ggerganov/whisper.cpp at WHISPER_MODEL_REVISION).
WHISPER_CPP_VERSION="1.9.5"
WHISPER_MODEL_REVISION="5359861c739e955e79d9a303bcbc70fb988958b1"
whisper_model_file() {
    case "$1" in
        whisper-large-v3-turbo) printf 'ggml-large-v3-turbo.bin' ;;
        whisper-small) printf 'ggml-small.bin' ;;
        whisper-base) printf 'ggml-base.bin' ;;
        *) return 1 ;;
    esac
}
whisper_model_sha256() {
    case "$1" in
        ggml-large-v3-turbo.bin) printf '1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69' ;;
        ggml-small.bin) printf '1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b' ;;
        ggml-base.bin) printf '60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe' ;;
        *) return 1 ;;
    esac
}

usage() {
    cat <<'USAGE'
usage: ./run-engine-leaderboard.sh [options]

Options:
  --engines "<ids>"     engines to run (default: all measured engines in
                        engine-leaderboard-engines.json)
  --modes "<modes>"     language modes: explicit and/or auto (default: both)
  --corpora "<names>"   limit runs to these corpus directories
  --cache-dir <path>    archive and model cache (default: public-downloads)
  --work-dir <path>     generated audio and raw results
                        (default: public-results/engine-leaderboard)
  --output <path>       leaderboard JSON (default: docs/benchmarks/engine-leaderboard.json)
  --librispeech-count <n>
                        seeded clips per LibriSpeech test split (default: 500)
  --full-librispeech    use every clip of both LibriSpeech test splits
  --fleurs-count <n>    seeded clips per FLEURS language (default: 100)
  --skip-prepare        reuse already prepared corpora
  --skip-run            only rescore existing raw results
  --force               rerun engine/corpus/mode results that already exist
  --self-test           run helper self-tests only
  -h, --help            show this help

Runs are sequential and resumable: a finished engine/corpus/mode result is
kept unless --force is given. Latency is sensitive to machine load; the
environment block records load, swap, memory pressure and power state at the
start and end of the run.
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --engines) ENGINES="$2"; shift 2 ;;
        --modes) MODES="$2"; shift 2 ;;
        --corpora) CORPORA="$2"; shift 2 ;;
        --cache-dir) CACHE_DIR="$2"; shift 2 ;;
        --work-dir) WORK_DIR="$2"; shift 2 ;;
        --output) OUTPUT="$2"; shift 2 ;;
        --librispeech-count) LIBRISPEECH_COUNT="$2"; shift 2 ;;
        --full-librispeech) FULL_LIBRISPEECH=1; shift ;;
        --fleurs-count) FLEURS_COUNT="$2"; shift 2 ;;
        --skip-prepare) SKIP_PREPARE=1; shift ;;
        --skip-run) SKIP_RUN=1; shift ;;
        --force) FORCE=1; shift ;;
        --self-test) SELF_TEST=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

# FLEURS corpus -> language code and Apple locale.
fleurs_codes() {
    python3 -I "$HERE/engine-leaderboard.py" fleurs-languages | awk -F '\t' -v c="${1#fleurs-}" '$1 == c { print $2 "\t" $3 }'
}

# Prints "<language code>\t<apple locale>" for one corpus.
corpus_language() {
    case "$1" in
        librispeech-*) printf 'en\ten-US\n' ;;
        fleurs-*) fleurs_codes "$1" ;;
        *) return 1 ;;
    esac
}

engine_command() {
    # engine_command <engine> <mode> <corpus> <clips.tsv> <out.jsonl>
    local engine="$1" mode="$2" corpus="$3" clips="$4" out="$5" code locale
    IFS=$'\t' read -r code locale < <(corpus_language "$corpus")
    case "$engine" in
        parakeet-v3|parakeet-ultra)
            local backend=v3
            [[ "$engine" == parakeet-ultra ]] && backend=ultra
            local language=auto
            [[ "$mode" == explicit ]] && language="$code"
            printf '%s\n' "$HERE/.build/release/presspeech-bench" --backend "$backend" --language "$language" \
                --batch "$clips" --batch-output "$out"
            ;;
        whisper-*)
            local model language=auto
            model="$(whisper_model_file "$engine")"
            [[ "$mode" == explicit ]] && language="$code"
            printf '%s\n' "$HERE/.build/whisper-batch/whisper-batch" --model "$CACHE_DIR/whisper-ggml/$model" \
                --language "$language" --threads 4 --beam-size 5 --best-of 5 \
                --batch "$clips" --batch-output "$out"
            ;;
        apple-dictation|apple-speech)
            [[ "$mode" == explicit ]] || return 3
            local backend=apple
            [[ "$engine" == apple-speech ]] && backend=apple-speech
            printf '%s\n' "$HERE/.build/release/presspeech-bench" --backend "$backend" --apple-locale "$locale" \
                --batch "$clips" --batch-output "$out"
            ;;
        *) return 1 ;;
    esac
}

run_self_test() {
    python3 -I "$HERE/engine-leaderboard.py" self-test
    local cmd
    cmd="$(engine_command parakeet-ultra explicit fleurs-pl_pl /c.tsv /o.jsonl | tr '\n' ' ')"
    [[ "$cmd" == *"--backend ultra --language pl "* ]] || { echo "self-test: ultra/pl command: $cmd" >&2; exit 1; }
    cmd="$(engine_command whisper-small auto librispeech-test-clean /c.tsv /o.jsonl | tr '\n' ' ')"
    [[ "$cmd" == *"ggml-small.bin --language auto "* ]] || { echo "self-test: whisper auto command: $cmd" >&2; exit 1; }
    cmd="$(engine_command apple-dictation explicit fleurs-es_419 /c.tsv /o.jsonl | tr '\n' ' ')"
    [[ "$cmd" == *"--backend apple --apple-locale es-MX "* ]] || { echo "self-test: apple es command: $cmd" >&2; exit 1; }
    if engine_command apple-speech auto fleurs-de_de /c.tsv /o.jsonl >/dev/null 2>&1; then
        echo "self-test: apple auto mode must be unsupported" >&2; exit 1
    fi
    [[ "$(whisper_model_sha256 "$(whisper_model_file whisper-base)")" =~ ^[0-9a-f]{64}$ ]] || exit 1
    python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["engines"] and d["method"]' "$ENGINES_FILE"
    echo "engine leaderboard self-test passed"
}

if [[ "$SELF_TEST" -eq 1 ]]; then
    run_self_test
    exit 0
fi

mkdir -p "$CACHE_DIR" "$WORK_DIR"
CACHE_DIR="$(cd "$CACHE_DIR" && pwd)"
WORK_DIR="$(cd "$WORK_DIR" && pwd)"
DATA_DIR="$WORK_DIR/audio"
RESULTS_DIR="$WORK_DIR/results"
VENV="$WORK_DIR/venv"

# ------------------------------------------------------------ tooling
command -v afconvert >/dev/null || { echo "afconvert is required (macOS)" >&2; exit 1; }
WHISPER_PREFIX="$(brew --prefix whisper.cpp 2>/dev/null || true)"
GGML_PREFIX="$(brew --prefix ggml 2>/dev/null || true)"
if [[ -z "$WHISPER_PREFIX" || ! -f "$WHISPER_PREFIX/include/whisper.h" ]]; then
    echo "whisper.cpp $WHISPER_CPP_VERSION is required: brew install whisper.cpp" >&2
    exit 1
fi
installed_whisper="$("$WHISPER_PREFIX/bin/whisper-cli" --version 2>&1 | sed -nE 's/.*version: *([0-9.]+).*/\1/p' | head -n 1)"
if [[ "$installed_whisper" != "$WHISPER_CPP_VERSION" ]]; then
    echo "warning: whisper.cpp $installed_whisper installed; the published results used $WHISPER_CPP_VERSION" >&2
fi

if [[ ! -x "$VENV/bin/python" ]]; then
    python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --disable-pip-version-check --no-deps -r "$HERE/engine-leaderboard-requirements.txt"

echo "building presspeech-bench (release)" >&2
swift build -c release --package-path "$HERE" >&2
echo "building whisper-batch against whisper.cpp $installed_whisper" >&2
mkdir -p "$HERE/.build/whisper-batch"
clang -O2 -std=c11 -Wall -Wextra \
    -I"$WHISPER_PREFIX/include" -I"$GGML_PREFIX/include" \
    "$HERE/whisper-batch/whisper-batch.c" \
    -L"$WHISPER_PREFIX/lib" -L"$GGML_PREFIX/lib" -lwhisper -lggml -lggml-base \
    -Wl,-rpath,"$WHISPER_PREFIX/lib" -Wl,-rpath,"$GGML_PREFIX/lib" \
    -o "$HERE/.build/whisper-batch/whisper-batch"

for engine in $ENGINES; do
    model="$(whisper_model_file "$engine" 2>/dev/null)" || continue
    dest="$CACHE_DIR/whisper-ggml/$model"
    if [[ ! -f "$dest" ]]; then
        mkdir -p "$(dirname "$dest")"
        curl -fL --retry 3 --continue-at - --output "$dest.part" \
            "https://huggingface.co/ggerganov/whisper.cpp/resolve/$WHISPER_MODEL_REVISION/$model"
        mv "$dest.part" "$dest"
    fi
    actual="$(shasum -a 256 "$dest" | awk '{ print $1 }')"
    if [[ "$actual" != "$(whisper_model_sha256 "$model")" ]]; then
        echo "checksum mismatch for $dest; delete it and rerun" >&2
        exit 1
    fi
done

# ------------------------------------------------------------ corpora
if [[ "$SKIP_PREPARE" -eq 0 ]]; then
    prepare_args=(prepare --cache-dir "$CACHE_DIR" --data-dir "$DATA_DIR" --manifest "$MANIFEST"
        --librispeech-count "$LIBRISPEECH_COUNT" --fleurs-count "$FLEURS_COUNT")
    [[ "$FULL_LIBRISPEECH" -eq 1 ]] && prepare_args+=(--full-librispeech)
    [[ -f "$MANIFEST" ]] && prepare_args+=(--verify-manifest)
    "$VENV/bin/python" -I "$HERE/engine-leaderboard.py" "${prepare_args[@]}"
fi
if [[ -z "$CORPORA" ]]; then
    CORPORA="$(find "$DATA_DIR" -mindepth 2 -maxdepth 2 -name clips.tsv -exec dirname {} \; | xargs -n 1 basename | LC_ALL=C sort | tr '\n' ' ')"
fi

# ------------------------------------------------------------ environment
snapshot_conditions() {
    python3 -I - "$1" <<'PY'
import json, re, subprocess, sys
def run(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return ""
swap = run("sysctl", "-n", "vm.swapusage")
used = re.search(r"used = ([0-9.]+)M", swap)
pressure = re.search(r"free percentage: (\d+)%", run("memory_pressure"))
load = run("sysctl", "-n", "vm.loadavg").strip("{} \n").split()
power = run("pmset", "-g", "batt")
print(json.dumps({
    "label": sys.argv[1],
    "load_average_1_5_15": [float(x) for x in load[:3]] if len(load) >= 3 else None,
    "swap_used_mb": float(used[1]) if used else None,
    "memory_free_percent": int(pressure[1]) if pressure else None,
    "power_source": "AC" if "AC Power" in power else ("battery" if "Battery Power" in power else None),
}))
PY
}

ENV_JSON="$WORK_DIR/environment.json"
CONDITIONS="$WORK_DIR/conditions.jsonl"
if [[ "$SKIP_RUN" -eq 0 ]]; then
    snapshot_conditions start >>"$CONDITIONS"
fi

# ------------------------------------------------------------ runs
if [[ "$SKIP_RUN" -eq 0 ]]; then
    for engine in $ENGINES; do
        for mode in $MODES; do
            for corpus in $CORPORA; do
                out_dir="$RESULTS_DIR/$engine/$mode"
                out="$out_dir/$corpus.jsonl"
                mkdir -p "$out_dir"
                if [[ "$FORCE" -eq 0 && -f "$out" ]] && grep -q '"type":"summary"\|"type": "summary"' "$out"; then
                    continue
                fi
                rm -f "$out" "${out%.jsonl}.unsupported" "${out%.jsonl}.time" "${out%.jsonl}.log"
                set +e
                cmd_text="$(engine_command "$engine" "$mode" "$corpus" "$DATA_DIR/$corpus/clips.tsv" "$out")"
                status=$?
                set -e
                if [[ "$status" -eq 3 ]]; then
                    echo "no auto-detect mode" >"${out%.jsonl}.unsupported"
                    continue
                fi
                [[ "$status" -eq 0 ]] || { echo "unknown engine $engine" >&2; exit 1; }
                cmd=()
                while IFS= read -r arg; do cmd+=("$arg"); done <<<"$cmd_text"
                echo "== $engine / $mode / $corpus" >&2
                snapshot_conditions "$engine/$mode/$corpus" >>"$CONDITIONS"
                set +e
                /usr/bin/time -l -o "${out%.jsonl}.time" "${cmd[@]}" 2>"${out%.jsonl}.log"
                status=$?
                tail -n 3 "${out%.jsonl}.log" >&2
                set -e
                if [[ "$status" -ne 0 ]]; then
                    if grep -q 'does not support locale' "${out%.jsonl}.log"; then
                        sed -n 's/.*does not support locale \([A-Za-z_-]*\).*/locale \1 not supported/p' "${out%.jsonl}.log" | head -n 1 >"${out%.jsonl}.unsupported"
                        rm -f "$out"
                        continue
                    fi
                    echo "$engine / $mode / $corpus failed (exit $status); see ${out%.jsonl}.log" >&2
                    exit 1
                fi
            done
        done
    done
    snapshot_conditions end >>"$CONDITIONS"
fi

# ------------------------------------------------------------ score
python3 -I - "$ENV_JSON" "$CONDITIONS" "$HERE" "$ROOT" "$installed_whisper" <<'PY'
import json, platform, re, subprocess, sys
out, conditions_path, here, root, whisper_version = sys.argv[1:]
def run(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception:
        return ""
hw = run("system_profiler", "SPHardwareDataType")
def field(name):
    m = re.search(rf"^\s*{re.escape(name)}: (.+)$", hw, re.M)
    return m[1].strip() if m else None
resolved = json.load(open(f"{here}/Package.resolved"))
fluid = next((p for p in resolved.get("pins", []) if p.get("identity") == "fluidaudio"), {})
conditions = [json.loads(l) for l in open(conditions_path)] if __import__("os").path.exists(conditions_path) else []
json.dump({
    "machine": field("Model Name"),
    "model_identifier": field("Model Identifier"),
    "chip": field("Chip"),
    "memory": field("Memory"),
    "macos": run("sw_vers", "-productVersion") + " (" + run("sw_vers", "-buildVersion") + ")",
    "fluidaudio": {"version": fluid.get("state", {}).get("version"), "revision": fluid.get("state", {}).get("revision")},
    "whisper_cpp": whisper_version,
    "presspeech_commit": run("git", "-C", root, "rev-parse", "--short", "HEAD"),
    "conditions": conditions,
}, open(out, "w"), indent=1)
PY
"$VENV/bin/python" -I "$HERE/engine-leaderboard.py" score --data-dir "$DATA_DIR" --results-dir "$RESULTS_DIR" \
    --engines "$ENGINES_FILE" --manifest "$MANIFEST" --environment "$ENV_JSON" --output "$OUTPUT"
echo "next: python3 scripts/sync-docs.py to render the README and benchmarks tables" >&2
