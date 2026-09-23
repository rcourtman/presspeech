# Windows speech benchmark fixtures

`../benchmark.py` measures model load/warm-up time, repeated inference latency,
synchronized Parakeet prepare/transfer/generate/decode stages, WER,
lowercase-normalized CER, case-sensitive CER, final-word retention, silence false positives, and Whisper VAD speech retention.
Version 3 reports identify the loader's pinned model repository/revision,
retain historical consensus WER alongside all-trial WER and a per-clip
best/worst error envelope, record the bounded Parakeet window count and longest
model input for each clip, and add the requested language policy plus detected
language counts for Whisper. Source metadata records what the loader requests;
it does not independently attest the local model files.
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
- Lowercase-normalized CER remains comparable with prior reports;
  case-sensitive CER additionally counts capitalization differences while
  applying the same whitespace normalization. Inspect per-sample `accuracy.case_sensitive_cer`
  and per-trial values alongside WER/CER because capitalization often does not
  alter word error rate.
- For a human-reviewed non-speech clip, set `"expected_silence": true` and
  `"reference_reviewed": true`, with no reference text. Every non-empty trial
  then counts as a silence false positive.
- Include short commands, quiet speech, fast speech, natural pauses, meaningful
  final words, and representative microphone/background conditions. A Parakeet
  release corpus should also include human-reviewed speech longer than 60
  seconds, with words spoken continuously across several likely window seams;
  inspect both WER and the reported window plan for duplication or loss.
- Keep spontaneous dictation distinct from read-speech benchmark clips when
  comparing models. Include unscripted sentences with ordinary disfluencies,
  self-corrections, contractions, names, and dictated numbers/punctuation;
  report results by these recording/task groups as well as in aggregate. Read
  speech remains useful for reproducibility, but alone does not establish
  performance on push-to-talk dictation. Use privately recorded, consented
  clips for this stratum and keep audio and references out of version control.
- For model or decoding changes, use the same reviewed clips and repeat count
  across conditions. Compare error rates and intermittent failures within each
  speech/task group, plus latency; a corpus-wide WER improvement must not hide
  a regression on short commands, noisy/quiet input, final words, or silence.
- Keep the same clips, references, run count, model precision, and hardware when
  comparing a decoding or VAD change. Reports record the effective Whisper VAD
  policy and per-trial VAD-retained duration. `speech_detection.trials` is the
  expected run count; compare it with `measured_trials` and `missing_trials`.
  A missing duration is not evidence that VAD retained speech: investigate
  incomplete timing before treating a run as a pass.
- The manifest defaults to the historical `"language": "en"` policy. Use
  `"language": "auto"` (or `--language auto`) to exercise multilingual
  Whisper's per-dictation detection, matching Presspeech's Whisper turbo path.
  Reports preserve the requested policy and count each speech-bearing trial's
  returned language code; compare accuracy and latency because detection itself
  is measured work. Codes inferred from VAD-rejected silence are discarded.
- Inspect `aggregate_worst_trial_wer` as well as consensus `aggregate_wer` when
  screening regressions: it sums each clip's worst observed word-error count,
  exposing intermittent internal substitutions that a modal transcript can hide.
  Best/worst envelopes can combine different repetitions across clips; they are
  neither an observed whole-corpus run nor confidence intervals. Their extremes
  also depend on run count, so compare candidates with the same number of runs.
- `aggregate_trial_wer` divides every reviewed trial's word errors by the total
  repeated reference-word count. It weights words, not clips. The report retains
  those numerator/denominator counts and each clip's ordered per-trial errors.
  Reviewed references without scoreable words are excluded from WER; reviewed
  silence uses the separate false-positive counters.
- Compare Parakeet optimizations using both total inference latency and the
  synchronized per-stage medians. Stage barriers are benchmark-only and are
  deliberately disabled during interactive dictation. For clips longer than
  60 seconds, confirm every trial reports more than one window and a longest
  input no greater than 60 seconds.
- Do not commit audio, reference text, manifests, hypotheses, or result files.

The manifest's `runs` value must be a positive JSON integer and is the number
of measured trials after model warm-up. `--runs` overrides it; zero is rejected. Use at least three for exploratory comparisons and more when checking
an intermittent boundary failure.
