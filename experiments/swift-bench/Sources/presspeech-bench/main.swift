// presspeech-bench — head-to-head benchmark of Apple SpeechAnalyzer
// (DictationTranscriber on the Apple Neural Engine, built into
// macOS 26 Tahoe) vs FluidAudio model variants via CoreML on the
// ANE on the same audio. Output is intentionally comparable to
// the sibling `./bench-py.py` (Python presspeech-mlx, GPU/Metal), so
// all three backends can be cross-referenced in one table.
//
// Usage:
//   presspeech-bench --file path/to/audio.wav [--trials 5] [--backend apple|v3|v3-vocab|v3-vocab-conservative|v3-vocab-no-rescue|sliding-v3|sliding-vocab|sliding-vocab-conservative|sliding-vocab-no-rescue|unified|nemotron-en|nemotron-multilingual|110m|fluid|both] [--redact-transcripts]
//
// Audio must be 16 kHz mono Float32 (or convertible to that —
// AVAudioFile + AVAudioPCMBuffer handles the conversion).

import Foundation
import AVFoundation
import Speech
import FluidAudio

let UNIFIED_MODEL_TRAILING_SILENCE_MS = 250
let DEFAULT_NEMOTRON_MULTILINGUAL_LANGUAGE = "en-US"
let DEFAULT_NEMOTRON_MULTILINGUAL_CHUNK_MS = 2240
let NEMOTRON_MULTILINGUAL_CHUNK_CHOICES = [560, 1120, 2240, 4480]
let BENCH_SAMPLE_RATE: Double = 16_000

// MARK: - CLI

struct CLIArgs {
    var file: URL
    var trials: Int = 5
    // "apple" | "v3" | "unified" | "nemotron-en" |
    // "nemotron-multilingual" | "110m" | "fluid" (= v3 + candidates +
    // 110m) | "both" (= apple + fluid).
    // Defaults to "v3": it's the production model. Unified and Nemotron
    // remain candidate backends; 110m remains broken upstream.
    var backend: String = "v3"
    // Ground-truth transcript for WER. If nil, falls back to a sibling
    // "<file-stem>.txt" (written by generate-test-audio.sh); if neither
    // exists, WER is skipped.
    var ref: String? = nil
    // Keep transcript/reference contents out of stdout while still
    // computing latency, memory, and WER. Used for local real-dictation
    // regression reports that should remain privacy-safe by default.
    var redactTranscripts = false
    // Candidate-model default used for final-word retention studies. Set to
    // 0 to measure the raw model, or sweep values when tuning a future model.
    var unifiedTrailingSilenceMs = UNIFIED_MODEL_TRAILING_SILENCE_MS
    // Nemotron 3.5 is prompt-conditioned and has separately exported chunk
    // tiers, so record both choices in every benchmark invocation.
    var nemotronMultilingualLanguage = DEFAULT_NEMOTRON_MULTILINGUAL_LANGUAGE
    var nemotronMultilingualChunkMs = DEFAULT_NEMOTRON_MULTILINGUAL_CHUNK_MS
    // Optional Parakeet v3 language/script hint. `nil` keeps auto-detection.
    var language: Language? = nil
    // The vocabulary path is accepted only by explicit vocabulary backends.
    // It stays outside production until the benchmark proves a quality win.
    var customVocabulary: URL? = nil
    // Plain-text canonical terms whose exact surface-form recall is reported
    // without printing term or transcript content.
    var criticalTerms: URL? = nil
}

func positiveTrialCount(_ value: String) -> Int? {
    guard let count = Int(value), count > 0 else { return nil }
    return count
}

