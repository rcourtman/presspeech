# Windows speech benchmark fixtures

`../benchmark.py` measures model load/warm-up time, repeated inference latency,
synchronized Parakeet prepare/transfer/generate/decode stages, WER,
lowercase-normalized CER, first- and final-word retention, silence false positives, and Whisper VAD speech retention.
Reports identify the loader's pinned model repository/revision,
retain historical consensus WER alongside all-trial WER and a per-clip
best/worst error envelope, record the bounded Parakeet window count and longest
model input for each clip, and add the requested language policy plus detected
language counts for Whisper; they also identify whether the effective Whisper
VAD policy is the product default or a benchmark-only pause-threshold override.
Version 9 and later normalize canonically equivalent Unicode text to NFC before
WER, CER, exact-match, and boundary-word scoring, and keeps remaining combining
marks attached to WER tokens. Raw transcripts and references are not rewritten.
Re-score older reports before comparing multilingual metrics when reference or
hypothesis text may use decomposed accents; version 8 scores can differ without
any model change. NFC preserves compatibility distinctions, unlike NFKC
([Unicode normalization](https://www.unicode.org/reports/tr15/)).
Optional `task_group` labels add separately
weighted consensus/all-trial WER, inference latency, and silence false-positive
counts per stratum. Optional `language_group` labels add an independent set of
language-stratified accuracy, worst-trial envelope, boundary-retention, silence,
and latency metrics. Version 11 also includes reviewed-speech Whisper VAD
rejections, measured/missing trial counts, and min/median/max per-trial
retained-audio ratios in the corpus and each of these strata. A retained-audio
ratio measures how much audio passed VAD, not how much speech it retained;
natural pauses can lower it. Non-Whisper models have no Whisper VAD coverage.
When both labels are present, `language_task_groups` also
reports the same metrics for each language/task intersection, so pooled
language or task results cannot hide a regression in a paired stratum. These
are human-assigned clip labels, distinct from the top-level `language` decoder
hint and Whisper's detected-language counts; clips missing either label are
omitted from the intersection report but remain in corpus and singly labelled
groups. Source metadata records what the loader requests; it does not
independently attest the local model files.
Version 10 adds `benchmark_inputs_sha256` to the JSON report and console summary.
It hashes the multiset of mono, resampled float32 samples **actually sent to
ASR**, original clip durations and sample rates, exact reference text, review
and silence flags, and task/language labels. It does not include paths, sample
IDs, manifest order, model, decoder language, precision, or run count. Compare
the digest *and* those separate report settings before attributing a WER or
latency difference to a model or VAD policy. A changed digest means the input
comparison is not paired; a matching digest does not establish reference
quality, speaker diversity, hardware equality, or a native release pass. The
report exposes only one aggregate digest, not individual audio hashes.
Versions 9 and earlier have no input digest and cannot retrospectively prove
that two runs used identical inputs.

Audio, reviewed references, manifests, and JSON results stay ignored because
they can contain private dictation.

Before downloading or loading a model, the runner now requires at least one
fixture with a unique non-empty ID and readable, non-empty, finite audio. It
decodes and resamples each clip once for preflight, retaining only the effective
audio digest, duration, and source rate—not a second corpus of audio in memory.
It reads each clip again just before inference and aborts if those values have
changed during model setup. Preflight is not a human reference review and does
not establish that the corpus is representative; it also adds an untimed read
and resample pass to benchmark startup, not to reported inference latency.

For a local Whisper pause-policy experiment, compare the release's 160 ms
minimum silence split with explicit alternatives without editing product code:

```bat
.venv\Scripts\python benchmark.py --manifest benchmarks\manifest.json --model base.en --runs 5 --output benchmarks\whisper-160ms.json
.venv\Scripts\python benchmark.py --manifest benchmarks\manifest.json --model base.en --runs 5 --whisper-vad-min-silence-ms 2000 --output benchmarks\whisper-2000ms.json
```

`--whisper-vad-min-silence-ms` changes only the benchmark's Whisper policy;
the report records the full effective policy and labels it as a benchmark-only
override. Omitting it records the unchanged Presspeech product default. The
option is rejected for non-Whisper models. The 160 ms and 2,000 ms cases match
the distinct defaults in the pinned faster-whisper 1.2.1 transcription path
and `VadOptions` ([transcribe.py](https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/faster_whisper/transcribe.py),
[vad.py](https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/faster_whisper/vad.py));
neither default is evidence that one is better for dictation. Use identical
reviewed audio, references, model, language, hardware, and run count; include
natural pauses, quiet speech, short commands, and silence controls. Compare WER,
per-trial errors, VAD-retained duration, rejected-speech trials, boundary-word
retention, silence false positives, and latency by task group before proposing
any product-policy change.

## Candidate watch: multilingual CPU recognition

Windows' first-run CPU fallback is English-only Whisper `base.en`; the
multilingual Parakeet path currently requires a much larger download. This is
an explicit language/availability tradeoff, not evidence that `base.en` is a
multilingual fallback. NVIDIA's Parakeet model card lists 25 European
languages and shows uneven FLEURS WER across them
([model card](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)). A
September 2026 Orukeet paper reports
9.85% pooled WER versus 11.01% for Parakeet on 20,146 FLEURS recordings, with
lower WER on 23 of 25 languages ([paper](https://arxiv.org/abs/2609.10054),
[model/runtime details](https://github.com/Oruk-AI/orukeet)). These are
author-reported public-benchmark results, not Presspeech or Windows-dictation
evidence. The paper also discloses final adaptation and checkpoint selection
on LibriSpeech test-other, so that split is not an independent held-out check.

Treat Orukeet as an evaluation candidate only. Its project advertises CPU and
CUDA runtimes, but does not establish compatibility with Presspeech's Windows
packaging; its weights are CC BY-SA 4.0, unlike NVIDIA Parakeet's CC BY 4.0.
The upstream v0.1.1 Python package requires Python 3.12+, which matches
Presspeech's pinned CPython 3.12.10 version; the installer says it selects CPU,
CUDA, or Metal runtimes, verifies model/SDK hashes, and supports offline
inference. Orukeet also documents an ONNX INT8 integration through OpenWhispr's
sherpa-onnx loader, including a Windows installation path; OpenWhispr advertises
Windows builds. That establishes a separate desktop integration route, not
compatibility with Presspeech's hash-locked dependencies and PyInstaller
package or evidence of Windows CPU latency. The upstream timed result is a
separate M4 Pro/Metal patch benchmark; the project says the full timed benchmark
has not been rerun for its packaged v0.1.1 SDK. Its ONNX INT8 archive is listed
as 486,807,585 bytes before extraction and 671,619,800 bytes after extraction.
These are upstream implementation facts, not measured Presspeech setup,
memory, or latency. Check the current
[Orukeet project](https://github.com/Oruk-AI/orukeet) for artifact and license
changes before testing or distributing it, and review its
[OpenWhispr integration notes](https://github.com/Oruk-AI/orukeet/blob/main/integrations/openwhispr/README.md)
and [OpenWhispr platform documentation](https://github.com/OpenWhispr/openwhispr)
as a separate implementation reference.

Before considering an app integration:

- Verify Windows CPU runtime, CUDA behavior where supported, offline inference,
  pinned model/runtime artifacts, and the weight-license obligations.
- Compare the same reviewed multilingual public clips and locally held,
  consented spontaneous dictations on the same Windows machine. Keep public
  read-speech and spontaneous dictation as separate task groups; include short,
  quiet, noisy, disfluent, and boundary-sensitive speech plus silence controls.
- Report per-language/task-group WER and worst-trial errors alongside first-
  and final-word retention, silence false positives, first-load/preparation,
  memory/cache footprint, and cold/warm inference latency. Compare CPU results
  with the current CPU path on English clips; do not score English-only
  `base.en` as a multilingual baseline.

Do not change the first-run model or expose a new selectable model based only
on the public WER figures. Keep audio and references local as described below.

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
  `"reference_reviewed": true`. WER, CER, and first-/final-word retention are
  not reported for unreviewed references. A reviewed speech row must contain
  non-empty reference text.
- Lowercase-normalized CER remains comparable with prior reports;
  case-sensitive CER additionally counts capitalization differences while
  applying the same whitespace normalization. Inspect per-sample `accuracy.case_sensitive_cer`
  and per-trial values alongside WER/CER because capitalization often does not
  alter word error rate.
- For a human-reviewed non-speech clip, set `"expected_silence": true` and
  `"reference_reviewed": true`, with no reference text. Every non-empty trial
  then counts as a silence false positive. Both flags must be JSON booleans
  (`true` or `false`), not quoted strings or numbers; contradictory rows are
  rejected before the model loads.
- Include short commands, quiet speech, fast speech, natural pauses, meaningful
  final words, and representative microphone/background conditions. A Parakeet
  release corpus should also include human-reviewed speech longer than 60
  seconds, with words spoken continuously across several likely window seams;
  inspect both WER and the reported window plan for duplication or loss.
- Keep spontaneous dictation distinct from read-speech benchmark clips when
  comparing models: ASR error rates vary materially across speaking styles and
  spontaneity levels ([Szymański et al., 2020](https://aclanthology.org/2020.findings-emnlp.295/),
  [Evain et al., 2024](https://aclanthology.org/2024.lrec-main.1491/)). Include
  unscripted sentences with ordinary disfluencies,
  self-corrections, contractions, names, and dictated numbers/punctuation;
  report results by these recording/task groups as well as in aggregate. Read
  speech remains useful for reproducibility, but alone does not establish
  performance on push-to-talk dictation. Use privately recorded, consented
  clips for this stratum and keep audio and references out of version control.
  Add a short, non-identifying `task_group` to each manifest row (for example
  `spontaneous-dictation`, `read-speech`, `short-command`, or `quiet-speech`);
  the JSON report and console summary then separate reviewed WER, repeated-trial
  WER, latency, and reviewed silence false positives for each label. A sample
  has one group, so choose a consistent primary stratum; unlabelled samples
  remain in corpus totals but not group totals.
- For multilingual comparisons, also set `language_group` on each speech clip
  using the same human-assigned language/locale labels across candidates (for
  example `pl`, `en-GB`, or `pt-BR`). It is evaluation metadata only: the
  top-level `language` field still controls the recognizer hint. Reports
  summarize language and task strata independently and their intersections
  when both labels are present. Preserve both labels to avoid trading away
  dictation-style breakdowns for language coverage. Review
  both consensus/all-trial and worst-trial WER per language; the worst-trial
  value combines each clip's worst repetition and is not an observed corpus
  run or a confidence interval.
- For model or decoding changes, use the same reviewed clips and repeat count
  across conditions. Compare error rates and intermittent failures within each
  speech/task group, plus latency; a corpus-wide WER improvement must not hide
  a regression on short commands, noisy/quiet input, final words, or silence.
- Keep the same clips, references, run count, model precision, and hardware when
  comparing a decoding or VAD change. Reports record the effective Whisper VAD
  policy and per-trial VAD-retained duration. `speech_detection.trials` is the
  expected run count; compare it with `measured_trials` and `missing_trials`.
  A missing duration is not evidence that VAD retained speech: investigate
  incomplete timing before treating a run as a pass. Check the corresponding
  reviewed-speech VAD counts and retained-audio ratios in task, language, and
  language/task groups; a pooled duration can hide a short-command or quiet-
  speech failure. Compare those ratios only for paired clips with the same
  run count, because pause lengths affect them independently of recognition.
  `reviewed_speech_vad_complete` is `null` without reviewed Whisper VAD data,
  false if any expected duration is missing, and true only with full coverage.
  Rejections are observed counts, not clean passes for missing trials.
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
- Inspect first- and final-word retention alongside WER. Each reviewed trial
  must begin with the reference's first word and end with its final word;
  per-sample and corpus/task-group failure counts expose intermittent boundary
  differences that aggregate WER can dilute. These checks complement WER and do
  not replace listening review; a mismatch does not by itself prove clipping.
- `aggregate_trial_wer` divides every reviewed trial's word errors by the total
  repeated reference-word count. It weights words, not clips. The report retains
  those numerator/denominator counts and each clip's ordered per-trial errors.
  Reviewed references without scoreable words are excluded from WER; reviewed
  silence uses the separate false-positive counters.
- Compare Parakeet optimizations using both total inference latency and the
  synchronized per-stage medians. Stage barriers are benchmark-only and are
  deliberately disabled during interactive dictation. A failed CUDA barrier
  aborts the benchmark rather than producing untrustworthy timings. The
  reported release-to-paste figures only add configured minimum/maximum
  post-roll and paste-delay constants to measured inference; they do not
  measure capture scheduling, resampling, delivery, or target-app response.
  For clips longer than 60 seconds, confirm every trial reports more than one
  window and a longest input no greater than 60 seconds.
- Do not commit audio, reference text, manifests, hypotheses, or result files.

The manifest's `runs` value must be a positive JSON integer and is the number
of measured trials after model warm-up. `--runs` overrides it; zero is rejected. Use at least three for exploratory comparisons and more when checking
an intermittent boundary failure.
