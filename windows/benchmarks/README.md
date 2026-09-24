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
Version 12 adds an optional Parakeet short-speech tail-silence probe. Its
`parakeet_tail_silence_ms` setting is reported separately and is **not** part
of `benchmark_inputs_sha256`; matching digests with different probe settings
do not describe the same inference workload. Without the option, ordinary
benchmark inference and scoring are unchanged.
Version 13 counterbalances the probe's clean/tailed execution order across
all reviewed pairs and records each pair's order. Compare both the input digest
and benchmark version before interpreting probe latency across reports; version
12 always ran the tailed member second. Manifest order can change which
condition goes first for a particular clip even when the input digest matches;
version 21's order digest makes this visible without revealing clip paths.
Version 14 adds signed, per-pair **tailed minus clean** inference time and
order-stratified medians at the sample and corpus levels. A positive delta is
slower with the tail; a negative delta is faster. These are diagnostics, not
release-to-paste timings, and small or imbalanced order strata cannot establish
an inference-speed effect. Compare identical input digests, run counts,
hardware, model settings, and manifest order before interpreting them.
Version 15 rejects byte-identical **effective 16 kHz mono ASR audio** under
different fixture IDs before loading a model. Rewrapped or differently sourced
files that decode to identical ASR samples cannot inflate the number of
independent clips or overweight aggregate WER and silence results. Older
reports could include exact duplicates; their aggregate input digest preserved
that multiplicity but did not reject it. This check does not detect
near-duplicates, repeated takes, or unrepresentative
speakers and cannot replace human review of corpus independence.

Version 15 adds a separate, opt-in *recorded-tail* probe. Its ordinary WER and
latency remain based on the full captured audio; the human-marked crop is only
a paired experimental variant. Its `recorded_tail_probe_inputs_sha256` covers
the full effective audio and exact crop sample for each probed clip. Compare it
**and** `benchmark_inputs_sha256`, model, language, hardware, run count, and
manifest order between reports. Changing `speech_end_ms` does not change the
ordinary corpus digest. Version 15 does not change product recognition.

Version 16 adds paired final-word loss and recovery counts to the Parakeet
tail-silence probe. These distinguish a final word lost only with the appended
tail from one already absent in the clean decode; the prior tailed-only failure
count remains for continuity. Losses are also stratified by decode order.

Version 17 adds signed **trimmed minus full** paired inference time to the
recorded-tail probe, pooled across trials and split by which variant ran
first. It also splits blank transitions and worsened word errors in *both*
directions by execution order, so a harmful crop cannot hide behind a pooled
tail-benefit count. These are model-inference diagnostics, not measured
release-to-paste times or evidence that an automatic crop can find the
human-marked speech endpoint. Older reports do not have these fields; compare
matching benchmark versions and inspect the order strata before interpreting
the deltas. Version 17 does not change product recognition.

Version 18 adds paired recorded-tail **final-word loss and recovery** counts,
also split by decode order, plus full and trimmed final-word failure counts.
A crop can correct an earlier word while losing the final one, leaving total
word errors unchanged; pooled WER alone would miss that boundary regression.
Compare version 18 reports for these fields. This is benchmark-only and does
not change capture or recognition.

Version 19 adds `tail_silence_probe_groups` and
`recorded_tail_probe_groups` to the JSON report. Each repeats the paired
probe's counts, decode-order breakdown, and signed latency distribution for
human-labelled task groups, language groups, and language/task intersections.
Only reviewed speech actually probed enters these groups; unlabelled probed
clips remain in the pooled probe summary, and unprobed silence controls do not
enter a probe group even if they have labels. The console prints compact harm
and order counts for each labelled group. Compare `sample_count`, pair counts,
and order balance before interpreting a small group; grouping does not make
clips independent or establish a statistically significant effect. Version 19
does not change product recognition, capture, or benchmark inference inputs.