func parseArgs() -> CLIArgs {
    var iter = CommandLine.arguments.dropFirst().makeIterator()
    var file: URL? = nil
    var trials: Int = 5
    var backend: String = "v3"
    var ref: String? = nil
    var redactTranscripts = false
    var unifiedTrailingSilenceMs = UNIFIED_MODEL_TRAILING_SILENCE_MS
    var nemotronMultilingualLanguage = DEFAULT_NEMOTRON_MULTILINGUAL_LANGUAGE
    var nemotronMultilingualChunkMs = DEFAULT_NEMOTRON_MULTILINGUAL_CHUNK_MS
    var language: Language? = nil
    var customVocabulary: URL? = nil
    var criticalTerms: URL? = nil
    while let arg = iter.next() {
        switch arg {
        case "--file":
            if let v = iter.next() { file = URL(fileURLWithPath: v) }
        case "--trials":
            guard let v = iter.next(), let n = positiveTrialCount(v) else {
                FileHandle.standardError.write(Data("--trials requires a positive integer\n".utf8))
                exit(2)
            }
            trials = n
        case "--backend":
            if let v = iter.next() { backend = v }
        case "--ref":
            if let v = iter.next() { ref = v }
        case "--redact-transcripts":
            redactTranscripts = true
        case "--language":
            guard let v = iter.next() else {
                FileHandle.standardError.write(Data("--language requires auto or one of: \(Language.allCases.map(\.rawValue).joined(separator: "|"))\n".utf8))
                exit(2)
            }
            if v == "auto" {
                language = nil
            } else if let parsed = Language(rawValue: v) {
                language = parsed
            } else {
                FileHandle.standardError.write(Data("unsupported --language \"\(v)\" (expected auto|\(Language.allCases.map(\.rawValue).joined(separator: "|")))\n".utf8))
                exit(2)
            }
        case "--custom-vocabulary":
            guard let v = iter.next(), !v.isEmpty else {
                FileHandle.standardError.write(Data("--custom-vocabulary requires a file path\n".utf8))
                exit(2)
            }
            customVocabulary = URL(fileURLWithPath: v)
        case "--critical-terms":
            guard let v = iter.next(), !v.isEmpty else {
                FileHandle.standardError.write(Data("--critical-terms requires a file path\n".utf8))
                exit(2)
            }
            criticalTerms = URL(fileURLWithPath: v)
        case "--unified-trailing-silence-ms":
            guard let v = iter.next(), let n = Int(v), n >= 0 else {
                FileHandle.standardError.write(Data("--unified-trailing-silence-ms requires a non-negative integer\n".utf8))
                exit(2)
            }
            unifiedTrailingSilenceMs = n
        case "--nemotron-multilingual-language":
            guard let v = iter.next(), !v.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                FileHandle.standardError.write(Data("--nemotron-multilingual-language requires a language code\n".utf8))
                exit(2)
            }
            nemotronMultilingualLanguage = v
        case "--nemotron-multilingual-chunk-ms":
            guard let v = iter.next(), let n = Int(v), NEMOTRON_MULTILINGUAL_CHUNK_CHOICES.contains(n) else {
                let choices = NEMOTRON_MULTILINGUAL_CHUNK_CHOICES.map(String.init).joined(separator: "|")
                FileHandle.standardError.write(Data("--nemotron-multilingual-chunk-ms must be one of \(choices)\n".utf8))
                exit(2)
            }
            nemotronMultilingualChunkMs = n
        case "-h", "--help":
            print("""
            usage: presspeech-bench --file <wav> [--trials N] [--backend apple|v3|v3-vocab|v3-vocab-conservative|v3-vocab-no-rescue|sliding-v3|sliding-vocab|sliding-vocab-conservative|sliding-vocab-no-rescue|unified|nemotron-en|nemotron-multilingual|110m|fluid|both] [--ref "text"] [--redact-transcripts]
                   presspeech-bench --self-test

              --backend  v3    FluidAudio Parakeet TDT v3 — production model (default)
                         v3-vocab
                              production v3 path plus auxiliary CTC vocabulary rescoring;
                              requires --custom-vocabulary
                         v3-vocab-conservative
                              v3-vocab with FluidAudio's recommended short-term taper
                              and spotter similarity floors
                         v3-vocab-no-rescue
                              v3-vocab with acoustic-only spotter rescue disabled
                         sliding-v3
                              Parakeet v3 through FluidAudio's sliding-window manager
                         sliding-vocab
                              sliding-v3 plus auxiliary CTC vocabulary rescoring;
                              requires --custom-vocabulary
                         sliding-vocab-conservative
                              sliding-vocab with FluidAudio's recommended short-term
                              taper and spotter similarity floors
                         sliding-vocab-no-rescue
                              sliding-vocab with FluidAudio's acoustic-only spotter
                              rescue disabled to reduce false replacements
                         unified
                              FluidAudio Parakeet Unified 0.6B offline batch
                         nemotron-en
                              FluidAudio Nemotron Speech Streaming English 0.6B, 1120 ms tier
                         nemotron-multilingual
                              FluidAudio Nemotron 3.5 Streaming Multilingual 0.6B
                         110m  FluidAudio Parakeet TDT-CTC 110M (smaller English model;
                               currently fails to load — broken upstream)
                         fluid v3 + candidate FluidAudio backends + 110m head-to-head
                         apple Apple SpeechAnalyzer (macOS 26+)
                         both  apple + fluid
              --ref      reference transcript for WER; defaults to <file>.txt if present
              --redact-transcripts
                         omit reference and hypothesis text from output while still
                         reporting WER; useful for private real-dictation runs
              --language <auto|code>
                         Parakeet v3 language/script hint (for example pl or en;
                         default: auto)
              --custom-vocabulary <path>
                         simple text or FluidAudio JSON vocabulary file; valid only
                         with a v3-vocab* or sliding-vocab* backend
              --critical-terms <path>
                         plain text, one canonical word or phrase per line; report
                         exact surface-form recall without printing term content
              --unified-trailing-silence-ms <n>
                         append n ms of silence before Unified transcription
                         (default: \(UNIFIED_MODEL_TRAILING_SILENCE_MS), matching Presspeech)
              --nemotron-multilingual-language <code>
                         language prompt for Nemotron 3.5 (default: \(DEFAULT_NEMOTRON_MULTILINGUAL_LANGUAGE))
              --nemotron-multilingual-chunk-ms <560|1120|2240|4480>
                         exported Nemotron 3.5 chunk tier (default: \(DEFAULT_NEMOTRON_MULTILINGUAL_CHUNK_MS))

            For a clean per-model memory number, run one model per process
            (--backend v3, then --backend 110m) — footprint is cumulative
            when several backends run in the same process.
            """)
            exit(0)
        default:
            FileHandle.standardError.write(Data("unknown arg: \(arg)\n".utf8))
            exit(2)
        }
    }
    guard let file else {
        FileHandle.standardError.write(Data("--file is required\n".utf8))
        exit(2)
    }
    return CLIArgs(file: file,
                   trials: trials,
                   backend: backend,
                   ref: ref,
                   redactTranscripts: redactTranscripts,
                   unifiedTrailingSilenceMs: unifiedTrailingSilenceMs,
                   nemotronMultilingualLanguage: nemotronMultilingualLanguage,
                   nemotronMultilingualChunkMs: nemotronMultilingualChunkMs,
                   language: language,
                   customVocabulary: customVocabulary,
                   criticalTerms: criticalTerms)
}

// MARK: - Audio loading
//
// Both backends want 16 kHz mono Float32. AVAudioFile gives us
// whatever the file actually is; we convert with AVAudioConverter
// rather than trusting the caller to have pre-resampled.

enum AudioLoadError: Error { case openFailed, convertFailed, emptyBuffer }

private final class SingleBufferConverterInputProvider: @unchecked Sendable {
    private let buffer: AVAudioPCMBuffer
    private let lock = NSLock()
    private var didProvideBuffer = false

    init(buffer: AVAudioPCMBuffer) {
        self.buffer = buffer
    }

    func provide(outStatus: UnsafeMutablePointer<AVAudioConverterInputStatus>) -> AVAudioBuffer? {
        lock.lock()
        defer { lock.unlock() }

        if didProvideBuffer {
            outStatus.pointee = .endOfStream
            return nil
        }

        didProvideBuffer = true
        outStatus.pointee = .haveData
        return buffer
    }
}

func load16kMono(url: URL) throws -> [Float] {
    let file = try AVAudioFile(forReading: url)
    let srcFormat = file.processingFormat

    // Target: 16 kHz mono Float32.
    guard let dstFormat = AVAudioFormat(
        commonFormat: .pcmFormatFloat32,
        sampleRate: 16_000,
        channels: 1,
        interleaved: false
    ) else { throw AudioLoadError.openFailed }

    // Read the whole file into a buffer at the file's native rate.
    guard let srcBuf = AVAudioPCMBuffer(
        pcmFormat: srcFormat,
        frameCapacity: AVAudioFrameCount(file.length)
    ) else { throw AudioLoadError.openFailed }
    try file.read(into: srcBuf)

    // Convert in one shot. Worst-case output length = src * (dst/src).
    let ratio = dstFormat.sampleRate / srcFormat.sampleRate
    let dstCap = AVAudioFrameCount(Double(srcBuf.frameLength) * ratio + 1024)
    guard let dstBuf = AVAudioPCMBuffer(pcmFormat: dstFormat, frameCapacity: dstCap),
          let converter = AVAudioConverter(from: srcFormat, to: dstFormat)
    else { throw AudioLoadError.convertFailed }

    var error: NSError?
    let inputProvider = SingleBufferConverterInputProvider(buffer: srcBuf)
    let status = converter.convert(to: dstBuf, error: &error) { _, outStatus in
        inputProvider.provide(outStatus: outStatus)
    }
    if status == .error { throw error ?? AudioLoadError.convertFailed }

    guard let chPtr = dstBuf.floatChannelData?[0] else { throw AudioLoadError.emptyBuffer }
    return Array(UnsafeBufferPointer(start: chPtr, count: Int(dstBuf.frameLength)))
}

// MARK: - Backends

protocol ASRBackend {
    var name: String { get }
    /// On-disk model caches used by this backend. Reported after prepare so
    /// candidate download/storage cost is visible beside latency and memory.
    var modelCacheComponents: [(label: String, url: URL)] { get }
    /// Run one transcription. Returns the transcript and elapsed seconds
    /// for inference only (model load + warmup happen in `prepare()`).
    func run(samples: [Float]) async throws -> (text: String, elapsed: Double)
    /// Load models, do whatever warmup is fair to exclude from the measured path.
    func prepare(warmupSamples: [Float]) async throws
}

extension ASRBackend {
    var modelCacheComponents: [(label: String, url: URL)] { [] }
}

