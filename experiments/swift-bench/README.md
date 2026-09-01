# swift-bench

Head-to-head ASR benchmark of production and candidate transcription backends running
against the same WAV files on the same Mac, so they can be compared
in like-for-like units.

Originally built to answer "should Presspeech port from Python+MLX to
native Swift?" — the answer turned out to be yes, and the production
app now runs on FluidAudio (see [`../../swift/`](../../swift/) and
the [main README](../../README.md)). The bench stays in
`experiments/` because it's still the cleanest way to validate any
future backend / model / FluidAudio-version change without touching
the production app.

## Backends

| Tag | Stack | Where it runs |
|---|---|---|
| **`v3`** | FluidAudio Swift SDK → Parakeet TDT 0.6 B **v3** → CoreML | Apple Neural Engine |
| **`v2`** | FluidAudio Swift SDK → English-only Parakeet TDT 0.6 B **v2** → CoreML | Apple Neural Engine |
| **`v3-int8-v2`** | production `v3` path + candidate linear-int8 `Encoder_v2` | Apple Neural Engine |
| **`v3-vocab`** | production `v3` + auxiliary CTC custom-vocabulary rescorer | Apple Neural Engine |
| **`v3-vocab-conservative`** | `v3-vocab` + FluidAudio's short-term taper and spotter similarity floors | Apple Neural Engine |
| **`v3-vocab-no-rescue`** | `v3-vocab` with acoustic-only spotter rescue disabled | Apple Neural Engine |
| **`v3-vocab-exact-similarity`** | production `v3` + only legacy-selected candidate-evidence replacements with exact normalized scorer similarity | Apple Neural Engine |
| **`sliding-v3`** | FluidAudio sliding-window manager → Parakeet TDT 0.6 B **v3** → CoreML | Apple Neural Engine |
| **`sliding-vocab`** | `sliding-v3` + auxiliary CTC custom-vocabulary rescorer | Apple Neural Engine |
| **`sliding-vocab-conservative`** | `sliding-vocab` + FluidAudio's short-term taper and spotter similarity floors | Apple Neural Engine |
| **`sliding-vocab-no-rescue`** | `sliding-vocab` with acoustic-only spotter rescue disabled | Apple Neural Engine |
| **`unified`** | FluidAudio Swift SDK → Parakeet Unified 0.6 B offline batch → CoreML | Apple Neural Engine |
| **`nemotron-en`** | FluidAudio Swift SDK → Nemotron Speech Streaming English 0.6 B, 1120 ms tier → CoreML | Apple Neural Engine |
| **`nemotron-multilingual`** | FluidAudio Swift SDK → Nemotron 3.5 Streaming Multilingual 0.6 B → CoreML | Apple Neural Engine |
| **`apple`** | `Speech.SpeechAnalyzer` + `DictationTranscriber` (built into macOS 26 Tahoe) | Apple Neural Engine |
| **`presspeech-mlx`** | presspeech's current path: `parakeet_mlx` → MLX → Metal | GPU |

## How to run

The FluidAudio backends, including every `v3-vocab` variant, run on the
same Apple Silicon macOS 14+ floor as Presspeech. The optional `apple`
backend requires macOS 26 and a macOS 26 SDK; older SDKs compile the
FluidAudio-only runner without that backend.

```sh
cd experiments/swift-bench

# 1. Generate test audio (4 clips: short-clean, medium-clean, disfluent,
#    longer-technical). Uses macOS `say` for TTS, `afconvert` for 16 kHz
#    mono Float32 WAV.
./generate-test-audio.sh

# 2. Build the Swift CLI (downloads FluidAudio dep + Parakeet v3 CoreML
#    weights on first run — model is ~600 MB, cached in
#    ~/Library/Application Support/FluidAudio/).
swift build

# 3a. Swift backends.
./.build/debug/presspeech-bench --file test-audio/short-clean.wav --backend v3 --trials 5
./.build/debug/presspeech-bench --file test-audio/short-clean.wav --backend v2 --trials 5
./.build/debug/presspeech-bench --file test-audio/short-clean.wav --backend unified --trials 5
./.build/debug/presspeech-bench --file test-audio/short-clean.wav --backend nemotron-multilingual --nemotron-multilingual-language en-US --nemotron-multilingual-chunk-ms 2240 --trials 5

# Unified uses a 250 ms candidate padding default in this benchmark.
# Set it to 0 to measure the raw model, or sweep values with
# run-tail-word-regression.sh when evaluating a future model.
./.build/debug/presspeech-bench --file test-audio/short-clean.wav --backend unified --trials 5 --unified-trailing-silence-ms 0

# 3b. Python / MLX backend, same audio.
../../.venv/bin/python bench-py.py --file test-audio/short-clean.wav --trials 5
```