Version 20 makes first-/final-word diagnostics require the full consecutive
run of a repeated boundary word. For example, a reference ending `go go` and
a hypothesis ending `go` now fails the final-word check rather than appearing
retained. The same rule applies to paired tail loss/recovery counts; WER and
product inference are unchanged. Compare version 20 reports when a reference
starts or ends with repeated words. The check is deliberately conservative:
it cannot identify which identical spoken occurrence was lost.

Version 21 adds `benchmark_order_sha256` to the JSON report and console. It
hashes effective ASR-audio identities in **manifest order**, including
unprobed controls, without emitting per-clip hashes, IDs, or paths. The older
`benchmark_inputs_sha256` remains order-independent. For paired probe
comparisons, require matching input **and** order digests, benchmark version,
run count, probe settings, model, language, precision, and hardware before
attributing differences to a policy. The recorded-tail probe also requires a
matching `recorded_tail_probe_inputs_sha256`. A matching order digest cannot
prove representative audio, human-review quality, or identical thermal and
background-load conditions. Version 21 changes report provenance only, not
recognition or the probe execution order.

Version 22 compares Whisper turbo's automatic language code with the primary
language in each **human-reviewed speech** clip's `language_group` label (for
example `pl-PL` compares with `pl`). The JSON report and console summary count
matches, mismatches, missing/invalid codes, and VAD-rejected trials separately
for each clip, the corpus, and task/language/intersection groups. Only
two/three-letter primary language labels with ordinary BCP-47 subtags are
comparable; unlabelled or custom-group clips are excluded from this diagnostic,
not silently treated as matches. A VAD-rejected clip has no meaningful
language result. `coverage_complete` is false if any code is missing or any
trial was VAD-rejected; even complete coverage and correct language IDs do not
prove an accurate transcript. Hinted-language runs, English-only Whisper, and
Parakeet have no automatic-language comparison. This is benchmark-only and
does not change model inference or product behavior.

Version 23 adds paired **first-word** loss and recovery counts to both
Parakeet tail probes, using the same consecutive-boundary-word rule as the
existing final-word diagnostics. Counts appear per pair, per clip, in pooled
and labelled-group summaries, and by decode order. The synthetic probe now
also reports clean and tailed first-word failure totals; the recorded probe
reports full and trimmed totals. A tail can alter recognition of earlier
speech, so unchanged total WER or a correct final word cannot establish that
the first word survived. These text comparisons cannot prove acoustic
alignment or that a crop is safe. Compare version 23 reports for these fields;
recognition and inference inputs are unchanged.

Version 24 adds the longest consecutive reference-word deletion on a stable
minimum-edit alignment. Each reviewed speech clip records the consensus value
and every trial's value; the report and console also show the worst reviewed
trial across the corpus. This catches a long internal omission that aggregate
WER or first-/final-word checks can obscure, especially in multi-window
Parakeet dictation. Equal-cost alignments prefer a diagonal, then a deletion,
then an insertion, matching the macOS benchmark. The number is path-dependent:
it does not locate an acoustic seam or prove the model caused the omission.
It is a diagnostic, not a Windows release threshold; inspect the reviewed
audio, references, individual transcripts, and window plan before changing
model or chunking policy. Unreviewed clips and silence controls are excluded.
Version 24 changes scoring only, not transcription or inference timing. Do not
compare this field with older reports, which did not measure it.

Version 25 marks clips whose **effective 16 kHz ASR audio** is shorter than
the Windows app's 250 ms minimum transcription gate. These clips still count
in model-only WER, silence, and inference summaries, but the per-clip
release-to-paste estimates are `null`: the app would discard that capture
before inference. The report and console give the below-gate clip count,
and the console labels each such clip. A clip at exactly 250 ms passes this
length gate. Passing it does not prove that a physical recording, hotkey,
target-app paste, or post-roll timing would succeed; the other delivery
figures remain estimates, not native measurements. Compare version 25 reports
when interpreting these fields; no recognition or capture policy changed.

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
Duplicate-audio errors omit fixture names and paths because those may reveal
private dictation content.