func fluidAudioModelsDirectory() -> URL {
    FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        .appendingPathComponent("FluidAudio", isDirectory: true)
        .appendingPathComponent("Models", isDirectory: true)
}

func modelCacheDirectory(for repo: Repo) -> URL {
    fluidAudioModelsDirectory().appendingPathComponent(repo.folderName, isDirectory: true)
}

func directoryLogicalBytes(at url: URL) -> UInt64 {
    let keys: Set<URLResourceKey> = [.isRegularFileKey, .fileSizeKey]
    guard let enumerator = FileManager.default.enumerator(
        at: url,
        includingPropertiesForKeys: Array(keys),
        options: [.skipsHiddenFiles]
    ) else { return 0 }
    var total: UInt64 = 0
    for case let fileURL as URL in enumerator {
        guard let values = try? fileURL.resourceValues(forKeys: keys),
              values.isRegularFile == true,
              let size = values.fileSize,
              size > 0
        else { continue }
        total += UInt64(size)
    }
    return total
}

// ----- Apple SpeechAnalyzer / DictationTranscriber ---------------------

#if compiler(>=6.2)
@available(macOS 26, *)
final class AppleBackend: ASRBackend {
    let name = "apple-SpeechAnalyzer"
    private var localeInstalled = false

    func prepare(warmupSamples: [Float]) async throws {
        // Apple's per-locale dictation model isn't preinstalled. If the
        // bundle hasn't been fetched for this locale yet, AssetInventory
        // hands us a request that wraps an actual download. Skipping
        // this step makes `transcriber.results` emit zero events and the
        // program 'succeeds' silently with an empty transcript.
        let template = makeTranscriber()
        let installed = await DictationTranscriber.installedLocales
        let target = Locale(identifier: "en-US")
        let hasIt = installed.contains { $0.identifier(.bcp47) == target.identifier(.bcp47) }
        if !hasIt {
            log("  DictationTranscriber en-US not installed — requesting download…")
            if let request = try await AssetInventory.assetInstallationRequest(
                supporting: [template]
            ) {
                try await request.downloadAndInstall()
                log("  download + install complete")
            } else {
                log("  no install request returned — assuming locale is available")
            }
        }
        localeInstalled = true

        // First inference loads the model into the ANE; subsequent ones
        // are warm. Run a warmup so measured runs reflect steady-state.
        _ = try await transcribe(samples: warmupSamples)
    }

    func run(samples: [Float]) async throws -> (text: String, elapsed: Double) {
        let t0 = Date()
        let text = try await transcribe(samples: samples)
        return (text, Date().timeIntervalSince(t0))
    }

    private func makeTranscriber() -> DictationTranscriber {
        // DictationTranscriber is the dictation-focused module (auto-
        // punctuation, sentence structure), which matches Presspeech's
        // workload. SpeechTranscriber is the raw-words sibling for
        // command-recognition use cases — wrong fit here.
        DictationTranscriber(
            locale: Locale(identifier: "en-US"),
            contentHints: [.shortForm],
            transcriptionOptions: [],
            reportingOptions: [],
            attributeOptions: []
        )
    }

    private func transcribe(samples: [Float]) async throws -> String {
        // `SpeechAnalyzer.finalizeAndFinishThroughEndOfInput()` puts the
        // analyzer (and the modules attached to it) into a terminal
        // state — you cannot push more audio afterwards. For push-to-
        // talk style benchmarks (and Presspeech's real-world usage) the
        // canonical pattern is therefore a fresh analyzer+transcriber
        // per utterance, mirroring `TdtDecoderState()` on the fluid
        // side.
        let transcriber = makeTranscriber()
        let analyzer = SpeechAnalyzer(modules: [transcriber])

        // Drain `transcriber.results` in a child task that starts BEFORE
        // `analyzer.start(...)`. Reading results sequentially after
        // finalize loses events on at least DictationTranscriber: the
        // module appears to discard pending results once the analyzer
        // hits its terminal state, so by the time we'd loop the stream
        // is empty. swift-scribe uses the same parallel pattern.
        let collected = Task<String, Error> {
            // SpeechAnalyzer/DictationTranscriber semantics:
            //   - `isFinal == true`  → committed text, append to finalized
            //   - `isFinal == false` → volatile preview, replace
            // For a single-shot push-to-talk utterance DictationTranscriber
            // tends to emit the entire transcript in one volatile event and
            // never marks it final, so the user-visible result is
            // `finalized + volatile`, not just `finalized`.
            var finalized = ""
            var volatileText = ""
            for try await result in transcriber.results {
                let chunk = String(result.text.characters)
                if result.isFinal {
                    finalized += chunk
                    volatileText = ""
                } else {
                    volatileText = chunk
                }
            }
            return finalized + volatileText
        }

        let (stream, continuation) = AsyncStream<AnalyzerInput>.makeStream()
        try await analyzer.start(inputSequence: stream)

        let buffer = makePCMBuffer(samples: samples)
        continuation.yield(AnalyzerInput(buffer: buffer))
        continuation.finish()

        try await analyzer.finalizeAndFinishThroughEndOfInput()

        return try await collected.value.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func makePCMBuffer(samples: [Float]) -> AVAudioPCMBuffer {
        // Speech.framework's DictationTranscriber rejects Float32 audio
        // with "Failed precondition: Audio sample data must be 16-bit
        // signed integers" — convert in [-1, 1] floats to clamped Int16
        // here so the analyzer gets the format it actually wants.
        let format = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: 16_000,
            channels: 1,
            interleaved: false
        )!
        let buf = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(samples.count))!
        buf.frameLength = AVAudioFrameCount(samples.count)
        let dst = buf.int16ChannelData!.pointee
        for i in 0..<samples.count {
            let clamped = max(-1.0, min(1.0, samples[i]))
            dst[i] = Int16(clamped * 32767.0)
        }
        return buf
    }
}
#endif

// ----- FluidAudio (Parakeet → CoreML → ANE) -----------------------------
//
// One class, two model versions: TDT v3 (0.6B, 25-language default) and
// TDT-CTC 110M (smaller English-focused model with a fused
// preprocessor+encoder). Both load through the same `AsrModels` /
// `AsrManager` API and both decode through `TdtDecoderState` — 110M is a
// hybrid TDT-CTC, so the TDT decoder path applies unchanged.

final class FluidBackend: ASRBackend {
    let name: String
    private let version: AsrModelVersion
    private let language: Language?
    private var asr: AsrManager!

    var modelCacheComponents: [(label: String, url: URL)] {
        switch version {
        case .v3:
            return [("parakeet-v3", modelCacheDirectory(for: .parakeetV3))]
        case .tdtCtc110m:
            return [("tdt-ctc-110m", modelCacheDirectory(for: .parakeetTdtCtc110m))]
        default:
            return []
        }
    }

    init(name: String, version: AsrModelVersion, language: Language? = nil) {
        self.name = name
        self.version = version
        self.language = language
    }

    func prepare(warmupSamples: [Float]) async throws {
        // First call downloads the CoreML weights to
        // ~/Library/Application Support/FluidAudio/ unless cached (v3 is
        // ~600 MB; 110M is smaller). `AsrManager.init` takes the loaded
        // models directly; no separate configure step.
        let models = try await AsrModels.downloadAndLoad(version: version)
        asr = AsrManager(config: .default, models: models)
        _ = try await run(samples: warmupSamples)
    }

