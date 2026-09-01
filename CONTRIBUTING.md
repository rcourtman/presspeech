# Contributing

Thanks for considering a contribution to Presspeech. The project has isolated
macOS and Windows implementations. The released macOS app remains a
single-file Swift menu-bar app plus a thin signed/notarised `.app` wrapper;
the Windows tray app lives under `windows/` and uses Python, native Windows
integration, and local CUDA inference.

## Reporting bugs

Use the cross-platform [bug report
form](https://github.com/rcourtman/presspeech/issues/new?template=bug_report.yml)
and include the affected platform, app and operating-system versions,
reproduction steps, and hardware. Choose **Copy Diagnostics** from the
Presspeech menu and paste its privacy-safe summary when the app can open. The
summary excludes transcript, audio, and dictionary contents.

For macOS, also include:

- Mac model (M1/M2/M3/M4 etc.)
- Whether the tink and pop sounds play at the expected moments
- Whether all three privacy permissions (Microphone, Accessibility,
  Input Monitoring) are granted to **Presspeech.app** specifically (not
  `Terminal` or anything else)

For Windows, include the Windows version, configured model, GPU/driver details,
whether the model reached **Ready**, and whether the microphone check passed.

If Presspeech cannot open, include only the relevant last ~30 lines from
`~/Library/Logs/Presspeech.log` on macOS or
`%APPDATA%\Presspeech\log.txt` on Windows. Review logs before posting. Issues
are public: never include dictated text, audio, dictionary or shortcut
contents, credentials, or other private data. Report security vulnerabilities
through the private process in [SECURITY.md](SECURITY.md).

## Suggesting features

Use the focused [feature request
form](https://github.com/rcourtman/presspeech/issues/new?template=feature_request.yml).
Describe the recurring problem and a measurable success condition, not only a
proposed implementation. Roughly in scope: hotkey behaviour, transcription
quality / latency, menu bar UX, install/upgrade ergonomics. Roughly out of
scope:

- Cloud transcription backends — the project is local-only by design.
- A shared cross-platform UI or runtime. The macOS and Windows integrations
  deliberately stay separate; Linux is not currently supported.
- Heavy GUIs / preferences windows — the menu bar is the UI.

For transcription-quality or latency requests, aggregate before/after evidence
is much more actionable than impressions. Include the platform, language,
hardware, number of clips and reference words, trial count, baseline, and
privacy-safe metrics such as WER or critical-error counts. Say whether the
references were listened to and corrected by a human. Do not upload private
audio, reference text, hypotheses, vocabulary terms, or local paths.

The macOS benchmark helpers under [`experiments/swift-bench/`](experiments/swift-bench/)
redact transcripts and paths by default. For custom-vocabulary evaluation,
`run-vocabulary-bias-regression.sh --preflight-only --no-threshold` checks and
summarises a private fixture set without loading a model; a complete run
compares the candidate with production and writes an ignored, privacy-redacted
report. See the benchmark README for corpus controls and the stricter evidence
gate used before a performance-sensitive feature can ship. A small corpus is
still useful exploratory evidence when reported honestly; it just cannot clear
that product-candidate gate by itself.

## Development setup

```sh
git clone https://github.com/rcourtman/presspeech.git
cd presspeech/swift
./dev-run.sh
```

`dev-run.sh` is idempotent — re-run it any time. It builds
`Sources/Presspeech/main.swift` with `swift build`, wraps the binary
in `/tmp/Presspeech-dev.app`, signs it with your Developer ID +
hardened runtime + the production entitlements (so TCC permissions
carry over from the Cask install — no manual re-grants), kills any
prior dev instance, and relaunches via `open`.

Requirements: Xcode 16+ (or the Swift 6.3+ toolchain), macOS 14
(Sonoma) or later, and a Developer ID Application certificate in your
keychain.

After editing `Sources/Presspeech/main.swift`:

```sh
./dev-run.sh
tail -f ~/Library/Logs/Presspeech.log
```

For Windows development, use Python 3.12 from the project virtual environment:

```bat
cd windows
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install -r requirements-cuda.txt
.venv\Scripts\python -m unittest discover -s tests -v
.venv\Scripts\python app.py --selftest
```

The unit suite is model-free. The self-test loads the configured model and is
the native CUDA/ASR gate.

## Pull requests

- Keep the diff focused on one change.
- No new dependencies unless they replace something heavier or unlock
  a meaningful feature. SwiftPM dependencies in particular show up in
  every release build's `Package.resolved` lockfile — commit that
  alongside `Package.swift`.
- Match the existing style: terse Swift, structured concurrency where
  it earns its keep (`actor` for ANE access, `@MainActor` for UI),
  comments only when the *why* is non-obvious.
- Don't reintroduce `@MainActor` on `AudioCapture` — the
  `AVAudioEngine` tap fires on an audio thread and the actor
  isolation check will trap. The class is `@unchecked Sendable` with
  `NSLock` on purpose.
- If you change anything performance-sensitive, include before/after
  numbers from `experiments/swift-bench/` and note which Mac you ran
  on (CPU + ANE generation).

## Code structure

- `swift/Sources/Presspeech/main.swift` — the menu bar app. One file,
  section-tagged with `// MARK: -` regions. State is kept on the
  `PresspeechApp` instance and the various support classes
  (`Settings`, `HotkeyListener`, `AudioCapture`, `TranscriptionWorker`,
  `UpdateCheck`, etc.).
- `swift/Package.swift` — SwiftPM manifest, single FluidAudio
  dependency, macOS 14 platform target.
- `swift/Info.plist` — canonical Info.plist used by both
  `dev-run.sh` and `ship-swift.sh` so dev and release builds share
  bundle id / minimum macOS / usage descriptions.
- `swift/Resources/` — menu-bar PNGs (template image + @2x). Live
  outside the SwiftPM target; copied into `Contents/Resources/` by
  the wrapper scripts.
- `swift/dev-run.sh` — debug build + wrap + sign + relaunch.
- `ship-swift.sh` — version bump, release build, sign, notarise,
  staple, ditto-zip, checksum, atomic tag push, immutable GitHub release
  verification, Cask bump.
- `entitlements.plist` — hardened-runtime entitlements (two keys:
  `audio-input` + `microphone`).
- `experiments/swift-bench/` — head-to-head latency benchmark
  (FluidAudio/ANE vs the older presspeech-mlx/GPU path). Useful when
  changing the inference backend.
- `windows/app.py` — Windows hotkey, audio, paste, and tray lifecycle.
- `windows/engine.py` — Windows local ASR backends.
- `windows/tests/` — model-free Windows unit tests.