The Swift benchmark normally pins FluidAudio to the same revision as the
production app. Its current pin is an intentional candidate-only exception:
it includes FluidAudio's opt-in `int8-v2` encoder API while the app remains on
the released v0.15.6 commit. Both direct-v3 benchmark lanes explicitly retain
the app's released mel-context chunking behavior because the candidate
revision also changed that long-form default; an encoder-precision comparison
must not vary both controls at once. Do not move the app pin until the
candidate checks below have passed.

`Package.resolved` is committed for the benchmark for the same reason
as the app: dependency changes should be visible in review. Candidate-only
API evaluation may move the benchmark pin with the exception documented
above. When promoting a validated FluidAudio revision to production, update
the app and benchmark manifests plus both resolved files together.

## Private real-dictation regression

TTS is useful for latency and smoke testing, but it is not a substitute
for human dictation. For local accuracy checks, put private clips under
`real-audio/` with matching `.txt` reference sidecars:

```text
real-audio/
  short-note.wav
  short-note.txt
  noisy-room.m4a
  noisy-room.txt
```

Then run:

```sh
./run-real-model-comparison.sh --trials 3
```

The comparison script normalizes audio through `afconvert`, runs v3 and a
selected candidate against every clip, and writes ignored Markdown/TSV reports
under `real-results/` with corpus WER, worst WER, final-word failures, and p50
latency by backend. Corpus WER is computed from exact edit and reference-word
counts, so longer clips contribute proportionally; worst-trial selection also
uses those exact counts rather than the rounded display percentage. Transcript
text, fixture filenames, and local paths are
redacted by default while WER, latency, and retention numbers remain
visible. Pass `--show-transcripts` and `--show-paths` only for local
reports you do not intend to share.

To add a local recording and reference sidecar without hand-copying files:

```sh
./add-real-dictation-fixture.sh --id short-note-001 --audio ~/Desktop/short-note.m4a --reference-file ~/Desktop/short-note.txt
```

For single-backend debugging:

```sh
./run-real-dictation-regression.sh --backend v3 --trials 5
./run-real-dictation-regression.sh --backend v2 --trials 5
./run-real-dictation-regression.sh --backend unified --trials 5 --unified-trailing-silence-ms 250
./run-real-dictation-regression.sh --backend nemotron-en --trials 5
./run-real-dictation-regression.sh --backend nemotron-multilingual --nemotron-multilingual-language en-US --nemotron-multilingual-chunk-ms 2240 --trials 5
```

Each single-backend report ends with the same aggregate decision metrics used
below: average and worst WER, final-word failures, and average p50 latency.

For a quick non-ASR check of argument parsing and report redaction:

```sh
./add-real-dictation-fixture.sh --self-test
./run-real-model-comparison.sh --self-test
./run-real-dictation-regression.sh --self-test
./run-vocabulary-bias-regression.sh --self-test
./.build/debug/presspeech-bench --self-test
```

## Custom-vocabulary regression

FluidAudio's auxiliary CTC vocabulary rescorer accepts the transcript and token
timings returned by the direct `AsrManager` call used by Presspeech. Its
sliding-window manager also exposes a convenience integration. The vocabulary
runner therefore compares nine isolated processes so engine-path changes are
not misattributed to vocabulary biasing:

1. production `v3`;
2. `v3-vocab` with the auxiliary CTC model and default rescorer;
3. `v3-vocab-conservative` with FluidAudio's recommended short-term taper and
   spotter similarity floors;
4. `v3-vocab-no-rescue`, which disables acoustic-only spotter rescue;
5. `v3-vocab-exact-similarity`, which uses FluidAudio's non-mutating candidate
   evidence and applies only candidates selected by legacy overlap arbitration
   whose normalized scorer similarity is exactly 1.0;
6. `sliding-v3` without vocabulary boosting;
7. `sliding-vocab` with the auxiliary CTC model and rescorer;
8. `sliding-vocab-conservative` with FluidAudio's recommended short-term
   taper (pivot 5) and spotter-rescue similarity floors (0.30 single-word,
   0.50 multi-word); and
9. `sliding-vocab-no-rescue`, which disables the acoustic-only spotter rescue
   that upstream identifies as the dominant source of short-term false
   replacements while leaving the string-similarity path active.