    func run(samples: [Float]) async throws -> (text: String, elapsed: Double) {
        // TDT keeps decoder state (LSTM hidden + last-token) across
        // streaming chunks. For a single isolated utterance the
        // canonical pattern is a fresh state per transcribe call,
        // matching what Presspeech's push-to-talk usage looks like.
        var state = try TdtDecoderState()
        let t0 = Date()
        let result = try await asr.transcribe(samples, decoderState: &state, language: language)
        return (result.text, Date().timeIntervalSince(t0))
    }
}

// ----- Production v3 + vocabulary rescoring ----------------------------
//
// Keep the production AsrManager path unchanged, then run FluidAudio's shared
// auxiliary CTC session against the ASR result and its token timings. This is
// the exact architecture a Presspeech vocabulary beta would use; benchmarking
// it separately avoids attributing sliding-window engine changes to boosting.

enum VocabularyPolicy {
    case standard
    case conservative
    case noSpotterRescue

    var config: VocabularyRescorer.Config? {
        switch self {
        case .standard:
            return nil
        case .conservative:
            return VocabularyRescorer.Config(
                shortTermCbwTaperPivot: 5,
                spotterRescueMinSimilarity: 0.30,
                spotterRescueMultiWordMinSimilarity: 0.50
            )
        case .noSpotterRescue:
            return VocabularyRescorer.Config(spotterRescueEnabled: false)
        }
    }
}

final class DirectVocabularyBackend: ASRBackend {
    let name: String
    private let language: Language?
    private let customVocabularyURL: URL
    private let vocabularyPolicy: VocabularyPolicy
    private var asr: AsrManager!
    private var boosting: VocabularyBoostingSession!

    var modelCacheComponents: [(label: String, url: URL)] {
        [
            ("parakeet-v3", modelCacheDirectory(for: .parakeetV3)),
            ("ctc-110m", CtcModels.defaultCacheDirectory()),
        ]
    }

    init(language: Language?, customVocabularyURL: URL, vocabularyPolicy: VocabularyPolicy) {
        self.language = language
        self.customVocabularyURL = customVocabularyURL
        self.vocabularyPolicy = vocabularyPolicy
        switch vocabularyPolicy {
        case .standard:
            self.name = "fluid-ParakeetTDTv3+Vocabulary"
        case .conservative:
            self.name = "fluid-ParakeetTDTv3+VocabularyConservative"
        case .noSpotterRescue:
            self.name = "fluid-ParakeetTDTv3+VocabularyNoRescue"
        }
    }

    func prepare(warmupSamples: [Float]) async throws {
        let models = try await AsrModels.downloadAndLoad(version: .v3)
        asr = AsrManager(config: .default, models: models)
        let loaded = try await CustomVocabularyContext.loadWithCtcTokens(
            from: customVocabularyURL.path
        )
        guard !loaded.vocab.terms.isEmpty else {
            throw NSError(
                domain: "presspeech-bench",
                code: 4,
                userInfo: [NSLocalizedDescriptionKey: "custom vocabulary contains no usable terms"]
            )
        }
        boosting = try await VocabularyBoostingSession(
            vocabulary: loaded.vocab,
            ctcModels: loaded.models,
            config: vocabularyPolicy.config
        )
        log("  custom vocabulary ready (\(loaded.vocab.terms.count) terms; content redacted)")
        _ = try await run(samples: warmupSamples)
    }

    func run(samples: [Float]) async throws -> (text: String, elapsed: Double) {
        var state = try TdtDecoderState()
        let t0 = Date()
        let result = try await asr.transcribe(samples, decoderState: &state, language: language)
        let text: String
        if let timings = result.tokenTimings,
           let rescored = await boosting.rescore(
               text: result.text,
               tokenTimings: timings,
               audioSamples: samples
           ) {
            text = rescored.text
        } else {
            text = result.text
        }
        return (text, Date().timeIntervalSince(t0))
    }
}

// ----- FluidAudio sliding-window Parakeet v3 + vocabulary rescoring ------
//
// FluidAudio exposes custom vocabulary on the sliding-window manager rather
// than the production app's direct AsrManager call. The unbiased variant is
// intentionally benchmarked alongside the vocabulary variant so changes from
// the engine path are not mistaken for vocabulary gains.

final class SlidingWindowBackend: ASRBackend {
    let name: String
    private let language: Language?
    private let customVocabularyURL: URL?
    private let vocabularyPolicy: VocabularyPolicy
    private var models: AsrModels!
    private var vocabulary: CustomVocabularyContext?
    private var ctcModels: CtcModels?

    var modelCacheComponents: [(label: String, url: URL)] {
        var components = [("parakeet-v3", modelCacheDirectory(for: .parakeetV3))]
        if customVocabularyURL != nil {
            components.append(("ctc-110m", CtcModels.defaultCacheDirectory()))
        }
        return components
    }

    init(
        language: Language?,
        customVocabularyURL: URL?,
        vocabularyPolicy: VocabularyPolicy = .standard
    ) {
        self.language = language
        self.customVocabularyURL = customVocabularyURL
        self.vocabularyPolicy = vocabularyPolicy
        if customVocabularyURL == nil {
            self.name = "fluid-ParakeetTDTv3Sliding"
        } else {
            switch vocabularyPolicy {
            case .standard:
                self.name = "fluid-ParakeetTDTv3Sliding+Vocabulary"
            case .conservative:
                self.name = "fluid-ParakeetTDTv3Sliding+VocabularyConservative"
            case .noSpotterRescue:
                self.name = "fluid-ParakeetTDTv3Sliding+VocabularyNoRescue"
            }
        }
    }

    func prepare(warmupSamples: [Float]) async throws {
        models = try await AsrModels.downloadAndLoad(version: .v3)
        if let customVocabularyURL {
            let loaded = try await CustomVocabularyContext.loadWithCtcTokens(
                from: customVocabularyURL.path
            )
            guard !loaded.vocab.terms.isEmpty else {
                throw NSError(
                    domain: "presspeech-bench",
                    code: 4,
                    userInfo: [NSLocalizedDescriptionKey: "custom vocabulary contains no usable terms"]
                )
            }
            vocabulary = loaded.vocab
            ctcModels = loaded.models
            log("  custom vocabulary ready (\(loaded.vocab.terms.count) terms; content redacted)")
        }
        _ = try await run(samples: warmupSamples)
    }

    func run(samples: [Float]) async throws -> (text: String, elapsed: Double) {
        let config = SlidingWindowAsrConfig.default.applying(language: language)
        let manager = SlidingWindowAsrManager(config: config)
        try await manager.loadModels(models)
        if let vocabulary, let ctcModels {
            try await manager.configureVocabularyBoosting(
                vocabulary: vocabulary,
                ctcModels: ctcModels,
                config: vocabularyPolicy.config
            )
        }
        try await manager.startStreaming()

        let buffer = makeFloatPCMBuffer(samples: samples)
        let t0 = Date()
        await manager.streamAudio(buffer)
        let text = try await manager.finish()
        let elapsed = Date().timeIntervalSince(t0)
        await manager.cleanup()
        return (text.trimmingCharacters(in: .whitespacesAndNewlines), elapsed)
    }
}

