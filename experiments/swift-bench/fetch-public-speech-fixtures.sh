#!/usr/bin/env bash
# Fetch licensed public speech fixtures for Presspeech ASR benchmarks.
#
# The script intentionally imports a bounded subset into public-audio/
# rather than checking audio into git. Generated clips are local benchmark
# fixtures with same-stem .txt references, matching the private real-audio
# layout used by the existing comparison scripts.

set -euo pipefail

SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$SCRIPT_PATH")"

SOURCE="librispeech"
SPLIT="dev-clean"
SPLIT_EXPLICIT=0
LANGUAGE=""
COUNT="25"
START_INDEX="0"
OUT_ROOT="public-audio"
FIXTURE_DIR_OVERRIDE=""
CACHE_DIR="public-downloads"
FORCE=0
SELF_TEST=0

# Pin the dataset tree as well as every model/runtime used by the benchmark.
# FLEURS archive downloads are Git LFS objects; the pinned pointer supplies
# the expected SHA-256 and byte count before any large download begins.
FLEURS_REVISION="70bb2e84b976b7e960aa89f1c648e09c59f894dd"

usage() {
    cat <<'USAGE'
usage: ./fetch-public-speech-fixtures.sh [options]

Options:
  --source <name>       public corpus: librispeech or fleurs (default: librispeech)
  --split <name>        LibriSpeech: dev-clean, dev-other, test-clean, test-other
                        FLEURS: train, dev, test (default: dev-clean or test)
  --language <locale>   FLEURS locale, for example uk_ua (required for FLEURS)
  --count <n>           number of clips to import (default: 25)
  --start-index <n>     zero-based offset into sorted transcript rows (default: 0)
  --out-dir <path>      generated fixture root (default: public-audio)
  --fixture-dir <path>  exact generated fixture directory; overrides --out-dir
  --cache-dir <path>    download cache for upstream archives (default: public-downloads)
  --force               replace an existing generated fixture directory
  --self-test           run parser and selection self-tests only
  -h, --help            show this help

LibriSpeech downloads a split archive from OpenSLR and verifies its published
MD5. FLEURS downloads one language/split archive from a pinned google/fleurs
revision and verifies the SHA-256 and size in its Git LFS pointer. Both paths
convert a bounded deterministic row range to 16 kHz Float32 WAV and write
same-stem .txt reference sidecars plus manifest.tsv.

Generated audio, download archives, and reports are ignored by git.
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

is_supported_librispeech_split() {
    case "$1" in
        dev-clean|dev-other|test-clean|test-other) return 0 ;;
        *) return 1 ;;
    esac
}

is_supported_fleurs_split() {
    case "$1" in
        train|dev|test) return 0 ;;
        *) return 1 ;;
    esac
}

is_supported_fleurs_language() {
    # These are the FLEURS locales corresponding to Presspeech's exposed
    # Parakeet language hints. Keep this bounded: accepting arbitrary FLEURS
    # languages would imply product support that Presspeech does not offer.
    case "$1" in
        en_us|es_419|fr_fr|de_de|it_it|pt_br|ro_ro|pl_pl|cs_cz|sk_sk|sl_si|hr_hr|bs_ba|ru_ru|uk_ua|be_by|bg_bg|sr_rs) return 0 ;;
        *) return 1 ;;
    esac
}

fleurs_language_hint() {
    case "$1" in
        en_us) printf 'en' ;;
        es_419) printf 'es' ;;
        fr_fr) printf 'fr' ;;
        de_de) printf 'de' ;;
        it_it) printf 'it' ;;
        pt_br) printf 'pt' ;;
        ro_ro) printf 'ro' ;;
        pl_pl) printf 'pl' ;;
        cs_cz) printf 'cs' ;;
        sk_sk) printf 'sk' ;;
        sl_si) printf 'sl' ;;
        hr_hr) printf 'hr' ;;
        bs_ba) printf 'bs' ;;
        ru_ru) printf 'ru' ;;
        uk_ua) printf 'uk' ;;
        be_by) printf 'be' ;;
        bg_bg) printf 'bg' ;;
        sr_rs) printf 'sr' ;;
        *) return 1 ;;
    esac
}

