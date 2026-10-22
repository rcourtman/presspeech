# Presspeech — Swift source

This directory is the canonical Presspeech app: a single-file Swift
menu-bar dictation tool for Apple Silicon. The whole app lives in
[`Sources/Presspeech/main.swift`](Sources/Presspeech/main.swift).

## Build without launching

```sh
swift build
.build/debug/Presspeech --self-test all
```

This builds the debug executable and runs the model-free, non-interactive
self-tests without launching the menu-bar app or starting a model download.
`all` deliberately excludes the opt-in native keyboard and paste fixture.
Building requires Xcode 16+ (or a Swift 6.3+ toolchain) and macOS 14
(Sonoma) or later; it does not require a signing certificate.

## Signed native run (opt in)

```sh
./dev-run.sh
```

Only run this when you intend to replace a running Presspeech session. The
script signs `/tmp/Presspeech-dev.app` with a Developer ID Application
certificate, stops prior development and `/Applications/Presspeech.app`
processes, clears the shared active-run marker, and launches the development
app. Do not use it while
an existing dictation must remain uninterrupted. A launch with a missing model
can make a model-download request, especially for an existing installation;
read the [macOS model-download warning](../README.md#install-on-macos) first.
For real keyboard, window-focus, and paste acceptance, use the separate
[native interaction protocol](../docs/native-interaction-qa.md) and
[macOS release qualification](../docs/manual-qa.md#macos-release-qualification).

Logs land in `~/Library/Logs/Presspeech.log` — same path the production
Cask install uses, so a single `tail -f` covers both.

## Isolated QA run (speech-model UI)

```sh
./dev-run.sh --isolated
```

Builds `/tmp/Presspeech-QA.app` as `com.local.presspeech.qa` and runs it
beside the installed Presspeech instead of replacing it: own defaults domain
and privacy rows; log, support files and FluidAudio model cache under
`$PRESSPEECH_QA_ROOT` (default `~/Library/Application Support/Presspeech-QA`);
no audio input, no global hotkey, no update checks. Use it to exercise
Settings → Dictation → Speech Model (download prompt, verification, fallback).
It cannot dictate. An empty QA root starts at the first-download choice; to
start from a local v3, copy `~/Library/Application Support/FluidAudio/Models/parakeet-tdt-0.6b-v3`
into `$PRESSPEECH_QA_ROOT/FluidAudio/Models/` first. The script prints the
reset command.

## Layout

| Path | Purpose |
|---|---|
| `Package.swift` | SwiftPM manifest. Single dependency: [FluidAudio](https://github.com/FluidInference/FluidAudio). |
| `Sources/Presspeech/main.swift` | The entire app. Section-tagged with `// MARK: -`. |
| `Info.plist` | Canonical Info.plist shared by `dev-run.sh` and `../ship-swift.sh`. |
| `Resources/presspeech-menubar.png` (+ `@2x`) | Template menu-bar icon copied into the app wrapper by the development and release scripts. |
| `dev-run.sh` | Local iteration loop (debug build, sign, relaunch). |

## More

- **End users** — see [`../README.md`](../README.md) for install / usage / troubleshooting.
- **Contributors** — see [`../CONTRIBUTING.md`](../CONTRIBUTING.md).
- **Release** — see [`../ship-swift.sh`](../ship-swift.sh).
- **Latency benchmarks** — see [`../experiments/swift-bench/`](../experiments/swift-bench/).