// ----- FluidAudio Unified (Parakeet Unified → CoreML → ANE) -------------
//
// English-only candidate backend. This is not a drop-in replacement for
// Presspeech's production v3 path because it uses FluidAudio's Unified manager
// instead of `AsrModels` / `AsrManager`, but the benchmark interface is the
// same: one complete push-to-talk utterance in, one transcript out.

final class UnifiedBatchBackend: ASRBackend {
    let name = "fluid-ParakeetUnifiedBatch"
    private let trailingSilenceSeconds: Double
    private var asr: UnifiedAsrManager!

    init(trailingSilenceMs: Int) {
        self.trailingSilenceSeconds = Double(trailingSilenceMs) / 1000.0
    }

    func prepare(warmupSamples: [Float]) async throws {
        asr = UnifiedAsrManager()
        try await asr.loadModels()
        _ = try await run(samples: warmupSamples)
    }

    func run(samples: [Float]) async throws -> (text: String, elapsed: Double) {
        try await asr.reset()
        let paddedSamples = samplesAppendingTrailingSilence(
            samples,
            seconds: trailingSilenceSeconds
        )
        let t0 = Date()
        let text = try await asr.transcribe(paddedSamples)
        return (text, Date().timeIntervalSince(t0))
    }
}

// ----- FluidAudio Nemotron Speech Streaming (English → CoreML → ANE) ----
//
// English-only candidate backend. Nemotron is a streaming model, but for
// Presspeech-style push-to-talk benchmarking we feed the full utterance and then
// finish the stream, which exercises the same final transcript path users
// would care about.

final class NemotronEnglishBackend: ASRBackend {
    let name = "fluid-NemotronEnglish1120"
    private var asr: StreamingNemotronAsrManager!

    func prepare(warmupSamples: [Float]) async throws {
        asr = StreamingNemotronAsrManager(requestedChunkSize: .ms1120)
        try await asr.loadModels()
        _ = try await run(samples: warmupSamples)
    }

    func run(samples: [Float]) async throws -> (text: String, elapsed: Double) {
        await asr.reset()
        let buffer = makeFloatPCMBuffer(samples: samples)
        let t0 = Date()
        _ = try await asr.process(audioBuffer: buffer)
        let text = try await asr.finish()
        return (text.trimmingCharacters(in: .whitespacesAndNewlines),
                Date().timeIntervalSince(t0))
    }
}

// ----- FluidAudio Nemotron 3.5 Multilingual (CoreML → ANE) -------------
//
// Prompt-conditioned multilingual candidate. The model repository contains
// separate vocabulary/tier variants, so both the language and chunk size are
// explicit benchmark inputs instead of hidden global defaults.

final class NemotronMultilingualBackend: ASRBackend {
    let name: String
    private let language: String
    private let chunkMs: Int
    private var asr: StreamingNemotronMultilingualAsrManager!

    init(language: String, chunkMs: Int) {
        self.language = language
        self.chunkMs = chunkMs
        self.name = "fluid-Nemotron3.5Multilingual-\(language)-\(chunkMs)"
    }

    func prepare(warmupSamples: [Float]) async throws {
        let shared = try await StreamingNemotronMultilingualAsrManager.downloadAndPreloadShared(
            languageCode: language,
            chunkMs: chunkMs
        )
        asr = StreamingNemotronMultilingualAsrManager()
        try await asr.loadFromShared(shared)
        await asr.setLanguage(language)
        _ = try await run(samples: warmupSamples)
    }

    func run(samples: [Float]) async throws -> (text: String, elapsed: Double) {
        await asr.reset()
        await asr.setLanguage(language)
        let t0 = Date()
        _ = try await asr.process(samples: samples)
        let text = try await asr.finish()
        return (text.trimmingCharacters(in: .whitespacesAndNewlines),
                Date().timeIntervalSince(t0))
    }
}

func makeFloatPCMBuffer(samples: [Float]) -> AVAudioPCMBuffer {
    let format = AVAudioFormat(
        commonFormat: .pcmFormatFloat32,
        sampleRate: BENCH_SAMPLE_RATE,
        channels: 1,
        interleaved: false
    )!
    let buf = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(samples.count))!
    buf.frameLength = AVAudioFrameCount(samples.count)
    let dst = buf.floatChannelData!.pointee
    dst.update(from: samples, count: samples.count)
    return buf
}

func samplesAppendingTrailingSilence(_ samples: [Float],
                                     seconds: Double,
                                     sampleRate: Double = BENCH_SAMPLE_RATE) -> [Float] {
    guard seconds > 0, sampleRate > 0, !samples.isEmpty else { return samples }
    let silenceSampleCount = Int((seconds * sampleRate).rounded())
    guard silenceSampleCount > 0 else { return samples }
    return samples + Array(repeating: 0, count: silenceSampleCount)
}

// MARK: - Word error rate
//
// Standard word-level WER: edit distance between normalized token
// streams, divided by reference word count. Normalization lowercases and
// strips punctuation but does NOT do inverse text normalization, so a
// model emitting "16" against a reference of "sixteen" counts as an error
// — fine for a relative v3-vs-110m comparison on the same references, but
// keep it in mind when reading absolute numbers (and note the TTS clips
// are "too clean" to stand in for real dictation).

func werTokens(_ s: String) -> [String] {
    // Normalize before inspecting individual scalars. Otherwise a decomposed
    // letter such as "n" + COMBINING ACUTE ACCENT loses its mark as
    // punctuation while the canonically equivalent precomposed "ń" remains
    // intact, producing false WER and critical-term misses.
    let lowered = s.lowercased().precomposedStringWithCanonicalMapping
    let kept = lowered.unicodeScalars.map { scalar -> Character in
        CharacterSet.alphanumerics.contains(scalar) ? Character(scalar) : " "
    }
    return String(kept).split(separator: " ").map(String.init)
}

func wordEditDistance(_ ref: [String], _ hyp: [String]) -> Int {
    let n = ref.count, m = hyp.count
    if n == 0 { return m }
    if m == 0 { return n }
    var prev = Array(0...m)
    var curr = [Int](repeating: 0, count: m + 1)
    for i in 1...n {
        curr[0] = i
        for j in 1...m {
            let cost = ref[i - 1] == hyp[j - 1] ? 0 : 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        }
        swap(&prev, &curr)
    }
    return prev[m]
}

struct WordErrorScore {
    let errors: Int
    let referenceWords: Int

    var percent: Double {
        guard referenceWords > 0 else { return errors == 0 ? 0 : 100 }
        return Double(errors) / Double(referenceWords) * 100
    }
}

func wordErrorScore(reference: String, hypothesis: String) -> WordErrorScore {
    let ref = werTokens(reference)
    let hyp = werTokens(hypothesis)
    return WordErrorScore(
        errors: wordEditDistance(ref, hyp),
        referenceWords: ref.count
    )
}

struct CriticalTermScore {
    let matched: Int
    let total: Int
    let unexpected: Int

    var recallPercent: Double {
        guard total > 0 else { return 100 }
        return Double(matched) / Double(total) * 100
    }
}