fleurs_base_url() {
    printf 'https://huggingface.co/datasets/google/fleurs'
}

fleurs_tsv_url() {
    printf '%s/resolve/%s/data/%s/%s.tsv' \
        "$(fleurs_base_url)" "$FLEURS_REVISION" "$1" "$2"
}

fleurs_archive_url() {
    printf '%s/resolve/%s/data/%s/audio/%s.tar.gz' \
        "$(fleurs_base_url)" "$FLEURS_REVISION" "$1" "$2"
}

fleurs_pointer_url() {
    printf '%s/raw/%s/data/%s/audio/%s.tar.gz' \
        "$(fleurs_base_url)" "$FLEURS_REVISION" "$1" "$2"
}

librispeech_archive_name() {
    local split="$1"
    printf '%s.tar.gz' "$split"
}

librispeech_archive_url() {
    local split="$1"
    printf 'https://www.openslr.org/resources/12/%s' "$(librispeech_archive_name "$split")"
}

librispeech_md5_url() {
    printf 'https://www.openslr.org/resources/12/md5sum.txt'
}

compute_md5() {
    local file="$1"
    if command -v md5 >/dev/null 2>&1; then
        md5 -q "$file"
    elif command -v md5sum >/dev/null 2>&1; then
        md5sum "$file" | awk '{ print $1 }'
    else
        echo "md5 or md5sum is required for archive verification" >&2
        exit 1
    fi
}

compute_sha256() {
    local file="$1"
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$file" | awk '{ print $1 }'
    elif command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$file" | awk '{ print $1 }'
    else
        echo "shasum or sha256sum is required for archive verification" >&2
        exit 1
    fi
}

expected_md5_for_archive() {
    local md5_file="$1"
    local archive_name="$2"
    awk -v name="$archive_name" '$2 == name { print $1 }' "$md5_file" | head -n 1
}

verify_md5() {
    local file="$1"
    local expected="$2"
    local actual
    actual="$(compute_md5 "$file")"
    if [[ "$actual" != "$expected" ]]; then
        cat >&2 <<MSG
checksum mismatch for $file
expected: $expected
actual:   $actual

Delete the cached archive and rerun the fetcher.
MSG
        exit 1
    fi
}

verify_sha256() {
    local file="$1"
    local expected="$2"
    local actual
    actual="$(compute_sha256 "$file")"
    if [[ "$actual" != "$expected" ]]; then
        cat >&2 <<MSG
checksum mismatch for $file
expected: $expected
actual:   $actual

Delete the cached archive and rerun the fetcher.
MSG
        exit 1
    fi
}

parse_lfs_pointer() {
    local pointer="$1"
    local oid size
    oid="$(sed -nE 's/^oid sha256:([0-9a-f]{64})$/\1/p' "$pointer")"
    size="$(sed -nE 's/^size ([0-9]+)$/\1/p' "$pointer")"
    if [[ -z "$oid" || -z "$size" ]]; then
        echo "invalid FLEURS Git LFS pointer: $pointer" >&2
        return 1
    fi
    printf '%s\t%s\n' "$oid" "$size"
}

verify_size() {
    local file="$1"
    local expected="$2"
    local actual
    actual="$(wc -c <"$file" | tr -d '[:space:]')"
    if [[ "$actual" != "$expected" ]]; then
        echo "size mismatch for $file: expected $expected bytes, got $actual" >&2
        exit 1
    fi
}

download_file() {
    local url="$1"
    local dest="$2"

    mkdir -p "$(dirname "$dest")"
    if [[ -f "$dest" ]]; then
        echo "using cached download: $dest"
        return
    fi

    echo "downloading $url"
    curl -fL --retry 3 --continue-at - --output "$dest" "$url"
}

