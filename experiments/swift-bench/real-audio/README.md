# Private Real-Dictation Fixtures

Put local-only dictation clips here when validating accuracy against
real speech. This directory is ignored by git except for this README.

Use one audio file plus one reference transcript sidecar per clip:

```text
real-audio/
  short-note.wav
  short-note.txt
  noisy-room.m4a
  noisy-room.txt
```

To add an existing local recording safely:

```sh
./add-real-dictation-fixture.sh \
  --id short-note-001 \
  --audio ~/Desktop/short-note.m4a \
  --reference-file ~/Desktop/short-note.txt
```

Then run the model-decision comparison:

```sh
./run-real-model-comparison.sh --trials 3
```

For single-backend debugging:

```sh
./run-real-dictation-regression.sh --backend v3 --trials 5
./run-real-dictation-regression.sh --backend unified --trials 5 --unified-trailing-silence-ms 250
./run-real-dictation-regression.sh --backend nemotron-en --trials 5
./run-real-dictation-regression.sh --backend nemotron-multilingual --nemotron-multilingual-language en-US --nemotron-multilingual-chunk-ms 2240 --trials 5
```

For the default release ASR check, keep at least 25 speech clips with 1,000
reference words **plus five non-speech controls** here. Each control needs a
distinct recording and an exactly zero-byte `.txt` sidecar; whitespace is not a
non-speech marker. Include realistic room/device or handling noise, not just
digital silence. Listen to every control in full to confirm it contains no
intelligible speech before running:

```sh
./run-release-asr-checks.sh --non-speech-controls-hand-audited
```

The release check fails if any of the three default measured v3 trials on a
control produces deliverable text. To inspect the same behavior directly:

```sh
./run-real-dictation-regression.sh --backend v3 --trials 3 --max-non-speech-emissions 0
```

That numeric check does not itself establish that the controls were correctly
hand-audited.

Reports are written to `real-results/`, which is also ignored by git.
By default the report redacts reference text, hypothesis text, fixture
filenames, and local paths while still showing latency, memory, and WER.

For a useful model-decision set, aim for 20-50 short clips across these
categories:

- short commands and one-sentence notes
- 30-90 second paragraphs
- final-word stress cases where the last word matters
- quiet speech, fast speech, and natural pauses
- filler-heavy speech if filler removal is enabled in product testing
- punctuation-heavy notes
- mild background noise
- at least a few non-English or mixed-language clips if multilingual
  behavior is in scope

Use `manifest.template.tsv` as a local planning sheet. Copy it to
`manifest.tsv` if you want to track your private set; that copy is ignored
by git.