func phraseOccurrenceCount(_ phrase: [String], in words: [String]) -> Int {
    guard !phrase.isEmpty, phrase.count <= words.count else { return 0 }
    var count = 0
    for start in 0...(words.count - phrase.count) {
        if Array(words[start..<(start + phrase.count)]) == phrase {
            count += 1
        }
    }
    return count
}

func criticalTermScore(reference: String,
                       hypothesis: String,
                       terms: [String]) -> CriticalTermScore {
    let referenceWords = werTokens(reference)
    let hypothesisWords = werTokens(hypothesis)
    var matched = 0
    var total = 0
    var unexpected = 0
    for term in terms {
        let phrase = werTokens(term)
        guard !phrase.isEmpty else { continue }
        let expected = phraseOccurrenceCount(phrase, in: referenceWords)
        let actual = phraseOccurrenceCount(phrase, in: hypothesisWords)
        total += expected
        matched += min(expected, actual)
        unexpected += max(0, actual - expected)
    }
    return CriticalTermScore(
        matched: matched,
        total: total,
        unexpected: unexpected
    )
}

enum CriticalTermsFileError: LocalizedError {
    case emptyNormalizedTerm(entry: Int)
    case duplicateNormalizedTerm(entry: Int, firstEntry: Int)

    var errorDescription: String? {
        switch self {
        case .emptyNormalizedTerm(let entry):
            return "critical-terms entry \(entry) is empty after normalization"
        case .duplicateNormalizedTerm(let entry, let firstEntry):
            return "critical-terms entry \(entry) duplicates normalized entry \(firstEntry)"
        }
    }
}

func parseCriticalTerms(_ contents: String) throws -> [String] {
    let terms = contents.split(whereSeparator: \.isNewline).compactMap { line in
        let value = String(line).trimmingCharacters(in: .whitespacesAndNewlines)
        return value.isEmpty || value.hasPrefix("#") ? nil : value
    }
    var normalizedEntries: [String: Int] = [:]
    for (offset, term) in terms.enumerated() {
        let entry = offset + 1
        let tokens = werTokens(term)
        guard !tokens.isEmpty else {
            throw CriticalTermsFileError.emptyNormalizedTerm(entry: entry)
        }
        // Scoring is performed on normalized token phrases. Reject entries
        // that become identical under that same normalization; otherwise a
        // single occurrence is silently counted more than once in recall and
        // unexpected-insertion totals.
        let key = tokens.joined(separator: "\u{0}")
        if let firstEntry = normalizedEntries[key] {
            throw CriticalTermsFileError.duplicateNormalizedTerm(
                entry: entry,
                firstEntry: firstEntry
            )
        }
        normalizedEntries[key] = entry
    }
    return terms
}

func loadCriticalTerms(from url: URL) throws -> [String] {
    let contents = try String(contentsOf: url, encoding: .utf8)
    return try parseCriticalTerms(contents)
}

enum BenchSelfTestError: Error {
    case failed(String)
}

func runBenchSelfTests() throws {
    func expect(_ condition: @autoclosure () -> Bool, _ message: String) throws {
        guard condition() else { throw BenchSelfTestError.failed(message) }
    }

    try expect(positiveTrialCount("3") == 3, "trial parser should accept positive integers")
    try expect(positiveTrialCount("0") == nil, "trial parser should reject zero")
    try expect(positiveTrialCount("-1") == nil, "trial parser should reject negative integers")
    try expect(positiveTrialCount("three") == nil, "trial parser should reject non-integers")
    try expect(
        VocabularyPolicy.standard.config == nil,
        "standard vocabulary policy should preserve FluidAudio defaults"
    )
    let conservativeConfig = VocabularyPolicy.conservative.config
    try expect(
        conservativeConfig?.shortTermCbwTaperPivot == 5 &&
            conservativeConfig?.spotterRescueMinSimilarity == 0.30 &&
            conservativeConfig?.spotterRescueMultiWordMinSimilarity == 0.50 &&
            conservativeConfig?.spotterRescueEnabled == true,
        "conservative vocabulary policy should apply only taper and similarity floors"
    )
    try expect(
        VocabularyPolicy.noSpotterRescue.config?.spotterRescueEnabled == false,
        "no-rescue vocabulary policy should disable acoustic-only rescue"
    )

    try expect(
        phraseOccurrenceCount(["szypańskim"], in: ["ze", "szypańskim", "i", "szypańskim"]) == 2,
        "phrase occurrence count should preserve Polish diacritics"
    )
    let decomposedName = "Szypańskim".decomposedStringWithCanonicalMapping
    try expect(
        werTokens(decomposedName) == werTokens("Szypańskim"),
        "token normalization should treat canonically equivalent diacritics equally"
    )
    let canonicalScore = criticalTermScore(
        reference: "Rozmawiałem z Szypańskim.",
        hypothesis: "Rozmawiałem z \(decomposedName).",
        terms: ["Szypańskim"]
    )
    try expect(
        canonicalScore.matched == 1 && canonicalScore.total == 1,
        "critical-term scoring should match decomposed model output"
    )
    let uniqueTerms = try parseCriticalTerms("# private terms\nSzypański\nNowy Sącz\n")
    try expect(uniqueTerms.count == 2, "critical-term parser should ignore comments")
    var rejectedDuplicate = false
    do {
        _ = try parseCriticalTerms("Szypański\nszypan\u{301}ski!\n")
    } catch CriticalTermsFileError.duplicateNormalizedTerm(let entry, let firstEntry) {
        rejectedDuplicate = entry == 2 && firstEntry == 1
    }
    try expect(
        rejectedDuplicate,
        "critical-term parser should reject canonically equivalent duplicates"
    )
    var rejectedEmpty = false
    do {
        _ = try parseCriticalTerms("---\n")
    } catch CriticalTermsFileError.emptyNormalizedTerm(let entry) {
        rejectedEmpty = entry == 1
    }
    try expect(rejectedEmpty, "critical-term parser should reject punctuation-only entries")
    let score = criticalTermScore(
        reference: "Rozmawiałem z Szypańskim i Szypańskim.",
        hypothesis: "Rozmawiałem z Szypańskim, Szymańskim i Nieobecny.",
        terms: ["Szypańskim", "Nieobecny"]
    )
    try expect(score.matched == 1, "critical-term recall should count matched occurrences")
    try expect(score.total == 2, "critical-term recall should count only terms present in reference")
    try expect(abs(score.recallPercent - 50) < 0.001, "critical-term recall percentage should be weighted")
    try expect(score.unexpected == 1, "critical-term scoring should count insertions absent from the reference")
    let wordErrors = wordErrorScore(
        reference: "one two three four",
        hypothesis: "one too three"
    )
    try expect(wordErrors.errors == 2, "WER should expose edit-error counts for corpus aggregation")
    try expect(wordErrors.referenceWords == 4, "WER should expose reference-word counts for corpus aggregation")
    try expect(abs(wordErrors.percent - 50) < 0.001, "WER percentage should derive from the exact counts")

    print("presspeech-bench self-test passed")
}

func finalWordRetention(reference: String, hypothesis: String) -> (retained: Bool, expected: String, actualLast: String?)? {
    guard let expected = werTokens(reference).last else { return nil }
    let actualLast = werTokens(hypothesis).last
    return (actualLast == expected, expected, actualLast)
}