select_librispeech_entries() {
    local transcript_root="$1"
    local split="$2"
    local start_index="$3"
    local count="$4"
    local out_tsv="$5"
    local seen=0
    local selected=0

    : >"$out_tsv"
    while IFS= read -r transcript_file; do
        while IFS= read -r line; do
            [[ -n "${line//[[:space:]]/}" ]] || continue
            if [[ "$line" != *" "* ]]; then
                echo "malformed transcript row in $transcript_file: $line" >&2
                exit 1
            fi

            local original_id="${line%% *}"
            local text="${line#* }"
            local speaker="${original_id%%-*}"
            local rest="${original_id#*-}"
            local chapter="${rest%%-*}"
            local member="LibriSpeech/$split/$speaker/$chapter/$original_id.flac"

            if [[ "$seen" -ge "$start_index" && "$selected" -lt "$count" ]]; then
                printf '%s\t%s\t%s\n' "$original_id" "$member" "$text" >>"$out_tsv"
                selected=$((selected + 1))
            fi

            seen=$((seen + 1))
            if [[ "$selected" -ge "$count" ]]; then
                break
            fi
        done <"$transcript_file"

        if [[ "$selected" -ge "$count" ]]; then
            break
        fi
    done < <(find "$transcript_root" -type f -name '*.trans.txt' | LC_ALL=C sort)

    if [[ "$selected" -lt "$count" ]]; then
        echo "requested $count clip(s), but only selected $selected from $transcript_root" >&2
        exit 1
    fi
}

select_fleurs_entries() {
    local source_tsv="$1"
    local start_index="$2"
    local count="$3"
    local out_tsv="$4"

    # FLEURS rows are: sentence id, WAV filename, raw transcript, normalized
    # transcript, character transcript, sample count, gender. Preserve the raw
    # human reference so casing and punctuation quality remain measurable.
    awk -F '\t' -v start="$start_index" -v count="$count" '
        BEGIN { OFS = "\t"; selected = 0; malformed = 0 }
        NR > start && selected < count {
            if (NF < 7 || $2 !~ /^[0-9]+[.]wav$/ || $3 == "") {
                malformed = 1
                next
            }
            print $1, $2, $3
            selected += 1
        }
        END {
            if (malformed || selected < count) {
                printf("requested %d FLEURS clip(s) at offset %d, selected %d valid row(s)\n",
                       count, start, selected) > "/dev/stderr"
                exit 1
            }
        }
    ' "$source_tsv" >"$out_tsv"
}

select_fleurs_archive_members() {
    local archive="$1"
    local selected_tsv="$2"
    local out_members="$3"
    local wanted="$4"

    cut -f2 "$selected_tsv" >"$wanted"
    tar -tzf "$archive" | awk -v wanted_file="$wanted" '
        BEGIN {
            while ((getline line < wanted_file) > 0) {
                wanted[line] = 1
                wanted_count += 1
            }
            close(wanted_file)
        }
        {
            member = $0
            count = split(member, parts, "/")
            base = parts[count]
            if (member ~ /^\// || member ~ /(^|\/)\.\.($|\/)/) {
                printf("unsafe member in FLEURS archive: %s\n", member) > "/dev/stderr"
                unsafe = 1
            } else if (base in wanted) {
                if (seen[base]++) {
                    printf("duplicate FLEURS audio member: %s\n", base) > "/dev/stderr"
                    duplicate = 1
                }
                print member
                found += 1
            }
        }
        END {
            if (unsafe || duplicate || found != wanted_count) {
                if (found != wanted_count) {
                    printf("FLEURS archive contained %d of %d selected audio files\n",
                           found, wanted_count) > "/dev/stderr"
                }
                exit 1
            }
        }
    ' >"$out_members"
}

filter_librispeech_transcript_members() {
    local split_name="$1"
    awk -v split_name="$split_name" '$0 ~ "^LibriSpeech/" split_name "/.*\\.trans\\.txt$" { print }'
}

