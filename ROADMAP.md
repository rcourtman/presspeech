# Presspeech product roadmap

Last reviewed: 23 September 2026

Presspeech is the small, private dictation tool: hold or toggle a key, speak,
and put locally transcribed text into the app you were using. The roadmap
optimises that loop before adding adjacent workflows.

This is a direction document, not a release schedule. **Now** means the next
problems to validate and solve; **Next** means the outcome that follows once
the core loop is dependable; **Explore** means evidence is promising but a
change has not cleared product and native-platform testing. Issue and pull
request status remains authoritative for individual changes.

## Product principles

1. **Deliver the right text to the right place.** A fast transcript is not a
   success if paste fails, stale clipboard content appears, or text reaches a
   window the user did not choose.
2. **Keep dictation private by construction.** Recognition remains on-device;
   there are no accounts, analytics, transcript uploads, or generative cloud
   fallback.
3. **Prefer a measured fixed path to a catalogue of choices.** A setting or
   engine earns its place by fixing a recurring user problem without making
   setup and recovery harder.
4. **Treat each platform as native.** macOS and Windows share product
   principles, not a UI toolkit or runtime. A change ships only after testing
   on the platform behavior it touches.
5. **Recover visibly.** When automatic insertion is unsafe or unavailable,
   Presspeech should preserve the complete transcript and tell the user how to
   continue.

## Now: qualify shipped text delivery