// MARK: - Memory
//
// `phys_footprint` is what Activity Monitor reports as "Memory" and is the
// closest single number to a model's resident cost. It does not capture
// everything the ANE allocates out of process, so treat it as a
// comparative signal between models, not an absolute RAM ceiling.

func footprintBytes() -> UInt64 {
    var info = task_vm_info_data_t()
    var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<integer_t>.size)
    let kr = withUnsafeMutablePointer(to: &info) {
        $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
            task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
        }
    }
    return kr == KERN_SUCCESS ? info.phys_footprint : 0
}

func fmtMB(_ bytes: UInt64) -> String { String(format: "%6.1f MB", Double(bytes) / (1024 * 1024)) }

// MARK: - Bench harness

struct TrialResult {
    let elapsed: Double
    let text: String
}

func percentile(_ values: [Double], _ p: Double) -> Double {
    guard !values.isEmpty else { return 0 }
    let sorted = values.sorted()
    let idx = max(0, min(sorted.count - 1, Int(Double(sorted.count - 1) * p)))
    return sorted[idx]
}

func fmtMs(_ s: Double) -> String { String(format: "%7.1f ms", s * 1000) }

func runBackend(_ backend: ASRBackend, samples: [Float], trials: Int) async throws -> (results: [TrialResult], peak: UInt64) {
    var out: [TrialResult] = []
    var peak = footprintBytes()
    for i in 0..<trials {
        let (text, t) = try await backend.run(samples: samples)
        out.append(TrialResult(elapsed: t, text: text))
        peak = max(peak, footprintBytes())
        FileHandle.standardError.write(Data("    \(backend.name) trial \(i+1)/\(trials): \(fmtMs(t))\n".utf8))
    }
    return (out, peak)
}

func redactedTextLabel(_ text: String) -> String {
    "<redacted \(text.count) chars>"
}

func summarize(_ name: String,
               _ results: [TrialResult],
               reference: String?,
               baseline: UInt64,
               peak: UInt64,
               redactTranscripts: Bool,
               criticalTerms: [String]) {
    let times = results.map(\.elapsed)
    let p50 = percentile(times, 0.5)
    let mn = times.min() ?? 0
    let mx = times.max() ?? 0
    let texts = Set(results.map(\.text))
    let delta = peak >= baseline ? peak - baseline : 0
    print("")
    print("  \(name)")
    print("    latency:  p50=\(fmtMs(p50))  min=\(fmtMs(mn))  max=\(fmtMs(mx))")
    print("    memory:   peak=\(fmtMB(peak))  Δ-from-start=\(fmtMB(delta))")
    func wordErrorTags(_ text: String) -> (wer: String, counts: String) {
        guard let reference else { return ("", "") }
        let score = wordErrorScore(reference: reference, hypothesis: text)
        return (
            " [WER \(String(format: "%.1f%%", score.percent))]",
            " [word-errors=\(score.errors) reference-words=\(score.referenceWords)]"
        )
    }
    func finalWordTag(_ text: String) -> String {
        guard let reference,
              let retention = finalWordRetention(reference: reference, hypothesis: text)
        else { return "" }
        if redactTranscripts {
            return " [final-word retained=\(retention.retained)]"
        }
        let actualLast = retention.actualLast ?? "<none>"
        return " [final-word retained=\(retention.retained) expected=\"\(retention.expected)\" actual-last=\"\(actualLast)\"]"
    }
    func criticalTermTag(_ text: String) -> String {
        guard let reference, !criticalTerms.isEmpty else { return "" }
        let score = criticalTermScore(
            reference: reference,
            hypothesis: text,
            terms: criticalTerms
        )
        return " [critical-terms matched=\(score.matched) total=\(score.total) recall=\(String(format: "%.1f%%", score.recallPercent)) unexpected=\(score.unexpected)]"
    }
    if texts.count == 1, let only = texts.first {
        let display = redactTranscripts ? redactedTextLabel(only) : "\"\(only)\""
        let wordErrors = wordErrorTags(only)
        print("    transcript:\(wordErrors.wer)\(finalWordTag(only))\(criticalTermTag(only))\(wordErrors.counts) \(display)")
    } else {
        print("    transcripts (\(texts.count) distinct):")
        for t in texts.sorted() {
            let display = redactTranscripts ? redactedTextLabel(t) : "\"\(t)\""
            let wordErrors = wordErrorTags(t)
            print("      •\(wordErrors.wer)\(finalWordTag(t))\(criticalTermTag(t))\(wordErrors.counts) \(display)")
        }
    }
}

// MARK: - Main