safe_remove_generated_dir() {
    local dir="$1"
    local trimmed_dir="$dir"
    local canonical_dir=""
    local expected_marker="Presspeech generated public speech fixtures"
    local expected_readme="Generated public Presspeech benchmark fixtures."
    local expected_manifest=$'clip_id\tsource\tsplit\toriginal_id\toriginal_audio\tlicense\treference'

    while [[ "$trimmed_dir" != "/" && "$trimmed_dir" == */ ]]; do
        trimmed_dir="${trimmed_dir%/}"
    done
    if [[ -z "$trimmed_dir" || "$trimmed_dir" == "." || "$trimmed_dir" == ".." ||
          "$trimmed_dir" == */. || "$trimmed_dir" == */.. ||
          ! -d "$trimmed_dir" || -L "$trimmed_dir" ]]; then
        echo "refusing to remove unowned fixture directory: $dir" >&2
        return 1
    fi
    canonical_dir="$(cd "$trimmed_dir" && pwd -P)"
    local current_dir
    current_dir="$(pwd -P)"
    if [[ "$canonical_dir" == "/" || "$canonical_dir" == "$current_dir" ||
          "$current_dir" == "$canonical_dir/"* ]]; then
        echo "refusing to remove unsafe fixture directory: $dir" >&2
        return 1
    fi

    local marker="$canonical_dir/.presspeech-public-fixtures"
    local readme="$canonical_dir/README.txt"
    local manifest="$canonical_dir/manifest.tsv"

    # New imports get an ownership marker before the first conversion so an
    # interrupted run remains safely replaceable. Accept the exact legacy
    # README + manifest header for fixture sets created before that marker.
    if [[ -f "$marker" && ! -L "$marker" &&
          "$(cat "$marker")" == "$expected_marker" ]]; then
        rm -rf -- "$canonical_dir"
        return
    fi
    if [[ -f "$readme" && ! -L "$readme" &&
          -f "$manifest" && ! -L "$manifest" &&
          "$(head -n 1 "$readme")" == "$expected_readme" &&
          "$(head -n 1 "$manifest")" == "$expected_manifest" ]]; then
        rm -rf -- "$canonical_dir"
        return
    fi

    echo "refusing to remove unowned fixture directory: $dir" >&2
    return 1
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

assert_success() {
    local label="$1"
    shift
    if ! "$@"; then
        echo "self-test expected success: $label" >&2
        exit 1
    fi
}

assert_failure() {
    local label="$1"
    shift
    if "$@"; then
        echo "self-test expected failure: $label" >&2
        exit 1
    fi
}

assert_file_contains() {
    local file="$1"
    local needle="$2"
    if ! grep -Fq -- "$needle" "$file"; then
        echo "self-test expected $file to contain: $needle" >&2
        exit 1
    fi
}

run_self_test() {
    local tmpdir
    tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/presspeech-public-fetch-self-test.XXXXXX")"
    trap 'rm -rf "$tmpdir"' EXIT INT TERM

    assert_success "supported split" is_supported_librispeech_split "dev-clean"
    assert_failure "unsupported split" is_supported_librispeech_split "train-clean-100"
    assert_success "supported FLEURS split" is_supported_fleurs_split "test"
    assert_failure "unsupported FLEURS split" is_supported_fleurs_split "validation"
    assert_success "supported FLEURS product locale" is_supported_fleurs_language "uk_ua"
    assert_failure "unexposed FLEURS locale" is_supported_fleurs_language "sw_ke"
    assert_eq "$(fleurs_language_hint uk_ua)" "uk" "FLEURS product language hint"
    assert_success "positive integer" is_positive_integer "1"
    assert_failure "zero is not positive" is_positive_integer "0"
    assert_success "non-negative integer" is_nonnegative_integer "0"
    assert_eq "$(librispeech_archive_name dev-clean)" "dev-clean.tar.gz" "archive name"
    assert_eq "$(librispeech_archive_url test-other)" "https://www.openslr.org/resources/12/test-other.tar.gz" "archive URL"

    local member_list="$tmpdir/members.txt"
    {
        echo "LibriSpeech/dev-clean/1/2/1-2.trans.txt"
        echo "LibriSpeech/test-clean/1/2/1-2.trans.txt"
        echo "LibriSpeech/dev-clean/1/2/1-2-0000.flac"
    } | filter_librispeech_transcript_members "dev-clean" >"$member_list"
    assert_eq "$(wc -l <"$member_list" | tr -d '[:space:]')" "1" "transcript member filter count"
    assert_file_contains "$member_list" "LibriSpeech/dev-clean/1/2/1-2.trans.txt"

    local checksum_file="$tmpdir/md5sum.txt"
    local data_file="$tmpdir/data.bin"
    printf 'fixture bytes\n' >"$data_file"
    printf '%s  data.bin\n' "$(compute_md5 "$data_file")" >"$checksum_file"
    assert_eq "$(expected_md5_for_archive "$checksum_file" "data.bin")" "$(compute_md5 "$data_file")" "expected md5 parser"
    verify_md5 "$data_file" "$(compute_md5 "$data_file")"

    local lfs_pointer="$tmpdir/archive.pointer"
    {
        echo "version https://git-lfs.github.com/spec/v1"
        echo "oid sha256:$(compute_sha256 "$data_file")"
        echo "size $(wc -c <"$data_file" | tr -d '[:space:]')"
    } >"$lfs_pointer"
    assert_eq "$(parse_lfs_pointer "$lfs_pointer")" \
        "$(compute_sha256 "$data_file")"$'\t'"$(wc -c <"$data_file" | tr -d '[:space:]')" \
        "FLEURS LFS pointer"
    verify_sha256 "$data_file" "$(compute_sha256 "$data_file")"

    local transcript_root="$tmpdir/transcripts"
    mkdir -p "$transcript_root/1/2" "$transcript_root/3/4"
    {
        echo "1-2-0000 FIRST ROW"
        echo "1-2-0001 SECOND ROW"
    } >"$transcript_root/1/2/1-2.trans.txt"
    {
        echo "3-4-0000 THIRD ROW"
        echo "3-4-0001 FOURTH ROW"
    } >"$transcript_root/3/4/3-4.trans.txt"

    local selected="$tmpdir/selected.tsv"
    select_librispeech_entries "$transcript_root" "dev-clean" 1 2 "$selected"
    assert_file_contains "$selected" $'1-2-0001\tLibriSpeech/dev-clean/1/2/1-2-0001.flac\tSECOND ROW'
    assert_file_contains "$selected" $'3-4-0000\tLibriSpeech/dev-clean/3/4/3-4-0000.flac\tTHIRD ROW'

    local fleurs_tsv="$tmpdir/fleurs.tsv"
    {
        printf '10\t100.wav\tRaw reference one.\traw reference one\tr a w\t16000\tFEMALE\n'
        printf '11\t101.wav\tRaw reference two!\traw reference two\tr a w\t16000\tMALE\n'
        printf '12\t102.wav\tRaw reference three?\traw reference three\tr a w\t16000\tFEMALE\n'
    } >"$fleurs_tsv"
    local fleurs_selected="$tmpdir/fleurs-selected.tsv"
    select_fleurs_entries "$fleurs_tsv" 1 2 "$fleurs_selected"
    assert_file_contains "$fleurs_selected" $'11\t101.wav\tRaw reference two!'
    assert_file_contains "$fleurs_selected" $'12\t102.wav\tRaw reference three?'

    local fleurs_archive_root="$tmpdir/fleurs-archive-root"
    mkdir -p "$fleurs_archive_root/test"
    printf 'one' >"$fleurs_archive_root/test/101.wav"
    printf 'two' >"$fleurs_archive_root/test/102.wav"
    local fleurs_archive="$tmpdir/fleurs.tar.gz"
    tar -czf "$fleurs_archive" -C "$fleurs_archive_root" test
    local fleurs_members="$tmpdir/fleurs-members.txt"
    local fleurs_wanted="$tmpdir/fleurs-wanted.txt"
    select_fleurs_archive_members \
        "$fleurs_archive" "$fleurs_selected" "$fleurs_members" "$fleurs_wanted"
    assert_file_contains "$fleurs_members" "test/101.wav"
    assert_file_contains "$fleurs_members" "test/102.wav"

    local unowned_dir="$tmpdir/unowned"
    mkdir -p "$unowned_dir"
    printf 'keep me\n' >"$unowned_dir/user-data.txt"
    assert_failure "unowned fixture replacement" safe_remove_generated_dir "$unowned_dir"
    [[ -f "$unowned_dir/user-data.txt" ]] || {
        echo "self-test expected unowned fixture data to remain" >&2
        exit 1
    }

    local symlink_target="$tmpdir/symlink-target"
    local symlink_dir="$tmpdir/symlink-fixtures"
    mkdir -p "$symlink_target"
    printf 'Presspeech generated public speech fixtures\n' >"$symlink_target/.presspeech-public-fixtures"
    ln -s "$symlink_target" "$symlink_dir"
    assert_failure "symlink fixture replacement" safe_remove_generated_dir "$symlink_dir/"
    [[ -f "$symlink_target/.presspeech-public-fixtures" ]] || {
        echo "self-test expected symlink target to remain" >&2
        exit 1
    }

    local marked_dir="$tmpdir/marked"
    mkdir -p "$marked_dir"
    printf 'Presspeech generated public speech fixtures\n' >"$marked_dir/.presspeech-public-fixtures"
    assert_success "marked fixture replacement" safe_remove_generated_dir "$marked_dir"
    [[ ! -e "$marked_dir" ]] || {
        echo "self-test expected marked fixture directory removal" >&2
        exit 1
    }

    local legacy_dir="$tmpdir/legacy"
    mkdir -p "$legacy_dir"
    printf 'Generated public Presspeech benchmark fixtures.\n' >"$legacy_dir/README.txt"
    printf 'clip_id\tsource\tsplit\toriginal_id\toriginal_audio\tlicense\treference\n' >"$legacy_dir/manifest.tsv"
    assert_success "legacy fixture replacement" safe_remove_generated_dir "$legacy_dir"
    [[ ! -e "$legacy_dir" ]] || {
        echo "self-test expected legacy fixture directory removal" >&2
        exit 1
    }

    local missing_value_log="$tmpdir/missing-value.log"
    if bash "$SCRIPT_PATH" --count >"$missing_value_log" 2>&1; then
        echo "self-test expected --count without a value to fail" >&2
        exit 1
    fi
    assert_file_contains "$missing_value_log" "--count requires a value"

    rm -rf "$tmpdir"
    trap - EXIT INT TERM
    echo "public speech fixture fetcher self-test passed"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --source)
            need_value "$@"
            SOURCE="$2"
            shift 2
            ;;
        --split)
            need_value "$@"
            SPLIT="$2"
            SPLIT_EXPLICIT=1
            shift 2
            ;;
        --language)
            need_value "$@"
            LANGUAGE="$2"
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
        --out-dir)
            need_value "$@"
            OUT_ROOT="$2"
            shift 2
            ;;
        --fixture-dir)
            need_value "$@"
            FIXTURE_DIR_OVERRIDE="$2"
            shift 2
            ;;
        --cache-dir)
            need_value "$@"
            CACHE_DIR="$2"
            shift 2
            ;;
        --force)
            FORCE=1
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