macOS 0.3.8 shipped on 22 September 2026 with automated, optimized-build, and
packaged-app checks complete. The current priority is to qualify its behavior
in the physical and target-app conditions those checks cannot simulate, not to
add another layer of settings. Open validation issues remain open until their
named evidence gates in the
[macOS release qualification](docs/manual-qa.md#macos-release-qualification)
are met; publication alone is not evidence that every target or input path
passed.

- Qualify paste-target capture in representative native, browser, and
  Electron/Chromium apps. macOS 0.3.8 gets frontmost-process identity
  from the window server but still requires the exact Accessibility-focused
  window before automatic paste. [Issue
  #33](https://github.com/rcourtman/presspeech/issues/33) stays open until a
  steady-focus Electron target can paste automatically while a switch between
  two windows of that same process still recovers to the clipboard. Process
  identity alone is not an acceptable substitute for the same-window check.
- Qualify the macOS 0.3.8 manual clipboard-recovery replacement against both
  fast native and slow Electron targets. It retires automatic timer restoration
  rather than choosing another delay: no timeout proves that another app has
  consumed a paste. Close [issue
  #36](https://github.com/rcourtman/presspeech/issues/36) only after the
  repeated native checks show that old clipboard content cannot race the new
  transcript.
- Exercise the macOS 0.3.8 configurable hotkey across keyboard layouts,
  hold and toggle modes, conflict cases, and keyboard/VoiceOver navigation.
  That is the remaining evidence gate for [issue
  #34](https://github.com/rcourtman/presspeech/issues/34), not a reason to
  widen the binding grammar further.
- Qualify the Windows 0.1.13 retained-dictation recovery and audio-device
  rescan on clean CPU and NVIDIA installations. A failed or uncertain delivery
  must keep reviewable text in process memory without silently replacing a
  newer clipboard value, and recording must wait for an explicit Copy or
  Discard decision.
- Keep improving setup, audio-route, permission, and no-speech diagnostics
  where a reproducible failure prevents a first successful dictation.
- Validate native behavior before shipping runtime changes. Model-free tests
  are necessary but do not prove microphone, accessibility, hotkey, clipboard,
  window-focus, or packaged-app behavior.

Success means a user can complete repeated dictations into representative
native, browser, Electron, remote-desktop, and elevated/non-elevated targets;
the intended text lands once, and every unsafe path leaves an explicit manual
paste recovery instead. The
[target-app compatibility protocol](docs/app-compatibility.md) gives community
reports from published builds the same small, privacy-safe baseline. Passing
reports provide the denominator that failure-only issues cannot, but community
reports supplement rather than replace native release testing.

### Product intelligence and community evidence (23 September 2026)

A small scan of two active local projects shows that offline recognition,
push-to-talk, and cross-app insertion are not unique differentiators among
these products: [Whisper Local](https://github.com/drajb/whisper-local)
advertises Windows and macOS support alongside hotwords, per-app rules, and
optional local cleanup; [Parrot](https://github.com/basic-intelligence/parrot)
describes local dictation across macOS, Windows, and Linux with cleanup and a
personal dictionary. This is a narrow feature scan, not market sizing or an
independent quality comparison. The implication is not to chase those feature
lists: Presspeech should earn distinction through a small, trustworthy
dictation loop and evidence that text reaches the intended target or is
recovered safely.

Public user evidence is thin and self-selected. A recent
[FOSS discussion of Handy](https://www.reddit.com/r/foss/comments/1vxrgg5/a_free_open_source_speechtotext_tool_that_has/)
includes anecdotal replies about reducing typing burden and one user's initial
difficulty finding a toggle mode that was already available. This is a narrow
discoverability signal about another project, not a representative survey or
Presspeech feedback. Do not infer adoption, unmet demand, or roadmap priority
from discussion activity or competitor feature lists.

Feedback intake is currently a measurement limitation: on 23 September,
GitHub's [Presspeech issue list](https://github.com/rcourtman/presspeech/issues)
shows issue creation restricted (and four open issues). New reports therefore
cannot be treated as evidence of low demand or few compatibility failures.
The local compatibility worksheet preserves aggregate counts without sending
them; if a comparable open report accepts comments, users can add their result
there, otherwise they should keep it privately and retry when intake returns.
Do not route around the restriction by soliciting transcripts, diagnostics,
or reports on unrelated public services. Reassess the community signal after
the project has a working, privacy-safe intake path.

## Next: earn a stable Windows release

The Windows build remains a prerelease with an unsigned installer. The next
product milestone is not feature parity with macOS; it is a trustworthy,
repeatable install-to-first-dictation path on supported Windows hardware.

- Pursue code signing rather than teaching users to bypass managed security
  policy.
- Keep installation and update integrity verifiable, including exact release
  assets, bounded downloads, checksums, and a recoverable failed update.
- Exercise CPU and supported NVIDIA paths on clean Windows installations,
  including first model preparation, microphone selection, hotkey conflicts,
  sleep/resume, and insertion into common app classes.
- Promote Windows from prerelease only when those native checks are repeatable
  and support documentation matches the shipped installer. Record the exact
  artifact and coverage in the
  [Windows release qualification](docs/manual-qa.md#windows-release-qualification)
  rather than treating portable tests as native evidence.

## Explore: reduce proper-name correction work safely

The need behind [issue
#21](https://github.com/rcourtman/presspeech/issues/21) is established: a daily
Polish user maintains hundreds of exact post-transcription rules for names and
domain terms across their inflected forms. The tested FluidAudio vocabulary
path is not the solution yet. In the corrected, complete-audio experiments:

- [lemma-only vocabulary did not recover the held-out inflected
  forms](https://github.com/rcourtman/presspeech/issues/21#issuecomment-5777963850)
  and raised negative-control WER from 6.12% to about 37%; and
- [explicitly supplied forms improved aggregate target
  recall](https://github.com/rcourtman/presspeech/issues/21#issuecomment-5778506977),
  but regressed seven individual recordings, produced unexpected supplied-term
  occurrences, and took about 2.6 times the baseline inference latency.

No tested decoder policy qualifies for the app. Presspeech will keep exact,
deterministic Dictionary & Shortcuts rules rather than expose an experimental
toggle that can silently replace unrelated words. A correction-side pattern or
stem rule also needs evidence before it enters the roadmap: it must define
bounded matching and prove, with positive and same-language negative fixtures,
that ordinary words and neighbouring text are not changed unexpectedly.

The redacted comparison harness remains available for materially different
upstream policies or correction approaches. The evidence gate in
[`experiments/swift-bench/README.md`](experiments/swift-bench/README.md) is
deliberately stricter than a promising small sample. Private audio, reference
text, hypotheses, vocabulary, and paths stay on the evaluator's computer.

## Not currently planned

These may be good products, but they would blur Presspeech's deliberately
narrow promise or add a network and support surface that the project does not
intend to carry:

- cloud transcription, account sync, telemetry, or a hosted service;
- generative rewriting of dictated text;
- meeting recording, speaker separation, or file transcription;
- a persistent transcript archive;
- a plugin or model marketplace;
- Linux support or a shared cross-platform UI/runtime.

The [comparison guide](https://rcourtman.github.io/presspeech/compare/) points
to tools that make different tradeoffs. “Not planned” is a scope decision, not
a claim that the workflow lacks value.

## How priorities change

Priority rises when evidence shows that a problem:

- blocks setup or the record-transcribe-insert loop;
- can lose, duplicate, misdirect, or expose user text;
- affects multiple users, common apps, hardware, languages, or accessibility
  workflows;
- has a small reproducible case and a result that can be measured privately;
- can be solved without compromising local-only operation or making the app
  substantially harder to understand.

Priority falls when a proposal is based only on competitor parity, has no
repeatable problem, requires an unbounded preferences surface, or cannot be
tested on the native platform before release.

To influence the roadmap, search the
[existing issues](https://github.com/rcourtman/presspeech/issues) before filing
a [bug report](https://github.com/rcourtman/presspeech/issues/new?template=bug_report.yml)
or [feature request](https://github.com/rcourtman/presspeech/issues/new?template=feature_request.yml).
Describe frequency, impact, the current workaround, and an observable success
condition. For recognition and performance work, follow the privacy-safe
evidence guidance in [CONTRIBUTING.md](CONTRIBUTING.md).