Prepare an input directory using the same audio + `.txt` sidecars as the real
dictation regression, a FluidAudio vocabulary file, and a plain-text critical
term list:

```text
polish-benchmark/
  sentence-01.wav
  sentence-01.txt
  sentence-02.wav
  sentence-02.txt

vocabulary.txt       # FluidAudio simple format: canonical: alias1, alias2
critical-terms.txt   # every exact canonical vocabulary form, one per line
```

Then run:

```sh
./run-vocabulary-bias-regression.sh \
  --input-dir polish-benchmark \
  --negative-control-dir polish-negative-controls \
  --vocabulary vocabulary.txt \
  --critical-terms critical-terms.txt \
  --language pl \
  --references-hand-audited \
  --trials 3
```

Before committing to the full nine-policy run, use the same arguments with
`--preflight-only`. It builds only the benchmark helper, validates paired
sidecars, reference counts, control contamination, source and normalized-audio
duplicates, audit status, evidence floors, and input provenance, then exits
without loading an ASR model:

```sh
./run-vocabulary-bias-regression.sh \
  --input-dir polish-benchmark \
  --negative-control-dir polish-negative-controls \
  --vocabulary vocabulary.txt \
  --critical-terms critical-terms.txt \
  --language pl \
  --references-hand-audited \
  --preflight-only
```

The summary contains aggregate counts and one folded input fingerprint, not
transcripts, vocabulary entries, fixture names, or local paths. Add
`--no-threshold` when checking an incomplete exploratory corpus; that mode
reports that the product-candidate evidence floors were not enforced.

The negative-control directory should contain ordinary speech in the same
language and from the same push-to-talk workflow in which none of the configured
critical terms occurs. It always uses the target `--language` hint. Those clips
run through every policy, and the product-candidate screen rejects any new term
insertion or WER regression. Before loading an ASR model, the runner uses the
benchmark executable's exact normalization to count reference words and critical
occurrences and to reject contaminated controls. This avoids hundreds of model
runs when a corpus cannot meet the evidence floor. The target corpus must contain
at least 25 distinct clips, 1,000 reference words, and 50 critical-term
occurrences for a thresholded run. Every target and control reference must also
be listened to and corrected by a human; pass `--references-hand-audited` only
after that review. A transcript
from another ASR can be a starting point, but cannot be the ground truth for a
thresholded run without human verification. Use `--no-threshold` for exploratory
runs with unaudited references. These bounds are below the first real-user
vocabulary corpus (40 clips,
1,295 words, and 68 occurrences), but prevent one hand-picked recovery from
clearing a policy whose observed effects varied substantially by clip. This
matters when the main corpus was selected
specifically because it contains target names. A thresholded product-candidate
run requires at least 10 same-language controls containing at least 1,000
reference words in total, so a tiny clean sample cannot clear a policy whose
known risk is occasional over-firing. Audio files must also be distinct across
the target and control corpora both as supplied and after normalization to 16
kHz mono WAV. Exact copies are rejected even when renamed, rewrapped, or
losslessly converted, so repeated material cannot inflate the control-clip
count. Omit the directory only for a `--no-threshold` exploratory run.

An additional cross-language corpus can broaden the safety check, but cannot
satisfy the same-language requirement. Supply both its directory and explicit
language hint with `--cross-language-control-dir` and
`--cross-language-control-language`.

For a reproducible public negative control, fetch a disjoint LibriSpeech split
and pass it alongside the private target corpus. Public audiobook speech does
not replace same-language, push-to-talk controls:

```sh
./fetch-public-speech-fixtures.sh \
  --split test-clean --count 25 --start-index 100
./run-vocabulary-bias-regression.sh \
  --input-dir polish-benchmark \
  --negative-control-dir polish-negative-controls \
  --cross-language-control-dir public-audio/librispeech-test-clean \
  --cross-language-control-language en \
  --vocabulary vocabulary.txt \
  --critical-terms critical-terms.txt \
  --language pl \
  --references-hand-audited \
  --trials 3
```