case "$SOURCE" in
    librispeech)
        if ! is_supported_librispeech_split "$SPLIT"; then
            echo "unsupported LibriSpeech split: $SPLIT" >&2
            exit 2
        fi
        if [[ -n "$LANGUAGE" ]]; then
            echo "--language is valid only with --source fleurs" >&2
            exit 2
        fi
        ;;
    fleurs)
        if [[ "$SPLIT_EXPLICIT" -eq 0 ]]; then
            SPLIT="test"
        fi
        if ! is_supported_fleurs_split "$SPLIT"; then
            echo "unsupported FLEURS split: $SPLIT" >&2
            exit 2
        fi
        if [[ -z "$LANGUAGE" ]]; then
            echo "--language is required with --source fleurs" >&2
            exit 2
        fi
        if ! is_supported_fleurs_language "$LANGUAGE"; then
            echo "unsupported FLEURS locale for Presspeech: $LANGUAGE" >&2
            exit 2
        fi
        ;;
    *)
        echo "unsupported source: $SOURCE" >&2
        exit 2
        ;;
esac

if ! is_positive_integer "$COUNT"; then
    echo "--count must be a positive integer" >&2
    exit 2
fi

if ! is_nonnegative_integer "$START_INDEX"; then
    echo "--start-index must be a non-negative integer" >&2
    exit 2