For a local Whisper pause-policy experiment, compare the release's 160 ms
minimum silence split with explicit alternatives without editing product code:

```bat
.venv\Scripts\python benchmark.py --manifest benchmarks\manifest.json --model base.en --runs 5 --output benchmarks\whisper-160ms.json
.venv\Scripts\python benchmark.py --manifest benchmarks\manifest.json --model base.en --runs 5 --whisper-vad-min-silence-ms 2000 --output benchmarks\whisper-2000ms.json
```

`--whisper-vad-min-silence-ms` changes only the benchmark's Whisper policy;
the report records the full effective policy and labels it as a benchmark-only
override. Omitting it records the unchanged Presspeech product default. The
option is rejected for non-Whisper models. Presspeech calls
`WhisperModel.transcribe` with an explicit 160 ms policy; without those
parameters, that unbatched API uses the 2,000 ms `VadOptions` default in
faster-whisper 1.2.1. The separate `BatchedInferencePipeline` has a 160 ms
implicit default ([transcribe.py](https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/faster_whisper/transcribe.py),
[vad.py](https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/faster_whisper/vad.py)).
Neither upstream default is evidence that one is better for dictation. Use
identical reviewed audio, references, model, language, hardware, and run count; include
natural pauses, quiet speech, short commands, and silence controls. Compare WER,
per-trial errors, VAD-retained duration, rejected-speech trials, boundary-word
retention, silence false positives, and latency by task group before proposing
any product-policy change.

For two version-25 JSON reports from those commands, run the model-free
comparison helper locally:

```bat
.venv\Scripts\python compare_whisper_vad.py benchmarks\whisper-160ms.json benchmarks\whisper-2000ms.json
```

It requires reviewed speech and silence controls and rejects mismatched
corpus/order digests, model snapshot, requested language,
precision, recorded environment, run count, per-clip review scope, or any VAD
setting other than the minimum silence duration. It compares all-trial WER,
longest deletion, first/final-word failures, VAD rejection and missing-duration
counts, reviewed-silence false positives, and inference median. It also counts
anonymous clip positions with worsened metrics and task/language/intersection
strata containing a clip-level quality regression, even if pooled errors improve.
It does **not** print IDs, paths, references,
transcripts, or group labels. Sub-250 ms clips remain in model scores and are
counted separately as below the app's transcription gate, not product delivery
evidence. The script does not certify the input digest or
hardware metadata independently and cannot prove identical thermal/background
load, representative references, acoustic speech recall, or native delivery.
Do not treat its zero-regression output as a release pass; review the private
reports and recordings before changing product VAD policy. Test the helper
without models using `python -m unittest tests.test_compare_whisper_vad` from
`windows/`.

## Parakeet trailing-silence probe

