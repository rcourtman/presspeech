# Windows speech benchmark fixtures

`../benchmark.py` measures model load/warm-up time, repeated inference latency,
synchronized Parakeet prepare/transfer/generate/decode stages, consensus and
per-trial WER/CER, longest consecutive reference-word deletion, final-word
retention, silence false positives, and Whisper VAD speech retention.
Audio, reviewed references, manifests, and JSON results stay ignored because
they can contain private dictation.

Start a local manifest from the tracked structure:

```bat
cd windows
copy benchmarks\manifest.example.json benchmarks\manifest.json
.venv\Scripts\python benchmark.py --manifest benchmarks\manifest.json --output benchmarks\result.json
```

Put 16 kHz mono WAV files under `benchmarks\audio\`, then add one manifest row
per clip. Other sample rates and channel layouts are accepted and converted,
but canonical fixtures make runs easier to compare.

- Listen to each speech clip and correct its reference before setting
  `"reference_reviewed": true`. WER, CER, and final-word retention are not
  reported for unreviewed references.
- For a human-reviewed non-speech clip, set `"expected_silence": true` and
  `"reference_reviewed": true`, with no reference text. Every non-empty trial
  then counts as a silence false positive.
- Include short commands, quiet speech, fast speech, natural pauses, meaningful
  final words, and representative microphone/background conditions.
- Keep the same clips, references, run count, model precision, and hardware when
  comparing a decoding or VAD change. Reports record the effective Whisper VAD
  policy and per-trial VAD-retained duration. Read the trial WER and longest
  deletion span as well as consensus WER: a modal transcript can hide an
  intermittent dropped phrase or sentence.
- Compare Parakeet optimizations using both total inference latency and the
  synchronized per-stage medians. Stage barriers are benchmark-only and are
  deliberately disabled during interactive dictation.
- Do not commit audio, reference text, manifests, hypotheses, or result files.

The manifest's `runs` value is the number of measured trials after model
warm-up. Use at least three for exploratory comparisons and more when checking
an intermittent boundary failure.