fi

if ! command -v curl >/dev/null 2>&1; then
    echo "curl is required to download public fixtures" >&2
    exit 1
fi

if ! command -v tar >/dev/null 2>&1; then
    echo "tar is required to extract public fixtures" >&2
    exit 1
fi

if ! command -v afconvert >/dev/null 2>&1; then
    echo "afconvert is required to convert public fixtures to WAV" >&2
    exit 1
fi

if [[ -n "$FIXTURE_DIR_OVERRIDE" ]]; then
    fixture_dir="$FIXTURE_DIR_OVERRIDE"
elif [[ "$SOURCE" == "fleurs" ]]; then
    fixture_dir="$OUT_ROOT/fleurs-$LANGUAGE-$SPLIT"
else
    fixture_dir="$OUT_ROOT/librispeech-$SPLIT"
fi

if [[ -e "$fixture_dir" ]]; then
    if [[ "$FORCE" -eq 1 ]]; then
        safe_remove_generated_dir "$fixture_dir"
    else
        cat >&2 <<MSG
fixture directory already exists: $fixture_dir

Use --force to replace it, or choose a different --out-dir.
MSG
        exit 1
    fi
fi

mkdir -p "$CACHE_DIR" "$OUT_ROOT"
tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/presspeech-public-fetch.XXXXXX")"
cleanup() { rm -rf "$tmpdir"; }
trap cleanup EXIT INT TERM