Reports are ignored and privacy-redacted by default. The raw logs replace every
structured FluidAudio diagnostic payload with its level and category, rather
than relying on an upstream category list; dependency errors are also reduced
to a content-free marker. This keeps vocabulary terms, transcript fragments,
and private paths out of artifacts even when upstream adds a new warning path.
They include corpus and
worst WER, weighted exact critical-term recall, unexpected critical-term
insertions, critical-term precision, p50 inference latency, peak process
memory, model-cache footprint, preparation time, the Presspeech and FluidAudio
revisions, whether the benchmark source was clean, the benchmark executable's
SHA-256, a single content fingerprint covering every paired audio/reference
fixture, its target/same-language/cross-language assignment, the vocabulary and
critical-term files, and the macOS and Swift
versions. The input fingerprint depends on paired file contents rather than
private names, so a copied or renamed frozen corpus remains comparable while
any benchmark input, target/same-language/cross-language assignment, language
hint, trial-count change, or reference-audit declaration is visible. Keeping
the component hashes folded
into one report value also avoids exposing a separately guessable digest for a
short private vocabulary. Thresholded runs require a clean Git checkout so a
shared report is tied to exact reviewable source; use `--no-threshold` for
exploratory local modifications. A pairwise policy table compares direct-v3
lanes with production `v3` and sliding lanes with unbiased `sliding-v3`,
reporting net critical hits, unexpected insertions, corpus WER change, and
counts of clean wins, costly wins, and pure losses. A separate product-candidate
screen evaluates only the four direct-v3 policies and fails the command unless
the references are declared human-audited, the run uses at least three measured
trials per clip/variant, and at least one policy has complete
comparable clips, at least 25 target clips containing
at least 1,000 reference words and 50 critical-term occurrences, at least 10
same-language negative-control clips with distinct source and normalized audio
and at least 1,000 reference words, gains a
critical-term hit, adds no unexpected insertions or WER either in
aggregate or on any individual clip, loses no critical-term hits on an
individual clip, and keeps average p50 latency within 2x production. Optional
cross-language controls are included in every comparison but never satisfy the
same-language requirement. The per-clip checks prevent gains on some utterances
from masking vocabulary-caused regressions on others. For repeated-trial safety,
the gate compares each candidate's worst WER, lowest recall, and highest
insertion count with production's best WER, highest recall, and lowest insertion
count. A single bad production trial therefore cannot make a candidate look
non-regressing. Passing this strict screen
is necessary evidence for product evaluation, not approval to ship; use
`--no-threshold` for exploratory runs that should always publish their report.
Disabling threshold enforcement does not waive the screen's evidence rules:
an unaudited, single-trial, or locally modified run is reported as blocked
rather than being labelled a passing product candidate.

The exact-similarity lane is a precision experiment, not a claim that a match is
semantically safe. FluidAudio defines `similarity == 1.0` over its normalized
scorer input, which can ignore case or punctuation and can concatenate compound
words. The lane also requires an upstream-proven UTF-8 source range and fails
closed rather than guessing a mutation span. It still has to clear the same
target, negative-control, WER, insertion, recall, and latency screen as every
other direct-v3 policy.

When repeated trials yield different transcripts, each per-clip row is a
conservative envelope: worst WER, lowest critical-term recall, and highest
unexpected-insertion count observed. Pass
`--show-transcripts` or `--show-paths` only for local reports that are safe to
share. Thresholded product-candidate runs require at least three trials so this
envelope cannot be bypassed with a single observation; `--no-threshold` keeps
one-trial exploratory runs available.

Corpus WER is the total edit-error count divided by total reference words, so
longer clips contribute proportionally instead of each clip receiving equal
weight. Worst WER remains the most adverse individual trial output per clip.
Worst-trial selection and vocabulary-policy win/loss categories use exact edit
counts; the one-decimal WER shown in per-clip tables is display-only.

Critical terms deliberately require exact, word-aligned surface forms after
case/punctuation normalization. A canonical form elsewhere in the hypothesis
does not hide a missed reference occurrence: it is reported as an unexpected
insertion instead. Include every canonical vocabulary form. FluidAudio aliases
are alternate acoustic/string matches, but an accepted candidate is replaced
with its canonical term; aliases do not generate grammatical inflections. List
every inflected form that the benchmark expects to preserve. Entries that are
empty or duplicate after this normalization are rejected instead of being
silently double-counted.
After FluidAudio parses and sanitizes either vocabulary format, the resulting
canonical forms must exactly match the critical-term forms under the same
normalization (aliases are not canonical forms). The run rejects an unscored
vocabulary term, an unrelated critical term, or duplicate/empty normalized
canonical terms. This prevents the candidate screen from hiding a configured
term's false insertions or attributing unrelated transcript changes to boosting.

## Public speech regression

Private clips are the best product signal, but they cannot be shared or
reproduced by another maintainer. For a reproducible public check, fetch a
bounded LibriSpeech subset into ignored local fixtures:

```sh
./fetch-public-speech-fixtures.sh --source librispeech --split dev-clean --count 25
```