@main
struct PresspeechBench {
    static func main() async throws {
        if CommandLine.arguments.dropFirst() == ["--self-test"] {
            try runBenchSelfTests()
            return
        }
        let args = parseArgs()

        let vocabularyBackends = [
            "v3-vocab",
            "v3-vocab-conservative",
            "v3-vocab-no-rescue",
            "sliding-vocab",
            "sliding-vocab-conservative",
            "sliding-vocab-no-rescue",
        ]
        if vocabularyBackends.contains(args.backend), args.customVocabulary == nil {
            FileHandle.standardError.write(Data("--backend \(args.backend) requires --custom-vocabulary\n".utf8))
            exit(2)
        }
        if !vocabularyBackends.contains(args.backend), args.customVocabulary != nil {
            FileHandle.standardError.write(Data("--custom-vocabulary is valid only with a vocabulary backend\n".utf8))
            exit(2)
        }

        var runSummary = "presspeech-bench: \(args.file.lastPathComponent), \(args.trials) trials, backend=\(args.backend)"
        if args.backend == "unified" || args.backend == "fluid" || args.backend == "both" {
            runSummary += ", unified-trailing-silence-ms=\(args.unifiedTrailingSilenceMs)"
        }
        if args.backend == "nemotron-multilingual" || args.backend == "fluid" || args.backend == "both" {
            runSummary += ", nemotron-multilingual-language=\(args.nemotronMultilingualLanguage)"
            runSummary += ", nemotron-multilingual-chunk-ms=\(args.nemotronMultilingualChunkMs)"
        }
        if args.backend == "v3" || args.backend == "sliding-v3" || vocabularyBackends.contains(args.backend) {
            runSummary += ", language=\(args.language?.rawValue ?? "auto")"
        }
        if vocabularyBackends.contains(args.backend) {
            runSummary += ", custom-vocabulary=enabled"
        }
        if args.backend == "v3-vocab-conservative" || args.backend == "sliding-vocab-conservative" {
            runSummary += ", vocabulary-policy=conservative"
        }
        if args.backend == "v3-vocab-no-rescue" || args.backend == "sliding-vocab-no-rescue" {
            runSummary += ", vocabulary-policy=no-spotter-rescue"
        }
        log(runSummary)
        let samples = try load16kMono(url: args.file)
        let durSec = Double(samples.count) / 16_000
        log("audio: \(samples.count) samples (~\(String(format: "%.2f", durSec)) s @ 16 kHz mono)")

        // Reference for WER: explicit --ref wins, else a sibling
        // "<stem>.txt" (written by generate-test-audio.sh).
        let reference: String? = {
            if let r = args.ref { return r }
            let sidecar = args.file.deletingPathExtension().appendingPathExtension("txt")
            if let text = try? String(contentsOf: sidecar, encoding: .utf8) {
                return text.trimmingCharacters(in: .whitespacesAndNewlines)
            }
            return nil
        }()
        if let reference {
            let display = args.redactTranscripts ? redactedTextLabel(reference) : "\"\(reference)\""
            log("reference: \(display)")
        } else {
            log("no reference (--ref or <file>.txt) — WER skipped")
        }
        let criticalTerms: [String]
        if let criticalTermsURL = args.criticalTerms {
            criticalTerms = try loadCriticalTerms(from: criticalTermsURL)
            guard !criticalTerms.isEmpty else {
                FileHandle.standardError.write(Data("--critical-terms contains no usable terms\n".utf8))
                exit(2)
            }
            log("critical terms: \(criticalTerms.count) canonical forms (content redacted)")
        } else {
            criticalTerms = []
        }

        // Use the same audio for warmup — it's the most representative
        // "first inference" for the same shape we'll measure.
        let warmup = samples

        let known = [
            "apple", "v3", "v3-vocab", "v3-vocab-conservative", "v3-vocab-no-rescue",
            "sliding-v3", "sliding-vocab", "sliding-vocab-conservative",
            "sliding-vocab-no-rescue", "unified",
            "nemotron-en", "nemotron-multilingual",
            "110m", "fluid", "both",
        ]
        guard known.contains(args.backend) else {
            FileHandle.standardError.write(Data("unknown --backend \"\(args.backend)\" (expected \(known.joined(separator: "|")))\n".utf8))
            exit(2)
        }
        var backends: [ASRBackend] = []
        var failedBackends = 0
        if args.backend == "apple" || args.backend == "both" {
#if compiler(>=6.2)
            if #available(macOS 26, *) {
                backends.append(AppleBackend())
            } else {
                failedBackends += 1
                log("apple backend unavailable — requires macOS 26+")
            }
#else
            failedBackends += 1
            log("apple backend unavailable — requires the macOS 26 SDK and Swift 6.2+")
#endif
        }
        if args.backend == "v3" || args.backend == "fluid" || args.backend == "both" {
            backends.append(
                FluidBackend(
                    name: "fluid-ParakeetTDTv3",
                    version: .v3,
                    language: args.language
                )
            )
        }
        if args.backend == "v3-vocab" ||
            args.backend == "v3-vocab-conservative" ||
            args.backend == "v3-vocab-no-rescue" {
            let policy: VocabularyPolicy
            switch args.backend {
            case "v3-vocab-conservative":
                policy = .conservative
            case "v3-vocab-no-rescue":
                policy = .noSpotterRescue
            default:
                policy = .standard
            }
            backends.append(
                DirectVocabularyBackend(
                    language: args.language,
                    customVocabularyURL: args.customVocabulary!,
                    vocabularyPolicy: policy
                )
            )
        }
        if args.backend == "sliding-v3" {
            backends.append(
                SlidingWindowBackend(language: args.language, customVocabularyURL: nil)
            )
        }
        if args.backend == "sliding-vocab" {
            backends.append(
                SlidingWindowBackend(
                    language: args.language,
                    customVocabularyURL: args.customVocabulary
                )
            )
        }
        if args.backend == "sliding-vocab-conservative" {
            backends.append(
                SlidingWindowBackend(
                    language: args.language,
                    customVocabularyURL: args.customVocabulary,
                    vocabularyPolicy: .conservative
                )
            )
        }
        if args.backend == "sliding-vocab-no-rescue" {
            backends.append(
                SlidingWindowBackend(
                    language: args.language,
                    customVocabularyURL: args.customVocabulary,
                    vocabularyPolicy: .noSpotterRescue
                )
            )
        }
        if args.backend == "unified" || args.backend == "fluid" || args.backend == "both" {
            backends.append(UnifiedBatchBackend(trailingSilenceMs: args.unifiedTrailingSilenceMs))
        }
        if args.backend == "nemotron-en" || args.backend == "fluid" || args.backend == "both" {
            backends.append(NemotronEnglishBackend())
        }
        if args.backend == "nemotron-multilingual" || args.backend == "fluid" || args.backend == "both" {
            backends.append(
                NemotronMultilingualBackend(
                    language: args.nemotronMultilingualLanguage,
                    chunkMs: args.nemotronMultilingualChunkMs
                )
            )
        }
        if args.backend == "110m" || args.backend == "fluid" || args.backend == "both" {
            // Kept wired up but off the default path: as of the current
            // tested FluidAudio revision the 110m CoreML bundle won't load — missing
            // CtcHead.mlmodelc plus a decoder shape mismatch (2×1×640 vs
            // 1×1×640). prepare() records the failure and lets the remaining
            // backends run before the benchmark exits unsuccessfully.
            // Re-test with --backend 110m once it's fixed upstream.
            backends.append(FluidBackend(name: "fluid-ParakeetTDTCTC110M", version: .tdtCtc110m))
        }

        // Footprint before any model loads. Δ-from-start is only a clean
        // per-model cost when one backend runs per process; with several in
        // one run the earlier models stay resident (see --help).
        let baseline = footprintBytes()
        log("baseline footprint: \(fmtMB(baseline))")
        if backends.count > 1 {
            log("note: \(backends.count) backends in one process — memory is cumulative; run one --backend per process for clean per-model numbers")
        }

        for backend in backends {
            log("preparing \(backend.name)…")
            let prepT0 = Date()
            do {
                try await backend.prepare(warmupSamples: warmup)
            } catch {
                failedBackends += 1
                log("  prepare(\(backend.name)) FAILED: \(error)")
                continue
            }
            let prepDt = Date().timeIntervalSince(prepT0)
            log("  ready in \(fmtMs(prepDt)) (model load + 1 warmup inference)")
            let cacheComponents = backend.modelCacheComponents.map { component in
                (label: component.label, bytes: directoryLogicalBytes(at: component.url))
            }
            if !cacheComponents.isEmpty {
                let totalCacheBytes = cacheComponents.reduce(UInt64(0)) { $0 + $1.bytes }
                let detail = cacheComponents.map {
                    "\($0.label)=\(fmtMB($0.bytes).trimmingCharacters(in: .whitespaces))"
                }.joined(separator: ",")
                log("  model-cache: total=\(fmtMB(totalCacheBytes).trimmingCharacters(in: .whitespaces)) components=\(detail)")
            }

            do {
                let (results, peak) = try await runBackend(backend, samples: samples, trials: args.trials)
                summarize(backend.name,
                          results,
                          reference: reference,
                          baseline: baseline,
                          peak: peak,
                          redactTranscripts: args.redactTranscripts,
                          criticalTerms: criticalTerms)
            } catch {
                failedBackends += 1
                log("  run(\(backend.name)) FAILED: \(error)")
            }
        }
        if failedBackends > 0 {
            log("presspeech-bench: \(failedBackends) backend(s) failed")
            exit(1)
        }
    }
}

/// stderr write that flushes immediately — print(...) buffering eats
/// the last line before a crash, which made this benchmark feel
/// broken when it wasn't.
func log(_ s: String) {
    FileHandle.standardError.write(Data((s + "\n").utf8))
}