selected="$tmpdir/selected.tsv"
audio_members="$tmpdir/audio-members.txt"
mkdir -p "$tmpdir/audio"

if [[ "$SOURCE" == "librispeech" ]]; then
    archive_name="$(librispeech_archive_name "$SPLIT")"
    archive_url="$(librispeech_archive_url "$SPLIT")"
    archive_path="$CACHE_DIR/$archive_name"
    md5_path="$CACHE_DIR/librispeech-md5sum.txt"
    download_file "$(librispeech_md5_url)" "$md5_path"
    expected_md5="$(expected_md5_for_archive "$md5_path" "$archive_name")"
    if [[ -z "$expected_md5" ]]; then
        echo "no upstream MD5 entry found for $archive_name" >&2
        exit 1
    fi

    download_file "$archive_url" "$archive_path"
    echo "verifying $archive_name..."
    verify_md5 "$archive_path" "$expected_md5"

    echo "reading LibriSpeech transcripts..."
    transcript_members="$tmpdir/transcript-members.txt"
    tar -tzf "$archive_path" \
        | filter_librispeech_transcript_members "$SPLIT" \
        | LC_ALL=C sort >"$transcript_members"
    if [[ ! -s "$transcript_members" ]]; then
        echo "no transcript files found in $archive_name for split $SPLIT" >&2
        exit 1
    fi

    mkdir -p "$tmpdir/transcripts"
    tar -xzf "$archive_path" -C "$tmpdir/transcripts" -T "$transcript_members"
    select_librispeech_entries \
        "$tmpdir/transcripts/LibriSpeech/$SPLIT" "$SPLIT" \
        "$START_INDEX" "$COUNT" "$selected"

    audio_members="$tmpdir/audio-members.txt"
    cut -f2 "$selected" >"$audio_members"
    echo "extracting selected audio..."
    tar -xzf "$archive_path" -C "$tmpdir/audio" -T "$audio_members"
else
    archive_name="fleurs-$LANGUAGE-$SPLIT-$FLEURS_REVISION.tar.gz"
    archive_url="$(fleurs_archive_url "$LANGUAGE" "$SPLIT")"
    archive_path="$CACHE_DIR/$archive_name"
    pointer_path="$CACHE_DIR/$archive_name.pointer"
    tsv_path="$CACHE_DIR/fleurs-$LANGUAGE-$SPLIT-$FLEURS_REVISION.tsv"

    download_file "$(fleurs_pointer_url "$LANGUAGE" "$SPLIT")" "$pointer_path"
    pointer_metrics="$(parse_lfs_pointer "$pointer_path")"
    IFS=$'\t' read -r expected_sha256 expected_size <<<"$pointer_metrics"
    download_file "$(fleurs_tsv_url "$LANGUAGE" "$SPLIT")" "$tsv_path"
    select_fleurs_entries "$tsv_path" "$START_INDEX" "$COUNT" "$selected"

    download_file "$archive_url" "$archive_path"
    echo "verifying $archive_name..."
    verify_size "$archive_path" "$expected_size"
    verify_sha256 "$archive_path" "$expected_sha256"

    audio_members="$tmpdir/audio-members.txt"
    wanted_audio="$tmpdir/wanted-audio.txt"
    select_fleurs_archive_members \
        "$archive_path" "$selected" "$audio_members" "$wanted_audio"
    echo "extracting selected audio..."
    tar -xzf "$archive_path" -C "$tmpdir/audio" -T "$audio_members"
