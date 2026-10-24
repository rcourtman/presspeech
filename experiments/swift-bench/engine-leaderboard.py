#!/usr/bin/env python3
"""Prepare corpora for, and score, the cross-engine ASR leaderboard.

run-engine-leaderboard.sh drives this helper:

  prepare  fetch and verify public archives, draw a seeded subset per corpus,
           convert every clip to 16 kHz mono PCM16 WAV and write clip lists,
           references and a committed manifest (ids and PCM SHA-256 only)
  score    normalise references and hypotheses identically for every engine,
           compute corpus WER, latency percentiles, real-time factor and
           memory, and write the leaderboard JSON

Audio is never committed. LibriSpeech and FLEURS are CC BY 4.0.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import statistics
import struct
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FLEURS_REVISION = "70bb2e84b976b7e960aa89f1c648e09c59f894dd"
FLEURS_BASE = "https://huggingface.co/datasets/google/fleurs"
LIBRISPEECH_BASE = "https://www.openslr.org/resources/12"
# FLEURS locale -> Presspeech/Whisper language code, and the Apple locale
# used for the Apple engines, which have no auto-detect. FLEURS Spanish is
# Latin American (es_419), so Apple gets es-MX rather than es-ES.
FLEURS_LANGUAGES = {
    "de_de": ("de", "de-DE"),
    "es_419": ("es", "es-MX"),
    "fr_fr": ("fr", "fr-FR"),
    "it_it": ("it", "it-IT"),
    "pl_pl": ("pl", "pl-PL"),
}


class LeaderboardError(Exception):
    pass


# ---------------------------------------------------------------- prepare


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def md5_file(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, dest: Path) -> None:
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading {url}", file=sys.stderr)
    subprocess.run(
        ["curl", "-fL", "--retry", "3", "--continue-at", "-", "--output", str(dest) + ".part", url],
        check=True,
    )
    os.replace(str(dest) + ".part", dest)


def wav_pcm16_mono_16k(path: Path) -> tuple[bytes, float]:
    """Return the PCM data chunk and duration, rejecting any other format."""
    blob = path.read_bytes()
    if blob[:4] != b"RIFF" or blob[8:12] != b"WAVE":
        raise LeaderboardError(f"not a WAV file: {path}")
    offset, fmt_ok = 12, False
    while offset + 8 <= len(blob):
        chunk_id = blob[offset : offset + 4]
        size = struct.unpack_from("<I", blob, offset + 4)[0]
        start = offset + 8
        if chunk_id == b"fmt ":
            fmt, channels, rate = struct.unpack_from("<HHI", blob, start)
            bits = struct.unpack_from("<H", blob, start + 14)[0]
            if fmt == 0xFFFE and size >= 40:
                fmt = struct.unpack_from("<H", blob, start + 24)[0]
            fmt_ok = fmt == 1 and channels == 1 and rate == 16000 and bits == 16
        elif chunk_id == b"data":
            if not fmt_ok:
                raise LeaderboardError(f"not 16 kHz mono PCM16: {path}")
            data = blob[start : start + size]
            return data, len(data) / 2 / 16000
        offset = start + size + (size & 1)
    raise LeaderboardError(f"no data chunk: {path}")


def convert_to_pcm16(source: Path, dest: Path) -> None:
    # Both corpora are 16-bit 16 kHz sources, so this decode is lossless and
    # the PCM checksum does not depend on the conversion tool.
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(source), str(dest)],
        check=True,
    )


def seeded_sample(keys: list[str], count: int | None, seed: str) -> list[str]:
    ordered = sorted(keys)
    if count is None or count >= len(ordered):
        return ordered
    return sorted(random.Random(seed).sample(ordered, count))


def extract_members(archive: Path, wanted: dict[str, str], out_dir: Path) -> None:
    """Stream one tar.gz and write each wanted member to out_dir/<name>."""
    found: set[str] = set()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            if member.name not in wanted or not member.isfile():
                continue
            if member.name in found:
                raise LeaderboardError(f"duplicate archive member {member.name}")
            source = tar.extractfile(member)
            assert source is not None
            target = out_dir / wanted[member.name]
            with target.open("wb") as fh:
                shutil.copyfileobj(source, fh)
            found.add(member.name)
            if len(found) == len(wanted):
                break
    missing = set(wanted) - found
    if missing:
        raise LeaderboardError(f"{archive.name} lacks {len(missing)} selected member(s)")


def write_corpus(out: Path, name: str, rows: list[dict[str, object]]) -> None:
    corpus = out / name
    with (corpus / "clips.tsv").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(f"{row['id']}\t{corpus / (str(row['id']) + '.wav')}\n")
    with (corpus / "references.tsv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_NONE, escapechar="\\")
        for row in rows:
            writer.writerow([row["id"], row["reference"]])


def manifest_rows(out: Path, name: str, rows: list[dict[str, object]]) -> list[dict[str, object]]:
    result = []
    for row in rows:
        data, seconds = wav_pcm16_mono_16k(out / name / f"{row['id']}.wav")
        result.append({
            "id": row["id"],
            "source": row["source"],
            "pcm_sha256": hashlib.sha256(data).hexdigest(),
            "seconds": round(seconds, 4),
        })
    return result


def prepare_librispeech(cache: Path, out: Path, split: str, count: int | None, seed: int) -> list[dict[str, object]]:
    archive = cache / f"{split}.tar.gz"
    md5_list = cache / "librispeech-md5sum.txt"
    download(f"{LIBRISPEECH_BASE}/md5sum.txt", md5_list)
    download(f"{LIBRISPEECH_BASE}/{split}.tar.gz", archive)
    expected = {
        parts[1]: parts[0]
        for parts in (line.split() for line in md5_list.read_text().splitlines())
        if len(parts) == 2
    }.get(archive.name)
    if not expected or md5_file(archive) != expected:
        raise LeaderboardError(f"{archive.name} does not match the OpenSLR MD5")
    transcripts: dict[str, str] = {}
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            if member.isfile() and member.name.endswith(".trans.txt"):
                handle = tar.extractfile(member)
                assert handle is not None
                for line in handle.read().decode("utf-8").splitlines():
                    if line.strip():
                        utt, text = line.split(" ", 1)
                        transcripts[utt] = text
    chosen = seeded_sample(list(transcripts), count, f"{seed}:librispeech-{split}")
    name = f"librispeech-{split}"
    corpus = out / name
    shutil.rmtree(corpus, ignore_errors=True)
    corpus.mkdir(parents=True)
    rows, wanted = [], {}
    for utt in chosen:
        speaker, chapter, _ = utt.split("-")
        member = f"LibriSpeech/{split}/{speaker}/{chapter}/{utt}.flac"
        wanted[member] = f"{utt}.flac"
        rows.append({"id": utt, "source": member, "reference": transcripts[utt]})
    extract_members(archive, wanted, corpus)
    for row in rows:
        flac = corpus / f"{row['id']}.flac"
        convert_to_pcm16(flac, corpus / f"{row['id']}.wav")
        flac.unlink()
    write_corpus(out, name, rows)
    return rows


def prepare_fleurs(cache: Path, out: Path, locale: str, count: int | None, seed: int) -> list[dict[str, object]]:
    stem = f"fleurs-{locale}-test-{FLEURS_REVISION}"
    tsv, pointer, archive = (cache / f"{stem}.tsv", cache / f"{stem}.tar.gz.pointer", cache / f"{stem}.tar.gz")
    download(f"{FLEURS_BASE}/resolve/{FLEURS_REVISION}/data/{locale}/test.tsv", tsv)
    download(f"{FLEURS_BASE}/raw/{FLEURS_REVISION}/data/{locale}/audio/test.tar.gz", pointer)
    download(f"{FLEURS_BASE}/resolve/{FLEURS_REVISION}/data/{locale}/audio/test.tar.gz", archive)
    text = pointer.read_text()
    oid = re.search(r"^oid sha256:([0-9a-f]{64})$", text, re.M)
    size = re.search(r"^size (\d+)$", text, re.M)
    if not oid or not size or archive.stat().st_size != int(size[1]) or sha256_file(archive) != oid[1]:
        raise LeaderboardError(f"{archive.name} does not match its pinned Git LFS pointer")
    # FLEURS rows: sentence id, WAV name, raw transcript, normalised
    # transcript, characters, samples, gender. The same sentence is often
    # read by several speakers; keep one reading per sentence so a subset
    # does not repeat text.
    readings: dict[str, tuple[str, str]] = {}
    for line in tsv.read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        if len(fields) < 7 or not re.fullmatch(r"\d+\.wav", fields[1]) or not fields[2]:
            raise LeaderboardError(f"malformed FLEURS row in {tsv.name}")
        readings[fields[1][:-4]] = (fields[0], fields[2])
    by_sentence: dict[str, list[str]] = {}
    for clip, (sentence, _) in readings.items():
        by_sentence.setdefault(sentence, []).append(clip)
    rng = random.Random(f"{seed}:fleurs-{locale}-reading")
    one_per_sentence = [rng.choice(sorted(clips)) for _, clips in sorted(by_sentence.items())]
    chosen = seeded_sample(one_per_sentence, count, f"{seed}:fleurs-{locale}")
    name = f"fleurs-{locale}"
    corpus = out / name
    shutil.rmtree(corpus, ignore_errors=True)
    corpus.mkdir(parents=True)
    rows, wanted = [], {}
    for clip in chosen:
        member = f"test/{clip}.wav"
        wanted[member] = f"{clip}.source.wav"
        rows.append({"id": clip, "source": member, "reference": readings[clip][1]})
    extract_members(archive, wanted, corpus)
    for row in rows:
        source = corpus / f"{row['id']}.source.wav"
        convert_to_pcm16(source, corpus / f"{row['id']}.wav")
        source.unlink()
    write_corpus(out, name, rows)
    return rows


def prepare_long_form(cache: Path, out: Path, source_count: int) -> list[dict[str, object]]:
    """Compose the existing English long-form set with the existing tools."""
    work = out / "_long-form-work"
    shutil.rmtree(work, ignore_errors=True)
    subprocess.run(
        [str(HERE / "fetch-public-speech-fixtures.sh"), "--source", "librispeech", "--split", "dev-clean",
         "--count", str(source_count), "--fixture-dir", str(work / "source"), "--cache-dir", str(cache)],
        check=True, stdout=sys.stderr,
    )
    subprocess.run(
        [sys.executable, str(HERE / "compose-public-long-form-fixtures.py"), "--input-dir", str(work / "source"),
         "--output-dir", str(work / "composed"), "--target-seconds", "45"],
        check=True, stdout=sys.stderr,
    )
    name = "librispeech-dev-clean-long-form"
    corpus = out / name
    shutil.rmtree(corpus, ignore_errors=True)
    corpus.mkdir(parents=True)
    rows = []
    for wav in sorted((work / "composed").glob("*.wav")):
        reference = wav.with_suffix(".txt").read_text(encoding="utf-8").strip()
        convert_to_pcm16(wav, corpus / wav.name)
        rows.append({"id": wav.stem, "source": f"composed from dev-clean rows 1-{source_count}", "reference": reference})
    if not rows:
        raise LeaderboardError("long-form composition produced no clips")
    write_corpus(out, name, rows)
    shutil.rmtree(work)
    return rows


def cmd_prepare(args: argparse.Namespace) -> int:
    cache, out = args.cache_dir.resolve(), args.data_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    full = args.full_librispeech
    corpora: dict[str, list[dict[str, object]]] = {}
    for split in ("test-clean", "test-other"):
        corpora[f"librispeech-{split}"] = prepare_librispeech(
            cache, out, split, None if full else args.librispeech_count, args.seed)
    for locale in args.fleurs_locales:
        corpora[f"fleurs-{locale}"] = prepare_fleurs(cache, out, locale, args.fleurs_count, args.seed)
    corpora["librispeech-dev-clean-long-form"] = prepare_long_form(cache, out, args.long_form_source_count)

    manifest = {
        "seed": args.seed,
        "librispeech_count": None if full else args.librispeech_count,
        "fleurs_count": args.fleurs_count,
        "fleurs_revision": FLEURS_REVISION,
        "long_form_source_count": args.long_form_source_count,
        "audio_format": "16 kHz mono 16-bit PCM WAV (afconvert, lossless from 16-bit sources)",
        "corpora": {name: manifest_rows(out, name, rows) for name, rows in corpora.items()},
    }
    if args.manifest:
        if args.verify_manifest and args.manifest.exists():
            committed = json.loads(args.manifest.read_text())
            if committed != manifest:
                raise LeaderboardError(f"prepared corpora differ from {args.manifest}")
            print(f"prepared corpora match {args.manifest}", file=sys.stderr)
        else:
            args.manifest.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n")
    for name, rows in corpora.items():
        print(f"{name}: {len(rows)} clips", file=sys.stderr)
    return 0


# ---------------------------------------------------------------- score


def normalizers():
    try:
        from whisper_normalizer.basic import BasicTextNormalizer
        from whisper_normalizer.english import EnglishTextNormalizer
    except ImportError as error:
        raise LeaderboardError("whisper-normalizer is missing; run via run-engine-leaderboard.sh") from error
    return EnglishTextNormalizer(), BasicTextNormalizer()


def edit_distance(ref: list[str], hyp: list[str]) -> tuple[int, int, int]:
    """Return (substitutions, deletions, insertions) for a minimum alignment."""
    previous = [(j, 0, 0, j) for j in range(len(hyp) + 1)]  # cost, sub, del, ins
    for i in range(1, len(ref) + 1):
        current = [(i, 0, i, 0)]
        for j in range(1, len(hyp) + 1):
            if ref[i - 1] == hyp[j - 1]:
                current.append(previous[j - 1])
                continue
            sub = previous[j - 1]
            dele = previous[j]
            ins = current[j - 1]
            best = min(
                (sub[0] + 1, sub[1] + 1, sub[2], sub[3]),
                (dele[0] + 1, dele[1], dele[2] + 1, dele[3]),
                (ins[0] + 1, ins[1], ins[2], ins[3] + 1),
            )
            current.append(best)
        previous = current
    return previous[-1][1], previous[-1][2], previous[-1][3]


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    lower = math.floor(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def read_jsonl(path: Path) -> tuple[list[dict], dict]:
    clips, summary = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("type") == "clip":
            clips.append(record)
        elif record.get("type") == "summary":
            summary = record
    if summary is None:
        raise LeaderboardError(f"{path} has no summary (incomplete run)")
    return clips, summary


def read_time_l(path: Path) -> int | None:
    if not path.exists():
        return None
    match = re.search(r"^\s*(\d+)\s+peak memory footprint$", path.read_text(), re.M)
    return int(match[1]) if match else None


def score_run(path: Path, references: dict[str, str], normalize) -> dict[str, object]:
    clips, summary = read_jsonl(path)
    ids = [clip["id"] for clip in clips]
    if sorted(ids) != sorted(references):
        raise LeaderboardError(f"{path} does not cover exactly the corpus clips")
    subs = dels = ins = words = 0
    latencies, audio, detected = [], 0.0, {}
    failures = 0
    for clip in clips:
        ref = normalize(references[clip["id"]]).split()
        hyp = normalize(clip.get("text", "")).split()
        s, d, i = edit_distance(ref, hyp)
        subs, dels, ins, words = subs + s, dels + d, ins + i, words + len(ref)
        audio += clip["audio_seconds"]
        if "elapsed_seconds" in clip:
            latencies.append(clip["elapsed_seconds"])
        else:
            failures += 1
        if clip.get("detected_language"):
            detected[clip["detected_language"]] = detected.get(clip["detected_language"], 0) + 1
    result: dict[str, object] = {
        "clips": len(clips),
        "failed_clips": failures,
        "reference_words": words,
        "substitutions": subs,
        "deletions": dels,
        "insertions": ins,
        "wer": round(100 * (subs + dels + ins) / words, 3),
        "audio_seconds": round(audio, 3),
        "latency_ms_p50": round(1000 * percentile(latencies, 0.5), 1),
        "latency_ms_p90": round(1000 * percentile(latencies, 0.9), 1),
        "latency_ms_mean": round(1000 * statistics.fmean(latencies), 1),
        "rtf": round(sum(latencies) / audio, 4),
        "prepare_seconds": round(summary["prepare_seconds"], 2),
        "peak_footprint_bytes": summary.get("peak_footprint_bytes"),
        "peak_footprint_bytes_time_l": read_time_l(path.with_suffix(".time")),
    }
    if detected:
        result["detected_languages"] = dict(sorted(detected.items()))
    for key in ("model_cache_bytes", "whisper_version", "system_info", "threads", "beam_size", "best_of",
                "flash_attn", "use_gpu", "engine", "apple_locale"):
        if key in summary and (key != "apple_locale" or summary.get("backend", "").startswith("apple")):
            result[key] = summary[key]
    return result


def cmd_score(args: argparse.Namespace) -> int:
    english, basic = normalizers()
    data, results = args.data_dir.resolve(), args.results_dir.resolve()
    engines = json.loads(args.engines.read_text())
    environment = json.loads(args.environment.read_text()) if args.environment else {}
    corpora = sorted(p.name for p in data.iterdir() if (p / "references.tsv").exists())
    references = {}
    for corpus in corpora:
        with (data / corpus / "references.tsv").open(encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE, escapechar="\\")
            references[corpus] = {row[0]: row[1] for row in reader}
    output_engines = []
    for engine in engines["engines"]:
        entry = dict(engine)
        if engine.get("measured", True):
            runs: dict[str, dict[str, object]] = {}
            for mode in ("explicit", "auto"):
                mode_runs = {}
                for corpus in corpora:
                    path = results / engine["id"] / mode / f"{corpus}.jsonl"
                    if path.exists():
                        normalize = english if corpus.startswith("librispeech") else basic
                        mode_runs[corpus] = score_run(path, references[corpus], normalize)
                    elif path.with_suffix(".unsupported").exists():
                        mode_runs[corpus] = {"unsupported": path.with_suffix(".unsupported").read_text().strip()}
                if mode_runs:
                    runs[mode] = mode_runs
            entry["runs"] = runs
        output_engines.append(entry)
    manifest = json.loads(args.manifest.read_text())
    out = {
        "schema": 1,
        "generated_by": "experiments/swift-bench/run-engine-leaderboard.sh",
        "environment": environment,
        "corpora": {
            name: {
                "clips": len(rows),
                "audio_seconds": round(sum(r["seconds"] for r in rows), 2),
                "normaliser": "whisper EnglishTextNormalizer" if name.startswith("librispeech") else "whisper BasicTextNormalizer",
            }
            for name, rows in manifest["corpora"].items()
        },
        "selection": {k: manifest[k] for k in ("seed", "librispeech_count", "fleurs_count", "fleurs_revision",
                                                 "long_form_source_count", "audio_format")},
        "method": engines["method"],
        "engines": output_engines,
    }
    args.output.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {args.output}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------- self-test


def self_test() -> None:
    assert edit_distance("a b c".split(), "a b c".split()) == (0, 0, 0)
    assert edit_distance("a b c".split(), "a x c".split()) == (1, 0, 0)
    assert edit_distance("a b c".split(), "a c".split()) == (0, 1, 0)
    assert edit_distance("a b".split(), "a b c d".split()) == (0, 0, 2)
    assert edit_distance([], "a".split()) == (0, 0, 1)
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert seeded_sample(list("edcba"), 2, "s") == seeded_sample(list("abcde"), 2, "s")
    assert seeded_sample(list("abc"), None, "s") == ["a", "b", "c"]
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "t.wav"
        pcm = struct.pack("<4h", 0, 1, -1, 32767)
        fmt = struct.pack("<HHIIHH", 1, 1, 16000, 32000, 2, 16)
        path.write_bytes(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<I", 16)
                         + fmt + b"data" + struct.pack("<I", len(pcm)) + pcm)
        data, seconds = wav_pcm16_mono_16k(path)
        assert data == pcm and seconds == 4 / 16000
        path.write_bytes(path.read_bytes().replace(struct.pack("<I", 16000), struct.pack("<I", 44100), 1))
        try:
            wav_pcm16_mono_16k(path)
        except LeaderboardError:
            pass
        else:
            raise AssertionError("44.1 kHz WAV accepted")
    print("engine leaderboard self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--cache-dir", type=Path, required=True)
    prep.add_argument("--data-dir", type=Path, required=True)
    prep.add_argument("--manifest", type=Path)
    prep.add_argument("--verify-manifest", action="store_true")
    prep.add_argument("--seed", type=int, default=20261024)
    prep.add_argument("--librispeech-count", type=int, default=500)
    prep.add_argument("--full-librispeech", action="store_true")
    prep.add_argument("--fleurs-count", type=int, default=100)
    prep.add_argument("--fleurs-locales", nargs="+", default=list(FLEURS_LANGUAGES))
    prep.add_argument("--long-form-source-count", type=int, default=100)
    score = sub.add_parser("score")
    score.add_argument("--data-dir", type=Path, required=True)
    score.add_argument("--results-dir", type=Path, required=True)
    score.add_argument("--engines", type=Path, required=True)
    score.add_argument("--manifest", type=Path, required=True)
    score.add_argument("--environment", type=Path)
    score.add_argument("--output", type=Path, required=True)
    sub.add_parser("self-test")
    sub.add_parser("fleurs-languages")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            return cmd_prepare(args)
        if args.command == "score":
            return cmd_score(args)
        if args.command == "fleurs-languages":
            for locale, (code, apple) in FLEURS_LANGUAGES.items():
                print(f"{locale}\t{code}\t{apple}")
            return 0
        self_test()
        return 0
    except LeaderboardError as error:
        print(f"engine-leaderboard: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
