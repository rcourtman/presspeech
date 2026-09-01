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

The FLEURS importer accepts only locales corresponding to languages exposed by
Presspeech. It pins the dataset revision, reads the human reference TSV, and
verifies the language/split archive against the SHA-256 and size in the pinned
Git LFS pointer before extracting the selected rows. FLEURS is CC BY 4.0.

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
`run-release-asr-checks.sh` detect it automatically. Use
`--require-long-public-audio` when absence of this seam coverage should fail a
release check.