fi

mkdir -p "$fixture_dir"
printf 'Presspeech generated public speech fixtures\n' >"$fixture_dir/.presspeech-public-fixtures"
manifest="$fixture_dir/manifest.tsv"
{
    printf 'clip_id\tsource\tsplit\toriginal_id\toriginal_audio\tlicense\treference\n'
} >"$manifest"

ordinal=0
while IFS=$'\t' read -r original_id original_member reference; do
    ordinal=$((ordinal + 1))
    if [[ "$SOURCE" == "librispeech" ]]; then
        clip_id="$(printf 'librispeech-%s-%04d-%s' "$SPLIT" "$ordinal" "$original_id")"
        source_audio="$tmpdir/audio/$original_member"
        manifest_source="LibriSpeech"
        manifest_split="$SPLIT"
    else
        original_stem="${original_member%.wav}"
        clip_id="$(printf 'fleurs-%s-%s-%04d-%s' "$LANGUAGE" "$SPLIT" "$ordinal" "$original_stem")"
        archive_member="$(awk -v base="$original_member" '
            { count = split($0, parts, "/"); if (parts[count] == base) { print; exit } }
        ' "$audio_members")"
        source_audio="$tmpdir/audio/$archive_member"
        manifest_source="FLEURS-$LANGUAGE"
        manifest_split="$SPLIT"
        original_member="$archive_member"
    fi
    out_wav="$fixture_dir/$clip_id.wav"
    out_ref="$fixture_dir/$clip_id.txt"

    if [[ ! -f "$source_audio" ]]; then
        echo "selected audio missing from archive extraction: $original_member" >&2
        exit 1
    fi

    echo "importing $clip_id..."
    afconvert -f WAVE -d LEF32@16000 "$source_audio" "$out_wav"
    printf '%s\n' "$reference" >"$out_ref"
    printf '%s\t%s\t%s\t%s\t%s\tCC BY 4.0\t%s\n' \
        "$clip_id" "$manifest_source" "$manifest_split" "$original_id" \
        "$original_member" "$reference" >>"$manifest"
done <"$selected"

if [[ "$SOURCE" == "librispeech" ]]; then
    cat >"$fixture_dir/README.txt" <<MSG
Generated public Presspeech benchmark fixtures.

Source: LibriSpeech ASR corpus, split $SPLIT
License: CC BY 4.0
Upstream: https://www.openslr.org/12
Archive: $archive_url
Imported clips: $COUNT
Start index: $START_INDEX

These files are ignored by git. Recreate them with:
  ./fetch-public-speech-fixtures.sh --source librispeech --split $SPLIT --count $COUNT --start-index $START_INDEX
MSG
else
    product_language="$(fleurs_language_hint "$LANGUAGE")"
    cat >"$fixture_dir/README.txt" <<MSG
Generated public Presspeech benchmark fixtures.

Source: Google FLEURS speech corpus, locale $LANGUAGE, split $SPLIT
Presspeech language hint: $product_language
License: CC BY 4.0
Upstream: https://huggingface.co/datasets/google/fleurs
Dataset revision: $FLEURS_REVISION
Archive: $archive_url
Archive SHA-256: $expected_sha256
Imported clips: $COUNT
Start index: $START_INDEX

These files are ignored by git. Recreate them with:
  ./fetch-public-speech-fixtures.sh --source fleurs --language $LANGUAGE --split $SPLIT --count $COUNT --start-index $START_INDEX

Compare the production and candidate encoders with:
  ./run-real-model-comparison.sh --input-dir $fixture_dir --candidate-backend v3-int8-v2 --language $product_language --public-corpus --show-transcripts --show-paths --trials 3
MSG
fi

echo "fixtures: $fixture_dir"
echo "manifest: $manifest"
