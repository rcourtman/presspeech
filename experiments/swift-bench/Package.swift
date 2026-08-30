// swift-tools-version: 6.0
//
// presspeech-bench — Swift CLI that benchmarks ASR backends against the
// same audio inputs. Output is intentionally comparable with the
// sibling `./bench-py.py` script, which runs the same audio through
// the Python presspeech-mlx path as a reference baseline.
//
// Backends tested:
//   * Apple `SpeechAnalyzer` + `DictationTranscriber` (built into
//     macOS 26 Tahoe — uses Apple Neural Engine, no model download).
//   * FluidAudio's `AsrManager` running Parakeet TDT v3 via CoreML
//     on the ANE (model downloaded from HuggingFace on first run,
//     ~600 MB, cached thereafter).
//   * FluidAudio's direct and sliding-window Parakeet v3 vocabulary paths,
//     including conservative, spotter-rescue-disabled, and exact-similarity
//     candidate-evidence policies.
//   * Candidate FluidAudio Parakeet Unified, Nemotron English, and
//     Nemotron 3.5 multilingual paths, also via CoreML on the ANE.
//
// This benchmark drove the original "should Presspeech port to Swift?"
// decision; FluidAudio won and the production app uses it now (see
// ../../swift/Sources/Presspeech/main.swift). The bench stays around as
// the canonical "is the inference path still healthy?" check for any
// future backend / model swap.
//
// FluidAudio benchmarks run on Presspeech's macOS 14+ product floor.
// Only the optional Apple `SpeechAnalyzer` / `DictationTranscriber`
// backend requires macOS 26; its implementation and construction are
// availability-gated in main.swift. This normally stays pinned to the
// production app. The current candidate-only exception is the first upstream
// commit exposing Parakeet `int8-v2`; the benchmark explicitly preserves the
// app's prior chunking config so the encoder A/B changes one control. Do not
// move the app pin until that candidate clears the corpus gates.
import PackageDescription

let package = Package(
    name: "presspeech-bench",
    platforms: [
        .macOS("14.0"),
    ],
    products: [
        .executable(name: "presspeech-bench", targets: ["presspeech-bench"]),
    ],
    dependencies: [
        .package(url: "https://github.com/FluidInference/FluidAudio.git",
                 revision: "c7246f4dc78d05f75cdfc5a550cd72ced0c658bf"),
    ],
    targets: [
        .executableTarget(
            name: "presspeech-bench",
            dependencies: [
                .product(name: "FluidAudio", package: "FluidAudio"),
            ],
            // Embed Info.plist into the CLI executable. Speech.framework's
            // DictationTranscriber traps (exit 133 / SIGTRAP) during prepare
            // when NSSpeechRecognitionUsageDescription / CFBundleIdentifier
            // are missing from the binary — SwiftPM-built executables don't
            // get an Info.plist by default. The `__TEXT,__info_plist`
            // section is the canonical way to ship one inside a CLI binary.
            linkerSettings: [
                .unsafeFlags([
                    "-Xlinker", "-sectcreate",
                    "-Xlinker", "__TEXT",
                    "-Xlinker", "__info_plist",
                    "-Xlinker", "Info.plist",
                ])
            ]
        ),
    ]
)