The fetcher downloads the OpenSLR archive, verifies the upstream MD5 checksum,
extracts the selected FLAC clips, converts them to 16 kHz Float32 WAV with
`afconvert`, and writes same-stem `.txt` references plus `manifest.tsv` under
`public-audio/librispeech-dev-clean/`.

Then run the production v3 regression with public-corpus reporting:

```sh
./run-real-dictation-regression.sh --input-dir public-audio/librispeech-dev-clean --out-dir public-results --backend v3 --public-corpus --show-transcripts --show-paths --trials 3
```

For candidate-model evaluation, run a v3-versus-candidate comparison with
public-corpus reporting. Unified remains the default candidate:

```sh
./run-public-model-comparison.sh --trials 3
./run-public-model-comparison.sh --candidate-backend v2 --trials 3
```

Or run a specific candidate backend against the public fixtures:

```sh
./run-real-dictation-regression.sh --input-dir public-audio/librispeech-dev-clean --out-dir public-results --backend apple --public-corpus --show-transcripts --show-paths --trials 3
./run-real-dictation-regression.sh --input-dir public-audio/librispeech-dev-clean --out-dir public-results --backend nemotron-en --public-corpus --show-transcripts --show-paths --trials 3
./run-real-dictation-regression.sh --input-dir public-audio/librispeech-dev-clean --out-dir public-results --backend nemotron-multilingual --nemotron-multilingual-language en-US --nemotron-multilingual-chunk-ms 2240 --public-corpus --show-transcripts --show-paths --trials 3
```

Or fetch and compare in one command:

```sh
./run-public-model-comparison.sh --fetch --count 50 --trials 3
```

Reports land under ignored `public-results/` and include source paths and
transcripts by default because the fixture corpus is public. LibriSpeech is
read English audiobook speech under CC BY 4.0, so treat it as a stable
reproducible benchmark, not as a replacement for local push-to-talk dictation
clips.

The ordinary LibriSpeech rows are mostly shorter than FluidAudio's 15-second
Parakeet encoder window. Build a second deterministic corpus that forces the
production path through several windows per clip:

```sh
python3 ./compose-public-long-form-fixtures.py \
  --input-dir public-audio/librispeech-dev-clean \
  --output-dir public-audio/librispeech-dev-clean-long-form \
  --target-seconds 45
./run-real-dictation-regression.sh \
  --input-dir public-audio/librispeech-dev-clean-long-form \
  --out-dir public-results/long-form \
  --backend v3 --public-corpus --show-transcripts --show-paths --trials 3
```

The composer joins the fetched WAV payloads and references in sorted order,
without resampling, generated speech, or duplicated source rows. Every output
is at least the target duration; a short remainder extends the last complete
composite instead of becoming a misleading sub-window fixture. Its manifest
records source boundaries and nominal 15-second boundary markers; FluidAudio's
overlap and actual window starts remain implementation details. This is a
repeatable seam/long-form regression, while the uncomposed rows remain the
better per-utterance diagnostic corpus.

## Parakeet encoder-precision regression