The Windows app captures at least 80 ms and at most 400 ms after hotkey
release. An [upstream Parakeet v3 report](https://github.com/NVIDIA-NeMo/Speech/issues/15757)
reproduced a short utterance decoding to an empty string after appending
400 ms of zeros. That report used NeMo on CPU, **not** Presspeech's pinned
Transformers loader or Windows capture path, so it is a risk to test rather
than evidence that Presspeech has the same defect.

To replay the **same public source and 1.0–3.2 s crop** through Presspeech's
Windows benchmark, use the source virtual environment:

```bat
cd windows
.venv\Scripts\python prepare_public_tail_fixture.py
```

The preparer fetches the audio from a fixed Hugging Face `speech-to-speech`
commit, checks its SHA-256 and size, downmixes and resamples it with the app's
SoXR-HQ policy, then writes a 16 kHz float WAV and manifest under the ignored
`benchmarks\public-parakeet-tail\` directory. It will not overwrite an
existing fixture. The upstream NeMo example used SciPy resampling, so this is
not a byte-identical replay of its waveform; it tests the pinned Presspeech
input path instead. The source repo's [license](https://github.com/huggingface/speech-to-speech/blob/main/LICENSE)
is Apache-2.0. The script does not commit, distribute, or upload audio.

**Listen to the generated clip** and enter its exact words in
`manifest.json`, then set `reference_reviewed` to `true`. The upstream
recognizer's quoted output is not a ground-truth reference. Until review, the
runner refuses the paired probe. Then run:

```bat
.venv\Scripts\python benchmark.py --manifest benchmarks\public-parakeet-tail\manifest.json --model parakeet-tdt-0.6b-v3 --runs 5 --parakeet-tail-silence-ms 400 --output benchmarks\public-parakeet-tail-result.json
```

Inspect paired blank transitions, word errors, first-/final-word losses,
and signed latency deltas, not just a single transcript. A result from one public voice
and synthetic zero tail cannot qualify an automatic trim/retry policy: also
test multiple native captured short and quiet utterances, reviewed silence
controls, and the actual Windows release-to-paste timing.

Build a small, private manifest of short speech clips cropped at the spoken
endpoint, with listened-to references and `reference_reviewed: true`; include
short commands and quiet speech. At 400 ms, keep original clips at or below
14.6 s so both variants use Presspeech's same 15 s Parakeet feature bucket.
The runner checks the **resampled 16 kHz sample count** before model loading
and rejects reviewed speech whose clean and tailed variants would use different
15/30/60 s buckets or cross the 60 s long-form windowing boundary. Silence
controls and unreviewed clips are not paired and do not need this restriction.
Run a paired probe on the same loaded Parakeet model:

```bat
.venv\Scripts\python benchmark.py --manifest benchmarks\short-speech.json --model parakeet-tdt-0.6b-v3 --runs 5 --parakeet-tail-silence-ms 400 --output benchmarks\tail-400ms.json
```

The option accepts 1–400 ms and affects only this benchmark. For each scored
speech clip, each trial transcribes the original and the same samples with
zero-valued 16 kHz samples appended, alternating which goes first across all
reviewed pairs. Reviewed silence, unreviewed audio,
and unscoreable references receive only the ordinary transcription. The JSON
keeps ordered transcript pairs, paired word-error counts, blank regressions,
paired first-/final-word losses and recoveries, separate tailed inference times, and
signed paired inference-time deltas.
Each sample's `trial_order` is indexed like `pairs`, both inference-time arrays,
and `paired_inference_delta_seconds.all`; aggregate order counts and paired
delta distributions are also reported. The `order_breakdown` in each sample
and the aggregate report separately count nonempty-to-empty and
worsened-word-error trials for each first variant.
`paired_inference_delta_seconds.by_order` retains separate
first-variant timing distributions; an empty stratum has `null` medians. The
console reports pooled and order-stratified counts, paired latency medians,
and harm counts by labelled task/language group and intersection.
Review `nonempty_to_empty_trial_count`, `first_word_lost_trial_count`,
`final_word_lost_trial_count`, and worsened word errors by order,
and first/final-word failures by task group, not just pooled WER. A tailed
output that differs from an already-wrong baseline is not automatically a
regression. Counterbalancing reduces systematic second-run warming bias but
cannot remove thermal drift, model state, or other order effects, so these
times remain diagnostics, not a release-to-paste comparison.
Synthetic zeros also do not represent microphone room tone or prove the live
post-roll behavior. Any product trim/retry policy still needs paired native
Windows dictation, silence controls, and a latency check before adoption.

## Parakeet recorded-tail probe

Use this probe to check *actual captured* post-release audio instead of
appending zeros. For a **source-run Windows app**, close Presspeech, set
`"capture_next_benchmark": true` in the local
`%APPDATA%\Presspeech\config.json`, then relaunch and dictate a short phrase.
There is no Settings control for this capture flag. The app resets the flag
after saving the next captured WAV under the ignored `benchmarks/audio/`
directory beside the source code. Keep both the WAV and local config private;
the latter can contain personal dictionary rules. Do not assume the packaged
installer has a writable benchmark directory. New captures use 32-bit float
WAV so the benchmark reads the same samples the app sent to ASR, including
quiet tails and any values outside the PCM16 range. Earlier app captures were
saved as clipped, quantized 16-bit WAV; they cannot recover the original ASR
input, so recapture them for sample-exact comparisons. Listen to the full
recording and set a reviewed reference. Mark `speech_end_ms` at the boundary **after
the entire final spoken sound**, including a quiet final consonant; do not
equate the hotkey release with speech end. For example, a reviewed 1,000 ms
capture with its last speech sample before 600 ms can use:

```json
{
  "id": "recorded-tail-001",
  "audio": "audio/recorded-tail-001.wav",
  "reference": "Turn on the lights",
  "reference_reviewed": true,
  "speech_end_ms": 600,
  "task_group": "short-command"
}
```

Then run the pinned model and reviewed corpus with the opt-in probe; the full
capture is also scored as the ordinary baseline in the same run:

```bat
.venv\Scripts\python benchmark.py --manifest benchmarks\manifest.json --model parakeet-tdt-0.6b-v3 --runs 5 --parakeet-recorded-tail-probe --output benchmarks\recorded-tail.json
```

The runner rejects non-integer, unreviewed, or unscoreable endpoints, a crop
that removes no audio or more than the app's 400 ms maximum post-roll, and a
pair that crosses a 15/30/60 s feature bucket or the 60 s windowing boundary.
The recorded-tail and synthetic-zero probe flags cannot be combined; a
manifest with `speech_end_ms` also cannot enter the synthetic-zero probe,
which requires endpoint-cropped clips rather than already-tailed captures.
Only reviewed speech rows with `speech_end_ms` are paired; other rows,
including silence controls, still receive ordinary full-audio trials. The
full captured input remains the product baseline for ordinary WER, boundary
retention, and latency. The JSON includes full/trimmed transcripts, word errors,
blank transitions in **both** directions, inference times, signed paired
trimmed-minus-full inference deltas, and counterbalanced execution order for
every pair. It also marks whether either variant retained the reviewed first
and final word runs and counts newly lost and recovered edge words separately.
The corpus summary keeps both harm directions, edge-word transitions, and
latency deltas stratified by decode order; `recorded_tail_probe_groups` also repeats the
summary for labelled task/language groups and their intersections. Compare
these strata as well as pooled medians.
These boundary flags compare the normalized initial or terminal word run with
the reference's run, so a missing repeat cannot appear retained. They are
diagnostic text matches, not acoustic proof that a particular spoken
occurrence survived a crop.
The cropped variant is a diagnostic only: an
improvement does not show that an automatic trim can locate this human-marked
boundary, and a mistaken crop can delete a final word. Neither the manifest nor
the report proves that the WAV came from Presspeech's capture path or that the
endpoint is correctly marked. Compare order strata,
short/quiet speech, first-/final-word errors, and silence controls before considering
any production policy. Keep WAVs, manifests, references, and results private.

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
  inspect WER, the longest consecutive reference-word deletion in every trial,
  and the reported window plan for duplication or loss.
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
  is measured work. On reviewed Whisper-turbo speech with a comparable
  `language_group`, also inspect the version-20 language-ID match, mismatch,
  missing, and VAD-rejected counts in each stratum. A wrong code can help
  diagnose an error, but it is not by itself a WER result. Codes inferred from
  VAD-rejected silence are discarded.
- Inspect `aggregate_worst_trial_wer` as well as consensus `aggregate_wer` when
  screening regressions: it sums each clip's worst observed word-error count,
  exposing intermittent internal substitutions that a modal transcript can hide.
  Best/worst envelopes can combine different repetitions across clips; they are
  neither an observed whole-corpus run nor confidence intervals. Their extremes
  also depend on run count, so compare candidates with the same number of runs.
- Inspect first- and final-word retention alongside WER. Each reviewed trial
  must begin with the reference's first word and end with its final word;
  adjacent repeats at either boundary must retain the full run count;
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
