# Public Speech Fixtures

Generated public benchmark clips live here. The directory is ignored by git
except for this README because the audio comes from third-party datasets and
can be hundreds of megabytes.

Fetch a small LibriSpeech fixture set:

```sh
./fetch-public-speech-fixtures.sh --source librispeech --split dev-clean --count 25
```

For a reproducible multilingual encoder check, fetch the pinned FLEURS
Ukrainian test rows that exercise the language family where the upstream
linear-int8 encoder fix was first demonstrated:

This candidate backend requires the dedicated FluidAudio revision and compile
condition documented in the parent benchmark README; it is unavailable in the
normal production-pinned build.

```sh
./fetch-public-speech-fixtures.sh \
  --source fleurs --language uk_ua --split test \
  --count 60
./run-real-model-comparison.sh \
  --input-dir public-audio/fleurs-uk_ua-test \
  --out-dir public-results/fleurs-uk_ua-test \
  --candidate-backend v3-int8-v2 --language uk \
  --public-corpus --show-transcripts --show-paths \
  --trials 3
```

The FLEURS importer accepts all 25 Parakeet TDT v3 language locales and the
Bosnian, Belarusian, and Serbian script-filter aliases exposed by Presspeech.
It pins the dataset revision, reads the human reference TSV, and verifies the
language/split archive against the SHA-256 and size in the pinned Git LFS
pointer before extracting the selected rows. FLEURS is CC BY 4.0.

Then run the production v3 regression:

```sh
./run-real-dictation-regression.sh --input-dir public-audio/librispeech-dev-clean --out-dir public-results --backend v3 --public-corpus --show-transcripts --show-paths --trials 3
```

For candidate-model evaluation, compare production v3 with the English
Unified model:

```sh
./run-public-model-comparison.sh --trials 3
```

Run the current Nemotron candidates through the same fixtures:

```sh
./run-real-dictation-regression.sh --input-dir public-audio/librispeech-dev-clean --out-dir public-results --backend nemotron-en --public-corpus --show-transcripts --show-paths --trials 3
./run-real-dictation-regression.sh --input-dir public-audio/librispeech-dev-clean --out-dir public-results --backend nemotron-multilingual --nemotron-multilingual-language en-US --nemotron-multilingual-chunk-ms 2240 --public-corpus --show-transcripts --show-paths --trials 3
```

Or fetch and compare in one command:

```sh
./run-public-model-comparison.sh --fetch --count 50 --trials 3
```

The fetcher imports LibriSpeech from OpenSLR and supported-language FLEURS
subsets from a pinned Google dataset revision. Both are read speech aligned to
human references and distributed under CC BY 4.0. They are useful reproducible
benchmarks, but do not replace private push-to-talk dictation clips. Keep both:

- public fixtures for reproducible production v3 WER checks and candidate model comparisons
- FLEURS fixtures for product-language and cross-script checks
- private real-dictation fixtures for Presspeech's actual short, messy workflow

Generated fixture sets contain:

```text
public-audio/librispeech-dev-clean/
  librispeech-dev-clean-0001-84-121123-0000.wav
  librispeech-dev-clean-0001-84-121123-0000.txt
  manifest.tsv
  README.txt
```

The generated `manifest.tsv` records the source corpus, split, original
LibriSpeech ID, original archive member, license, and reference transcript
for each imported clip.

## Multi-window fixtures

The imported utterances are useful for WER but are mostly short. Compose them
into deterministic 45-second-or-longer fixtures to exercise Parakeet's
15-second CoreML windows and chunk merging:

```sh
python3 ./compose-public-long-form-fixtures.py \
  --input-dir public-audio/librispeech-dev-clean \
  --output-dir public-audio/librispeech-dev-clean-long-form \
  --target-seconds 45
```

The generated long-form directory is also ignored by git. Its manifest records
the source rows, their boundaries, and nominal 15-second boundary markers;
FluidAudio's overlap and actual window starts remain implementation details.
Run it through `run-real-dictation-regression.sh`, or let
`run-release-asr-checks.sh` validate and run it automatically. The release
wrapper requires this seam coverage by default; only explicitly lightweight
helper runs should pass `--allow-missing-long-public-audio`.

The release wrapper also requires a German multilingual seam probe. Fetch the
pinned German FLEURS test rows and compose them into long-form clips:

```sh
./fetch-public-speech-fixtures.sh \
  --source fleurs --language de_de --split test --count 50
python3 ./compose-public-long-form-fixtures.py \
  --input-dir public-audio/fleurs-de_de-test \
  --output-dir public-audio/fleurs-de_de-test-long-form \
  --target-seconds 45
```

The release wrapper validates the inherited German test manifest and runs the
production v3 backend with `--language de`, applying the long-form WER and
deletion-run screens. To omit this corpus for a lightweight, non-release check,
pass `--allow-missing-multilingual-long-public-audio` explicitly.

## Spanish language-behavior probe

The upstream report of errors on spontaneous Latin-American Spanish motivates
checking a second language, but the report is not a Presspeech result and its
corrected explanation is model-level behavior rather than a CoreML-specific
bug. The release gate does not currently require a Spanish-specific corpus.
For a reproducible read-speech probe on the current production pin, fetch a bounded
FLEURS test set and run the same frozen audio once with automatic selection
and once with the Spanish hint:

```sh
./fetch-public-speech-fixtures.sh \
  --source fleurs --language es_419 --split test --count 60

./run-real-dictation-regression.sh \
  --input-dir public-audio/fleurs-es_419-test \
  --out-dir public-results/fleurs-es_419-test-auto \
  --backend v3 --language auto --public-corpus --trials 3
./run-real-dictation-regression.sh \
  --input-dir public-audio/fleurs-es_419-test \
  --out-dir public-results/fleurs-es_419-test-spanish-hint \
  --backend v3 --language es --public-corpus --trials 3
```

Compare the input digest, aggregate and worst WER, final-word failures, and
latency in the two reports. In Presspeech, `--language es` is a decoder
script-filter hint, not language forcing; it is not evidence that v3 can
reliably identify or transcribe Spanish. FLEURS is read speech and does not
exercise the conversational speech described in the upstream report. A
separate local-only test with naturally spoken Spanish clips is needed for
that question. Keep those recordings, references, and raw logs private; do
not commit or share them. The redacted summary is evaluation evidence only,
not a product gate or an approval to change the model.

## Context-variation fixtures

For an encoder-precision comparison, generate probe/context/combined triplets
that keep identical speech below one 15-second encoder window while changing
its trailing speech context:

```sh
python3 ./compose-public-context-fixtures.py \
  --input-dir public-audio/fleurs-uk_ua-test \
  --output-dir public-audio/fleurs-uk_ua-test-context \
  --pair-count 10
```

Run `v3` versus `v3-int8-v2` over that directory with
`run-real-model-comparison.sh`, then pass its printed TSV path and the generated
manifest to `analyze-context-variation.py`. The comparison helper validates the
generated manifest and fixture digests before running. Each source utterance is
assigned to only one pair, but its audio intentionally appears both alone and
in the combined clip. This makes context-induced error counts measurable; it
also means the generated triplets cannot satisfy the independent-corpus
candidate gate.