FluidAudio's original v3 `Encoder.mlmodelc` uses 6-bit LUT palettization even
though its historical API label is `int8`. Upstream isolated a deterministic,
high-confidence Ukrainian token corruption to that encoder and published a
larger linear-int8 `Encoder_v2.mlmodelc` candidate. The fix is opt-in because
upstream had not completed broad WER or Apple Neural Engine latency checks
([FluidAudio issue #760](https://github.com/FluidInference/FluidAudio/issues/760),
[implementation #872](https://github.com/FluidInference/FluidAudio/pull/872)).

Compare it with Presspeech's production encoder on exactly the same fixtures:

```sh
./run-real-model-comparison.sh \
  --input-dir public-audio/librispeech-dev-clean \
  --out-dir public-results \
  --candidate-backend v3-int8-v2 \
  --language en \
  --public-corpus --show-transcripts --show-paths \
  --trials 3
```

Parakeet v2 is deliberately benchmark-only. FluidAudio recommends it when only
English is needed because its tighter vocabulary improves English recall, but
Presspeech does not promote a model from upstream aggregate results alone. The
v2 comparison uses an explicit English hint for the production-v3 baseline and
the same audio for both models.

For a Unified, v2, or encoder product-candidate gate, add
`--require-candidate-pass`. The gate requires a clean checkout, at least 3
trials, 25 comparable clips and 1,000 reference words, at least one
demonstrated error reduction, no per-clip or aggregate word-error increase,
and average p50 latency no more than 1.25× production.
The public comparison wrapper accepts and forwards the gate, for example:

```sh
./run-public-model-comparison.sh \
  --candidate-backend v2 \
  --trials 3 \
  --require-candidate-pass
```

It compares the candidate's worst observed transcript with production's best
on every clip so unstable baseline output cannot hide a regression. Private
corpora additionally require `--references-hand-audited`.

Unified must use the product-candidate 250 ms trailing-silence setting and
also pass `run-tail-word-regression.sh`; the corpus gate does not replace that
short push-to-talk final-word check.
Unified and v2 are English-only, so their thresholded comparisons also require
`--language en` for the production-v3 baseline. The public comparison wrapper
adds that hint automatically.

A pass is only a per-corpus prerequisite. Before changing production, run at
least one general dictation corpus and a human-audited corpus in a language
that exercises the reported quantization failure (currently Ukrainian), and
inspect the additional model-cache and memory cost. A clean English corpus
that never changes cannot pass merely because latency is acceptable.

### 2026-07-22 candidate recheck

The first 25 LibriSpeech dev-clean clips, three trials per clip, on the
production FluidAudio pin. This recheck includes FluidAudio's repaired native
mel front-end for Nemotron English and adds Nemotron 3.5 multilingual at its
recommended 2240 ms tier with an `en-US` language prompt.

| Backend | Avg WER | Worst WER | Final-word failures | Avg p50 |
|---|---:|---:|---:|---:|
| `v3` | 1.73% | 14.0% | 1 | 85.0 ms |
| `nemotron-en` | 4.32% | 40.0% | 1 | 679.4 ms |
| `nemotron-multilingual` | 6.22% | 40.0% | 2 | 123.2 ms |

Neither repaired Nemotron English nor Nemotron 3.5 multilingual beats
Parakeet TDT v3 on this larger public corpus. Multilingual Nemotron is much
faster than the older English streaming path, but its WER and final-word
retention are worse than v3. Parakeet TDT v3 therefore remains the production
model; rerun these gates when FluidAudio or candidate weights change.

## Tail-word retention regression

The Unified candidate model has a specific failure mode worth tracking separately:
short push-to-talk clips can lose the final word when the recording stops
close to the last phoneme. The app no longer exposes Unified, but this
script remains useful when evaluating whether a future English model should
become user-facing.

```sh
./run-tail-word-regression.sh
```

The default run synthesizes two local TTS phrases, trims natural trailing
silence, cuts 100 ms, 150 ms, and 200 ms from the end, and compares v3
against Unified with 0 ms and 250 ms trailing silence. It writes ignored
Markdown and TSV reports under `tail-results/`. The candidate threshold
requires Unified at 250 ms to retain the final word on the known
regression cases and keep max WER at or below 20% before further evaluation.

To tune the number instead of only checking the current candidate value:

```sh
./run-tail-word-regression.sh --unified-trailing-ms-list 0,100,150,200,250,300,500
```

To test a post-release capture grace experiment, sweep grace separately
from synthetic model padding. A 100 ms grace means the generated fixture
puts 100 ms of the cut tail back into the recording before inference.

```sh
./run-tail-word-regression.sh --capture-grace-ms-list 0,50,100,150 --unified-trailing-ms-list 250
```

For a quick non-ASR check of parser and threshold logic:

```sh
./run-tail-word-regression.sh --self-test
```

## Release ASR checks

Before a release that changes ASR code, FluidAudio, audio
capture, or transcription post-processing, run the release wrapper:

```sh
./run-release-asr-checks.sh
```

It runs helper self-tests, production v3 private real-dictation regressions
if `real-audio/` contains local clips, and production v3 public speech
regressions if `public-audio/librispeech-dev-clean/` has been fetched. It also
runs production v3 over composed multi-window fixtures when
`public-audio/librispeech-dev-clean-long-form/` exists.
If you want the release check to fail when no local corpus is available:

```sh
./run-release-asr-checks.sh --require-real-audio
./run-release-asr-checks.sh --require-public-audio
./run-release-asr-checks.sh --require-long-public-audio
```

To also run Parakeet v2, linear-int8 v3, and Unified comparisons, the Unified
tail-word check, plus repaired Nemotron English and Nemotron 3.5 multilingual
regressions:

```sh
./run-release-asr-checks.sh --include-candidate-models
```

## Power measurement

Latency is already below the threshold where another few milliseconds are
likely to matter. To compare energy impact on the same Mac, run:

```sh
sudo -v
./bench-power.sh --file test-audio/medium-clean.wav --backend v3 --trials 20
```

This samples `cpu_power,gpu_power,ane_power` with `powermetrics` while
`presspeech-bench` runs, then writes a Markdown summary plus raw logs under
`power-results/`. Transcript text, fixture filenames, and local paths are
redacted by default; pass `--show-transcripts` or `--show-paths` only for
local reports you do not intend to share. `powermetrics` values are
estimates, so use them only for same-machine comparisons across backends,
dependency versions, or model changes.

For a quick no-sudo check of argument parsing, path redaction, and report
generation:

```sh
./bench-power.sh --self-test
```

## Results

Mac mini M4, 10 cores, 16 GB, macOS 26.4.1, Xcode/Swift 6.3.
5 trials per backend per clip, p50 reported below. First inference
(after model load) excluded from each row's p50 — that's the warmup.

| Clip | Duration | `fluid` (ANE) | `presspeech-mlx` (GPU) | Speed ratio |
|---|---:|---:|---:|---:|
| `short-clean` | 2.50 s | **92.4 ms** | 145.4 ms | 1.57× |
| `medium-clean` | 3.99 s | **96.1 ms** | 176.3 ms | 1.83× |
| `disfluent` | 5.31 s | **94.1 ms** | 185.9 ms | 1.97× |
| `longer-technical` | 9.49 s | **152.4 ms** | 300.9 ms | 1.97× |

**Key findings:**

- **`fluid` (ANE) is consistently ~1.5–2× faster than `presspeech-mlx`
  (GPU)** — the gap widens with clip length, as expected for an
  encoder-bound workload.
- **Both backends produce essentially identical transcripts.** They
  even agreed on the TTS-induced quirks ("push-to-tock" instead of
  "push-to-talk", "Max" instead of "Macs") — neither backend has an
  accuracy advantage on this material. Real human dictation would
  likely be slightly more forgiving to both.
- **Both backends are well below the human-perception threshold for
  "instant".** 90 ms vs 180 ms for a typical 3-second clip is a
  measurable improvement but not a felt one — both finish before the
  user has finished releasing the dictation key.
- **The likely real win for `fluid` is power, not latency.** ANE
  draws materially less power than GPU. Use `bench-power.sh` for
  same-machine power comparisons when evaluating backend or dependency
  changes.

**Transcript samples (best of 5 per backend):**

| Clip | Reference (TTS input) | Both backends |
|---|---|---|
| `short-clean` | "The quick brown fox jumps over the lazy dog." | ✓ exact |
| `medium-clean` | "Presspeech is a lightweight push-to-talk dictation app for Apple Silicon Macs." | "push-to-tock", "Max" (TTS artifacts) |
| `disfluent` | "So, um, I was going to send, like, maybe a quick note about the thing we discussed earlier, you know." | ✓ exact, fillers preserved |
| `longer-technical` | "When you press the dictation key, the audio buffer is captured at sixteen kilohertz, run through Parakeet's encoder on the neural engine, and the resulting tokens are pasted at the cursor location." | "16 kHz" (correctly normalised), `fluid` lowercases "parakeet", `presspeech-mlx` capitalises |

### Re-run post-v0.14.5 FluidAudio pin + Apple SpeechAnalyzer unblock

Mac mini M4, 10 cores, 16 GB, macOS 26.4.1, Swift 6.3. 5 trials per
backend per clip, p50 reported below. Apple backend now runs
end-to-end after embedding a minimal `Info.plist` into the executable
and using the fresh-analyzer-per-call pattern (see "Apple backend
notes" below).

| Clip | Duration | `fluid` (ANE) | `apple-SpeechAnalyzer` | Apple/Fluid |
|---|---:|---:|---:|---:|
| `short-clean` | 2.50 s | **66.7 ms** | 173.8 ms | 2.6x slower |
| `medium-clean` | 4.29 s | **84.0 ms** | 368.3 ms | 4.4x slower |
| `disfluent` | 6.53 s | **80.6 ms** | 240.2 ms | 3.0x slower |
| `longer-technical` | 10.94 s | **119.8 ms** | 408.5 ms | 3.4x slower |

**Key findings (`fluid` vs `apple`):**

- **`fluid` is 2.6-4.4x faster than `apple` on this Mac**, across every
  clip. The gap widens with clip length.
- **`apple` drops punctuation** ("So I was going to send like maybe a
  quick note about the thing we discussed earlier you know") whereas
  `fluid` produces commas and periods.
- **Both have minor word-segmentation errors** on "Presspeech"
  ("push-to-tock" for `fluid`, "Para key" for `apple`) and
  "Macs"->"Max" (TTS artifact, both backends).
- **Apple's first-time model is also downloaded**, not preinstalled
  for every user — the "no download" pitch for SpeechAnalyzer applies
  only on machines where the en-US dictation locale was already
  fetched for system Dictation. The download is smaller than
  FluidAudio's 600 MB but non-zero.

**`apple-SpeechAnalyzer` transcripts:**

| Clip | `apple` transcript |
|---|---|
| `short-clean` | "The quick brown fox jumps over the lazy dog" (no trailing period) |
| `medium-clean` | "Para key is a lightweight push to talk dictation app for Apple Silicon Max" ("Para key", no punctuation) |
| `disfluent` | "So I was going to send like maybe a quick note about the thing we discussed earlier you know" (fillers preserved, no punctuation) |
| `longer-technical` | "When you press the dictation key the audio buffer is captured at 16 kHz run through parakeets encoder on the neural engine and the resulting tokens are pasted at the cursor location" (no punctuation) |

## Apple backend notes

Once-blocking gaps that have been resolved:

1. **Info.plist** — Embedded into the executable via a linker
   `-sectcreate __TEXT __info_plist` flag (see `Package.swift`).
   `NSSpeechRecognitionUsageDescription` and `CFBundleIdentifier` are
   what Speech.framework checks; without them
   `DictationTranscriber.prepare` traps with exit 133 / SIGTRAP.
2. **Audio format** — `DictationTranscriber` rejects Float32 with
   "Audio sample data must be 16-bit signed integers". The bench now
   converts the load-time float buffer to Int16 in `makePCMBuffer`.
3. **Analyzer lifecycle** — `analyzer.finalizeAndFinishThroughEndOfInput()`
   puts the analyzer into a terminal state; subsequent transcribe calls
   on the same instance produce empty output instantly. The bench now
   recreates analyzer + transcriber per call (matches Presspeech's
   push-to-talk pattern: one utterance, one session).
4. **Results draining** — Reading `transcriber.results` sequentially
   after finalize loses events. The bench drains results in a child
   task started *before* `analyzer.start(...)`, mirroring
   FluidInference's `swift-scribe` reference app.
5. **isFinal semantics** — For a single-shot push-to-talk utterance,
   `DictationTranscriber` emits the entire transcript as a single
   `isFinal=false` (volatile) event. The "final" text the user sees is
   therefore `finalized + last-volatile`, not just `finalized`.

### TTS audio is not real dictation

`say` produces clean, expressionless audio with no breathing, no
overlapping noise, no real prosody. Latency results are realistic;
accuracy numbers measure how the engines handle synthetic speech,
which both engines are slightly worse at than the real thing they
were trained on. To get real accuracy numbers, drop your own
recordings into `test-audio/` — the bench loads any 16 kHz mono WAV
by filename.

### Power measurement

The core bench measures compute latency. Use `bench-power.sh` when the
question is battery impact or ANE/GPU power draw. `powermetrics` is noisy,
so compare runs on the same Mac, OS build, power source, and thermal state.

## What this benchmark drove

The original questions and where they landed:

- **"Is ANE meaningfully faster than MLX for our workload?"** —
  Yes, consistently 1.5–2× depending on clip length.

- **"Would users perceive the difference?"** — At the bench's
  granularity, no; both backends finish in well under 200 ms for a
  3-second clip. But the ANE path **does** finish before the user
  has released the dictation key on typical clips, which makes
  end-to-end "press → text" feel measurably snappier in real use.

- **"Is it worth porting Presspeech to Swift to capture the win?"** —
  Yes. Beyond the latency win, going native removed the embedded
  Python interpreter (149 MB → 2.2 MB zip), shrank the hardened-
  runtime entitlement set from six keys to two, and sidestepped the
  whole class of TCC/codesigning bugs that plagued the
  PyInstaller-bundled `.app`. The port shipped as Presspeech 0.2.0.

## Future use

The bench is kept as the "is the inference path still healthy?"
sanity check. Re-run it whenever:

- FluidAudio publishes a new release and you want to confirm the
  latency curve hasn't regressed.
- Apple ships a new SpeechAnalyzer revision (or someone unblocks
  the entitlements gap so the `apple` backend runs end-to-end).
- A future Parakeet / MLX / WhisperKit model arrives and you want
  to evaluate it against the current numbers.
- A latency-equivalent change might still affect battery life; use
  `bench-power.sh` to compare power on the same machine.
