// Presspeech — push-to-talk dictation for macOS Apple Silicon.
//
// Single-file Swift menu-bar app. The whole runtime lives in this
// file: hotkey capture (`CGEventTap`), audio capture
// (`AVAudioEngine`), transcription (`FluidAudio` on the Apple
// Neural Engine), paste-at-cursor (`NSPasteboard` + `CGEvent`),
// system-audio mute (`NSAppleScript`), menu-bar UI, settings,
// rolling history, in-app updater, TCC self-healing.
//
// Section comments (`// MARK: -`) tag every major region; Cmd+Ctrl+Up
// in Xcode jumps between them. Keep them honest as you edit.
//
// Architectural invariants the build relies on:
//   - `AudioCapture` is *not* @MainActor (AVAudioEngine tap fires on
//     an audio thread; main-actor entry would SIGTRAP under Swift 6
//     strict concurrency).
//   - `AVAudioConverter` inputBlock must return .noDataNow, never
//     .endOfStream — the latter puts the converter in a terminal
//     state and every press after the first captures silence.
//   - Resources are loaded via `Bundle.main`, never `Bundle.module`
//     — SwiftPM's auto-generated resource bundle has no Info.plist
//     and breaks `codesign --deep`.

import AppKit
import AVFoundation
import AudioToolbox
import Foundation
import CoreGraphics
import CryptoKit
import Darwin
import ApplicationServices
import FluidAudio
import IOKit
import QuartzCore
import ServiceManagement
import UniformTypeIdentifiers

// MARK: - Constants

let SAMPLE_RATE: Double = 16_000
let DEFAULT_HOTKEY_KEYCODE: CGKeyCode = 61  // Right Option
let ESCAPE_KEYCODE: CGKeyCode = 53
// Keep native modal-dialog navigation available while the hotkey recorder's
// local event monitor is active. These keys are not valid dictation hotkeys,
// but AppKit needs their keyDown events for Return/Enter, Tab, Space, and
// Escape to confirm, move focus, activate a focused button, or cancel.
let HOTKEY_RECORDER_CONTROL_KEYCODES: Set<CGKeyCode> = [36, 48, 49, 53, 76]
let MIN_CLIP_SECONDS: Double = 0.25
// A key release can arrive while the speaker is still finishing the last
// phoneme, and AVAudioEngine delivers microphone samples in buffers rather
// than synchronously with that key event. Keep capture armed briefly after
// release, stopping as soon as the new tail is quiet and imposing a hard
// bound when the user released mid-word. These values match the reviewed
// Windows push-to-talk policy so both apps protect the same capture boundary.
let POST_RELEASE_MIN_CAPTURE_SECONDS: TimeInterval = 0.08
let POST_RELEASE_MAX_CAPTURE_SECONDS: TimeInterval = 0.4
let POST_RELEASE_CAPTURE_CHECK_SECONDS: TimeInterval = 0.04
let POST_RELEASE_TAIL_SECONDS: TimeInterval = 0.09
let POST_RELEASE_ABSOLUTE_SILENCE_RMS = 0.003
let POST_RELEASE_RELATIVE_SILENCE = 0.06
let POST_RELEASE_MAX_SILENCE_RMS = 0.012
// A forgotten toggle must still stop, and even hand-written defaults
// cannot grow the in-memory capture without a bound.
let DEFAULT_MAX_RECORDING_SECONDS: TimeInterval = 120
let MIN_MAX_RECORDING_SECONDS: TimeInterval = 30
let MAX_MAX_RECORDING_SECONDS: TimeInterval = 600
// How long to wait after posting Cmd+V before restoring the user's
// previous clipboard. The paste event is delivered asynchronously, so
// the target app must be given a moment to read the transcript off the
// pasteboard before we write the old contents back over it. Quartz has
// no paste-consumed acknowledgement, which is why restoration remains
// an opt-in, best-effort behavior.
let CLIPBOARD_RESTORE_DELAY_SECONDS: TimeInterval = 0.4
let PASTE_TARGET_AX_TIMEOUT_SECONDS: Float = 0.25
let UPDATE_CHECK_FIRST_DELAY_SECONDS: TimeInterval = 30
let UPDATE_CHECK_INTERVAL_SECONDS: TimeInterval = 6 * 3600  // 6h
let UPDATE_REMIND_LATER_SECONDS: TimeInterval = 24 * 3600  // 24h
let GITHUB_LATEST_RELEASE_URL = URL(string: "https://api.github.com/repos/rcourtman/presspeech/releases/latest")!
let GITHUB_REPOSITORY_PAGE = URL(string: "https://github.com/rcourtman/presspeech")!
let GITHUB_RELEASES_PAGE = URL(string: "https://github.com/rcourtman/presspeech/releases/latest")!
let GITHUB_BUG_REPORT_PAGE = URL(string: "https://github.com/rcourtman/presspeech/issues/new?template=bug_report.yml")!
let GITHUB_FEATURE_REQUEST_PAGE = URL(string: "https://github.com/rcourtman/presspeech/issues/new?template=feature_request.yml")!
let HOMEBREW_CASK_TAP = "rcourtman/presspeech"
let HOMEBREW_CASK_TOKEN = "rcourtman/presspeech/presspeech"
let HOMEBREW_CASK_INSTALLED_TOKEN = "presspeech"
let INSTALLED_APP_BUNDLE_PATH = "/Applications/Presspeech.app"
let UPDATE_HELPER_LOG_PATH = (NSHomeDirectory() as NSString)
    .appendingPathComponent("Library/Logs/Presspeech-update.log")
let UPDATE_PROGRESS_ARGUMENT = "--update-progress"
let UPDATE_PROGRESS_APP_PREFIX = "Presspeech-update-progress-"
let MAX_SKIPPED_UPDATE_VERSIONS = 20
let MAX_CORRECTION_SYNC_PATH_BYTES = 4096
let MAX_INPUT_DEVICE_PREFERENCE_BYTES = 512
let DIAGNOSTICS_LOG_MAX_BYTES = 128 * 1024
let DIAGNOSTICS_LOG_MAX_LINES = 40
let DIAGNOSTICS_LOG_MAX_LINE_CHARACTERS = 4096
let RECORDING_HUD_EXPANDED_SIZE = NSSize(width: 232, height: 54)
let RECORDING_HUD_COLLAPSED_SIZE = NSSize(width: 58, height: 42)
let RECORDING_HUD_ANIMATE_IN_SECONDS: TimeInterval = 0.12
let RECORDING_HUD_ANIMATE_OUT_SECONDS: TimeInterval = 0.08
let RECORDING_HUD_BUSY_DELAY_SECONDS: TimeInterval = 0.25
let DICTATION_NOTICE_HUD_SECONDS: TimeInterval = 4
let DICTATION_ERROR_FLASH_SECONDS: TimeInterval = 1.5  // how long the menu-bar icon flags a dropped dictation before returning to idle
let AUDIO_START_RETRY_DELAYS_SECONDS: [UInt64] = [1, 3, 8]
let AUDIO_IDLE_STOP_DELAY_SECONDS: TimeInterval = 5
let AUDIO_CONFIGURATION_CHANGE_SUPPRESSION_SECONDS: TimeInterval = 1
let MODEL_DOWNLOAD_HEADROOM_BYTES: Int64 = 500 * 1024 * 1024

/// macOS can transiently report no screens during display sleep or
/// reconfiguration. In that state the optional HUD stays hidden and the
/// recording-level timer will place it once a screen becomes available.
func recordingHUDFrame(size: NSSize, visibleFrame: NSRect?) -> NSRect? {
    guard let visibleFrame else { return nil }
    return NSRect(x: visibleFrame.midX - (size.width / 2),
                  y: visibleFrame.minY + 84,
                  width: size.width,
                  height: size.height)
}

func setupChecklistWindowContentSize(ideal: NSSize = NSSize(width: 560, height: 620),
                                     visibleFrame: NSRect?) -> NSSize {
    guard let visibleFrame,
          visibleFrame.width > 0,
          visibleFrame.height > 0 else { return ideal }

    // Leave enough room to move and resize the window without putting its
    // controls under the menu bar, Dock, or camera housing. The checklist
    // itself scrolls when a display cannot accommodate the ideal height.
    let availableWidth = max(420, visibleFrame.width - 80)
    let availableHeight = max(320, visibleFrame.height - 80)
    return NSSize(width: min(ideal.width, availableWidth),
                  height: min(ideal.height, availableHeight))
}

/// Keep the user's place when the live setup checklist replaces its view tree.
/// AppKit resets a new scroll document to its origin, so permission/model state
/// refreshes otherwise jump a user on a short display back to the first row.
func restoredSetupChecklistScrollOriginY(previousOriginY: CGFloat,
                                         documentHeight: CGFloat,
                                         viewportHeight: CGFloat) -> CGFloat {
    guard previousOriginY.isFinite,
          documentHeight.isFinite,
          viewportHeight.isFinite else { return 0 }
    let maximumOriginY = max(0, documentHeight - max(0, viewportHeight))
    return min(max(0, previousOriginY), maximumOriginY)
}

/// A registered login item can still require the user's approval in System
/// Settings. Treating that state as "enabled" makes a click unregister the
/// pending request, leaving no path from Presspeech to the approval macOS is
/// asking for. Keep the status-to-intent mapping pure so the recovery behavior
/// is covered without mutating the machine's real login items in self-tests.
enum LaunchAtLoginAction: Equatable {
    case register
    case unregister
    case openSystemSettings
}

func launchAtLoginAction(for status: SMAppService.Status) -> LaunchAtLoginAction {
    switch status {
    case .enabled:
        return .unregister
    case .requiresApproval:
        return .openSystemSettings
    case .notRegistered, .notFound:
        return .register
    @unknown default:
        return .register
    }
}

let SETTINGS_SUITE = "com.local.presspeech"
let CORRECTIONS_FILE_UTI = "com.local.presspeech.corrections"
let CORRECTIONS_FILE_EXTENSION = "presspeech-corrections"
let CORRECTIONS_FILE_NAME = "Presspeech Corrections.\(CORRECTIONS_FILE_EXTENSION)"
let TRANSIENT_PASTEBOARD_TYPE = NSPasteboard.PasteboardType("org.nspasteboard.TransientType")
let AUTO_GENERATED_PASTEBOARD_TYPE = NSPasteboard.PasteboardType("org.nspasteboard.AutoGeneratedType")
let PASTEBOARD_SOURCE_TYPE = NSPasteboard.PasteboardType("org.nspasteboard.source")
// Migration-only identifiers. They let the first build with the
// Presspeech identity move existing user data, then delete the former
// defaults domain and support files. Do not use these for new state.
private let LEGACY_SETTINGS_SUITE = "com.local.parakey"
private let LEGACY_APPLICATION_SUPPORT_DIRECTORY_NAME = "Parakey"
private let LEGACY_CORRECTIONS_FILE_EXTENSION = "parakey-corrections"
let MAX_TRANSCRIPT_CORRECTIONS = 512
let MAX_TRANSCRIPT_CORRECTION_SOURCE_BYTES = 512
let MAX_TRANSCRIPT_CORRECTION_REPLACEMENT_BYTES = 4096
let MAX_INLINE_CORRECTION_MENU_ENTRIES = 20

/// Visible state of the menu-bar item. Idle/loading/busy use the
/// template image so macOS handles light/dark menu bars. Recording and
/// error use distinct SF Symbol silhouettes as well as pre-tinted static
/// frames, so neither important state is communicated by colour alone.
enum MenuBarState {
    case loading
    case idle
    case recording
    case busy
    case error
}

func menuBarAccessibilityValue(for state: MenuBarState) -> String {
    switch state {
    case .loading: return "Loading"
    case .idle: return "Ready"
    case .recording: return "Recording"
    case .busy: return "Transcribing"
    case .error: return "Needs attention"
    }
}

enum DictationNotice: Equatable {
    case copiedToClipboard
    case insertionFailed
    case insertionFailedWithoutHistory
    case transcriptionFailed
    case noSpeechDetected

    var statusTitle: String {
        switch self {
        case .copiedToClipboard:
            return "Transcript copied — press ⌘V to paste"
        case .insertionFailed:
            return "Couldn't paste — use Copy Last Transcript"
        case .insertionFailedWithoutHistory:
            return "Couldn't paste — try again"
        case .transcriptionFailed:
            return "Transcription failed — try again"
        case .noSpeechDetected:
            return "No speech detected — try again"
        }
    }

    var hudTitle: String {
        switch self {
        case .copiedToClipboard:
            return "Copied — press ⌘V to paste"
        case .insertionFailed:
            return "Couldn't paste — copy from menu"
        case .insertionFailedWithoutHistory:
            return "Couldn't paste — try again"
        case .transcriptionFailed:
            return "Transcription failed — try again"
        case .noSpeechDetected:
            return "No speech detected — try again"
        }
    }

    var accessibilityValue: String {
        switch self {
        case .copiedToClipboard:
            return "Transcript copied. Press Command V to paste."
        case .insertionFailed:
            return "Couldn't paste. Use Copy Last Transcript in the Presspeech menu."
        case .insertionFailedWithoutHistory:
            return "Couldn't paste. Recent Transcripts is off, so try dictating again."
        case .transcriptionFailed:
            return "Transcription failed. Try again."
        case .noSpeechDetected:
            return "No speech detected. Try again."
        }
    }

    var requiresInMemoryHistory: Bool {
        self == .insertionFailed
    }
}

enum RecordingHUDMode {
    case recording
    case transcribing
    case notice(DictationNotice)

    var isRecording: Bool {
        if case .recording = self { return true }
        return false
    }
}

/// Snapshot of the macOS visual-accessibility settings that affect the
/// custom-drawn recording HUD. Standard AppKit controls adapt themselves;
/// this view and its hand-written animations have to do so explicitly.
struct RecordingHUDAccessibilityOptions: Equatable {
    let reduceMotion: Bool
    let reduceTransparency: Bool
    let increaseContrast: Bool
    let differentiateWithoutColor: Bool

    @MainActor
    static func current(workspace: NSWorkspace = .shared) -> RecordingHUDAccessibilityOptions {
        RecordingHUDAccessibilityOptions(
            reduceMotion: workspace.accessibilityDisplayShouldReduceMotion,
            reduceTransparency: workspace.accessibilityDisplayShouldReduceTransparency,
            increaseContrast: workspace.accessibilityDisplayShouldIncreaseContrast,
            differentiateWithoutColor: workspace.accessibilityDisplayShouldDifferentiateWithoutColor
        )
    }

    var usesStaticRecordingPresentation: Bool {
        reduceMotion || differentiateWithoutColor
    }

    var usesOpaqueHUDBackground: Bool {
        reduceTransparency || increaseContrast
    }

    var animatesHUDTransitions: Bool {
        !reduceMotion
    }
}

func recordingHUDRecordingStatusText(
    options: RecordingHUDAccessibilityOptions,
    showsCancelHint: Bool
) -> String? {
    guard options.usesStaticRecordingPresentation else { return nil }
    return showsCancelHint ? "Recording — Esc cancels" : "Recording"
}

/// The hotkeys the user can pick from in Settings → Hotkey. Modifier
/// keycodes (62/61/54) are tracked via `.flagsChanged` events; the
/// F-keys are normal `.keyDown` / `.keyUp`.
struct HotkeyChoice: Equatable {
    let name: String
    let keycode: CGKeyCode
    let isModifier: Bool
    /// Which CGEventFlags mask bit fires for this modifier (nil for non-modifiers).
    let modifierFlag: CGEventFlags?
}

let RIGHT_MODIFIER_HOTKEY_CHOICES: [HotkeyChoice] = [
    HotkeyChoice(name: "Right Control", keycode: 62, isModifier: true, modifierFlag: .maskControl),
    HotkeyChoice(name: "Right Option", keycode: 61, isModifier: true, modifierFlag: .maskAlternate),
    HotkeyChoice(name: "Right Command", keycode: 54, isModifier: true, modifierFlag: .maskCommand),
]

/// Names are safe diagnostic metadata: unlike ordinary keycodes, these
/// identify only modifier keys and cannot disclose typed text.
let MODIFIER_KEY_NAMES_BY_KEYCODE: [CGKeyCode: String] = [
    54: "Right Command",
    55: "Left Command",
    56: "Left Shift",
    57: "Caps Lock",
    58: "Left Option",
    59: "Left Control",
    60: "Right Shift",
    61: "Right Option",
    62: "Right Control",
    63: "Fn",
]

let FUNCTION_KEY_NAMES_BY_KEYCODE: [CGKeyCode: String] = [
    122: "F1",
    120: "F2",
    99: "F3",
    118: "F4",
    96: "F5",
    97: "F6",
    98: "F7",
    100: "F8",
    101: "F9",
    109: "F10",
    103: "F11",
    111: "F12",
    105: "F13",
    107: "F14",
    113: "F15",
    106: "F16",
    64: "F17",
    79: "F18",
    80: "F19",
    90: "F20",
]

let HOTKEY_CHOICES: [HotkeyChoice] = [
    RIGHT_MODIFIER_HOTKEY_CHOICES[0],
    RIGHT_MODIFIER_HOTKEY_CHOICES[1],
    RIGHT_MODIFIER_HOTKEY_CHOICES[2],
    HotkeyChoice(name: "F5",            keycode: 96,  isModifier: false, modifierFlag: nil),
    HotkeyChoice(name: "F6",            keycode: 97,  isModifier: false, modifierFlag: nil),
    HotkeyChoice(name: "F13",           keycode: 105, isModifier: false, modifierFlag: nil),
    HotkeyChoice(name: "F18",           keycode: 79,  isModifier: false, modifierFlag: nil),
    HotkeyChoice(name: "F19",           keycode: 80,  isModifier: false, modifierFlag: nil),
]

func recordableHotkeyChoice(forKeycode keycode: CGKeyCode) -> HotkeyChoice? {
    if let choice = RIGHT_MODIFIER_HOTKEY_CHOICES.first(where: { $0.keycode == keycode }) {
        return choice
    }
    if let name = FUNCTION_KEY_NAMES_BY_KEYCODE[keycode] {
        return HotkeyChoice(name: name, keycode: keycode, isModifier: false, modifierFlag: nil)
    }
    return nil
}

func hotkeyChoice(forKeycode keycode: CGKeyCode) -> HotkeyChoice {
    recordableHotkeyChoice(forKeycode: keycode)
        ?? HOTKEY_CHOICES.first(where: { $0.keycode == DEFAULT_HOTKEY_KEYCODE })!
}

func normalizedHotkeyKeycode(storedValue value: Any?) -> CGKeyCode? {
    let raw: Int?
    if let number = value as? NSNumber {
        raw = number.intValue
    } else if let string = value as? String {
        raw = Int(string.trimmingCharacters(in: .whitespacesAndNewlines))
    } else {
        raw = nil
    }

    guard let raw,
          raw >= 0,
          raw <= Int(CGKeyCode.max),
          recordableHotkeyChoice(forKeycode: CGKeyCode(raw)) != nil else {
        return nil
    }
    return CGKeyCode(raw)
}

enum TriggerMode: String { case hold, toggle }
let TRIGGER_DISPLAY: [TriggerMode: String] = [
    .hold: "Press and hold",
    .toggle: "Press to toggle",
]

struct MaximumRecordingLengthChoice: Equatable {
    let seconds: TimeInterval
    let title: String
}

let MAXIMUM_RECORDING_LENGTH_CHOICES: [MaximumRecordingLengthChoice] = [
    MaximumRecordingLengthChoice(seconds: 60, title: "1 minute"),
    MaximumRecordingLengthChoice(seconds: 120, title: "2 minutes (Default)"),
    MaximumRecordingLengthChoice(seconds: 300, title: "5 minutes"),
    MaximumRecordingLengthChoice(seconds: 600, title: "10 minutes"),
]

func normalizedMaximumRecordingSeconds(storedValue value: Any?) -> TimeInterval? {
    let raw: TimeInterval?
    if let number = value as? NSNumber {
        raw = number.doubleValue
    } else if let string = value as? String {
        raw = TimeInterval(string.trimmingCharacters(in: .whitespacesAndNewlines))
    } else {
        raw = nil
    }

    guard let raw, raw.isFinite else { return nil }
    return min(max(raw.rounded(), MIN_MAX_RECORDING_SECONDS), MAX_MAX_RECORDING_SECONDS)
}

func maximumRecordingLengthTitle(seconds: TimeInterval) -> String {
    let wholeSeconds = Int(seconds)
    if wholeSeconds.isMultiple(of: 60) {
        let minutes = wholeSeconds / 60
        return "\(minutes) minute\(minutes == 1 ? "" : "s")"
    }
    return "\(wholeSeconds) seconds"
}

enum PasteSuffix: String { case appendSpace = "space", none, appendNewline = "newline" }
let PASTE_SUFFIX_DISPLAY: [PasteSuffix: String] = [
    .appendSpace: "Append space",
    .none: "No suffix",
    .appendNewline: "Append newline",
]

/// User-visible language choice for the v3 decoder script filter. `.auto`
/// passes no hint and lets the decoder pick freely — the right default for
/// almost everyone. Selecting a specific language biases the joint head
/// toward that script (Latin vs Cyrillic), which prevents the occasional
/// Cyrillic-character bleed-through that v3 can emit when transcribing
/// Latin-script speech (FluidAudio v0.14.1 fix). Raw values match
/// FluidAudio's `Language` BCP-47-ish keys so `fluidLanguage` is a direct
/// lookup.
enum DictationLanguage: String, CaseIterable {
    case auto
    case english = "en"
    case spanish = "es"
    case french = "fr"
    case german = "de"
    case italian = "it"
    case portuguese = "pt"
    case romanian = "ro"
    case polish = "pl"
    case czech = "cs"
    case slovak = "sk"
    case slovenian = "sl"
    case croatian = "hr"
    case bosnian = "bs"
    case russian = "ru"
    case ukrainian = "uk"
    case belarusian = "be"
    case bulgarian = "bg"
    case serbian = "sr"

    /// Map to FluidAudio's `Language` enum. Returns nil for `.auto` so the
    /// caller passes no hint and the decoder script filter stays off.
    var fluidLanguage: Language? {
        switch self {
        case .auto:        return nil
        case .english:     return .english
        case .spanish:     return .spanish
        case .french:      return .french
        case .german:      return .german
        case .italian:     return .italian
        case .portuguese:  return .portuguese
        case .romanian:    return .romanian
        case .polish:      return .polish
        case .czech:       return .czech
        case .slovak:      return .slovak
        case .slovenian:   return .slovenian
        case .croatian:    return .croatian
        case .bosnian:     return .bosnian
        case .russian:     return .russian
        case .ukrainian:   return .ukrainian
        case .belarusian:  return .belarusian
        case .bulgarian:   return .bulgarian
        case .serbian:     return .serbian
        }
    }
}

let DICTATION_LANGUAGE_DISPLAY: [DictationLanguage: String] = [
    .auto: "Auto-detect",
    .english: "English",
    .spanish: "Spanish",
    .french: "French",
    .german: "German",
    .italian: "Italian",
    .portuguese: "Portuguese",
    .romanian: "Romanian",
    .polish: "Polish",
    .czech: "Czech",
    .slovak: "Slovak",
    .slovenian: "Slovenian",
    .croatian: "Croatian",
    .bosnian: "Bosnian",
    .russian: "Russian",
    .ukrainian: "Ukrainian",
    .belarusian: "Belarusian",
    .bulgarian: "Bulgarian",
    .serbian: "Serbian",
]

enum SpeechModelProfile: String, CaseIterable {
    case multilingualV3 = "multilingual_v3"
    // Deprecated production option. Kept only so old saved preferences
    // can be read and migrated back to the supported v3 model.
    case englishUnified = "english_unified"

    static let productionDefault: SpeechModelProfile = .multilingualV3

    var isProductionSupported: Bool {
        self == .multilingualV3
    }

    var productionProfile: SpeechModelProfile {
        isProductionSupported ? self : Self.productionDefault
    }

    var displayName: String {
        switch self {
        case .multilingualV3:
            return "Multilingual (Parakeet TDT v3)"
        case .englishUnified:
            return "English optimized (Parakeet Unified, deprecated)"
        }
    }

    var shortName: String {
        switch self {
        case .multilingualV3:
            return "Parakeet TDT v3"
        case .englishUnified:
            return "Parakeet Unified"
        }
    }

    var aboutModelText: String {
        switch self {
        case .multilingualV3:
            return "FluidAudio · Parakeet TDT v3 multilingual (CoreML / ANE)"
        case .englishUnified:
            return "FluidAudio · Parakeet Unified English (deprecated)"
        }
    }

    var setupReadyDetail: String {
        "\(shortName) is loaded locally."
    }

    var cacheResetDetail: String {
        switch self {
        case .multilingualV3:
            return "Presspeech will delete the local Parakeet TDT v3 model cache, unload the current speech model, and download a fresh verified copy before dictation is available again."
        case .englishUnified:
            return "Presspeech will delete the local Parakeet TDT v3 model cache, unload the current speech model, and download a fresh verified copy before dictation is available again."
        }
    }

    var estimatedDownloadBytes: Int64 {
        700 * 1024 * 1024
    }

    var downloadSizeText: String {
        "about 500-700 MB"
    }
}

func productionSpeechModelProfile(rawValue: String?) -> SpeechModelProfile {
    guard let rawValue,
          let profile = SpeechModelProfile(rawValue: rawValue),
          profile.isProductionSupported else {
        return .productionDefault
    }
    return profile
}

enum RecentTranscriptLimit: String, CaseIterable {
    case off
    case last1 = "1"
    case last5 = "5"

    var count: Int {
        switch self {
        case .off: return 0
        case .last1: return 1
        case .last5: return 5
        }
    }
}

let DEFAULT_RECENT_TRANSCRIPT_LIMIT = RecentTranscriptLimit.last5
let RECENT_TRANSCRIPT_LIMIT_DISPLAY: [RecentTranscriptLimit: String] = [
    .off: "Off",
    .last1: "Last 1",
    .last5: "Last 5",
]

func parseRecentTranscriptLimit(storedValue value: Any?) -> RecentTranscriptLimit? {
    if let raw = value as? String {
        return RecentTranscriptLimit(rawValue: raw)
    }
    if let number = value as? NSNumber {
        return RecentTranscriptLimit(rawValue: number.stringValue)
    }
    return nil
}

func limitedRecentTranscripts(_ transcripts: [String], limit: RecentTranscriptLimit) -> [String] {
    let count = limit.count
    guard count > 0 else { return [] }
    guard transcripts.count > count else { return transcripts }
    return Array(transcripts.prefix(count))
}

func normalizedStoredAppVersion(_ value: String) -> String? {
    UpdateCheck.normalizedReleaseVersion(from: value)
}

func normalizedSkippedUpdateVersions(_ values: [String]) -> [String] {
    var result: [String] = []
    var seen = Set<String>()

    for value in values.reversed() {
        guard let version = UpdateCheck.normalizedReleaseVersion(from: value),
              !seen.contains(version) else {
            continue
        }
        seen.insert(version)
        result.append(version)
        if result.count == MAX_SKIPPED_UPDATE_VERSIONS { break }
    }

    return result.reversed()
}

enum UpdateCheckSource: String, Equatable {
    case automatic
    case manual
    /// Check fired because the user re-enabled automatic update checks
    /// in the settings menu — user-initiated like .manual but silent like
    /// .automatic, so diagnostics record it as its own source.
    case settingsToggle = "settings_toggle"

    var diagnosticLabel: String {
        switch self {
        case .automatic: return "automatic"
        case .manual: return "manual"
        case .settingsToggle: return "settings toggle"
        }
    }
}

enum UpdateCheckResult: String, Equatable {
    case failed = "failed"
    case upToDate = "up_to_date"
    case available = "available"
    case skipped = "skipped"

    var diagnosticLabel: String {
        switch self {
        case .failed: return "failed or unavailable"
        case .upToDate: return "up to date"
        case .available: return "update available"
        case .skipped: return "skipped version available"
        }
    }
}

func updateCheckResult(for release: GitHubRelease?,
                       currentVersion: String,
                       skippedVersions: [String]) -> UpdateCheckResult {
    guard let release else { return .failed }
    guard isNewer(release.version, than: currentVersion) else { return .upToDate }
    return skippedVersions.contains(release.version) ? .skipped : .available
}

func shouldSuppressUpdateForReminder(version: String,
                                     reminderVersion: String?,
                                     reminderUntil: Date?,
                                     now: Date) -> Bool {
    guard let reminderVersion,
          let reminderUntil,
          reminderVersion == version else {
        return false
    }
    return now < reminderUntil
}

/// True when a fetched release makes a stored "Remind me later" pause
/// stale: either the pause expired for the same version (it is about
/// to be re-shown), or a NEWER release superseded the paused one.
/// Without the newer-version case, pausing v0.3.0 and seeing v0.3.1
/// ship within 24 h left diagnostics showing both "Pending update:
/// v0.3.1" and "Reminder paused: v0.3.0 until …". An OLDER fetched
/// version (e.g. a retracted release) keeps the pause.
func shouldClearUpdateReminderPause(fetchedVersion: String, pausedVersion: String?) -> Bool {
    guard let pausedVersion else { return false }
    return fetchedVersion == pausedVersion || isNewer(fetchedVersion, than: pausedVersion)
}

/// Validates a persisted "Remind me later" expiry read back from
/// UserDefaults. Non-Date values and dates further in the future than
/// one full pause window are treated as corrupt and degrade to nil,
/// so a tampered or clock-skewed value re-arms the reminder instead
/// of suppressing updates indefinitely. Past dates pass through —
/// an expired pause is legitimate state that the suppress logic and
/// `shouldClearUpdateReminderPause` handle.
func normalizedUpdateReminderPauseExpiry(storedValue value: Any?,
                                         now: Date = Date(),
                                         maxPauseSeconds: TimeInterval = UPDATE_REMIND_LATER_SECONDS) -> Date? {
    guard let date = value as? Date else { return nil }
    guard date.timeIntervalSince(now) <= maxPauseSeconds else { return nil }
    return date
}

func updateCheckDiagnosticText(checkedAt: Date?,
                               source: UpdateCheckSource?,
                               result: UpdateCheckResult?,
                               releaseVersion: String) -> String {
    guard let checkedAt else { return "never" }
    let timestamp = ISO8601DateFormatter().string(from: checkedAt)
    let sourceText = source?.diagnosticLabel ?? "unknown source"
    let resultText = result?.diagnosticLabel ?? "unknown result"
    let versionText = releaseVersion.isEmpty ? "" : " (latest v\(releaseVersion))"
    return "\(timestamp), \(sourceText), \(resultText)\(versionText)"
}

struct AudioInputDevice: Equatable {
    let id: AudioDeviceID
    let uid: String
    let name: String
}

private let CORE_AUDIO_DEFAULT_AGGREGATE_PREFIX = "CADefaultDeviceAggregate-"

struct TranscriptCorrection: Codable, Equatable, Sendable {
    let source: String
    let replacement: String
}

// MARK: - Text correction transfer

struct TranscriptCorrectionsDocument: Codable {
    let schemaVersion: Int
    let exportedAt: Date
    let appVersion: String
    let corrections: [TranscriptCorrection]
}

enum TranscriptCorrectionsDocumentError: LocalizedError {
    case unsupportedSchema(Int)

    var errorDescription: String? {
        switch self {
        case .unsupportedSchema(let version):
            return "This corrections file uses schema version \(version), which this version of Presspeech cannot read."
        }
    }
}

enum TranscriptCorrectionsTransferError: LocalizedError {
    case fileTooLarge(Int, Int)
    case notRegularFile

    var errorDescription: String? {
        switch self {
        case .fileTooLarge(let bytes, let limit):
            let actual = ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file)
            let maximum = ByteCountFormatter.string(fromByteCount: Int64(limit), countStyle: .file)
            return "This corrections file is \(actual), which is larger than Presspeech's \(maximum) import limit."
        case .notRegularFile:
            return "The selected corrections path is not a regular file."
        }
    }
}

enum TranscriptCorrectionsTransfer {
    static let schemaVersion = 1
    /// Hard cap for corrections files and in-memory transfers.
    /// Derivation: the worst-case legal set is MAX_TRANSCRIPT_CORRECTIONS
    /// (512) entries at the per-field caps (512 B source + 4096 B
    /// replacement) ≈ 2.25 MiB of raw field bytes, ~2.4 MB once JSON
    /// keys, quoting, and pretty-printing are added — already over the
    /// old 2 MiB cap, which made a full legal set silently unsaveable.
    /// 4 MiB fits that worst case with headroom for JSON escaping while
    /// still rejecting runaway files.
    static let maxFileBytes = 4 * 1024 * 1024

    static var contentType: UTType {
        UTType(filenameExtension: CORRECTIONS_FILE_EXTENSION)
            ?? UTType(exportedAs: CORRECTIONS_FILE_UTI, conformingTo: .json)
    }

    static func encode(_ corrections: [TranscriptCorrection]) throws -> Data {
        let document = TranscriptCorrectionsDocument(
            schemaVersion: schemaVersion,
            exportedAt: Date(),
            appVersion: currentBundleVersion(),
            corrections: normalizedTranscriptCorrections(corrections)
        )
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        return try encoder.encode(document)
    }

    /// Decode result that also reports how many entries the file held
    /// BEFORE normalization, so the import dialog can disclose
    /// truncation (over-cap, invalid, or duplicate entries) instead of
    /// presenting the capped count as the file's content.
    struct CountedDecodeResult: Sendable, Equatable {
        let corrections: [TranscriptCorrection]
        let originalCount: Int
    }

    static func decode(_ data: Data) throws -> [TranscriptCorrection] {
        try decodeCounted(data).corrections
    }

    static func decodeCounted(_ data: Data) throws -> CountedDecodeResult {
        try validateTransferSize(data.count)
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601

        if let document = try? decoder.decode(TranscriptCorrectionsDocument.self, from: data) {
            guard document.schemaVersion == schemaVersion else {
                throw TranscriptCorrectionsDocumentError.unsupportedSchema(document.schemaVersion)
            }
            return CountedDecodeResult(
                corrections: normalizedTranscriptCorrections(document.corrections),
                originalCount: document.corrections.count
            )
        }

        // Early internal builds stored the bare array. Keeping the
        // fallback costs almost nothing and makes hand-authored files
        // forgiving while the public file format settles.
        let legacy = try decoder.decode([TranscriptCorrection].self, from: data)
        return CountedDecodeResult(
            corrections: normalizedTranscriptCorrections(legacy),
            originalCount: legacy.count
        )
    }

    static func validateTransferSize(_ bytes: Int) throws {
        guard bytes <= maxFileBytes else {
            throw TranscriptCorrectionsTransferError.fileTooLarge(bytes, maxFileBytes)
        }
    }

    /// Writes the encoded document and returns the exact bytes that
    /// landed on disk, so callers can fingerprint what was written
    /// without re-reading the file (a re-read races with sync
    /// providers replacing the file behind us).
    @discardableResult
    static func write(_ corrections: [TranscriptCorrection], to url: URL) throws -> Data {
        let data = try encode(corrections)
        try validateTransferSize(data.count)
        try validateWritablePath(url)
        let parent = url.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: parent, withIntermediateDirectories: true)
        try data.write(to: url, options: .atomic)
        return data
    }

    static func read(from url: URL) throws -> [TranscriptCorrection] {
        try decode(try readData(from: url))
    }

    static func readCounted(from url: URL) throws -> CountedDecodeResult {
        try decodeCounted(try readData(from: url))
    }

    private static func readData(from url: URL) throws -> Data {
        let fd = Darwin.open(url.path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW)
        guard fd >= 0 else {
            if errno == ELOOP {
                throw TranscriptCorrectionsTransferError.notRegularFile
            }
            throw currentPOSIXError()
        }
        defer { _ = Darwin.close(fd) }

        var st = stat()
        guard Darwin.fstat(fd, &st) == 0 else {
            throw currentPOSIXError()
        }
        guard (st.st_mode & S_IFMT) == S_IFREG else {
            throw TranscriptCorrectionsTransferError.notRegularFile
        }
        if st.st_size > off_t(maxFileBytes) {
            throw TranscriptCorrectionsTransferError.fileTooLarge(Int(st.st_size), maxFileBytes)
        }

        let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: false)
        var data = Data()
        while true {
            guard let chunk = try handle.read(upToCount: 1024 * 1024), !chunk.isEmpty else {
                break
            }
            data.append(chunk)
            try validateTransferSize(data.count)
        }
        return data
    }

    private static func validateWritablePath(_ url: URL) throws {
        var st = stat()
        guard lstat(url.path, &st) == 0 else {
            if errno == ENOENT { return }
            throw currentPOSIXError()
        }
        guard (st.st_mode & S_IFMT) == S_IFREG else {
            throw TranscriptCorrectionsTransferError.notRegularFile
        }
    }
}

// MARK: - Correction sync path safety
//
// The corrections sync-file path is persisted in UserDefaults and used
// by the periodic timer to read and write without further user
// confirmation. If an attacker can plant a leaf symlink at that path
// (e.g. via prior local code execution), each subsequent sync would
// follow it and either read or overwrite an unrelated file. Reject
// leaf-symlinks at the boundary. Parent-directory symlinks are not
// blocked — those are legitimate sync-provider layouts the user has
// already chosen.

enum TranscriptCorrectionsSyncPathError: LocalizedError {
    case isSymbolicLink

    var errorDescription: String? {
        switch self {
        case .isSymbolicLink:
            return "The text correction sync file is a symbolic link. Presspeech refuses to sync through symlinks. Reconnect Presspeech to a regular file."
        }
    }
}

func normalizedCorrectionSyncFilePath(_ value: String) -> String? {
    let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty,
          trimmed.utf8.count <= MAX_CORRECTION_SYNC_PATH_BYTES,
          !trimmed.unicodeScalars.contains(where: { $0.value == 0 }),
          (trimmed as NSString).isAbsolutePath else {
        return nil
    }
    return URL(fileURLWithPath: trimmed).standardizedFileURL.path
}

func validateCorrectionSyncPath(_ url: URL) throws {
    var st = stat()
    // lstat (not stat) so we inspect the link itself rather than its
    // target. Missing files are fine — first-time writes are allowed.
    guard lstat(url.path, &st) == 0 else { return }
    if (st.st_mode & S_IFMT) == S_IFLNK {
        throw TranscriptCorrectionsSyncPathError.isSymbolicLink
    }
}

func shouldStopCorrectionSync(afterPathValidationError error: Error) -> Bool {
    error is TranscriptCorrectionsSyncPathError
}

// MARK: - Model registry hardening
//
// FluidAudio reads REGISTRY_URL and MODEL_REGISTRY_URL from the process
// environment to override the speech-model download base URL. Presspeech
// does not document either as a feature, so a value here means either
// (a) a developer is debugging a mirror — uncommon — or (b) a process
// or LaunchAgent has injected one to redirect first-launch model
// downloads to an attacker-controlled host. An attacker who can plant
// `~/Library/LaunchAgents/*.plist` with `EnvironmentVariables` gets
// this persistence channel for free on every GUI app launch. Treat
// any value as adversarial: log it, present a blocking alert, refuse
// to start. The user fixes the env source and relaunches.
//
// We do not block HF_TOKEN etc. — those are auth headers FluidAudio
// sends to the (unchanged) huggingface.co host; a user with HF_TOKEN
// set for unrelated tooling shouldn't be punished.

let HOSTILE_REGISTRY_ENV_VARS = ["REGISTRY_URL", "MODEL_REGISTRY_URL"]

func detectedHostileRegistryEnvVars(in env: [String: String]) -> [String] {
    HOSTILE_REGISTRY_ENV_VARS.filter { env[$0] != nil }.sorted()
}

@MainActor
func refuseHostileRegistryEnvironmentAndExit() {
    let detected = detectedHostileRegistryEnvVars(in: ProcessInfo.processInfo.environment)
    guard !detected.isEmpty else { return }
    let names = detected.joined(separator: ", ")
    log("refusing to start: registry override env var(s) set: \(names)")
    let alert = NSAlert()
    alert.alertStyle = .critical
    alert.messageText = "Presspeech refused to start"
    alert.informativeText = """
        These environment variable(s) are set in Presspeech's process: \(names).

        FluidAudio uses them to override the speech-model download URL. Presspeech does not support this and treats it as a sign that the launch environment has been tampered with.

        Check ~/Library/LaunchAgents/, your shell rc files, and any parent process. Once the variables are gone, launch Presspeech again.
        """
    alert.addButton(withTitle: "Quit")
    alert.runModal()
    exit(EXIT_FAILURE)
}

// MARK: - Speech model integrity
//
// FluidAudio owns the Hugging Face download mechanics, but it does not
// pin the downloaded CoreML bundle contents. Presspeech downloads first,
// verifies the files that will be loaded by CoreML, and only then asks
// FluidAudio to compile/load the models. The manifest is intentionally
// tied to one upstream repo commit; a legitimate upstream model change
// should arrive as an explicit Presspeech update with refreshed hashes.

struct ModelFileDigest: Equatable {
    let relativePath: String
    let sha256: String
}

enum ModelIntegrityError: LocalizedError {
    case invalidManifestPath(String)
    case missingFile(String)
    case unexpectedFile(String)
    case invalidFileType(String)
    case digestMismatch(path: String, expected: String, actual: String)

    var errorDescription: String? {
        switch self {
        case .invalidManifestPath(let path):
            return "Speech model integrity manifest contains an unsafe path: \(path)"
        case .missingFile(let path):
            return "Speech model integrity check failed: missing file \(path)"
        case .unexpectedFile(let path):
            return "Speech model integrity check failed: unexpected file \(path)"
        case .invalidFileType(let path):
            return "Speech model integrity check failed: \(path) is not a regular file or directory"
        case .digestMismatch(let path, let expected, let actual):
            return "Speech model integrity check failed for \(path): expected \(expected), got \(actual)"
        }
    }
}

enum ModelIntegrity {
    static let parakeetV3Repository = "FluidInference/parakeet-tdt-0.6b-v3-coreml"
    static let parakeetV3RepositoryCommit = "aed02740059203c4a87495924f685de3722ae9ce"
    private static let sha256Characters = Set("0123456789abcdefABCDEF")

    private static let parakeetV3StrictDirectories = [
        "Decoder.mlmodelc",
        "Encoder.mlmodelc",
        "JointDecisionv3.mlmodelc",
        "Preprocessor.mlmodelc",
    ]

    private static let parakeetV3Files = [
        // BEGIN GENERATED PARAKEET_V3_MODEL_MANIFEST
        ModelFileDigest(relativePath: "Decoder.mlmodelc/analytics/coremldata.bin", sha256: "4238c4e81ecd0dc94bd7dfbb60f7e2cc824107c1ffe0387b8607b72833dba350"),
        ModelFileDigest(relativePath: "Decoder.mlmodelc/coremldata.bin", sha256: "18647af085d87bd8f3121c8a9b4d4564c1ede038dab63d295b4e745cf2d7fb99"),
        ModelFileDigest(relativePath: "Decoder.mlmodelc/metadata.json", sha256: "a39e93cd8371b8ded92635c7804fcd0590f0d1dd9415c6d19a0484be073077d9"),
        ModelFileDigest(relativePath: "Decoder.mlmodelc/model.mil", sha256: "ef2a0a281695398a62fde86ac269c68f73d5b578d7ed3b31f2ba91a2d1ea1f35"),
        ModelFileDigest(relativePath: "Decoder.mlmodelc/weights/weight.bin", sha256: "48adf0f0d47c406c8253d4f7fef967436a39da14f5a65e66d5a4b407be355d41"),
        ModelFileDigest(relativePath: "Encoder.mlmodelc/analytics/coremldata.bin", sha256: "42e638870d73f26b332918a3496ce36793fbb413a81cbd3d16ba01328637a105"),
        ModelFileDigest(relativePath: "Encoder.mlmodelc/coremldata.bin", sha256: "d48034a167a82e88fc3df64f60af963ab3983538271175b8319e7d5720a0fb86"),
        ModelFileDigest(relativePath: "Encoder.mlmodelc/metadata.json", sha256: "da24da9cca943fb29d7fa8e376d57fca7cb3aa08ca51b956b0b0e56813f087e9"),
        ModelFileDigest(relativePath: "Encoder.mlmodelc/model.mil", sha256: "ed7b19156ca29fa7dfd6891deb9fda4b0e8893f68597c985d135736546a43808"),
        ModelFileDigest(relativePath: "Encoder.mlmodelc/weights/weight.bin", sha256: "e2020f323703477a5b21d7c2d282c403e371afb5962e79877e3033e73ba6f421"),
        ModelFileDigest(relativePath: "JointDecisionv3.mlmodelc/analytics/coremldata.bin", sha256: "26def4bf73dd56d29dee21c8ef97cb8969e62f6120ed1adc91e46828e2737b6c"),
        ModelFileDigest(relativePath: "JointDecisionv3.mlmodelc/coremldata.bin", sha256: "f5fc08b741400f0088492c9e839418b1e18522f19cba28d361dd030c5f398342"),
        ModelFileDigest(relativePath: "JointDecisionv3.mlmodelc/metadata.json", sha256: "d9307211b9a37e0f0ac260c7660b1571a3de25841035cfdf9b58fd40425f890f"),
        ModelFileDigest(relativePath: "JointDecisionv3.mlmodelc/model.mil", sha256: "be60732943389a047175111a83f8839f3eb39d4803adafa828a0871b2f39818d"),
        ModelFileDigest(relativePath: "JointDecisionv3.mlmodelc/weights/weight.bin", sha256: "4e0e63d840032f7f07ddb1d64446051166281e5491bf22da8a945c41f6eedb3e"),
        ModelFileDigest(relativePath: "Preprocessor.mlmodelc/analytics/coremldata.bin", sha256: "c9beeb989c8d66f8be11df59bc6df277ec76cee404f6865b46243835ef562f6d"),
        ModelFileDigest(relativePath: "Preprocessor.mlmodelc/coremldata.bin", sha256: "dbde3f2300842c1fd51ef3ff948a0bcffe65ffd2dca10707f2509f32c1d65b1d"),
        ModelFileDigest(relativePath: "Preprocessor.mlmodelc/metadata.json", sha256: "2a98699e22d279dd37fa1d238aeb1c6db1df0d6fad687775324157689d8f3acf"),
        ModelFileDigest(relativePath: "Preprocessor.mlmodelc/model.mil", sha256: "4b8518a956450fec57f06c2a21bdffc26973f7f1fa6842fb38fe917f896b6b93"),
        ModelFileDigest(relativePath: "Preprocessor.mlmodelc/weights/weight.bin", sha256: "129b76e3aeafa8afa3ea76d995b964b145fe83700d579f6ff42c4c38fa0968ea"),
        ModelFileDigest(relativePath: "parakeet_vocab.json", sha256: "7ec60e05f1b24480736ec0eed40900f4626bce1fa9a60fd700ec7e2a59198735"),
        // END GENERATED PARAKEET_V3_MODEL_MANIFEST
    ]

    static func verifyParakeetV3Model(at directory: URL) throws {
        try verifyFiles(root: directory,
                        expectedFiles: parakeetV3Files,
                        strictDirectories: parakeetV3StrictDirectories)
        log("ASR: verified \(parakeetV3Files.count) model files from \(parakeetV3Repository) @ \(parakeetV3RepositoryCommit)")
    }

    static func verifyFiles(root: URL,
                            expectedFiles: [ModelFileDigest],
                            strictDirectories: [String]) throws {
        var expectedByPath: [String: String] = [:]
        var expectedDirectoryPaths = Set<String>()
        for directory in strictDirectories {
            try validateRelativePath(directory)
            expectedDirectoryPaths.insert(directory)
        }

        for file in expectedFiles {
            try validateRelativePath(file.relativePath)
            try validateSHA256(file.sha256, relativePath: file.relativePath)
            if expectedByPath.updateValue(file.sha256.lowercased(),
                                          forKey: file.relativePath) != nil {
                throw ModelIntegrityError.invalidManifestPath("duplicate file path: \(file.relativePath)")
            }
            expectedDirectoryPaths.formUnion(parentDirectories(of: file.relativePath))
        }
        var seenPaths: Set<String> = []

        for file in expectedFiles {
            let fileURL = root.appendingPathComponent(file.relativePath, isDirectory: false)
            try requireRegularFile(fileURL, relativePath: file.relativePath)

            let actual = try sha256Hex(of: fileURL, relativePath: file.relativePath)
            let expected = file.sha256.lowercased()
            guard actual == expected else {
                throw ModelIntegrityError.digestMismatch(path: file.relativePath,
                                                         expected: expected,
                                                         actual: actual)
            }
            seenPaths.insert(file.relativePath)
        }

        guard seenPaths.count == expectedFiles.count else {
            throw ModelIntegrityError.invalidManifestPath("duplicate file path")
        }

        for directory in strictDirectories {
            let directoryURL = root.appendingPathComponent(directory, isDirectory: true)
            try requireDirectory(directoryURL, relativePath: directory)
            guard let enumerator = FileManager.default.enumerator(at: directoryURL,
                                                                  includingPropertiesForKeys: nil)
            else { continue }

            for case let itemURL as URL in enumerator {
                let relativePath = relativePath(of: itemURL, under: root)
                switch try fileSystemNodeType(itemURL, relativePath: relativePath) {
                case .directory:
                    guard expectedDirectoryPaths.contains(relativePath) else {
                        throw ModelIntegrityError.unexpectedFile(relativePath)
                    }
                case .regularFile:
                    guard expectedByPath[relativePath] != nil else {
                        throw ModelIntegrityError.unexpectedFile(relativePath)
                    }
                }
            }
        }
    }

    static func sha256Hex(of url: URL, relativePath: String) throws -> String {
        let handle = try openRegularFileForHashing(url, relativePath: relativePath)
        defer { try? handle.close() }

        var hasher = SHA256()
        while true {
            guard let chunk = try handle.read(upToCount: 1024 * 1024), !chunk.isEmpty else {
                break
            }
            hasher.update(data: chunk)
        }

        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    private static func openRegularFileForHashing(_ url: URL,
                                                  relativePath: String) throws -> FileHandle {
        let fd = Darwin.open(url.path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW)
        guard fd >= 0 else {
            if errno == ENOENT { throw ModelIntegrityError.missingFile(relativePath) }
            throw ModelIntegrityError.invalidFileType(relativePath)
        }

        do {
            var st = stat()
            guard Darwin.fstat(fd, &st) == 0 else {
                throw ModelIntegrityError.invalidFileType(relativePath)
            }
            guard (st.st_mode & S_IFMT) == S_IFREG else {
                throw ModelIntegrityError.invalidFileType(relativePath)
            }
            return FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        } catch {
            _ = Darwin.close(fd)
            throw error
        }
    }

    private enum FileSystemNodeType {
        case regularFile
        case directory
    }

    private static func validateRelativePath(_ path: String) throws {
        guard !path.isEmpty, !path.hasPrefix("/") else {
            throw ModelIntegrityError.invalidManifestPath(path)
        }
        let components = path.split(separator: "/", omittingEmptySubsequences: false)
        guard !components.contains(".."),
              !components.contains("."),
              !components.contains("") else {
            throw ModelIntegrityError.invalidManifestPath(path)
        }
    }

    private static func validateSHA256(_ digest: String, relativePath: String) throws {
        guard digest.count == 64,
              digest.allSatisfy({ sha256Characters.contains($0) }) else {
            throw ModelIntegrityError.invalidManifestPath("invalid SHA-256 digest for \(relativePath)")
        }
    }

    private static func parentDirectories(of path: String) -> Set<String> {
        var result = Set<String>()
        var current = path
        while let slash = current.lastIndex(of: "/") {
            current = String(current[..<slash])
            result.insert(current)
        }
        return result
    }

    private static func requireRegularFile(_ url: URL, relativePath: String) throws {
        guard try fileSystemNodeType(url, relativePath: relativePath) == .regularFile else {
            throw ModelIntegrityError.invalidFileType(relativePath)
        }
    }

    private static func requireDirectory(_ url: URL, relativePath: String) throws {
        guard try fileSystemNodeType(url, relativePath: relativePath) == .directory else {
            throw ModelIntegrityError.invalidFileType(relativePath)
        }
    }

    private static func fileSystemNodeType(_ url: URL,
                                           relativePath: String) throws -> FileSystemNodeType {
        var st = stat()
        guard lstat(url.path, &st) == 0 else {
            if errno == ENOENT { throw ModelIntegrityError.missingFile(relativePath) }
            throw ModelIntegrityError.invalidFileType(relativePath)
        }

        switch st.st_mode & S_IFMT {
        case S_IFREG:
            return .regularFile
        case S_IFDIR:
            return .directory
        default:
            throw ModelIntegrityError.invalidFileType(relativePath)
        }
    }

    private static func relativePath(of url: URL, under root: URL) -> String {
        let rootPath = root.standardizedFileURL.path
        let prefix = rootPath.hasSuffix("/") ? rootPath : "\(rootPath)/"
        let path = url.standardizedFileURL.path
        guard path.hasPrefix(prefix) else { return url.lastPathComponent }
        return String(path.dropFirst(prefix.count))
    }
}

private func resolvedFluidAudioSupportDirectory(_ override: URL?) -> URL? {
    override
        ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?
            .appendingPathComponent("FluidAudio", isDirectory: true)
}

func isSafeSpeechModelCacheDirectory(_ cacheDir: URL,
                                     fluidAudioSupportDirectory: URL? = nil) -> Bool {
    let supportDirectory = resolvedFluidAudioSupportDirectory(fluidAudioSupportDirectory)
    guard let supportDirectory else { return false }

    let cacheURL = cacheDir.standardizedFileURL
    let supportURL = supportDirectory.standardizedFileURL
    guard cacheURL.isFileURL, supportURL.isFileURL else { return false }

    let cachePath = cacheURL.path
    let supportPath = supportURL.path
    let supportPrefix = supportPath.hasSuffix("/") ? supportPath : "\(supportPath)/"
    guard cachePath.hasPrefix(supportPrefix), cachePath != supportPath else { return false }

    let relativePath = String(cachePath.dropFirst(supportPrefix.count))
    let components = relativePath.split(separator: "/", omittingEmptySubsequences: false)
    return !components.isEmpty
        && !components.contains("")
        && !components.contains(".")
        && !components.contains("..")
}

func isExistingSpeechModelCacheDirectorySafeForRemoval(
    _ cacheDir: URL,
    fluidAudioSupportDirectory: URL? = nil
) -> Bool {
    guard isSafeSpeechModelCacheDirectory(cacheDir,
                                          fluidAudioSupportDirectory: fluidAudioSupportDirectory),
          let supportDirectory = resolvedFluidAudioSupportDirectory(fluidAudioSupportDirectory) else {
        return false
    }

    let cachePath = cacheDir.standardizedFileURL.path
    let supportPath = supportDirectory.standardizedFileURL.path
    let supportPrefix = supportPath.hasSuffix("/") ? supportPath : "\(supportPath)/"
    let relativePath = String(cachePath.dropFirst(supportPrefix.count))
    let components = relativePath.split(separator: "/", omittingEmptySubsequences: false)

    guard isExistingPlainDirectory(supportPath) else { return false }
    var currentPath = supportPath
    for component in components {
        currentPath = (currentPath as NSString).appendingPathComponent(String(component))
        guard isExistingPlainDirectory(currentPath) else { return false }
    }
    return currentPath == cachePath
}

func speechModelCacheBaseDirectory() -> URL {
    MLModelConfigurationUtils.defaultModelsDirectory()
}

func speechModelCacheDirectory(for _: SpeechModelProfile) -> URL {
    AsrModels.defaultCacheDirectory(for: .v3)
}

func speechModelDownloadRequiredBytes(for profile: SpeechModelProfile,
                                      headroomBytes: Int64 = MODEL_DOWNLOAD_HEADROOM_BYTES) -> Int64 {
    profile.estimatedDownloadBytes + headroomBytes
}

func speechModelDiskSpaceFailureDetail(profile: SpeechModelProfile,
                                       availableBytes: Int64?,
                                       requiredBytes: Int64) -> String? {
    guard let availableBytes, availableBytes >= 0, availableBytes < requiredBytes else {
        return nil
    }
    return """
    Presspeech needs \(profile.downloadSizeText) of free disk space to download \(profile.shortName), plus room for CoreML to prepare it.

    Available: \(formattedByteCount(UInt64(availableBytes)))
    Needed: \(formattedByteCount(UInt64(requiredBytes)))

    Free some disk space, then retry loading the speech model. Audio is not uploaded.
    """
}

func availableImportantDiskSpaceBytes(containing url: URL) -> Int64? {
    let fm = FileManager.default
    var probe = url.standardizedFileURL
    while !fm.fileExists(atPath: probe.path), probe.path != "/" {
        probe.deleteLastPathComponent()
    }
    guard let values = try? probe.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]),
          let capacity = values.volumeAvailableCapacityForImportantUsage else {
        return nil
    }
    return Int64(capacity)
}

func speechModelCacheExists(for profile: SpeechModelProfile) -> Bool {
    FileManager.default.fileExists(atPath: speechModelCacheDirectory(for: profile).path)
}

func assertSufficientDiskSpaceForSpeechModelDownload(profile: SpeechModelProfile) throws {
    let requiredBytes = speechModelDownloadRequiredBytes(for: profile)
    let availableBytes = availableImportantDiskSpaceBytes(containing: speechModelCacheBaseDirectory())
    guard let detail = speechModelDiskSpaceFailureDetail(profile: profile,
                                                        availableBytes: availableBytes,
                                                        requiredBytes: requiredBytes) else {
        return
    }
    throw NSError(domain: "Presspeech",
                  code: -8,
                  userInfo: [NSLocalizedDescriptionKey: detail])
}

func removeSpeechModelCacheDirectory(_ cacheDir: URL) async throws -> Bool {
    guard isSafeSpeechModelCacheDirectory(cacheDir) else {
        throw NSError(
            domain: "Presspeech",
            code: -3,
            userInfo: [
                NSLocalizedDescriptionKey: "Refusing to remove unexpected speech model cache path: \(cacheDir.path)"
            ]
        )
    }

    return try await Task.detached(priority: .userInitiated) {
        let fm = FileManager.default
        guard fm.fileExists(atPath: cacheDir.path) else {
            return false
        }
        guard isExistingSpeechModelCacheDirectorySafeForRemoval(cacheDir) else {
            throw NSError(
                domain: "Presspeech",
                code: -4,
                userInfo: [
                    NSLocalizedDescriptionKey: "Refusing to remove unsafe speech model cache path: \(cacheDir.path)"
                ]
            )
        }
        try fm.removeItem(at: cacheDir)
        return true
    }.value
}

private func isExistingPlainDirectory(_ path: String) -> Bool {
    var st = stat()
    guard lstat(path, &st) == 0 else { return false }
    return (st.st_mode & S_IFMT) == S_IFDIR
}

func normalizedTranscriptCorrectionSource(_ source: String) -> String {
    source
        .split(whereSeparator: { $0.isWhitespace })
        .joined(separator: " ")
        .lowercased()
}

func normalizedTranscriptCorrections(_ corrections: [TranscriptCorrection]) -> [TranscriptCorrection] {
    var result: [TranscriptCorrection] = []
    var indexBySource: [String: Int] = [:]

    for correction in corrections {
        let source = correction.source.trimmingCharacters(in: .whitespacesAndNewlines)
        let replacement = correction.replacement.trimmingCharacters(in: .whitespacesAndNewlines)
        let key = normalizedTranscriptCorrectionSource(source)
        guard !source.isEmpty,
              !replacement.isEmpty,
              !key.isEmpty,
              source.utf8.count <= MAX_TRANSCRIPT_CORRECTION_SOURCE_BYTES,
              replacement.utf8.count <= MAX_TRANSCRIPT_CORRECTION_REPLACEMENT_BYTES,
              !source.unicodeScalars.contains(where: { $0.value == 0 }),
              !replacement.unicodeScalars.contains(where: { $0.value == 0 }) else {
            continue
        }

        let cleaned = TranscriptCorrection(source: source, replacement: replacement)
        if let existing = indexBySource[key] {
            result[existing] = cleaned
        } else {
            guard result.count < MAX_TRANSCRIPT_CORRECTIONS else { continue }
            indexBySource[key] = result.count
            result.append(cleaned)
        }
    }

    return result
}

/// Keep the menu useful for small dictionaries without turning a large saved
/// vocabulary into a screen-height cascade. Large sets remain available in the
/// searchable manager window.
func shouldShowInlineCorrectionMenuEntries(count: Int,
                                           limit: Int = MAX_INLINE_CORRECTION_MENU_ENTRIES) -> Bool {
    count > 0 && count <= limit
}

/// Stable indices into `corrections` whose source or replacement contains all
/// whitespace-separated search terms. Matching ignores case and diacritics so
/// a user can still find an inflected name without typing every accent.
func correctionSearchMatchIndices(in corrections: [TranscriptCorrection],
                                  query: String) -> [Int] {
    let terms = query
        .split(whereSeparator: { $0.isWhitespace })
        .map(String.init)
    guard !terms.isEmpty else { return Array(corrections.indices) }

    let options: String.CompareOptions = [.caseInsensitive, .diacriticInsensitive]
    return corrections.indices.filter { index in
        let correction = corrections[index]
        let searchable = correction.source + "\n" + correction.replacement
        return terms.allSatisfy { searchable.range(of: $0, options: options) != nil }
    }
}

/// Resolve which visible rows a dictionary-table shortcut menu should act on.
/// A pointer invocation on an unselected row moves selection to that row, as
/// native macOS tables do; invoking the menu on an existing multi-selection
/// preserves the whole selection. Keyboard/VoiceOver invocation may not have
/// a clicked row, so it keeps the current selection instead of disabling a
/// context menu that the user opened with VO-Shift-M.
func correctionContextSelection(clickedRow: Int,
                                selectedRows: IndexSet,
                                rowCount: Int) -> IndexSet {
    guard clickedRow >= 0, clickedRow < rowCount else { return selectedRows }
    guard !selectedRows.contains(clickedRow) else { return selectedRows }
    return IndexSet(integer: clickedRow)
}

enum TranscriptCorrectionSortField: String {
    case source
    case replacement
}

/// Sort only the manager's index view; stored correction order remains stable
/// for export, sync, and deterministic transcript processing. The original
/// index is the final tie-breaker so clicking a column never makes equal rows
/// jump around between refreshes.
func sortedTranscriptCorrectionIndices(_ indices: [Int],
                                       in corrections: [TranscriptCorrection],
                                       by field: TranscriptCorrectionSortField,
                                       ascending: Bool) -> [Int] {
    let validIndices = indices.filter { corrections.indices.contains($0) }
    return validIndices.sorted { lhsIndex, rhsIndex in
        let lhs = corrections[lhsIndex]
        let rhs = corrections[rhsIndex]
        let lhsPrimary = field == .source ? lhs.source : lhs.replacement
        let rhsPrimary = field == .source ? rhs.source : rhs.replacement
        let lhsSecondary = field == .source ? lhs.replacement : lhs.source
        let rhsSecondary = field == .source ? rhs.replacement : rhs.source
        let options: String.CompareOptions = [.caseInsensitive, .diacriticInsensitive, .numeric]

        let primary = lhsPrimary.compare(rhsPrimary, options: options)
        if primary != .orderedSame {
            return ascending ? primary == .orderedAscending : primary == .orderedDescending
        }
        let secondary = lhsSecondary.compare(rhsSecondary, options: options)
        if secondary != .orderedSame {
            return ascending ? secondary == .orderedAscending : secondary == .orderedDescending
        }
        return lhsIndex < rhsIndex
    }

}

/// First line of the import-confirmation dialog. When the file holds
/// more entries than survive normalization (over the
/// MAX_TRANSCRIPT_CORRECTIONS cap, or invalid/duplicate entries), the
/// dialog must state the file's real count and how many will actually
/// be kept — normalization runs before the dialog, so without this the
/// user is told an oversized file "contains 512 corrections".
func correctionImportCountText(sourceName: String, originalCount: Int, keptCount: Int) -> String {
    guard originalCount > keptCount else {
        return "\(sourceName) contains \(keptCount) corrections."
    }
    return "\(sourceName) contains \(originalCount) entries; only the first \(keptCount) valid corrections (Presspeech keeps at most \(MAX_TRANSCRIPT_CORRECTIONS)) will be imported."
}

/// Appended to the import dialog when choosing Merge would push the
/// combined set over the correction cap. The merge path drops over-cap
/// entries silently, so the dialog has to warn before the user picks.
func correctionImportMergeCapWarningText(existingCount: Int,
                                         newCount: Int,
                                         cap: Int = MAX_TRANSCRIPT_CORRECTIONS) -> String? {
    let mergedCount = existingCount + newCount
    guard mergedCount > cap else { return nil }
    return "Merging would produce \(mergedCount) corrections; Presspeech keeps at most \(cap), so \(mergedCount - cap) would be dropped."
}

enum CorrectionEditorValidationIssue: Equatable {
    case missingSource
    case missingReplacement
    case unsupportedSourceCharacters
    case unsupportedReplacementCharacters
    case sourceTooLong
    case replacementTooLong
    case correctionLimitReached

    func message(sourceTitle: String) -> String {
        switch self {
        case .missingSource:
            return "Enter text in \(sourceTitle). Your other edit is still here."
        case .missingReplacement:
            return "Enter the exact text to paste. Your other edit is still here."
        case .unsupportedSourceCharacters:
            return "\(sourceTitle) contains an unsupported control character. Remove it and try again."
        case .unsupportedReplacementCharacters:
            return "Paste contains an unsupported control character. Remove it and try again."
        case .sourceTooLong:
            return "\(sourceTitle) must use \(MAX_TRANSCRIPT_CORRECTION_SOURCE_BYTES) bytes or fewer. Shorten it and try again."
        case .replacementTooLong:
            return "Paste must use \(MAX_TRANSCRIPT_CORRECTION_REPLACEMENT_BYTES) bytes or fewer. Shorten it and try again."
        case .correctionLimitReached:
            return "Presspeech already has \(MAX_TRANSCRIPT_CORRECTIONS) saved items. Delete one, or use a phrase that is already saved to update it."
        }
    }

    var focusesSource: Bool {
        switch self {
        case .missingSource, .unsupportedSourceCharacters, .sourceTooLong, .correctionLimitReached:
            return true
        case .missingReplacement, .unsupportedReplacementCharacters, .replacementTooLong:
            return false
        }
    }
}

func correctionEditorValidationIssue(source: String,
                                     replacement: String,
                                     isEditing: Bool,
                                     existingCorrections: [TranscriptCorrection]) -> CorrectionEditorValidationIssue? {
    let trimmedSource = source.trimmingCharacters(in: .whitespacesAndNewlines)
    let trimmedReplacement = replacement.trimmingCharacters(in: .whitespacesAndNewlines)

    if trimmedSource.isEmpty { return .missingSource }
    if trimmedReplacement.isEmpty { return .missingReplacement }
    if trimmedSource.contains("\u{0}") { return .unsupportedSourceCharacters }
    if trimmedReplacement.contains("\u{0}") { return .unsupportedReplacementCharacters }
    if trimmedSource.utf8.count > MAX_TRANSCRIPT_CORRECTION_SOURCE_BYTES { return .sourceTooLong }
    if trimmedReplacement.utf8.count > MAX_TRANSCRIPT_CORRECTION_REPLACEMENT_BYTES { return .replacementTooLong }

    if !isEditing, existingCorrections.count >= MAX_TRANSCRIPT_CORRECTIONS {
        let key = normalizedTranscriptCorrectionSource(trimmedSource)
        let updatesExisting = existingCorrections.contains {
            normalizedTranscriptCorrectionSource($0.source) == key
        }
        if !updatesExisting { return .correctionLimitReached }
    }
    return nil
}

private func utf8ClippedPrefix(_ text: String, maxBytes: Int) -> String {
    guard maxBytes > 0 else { return "" }
    var result = ""
    var usedBytes = 0
    for character in text {
        let byteCount = String(character).utf8.count
        guard usedBytes + byteCount <= maxBytes else { break }
        result.append(character)
        usedBytes += byteCount
    }
    return result
}

func correctionSourcePrefill(from transcript: String) -> String {
    let flat = transcript
        .split(whereSeparator: { $0.isWhitespace })
        .joined(separator: " ")
    return utf8ClippedPrefix(flat, maxBytes: MAX_TRANSCRIPT_CORRECTION_SOURCE_BYTES)
}

func normalizedAudioLevel(from samples: [Float]) -> Float {
    var sumSquares: Double = 0
    var count = 0

    for sample in samples where sample.isFinite {
        let clamped = max(-1, min(1, sample))
        sumSquares += Double(clamped * clamped)
        count += 1
    }

    return normalizedAudioLevel(sumSquares: sumSquares, sampleCount: count)
}

func normalizedAudioLevel(sumSquares: Double, sampleCount: Int) -> Float {
    guard sampleCount > 0, sumSquares > 0 else { return 0 }
    let rms = sqrt(sumSquares / Double(sampleCount))
    guard rms.isFinite, rms > 0 else { return 0 }

    // This is a voice-visibility meter, not a calibrated VU meter.
    // Keep low room tone calm, then aggressively lift speech-range RMS
    // so normal close-mic speech visibly opens the HUD without shouting.
    let decibels = 20 * log10(rms)
    let gated = (decibels + 52) / 20
    guard gated > 0.06 else { return 0 }
    let lifted = pow(max(0, min(1, gated)), 0.42)
    return Float(max(0, min(1, lifted)))
}

func visibleRecordingLevel(rawLevel: Float) -> Float {
    guard rawLevel.isFinite else { return 0 }
    return max(0, min(1, rawLevel))
}

struct TranscriptCorrectionSyncMergeResult: Equatable {
    let corrections: [TranscriptCorrection]
    let conflictingSources: [String]
}

struct CorrectionSyncFileFingerprint: Equatable {
    let modifiedAt: Date?
    let size: Int?
    let sha256: String
}

func correctionSyncFingerprint(for url: URL) -> CorrectionSyncFileFingerprint? {
    do {
        let digest = try correctionSyncFileSHA256Hex(url)
        let values = try url.resourceValues(forKeys: [.contentModificationDateKey, .fileSizeKey])
        return CorrectionSyncFileFingerprint(modifiedAt: values.contentModificationDate,
                                             size: values.fileSize,
                                             sha256: digest)
    } catch {
        return nil
    }
}

/// Fingerprint for bytes this process just wrote to `url`. Content
/// hash and size come from the in-memory data — never from re-reading
/// the file, which races with a sync provider replacing it in the
/// write-to-fingerprint window and would swallow that remote change
/// until the next local edit. Only the modification date is read
/// back; if even that races, the SHA mismatch on the next scan still
/// detects the remote change.
func correctionSyncFingerprint(forWrittenData data: Data, at url: URL) -> CorrectionSyncFileFingerprint {
    var hasher = SHA256()
    hasher.update(data: data)
    let digest = hasher.finalize().map { String(format: "%02x", $0) }.joined()
    let modifiedAt = (try? url.resourceValues(forKeys: [.contentModificationDateKey]))?
        .contentModificationDate
    return CorrectionSyncFileFingerprint(modifiedAt: modifiedAt,
                                         size: data.count,
                                         sha256: digest)
}

private func correctionSyncFileSHA256Hex(_ url: URL) throws -> String {
    let fd = Darwin.open(url.path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW)
    guard fd >= 0 else {
        throw currentPOSIXError()
    }
    defer { _ = Darwin.close(fd) }

    var st = stat()
    guard Darwin.fstat(fd, &st) == 0 else {
        throw currentPOSIXError()
    }
    guard (st.st_mode & S_IFMT) == S_IFREG else {
        throw TranscriptCorrectionsTransferError.notRegularFile
    }
    guard st.st_size <= TranscriptCorrectionsTransfer.maxFileBytes else {
        throw TranscriptCorrectionsTransferError.fileTooLarge(Int(st.st_size),
                                                              TranscriptCorrectionsTransfer.maxFileBytes)
    }

    let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: false)
    var hasher = SHA256()
    while true {
        guard let chunk = try handle.read(upToCount: 1024 * 1024), !chunk.isEmpty else {
            break
        }
        hasher.update(data: chunk)
    }
    return hasher.finalize().map { String(format: "%02x", $0) }.joined()
}

func mergedTranscriptCorrectionsForSync(base: [TranscriptCorrection],
                                        local: [TranscriptCorrection],
                                        remote: [TranscriptCorrection]) -> TranscriptCorrectionSyncMergeResult {
    let base = normalizedTranscriptCorrections(base)
    let local = normalizedTranscriptCorrections(local)
    let remote = normalizedTranscriptCorrections(remote)

    func dictionaryBySource(_ corrections: [TranscriptCorrection]) -> [String: TranscriptCorrection] {
        Dictionary(uniqueKeysWithValues: corrections.map {
            (normalizedTranscriptCorrectionSource($0.source), $0)
        })
    }

    let baseBySource = dictionaryBySource(base)
    let localBySource = dictionaryBySource(local)
    let remoteBySource = dictionaryBySource(remote)

    var orderedSources: [String] = []
    var seenSources: Set<String> = []
    func appendSources(from corrections: [TranscriptCorrection]) {
        for correction in corrections {
            let key = normalizedTranscriptCorrectionSource(correction.source)
            if seenSources.insert(key).inserted {
                orderedSources.append(key)
            }
        }
    }

    appendSources(from: local)
    appendSources(from: remote)
    appendSources(from: base)

    var merged: [TranscriptCorrection] = []
    var conflicts: [String] = []

    for source in orderedSources {
        let baseline = baseBySource[source]
        let localCorrection = localBySource[source]
        let remoteCorrection = remoteBySource[source]

        let chosen: TranscriptCorrection?
        if localCorrection == remoteCorrection {
            chosen = localCorrection
        } else if localCorrection == baseline {
            chosen = remoteCorrection
        } else if remoteCorrection == baseline {
            chosen = localCorrection
        } else {
            conflicts.append(localCorrection?.source ?? remoteCorrection?.source ?? baseline?.source ?? source)
            continue
        }

        if let chosen {
            merged.append(chosen)
        }
    }

    return TranscriptCorrectionSyncMergeResult(corrections: merged,
                                               conflictingSources: conflicts)
}

// MARK: - Audio input devices

func audioObjectStringProperty(_ objectID: AudioObjectID,
                               selector: AudioObjectPropertySelector) -> String? {
    var address = AudioObjectPropertyAddress(mSelector: selector,
                                             mScope: kAudioObjectPropertyScopeGlobal,
                                             mElement: kAudioObjectPropertyElementMain)
    var rawValue: UnsafeRawPointer?
    var size = UInt32(MemoryLayout<UnsafeRawPointer?>.size)
    let status = AudioObjectGetPropertyData(objectID, &address, 0, nil, &size, &rawValue)
    guard status == noErr, let rawValue else { return nil }
    let string = Unmanaged<CFString>.fromOpaque(rawValue).takeUnretainedValue() as String
    return string.isEmpty ? nil : string
}

func audioDeviceHasInputChannels(_ deviceID: AudioDeviceID) -> Bool {
    var address = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyStreamConfiguration,
                                             mScope: kAudioDevicePropertyScopeInput,
                                             mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(deviceID, &address, 0, nil, &size) == noErr,
          size > 0 else { return false }

    let raw = UnsafeMutableRawPointer.allocate(byteCount: Int(size),
                                               alignment: MemoryLayout<AudioBufferList>.alignment)
    defer { raw.deallocate() }
    let bufferList = raw.assumingMemoryBound(to: AudioBufferList.self)
    guard AudioObjectGetPropertyData(deviceID, &address, 0, nil, &size, bufferList) == noErr else {
        return false
    }

    let buffers = UnsafeMutableAudioBufferListPointer(bufferList)
    return buffers.contains { $0.mNumberChannels > 0 }
}

func isDefaultAggregateAudioInputPreference(_ preference: String) -> Bool {
    let trimmed = preference.trimmingCharacters(in: .whitespacesAndNewlines)
    return trimmed.range(of: CORE_AUDIO_DEFAULT_AGGREGATE_PREFIX,
                         options: [.anchored, .caseInsensitive]) != nil
}

func normalizedInputDevicePreference(_ preference: String) -> String? {
    let trimmed = preference.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty,
          trimmed.utf8.count <= MAX_INPUT_DEVICE_PREFERENCE_BYTES,
          !trimmed.unicodeScalars.contains(where: { $0.value == 0 }),
          !isDefaultAggregateAudioInputPreference(trimmed) else {
        return nil
    }
    return trimmed
}

func isDefaultAggregateAudioInputDevice(_ device: AudioInputDevice) -> Bool {
    isDefaultAggregateAudioInputPreference(device.uid)
        || isDefaultAggregateAudioInputPreference(device.name)
}

func availableAudioInputDevices() -> [AudioInputDevice] {
    var address = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDevices,
                                             mScope: kAudioObjectPropertyScopeGlobal,
                                             mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject),
                                         &address, 0, nil, &size) == noErr,
          size >= UInt32(MemoryLayout<AudioDeviceID>.size) else { return [] }

    let count = Int(size) / MemoryLayout<AudioDeviceID>.size
    var ids = Array(repeating: AudioDeviceID(0), count: count)
    guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject),
                                     &address, 0, nil, &size, &ids) == noErr else { return [] }

    return ids.compactMap { id in
        guard audioDeviceHasInputChannels(id),
              let uid = audioObjectStringProperty(id, selector: kAudioDevicePropertyDeviceUID),
              let name = audioObjectStringProperty(id, selector: kAudioObjectPropertyName) else {
            return nil
        }
        let device = AudioInputDevice(id: id, uid: uid, name: name)
        return isDefaultAggregateAudioInputDevice(device) ? nil : device
    }
    .sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
}

func audioInputDevice(matching preference: String,
                      in devices: [AudioInputDevice] = availableAudioInputDevices()) -> AudioInputDevice? {
    guard let trimmed = normalizedInputDevicePreference(preference) else { return nil }
    return devices.first { $0.uid == trimmed }
        ?? devices.first { $0.name.caseInsensitiveCompare(trimmed) == .orderedSame }
}

// MARK: - Logger
//
// All output goes to stderr (line-buffered, so we don't lose lines
// across an abrupt exit) and to ~/Library/Logs/Presspeech.log. Same
// path the Homebrew Cask install and the dev binary built by
// dev-run.sh both write to (they share a bundle id), so a single
// `tail -f` follows both.

final class Logger: @unchecked Sendable {
    static let shared = Logger()
    private let url: URL
    private let q = DispatchQueue(label: "PresspeechLogger")

    var fileURL: URL { url }

    init() {
        let logs = FileManager.default.urls(for: .libraryDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Logs", isDirectory: true)
        url = logs.appendingPathComponent("Presspeech.log")
    }

    func log(_ msg: String) {
        let stamp = ISO8601DateFormatter.timeOnly.string(from: Date())
        let line = "[\(stamp)] \(msg)\n"
        let data = Data(line.utf8)
        FileHandle.standardError.write(data)
        q.async { [url] in
            do {
                try appendPrivateLogData(data, to: url)
            } catch {
                let fallback = "Logger: file write failed: \(error.localizedDescription)\n"
                FileHandle.standardError.write(Data(fallback.utf8))
            }
        }
    }
}

func log(_ msg: String) { Logger.shared.log(msg) }

func privacySafeLogPath(_ path: String) -> String {
    privacySafeLogPath(URL(fileURLWithPath: path))
}

func privacySafeLogPath(_ url: URL) -> String {
    let name = url.lastPathComponent.trimmingCharacters(in: .whitespacesAndNewlines)
    return name.isEmpty || name == "/" ? "<local path>" : name
}

func privacySafeBundlePath(_ path: String) -> String {
    switch path {
    case "/Applications/Presspeech.app", "/tmp/Presspeech-dev.app":
        return path
    default:
        return privacySafeLogPath(path)
    }
}

private let PRIVATE_LOG_FILE_MODE = mode_t(S_IRUSR | S_IWUSR)
private let PRIVATE_HELPER_FILE_MODE = mode_t(S_IRUSR | S_IWUSR)

private func appendPrivateLogData(_ data: Data, to url: URL) throws {
    try FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                            withIntermediateDirectories: true)
    let flags = O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC | O_NOFOLLOW
    let fd = Darwin.open(url.path, flags, PRIVATE_LOG_FILE_MODE)
    guard fd >= 0 else { throw currentPOSIXError() }
    defer { _ = Darwin.close(fd) }

    try validateSingleLinkRegularFileDescriptor(fd)

    guard Darwin.fchmod(fd, PRIVATE_LOG_FILE_MODE) == 0 else {
        throw currentPOSIXError()
    }

    try writeAllData(data, to: fd)
}

private func validateSingleLinkRegularFileDescriptor(_ fd: Int32) throws {
    var st = stat()
    guard Darwin.fstat(fd, &st) == 0 else {
        throw currentPOSIXError()
    }
    guard (st.st_mode & S_IFMT) == S_IFREG else {
        throw posixError(EFTYPE)
    }
    guard st.st_nlink == 1 else {
        throw posixError(EMLINK)
    }
}

private func writeAllData(_ data: Data, to fd: Int32) throws {
    try data.withUnsafeBytes { rawBuffer in
        guard let base = rawBuffer.baseAddress else { return }
        var offset = 0
        while offset < rawBuffer.count {
            let written = Darwin.write(fd,
                                       base.advanced(by: offset),
                                       rawBuffer.count - offset)
            if written < 0 {
                if errno == EINTR { continue }
                throw currentPOSIXError()
            }
            guard written > 0 else { throw POSIXError(.EIO) }
            offset += written
        }
    }
}

private func currentPOSIXError() -> POSIXError {
    POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
}

private func posixError(_ code: Int32) -> POSIXError {
    POSIXError(POSIXErrorCode(rawValue: code) ?? .EIO)
}

extension ISO8601DateFormatter {
    static let timeOnly: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        return f
    }()
}

// MARK: - Settings
//
// Thin wrapper around NSUserDefaults using the explicit suite name
// `com.local.presspeech` (the bundle id), so settings persist at
// `~/Library/Preferences/com.local.presspeech.plist`. One property per
// user-visible setting; defaults are returned inline by each getter
// when the key is missing, rather than via a central `register()`
// call.

func settingsDomainAfterIdentityMigration(current: [String: Any],
                                          legacy: [String: Any]) -> [String: Any] {
    var migrated = current
    for (key, value) in legacy where migrated[key] == nil {
        migrated[key] = value
    }
    return migrated
}

func identityMigrationDestinationURL(forLegacyCorrectionURL url: URL) -> URL? {
    guard url.pathExtension.caseInsensitiveCompare(LEGACY_CORRECTIONS_FILE_EXTENSION) == .orderedSame else {
        return nil
    }
    return url.deletingPathExtension().appendingPathExtension(CORRECTIONS_FILE_EXTENSION)
}

final class Settings: @unchecked Sendable {
    private static let keyHotkeyKeycode = "hotkey_keycode"
    private static let keyTriggerMode = "trigger_mode"
    private static let keyMaxRecordingSeconds = "max_recording_seconds"
    private static let keyPasteSuffix = "paste_suffix"
    private static let keyRecentTranscripts = "recent_transcripts"
    private static let keyShowRecordingWaveform = "show_recording_waveform"
    private static let legacyKeyShowRecordingIndicator = "show_recording_indicator"
    private static let keyMuteWhileRecording = "mute_while_recording"
    private static let keyPlayFeedbackSounds = "play_feedback_sounds"
    private static let keyRestoreClipboardAfterPaste = "restore_clipboard_after_paste"
    private static let keyShowInDock = "show_in_dock"
    private static let keyInputDevice = "input_device"
    private static let keyCheckForUpdates = "check_for_updates"
    private static let keyLastUpdateCheckAt = "last_update_check_at"
    private static let keyLastUpdateCheckSource = "last_update_check_source"
    private static let keyLastUpdateCheckResult = "last_update_check_result"
    private static let keyLastUpdateCheckVersion = "last_update_check_version"
    private static let keyUpdateReminderPausedVersion = "update_reminder_paused_version"
    private static let keyUpdateReminderPausedUntil = "update_reminder_paused_until"
    private static let keyLastSeenVersion = "last_seen_version"
    private static let keySkippedVersions = "skipped_versions"
    private static let keyTranscriptCorrections = "transcript_corrections"
    private static let keyTranscriptCorrectionsSyncFile = "transcript_corrections_sync_file"
    private static let keyDictationLanguage = "dictation_language"
    private static let keySpeechModelProfile = "speech_model_profile"
    private static let keyInitialSpeechModelChoiceRequired = "initial_speech_model_choice_required"
    private static let keyRemoveFillerWords = "remove_filler_words"
    private static let keySpokenFormattingCommands = "spoken_formatting_commands"
    private static let keyActiveRunMarker = "active_run_marker"

    private let defaults: UserDefaults
    let didMigrateLegacyIdentity: Bool

    static let shared = Settings()

    init() {
        // Bundled .app under the Cask uses bundle id com.local.presspeech,
        // so `standardUserDefaults` IS the right suite. Belt and braces:
        // also try the suite-name initialiser, which works regardless
        // of bundle identification.
        let standard = UserDefaults.standard
        if let legacyDomain = standard.persistentDomain(forName: LEGACY_SETTINGS_SUITE),
           !legacyDomain.isEmpty {
            let currentDomain = standard.persistentDomain(forName: SETTINGS_SUITE) ?? [:]
            let migratedDomain = settingsDomainAfterIdentityMigration(current: currentDomain,
                                                                       legacy: legacyDomain)
            standard.setPersistentDomain(migratedDomain, forName: SETTINGS_SUITE)
            standard.removePersistentDomain(forName: LEGACY_SETTINGS_SUITE)
            self.didMigrateLegacyIdentity = true
            log("identity migration: moved \(legacyDomain.count) saved settings")
        } else {
            self.didMigrateLegacyIdentity = false
        }
        // Construct this after the domain copy so the suite cannot
        // retain a pre-migration cache.
        self.defaults = UserDefaults(suiteName: SETTINGS_SUITE) ?? .standard
    }

    var hotkeyKeycode: CGKeyCode {
        get {
            normalizedHotkeyKeycode(storedValue: defaults.object(forKey: Self.keyHotkeyKeycode))
                ?? DEFAULT_HOTKEY_KEYCODE
        }
        set {
            let normalized = normalizedHotkeyKeycode(storedValue: NSNumber(value: Int(newValue)))
                ?? DEFAULT_HOTKEY_KEYCODE
            defaults.set(Int(normalized), forKey: Self.keyHotkeyKeycode)
        }
    }

    var triggerMode: TriggerMode {
        get {
            if let v = defaults.string(forKey: Self.keyTriggerMode), let m = TriggerMode(rawValue: v) {
                return m
            }
            return .hold
        }
        set { defaults.set(newValue.rawValue, forKey: Self.keyTriggerMode) }
    }

    var maxRecordingSeconds: TimeInterval {
        get {
            normalizedMaximumRecordingSeconds(
                storedValue: defaults.object(forKey: Self.keyMaxRecordingSeconds)
            ) ?? DEFAULT_MAX_RECORDING_SECONDS
        }
        set {
            let normalized = normalizedMaximumRecordingSeconds(storedValue: newValue)
                ?? DEFAULT_MAX_RECORDING_SECONDS
            defaults.set(Int(normalized), forKey: Self.keyMaxRecordingSeconds)
        }
    }

    var pasteSuffix: PasteSuffix {
        get {
            if let v = defaults.string(forKey: Self.keyPasteSuffix), let s = PasteSuffix(rawValue: v) {
                return s
            }
            return .appendSpace
        }
        set { defaults.set(newValue.rawValue, forKey: Self.keyPasteSuffix) }
    }

    var recentTranscriptLimit: RecentTranscriptLimit {
        get {
            parseRecentTranscriptLimit(storedValue: defaults.object(forKey: Self.keyRecentTranscripts))
                ?? DEFAULT_RECENT_TRANSCRIPT_LIMIT
        }
        set { defaults.set(newValue.rawValue, forKey: Self.keyRecentTranscripts) }
    }

    var showRecordingWaveform: Bool {
        get {
            if defaults.object(forKey: Self.keyShowRecordingWaveform) != nil {
                return defaults.bool(forKey: Self.keyShowRecordingWaveform)
            }
            if defaults.object(forKey: Self.legacyKeyShowRecordingIndicator) != nil {
                return defaults.bool(forKey: Self.legacyKeyShowRecordingIndicator)
            }
            return true
        }
        set { defaults.set(newValue, forKey: Self.keyShowRecordingWaveform) }
    }

    var muteWhileRecording: Bool {
        get {
            if defaults.object(forKey: Self.keyMuteWhileRecording) == nil { return true }
            return defaults.bool(forKey: Self.keyMuteWhileRecording)
        }
        set { defaults.set(newValue, forKey: Self.keyMuteWhileRecording) }
    }

    var playFeedbackSounds: Bool {
        get {
            if defaults.object(forKey: Self.keyPlayFeedbackSounds) == nil { return true }
            return defaults.bool(forKey: Self.keyPlayFeedbackSounds)
        }
        set { defaults.set(newValue, forKey: Self.keyPlayFeedbackSounds) }
    }

    var restoreClipboardAfterPaste: Bool {
        get {
            if defaults.object(forKey: Self.keyRestoreClipboardAfterPaste) == nil { return false }
            return defaults.bool(forKey: Self.keyRestoreClipboardAfterPaste)
        }
        set { defaults.set(newValue, forKey: Self.keyRestoreClipboardAfterPaste) }
    }

    var showInDock: Bool {
        get { defaults.bool(forKey: Self.keyShowInDock) }
        set { defaults.set(newValue, forKey: Self.keyShowInDock) }
    }

    var inputDevice: String {
        get {
            guard let raw = defaults.string(forKey: Self.keyInputDevice),
                  let normalized = normalizedInputDevicePreference(raw) else {
                return ""
            }
            return normalized
        }
        set {
            if let normalized = normalizedInputDevicePreference(newValue) {
                defaults.set(normalized, forKey: Self.keyInputDevice)
            } else {
                defaults.removeObject(forKey: Self.keyInputDevice)
            }
        }
    }

    var checkForUpdates: Bool {
        get {
            if defaults.object(forKey: Self.keyCheckForUpdates) == nil { return true }
            return defaults.bool(forKey: Self.keyCheckForUpdates)
        }
        set { defaults.set(newValue, forKey: Self.keyCheckForUpdates) }
    }

    var lastUpdateCheckAt: Date? {
        get { defaults.object(forKey: Self.keyLastUpdateCheckAt) as? Date }
        set {
            if let newValue {
                defaults.set(newValue, forKey: Self.keyLastUpdateCheckAt)
            } else {
                defaults.removeObject(forKey: Self.keyLastUpdateCheckAt)
            }
        }
    }

    var lastUpdateCheckSource: UpdateCheckSource? {
        get {
            guard let raw = defaults.string(forKey: Self.keyLastUpdateCheckSource) else {
                return nil
            }
            return UpdateCheckSource(rawValue: raw)
        }
        set {
            if let newValue {
                defaults.set(newValue.rawValue, forKey: Self.keyLastUpdateCheckSource)
            } else {
                defaults.removeObject(forKey: Self.keyLastUpdateCheckSource)
            }
        }
    }

    var lastUpdateCheckResult: UpdateCheckResult? {
        get {
            guard let raw = defaults.string(forKey: Self.keyLastUpdateCheckResult) else {
                return nil
            }
            return UpdateCheckResult(rawValue: raw)
        }
        set {
            if let newValue {
                defaults.set(newValue.rawValue, forKey: Self.keyLastUpdateCheckResult)
            } else {
                defaults.removeObject(forKey: Self.keyLastUpdateCheckResult)
            }
        }
    }

    var lastUpdateCheckVersion: String {
        get {
            guard let raw = defaults.string(forKey: Self.keyLastUpdateCheckVersion),
                  let normalized = normalizedStoredAppVersion(raw) else {
                return ""
            }
            return normalized
        }
        set {
            if let normalized = normalizedStoredAppVersion(newValue) {
                defaults.set(normalized, forKey: Self.keyLastUpdateCheckVersion)
            } else {
                defaults.removeObject(forKey: Self.keyLastUpdateCheckVersion)
            }
        }
    }

    /// "Remind me later" pause state, persisted so a relaunch inside
    /// the 24 h window does not re-prompt ~30 s after launch. Both
    /// halves are validated independently and corrupt stored values
    /// degrade to nil; PresspeechApp treats a missing half as "no pause"
    /// and clears the leftover at startup.
    var updateReminderPausedVersion: String? {
        get {
            guard let raw = defaults.string(forKey: Self.keyUpdateReminderPausedVersion),
                  let normalized = normalizedStoredAppVersion(raw) else {
                return nil
            }
            return normalized
        }
        set {
            if let newValue, let normalized = normalizedStoredAppVersion(newValue) {
                defaults.set(normalized, forKey: Self.keyUpdateReminderPausedVersion)
            } else {
                defaults.removeObject(forKey: Self.keyUpdateReminderPausedVersion)
            }
        }
    }

    var updateReminderPausedUntil: Date? {
        get {
            normalizedUpdateReminderPauseExpiry(
                storedValue: defaults.object(forKey: Self.keyUpdateReminderPausedUntil)
            )
        }
        set {
            if let newValue,
               normalizedUpdateReminderPauseExpiry(storedValue: newValue) != nil {
                defaults.set(newValue, forKey: Self.keyUpdateReminderPausedUntil)
            } else {
                defaults.removeObject(forKey: Self.keyUpdateReminderPausedUntil)
            }
        }
    }

    var lastSeenVersion: String {
        get {
            guard let raw = defaults.string(forKey: Self.keyLastSeenVersion),
                  let normalized = normalizedStoredAppVersion(raw) else {
                return ""
            }
            return normalized
        }
        set {
            if let normalized = normalizedStoredAppVersion(newValue) {
                defaults.set(normalized, forKey: Self.keyLastSeenVersion)
            } else {
                defaults.removeObject(forKey: Self.keyLastSeenVersion)
            }
        }
    }

    var skippedVersions: [String] {
        get {
            normalizedSkippedUpdateVersions(
                (defaults.array(forKey: Self.keySkippedVersions) as? [String]) ?? []
            )
        }
        set {
            let versions = normalizedSkippedUpdateVersions(newValue)
            if versions.isEmpty {
                defaults.removeObject(forKey: Self.keySkippedVersions)
            } else {
                defaults.set(versions, forKey: Self.keySkippedVersions)
            }
        }
    }

    var transcriptCorrections: [TranscriptCorrection] {
        get {
            guard let data = defaults.data(forKey: Self.keyTranscriptCorrections) else { return [] }
            do {
                return try TranscriptCorrectionsTransfer.decode(data)
            } catch {
                log("settings: transcript correction decode failed: \(error)")
                return []
            }
        }
        set { storeTranscriptCorrections(newValue) }
    }

    /// Persists corrections and reports failure to the caller instead
    /// of swallowing it. With the per-field/per-count caps the encoded
    /// set always fits maxFileBytes in practice (see its derivation
    /// comment), but if encoding or the size guard ever fails the
    /// user's edit must not silently vanish — UI entry points alert on
    /// a non-nil return. The property setter above keeps the
    /// fire-and-forget shape (and the log below) for internal callers.
    @discardableResult
    func storeTranscriptCorrections(_ newValue: [TranscriptCorrection]) -> Error? {
        let corrections = normalizedTranscriptCorrections(newValue)
        guard !corrections.isEmpty else {
            defaults.removeObject(forKey: Self.keyTranscriptCorrections)
            return nil
        }
        do {
            let data = try JSONEncoder().encode(corrections)
            try TranscriptCorrectionsTransfer.validateTransferSize(data.count)
            defaults.set(data, forKey: Self.keyTranscriptCorrections)
            return nil
        } catch {
            log("settings: transcript correction encode failed: \(error)")
            return error
        }
    }

    var transcriptCorrectionsSyncFile: String {
        get {
            guard let raw = defaults.string(forKey: Self.keyTranscriptCorrectionsSyncFile),
                  let normalized = normalizedCorrectionSyncFilePath(raw) else {
                return ""
            }
            return normalized
        }
        set {
            if let normalized = normalizedCorrectionSyncFilePath(newValue) {
                defaults.set(normalized, forKey: Self.keyTranscriptCorrectionsSyncFile)
            } else {
                defaults.removeObject(forKey: Self.keyTranscriptCorrectionsSyncFile)
            }
        }
    }

    var dictationLanguage: DictationLanguage {
        get {
            if let v = defaults.string(forKey: Self.keyDictationLanguage),
               let lang = DictationLanguage(rawValue: v) {
                return lang
            }
            return .auto
        }
        set { defaults.set(newValue.rawValue, forKey: Self.keyDictationLanguage) }
    }

    var speechModelProfile: SpeechModelProfile {
        get {
            productionSpeechModelProfile(rawValue: defaults.string(forKey: Self.keySpeechModelProfile))
        }
        set { defaults.set(newValue.productionProfile.rawValue, forKey: Self.keySpeechModelProfile) }
    }

    @discardableResult
    func normalizeSpeechModelProfileForCurrentBuild() -> Bool {
        var changed = false
        if let raw = defaults.string(forKey: Self.keySpeechModelProfile) {
            let normalized = productionSpeechModelProfile(rawValue: raw)
            if normalized.rawValue != raw {
                defaults.set(SpeechModelProfile.productionDefault.rawValue,
                             forKey: Self.keySpeechModelProfile)
                changed = true
            }
        }
        if defaults.object(forKey: Self.keyInitialSpeechModelChoiceRequired) != nil {
            defaults.removeObject(forKey: Self.keyInitialSpeechModelChoiceRequired)
            changed = true
        }
        return changed
    }

    var removeFillerWords: Bool {
        get { defaults.bool(forKey: Self.keyRemoveFillerWords) }
        set { defaults.set(newValue, forKey: Self.keyRemoveFillerWords) }
    }

    var spokenFormattingCommands: Bool {
        get { defaults.bool(forKey: Self.keySpokenFormattingCommands) }
        set { defaults.set(newValue, forKey: Self.keySpokenFormattingCommands) }
    }

    var hasActiveRunMarker: Bool {
        get { defaults.bool(forKey: Self.keyActiveRunMarker) }
        set {
            if newValue {
                defaults.set(true, forKey: Self.keyActiveRunMarker)
            } else {
                defaults.removeObject(forKey: Self.keyActiveRunMarker)
            }
        }
    }
}

// MARK: - Permissions

enum Permission: String, CaseIterable, Equatable {
    case microphone = "Microphone"
    case accessibility = "Accessibility"
    case inputMonitoring = "Input Monitoring"
}

private enum ReadinessTransition: Equatable {
    case rebuildMenuOnly
    case blockForPermissions([Permission])
    case startHotkeyListener
}

private func readinessTransition(
    isReady: Bool,
    isCoreRuntimeReady: Bool,
    missingPermissions: [Permission]
) -> ReadinessTransition {
    if isReady {
        return missingPermissions.isEmpty
            ? .rebuildMenuOnly
            : .blockForPermissions(missingPermissions)
    }

    guard isCoreRuntimeReady else {
        return .rebuildMenuOnly
    }

    return missingPermissions.isEmpty
        ? .startHotkeyListener
        : .blockForPermissions(missingPermissions)
}

private enum AudioRouteChangeAction: Equatable {
    case ignore
    case rebuildMenuOnly
    case deferRefresh
    case restartNow
}

private func audioRouteChangeAction(isTerminating: Bool,
                                    isRestartingAudioInput: Bool,
                                    isCoreRuntimeReady: Bool,
                                    isRecording: Bool,
                                    isBusy: Bool,
                                    hasStartupTask: Bool) -> AudioRouteChangeAction {
    guard !isTerminating, !isRestartingAudioInput else { return .ignore }
    guard isCoreRuntimeReady else { return .rebuildMenuOnly }
    guard !isRecording, !isBusy, !hasStartupTask else { return .deferRefresh }
    return .restartNow
}

private func audioConfigurationChangeIsSuppressed(now: TimeInterval,
                                                  suppressedUntil: TimeInterval?) -> Bool {
    guard let suppressedUntil else { return false }
    return now < suppressedUntil
}

private enum WakeRuntimeRecoveryAction: Equatable {
    case ignore
    case deferUntilIdle
    case startAudioRuntime
    case startFullStartup
}

private func shouldResumeRuntimeAfterSystemSleep(isTerminating: Bool,
                                                 isCoreRuntimeReady: Bool,
                                                 isReady: Bool,
                                                 isRecording: Bool,
                                                 audioIsRunning: Bool) -> Bool {
    guard !isTerminating else { return false }
    return isCoreRuntimeReady || isReady || isRecording || audioIsRunning
}

private func wakeRuntimeRecoveryAction(shouldResumeAfterWake: Bool,
                                       isTerminating: Bool,
                                       hasStartupTask: Bool,
                                       isBusy: Bool,
                                       isSpeechModelReady: Bool) -> WakeRuntimeRecoveryAction {
    guard shouldResumeAfterWake, !isTerminating else { return .ignore }
    guard !hasStartupTask, !isBusy else { return .deferUntilIdle }
    return isSpeechModelReady ? .startAudioRuntime : .startFullStartup
}

private enum StartupFailureStage {
    case speechModel
    case audioInput
    case hotkeyListener

    var statusTitle: String {
        switch self {
        case .speechModel: return "Speech model failed to load"
        case .audioInput: return "Audio input failed to start"
        case .hotkeyListener: return "Hotkey listener failed to start"
        }
    }

    var retryTitle: String {
        switch self {
        case .speechModel: return "Retry Loading Speech Model"
        case .audioInput: return "Retry Audio Startup"
        case .hotkeyListener: return "Retry Hotkey Startup"
        }
    }
}

private struct StartupFailure {
    let stage: StartupFailureStage
    let detail: String

    var statusTitle: String { stage.statusTitle }
    var retryTitle: String { stage.retryTitle }
}

private enum PreviousExitNoticeAction: Equatable {
    case none
    case showNotice
}

private func previousExitNoticeAction(previousRunWasActive: Bool) -> PreviousExitNoticeAction {
    previousRunWasActive ? .showNotice : .none
}

private func speechModelFailureDetail(errorDescription: String) -> String {
    let lower = errorDescription.lowercased()
    let looksLikeIntegrityFailure = [
        "sha",
        "hash",
        "integrity",
        "verification",
        "verified",
        "corrupt",
        "incomplete",
    ].contains { lower.contains($0) }
    let looksLikeNetworkFailure = [
        "download",
        "network",
        "internet",
        "offline",
        "timed out",
        "timeout",
        "could not connect",
        "cannot connect",
        "not connected",
        "host",
        "url",
    ].contains { lower.contains($0) }
    let looksLikeDiskSpaceFailure = [
        "disk space",
        "free some disk",
        "available:",
        "needed:",
    ].contains { lower.contains($0) }

    if looksLikeDiskSpaceFailure {
        return errorDescription
    }

    if looksLikeIntegrityFailure {
        return """
        \(errorDescription)

        The local speech model cache may be incomplete or corrupt. Use Support → Reset Speech Model Cache… to delete it and download a fresh verified copy.
        """
    }
    if looksLikeNetworkFailure {
        return """
        \(errorDescription)

        Presspeech needs a one-time download of the local speech model. Check your network connection and retry; audio is not uploaded.
        """
    }
    return """
    \(errorDescription)

    If this keeps happening, use Support → Reset Speech Model Cache… to download a fresh verified copy, then Copy Diagnostics for a GitHub issue.
    """
}

private func fourCharacterCodeString(forRawOSStatus raw: UInt32) -> String? {
    let bytes = [
        UInt8((raw >> 24) & 0xff),
        UInt8((raw >> 16) & 0xff),
        UInt8((raw >> 8) & 0xff),
        UInt8(raw & 0xff),
    ]
    guard bytes.allSatisfy({ $0 >= 0x20 && $0 <= 0x7e }) else { return nil }
    return String(bytes: bytes, encoding: .ascii)
}

private func formattedOSStatusCode(_ code: Int) -> String {
    let raw = UInt32(bitPattern: Int32(truncatingIfNeeded: code))
    let hex = String(format: "0x%08x", raw)
    if let fourCharacterCode = fourCharacterCodeString(forRawOSStatus: raw) {
        return "OSStatus \(code) (\(hex), '\(fourCharacterCode)')"
    }
    return "OSStatus \(code) (\(hex))"
}

private func formattedOSStatus(_ status: OSStatus) -> String {
    formattedOSStatusCode(Int(status))
}

private func coreAudioOSStatusCode(from error: NSError) -> Int? {
    let domain = error.domain.lowercased()
    guard error.domain == NSOSStatusErrorDomain
        || domain.contains("coreaudio")
        || domain.contains("avfaudio") else {
        return nil
    }
    return error.code
}

private func stringValue(fromUserInfoValue value: Any?) -> String? {
    guard let value else { return nil }
    let text = String(describing: value).trimmingCharacters(in: .whitespacesAndNewlines)
    return text.isEmpty || text == "nil" ? nil : text
}

private func failedAudioCallDescription(from error: NSError) -> String? {
    for key in ["failed call", "failedCall", "AVAudioEngineFailedCall"] {
        if let text = stringValue(fromUserInfoValue: error.userInfo[key]) {
            return text
        }
    }

    for (key, value) in error.userInfo {
        let lower = key.lowercased()
        guard lower.contains("failed"), lower.contains("call") else { continue }
        if let text = stringValue(fromUserInfoValue: value) {
            return text
        }
    }
    return nil
}

private func audioStartupErrorDescription(_ error: Error) -> String {
    let nsError = error as NSError
    var lines = [nsError.localizedDescription]
    if let statusCode = coreAudioOSStatusCode(from: nsError) {
        lines.append("CoreAudio \(formattedOSStatusCode(statusCode)).")
    }
    if let failedCall = failedAudioCallDescription(from: nsError) {
        lines.append("Failed call: \(failedCall).")
    }
    return lines.joined(separator: "\n")
}

private func singleLineLogDetail(_ text: String) -> String {
    text.components(separatedBy: .newlines)
        .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
        .filter { !$0.isEmpty }
        .joined(separator: " | ")
}

private func audioInputFailureDetail(errorDescription: String) -> String {
    let lower = errorDescription.lowercased()
    let looksLikeCoreAudioFailure = lower.contains("coreaudio")
        || lower.contains("avfaudio")
        || lower.contains("osstatus")
        || lower.contains("kaustartio")
    guard looksLikeCoreAudioFailure else { return errorDescription }

    return """
    \(errorDescription)

    Presspeech rebuilt the audio engine and retried microphone startup, but CoreAudio is still refusing to start the input unit. If this began after sleep/wake or an audio-device change, restart CoreAudio with sudo killall coreaudiod or reboot the Mac, then retry audio startup.
    """
}

private func startupFailureDetail(stage: StartupFailureStage, errorDescription: String) -> String {
    switch stage {
    case .speechModel:
        return speechModelFailureDetail(errorDescription: errorDescription)
    case .audioInput:
        return audioInputFailureDetail(errorDescription: errorDescription)
    case .hotkeyListener:
        return errorDescription
    }
}

private func startupFailureDetail(stage: StartupFailureStage, error: Error) -> String {
    let errorDescription = stage == .audioInput
        ? audioStartupErrorDescription(error)
        : error.localizedDescription
    return startupFailureDetail(stage: stage, errorDescription: errorDescription)
}

private func startupFailureLogDetail(stage: StartupFailureStage, error: Error) -> String {
    let detail = stage == .audioInput
        ? audioStartupErrorDescription(error)
        : String(describing: error)
    return singleLineLogDetail(detail)
}

private func audioStartupRetryDelaySeconds(afterFailedAttempt failedAttempt: Int,
                                           retryDelays: [UInt64] = AUDIO_START_RETRY_DELAYS_SECONDS) -> UInt64? {
    guard failedAttempt > 0, failedAttempt <= retryDelays.count else { return nil }
    return retryDelays[failedAttempt - 1]
}

private func audioStartupRetryStatusTitle(nextAttempt: Int,
                                          totalAttempts: Int,
                                          delaySeconds: UInt64) -> String {
    "Audio input failed; retrying in \(delaySeconds)s (\(nextAttempt)/\(totalAttempts))…"
}

private struct SetupChecklistRowState: Equatable {
    let detail: String
    let status: String
    let buttonTitle: String?
}

private struct SetupChecklistPermissionState: Equatable {
    let permission: Permission
    let detail: String
    let status: String
    let buttonTitle: String?
}

private struct SetupChecklistSnapshot: Equatable {
    let speechModel: SetupChecklistRowState
    let audioInput: SetupChecklistRowState
    let permissions: [SetupChecklistPermissionState]
    let hotkey: SetupChecklistRowState
    let showInDock: Bool
    let isComplete: Bool
}

private func speechModelSetupRowState(profile: SpeechModelProfile,
                                      isSpeechModelReady: Bool,
                                      isStartupInProgress: Bool,
                                      startupStatusTitle: String,
                                      failure: StartupFailure?) -> SetupChecklistRowState {
    if let failure, failure.stage == .speechModel {
        return SetupChecklistRowState(detail: failure.detail,
                                      status: "Needs retry",
                                      buttonTitle: "Retry")
    }
    if isSpeechModelReady {
        return SetupChecklistRowState(detail: profile.setupReadyDetail,
                                      status: "Ready",
                                      buttonTitle: nil)
    }
    if isStartupInProgress {
        return SetupChecklistRowState(detail: startupStatusTitle,
                                      status: "Loading",
                                      buttonTitle: nil)
    }
    return SetupChecklistRowState(detail: "The speech model loads before dictation can start.",
                                  status: "Waiting",
                                  buttonTitle: nil)
}

private func audioInputSetupRowState(isSpeechModelReady: Bool,
                                     isCoreRuntimeReady: Bool,
                                     isStartupInProgress: Bool,
                                     startupStatusTitle: String = "Starting audio input…",
                                     failure: StartupFailure?) -> SetupChecklistRowState {
    if let failure, failure.stage == .audioInput {
        return SetupChecklistRowState(detail: failure.detail,
                                      status: "Needs retry",
                                      buttonTitle: "Retry")
    }
    if isCoreRuntimeReady {
        return SetupChecklistRowState(detail: "Microphone capture is ready.",
                                      status: "Ready",
                                      buttonTitle: nil)
    }
    if !isSpeechModelReady {
        return SetupChecklistRowState(detail: "Available after the speech model loads.",
                                      status: "Waiting",
                                      buttonTitle: nil)
    }
    if isStartupInProgress {
        return SetupChecklistRowState(detail: startupStatusTitle,
                                      status: "Starting",
                                      buttonTitle: nil)
    }
    return SetupChecklistRowState(detail: "Audio input starts before dictation can begin.",
                                  status: "Waiting",
                                  buttonTitle: nil)
}

private func hotkeySetupRowState(isReady: Bool,
                                 hotkeyTestSucceeded: Bool,
                                 triggerMode: TriggerMode,
                                 hotkeyName: String,
                                 failure: StartupFailure?) -> SetupChecklistRowState {
    if let failure, failure.stage == .hotkeyListener {
        return SetupChecklistRowState(detail: failure.detail,
                                      status: "Needs retry",
                                      buttonTitle: "Retry")
    }

    let verb = triggerMode == .hold ? "Hold" : "Press"
    if !isReady {
        return SetupChecklistRowState(detail: "Available after the model, audio input, and permissions are ready.",
                                      status: "Waiting",
                                      buttonTitle: nil)
    }
    if hotkeyTestSucceeded {
        return SetupChecklistRowState(detail: "\(verb) \(hotkeyName) to dictate.",
                                      status: "Detected",
                                      buttonTitle: nil)
    }
    let testDetail = triggerMode == .hold
        ? "Hold \(hotkeyName) briefly, then release."
        : "Press \(hotkeyName) once, then press it again to stop the test."
    return SetupChecklistRowState(detail: testDetail,
                                  status: "Ready to test",
                                  buttonTitle: nil)
}

private func setupChecklistCompletionState(isSpeechModelReady: Bool,
                                           isReady: Bool,
                                           permissionsGranted: Bool,
                                           hotkeyTestSucceeded: Bool) -> Bool {
    isSpeechModelReady && isReady && permissionsGranted && hotkeyTestSucceeded
}

@MainActor
final class Permissions {
    static func isGranted(_ p: Permission) -> Bool {
        switch p {
        case .microphone:
            return AVCaptureDevice.authorizationStatus(for: .audio) == .authorized
        case .accessibility:
            return AXIsProcessTrusted()
        case .inputMonitoring:
            return CGPreflightListenEventAccess()
        }
    }

    /// Trigger the system prompt or, if previously denied, push the
    /// user toward the right Settings pane. Returns immediately;
    /// actual grant happens asynchronously.
    static func request(_ p: Permission) {
        switch p {
        case .microphone:
            let status = AVCaptureDevice.authorizationStatus(for: .audio)
            if status == .denied {
                openSettingsPane("Privacy_Microphone")
            } else {
                AVCaptureDevice.requestAccess(for: .audio) { granted in
                    log("Microphone request: granted=\(granted)")
                }
            }
        case .accessibility:
            // The AX-trust-with-prompt API shows a native dialog
            // when status is undetermined, falls through silently if
            // already granted. We also open Settings as a fallback
            // for the previously-denied case.
            // kAXTrustedCheckOptionPrompt is an Apple-defined CFStringRef.
            // Swift 6 strict concurrency complains about referencing the
            // global directly from an @MainActor method; bridge via a
            // string literal that matches its documented value.
            let key = "AXTrustedCheckOptionPrompt"
            _ = AXIsProcessTrustedWithOptions([key: kCFBooleanTrue!] as CFDictionary)
            openSettingsPane("Privacy_Accessibility")
        case .inputMonitoring:
            // CGRequestListenEventAccess is the canonical request
            // path for CGEventTap clients. On macOS 26 it registers
            // the app in the Input Monitoring list and shows a
            // prompt OR opens Settings as appropriate.
            _ = CGRequestListenEventAccess()
            openSettingsPane("Privacy_ListenEvent")
        }
    }

    private static func openSettingsPane(_ subpath: String) {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(subpath)") {
            NSWorkspace.shared.open(url)
        }
    }
}

// MARK: - Hotkey listener
//
// Global event tap on the keyDown / keyUp / flagsChanged stream.
// Right Option is a modifier so it doesn't fire keyDown — we watch
// flagsChanged and diff the .option flag.

private struct HotkeyEventSnapshot: Sendable {
    let typeRawValue: UInt32
    let keycode: CGKeyCode
    let flagsRawValue: UInt64
    let isAutoRepeat: Bool

    var flags: CGEventFlags {
        CGEventFlags(rawValue: flagsRawValue)
    }
}

private func diagnosticModifierName(for event: HotkeyEventSnapshot) -> String? {
    guard event.typeRawValue == CGEventType.flagsChanged.rawValue else { return nil }
    return MODIFIER_KEY_NAMES_BY_KEYCODE[event.keycode]
}

private enum HotkeyRecordingDecision: Equatable {
    case accept(HotkeyChoice)
    case reject(String)
    case ignore
}

private enum HotkeyPreferenceUpdateResult: Equatable {
    case saved(HotkeyChoice)
    case rejected(String)
    case rolledBack(previous: HotkeyChoice, message: String)
}

private func hotkeyPreferenceUpdateResult(
    requested: HotkeyChoice,
    previous: HotkeyChoice,
    persistedKeycode: CGKeyCode
) -> HotkeyPreferenceUpdateResult {
    guard let recordable = recordableHotkeyChoice(forKeycode: requested.keycode) else {
        return .rejected("That key cannot be used for dictation.")
    }

    guard persistedKeycode == recordable.keycode else {
        return .rolledBack(
            previous: previous,
            message: "Presspeech could not save that hotkey, so it kept \(previous.name)."
        )
    }

    return .saved(recordable)
}

private enum HotkeyRecorderRestartAction: Equatable {
    case none
    case restoredListener
    case recordFailure
}

private func hotkeyRecorderRestartAction(
    shouldRestoreHotkeyTap: Bool,
    isTerminating: Bool,
    restartSucceeded: Bool
) -> HotkeyRecorderRestartAction {
    guard shouldRestoreHotkeyTap, !isTerminating else { return .none }
    return restartSucceeded ? .restoredListener : .recordFailure
}

private func hotkeyRecordingDecision(for event: HotkeyEventSnapshot) -> HotkeyRecordingDecision {
    if event.isAutoRepeat { return .ignore }

    if event.typeRawValue == CGEventType.flagsChanged.rawValue {
        guard let choice = RIGHT_MODIFIER_HOTKEY_CHOICES.first(where: { $0.keycode == event.keycode }),
              let mask = choice.modifierFlag,
              event.flags.contains(mask) else {
            return .ignore
        }
        return .accept(choice)
    }

    guard event.typeRawValue == CGEventType.keyDown.rawValue else { return .ignore }
    guard let choice = recordableHotkeyChoice(forKeycode: event.keycode),
          !choice.isModifier else {
        return .reject("Choose a right-side modifier key or an F-key. Typing keys are not safe because Presspeech suppresses its dictation key globally.")
    }
    return .accept(choice)
}

private func hotkeyRecorderShouldPassThrough(_ event: HotkeyEventSnapshot) -> Bool {
    event.typeRawValue == CGEventType.keyDown.rawValue
        && HOTKEY_RECORDER_CONTROL_KEYCODES.contains(event.keycode)
}

private enum HotkeyTransitionAction: Equatable, Sendable {
    case press
    case release
    case cancel
}

private enum RecordingStartBlocker: String, Sendable {
    case notReady = "not ready"
    case alreadyRecording = "already recording"
    case busyTranscribing = "busy transcribing"
    case terminating
}

private func resolvedRecordingStartBlocker<Owner: AnyObject>(
    for owner: Owner?,
    evaluate: (Owner) -> RecordingStartBlocker?
) -> RecordingStartBlocker? {
    guard let owner else { return .terminating }
    return evaluate(owner)
}

private struct HotkeyTransitionResult: Equatable, Sendable {
    let suppress: Bool
    let actions: [HotkeyTransitionAction]
    let startWasDeclined: Bool

    init(suppress: Bool,
         actions: [HotkeyTransitionAction],
         startWasDeclined: Bool = false) {
        self.suppress = suppress
        self.actions = actions
        self.startWasDeclined = startWasDeclined
    }

    static let pass = HotkeyTransitionResult(suppress: false, actions: [])
    static let suppressOnly = HotkeyTransitionResult(suppress: true, actions: [])
}

private struct HotkeyTransitionState {
    private var hotkeyModifierDown = false
    private var toggleActive = false
    private var suppressEscapeKeyUp = false

    mutating func resetAll() {
        hotkeyModifierDown = false
        toggleActive = false
        suppressEscapeKeyUp = false
    }

    mutating func resetToggleState() {
        toggleActive = false
    }

    mutating func recordingDidStartOutsideHotkey(triggerMode: TriggerMode) {
        if triggerMode == .toggle {
            toggleActive = true
        }
    }

    /// `canStartRecording` mirrors the app-side guard on handlePress
    /// (ready, not recording, not busy, not terminating). Toggle mode
    /// consults it before flipping state — see the `.toggle` case.
    /// Defaults to true so hold-mode behaviour and existing callers
    /// are unchanged.
    mutating func transition(
        for event: HotkeyEventSnapshot,
        hotkey: HotkeyChoice,
        triggerMode: TriggerMode,
        isRecording: Bool,
        canStartRecording: Bool = true
    ) -> HotkeyTransitionResult {
        if event.keycode == ESCAPE_KEYCODE {
            return transitionEscape(for: event, isRecording: isRecording)
        }

        guard event.keycode == hotkey.keycode else { return .pass }

        // Modifier masks are side-agnostic, so the physical keycode's
        // own down state is the source of truth for right-side releases.
        var isPress = false
        var isRelease = false
        if hotkey.isModifier {
            guard event.typeRawValue == CGEventType.flagsChanged.rawValue,
                  let mask = hotkey.modifierFlag else { return .suppressOnly }
            if hotkeyModifierDown {
                isRelease = true
                hotkeyModifierDown = false
            } else if event.flags.contains(mask) {
                isPress = true
                hotkeyModifierDown = true
            }
        } else {
            if event.typeRawValue == CGEventType.keyDown.rawValue, !event.isAutoRepeat {
                isPress = true
            } else if event.typeRawValue == CGEventType.keyUp.rawValue {
                isRelease = true
            } else {
                return .suppressOnly
            }
        }

        switch triggerMode {
        case .hold:
            var actions: [HotkeyTransitionAction] = []
            if isPress { actions.append(.press) }
            if isRelease { actions.append(.release) }
            return HotkeyTransitionResult(suppress: true, actions: actions)
        case .toggle:
            // Toggle mode: every press flips between "start recording"
            // and "stop recording". Releases are no-ops.
            guard isPress else { return .suppressOnly }
            if toggleActive {
                toggleActive = false
                return HotkeyTransitionResult(suppress: true, actions: [.release])
            }
            // A press the app will reject must not flip the toggle. Report
            // the decline separately so the next ready press still starts.
            guard canStartRecording else {
                return HotkeyTransitionResult(suppress: true,
                                              actions: [],
                                              startWasDeclined: true)
            }
            toggleActive = true
            return HotkeyTransitionResult(suppress: true, actions: [.press])
        }
    }

    private mutating func transitionEscape(
        for event: HotkeyEventSnapshot,
        isRecording: Bool
    ) -> HotkeyTransitionResult {
        if event.typeRawValue == CGEventType.keyDown.rawValue {
            if event.isAutoRepeat, suppressEscapeKeyUp {
                return .suppressOnly
            }
            guard isRecording else { return .pass }
            suppressEscapeKeyUp = true
            return event.isAutoRepeat
                ? .suppressOnly
                : HotkeyTransitionResult(suppress: true, actions: [.cancel])
        }

        if event.typeRawValue == CGEventType.keyUp.rawValue, suppressEscapeKeyUp {
            suppressEscapeKeyUp = false
            return .suppressOnly
        }

        return .pass
    }
}

@MainActor
final class HotkeyListener {

    private var tap: CFMachPort?
    private var runLoopSource: CFRunLoopSource?
    private var transitionState = HotkeyTransitionState()
    /// `tapCreate` succeeding proves only that the tap was installed, not
    /// that events are reaching it — with Input Monitoring stale the tap
    /// goes live and then stays silent forever, which is indistinguishable
    /// in the log from "the user never pressed anything". Log the first
    /// event that actually arrives so the two can be told apart.
    private var didLogFirstEvent = false
    /// Same problem one level in: events arrive, but none of them match the
    /// configured hotkey. Only modifier names are safe to persist; logging an
    /// arbitrary global keycode could disclose part of something the user typed.
    private var didLogFirstUnmatchedModifier = false

    /// User's current hotkey (set via Settings → Hotkey submenu).
    var hotkey: HotkeyChoice = hotkeyChoice(forKeycode: DEFAULT_HOTKEY_KEYCODE)
    var triggerMode: TriggerMode = .hold

    /// onPress fires when a recording should start (press in hold mode,
    /// or first press in toggle mode). onRelease fires when it should
    /// stop (release in hold mode, or second press in toggle mode).
    /// onCancel fires for Escape while a recording is active.
    var onPress: (() -> Void)?
    var onRelease: (() -> Void)?
    var onCancel: (() -> Void)?
    var isRecordingActive: (() -> Bool)?
    /// Describes why a new recording cannot start. Toggle mode uses this to
    /// report a rejected press without flipping its state. nil means ready.
    fileprivate var recordingStartBlocker: (() -> RecordingStartBlocker?)?

    @discardableResult
    func start() -> Bool {
        if let tap {
            CGEvent.tapEnable(tap: tap, enable: true)
            return true
        }

        let mask: CGEventMask = (1 << CGEventType.keyDown.rawValue)
                              | (1 << CGEventType.keyUp.rawValue)
                              | (1 << CGEventType.flagsChanged.rawValue)

        let opaqueSelf = Unmanaged.passUnretained(self).toOpaque()
        guard let tap = CGEvent.tapCreate(
            tap: .cgSessionEventTap,
            place: .headInsertEventTap,
            options: .defaultTap,
            eventsOfInterest: mask,
            callback: { _, type, event, userInfo in
                guard let userInfo else { return Unmanaged.passUnretained(event) }
                let listener = Unmanaged<HotkeyListener>.fromOpaque(userInfo).takeUnretainedValue()
                let snapshot = HotkeyEventSnapshot(
                    typeRawValue: type.rawValue,
                    keycode: CGKeyCode(event.getIntegerValueField(.keyboardEventKeycode)),
                    flagsRawValue: event.flags.rawValue,
                    isAutoRepeat: type == .keyDown && event.getIntegerValueField(.keyboardEventAutorepeat) != 0
                )
                let shouldSuppress = MainActor.assumeIsolated {
                    listener.handleTapCallback(snapshot)
                }
                return shouldSuppress ? nil : Unmanaged.passUnretained(event)
            },
            userInfo: opaqueSelf
        ) else {
            log("CGEvent.tapCreate failed — Input Monitoring permission missing?")
            return false
        }

        self.tap = tap
        runLoopSource = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
        CFRunLoopAddSource(CFRunLoopGetMain(), runLoopSource, .commonModes)
        CGEvent.tapEnable(tap: tap, enable: true)
        log("HotkeyListener: tap active (watching \(hotkey.name))")
        return true
    }

    func stop() {
        if let tap {
            CGEvent.tapEnable(tap: tap, enable: false)
            CFMachPortInvalidate(tap)
        }
        if let runLoopSource {
            CFRunLoopRemoveSource(CFRunLoopGetMain(), runLoopSource, .commonModes)
        }
        tap = nil
        runLoopSource = nil
        transitionState.resetAll()
    }

    /// Replace the current hotkey choice. Safe to call at runtime —
    /// the tap stays bound, only the per-event filter changes.
    func setHotkey(_ choice: HotkeyChoice) {
        self.hotkey = choice
        self.transitionState.resetAll()
        log("HotkeyListener: hotkey changed → \(choice.name)")
    }

    func setTriggerMode(_ mode: TriggerMode) {
        // Reset toggle state when switching modes so we don't get
        // stuck in mid-toggle from a previous session.
        if mode != triggerMode { transitionState.resetToggleState() }
        triggerMode = mode
        log("HotkeyListener: trigger mode → \(mode.rawValue)")
    }

    private func handleTapCallback(_ event: HotkeyEventSnapshot) -> Bool {
        if event.typeRawValue == CGEventType.tapDisabledByTimeout.rawValue
            || event.typeRawValue == CGEventType.tapDisabledByUserInput.rawValue {
            if let tap {
                CGEvent.tapEnable(tap: tap, enable: true)
                log("HotkeyListener: event tap re-enabled after \(event.typeRawValue)")
            }
            return false
        }

        if !didLogFirstEvent {
            didLogFirstEvent = true
            log("HotkeyListener: first keyboard event received — tap is delivering")
        }

        let startBlocker = recordingStartBlocker?()
        let result = transitionState.transition(for: event,
                                                hotkey: hotkey,
                                                triggerMode: triggerMode,
                                                isRecording: isRecordingActive?() ?? false,
                                                canStartRecording: startBlocker == nil)
        if result.startWasDeclined {
            log("press ignored: \((startBlocker ?? .notReady).rawValue)")
        }
        if result.actions.isEmpty,
           !didLogFirstUnmatchedModifier,
           event.keycode != hotkey.keycode,
           let modifierName = diagnosticModifierName(for: event) {
            didLogFirstUnmatchedModifier = true
            log("HotkeyListener: received \(modifierName), but watching \(hotkey.name)")
        }
        dispatchHotkeyActions(result.actions)
        return result.suppress
    }

    private func dispatchHotkeyActions(_ actions: [HotkeyTransitionAction]) {
        guard !actions.isEmpty else { return }

        Task { @MainActor [weak self] in
            self?.performHotkeyActions(actions)
        }
    }

    private func performHotkeyActions(_ actions: [HotkeyTransitionAction]) {
        for action in actions {
            switch action {
            case .press: onPress?()
            case .release: onRelease?()
            case .cancel: onCancel?()
            }
        }
    }

    /// Called when the recording stops via a path other than the
    /// hotkey (auto-release at max duration, app quitting, etc.) so
    /// toggle mode doesn't end up offset by one.
    func resetToggleState() {
        transitionState.resetToggleState()
    }

    /// Keep toggle mode aligned when a menu action starts the recording.
    /// The next hotkey press should stop that recording, not be declined as
    /// an attempted second start.
    func recordingDidStartOutsideHotkey() {
        transitionState.recordingDidStartOutsideHotkey(triggerMode: triggerMode)
    }
}

// MARK: - Audio capture
//
// AVAudioEngine tap on the input node, downmix to mono / 16 kHz /
// Float32 if needed, append to a buffer while recording.
//
// Deliberately NOT @MainActor. AVAudioEngine's installTap delivers
// callbacks on an audio worker thread. Under Swift 6 strict
// concurrency, calling a @MainActor method from that thread triggers
// dispatch_assert_queue_fail (SIGTRAP) and kills the process. We
// instead guard mutable state with NSLock and let the tap callback
// run wherever AVFoundation calls it.
//
// Locking discipline: `lock` protects ALL mutable state shared with
// the render thread — `samples`, `_isRunning`, `latestLevel`,
// `latestLevelSequence`, `recordingGeneration`, the engine-open flag,
// AND the converter trio (`converter`, `converterInputFormat`,
// `manuallyMixInputToMono`). The trio is written on the main thread
// in startEngine/stopEngine and read in handleTap on AVFoundation's
// render thread; removeTap(onBus:) does NOT wait for in-flight tap
// callbacks, so an unlocked read could race stopEngine nil-ing the
// converter (an unsynchronised ARC pointer read — potential
// use-after-free). handleTap snapshots the trio once, inside the
// same lock acquisition that reads `_isRunning`, and works off the
// snapshots; a straggler callback then keeps the old converter
// alive through its own strong reference, which is safe.
// `configurationObserver` and `onConfigurationChange` are
// main-thread-only: the observer is registered with queue: .main so
// the notification callback runs on the same thread that installs
// the observer and that clears `onConfigurationChange` at
// termination.

private struct CapturedAudioSegments {
    let segments: [[Float]]
    let sampleCount: Int

    func flattened() -> [Float] {
        guard sampleCount > 0 else { return [] }
        var out: [Float] = []
        out.reserveCapacity(sampleCount)
        for segment in segments {
            out.append(contentsOf: segment)
        }
        return out
    }
}

private struct AudioSampleAccumulator {
    private var segments: [[Float]] = []
    private(set) var sampleCount = 0

    mutating func append(_ segment: [Float]) {
        guard !segment.isEmpty else { return }
        segments.append(segment)
        sampleCount += segment.count
    }

    mutating func removeAll(keepingCapacity: Bool) {
        segments.removeAll(keepingCapacity: keepingCapacity)
        sampleCount = 0
    }

    mutating func drain() -> CapturedAudioSegments {
        let captured = CapturedAudioSegments(segments: segments,
                                             sampleCount: sampleCount)
        segments.removeAll(keepingCapacity: true)
        sampleCount = 0
        return captured
    }

    /// RMS of the newest samples without flattening the full recording.
    /// The caller supplies the number of 16 kHz samples that define the tail.
    func tailRMS(maxSampleCount: Int) -> Double {
        guard maxSampleCount > 0 else { return 0 }
        var remaining = maxSampleCount
        var sumSquares = 0.0
        var finiteCount = 0

        for segment in segments.reversed() {
            let take = min(remaining, segment.count)
            guard take > 0 else { continue }
            for sample in segment.suffix(take) where sample.isFinite {
                let clamped = max(-1, min(1, sample))
                sumSquares += Double(clamped * clamped)
                finiteCount += 1
            }
            remaining -= take
            if remaining == 0 { break }
        }

        guard finiteCount > 0 else { return 0 }
        return sqrt(sumSquares / Double(finiteCount))
    }
}

fileprivate struct PostReleaseTailMeasurement: Equatable {
    let tailRMS: Double
    let silenceThreshold: Double
    let sampleSequence: UInt64

    init(tailRMS: Double,
         silenceThreshold: Double,
         sampleSequence: UInt64 = 0) {
        self.tailRMS = tailRMS
        self.silenceThreshold = silenceThreshold
        self.sampleSequence = sampleSequence
    }
}

private enum PostReleaseCaptureDecision: Equatable {
    case wait(TimeInterval)
    case finishSilence
    case finishMaximum
}

private func postReleaseSilenceThreshold(peakRMS: Double) -> Double {
    min(POST_RELEASE_MAX_SILENCE_RMS,
        max(POST_RELEASE_ABSOLUTE_SILENCE_RMS,
            peakRMS * POST_RELEASE_RELATIVE_SILENCE))
}

private func postReleaseCaptureDecision(elapsed: TimeInterval,
                                        measurement: PostReleaseTailMeasurement,
                                        receivedNewSamples: Bool = true) -> PostReleaseCaptureDecision {
    let elapsed = max(0, elapsed)
    if elapsed >= POST_RELEASE_MAX_CAPTURE_SECONDS {
        return .finishMaximum
    }
    if elapsed < POST_RELEASE_MIN_CAPTURE_SECONDS {
        return .wait(POST_RELEASE_MIN_CAPTURE_SECONDS - elapsed)
    }
    // AVAudioEngine documents bufferSize as a request: the implementation may
    // choose another size. Never let the first timer merely re-score the
    // pre-release tail while the boundary buffer is still pending.
    if !receivedNewSamples {
        return .wait(min(POST_RELEASE_CAPTURE_CHECK_SECONDS,
                         POST_RELEASE_MAX_CAPTURE_SECONDS - elapsed))
    }
    if measurement.tailRMS <= measurement.silenceThreshold {
        return .finishSilence
    }
    return .wait(min(POST_RELEASE_CAPTURE_CHECK_SECONDS,
                     POST_RELEASE_MAX_CAPTURE_SECONDS - elapsed))
}

func selectedMonoMixChannelIndices(channelRMS: [Double]) -> [Int] {
    let peak = channelRMS.max() ?? 0
    let active = channelRMS.enumerated()
        .filter { pair in peak > 0 && pair.element >= peak * 0.25 }
        .map { $0.offset }
    return active.isEmpty ? [0] : active
}

func channelRMSValues(channels: UnsafePointer<UnsafeMutablePointer<Float>>,
                      channelCount: Int,
                      frameCount: Int) -> [Double] {
    guard channelCount > 0, frameCount > 0 else { return [] }
    var rms = Array(repeating: 0.0, count: channelCount)
    for channelIndex in 0..<channelCount {
        var sumSquares = 0.0
        let source = channels[channelIndex]
        for frameIndex in 0..<frameCount {
            let sample = source[frameIndex]
            guard sample.isFinite else { continue }
            let clamped = max(-1, min(1, sample))
            sumSquares += Double(clamped * clamped)
        }
        rms[channelIndex] = sqrt(sumSquares / Double(frameCount))
    }
    return rms
}

func writeMonoMix(channels: UnsafePointer<UnsafeMutablePointer<Float>>,
                  selectedChannels: [Int],
                  frameCount: Int,
                  to mono: UnsafeMutablePointer<Float>) {
    guard frameCount > 0 else { return }
    let selectedChannels = selectedChannels.isEmpty ? [0] : selectedChannels
    let scale = Float(1.0 / Double(selectedChannels.count))
    for frameIndex in 0..<frameCount {
        var mixed: Float = 0
        for channelIndex in selectedChannels {
            mixed += channels[channelIndex][frameIndex] * scale
        }
        mono[frameIndex] = mixed
    }
}

final class AudioCapture: @unchecked Sendable {
    private var engine = AVAudioEngine()
    private var converter: AVAudioConverter?
    private var converterInputFormat: AVAudioFormat?
    private var manuallyMixInputToMono = false
    private let lock = NSLock()
    private var samples = AudioSampleAccumulator()
    private var _isRunning = false
    private var latestLevel: Float = 0
    private var latestLevelSequence: UInt64 = 0
    private var peakRMS = 0.0
    private var recordingGeneration: UInt64 = 0
    private var engineStarted = false
    private var configurationObserver: NSObjectProtocol?

    var onConfigurationChange: (@Sendable () -> Void)?

    var isRunning: Bool {
        lock.lock(); defer { lock.unlock() }
        return _isRunning
    }

    var isEngineStarted: Bool {
        lock.lock(); defer { lock.unlock() }
        return engineStarted
    }

    func startEngine(inputDevicePreference: String = "",
                     recordingImmediately: Bool = false) throws {
        if isEngineStarted {
            if recordingImmediately {
                beginRecording()
            }
            return
        }

        let input = engine.inputNode
        applyInputDevicePreference(inputDevicePreference, to: input)
        // inputFormat(forBus:), not outputFormat(forBus:). The latter reports
        // the engine graph's rate, which follows the default *output* device.
        // applyInputDevicePreference() has just repointed the audio unit at a
        // different input device, so the two disagree whenever output and input
        // hardware run at different rates — a 44.1 kHz Bluetooth headset
        // alongside a 48 kHz microphone. A tap installed against the graph rate
        // then receives buffers of pure silence, with no error raised anywhere:
        // the capture simply comes back empty and is discarded as too short.
        let inputFormat = input.inputFormat(forBus: 0)

        guard let targetFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: SAMPLE_RATE,
            channels: 1,
            interleaved: false
        ) else { throw NSError(domain: "Presspeech", code: -1) }

        let sourceFormat = converterSourceFormat(for: inputFormat)
        let mixToMono = inputFormat.channelCount > 1 && sourceFormat.channelCount == 1
        let newConverter = AVAudioConverter(from: sourceFormat, to: targetFormat)
        // Publish the converter trio under the lock — handleTap reads
        // them on the render thread (see the locking-discipline note
        // on the class comment).
        lock.lock()
        converterInputFormat = sourceFormat
        manuallyMixInputToMono = mixToMono
        converter = newConverter
        if recordingImmediately {
            recordingGeneration &+= 1
            samples.removeAll(keepingCapacity: true)
            latestLevel = 0
            latestLevelSequence &+= 1
            peakRMS = 0
            _isRunning = true
        }
        lock.unlock()
        let mixLabel = mixToMono ? " via manual mono mix" : ""
        log("AudioCapture: input \(inputFormat.sampleRate) Hz \(inputFormat.channelCount)ch\(mixLabel) → \(targetFormat.sampleRate) Hz mono")

        // Capture targetFormat by value into the closure. self is
        // weak so the engine doesn't keep AudioCapture alive past
        // its owner. The closure runs on AVFoundation's audio
        // thread — handleTap is non-isolated and uses NSLock for
        // any shared-state access.
        input.installTap(onBus: 0, bufferSize: 4096, format: inputFormat) { [weak self] buffer, _ in
            self?.handleTap(buffer: buffer, target: targetFormat)
        }

        do {
            try engine.start()
        } catch {
            input.removeTap(onBus: 0)
            clearConverterState()
            resetEngineInstance()
            throw error
        }
        lock.lock()
        engineStarted = true
        lock.unlock()
        installConfigurationObserver()
        log("AudioCapture: engine started")
    }

    func startRecording(inputDevicePreference: String = "") throws {
        if isEngineStarted {
            beginRecording()
            return
        }
        try startEngine(inputDevicePreference: inputDevicePreference,
                        recordingImmediately: true)
    }

    func stopEngine() {
        removeConfigurationObserver()

        let wasEngineStarted = isEngineStarted
        clearStoppedCaptureState()

        guard wasEngineStarted else { return }
        engine.inputNode.removeTap(onBus: 0)
        resetEngineInstance()
    }

    private func clearStoppedCaptureState() {
        lock.lock()
        _isRunning = false
        latestLevel = 0
        latestLevelSequence &+= 1
        peakRMS = 0
        recordingGeneration &+= 1
        samples.removeAll(keepingCapacity: true)
        engineStarted = false
        // Clear the converter trio under the same lock the render
        // thread snapshots them with — removeTap below does not wait
        // for an in-flight tap callback. A callback that already took
        // its snapshot keeps the old converter alive through its own
        // strong reference, which is safe.
        converter = nil
        converterInputFormat = nil
        manuallyMixInputToMono = false
        lock.unlock()
    }

    private func clearConverterState() {
        lock.lock()
        converter = nil
        converterInputFormat = nil
        manuallyMixInputToMono = false
        lock.unlock()
    }

    private func resetEngineInstance() {
        engine.stop()
        engine.reset()
        engine = AVAudioEngine()
    }

    func beginRecording() {
        lock.lock(); defer { lock.unlock() }
        recordingGeneration &+= 1
        samples.removeAll(keepingCapacity: true)
        latestLevel = 0
        latestLevelSequence &+= 1
        peakRMS = 0
        _isRunning = true
    }

    private func installConfigurationObserver() {
        removeConfigurationObserver()
        // queue: .main — the notification can be posted from an
        // AVFoundation worker thread, and `onConfigurationChange` is
        // an unsynchronised var that the owner clears on the main
        // thread at termination. Hopping to the main queue makes the
        // read of the callback and the nil-ing write happen on the
        // same thread, so a config change racing teardown can never
        // observe a half-released closure.
        configurationObserver = NotificationCenter.default.addObserver(
            forName: .AVAudioEngineConfigurationChange,
            object: engine,
            queue: .main
        ) { [weak self] _ in
            self?.onConfigurationChange?()
        }
    }

    private func removeConfigurationObserver() {
        if let configurationObserver {
            NotificationCenter.default.removeObserver(configurationObserver)
            self.configurationObserver = nil
        }
    }

    /// Stops recording and returns the captured samples.
    func endRecording() -> [Float] {
        lock.lock()
        _isRunning = false
        latestLevel = 0
        latestLevelSequence &+= 1
        recordingGeneration &+= 1
        let captured = samples.drain()
        peakRMS = 0
        lock.unlock()
        return captured.flattened()
    }

    func latestRecordingLevelSnapshot() -> (level: Float, sequence: UInt64) {
        lock.lock(); defer { lock.unlock() }
        return _isRunning ? (latestLevel, latestLevelSequence) : (0, latestLevelSequence)
    }

    fileprivate func postReleaseTailMeasurement() -> PostReleaseTailMeasurement {
        lock.lock(); defer { lock.unlock() }
        let tailSamples = max(1, Int(SAMPLE_RATE * POST_RELEASE_TAIL_SECONDS))
        return PostReleaseTailMeasurement(
            tailRMS: samples.tailRMS(maxSampleCount: tailSamples),
            silenceThreshold: postReleaseSilenceThreshold(peakRMS: peakRMS),
            sampleSequence: latestLevelSequence
        )
    }

    private func handleTap(buffer: AVAudioPCMBuffer, target: AVAudioFormat) {
        // Snapshot the running flag AND the converter trio in one
        // lock acquisition; bail fast if we're not recording so we
        // don't pay conversion cost for nothing. Working off the
        // snapshots keeps this callback consistent even if
        // stopEngine() clears the fields mid-flight — removeTap does
        // not wait for us, and the local strong reference keeps the
        // converter alive for the rest of this call.
        lock.lock()
        let running = _isRunning
        let generation = recordingGeneration
        let converter = self.converter
        let monoMixFormat = converterInputFormat
        let mixToMono = manuallyMixInputToMono
        lock.unlock()
        guard running, let converter else { return }

        let converterInput = preparedConverterInputBuffer(from: buffer,
                                                          mixToMono: mixToMono,
                                                          monoFormat: monoMixFormat) ?? buffer
        let ratio = target.sampleRate / converterInput.format.sampleRate
        let outCap = AVAudioFrameCount(Double(converterInput.frameLength) * ratio + 1024)
        guard let out = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: outCap) else { return }

        // .noDataNow vs .endOfStream: this is reusing the same
        // AVAudioConverter across every tap callback (~50 Hz). If we
        // signal .endOfStream after the buffer, the converter goes
        // into a terminal state and produces 0 samples on every
        // subsequent call — exactly the "first capture was 0.10s,
        // every press after that was 0.00s" bug we saw before this
        // fix. .noDataNow means "I'm out of input *for this call*,
        // but the stream continues" and leaves the converter usable.
        let inputProvider = AudioConverterInputProvider(buffer: converterInput)
        var error: NSError?
        let status = converter.convert(to: out, error: &error) { _, outStatus in
            inputProvider.provide(outStatus: outStatus)
        }
        if status == .error {
            log("AudioCapture: convert error: \(error?.localizedDescription ?? "?")")
            return
        }
        guard let ch = out.floatChannelData?[0] else { return }
        let frameCount = Int(out.frameLength)
        var arr: [Float] = []
        arr.reserveCapacity(frameCount)
        var sumSquares: Double = 0
        var finiteSampleCount = 0
        for sample in UnsafeBufferPointer(start: ch, count: frameCount) {
            arr.append(sample)
            guard sample.isFinite else { continue }
            let clamped = max(-1, min(1, sample))
            sumSquares += Double(clamped * clamped)
            finiteSampleCount += 1
        }
        let level = normalizedAudioLevel(sumSquares: sumSquares,
                                         sampleCount: finiteSampleCount)
        let rawRMS = finiteSampleCount > 0
            ? sqrt(sumSquares / Double(finiteSampleCount))
            : 0

        // Re-check running under lock — endRecording() might have
        // fired during conversion, then a rapid next recording may
        // already have started. The generation token keeps straggler
        // frames out of the next clip.
        lock.lock()
        if _isRunning && recordingGeneration == generation {
            samples.append(arr)
            latestLevel = level
            latestLevelSequence &+= 1
            peakRMS = max(peakRMS, rawRMS)
        }
        lock.unlock()
    }

    private func converterSourceFormat(for inputFormat: AVAudioFormat) -> AVAudioFormat {
        guard inputFormat.channelCount > 1,
              let monoFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32,
                                             sampleRate: inputFormat.sampleRate,
                                             channels: 1,
                                             interleaved: false) else {
            return inputFormat
        }
        return monoFormat
    }

    /// `mixToMono` / `monoFormat` are the caller's lock-held
    /// snapshots of `manuallyMixInputToMono` / `converterInputFormat`
    /// — this runs on the render thread and must not read the shared
    /// fields directly (see the locking-discipline note on the class
    /// comment).
    private func preparedConverterInputBuffer(from buffer: AVAudioPCMBuffer,
                                              mixToMono: Bool,
                                              monoFormat: AVAudioFormat?) -> AVAudioPCMBuffer? {
        guard mixToMono else { return buffer }
        guard let monoFormat,
              let channels = buffer.floatChannelData else {
            return nil
        }

        let channelCount = Int(buffer.format.channelCount)
        let frameCount = Int(buffer.frameLength)
        guard channelCount > 1, frameCount > 0 else { return buffer }
        guard let out = AVAudioPCMBuffer(pcmFormat: monoFormat,
                                         frameCapacity: AVAudioFrameCount(frameCount)),
              let mono = out.floatChannelData?[0] else {
            return nil
        }

        let rms = channelRMSValues(channels: channels,
                                   channelCount: channelCount,
                                   frameCount: frameCount)
        writeMonoMix(channels: channels,
                     selectedChannels: selectedMonoMixChannelIndices(channelRMS: rms),
                     frameCount: frameCount,
                     to: mono)
        out.frameLength = AVAudioFrameCount(frameCount)
        return out
    }

    private func applyInputDevicePreference(_ preference: String, to input: AVAudioInputNode) {
        let trimmed = preference.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        guard !isDefaultAggregateAudioInputPreference(trimmed) else { return }

        guard let device = audioInputDevice(matching: trimmed) else {
            log("AudioCapture: saved input device unavailable, using system default")
            return
        }
        guard let unit = input.audioUnit else {
            log("AudioCapture: input audio unit unavailable, using system default")
            return
        }

        var deviceID = device.id
        let status = AudioUnitSetProperty(unit,
                                          kAudioOutputUnitProperty_CurrentDevice,
                                          kAudioUnitScope_Global,
                                          0,
                                          &deviceID,
                                          UInt32(MemoryLayout<AudioDeviceID>.size))
        guard status == noErr else {
            log("AudioCapture: input device switch failed (\(formattedOSStatus(status))), using system default")
            return
        }
        log("AudioCapture: selected input \(device.name)")
    }
}

private final class AudioConverterInputProvider: @unchecked Sendable {
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
            outStatus.pointee = .noDataNow
            return nil
        }

        didProvideBuffer = true
        outStatus.pointee = .haveData
        return buffer
    }
}

// MARK: - Transcription worker
//
// Owns the FluidAudio AsrManager. The Apple Neural Engine doesn't
// tolerate concurrent inference calls against the same compiled
// CoreML graph — but the actor alone does NOT keep that contract.
// Actors are reentrant at suspension points: while
// `await asr.transcribe(...)` is suspended, a second transcribe()
// call would enter the actor and start concurrent inference. The
// real guard is PresspeechApp.isBusy, which ensures the app never
// issues a second transcribe while one is in flight. The `inFlight`
// flag below is a cheap defensive backstop should that invariant
// ever break: it refuses (and, in DEBUG, asserts on) a re-entrant
// call instead of corrupting ANE state.

private enum LoadedSpeechEngine {
    case parakeetV3(AsrManager)
}

actor TranscriptionWorker {
    private var engine: LoadedSpeechEngine?
    private var loadedProfile: SpeechModelProfile?
    private(set) var ready = false
    /// Reentrancy backstop — see the comment above. True for the full
    /// duration of transcribe(), including across its await.
    private var inFlight = false

    func load(profile requestedProfile: SpeechModelProfile,
              progressHandler: ProgressHandler? = nil) async throws {
        let profile = requestedProfile.productionProfile
        if requestedProfile != profile {
            log("ASR: ignoring unsupported speech model \(requestedProfile.shortName); using \(profile.shortName)")
        }
        if ready, engine != nil, loadedProfile == profile {
            log("ASR: \(profile.shortName) already ready")
            return
        }

        if engine != nil {
            await unload()
        }

        log("ASR: downloading + verifying + loading \(profile.shortName) CoreML weights…")
        let t0 = Date()
        engine = .parakeetV3(try await loadParakeetV3(progressHandler: progressHandler))
        loadedProfile = profile
        ready = true
        log("ASR: \(profile.shortName) ready in \(String(format: "%.2f", Date().timeIntervalSince(t0))) s")
    }

    private func loadParakeetV3(progressHandler: ProgressHandler?) async throws -> AsrManager {
        if !speechModelCacheExists(for: .multilingualV3) {
            try assertSufficientDiskSpaceForSpeechModelDownload(profile: .multilingualV3)
        }
        var modelDirectory = try await AsrModels.download(version: .v3,
                                                          progressHandler: progressHandler)
        do {
            try ModelIntegrity.verifyParakeetV3Model(at: modelDirectory)
        } catch {
            log("ASR: model integrity check failed; redownloading once: \(error.localizedDescription)")
            try assertSufficientDiskSpaceForSpeechModelDownload(profile: .multilingualV3)
            modelDirectory = try await AsrModels.download(force: true,
                                                          version: .v3,
                                                          progressHandler: progressHandler)
            try ModelIntegrity.verifyParakeetV3Model(at: modelDirectory)
        }
        let models = try await AsrModels.load(from: modelDirectory,
                                              version: .v3,
                                              progressHandler: progressHandler)
        return AsrManager(config: .default, models: models)
    }

    func transcribe(samples: [Float], language: Language? = nil) async throws -> String {
        guard let engine else { throw NSError(domain: "Presspeech", code: -2) }
        guard !inFlight else {
            log("ASR: transcribe re-entered while another transcription is in flight — refusing (PresspeechApp.isBusy should make this impossible)")
            assertionFailure("TranscriptionWorker.transcribe re-entered across a suspension point")
            throw NSError(domain: "Presspeech", code: -3)
        }
        inFlight = true
        defer { inFlight = false }
        switch engine {
        case .parakeetV3(let asr):
            var state = try TdtDecoderState()
            let result = try await asr.transcribe(samples, decoderState: &state, language: language)
            return result.text
        }
    }

    func unload() async {
        engine = nil
        loadedProfile = nil
        ready = false
        log("ASR: unloaded")
    }
}

// MARK: - Transcript corrections
//
// Deterministic local rewrite pass for words or phrases the model
// consistently mishears. Corrections are applied to the transcript
// text before paste/history, never to audio, and replacement text is
// used exactly as the user typed it.

enum TranscriptCorrector {
    private struct Match {
        let range: NSRange
        let replacement: String
    }

    static func apply(to text: String, corrections: [TranscriptCorrection]) -> (text: String, appliedCount: Int) {
        let active = normalizedTranscriptCorrections(corrections)
            .sorted { lhs, rhs in
                if lhs.source.count != rhs.source.count { return lhs.source.count > rhs.source.count }
                return lhs.source.localizedCaseInsensitiveCompare(rhs.source) == .orderedAscending
            }

        guard !text.isEmpty, !active.isEmpty else { return (text, 0) }

        let fullRange = NSRange(text.startIndex..<text.endIndex, in: text)
        var matches: [Match] = []

        for correction in active {
            guard let pattern = pattern(for: correction.source),
                  let regex = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive])
            else { continue }

            regex.enumerateMatches(in: text, range: fullRange) { match, _, _ in
                guard let range = match?.range, range.location != NSNotFound else { return }
                guard !matches.contains(where: { NSIntersectionRange($0.range, range).length > 0 }) else { return }
                matches.append(Match(range: range, replacement: correction.replacement))
            }
        }

        guard !matches.isEmpty else { return (text, 0) }

        let rewritten = NSMutableString(string: text)
        for match in matches.sorted(by: { $0.range.location > $1.range.location }) {
            rewritten.replaceCharacters(in: match.range, with: match.replacement)
        }
        return (rewritten as String, matches.count)
    }

    private static func pattern(for source: String) -> String? {
        let parts = source
            .split(whereSeparator: { $0.isWhitespace })
            .map { NSRegularExpression.escapedPattern(for: String($0)) }
        guard !parts.isEmpty else { return nil }
        return #"(?<![\p{L}\p{N}_])"# + parts.joined(separator: #"\s+"#) + #"(?![\p{L}\p{N}_])"#
    }
}

// MARK: - Spoken formatting commands
//
// An optional deterministic pass for people who prefer explicit voice
// formatting. This deliberately does not use a language model: each
// documented phrase has one auditable result, and the pass stays off
// until the user enables it. The ASR commonly appends punctuation to a
// command phrase ("new paragraph."), so structural commands consume
// that punctuation rather than leaving a stray full stop behind.

enum SpokenFormattingCommandProcessor {
    private struct Command {
        let phrases: [String]
        let replacement: String
        let consumesTrailingPunctuation: Bool
    }

    private static let englishCommands: [Command] = [
        Command(phrases: ["new paragraph"], replacement: "\n\n", consumesTrailingPunctuation: true),
        Command(phrases: ["new line"], replacement: "\n", consumesTrailingPunctuation: true),
        Command(phrases: ["bullet point"], replacement: "\n• ", consumesTrailingPunctuation: true),
        Command(phrases: ["open quote"], replacement: "“", consumesTrailingPunctuation: true),
        Command(phrases: ["close quote"], replacement: "”", consumesTrailingPunctuation: true),
        Command(phrases: ["open parenthesis", "open parentheses"], replacement: "(", consumesTrailingPunctuation: true),
        Command(phrases: ["close parenthesis", "close parentheses"], replacement: ")", consumesTrailingPunctuation: true),
        Command(phrases: ["question mark"], replacement: "?", consumesTrailingPunctuation: true),
        Command(phrases: ["exclamation point", "exclamation mark"], replacement: "!", consumesTrailingPunctuation: true),
        Command(phrases: ["full stop", "period"], replacement: ".", consumesTrailingPunctuation: true),
        Command(phrases: ["semicolon"], replacement: ";", consumesTrailingPunctuation: true),
        Command(phrases: ["colon"], replacement: ":", consumesTrailingPunctuation: true),
        Command(phrases: ["comma"], replacement: ",", consumesTrailingPunctuation: true),
    ]

    // Keep command matching tied to the explicit language hint. Running every
    // language's short punctuation words over every transcript would turn
    // ordinary words such as French "point" into punctuation unexpectedly.
    // Auto-detect retains the original English behavior for compatibility.
    // French spellings follow the names in Apple's current macOS Dictation
    // command reference; apostrophe and hyphen variants tolerate ASR output.
    private static let frenchCommands: [Command] = [
        Command(phrases: ["nouveau paragraphe"], replacement: "\n\n", consumesTrailingPunctuation: true),
        Command(phrases: ["nouvelle ligne"], replacement: "\n", consumesTrailingPunctuation: true),
        Command(phrases: ["guillemet ouvrant"], replacement: "«", consumesTrailingPunctuation: true),
        Command(phrases: ["guillemet fermant"], replacement: "»", consumesTrailingPunctuation: true),
        Command(phrases: ["parenthèse ouvrante"], replacement: "(", consumesTrailingPunctuation: true),
        Command(phrases: ["parenthèse fermante"], replacement: ")", consumesTrailingPunctuation: true),
        Command(phrases: ["point d'interrogation", "point d’interrogation"], replacement: "?", consumesTrailingPunctuation: true),
        Command(phrases: ["point d'exclamation", "point d’exclamation"], replacement: "!", consumesTrailingPunctuation: true),
        Command(phrases: ["point-virgule", "point virgule"], replacement: ";", consumesTrailingPunctuation: true),
        Command(phrases: ["deux-points", "deux points"], replacement: ":", consumesTrailingPunctuation: true),
        Command(phrases: ["virgule"], replacement: ",", consumesTrailingPunctuation: true),
        Command(phrases: ["point"], replacement: ".", consumesTrailingPunctuation: true),
    ]

    private static func commands(for language: DictationLanguage) -> [Command] {
        language == .french ? frenchCommands : englishCommands
    }

    static func apply(to text: String,
                      language: DictationLanguage = .auto) -> (text: String, appliedCount: Int) {
        guard !text.isEmpty else { return (text, 0) }

        var result = text
        var appliedCount = 0
        for command in commands(for: language) {
            let alternatives = command.phrases.map { phrase in
                phrase
                    .split(whereSeparator: { $0.isWhitespace })
                    .map { NSRegularExpression.escapedPattern(for: String($0)) }
                    .joined(separator: #"\s+"#)
            }.joined(separator: "|")
            let trailing = command.consumesTrailingPunctuation ? #"(?:[.,])?"# : ""
            let pattern = #"(?<![\p{L}\p{N}_])(?:"# + alternatives + #")"# + trailing + #"(?![\p{L}\p{N}_])"#
            guard let regex = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive]) else {
                continue
            }
            let range = NSRange(result.startIndex..<result.endIndex, in: result)
            let matches = regex.numberOfMatches(in: result, range: range)
            guard matches > 0 else { continue }
            result = regex.stringByReplacingMatches(in: result,
                                                     range: range,
                                                     withTemplate: NSRegularExpression.escapedTemplate(for: command.replacement))
            appliedCount += matches
        }

        guard appliedCount > 0 else { return (text, 0) }

        result = result.replacingOccurrences(of: #"[ \t]*\n[ \t]*"#, with: "\n", options: .regularExpression)
        result = result.replacingOccurrences(of: #"\n{3,}"#, with: "\n\n", options: .regularExpression)
        result = result.replacingOccurrences(of: #"[ \t]{2,}"#, with: " ", options: .regularExpression)
        result = result.replacingOccurrences(of: #"[ \t]+([,.;:!?])"#, with: "$1", options: .regularExpression)
        result = result.replacingOccurrences(of: #"([“(])[ \t]+"#, with: "$1", options: .regularExpression)
        result = result.replacingOccurrences(of: #"[ \t]+([”)])"#, with: "$1", options: .regularExpression)
        result = result.trimmingCharacters(in: .whitespacesAndNewlines)
        return (result, appliedCount)
    }
}

// MARK: - Filler word removal
//
// Deterministic regex pass that strips standalone non-word fillers
// ("um", "uh", "ah", "er", "erm", "hmm") and cleans up the punctuation
// artifacts left behind. Intentionally conservative: skips ambiguous
// fillers ("like", "you know") that have legitimate non-filler uses,
// and only fires when the user explicitly enables it via Settings →
// Remove filler words. Applied *after* TranscriptCorrector so explicit
// user corrections always win over filler stripping.

enum FillerWordRemover {
    private enum CapitalizationRepairTarget: Hashable {
        case start
        case afterSentenceTerminator(Int)
    }

    /// Non-word interjections only. "like" and "you know" are excluded
    /// because they have valid non-filler meanings ("I like cats", "you
    /// know who"). Most entries are regex fragments that allow the
    /// trailing letter to repeat, since real-world fillers stretch out
    /// ("ummm", "uhhhh", "ahhh", "hmmm") and the word-boundary lookahead
    /// would otherwise reject them. "er" and "erm" deliberately have no
    /// repeat quantifier: "er+" would also match the real word "err".
    private static let fillerPatterns = ["um+", "uh+", "ah+", "er", "erm", "hm+"]

    static func apply(to text: String) -> (text: String, removedCount: Int) {
        guard !text.isEmpty else { return (text, 0) }

        // Word-boundary lookarounds include `'` (so "it's" stays one
        // token) and `-` (so "uh-huh", "uh-oh" don't get split apart).
        let alternation = fillerPatterns.joined(separator: "|")
        let pattern = #"(?i)(?<![\p{L}\p{N}'\-])("# + alternation + #")(?![\p{L}\p{N}'\-])"#
        guard let regex = try? NSRegularExpression(pattern: pattern) else {
            return (text, 0)
        }

        let fullRange = NSRange(text.startIndex..<text.endIndex, in: text)
        let matches = regex.matches(in: text, range: fullRange)
        guard !matches.isEmpty else { return (text, 0) }

        // Preserve sentence-start casing when the removed filler carried
        // the capital ("Um, hello." and "First. Um hello.").
        let capitalizationRepairTargets = capitalizationRepairTargets(for: matches,
                                                                      in: text)

        let mutable = NSMutableString(string: text)
        for match in matches.reversed() {
            mutable.replaceCharacters(in: match.range, with: "")
        }
        var result = mutable as String

        // Clean up artifacts left behind by removal:
        //   1. Comma runs left by consecutive fillers: "x, , , y" →
        //      "x, y". Quantified so a run of ANY length collapses in
        //      one pass — a non-overlapping ",\s*," pattern consumed
        //      pairs and left ",," behind for two-plus fillers.
        //   2. Whitespace before punctuation: "x ." → "x."
        //   3. Orphan comma glued onto terminal punctuation by pass 2:
        //      "x,." → "x." ("That's all, um." must not end ",.")
        //   4. Multiple consecutive spaces → single space
        //   5. Leading punctuation / whitespace, including "?" and "!"
        //      so a removed sentence-initial filler takes its terminal
        //      punctuation with it ("Um? What?" → "What?")
        //   6. Orphan punctuation after an existing sentence terminator:
        //      "x. , y" → "x. y" when removing "Um," after the period.
        //   7. Trailing whitespace
        result = result.replacingOccurrences(of: #"\s*,(?:\s*,)+"#, with: ",", options: .regularExpression)
        result = result.replacingOccurrences(of: #"([.!?])\s+[,.;:!?]+\s*"#, with: "$1 ", options: .regularExpression)
        result = result.replacingOccurrences(of: #"\s+([.,!?;:])"#, with: "$1", options: .regularExpression)
        result = result.replacingOccurrences(of: #",+([.!?;:])"#, with: "$1", options: .regularExpression)
        result = result.replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
        result = result.replacingOccurrences(of: #"^[\s,.;:!?]+"#, with: "", options: .regularExpression)
        result = result.trimmingCharacters(in: .whitespacesAndNewlines)
        result = restoringCapitalization(in: result,
                                         targets: capitalizationRepairTargets)

        return (result, matches.count)
    }

    private static func capitalizationRepairTargets(for matches: [NSTextCheckingResult],
                                                     in text: String) -> Set<CapitalizationRepairTarget> {
        Set(matches.compactMap { match in
            guard let range = Range(match.range, in: text),
                  text[range].first?.isUppercase == true else {
                return nil
            }
            return capitalizationRepairTarget(for: range, in: text)
        })
    }

    private static func capitalizationRepairTarget(for range: Range<String.Index>,
                                                   in text: String) -> CapitalizationRepairTarget? {
        var index = range.lowerBound
        while index > text.startIndex {
            let previous = text.index(before: index)
            let character = text[previous]
            if character.isWhitespace || isBoundaryWrapper(character) {
                index = previous
                continue
            }
            guard isSentenceTerminator(character) else { return nil }
            return .afterSentenceTerminator(sentenceTerminatorOrdinal(at: previous,
                                                                      in: text))
        }
        return .start
    }

    private static func sentenceTerminatorOrdinal(at target: String.Index,
                                                  in text: String) -> Int {
        var ordinal = 0
        var index = text.startIndex
        while index <= target {
            if isSentenceTerminator(text[index]) {
                ordinal += 1
            }
            index = text.index(after: index)
        }
        return ordinal
    }

    private static func restoringCapitalization(in text: String,
                                                targets: Set<CapitalizationRepairTarget>) -> String {
        guard !targets.isEmpty, !text.isEmpty else { return text }

        let sentenceTargets = Set(targets.compactMap { target -> Int? in
            guard case .afterSentenceTerminator(let ordinal) = target else { return nil }
            return ordinal
        })
        var result = ""
        result.reserveCapacity(text.count)
        var sentenceTerminatorOrdinal = 0
        var shouldCapitalizeNextWord = targets.contains(.start)

        for character in text {
            if shouldCapitalizeNextWord {
                if character.isLowercase {
                    result += character.uppercased()
                    shouldCapitalizeNextWord = false
                    continue
                }
                if character.isLetter || character.isNumber {
                    shouldCapitalizeNextWord = false
                }
            }

            result.append(character)

            if isSentenceTerminator(character) {
                sentenceTerminatorOrdinal += 1
                if sentenceTargets.contains(sentenceTerminatorOrdinal) {
                    shouldCapitalizeNextWord = true
                }
            } else if shouldCapitalizeNextWord,
                      !character.isWhitespace,
                      !isBoundaryWrapper(character),
                      !isOrphanSeparator(character) {
                shouldCapitalizeNextWord = false
            }
        }

        return result
    }

    private static func isSentenceTerminator(_ character: Character) -> Bool {
        character == "." || character == "!" || character == "?"
    }

    private static func isBoundaryWrapper(_ character: Character) -> Bool {
        "\"'“”‘’([{".contains(character)
    }

    private static func isOrphanSeparator(_ character: Character) -> Bool {
        ",.;:!?".contains(character)
    }
}

// MARK: - Recording lifecycle decisions

private enum RecordingReleaseAction: Equatable {
    case discardTooShort(duration: Double)
    case transcribe(duration: Double)
}

private enum DictationMenuControlAction: Int, Equatable {
    case start
    case stop
}

private struct DictationMenuControlState: Equatable {
    let action: DictationMenuControlAction
    let title: String
    let isEnabled: Bool
    let help: String
}

private func dictationMenuControlState(isReady: Bool,
                                       isRecording: Bool,
                                       isBusy: Bool,
                                       isTerminating: Bool,
                                       hasMissingPermissions: Bool) -> DictationMenuControlState {
    if isRecording {
        return DictationMenuControlState(
            action: .stop,
            title: "Stop and Transcribe",
            isEnabled: !isTerminating,
            help: "Stop recording, transcribe locally, and paste into the window where recording began."
        )
    }

    let unavailableReason: String?
    if isTerminating {
        unavailableReason = "Presspeech is quitting."
    } else if isBusy {
        unavailableReason = "Wait for the current transcription to finish."
    } else if hasMissingPermissions {
        unavailableReason = "Finish the permission steps in Setup Checklist first."
    } else if !isReady {
        unavailableReason = "Wait for Presspeech to finish setup."
    } else {
        unavailableReason = nil
    }

    return DictationMenuControlState(
        action: .start,
        title: "Start Dictation",
        isEnabled: unavailableReason == nil,
        help: unavailableReason
            ?? "Start recording for the currently focused window; choose Stop and Transcribe when finished."
    )
}

private func recordingReleaseAction(capturedSampleCount: Int,
                                    sampleRate: Double = SAMPLE_RATE,
                                    minimumClipSeconds: Double = MIN_CLIP_SECONDS) -> RecordingReleaseAction {
    let duration = sampleRate > 0 ? Double(max(0, capturedSampleCount)) / sampleRate : 0
    return duration < minimumClipSeconds
        ? .discardTooShort(duration: duration)
        : .transcribe(duration: duration)
}

/// A release that captured nothing at all is not a short press — the tap ran
/// and delivered no samples. That is precisely what a capture-format mismatch
/// looks like from here: AVAudioEngine raises no error, the buffers are simply
/// silent, and the clip is discarded as too short with no hint as to why. Name
/// the likely cause instead of logging an empty clip and moving on.
private func noAudioCapturedHint(capturedSampleCount: Int) -> String? {
    guard capturedSampleCount == 0 else { return nil }
    return "release: no samples captured — the input tap delivered nothing. "
        + "Compare the \"AudioCapture: input\" rate logged above against the "
        + "input device's actual rate; a mismatch yields silence, not an error."
}

private struct DictationTextProcessingResult: Equatable {
    let text: String
    let appliedCorrectionCount: Int
    let appliedFormattingCommandCount: Int
    let removedFillerWordCount: Int
}

private func processedDictationText(rawTranscript: String,
                                    corrections: [TranscriptCorrection],
                                    spokenFormattingCommands: Bool,
                                    dictationLanguage: DictationLanguage,
                                    removeFillerWords: Bool) -> DictationTextProcessingResult {
    let trimmed = rawTranscript.trimmingCharacters(in: .whitespacesAndNewlines)
    let corrected = TranscriptCorrector.apply(to: trimmed, corrections: corrections)
    let formatted = spokenFormattingCommands
        ? SpokenFormattingCommandProcessor.apply(to: corrected.text,
                                                 language: dictationLanguage)
        : (text: corrected.text, appliedCount: 0)

    guard removeFillerWords else {
        return DictationTextProcessingResult(text: formatted.text,
                                             appliedCorrectionCount: corrected.appliedCount,
                                             appliedFormattingCommandCount: formatted.appliedCount,
                                             removedFillerWordCount: 0)
    }

    let stripped = FillerWordRemover.apply(to: formatted.text)
    return DictationTextProcessingResult(text: stripped.text,
                                         appliedCorrectionCount: corrected.appliedCount,
                                         appliedFormattingCommandCount: formatted.appliedCount,
                                         removedFillerWordCount: stripped.removedCount)
}

// MARK: - Text insertion
//
// Default path: write to general pasteboard, post Cmd+V. If that setup
// fails, fall back to direct Unicode events so a pasteboard problem
// does not automatically lose the transcript.
//
// When "Restore clipboard after paste" is enabled, the
// clipboard-paste path snapshots the user's previous clipboard (every
// item and type — text, images, files, RTF, links), pastes the
// transcript, then writes the snapshot back so dictation doesn't
// clobber whatever the user had copied. The restore is guarded two
// ways against the races that make blind round-tripping fragile: it
// runs on a short delay so the target app reads the transcript first,
// and it only fires if the pasteboard's changeCount still matches the
// value we set — if a paste-manager or the user copied something new
// in the meantime, we leave their content alone.

func pastedText(from correctedTranscript: String, suffix: PasteSuffix) -> String {
    switch suffix {
    case .appendSpace:
        return correctedTranscript + " "
    case .none:
        return correctedTranscript
    case .appendNewline:
        return correctedTranscript + "\n"
    }
}

func speechModelStartupStatusTitle(_ progress: DownloadProgress) -> String {
    switch progress.phase {
    case .listing:
        return "Checking speech model files…"
    case .downloading(let completedFiles, let totalFiles):
        guard totalFiles > 0 else { return "Loading cached speech model…" }
        let downloadFraction = min(max(progress.fractionCompleted / 0.5, 0), 1)
        let percent = min(100, max(0, Int((downloadFraction * 20).rounded()) * 5))
        return "Downloading speech model… \(percent)% (\(completedFiles)/\(totalFiles))"
    case .compiling:
        return "Preparing speech model…"
    }
}

func speechModelStartupProgressValue(_ progress: DownloadProgress) -> Double? {
    switch progress.phase {
    case .downloading(_, let totalFiles):
        guard totalFiles > 0 else { return nil }
        return min(max(progress.fractionCompleted / 0.5, 0), 1)
    case .listing, .compiling:
        return nil
    }
}

enum TextInsertionStrategy: String {
    case clipboardPaste
    case directUnicode

    var displayName: String {
        switch self {
        case .clipboardPaste: return "Clipboard paste"
        case .directUnicode: return "Direct Unicode typing"
        }
    }
}

/// The exact application window that owned the cursor when a recording began.
/// Accessibility permission is already a runtime requirement for synthetic
/// paste events, so using the focused AX window does not broaden Presspeech's
/// permission footprint. Keeping the opaque element (rather than a title or
/// document URL) also avoids retaining private window metadata.
struct DictationPasteTarget {
    let processIdentifier: pid_t
    let focusedWindow: AXUIElement
}

enum TextInsertionOutcome: Equatable {
    case inserted
    case copiedWithoutPasting
    case failed
}

func dictationCompletionNotice(processedText: String,
                                insertionOutcome: TextInsertionOutcome?,
                                keepsRecentTranscripts: Bool) -> DictationNotice? {
    guard !processedText.isEmpty else { return .noSpeechDetected }
    guard let insertionOutcome else { return nil }
    switch insertionOutcome {
    case .copiedWithoutPasting:
        return .copiedToClipboard
    case .failed:
        return keepsRecentTranscripts ? .insertionFailed : .insertionFailedWithoutHistory
    case .inserted:
        return nil
    }
}

@MainActor
func currentDictationPasteTarget() -> DictationPasteTarget? {
    let systemWide = AXUIElementCreateSystemWide()
    var applicationValue: CFTypeRef?
    guard AXUIElementCopyAttributeValue(systemWide,
                                        kAXFocusedApplicationAttribute as CFString,
                                        &applicationValue) == .success,
          let applicationValue,
          CFGetTypeID(applicationValue) == AXUIElementGetTypeID() else { return nil }

    let application = applicationValue as! AXUIElement
    _ = AXUIElementSetMessagingTimeout(application, PASTE_TARGET_AX_TIMEOUT_SECONDS)
    var processIdentifier: pid_t = 0
    guard AXUIElementGetPid(application, &processIdentifier) == .success,
          processIdentifier > 0 else { return nil }

    var windowValue: CFTypeRef?
    guard AXUIElementCopyAttributeValue(application,
                                        kAXFocusedWindowAttribute as CFString,
                                        &windowValue) == .success,
          let windowValue,
          CFGetTypeID(windowValue) == AXUIElementGetTypeID() else { return nil }

    return DictationPasteTarget(processIdentifier: processIdentifier,
                                focusedWindow: windowValue as! AXUIElement)
}

@MainActor
func dictationPasteTargetMatches(_ expected: DictationPasteTarget,
                                 _ current: DictationPasteTarget?) -> Bool {
    guard let current,
          expected.processIdentifier == current.processIdentifier else { return false }
    return CFEqual(expected.focusedWindow, current.focusedWindow)
}

func dictationDeliveryRoute(capturedTargetAvailable: Bool,
                            targetStillFocused: Bool) -> TextInsertionOutcome {
    capturedTargetAvailable && targetStillFocused ? .inserted : .copiedWithoutPasting
}

func textInsertionStrategyChain(primary: TextInsertionStrategy) -> [TextInsertionStrategy] {
    switch primary {
    case .clipboardPaste:
        return [.clipboardPaste, .directUnicode]
    case .directUnicode:
        return [.directUnicode]
    }
}

func textInsertionStrategyDescription(primary: TextInsertionStrategy) -> String {
    let strategies = textInsertionStrategyChain(primary: primary).map(\.displayName)
    guard let first = strategies.first else { return "Unavailable" }
    guard strategies.count > 1 else { return first }
    return "\(first) with \(strategies.dropFirst().joined(separator: ", ")) fallback"
}

func unicodeInsertionChunks(for text: String, maxUTF16UnitsPerEvent maxUnits: Int) -> [[UInt16]] {
    guard maxUnits > 0 else { return [] }
    var chunks: [[UInt16]] = []
    var current: [UInt16] = []

    for character in text {
        let units = Array(String(character).utf16)
        if units.count > maxUnits {
            if !current.isEmpty {
                chunks.append(current)
                current.removeAll(keepingCapacity: true)
            }
            chunks.append(units)
            continue
        }
        if !current.isEmpty, current.count + units.count > maxUnits {
            chunks.append(current)
            current.removeAll(keepingCapacity: true)
        }
        current.append(contentsOf: units)
    }

    if !current.isEmpty {
        chunks.append(current)
    }
    return chunks
}

private struct KeyboardEventStep: Equatable {
    let virtualKey: CGKeyCode
    let keyDown: Bool
    let flags: CGEventFlags
}

private func clipboardPasteKeyboardEventSteps(commandKey: CGKeyCode,
                                              pasteKey: CGKeyCode) -> [KeyboardEventStep] {
    [
        KeyboardEventStep(virtualKey: commandKey, keyDown: true, flags: .maskCommand),
        KeyboardEventStep(virtualKey: pasteKey, keyDown: true, flags: .maskCommand),
        KeyboardEventStep(virtualKey: pasteKey, keyDown: false, flags: .maskCommand),
        KeyboardEventStep(virtualKey: commandKey, keyDown: false, flags: []),
    ]
}

private enum ClipboardPasteEventOutcome: Equatable {
    case posted
    case targetChanged
    case failed
}

@MainActor
private func postFocusBoundClipboardPasteSteps(
    _ steps: [KeyboardEventStep],
    pasteKey: CGKeyCode,
    targetStillFocused: () -> Bool,
    postStep: (KeyboardEventStep) -> Bool
) -> ClipboardPasteEventOutcome {
    var pressedKeys: [CGKeyCode] = []

    func releasePressedKeys() {
        for key in pressedKeys.reversed() {
            _ = postStep(KeyboardEventStep(virtualKey: key,
                                           keyDown: false,
                                           flags: []))
        }
    }

    for step in steps {
        // Command+V is several independent HID events. Focus can move after
        // Command goes down, so revalidate immediately before the V key-down
        // that makes the clipboard contents visible to the destination.
        if step.virtualKey == pasteKey, step.keyDown, !targetStillFocused() {
            releasePressedKeys()
            return .targetChanged
        }
        guard postStep(step) else {
            releasePressedKeys()
            return .failed
        }
        if step.keyDown {
            if !pressedKeys.contains(step.virtualKey) {
                pressedKeys.append(step.virtualKey)
            }
        } else {
            pressedKeys.removeAll { $0 == step.virtualKey }
        }
    }
    return .posted
}

@MainActor
enum TextInserter {
    nonisolated static let defaultStrategy = TextInsertionStrategy.clipboardPaste

    nonisolated static var defaultStrategyDescription: String {
        textInsertionStrategyDescription(primary: defaultStrategy)
    }

    @discardableResult
    static func insert(_ text: String,
                       strategy: TextInsertionStrategy = defaultStrategy,
                       restoreClipboard: Bool = false,
                       expectedTarget: DictationPasteTarget) -> TextInsertionOutcome {
        for candidate in textInsertionStrategyChain(primary: strategy) {
            let outcome = insert(text,
                                 using: candidate,
                                 restoreClipboard: restoreClipboard,
                                 expectedTarget: expectedTarget)
            if outcome == .inserted {
                if candidate != strategy {
                    log("text insertion fallback succeeded: \(candidate.displayName)")
                }
                return outcome
            }
            if outcome == .copiedWithoutPasting { return outcome }
            log("text insertion attempt failed: \(candidate.displayName)")
        }
        return .failed
    }

    private static func insert(_ text: String,
                               using strategy: TextInsertionStrategy,
                               restoreClipboard: Bool,
                               expectedTarget: DictationPasteTarget) -> TextInsertionOutcome {
        switch strategy {
        case .clipboardPaste:
            return ClipboardPasteInserter.insert(text,
                                                 restoreClipboard: restoreClipboard,
                                                 expectedTarget: expectedTarget)
        case .directUnicode:
            return DirectUnicodeInserter.insert(text, expectedTarget: expectedTarget)
        }
    }

    static func copyWithoutPasting(_ text: String) -> TextInsertionOutcome {
        ClipboardPasteInserter.write(text, to: .general)
            ? .copiedWithoutPasting
            : .failed
    }
}

// Only restore the previous clipboard if Presspeech still owns the
// pasteboard. A `changeCount` mismatch means another app or the user
// replaced its contents, and we must not clobber them.
func pasteboardChangeCountAllowsRestore(current: Int, expected: Int) -> Bool {
    current == expected
}

@MainActor
private enum ClipboardPasteInserter {
    private static let virtualKeyCommand: CGKeyCode = 0x37  // left Command
    private static let virtualKeyV: CGKeyCode = 0x09  // ANSI 'v'

    /// A faithful copy of a pasteboard's contents: every item with all
    /// of its representation types and data. Copied into fresh
    /// `NSPasteboardItem`s because an item can't be reused across
    /// pasteboards or after its owning pasteboard is cleared.
    struct Snapshot {
        let items: [NSPasteboardItem]
        let sourceChangeCount: Int
    }

    /// Returns nil rather than a partial snapshot if any advertised
    /// representation cannot be materialized. Restoring a partial copy
    /// would silently discard precisely the clipboard content this
    /// option is intended to preserve.
    static func snapshot(of pb: NSPasteboard) -> Snapshot? {
        let sourceChangeCount = pb.changeCount
        guard let sourceItems = pb.pasteboardItems else {
            return (pb.types ?? []).isEmpty
                ? Snapshot(items: [], sourceChangeCount: sourceChangeCount)
                : nil
        }

        var copies: [NSPasteboardItem] = []
        copies.reserveCapacity(sourceItems.count)
        for item in sourceItems {
            guard !item.types.isEmpty else { return nil }
            let copy = NSPasteboardItem()
            for type in item.types {
                guard let data = item.data(forType: type),
                      copy.setData(data, forType: type) else { return nil }
            }
            copies.append(copy)
        }

        guard pasteboardChangeCountAllowsRestore(current: pb.changeCount,
                                                 expected: sourceChangeCount) else {
            return nil
        }
        return Snapshot(items: copies, sourceChangeCount: sourceChangeCount)
    }

    /// Writes the snapshot back onto the pasteboard, but only if
    /// `expectedChangeCount` still matches — otherwise something else
    /// owns the clipboard now and we leave it untouched.
    @discardableResult
    static func restore(_ snapshot: Snapshot, to pb: NSPasteboard, expectedChangeCount: Int) -> Bool {
        guard pasteboardChangeCountAllowsRestore(current: pb.changeCount,
                                                 expected: expectedChangeCount) else {
            return false
        }
        pb.clearContents()
        guard !snapshot.items.isEmpty else { return true }
        return pb.writeObjects(snapshot.items)
    }

    static func contentsOptions(transient: Bool) -> NSPasteboard.ContentsOptions {
        transient ? .currentHostOnly : []
    }

    static func write(_ text: String, to pb: NSPasteboard, transient: Bool = false) -> Bool {
        // Build the complete item before taking pasteboard ownership so a
        // representation failure cannot erase the user's existing clipboard.
        let item = NSPasteboardItem()
        guard item.setString(text, forType: .string) else { return false }

        if transient {
            // These community-standard markers keep cooperating clipboard
            // managers from recording a transcript that exists only long
            // enough to drive Cmd+V. currentHostOnly likewise keeps the
            // temporary value out of Universal Clipboard; the restored
            // snapshot retains its original behavior.
            guard item.setData(Data(), forType: TRANSIENT_PASTEBOARD_TYPE),
                  item.setData(Data(), forType: AUTO_GENERATED_PASTEBOARD_TYPE),
                  item.setString(SETTINGS_SUITE, forType: PASTEBOARD_SOURCE_TYPE) else {
                return false
            }
        }

        pb.prepareForNewContents(with: contentsOptions(transient: transient))
        return pb.writeObjects([item])
    }

    static func insert(_ text: String,
                       restoreClipboard: Bool = false,
                       expectedTarget: DictationPasteTarget) -> TextInsertionOutcome {
        guard dictationPasteTargetMatches(expectedTarget,
                                          currentDictationPasteTarget()) else {
            return TextInserter.copyWithoutPasting(text)
        }

        let pb = NSPasteboard.general
        var previous: Snapshot?
        if restoreClipboard {
            previous = snapshot(of: pb)
            if previous == nil {
                log("clipboard snapshot incomplete; restore skipped")
            }
        }

        // A lazy data provider can make snapshotting take long enough
        // for another process to replace the clipboard. Never restore
        // the now-stale snapshot after taking ownership for the paste.
        if let candidate = previous,
           !pasteboardChangeCountAllowsRestore(current: pb.changeCount,
                                               expected: candidate.sourceChangeCount) {
            previous = nil
            log("clipboard changed during snapshot; restore skipped")
        }

        guard write(text, to: pb, transient: previous != nil) else {
            log("pasteboard write failed")
            return .failed
        }
        let writeChangeCount = pb.changeCount

        // A lazy pasteboard provider can make the optional snapshot above
        // block long enough for the user to focus another window. Recheck at
        // the last possible point before posting Command+V. Rewrite the
        // transcript without temporary-paste markers so a focus change
        // degrades to an ordinary manual paste instead of losing the words.
        guard dictationPasteTargetMatches(expectedTarget,
                                          currentDictationPasteTarget()) else {
            return TextInserter.copyWithoutPasting(text)
        }

        let steps = clipboardPasteKeyboardEventSteps(commandKey: virtualKeyCommand,
                                                     pasteKey: virtualKeyV)
        let postOutcome = post(
            steps,
            pasteKey: virtualKeyV,
            targetStillFocused: {
                dictationPasteTargetMatches(expectedTarget,
                                             currentDictationPasteTarget())
            }
        )
        if postOutcome == .targetChanged {
            return TextInserter.copyWithoutPasting(text)
        }
        guard postOutcome == .posted else {
            log("paste event creation failed")
            // Nothing consumed the transcript, so restoring immediately
            // (guarded) keeps the clipboard clean before we fall back to
            // direct typing.
            if let previous {
                restore(previous, to: pb, expectedChangeCount: writeChangeCount)
            }
            return .failed
        }

        if let previous {
            DispatchQueue.main.asyncAfter(deadline: .now() + CLIPBOARD_RESTORE_DELAY_SECONDS) {
                if restore(previous, to: pb, expectedChangeCount: writeChangeCount) {
                    log("clipboard restored after paste")
                }
            }
        }
        return .inserted
    }

    private static func post(
        _ steps: [KeyboardEventStep],
        pasteKey: CGKeyCode,
        targetStillFocused: () -> Bool
    ) -> ClipboardPasteEventOutcome {
        let source = CGEventSource(stateID: .hidSystemState)
        let events = steps.compactMap { step -> (KeyboardEventStep, CGEvent)? in
            guard let event = CGEvent(keyboardEventSource: source,
                                      virtualKey: step.virtualKey,
                                      keyDown: step.keyDown) else {
                return nil
            }
            event.flags = step.flags
            return (step, event)
        }
        guard events.count == steps.count else { return .failed }

        // Post Command as real key events instead of only tagging the V
        // events with .maskCommand. Sleep/wake can leave session modifier
        // state unreliable for flag-only synthetic shortcuts.
        return postFocusBoundClipboardPasteSteps(
            steps,
            pasteKey: pasteKey,
            targetStillFocused: targetStillFocused
        ) { step in
            guard let event = events.first(where: { $0.0 == step })?.1 else {
                return false
            }
            event.post(tap: .cghidEventTap)
            return true
        }
    }
}

@MainActor
private func directUnicodeInsertionOutcome(
    chunks: [[UInt16]],
    targetStillFocused: () -> Bool,
    postChunk: ([UInt16]) -> Bool,
    copyWithoutPasting: () -> TextInsertionOutcome
) -> TextInsertionOutcome {
    for chunk in chunks {
        // Direct Unicode typing is a sequence of independent key events, not
        // one atomic paste. Revalidate before every chunk so switching focus
        // during a long fallback cannot redirect the remaining transcript.
        guard targetStillFocused() else { return copyWithoutPasting() }
        // A failed chunk would leave a gap. Stop immediately instead of
        // emitting later text after content we know was not delivered.
        guard postChunk(chunk) else { return .failed }
    }
    return .inserted
}

@MainActor
private enum DirectUnicodeInserter {
    private static let maxUTF16UnitsPerEvent = 20

    static func insert(_ text: String,
                       expectedTarget: DictationPasteTarget) -> TextInsertionOutcome {
        let source = CGEventSource(stateID: .combinedSessionState)
        let chunks = unicodeInsertionChunks(
            for: text,
            maxUTF16UnitsPerEvent: maxUTF16UnitsPerEvent
        )
        return directUnicodeInsertionOutcome(
            chunks: chunks,
            targetStillFocused: {
                dictationPasteTargetMatches(expectedTarget,
                                             currentDictationPasteTarget())
            },
            postChunk: { post($0, source: source) },
            copyWithoutPasting: { TextInserter.copyWithoutPasting(text) }
        )
    }

    private static func post(_ units: [UInt16], source: CGEventSource?) -> Bool {
        // Each chunk posts a keyDown AND a matching keyUp carrying the
        // same unicode payload — standard CGEvent unicode-typing
        // practice. A keyDown-only stream leaves apps that track key
        // state believing a key is still held.
        guard let down = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: true),
              let up = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: false) else {
            return false
        }
        down.flags = []
        up.flags = []
        for event in [down, up] {
            units.withUnsafeBufferPointer { buffer in
                guard let base = buffer.baseAddress else { return }
                event.keyboardSetUnicodeString(stringLength: buffer.count, unicodeString: base)
            }
        }
        down.post(tap: .cghidEventTap)
        up.post(tap: .cghidEventTap)
        return true
    }
}

// MARK: - System audio mute
//
// Mute the system output volume during recording so an open Zoom /
// Music / browser tab doesn't get captured back into the mic and
// transcribed alongside the user's voice. Done via NSAppleScript
// since there's no public AVFoundation knob for it. On release we
// only unmute if WE were the ones who muted — leave alone if the
// user had already muted manually.
//
// Threading: every AppleScript round-trip takes milliseconds at best
// and can stall for much longer under load. The hotkey path runs
// behind a session-wide CGEvent tap on the main run loop, where ANY
// main-thread stall delays every keystroke system-wide and a >1 s
// stall makes macOS disable the tap. So the recording-time mute /
// unmute scripts execute on a dedicated serial queue (the *Async
// wrappers below) and report back to the main actor. The serial
// queue is also the ordering guarantee: a mute enqueued before an
// unmute always executes before it. The synchronous isMuted() /
// unmute() remain for launch-time stale-mute recovery, which runs
// before the event tap exists.

/// Outcome of the "set volume with output muted" command plus its
/// follow-up verification read. The distinction matters for crash
/// recovery: a command that succeeded but could not be VERIFIED must
/// be assumed muted, so the recovery marker and watchdog stay armed.
/// Treating it as a failure would dismantle every recovery mechanism
/// for a mute that may well have happened, leaving the system muted
/// with no way back.
enum SystemAudioMuteCommandOutcome: Equatable, Sendable {
    /// Command ran without error and verification confirmed the
    /// output is muted.
    case muted
    /// Command ran without error but the verification read itself
    /// failed. Assume we muted: keeping recovery armed for a mute
    /// that didn't happen is harmless; the reverse is not.
    case assumedMuted
    /// The command itself failed, or verification definitively
    /// reported the output unmuted. Nothing happened to recover from.
    case failed
}

func systemAudioMuteCommandOutcome(commandSucceeded: Bool,
                                   verifiedMuted: Bool?) -> SystemAudioMuteCommandOutcome {
    guard commandSucceeded else { return .failed }
    switch verifiedMuted {
    case .some(true): return .muted
    case .none: return .assumedMuted
    case .some(false): return .failed
    }
}

enum SystemAudio {
    // NSAppleScript isn't Sendable so we can't memoise it across
    // threads under Swift 6 strict concurrency. AppleScript compile
    // is microseconds — happy to take the per-call cost. Each script
    // instance is created, executed, and discarded entirely on one
    // thread (this serial queue or, for the launch-time sync calls,
    // the main thread), which satisfies NSAppleScript's
    // not-thread-safe contract.
    private static let queue = DispatchQueue(label: "PresspeechSystemAudio", qos: .userInitiated)

    /// nil = the query itself failed, as opposed to a definitive
    /// muted/unmuted answer.
    static func mutedState() -> Bool? {
        var err: NSDictionary?
        guard let script = NSAppleScript(source: "output muted of (get volume settings)") else {
            return nil
        }
        let result = script.executeAndReturnError(&err)
        guard err == nil else { return nil }
        return result.booleanValue
    }

    static func isMuted() -> Bool { mutedState() == true }

    static func mute() -> SystemAudioMuteCommandOutcome {
        guard let script = NSAppleScript(source: "set volume with output muted") else {
            return systemAudioMuteCommandOutcome(commandSucceeded: false, verifiedMuted: nil)
        }
        var err: NSDictionary?
        script.executeAndReturnError(&err)
        return systemAudioMuteCommandOutcome(commandSucceeded: err == nil,
                                             verifiedMuted: mutedState())
    }

    @discardableResult
    static func unmute() -> Bool {
        var err: NSDictionary?
        _ = NSAppleScript(source: "set volume without output muted")?.executeAndReturnError(&err)
        // A failed verification counts as "not unmuted": the caller
        // keeps the recovery marker + watchdog armed and retries
        // later, which is the safe direction.
        return err == nil && mutedState() == false
    }

    // Async wrappers — see the threading note above. Completions hop
    // back to the main actor, where all mute-lifecycle state lives.
    static func mutedStateAsync(_ completion: @escaping @MainActor @Sendable (Bool?) -> Void) {
        queue.async {
            let state = mutedState()
            Task { @MainActor in completion(state) }
        }
    }

    static func muteAsync(_ completion: @escaping @MainActor @Sendable (SystemAudioMuteCommandOutcome) -> Void) {
        queue.async {
            let outcome = mute()
            Task { @MainActor in completion(outcome) }
        }
    }

    static func unmuteAsync(_ completion: @escaping @MainActor @Sendable (Bool) -> Void) {
        queue.async {
            let unmuted = unmute()
            Task { @MainActor in completion(unmuted) }
        }
    }
}

// MARK: - System audio mute lifecycle
//
// Pure decision functions for the recording-time mute state machine.
// All phase transitions happen on the main actor; only the
// AppleScript execution itself runs on SystemAudio's serial queue.
// At most one command is in flight at a time — each phase has exactly
// one outstanding completion, which performs the next transition.

enum SystemAudioMutePhase: Equatable, Sendable {
    /// No mute lifecycle active; marker + watchdog disarmed.
    case idle
    /// "is the output already muted?" probe in flight. No marker or
    /// watchdog yet, and nothing has been muted.
    case probing
    /// Marker + watchdog armed; the mute command is in flight.
    case muting
    /// We muted the output; marker + watchdog stay armed until an
    /// unmute succeeds (or the watchdog recovers after a crash).
    case muted
    /// Unmute command in flight; marker + watchdog stay armed until
    /// it succeeds.
    case unmuting
}

enum SystemAudioMuteProbeDecision: Equatable, Sendable {
    /// Do not mute: the output is already muted by the user, the
    /// probe failed (we can't risk stomping a user-set mute we can't
    /// see), or the recording already ended. Nothing to arm or undo.
    case standDown
    /// The output is live and the recording still wants it muted —
    /// arm the recovery marker + watchdog, then issue the mute.
    case armRecoveryAndMute
}

func systemAudioMuteProbeDecision(mutedState: Bool?,
                                  unmuteAlreadyRequested: Bool) -> SystemAudioMuteProbeDecision {
    guard mutedState == false, !unmuteAlreadyRequested else { return .standDown }
    return .armRecoveryAndMute
}

enum SystemAudioMuteCommandDecision: Equatable, Sendable {
    /// The mute definitively failed — disarm the marker + watchdog.
    case disarmRecovery
    /// We are (or must assume we are) muted and the recording is
    /// still running — hold the muted state.
    case stayMuted
    /// We muted, but the recording ended while the command ran —
    /// unmute immediately.
    case beginUnmute
}

func systemAudioMuteCommandDecision(outcome: SystemAudioMuteCommandOutcome,
                                    unmuteAlreadyRequested: Bool) -> SystemAudioMuteCommandDecision {
    switch outcome {
    case .failed:
        return .disarmRecovery
    case .muted, .assumedMuted:
        return unmuteAlreadyRequested ? .beginUnmute : .stayMuted
    }
}

enum SystemAudioUnmuteRequestDecision: Equatable, Sendable {
    /// We never muted (or an unmute is already in flight).
    case nothingToDo
    /// A probe or the mute command is still in flight — record the
    /// request; that command's completion honours it.
    case deferUntilCommandSettles
    /// We hold the mute — issue the unmute now.
    case beginUnmute
}

func systemAudioUnmuteRequestDecision(phase: SystemAudioMutePhase) -> SystemAudioUnmuteRequestDecision {
    switch phase {
    case .idle, .unmuting: return .nothingToDo
    case .probing, .muting: return .deferUntilCommandSettles
    case .muted: return .beginUnmute
    }
}

private func presspeechApplicationSupportDirectory() -> URL {
    FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("Presspeech", isDirectory: true)
}

private func legacyApplicationSupportDirectory() -> URL {
    FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appendingPathComponent(LEGACY_APPLICATION_SUPPORT_DIRECTORY_NAME, isDirectory: true)
}

private func systemAudioMuteMarkerURL() -> URL {
    presspeechApplicationSupportDirectory()
        .appendingPathComponent("system-audio-muted", isDirectory: false)
}

private func migrateLegacyIdentityFilesIfNeeded(settings: Settings,
                                                fileManager: FileManager = .default) {
    let legacySupport = legacyApplicationSupportDirectory()
    let currentSupport = presspeechApplicationSupportDirectory()
    let markerName = "system-audio-muted"
    let legacyMarker = legacySupport.appendingPathComponent(markerName, isDirectory: false)
    let currentMarker = currentSupport.appendingPathComponent(markerName, isDirectory: false)

    if fileManager.fileExists(atPath: legacyMarker.path) {
        do {
            try fileManager.createDirectory(at: currentSupport,
                                            withIntermediateDirectories: true,
                                            attributes: [.posixPermissions: 0o700])
            if fileManager.fileExists(atPath: currentMarker.path) {
                try fileManager.removeItem(at: legacyMarker)
            } else {
                try fileManager.moveItem(at: legacyMarker, to: currentMarker)
            }
            log("identity migration: moved system-audio recovery state")
        } catch {
            log("identity migration: recovery-state move failed: \(error.localizedDescription)")
        }
    }

    if let contents = try? fileManager.contentsOfDirectory(atPath: legacySupport.path),
       contents.isEmpty {
        try? fileManager.removeItem(at: legacySupport)
    }

    let configuredPath = settings.transcriptCorrectionsSyncFile
    if !configuredPath.isEmpty {
        let legacyURL = URL(fileURLWithPath: configuredPath)
        if let currentURL = identityMigrationDestinationURL(forLegacyCorrectionURL: legacyURL) {
            do {
                if fileManager.fileExists(atPath: currentURL.path) {
                    settings.transcriptCorrectionsSyncFile = currentURL.path
                } else if fileManager.fileExists(atPath: legacyURL.path) {
                    try fileManager.moveItem(at: legacyURL, to: currentURL)
                    settings.transcriptCorrectionsSyncFile = currentURL.path
                    log("identity migration: renamed correction sync file")
                }
            } catch {
                // Keep the configured legacy path so sync continues to
                // work; the next launch can retry the rename.
                log("identity migration: correction sync rename failed: \(error.localizedDescription)")
            }
        }
    }

    let library = FileManager.default.urls(for: .libraryDirectory, in: .userDomainMask)[0]
    let stalePaths = [
        library.appendingPathComponent("Caches/\(LEGACY_SETTINGS_SUITE)", isDirectory: true),
        library.appendingPathComponent("HTTPStorages/\(LEGACY_SETTINGS_SUITE)", isDirectory: true),
        library.appendingPathComponent("Logs/\(LEGACY_APPLICATION_SUPPORT_DIRECTORY_NAME).log",
                                       isDirectory: false),
    ]
    for url in stalePaths where fileManager.fileExists(atPath: url.path) {
        do {
            try fileManager.removeItem(at: url)
        } catch {
            log("identity migration: stale-file cleanup failed for \(privacySafeLogPath(url)): \(error.localizedDescription)")
        }
    }
}

private func systemAudioMuteMarkerText(pid: pid_t = getpid(), date: Date = Date()) -> String {
    """
    pid=\(pid)
    created=\(ISO8601DateFormatter().string(from: date))
    """
}

private func systemAudioMuteMarkerProcessID(from text: String) -> pid_t? {
    for line in text.split(separator: "\n") {
        guard line.hasPrefix("pid="),
              let raw = Int32(line.dropFirst(4)),
              raw > 0 else { continue }
        return raw
    }
    return nil
}

private func writeSystemAudioMuteMarker(to url: URL = systemAudioMuteMarkerURL(),
                                        text: String = systemAudioMuteMarkerText()) throws {
    let fm = FileManager.default
    let directory = url.deletingLastPathComponent()
    try fm.createDirectory(at: directory,
                           withIntermediateDirectories: true,
                           attributes: [.posixPermissions: 0o700])

    let fd = Darwin.open(url.path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC | O_NOFOLLOW, 0o600)
    guard fd >= 0 else { throw currentPOSIXError() }
    defer { Darwin.close(fd) }
    try text.withCString { raw in
        let data = UnsafeRawPointer(raw)
        let count = strlen(raw)
        var written = 0
        while written < count {
            let n = Darwin.write(fd, data.advanced(by: written), count - written)
            guard n >= 0 else { throw currentPOSIXError() }
            written += n
        }
    }
    _ = Darwin.fchmod(fd, 0o600)
}

private func removeSystemAudioMuteMarker(at url: URL = systemAudioMuteMarkerURL()) {
    try? FileManager.default.removeItem(at: url)
}

private func systemAudioMuteWatchdogScript() -> String {
    #"""
    PID="$1"
    MARKER="$2"

    while /bin/kill -0 "$PID" 2>/dev/null; do
        /bin/sleep 0.5
    done

    if [ -e "$MARKER" ]; then
        /usr/bin/osascript -e 'set volume without output muted' >/dev/null 2>&1 || true
        /bin/rm -f "$MARKER"
    fi
    """#
}

// MARK: - Sounds
//
// Short system sounds: Tink on recording start, Pop after a
// successful paste, Basso when a dictation is dropped. Loaded from
// /System/Library/Sounds so we don't have to bundle audio resources.

@MainActor
enum Sounds {
    private static let start: NSSound? = NSSound(contentsOfFile: "/System/Library/Sounds/Tink.aiff",  byReference: true)
    private static let done:  NSSound? = NSSound(contentsOfFile: "/System/Library/Sounds/Pop.aiff",   byReference: true)
    private static let error: NSSound? = NSSound(contentsOfFile: "/System/Library/Sounds/Basso.aiff", byReference: true)

    static func playStart() { start?.stop(); start?.play() }
    static func playDone()  { done?.stop();  done?.play() }
    static func playError() { error?.stop(); error?.play() }
}

// MARK: - Bundle version helpers

func currentBundleVersion() -> String {
    Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.0.0"
}

func currentBundleBuild() -> String {
    Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "0"
}

struct AppMemoryUsage {
    let residentBytes: UInt64
    let physicalFootprintBytes: UInt64
}

func currentAppMemoryUsage() -> AppMemoryUsage? {
    var info = task_vm_info_data_t()
    var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.stride / MemoryLayout<natural_t>.stride)
    let result = withUnsafeMutablePointer(to: &info) { pointer in
        pointer.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { rebound in
            task_info(mach_task_self_,
                      task_flavor_t(TASK_VM_INFO),
                      rebound,
                      &count)
        }
    }
    guard result == KERN_SUCCESS else { return nil }
    return AppMemoryUsage(residentBytes: UInt64(info.resident_size),
                          physicalFootprintBytes: UInt64(info.phys_footprint))
}

func formattedByteCount(_ bytes: UInt64) -> String {
    ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .memory)
}

// MARK: - Diagnostics
//
// User-triggered local diagnostics for GitHub issue triage. Keep the
// report useful but metadata-only: no transcript text and no text
// correction contents.

struct DiagnosticsReportSnapshot {
    let generated: String
    let appVersion: String
    let appBuild: String
    let macOS: String
    let bundleID: String
    let bundlePath: String
    let installKind: String
    let status: String
    let startup: String
    let speechModelReady: Bool
    let coreRuntimeReady: Bool
    let readyForDictation: Bool
    let recordingActive: Bool
    let transcribing: Bool
    let memoryLines: [String]
    let permissionLines: [String]
    let settingLines: [String]
    let updateLines: [String]
    let microphoneLines: [String]
    let logPath: String
    let recentLogLines: [String]
}

private func diagnosticBulletLines(_ lines: [String], emptyText: String) -> String {
    guard !lines.isEmpty else { return "- \(emptyText)" }
    return lines.map { "- \($0)" }.joined(separator: "\n")
}

func diagnosticsReportText(from snapshot: DiagnosticsReportSnapshot) -> String {
    """
    Presspeech diagnostics
    Generated: \(snapshot.generated)
    App version: \(snapshot.appVersion) (\(snapshot.appBuild))
    macOS: \(snapshot.macOS)
    Bundle ID: \(snapshot.bundleID)
    Bundle path: \(snapshot.bundlePath)
    Install kind: \(snapshot.installKind)

    Status:
    - Menu: \(snapshot.status)
    - Startup: \(snapshot.startup)
    - Speech model ready: \(snapshot.speechModelReady)
    - Core runtime ready: \(snapshot.coreRuntimeReady)
    - Ready for dictation: \(snapshot.readyForDictation)
    - Recording active: \(snapshot.recordingActive)
    - Transcribing: \(snapshot.transcribing)

    Memory:
    \(diagnosticBulletLines(snapshot.memoryLines, emptyText: "Unavailable"))

    Permissions:
    \(diagnosticBulletLines(snapshot.permissionLines, emptyText: "Unavailable"))

    Settings:
    \(diagnosticBulletLines(snapshot.settingLines, emptyText: "Unavailable"))

    Update:
    \(diagnosticBulletLines(snapshot.updateLines, emptyText: "Unavailable"))

    Microphone:
    \(diagnosticBulletLines(snapshot.microphoneLines, emptyText: "Unavailable"))

    Recent log lines:
    \(diagnosticBulletLines(snapshot.recentLogLines, emptyText: "No recent log lines available"))

    Logs: \(snapshot.logPath)
    Privacy: transcript text and text-correction contents are not included.
    """
}

func recentDiagnosticLogLines(from url: URL = Logger.shared.fileURL,
                              maxBytes: Int = DIAGNOSTICS_LOG_MAX_BYTES,
                              maxLines: Int = DIAGNOSTICS_LOG_MAX_LINES) throws -> [String] {
    guard maxBytes > 0, maxLines > 0 else { return [] }

    let fd = Darwin.open(url.path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW)
    guard fd >= 0 else {
        if errno == ENOENT { return [] }
        throw currentPOSIXError()
    }
    defer { _ = Darwin.close(fd) }

    try validateSingleLinkRegularFileDescriptor(fd)

    var st = stat()
    guard Darwin.fstat(fd, &st) == 0 else { throw currentPOSIXError() }
    guard st.st_size > 0 else { return [] }

    let startOffset = max(Int64(0), Int64(st.st_size) - Int64(maxBytes))
    guard Darwin.lseek(fd, off_t(startOffset), SEEK_SET) >= 0 else {
        throw currentPOSIXError()
    }

    var data = Data()
    data.reserveCapacity(min(maxBytes, Int(st.st_size)))
    while data.count < maxBytes {
        let remaining = maxBytes - data.count
        var buffer = [UInt8](repeating: 0, count: min(8192, remaining))
        let bytesRead = buffer.withUnsafeMutableBytes { rawBuffer in
            Darwin.read(fd, rawBuffer.baseAddress, rawBuffer.count)
        }
        if bytesRead < 0 {
            if errno == EINTR { continue }
            throw currentPOSIXError()
        }
        guard bytesRead > 0 else { break }
        data.append(buffer, count: bytesRead)
    }

    var text = String(decoding: data, as: UTF8.self)
    if startOffset > 0, let firstNewline = text.firstIndex(of: "\n") {
        text = String(text[text.index(after: firstNewline)...])
    }

    let sanitized = text
        .components(separatedBy: .newlines)
        .map(sanitizedDiagnosticLogLine)
        .filter { !$0.isEmpty }
    return Array(sanitized.suffix(maxLines))
}

private func sanitizedDiagnosticLogLine(_ line: String) -> String {
    var result = String()
    result.reserveCapacity(min(line.count, DIAGNOSTICS_LOG_MAX_LINE_CHARACTERS))
    for scalar in line.unicodeScalars {
        guard result.count < DIAGNOSTICS_LOG_MAX_LINE_CHARACTERS else { break }
        if scalar == "\t" || (scalar.value >= 0x20 && scalar.value != 0x7f) {
            result.unicodeScalars.append(scalar)
        } else {
            result.append(" ")
        }
    }
    return result.trimmingCharacters(in: .whitespaces)
}

func parseSemver(_ s: String) -> [Int] {
    // Strip leading whitespace + 'v', split on '.', take leading
    // digit run from each chunk. Tolerant by design; "" returns []
    // which compares less than any real version.
    let trimmed = s.trimmingCharacters(in: .whitespaces)
        .drop(while: { $0 == "v" || $0 == "V" })
    return trimmed.split(separator: ".").map { chunk in
        var n = 0
        var seen = false
        for c in chunk {
            guard let d = c.wholeNumberValue else { break }
            let multiplied = n.multipliedReportingOverflow(by: 10)
            if multiplied.overflow { return Int.max }
            let added = multiplied.partialValue.addingReportingOverflow(d)
            if added.overflow { return Int.max }
            n = added.partialValue
            seen = true
        }
        return seen ? n : 0
    }
}

func isNewer(_ candidate: String, than current: String) -> Bool {
    let a = parseSemver(candidate)
    let b = parseSemver(current)
    for i in 0..<max(a.count, b.count) {
        let x = i < a.count ? a[i] : 0
        let y = i < b.count ? b[i] : 0
        if x != y { return x > y }
    }
    return false
}

// MARK: - TCC recovery
//
// macOS's TCC database occasionally ends up with a DENIED entry
// for our bundle id that the user can't easily clear (typical
// trigger: an upgrade that changes the signed binary while a
// previous denial is still cached). On a fresh launch after an
// upgrade (CFBundleShortVersionString differs from
// settings.lastSeenVersion), we proactively `tccutil reset` any
// DENIED entry for `com.local.presspeech`. GRANTED entries stay
// intact — we never reset away permissions the user gave us.
//
// The companion to this is the click-twice-to-reset retry in the
// permission rows: if the user clicks a ⚠ row, sees the OS dialog
// say nothing useful, and clicks the same row again, the second
// click runs `tccutil reset` to clear stuck state and re-request.

enum TCC {
    /// Maps the human-readable permission name we use in the menu to
    /// the TCC service identifier `tccutil reset` accepts. Input
    /// Monitoring is "ListenEvent" internally.
    static let serviceName: [Permission: String] = [
        .microphone: "Microphone",
        .accessibility: "Accessibility",
        .inputMonitoring: "ListenEvent",
    ]

    /// Serial so multiple resets (e.g. the upgrade-recovery loop)
    /// execute in the order they were requested.
    private static let queue = DispatchQueue(label: "PresspeechTCCReset", qos: .userInitiated)

    /// Runs `tccutil reset` on a background queue. tccutil is usually
    /// quick but waitUntilExit() on the main thread would run behind
    /// the session-wide event tap, where any stall delays every
    /// keystroke system-wide. `completion`, if provided, is invoked
    /// on the main actor after the reset has finished — callers that
    /// re-request the permission must do so from the completion, or
    /// the request would race the scrub it depends on.
    static func reset(_ p: Permission,
                      bundleID: String,
                      completion: (@MainActor @Sendable () -> Void)? = nil) {
        guard let service = serviceName[p] else {
            if let completion { Task { @MainActor in completion() } }
            return
        }
        queue.async {
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: "/usr/bin/tccutil")
            proc.arguments = ["reset", service, bundleID]
            proc.environment = systemToolProcessEnvironment()
            proc.standardOutput = Pipe()
            proc.standardError = Pipe()
            do {
                try proc.run()
                proc.waitUntilExit()
                log("  tccutil reset \(service) \(bundleID) → exit \(proc.terminationStatus)")
            } catch {
                log("  tccutil reset \(service) failed: \(error)")
            }
            if let completion { Task { @MainActor in completion() } }
        }
    }
}

// MARK: - Update check
//
// Hits the GitHub Releases API once at boot + every 6 h. Users can
// also force the same lookup from the menu. When a newer version is
// found AND it's not in the user's skipped list, a submenu inserts
// itself at the top of the menu: What's new / Update now / Remind me
// in 24 hours / Skip vX.Y.Z.

struct GitHubRelease: Sendable, Equatable {
    let tagName: String      // 'v0.1.7'
    let version: String      // '0.1.7' (no v)
    let body: String         // release notes, raw markdown
    let htmlURL: String
}

private struct GitHubReleaseResponse: Decodable {
    let tagName: String
    let body: String?
    let htmlURL: String?

    enum CodingKeys: String, CodingKey {
        case tagName = "tag_name"
        case body
        case htmlURL = "html_url"
    }
}

/// Why an update check failed. Carried as a value (not a string) so
/// the manual-check alert can explain the actual problem instead of
/// blaming the network for everything; automatic ticks ignore it and
/// stay silent.
enum UpdateCheckFailure: Error, Equatable, Sendable {
    /// The HTTPS request itself failed (offline, DNS, timeout).
    case network
    /// GitHub answered with a non-2xx status (403 → likely API rate
    /// limiting).
    case httpStatus(Int)
    /// A response arrived but was oversized, malformed, or carried an
    /// unusable tag.
    case unexpectedResponse
}

/// User-facing explanation for a failed *manual* update check. Only
/// the alert behind "Check for Updates…" uses this — automatic and
/// settings-toggle checks never alert.
func manualUpdateCheckFailureText(_ failure: UpdateCheckFailure) -> String {
    switch failure {
    case .network:
        return "Presspeech couldn't reach GitHub. Check your internet connection and try again."
    case .httpStatus(403):
        return "GitHub declined the update check (HTTP 403). This is usually temporary rate limiting — try again in a few minutes."
    case .httpStatus(let code):
        return "GitHub returned an error (HTTP \(code)). Try again later."
    case .unexpectedResponse:
        return "GitHub returned a response Presspeech couldn't read. Try again later, or check the releases page on GitHub directly."
    }
}

enum UpdateCheck {
    private static let githubReleaseURLPathPrefix = "/rcourtman/presspeech/releases/tag/"
    static let maxReleaseResponseBytes = 512 * 1024

    static func fetchLatest() async -> Result<GitHubRelease, UpdateCheckFailure> {
        var req = URLRequest(url: GITHUB_LATEST_RELEASE_URL)
        req.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        // The privacy docs promise exactly this fixed token — no
        // version, device, or user identifiers. Must stay in sync with
        // docs/privacy/network-calls.json.
        req.setValue("presspeech-update-check", forHTTPHeaderField: "User-Agent")
        req.timeoutInterval = 10
        let config = URLSessionConfiguration.ephemeral
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.urlCache = nil
        config.timeoutIntervalForRequest = 10
        config.timeoutIntervalForResource = 10
        let session = URLSession(configuration: config)
        defer { session.finishTasksAndInvalidate() }

        do {
            let (data, response) = try await session.data(for: req)
            return parseLatest(data: data, response: response)
        } catch {
            return .failure(.network)
        }
    }

    static func parseLatest(data: Data, response: URLResponse) -> Result<GitHubRelease, UpdateCheckFailure> {
        guard let http = response as? HTTPURLResponse else {
            return .failure(.unexpectedResponse)
        }
        guard (200..<300).contains(http.statusCode) else {
            return .failure(.httpStatus(http.statusCode))
        }
        guard data.count <= maxReleaseResponseBytes,
              let payload = try? JSONDecoder().decode(GitHubReleaseResponse.self, from: data) else {
            return .failure(.unexpectedResponse)
        }

        let tag = payload.tagName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let version = normalizedReleaseVersion(from: tag) else {
            return .failure(.unexpectedResponse)
        }

        return .success(GitHubRelease(
            tagName: tag,
            version: version,
            body: payload.body ?? "",
            htmlURL: sanitizedReleaseURL(payload.htmlURL, expectedTag: tag)
        ))
    }

    static func normalizedReleaseVersion(from tag: String) -> String? {
        var version = tag.trimmingCharacters(in: .whitespacesAndNewlines)
        if let first = version.first, first == "v" || first == "V" {
            version.removeFirst()
        }

        let parts = version.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 3 else { return nil }
        for part in parts {
            guard !part.isEmpty,
                  part.allSatisfy({ ("0"..."9").contains($0) }),
                  part == "0" || !part.hasPrefix("0"),
                  Int(part) != nil else {
                return nil
            }
        }
        return parts.joined(separator: ".")
    }

    static func sanitizedReleaseURL(_ value: String?, expectedTag: String) -> String {
        guard let value else { return GITHUB_RELEASES_PAGE.absoluteString }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let components = URLComponents(string: trimmed),
              components.scheme == "https",
              components.host == "github.com",
              components.user == nil,
              components.password == nil,
              components.query == nil,
              components.fragment == nil,
              components.path == "\(githubReleaseURLPathPrefix)\(expectedTag)" else {
            return GITHUB_RELEASES_PAGE.absoluteString
        }
        return trimmed
    }
}

func shellSingleQuoted(_ value: String) -> String {
    "'\(value.replacingOccurrences(of: "'", with: "'\"'\"'"))'"
}

private func sanitizedEnvironmentValue(_ value: String?) -> String? {
    guard let value,
          !value.isEmpty,
          !value.utf8.contains(0),
          !value.contains(where: { $0.isNewline }) else {
        return nil
    }
    return value
}

private func trustedProcessEnvironment(path: String,
                                       current: [String: String] = ProcessInfo.processInfo.environment) -> [String: String] {
    var env: [String: String] = [
        "HOME": NSHomeDirectory(),
        "PATH": path,
        "SHELL": "/bin/zsh",
        "TMPDIR": NSTemporaryDirectory(),
        "LANG": sanitizedEnvironmentValue(current["LANG"]) ?? "en_US.UTF-8",
    ]

    if let user = sanitizedEnvironmentValue(current["USER"]) {
        env["USER"] = user
    }
    if let logname = sanitizedEnvironmentValue(current["LOGNAME"]) ?? env["USER"] {
        env["LOGNAME"] = logname
    }
    if let encoding = sanitizedEnvironmentValue(current["__CF_USER_TEXT_ENCODING"]) {
        env["__CF_USER_TEXT_ENCODING"] = encoding
    }

    return env
}

private func systemToolProcessEnvironment(current: [String: String] = ProcessInfo.processInfo.environment) -> [String: String] {
    trustedProcessEnvironment(path: "/usr/bin:/bin:/usr/sbin:/sbin", current: current)
}

private func updateProcessEnvironment(current: [String: String] = ProcessInfo.processInfo.environment) -> [String: String] {
    trustedProcessEnvironment(path: "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                              current: current)
}

func updateHelperScript(pid: pid_t,
                        brewPath: String,
                        targetVersion: String,
                        statePath: String,
                        appPath: String = INSTALLED_APP_BUNDLE_PATH,
                        releasesPageURL: String = GITHUB_RELEASES_PAGE.absoluteString) -> String {
    #"""
    #!/bin/bash
    set -u
    umask 077

    SCRIPT_PATH="$0"
    BREW=\#(shellSingleQuoted(brewPath))
    TARGET_VERSION=\#(shellSingleQuoted(targetVersion))
    STATE_PATH=\#(shellSingleQuoted(statePath))
    APP_PATH=\#(shellSingleQuoted(appPath))
    RELEASES_PAGE=\#(shellSingleQuoted(releasesPageURL))
    PRESSPEECH_PID=\#(pid)
    CASK_TAP=\#(shellSingleQuoted(HOMEBREW_CASK_TAP))
    CASK_TOKEN=\#(shellSingleQuoted(HOMEBREW_CASK_TOKEN))
    CASK_INSTALLED_TOKEN=\#(shellSingleQuoted(HOMEBREW_CASK_INSTALLED_TOKEN))
    INFO_PLIST="$APP_PATH/Contents/Info.plist"
    APP_DIR="$(/usr/bin/dirname "$APP_PATH")"

    cleanup() {
        if [ -n "${SCRIPT_PATH:-}" ]; then
            /bin/rm -f "$SCRIPT_PATH" 2>/dev/null || true
        fi
    }
    trap cleanup EXIT

    timestamp() {
        /bin/date -u '+%Y-%m-%dT%H:%M:%SZ'
    }

    log() {
        printf '[%s] %s\n' "$(timestamp)" "$*"
    }

    state() {
        local phase="$1"
        local message="$2"
        local tmp
        log "$message"
        [ -n "$STATE_PATH" ] || return 0
        tmp="${STATE_PATH}.$$"
        if printf '%s\t%s\n' "$phase" "$message" >"$tmp"; then
            /bin/chmod 600 "$tmp" 2>/dev/null || true
            /bin/mv -f "$tmp" "$STATE_PATH" 2>/dev/null || true
        else
            /bin/rm -f "$tmp" 2>/dev/null || true
        fi
    }

    fail() {
        state "failed" "$*"
        /usr/bin/open "$RELEASES_PAGE"
        exit 1
    }

    app_version() {
        /usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$INFO_PLIST" 2>/dev/null || true
    }

    version_at_least() {
        /usr/bin/awk -v actual="$1" -v target="$2" '
            BEGIN {
                actual_count = split(actual, actual_parts, ".")
                target_count = split(target, target_parts, ".")
                for (i = 1; i <= 4; i++) {
                    actual_part = i <= actual_count ? actual_parts[i] : "0"
                    target_part = i <= target_count ? target_parts[i] : "0"
                    sub(/[^0-9].*$/, "", actual_part)
                    sub(/[^0-9].*$/, "", target_part)
                    actual_number = actual_part == "" ? 0 : actual_part + 0
                    target_number = target_part == "" ? 0 : target_part + 0
                    if (actual_number > target_number) { exit 0 }
                    if (actual_number < target_number) { exit 1 }
                }
                exit 0
            }'
    }

    run_brew() {
        log "Running: $BREW $*"
        "$BREW" "$@"
    }

    wait_for_presspeech_exit() {
        for _ in {1..60}; do
            if ! kill -0 "$PRESSPEECH_PID" 2>/dev/null; then
                return 0
            fi
            sleep 0.5
        done

        log "Presspeech was still running after 30s; sending TERM before updating."
        kill -TERM "$PRESSPEECH_PID" 2>/dev/null || true
        for _ in {1..20}; do
            if ! kill -0 "$PRESSPEECH_PID" 2>/dev/null; then
                return 0
            fi
            sleep 0.5
        done

        fail "Presspeech did not quit, so the app bundle was not touched."
    }

    installed_target_version() {
        local installed
        installed="$(app_version)"
        log "Installed app version: ${installed:-unknown}"
        [ -n "$installed" ] && version_at_least "$installed" "$TARGET_VERSION"
    }

    {
        echo "[$(timestamp)] Presspeech update starting"
        echo "Target version: $TARGET_VERSION"
        echo "Current installed version: $(app_version)"
        echo "Brew: $BREW"
        echo "Cask tap: $CASK_TAP"
        echo "Cask: $CASK_TOKEN"
        echo "Installed cask name: $CASK_INSTALLED_TOKEN"
        echo "App: $APP_PATH"
    }

    state "preparing" "Preparing Homebrew for Presspeech v$TARGET_VERSION..."

    if ! run_brew tap "$CASK_TAP"; then
        fail "brew tap failed; leaving the existing app in place."
    fi

    state "checking" "Checking Homebrew metadata..."
    if ! run_brew update --force; then
        fail "brew update failed; leaving the existing app in place."
    fi

    state "downloading" "Downloading Presspeech v$TARGET_VERSION..."
    if ! run_brew fetch --cask --force "$CASK_TOKEN"; then
        fail "brew cask fetch failed; leaving the existing app in place."
    fi

    state "installing" "Installing Presspeech v$TARGET_VERSION..."
    wait_for_presspeech_exit

    if ! run_brew upgrade --cask --force --appdir="$APP_DIR" "$CASK_TOKEN"; then
        fail "brew cask upgrade failed; leaving the existing app in place."
    fi

    state "verifying" "Verifying the installed app..."
    if ! installed_target_version; then
        log "brew upgrade completed without installing v$TARGET_VERSION; forcing qualified cask reinstall."
        state "installing" "Reinstalling Presspeech v$TARGET_VERSION..."
        if ! run_brew update --force; then
            fail "brew update failed before reinstall; leaving the existing app in place."
        fi
        if ! run_brew reinstall --cask --force --appdir="$APP_DIR" "$CASK_TOKEN"; then
            fail "brew cask reinstall failed; leaving the existing app in place."
        fi
    fi

    if ! installed_target_version; then
        fail "Expected Presspeech v$TARGET_VERSION or newer after update, but the installed app is still $(app_version)."
    fi

    state "relaunching" "Update complete. Reopening Presspeech..."
    sleep 2
    /usr/bin/open "$APP_PATH"
    state "complete" "Presspeech v$TARGET_VERSION is installed."
    """#
}

private func writePrivateUpdateHelperScript(_ script: String,
                                            directory: String = NSTemporaryDirectory(),
                                            fileName: String? = nil) throws -> String {
    guard !directory.isEmpty else { throw posixError(EINVAL) }
    let leafName = fileName ?? "presspeech-update-\(UUID().uuidString).sh"
    guard !leafName.isEmpty,
          (leafName as NSString).lastPathComponent == leafName else {
        throw posixError(EINVAL)
    }

    let path = (directory as NSString).appendingPathComponent(leafName)
    let flags = O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW
    let fd = Darwin.open(path, flags, PRIVATE_HELPER_FILE_MODE)
    guard fd >= 0 else { throw currentPOSIXError() }

    var closed = false
    var removeOnFailure = true
    do {
        try validateSingleLinkRegularFileDescriptor(fd)
        guard Darwin.fchmod(fd, PRIVATE_HELPER_FILE_MODE) == 0 else {
            throw currentPOSIXError()
        }
        try writeAllData(Data(script.utf8), to: fd)
        try validateSingleLinkRegularFileDescriptor(fd)

        let closeStatus = Darwin.close(fd)
        closed = true
        guard closeStatus == 0 else { throw currentPOSIXError() }

        removeOnFailure = false
        return path
    } catch {
        if !closed { _ = Darwin.close(fd) }
        if removeOnFailure { _ = Darwin.unlink(path) }
        throw error
    }
}

private struct PrivateOutputFile {
    let path: String
    let handle: FileHandle
}

private func openPrivateUpdateHelperLog(preferredPath: String = UPDATE_HELPER_LOG_PATH,
                                        fallbackDirectory: String = NSTemporaryDirectory()) throws -> PrivateOutputFile {
    do {
        let fd = try openPrivateOutputFileDescriptor(atPath: preferredPath,
                                                     exclusive: false,
                                                     removeOnFailure: false)
        return PrivateOutputFile(path: preferredPath,
                                 handle: FileHandle(fileDescriptor: fd, closeOnDealloc: true))
    } catch {
        let fallbackPath = (fallbackDirectory as NSString)
            .appendingPathComponent("presspeech-update-\(UUID().uuidString).log")
        let fd = try openPrivateOutputFileDescriptor(atPath: fallbackPath,
                                                     exclusive: true,
                                                     removeOnFailure: true)
        return PrivateOutputFile(path: fallbackPath,
                                 handle: FileHandle(fileDescriptor: fd, closeOnDealloc: true))
    }
}

private func createPrivateUpdateProgressStateFile(directory: String = NSTemporaryDirectory()) throws -> String {
    let path = (directory as NSString)
        .appendingPathComponent("\(UPDATE_PROGRESS_APP_PREFIX)\(UUID().uuidString).state")
    let fd = try openPrivateOutputFileDescriptor(atPath: path,
                                                 exclusive: true,
                                                 removeOnFailure: true)
    do {
        try writeAllData(Data("starting\tStarting update...\n".utf8), to: fd)
        guard Darwin.close(fd) == 0 else { throw currentPOSIXError() }
        return path
    } catch {
        _ = Darwin.close(fd)
        _ = Darwin.unlink(path)
        throw error
    }
}

private func writePrivateUpdateProgressState(phase: String,
                                             message: String,
                                             to path: String) throws {
    let safePhase = phase.replacingOccurrences(of: "\t", with: " ")
        .replacingOccurrences(of: "\n", with: " ")
    let safeMessage = message.replacingOccurrences(of: "\t", with: " ")
        .replacingOccurrences(of: "\n", with: " ")
    let fd = try openPrivateOutputFileDescriptor(atPath: path,
                                                 exclusive: false,
                                                 removeOnFailure: false)
    do {
        try writeAllData(Data("\(safePhase)\t\(safeMessage)\n".utf8), to: fd)
        guard Darwin.close(fd) == 0 else { throw currentPOSIXError() }
    } catch {
        _ = Darwin.close(fd)
        throw error
    }
}

private func openPrivateOutputFileDescriptor(atPath path: String,
                                             exclusive: Bool,
                                             removeOnFailure: Bool) throws -> Int32 {
    let url = URL(fileURLWithPath: path)
    try FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                            withIntermediateDirectories: true)

    var flags = O_WRONLY | O_CREAT | O_NOFOLLOW
    if exclusive { flags |= O_EXCL }

    let fd = Darwin.open(path, flags, PRIVATE_LOG_FILE_MODE)
    guard fd >= 0 else { throw currentPOSIXError() }

    do {
        try validateSingleLinkRegularFileDescriptor(fd)
        guard Darwin.fchmod(fd, PRIVATE_LOG_FILE_MODE) == 0 else {
            throw currentPOSIXError()
        }
        guard Darwin.ftruncate(fd, 0) == 0 else {
            throw currentPOSIXError()
        }
        return fd
    } catch {
        _ = Darwin.close(fd)
        if removeOnFailure { _ = Darwin.unlink(path) }
        throw error
    }
}

// MARK: - App
//
// Single class that owns the lifecycle and the AppKit menu-bar UI.
// All UI state lives here; subsystems (HotkeyListener, AudioCapture,
// TranscriptionWorker, UpdateCheck, …) hold their own state but
// call back into `PresspeechApp` for anything that touches the menu.

@MainActor
final class CorrectionShareCleanupDelegate: NSObject, @preconcurrency NSSharingServicePickerDelegate, NSSharingServiceDelegate {
    private let cleanup: (String) -> Void

    init(cleanup: @escaping (String) -> Void) {
        self.cleanup = cleanup
    }

    private func runCleanup(reason: String) {
        cleanup(reason)
    }

    func sharingServicePicker(_ sharingServicePicker: NSSharingServicePicker,
                              delegateFor sharingService: NSSharingService) -> NSSharingServiceDelegate? {
        self
    }

    func sharingServicePicker(_ sharingServicePicker: NSSharingServicePicker,
                              didChoose service: NSSharingService?) {
        if service == nil {
            runCleanup(reason: "dismissed")
        }
    }

    func sharingService(_ sharingService: NSSharingService, didShareItems items: [Any]) {
        runCleanup(reason: "shared")
    }

    func sharingService(_ sharingService: NSSharingService,
                        didFailToShareItems items: [Any],
                        error: Error) {
        runCleanup(reason: "share failed")
    }
}

/// Owns the dictionary editor's modal loop so Save can validate without
/// dismissing the alert. `NSAlert.runModal()` normally ends the modal session
/// as soon as a button is chosen, which used to throw away both text fields
/// before Presspeech could explain an invalid value.
@MainActor
private final class CorrectionEditorModalController: NSObject {
    private let alert: NSAlert
    private let sourceTitle: String
    private let sourceEditor: NSTextView
    private let replacementEditor: NSTextView
    private let isEditing: Bool
    private let existingCorrections: [TranscriptCorrection]
    private var result: TranscriptCorrection?

    init(alert: NSAlert,
         sourceTitle: String,
         sourceEditor: NSTextView,
         replacementEditor: NSTextView,
         isEditing: Bool,
         existingCorrections: [TranscriptCorrection]) {
        self.alert = alert
        self.sourceTitle = sourceTitle
        self.sourceEditor = sourceEditor
        self.replacementEditor = replacementEditor
        self.isEditing = isEditing
        self.existingCorrections = existingCorrections
    }

    func run() -> TranscriptCorrection? {
        guard alert.buttons.count >= 2 else { return nil }
        let save = alert.buttons[0]
        save.target = self
        save.action = #selector(saveClicked(_:))
        save.keyEquivalent = "\r"

        let cancel = alert.buttons[1]
        cancel.target = self
        cancel.action = #selector(cancelClicked(_:))
        cancel.keyEquivalent = "\u{1b}"

        alert.window.initialFirstResponder = sourceEditor
        let response = NSApp.runModal(for: alert.window)
        alert.window.orderOut(nil)
        return response == .alertFirstButtonReturn ? result : nil
    }

    @objc private func saveClicked(_ sender: NSButton) {
        let source = sourceEditor.string.trimmingCharacters(in: .whitespacesAndNewlines)
        let replacement = replacementEditor.string.trimmingCharacters(in: .whitespacesAndNewlines)
        if let issue = correctionEditorValidationIssue(
            source: source,
            replacement: replacement,
            isEditing: isEditing,
            existingCorrections: existingCorrections
        ) {
            let message = issue.message(sourceTitle: sourceTitle)
            alert.alertStyle = .warning
            alert.informativeText = message
            alert.layout()
            let editor = issue.focusesSource ? sourceEditor : replacementEditor
            alert.window.makeFirstResponder(editor)
            NSAccessibility.post(
                element: alert.window as Any,
                notification: .announcementRequested,
                userInfo: [
                    .announcement: message,
                    .priority: NSAccessibilityPriorityLevel.medium.rawValue,
                ]
            )
            NSSound.beep()
            return
        }

        result = TranscriptCorrection(source: source, replacement: replacement)
        NSApp.stopModal(withCode: .alertFirstButtonReturn)
    }

    @objc private func cancelClicked(_ sender: NSButton) {
        NSApp.stopModal(withCode: .alertSecondButtonReturn)
    }
}

@MainActor
private final class SetupChecklistDocumentView: NSView {
    // A flipped document view keeps the first setup row at the top when the
    // checklist is taller than the scroll viewport.
    override var isFlipped: Bool { true }
}

@MainActor
private final class RecordingHUDView: NSView {
    var mode: RecordingHUDMode = .recording {
        didSet { needsDisplay = true }
    }

    var level: Float = 0 {
        didSet { needsDisplay = true }
    }

    var phase: CGFloat = 0 {
        didSet { needsDisplay = true }
    }

    var showsCancelHint: Bool = false {
        didSet { needsDisplay = true }
    }

    var accessibilityOptions = RecordingHUDAccessibilityOptions.current() {
        didSet { needsDisplay = true }
    }

    override var isFlipped: Bool { true }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)

        let capsuleBounds = bounds.insetBy(dx: 1, dy: 1)
        let capsule = NSBezierPath(roundedRect: capsuleBounds,
                                   xRadius: capsuleBounds.height / 2,
                                   yRadius: capsuleBounds.height / 2)
        let backgroundAlpha: CGFloat = accessibilityOptions.usesOpaqueHUDBackground ? 1 : 0.9
        NSColor(calibratedWhite: accessibilityOptions.increaseContrast ? 0 : 0.06,
                alpha: backgroundAlpha).setFill()
        capsule.fill()
        if accessibilityOptions.increaseContrast {
            capsule.lineWidth = 2
            NSColor.white.withAlphaComponent(0.9).setStroke()
            capsule.stroke()
        }

        let clamped = CGFloat(max(0, min(1, level)))
        let accentColor: NSColor
        switch mode {
        case .recording:
            accentColor = .systemRed
        case .transcribing:
            accentColor = .systemBlue
        case .notice(.copiedToClipboard):
            accentColor = .systemOrange
        case .notice:
            accentColor = .systemYellow
        }

        let recordDotRect = NSRect(x: 17, y: bounds.midY - 6, width: 12, height: 12)
        accentColor.withAlphaComponent(0.18 + (0.22 * max(clamped, 0.35))).setFill()
        NSBezierPath(ovalIn: recordDotRect.insetBy(dx: -5, dy: -5)).fill()
        accentColor.withAlphaComponent(0.92).setFill()
        NSBezierPath(ovalIn: recordDotRect).fill()

        let statusText: String?
        switch mode {
        case .recording:
            statusText = recordingHUDRecordingStatusText(
                options: accessibilityOptions,
                showsCancelHint: showsCancelHint
            )
        case .transcribing:
            statusText = "Transcribing..."
        case .notice(let notice):
            statusText = notice.hudTitle
        }

        if let statusText {
            let text = statusText as NSString
            let attributes: [NSAttributedString.Key: Any] = [
                .font: NSFont.systemFont(ofSize: 12.5, weight: .semibold),
                .foregroundColor: NSColor.white.withAlphaComponent(
                    accessibilityOptions.increaseContrast ? 1 : 0.86
                ),
            ]
            let size = text.size(withAttributes: attributes)
            let rect = NSRect(x: 46,
                              y: bounds.midY - (size.height / 2),
                              width: min(size.width, bounds.width - 64),
                              height: size.height)
            text.draw(in: rect, withAttributes: attributes)
            return
        }

        var waveformMaxX = bounds.maxX - 15
        if showsCancelHint && bounds.width > 150 {
            let hint = "Esc cancel" as NSString
            let attributes: [NSAttributedString.Key: Any] = [
                .font: NSFont.systemFont(ofSize: 11, weight: .medium),
                .foregroundColor: NSColor.white.withAlphaComponent(
                    accessibilityOptions.increaseContrast ? 1 : 0.58
                ),
            ]
            let size = hint.size(withAttributes: attributes)
            let rect = NSRect(x: bounds.maxX - size.width - 17,
                              y: bounds.midY - (size.height / 2),
                              width: size.width,
                              height: size.height)
            hint.draw(in: rect, withAttributes: attributes)
            waveformMaxX = rect.minX - 14
        }

        guard clamped > 0.001 else { return }

        let barWidth: CGFloat = 3
        let barGap: CGFloat = 3
        let minHeight: CGFloat = 3
        let maxHeight: CGFloat = 28
        let startX: CGFloat = 46
        let maxBarCount = 29
        let availableWidth = max(0, waveformMaxX - startX)
        let barCount = min(maxBarCount, Int((availableWidth + barGap) / (barWidth + barGap)))
        guard barCount > 0 else { return }

        let centerY = bounds.midY
        let centerIndex = CGFloat(barCount - 1) / 2
        let centerDenominator = max(centerIndex, 1)

        for index in 0..<barCount {
            let i = CGFloat(index)
            let distance = abs(i - centerIndex) / centerDenominator
            let envelope = pow(max(0, 1 - distance), 0.55)
            let ripple = (sin((i * 0.74) + phase) + 1) / 2
            let fineRipple = (sin((i * 1.73) - (phase * 0.7)) + 1) / 2
            let motion = (ripple * 0.68) + (fineRipple * 0.32)
            let quietShape = 0.06 + (0.1 * envelope)
            let activeShape = 0.16 + (envelope * (0.5 + (0.5 * motion)))
            let activity = max(quietShape, min(1, quietShape + (clamped * activeShape)))
            let height = minHeight + ((maxHeight - minHeight) * activity)
            let x = startX + CGFloat(index) * (barWidth + barGap)
            let rect = NSRect(x: x,
                              y: centerY - (height / 2),
                              width: barWidth,
                              height: height)
            let path = NSBezierPath(roundedRect: rect,
                                    xRadius: barWidth / 2,
                                    yRadius: barWidth / 2)
            let hue = NSColor.systemRed.blended(withFraction: 0.16, of: .white) ?? .systemRed
            hue.withAlphaComponent(0.28 + (0.72 * activity)).setFill()
            path.fill()
        }
    }
}

private struct UpdateProgressLaunch {
    let statePath: String
    let logPath: String
    let targetVersion: String
    let cleanupAppPath: String

    init?(arguments: [String]) {
        guard arguments.count >= 5,
              arguments[0] == UPDATE_PROGRESS_ARGUMENT,
              !arguments[1].isEmpty,
              !arguments[2].isEmpty,
              !arguments[3].isEmpty,
              !arguments[4].isEmpty else {
            return nil
        }

        statePath = arguments[1]
        logPath = arguments[2]
        targetVersion = arguments[3]
        cleanupAppPath = arguments[4]
    }
}

private struct UpdateProgressState {
    let phase: String
    let message: String

    static func read(from path: String) -> UpdateProgressState? {
        guard let raw = try? String(contentsOfFile: path, encoding: .utf8) else { return nil }
        let trimmed = raw.trimmingCharacters(in: .newlines)
        let parts = trimmed.split(separator: "\t", maxSplits: 1, omittingEmptySubsequences: false)
        guard parts.count == 2 else { return nil }
        return UpdateProgressState(phase: String(parts[0]), message: String(parts[1]))
    }
}

private func isSafeUpdateProgressCleanupPath(_ path: String) -> Bool {
    guard !path.isEmpty else { return false }
    let tempPath = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
        .standardizedFileURL
        .path
    let tempPrefix = tempPath.hasSuffix("/") ? tempPath : "\(tempPath)/"
    let url = URL(fileURLWithPath: path, isDirectory: true).standardizedFileURL
    return url.path.hasPrefix(tempPrefix)
        && url.pathExtension == "app"
        && url.lastPathComponent.hasPrefix(UPDATE_PROGRESS_APP_PREFIX)
}

@MainActor
private final class UpdateProgressAppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private let launch: UpdateProgressLaunch
    private var window: NSWindow?
    private var pollTimer: Timer?
    private var closeWorkItem: DispatchWorkItem?
    private var lastPhase = ""
    private var lastMessage = ""

    private var messageLabel: NSTextField!
    private var detailLabel: NSTextField!
    private var progress: NSProgressIndicator!
    private var openReleaseButton: NSButton!
    private var closeButton: NSButton!

    init(launch: UpdateProgressLaunch) {
        self.launch = launch
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        buildWindow()
        pollState()
        pollTimer = Timer.scheduledTimer(timeInterval: 0.5,
                                         target: self,
                                         selector: #selector(updateProgressTimerFired(_:)),
                                         userInfo: nil,
                                         repeats: true)
        pollTimer?.tolerance = 0.15
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationWillTerminate(_ notification: Notification) {
        pollTimer?.invalidate()
        pollTimer = nil
        closeWorkItem?.cancel()
        scheduleCopiedAppCleanup()
    }

    func windowWillClose(_ notification: Notification) {
        NSApp.terminate(nil)
    }

    private func buildWindow() {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 430, height: 184),
                              styleMask: [.titled, .closable],
                              backing: .buffered,
                              defer: false)
        window.title = "Updating Presspeech"
        window.isReleasedWhenClosed = false
        window.delegate = self
        self.window = window

        let root = NSStackView()
        root.orientation = .vertical
        root.alignment = .leading
        root.spacing = 12
        root.edgeInsets = NSEdgeInsets(top: 18, left: 20, bottom: 16, right: 20)
        root.translatesAutoresizingMaskIntoConstraints = false

        let title = updateProgressLabel("Updating Presspeech to v\(launch.targetVersion)",
                                        font: .systemFont(ofSize: 18, weight: .semibold))
        messageLabel = updateProgressLabel("Starting update...",
                                           font: .systemFont(ofSize: 13, weight: .medium))
        detailLabel = updateProgressLabel("Presspeech will reopen automatically when the update finishes.",
                                          font: .systemFont(ofSize: 12),
                                          color: .secondaryLabelColor)
        detailLabel.preferredMaxLayoutWidth = 390

        progress = NSProgressIndicator()
        progress.style = .bar
        progress.isIndeterminate = true
        progress.usesThreadedAnimation = true
        progress.translatesAutoresizingMaskIntoConstraints = false
        progress.startAnimation(nil)

        let buttonRow = NSStackView()
        buttonRow.orientation = .horizontal
        buttonRow.alignment = .centerY
        buttonRow.spacing = 8
        buttonRow.translatesAutoresizingMaskIntoConstraints = false

        let openLog = NSButton(title: "Open Log",
                               target: self,
                               action: #selector(openUpdateLogClicked(_:)))
        openLog.bezelStyle = .rounded

        openReleaseButton = NSButton(title: "Open Release Page",
                                     target: self,
                                     action: #selector(openReleasePageClicked(_:)))
        openReleaseButton.bezelStyle = .rounded
        openReleaseButton.isHidden = true

        closeButton = NSButton(title: "Close",
                               target: self,
                               action: #selector(closeUpdateProgressClicked(_:)))
        closeButton.bezelStyle = .rounded
        closeButton.isHidden = true

        buttonRow.addArrangedSubview(openLog)
        buttonRow.addArrangedSubview(openReleaseButton)
        buttonRow.addArrangedSubview(NSView())
        buttonRow.addArrangedSubview(closeButton)
        buttonRow.setHuggingPriority(.defaultLow, for: .horizontal)

        root.addArrangedSubview(title)
        root.addArrangedSubview(messageLabel)
        root.addArrangedSubview(progress)
        root.addArrangedSubview(detailLabel)
        root.addArrangedSubview(buttonRow)

        for view in root.arrangedSubviews {
            view.widthAnchor.constraint(equalTo: root.widthAnchor,
                                        constant: -(root.edgeInsets.left + root.edgeInsets.right)).isActive = true
        }

        let container = NSView()
        container.addSubview(root)
        NSLayoutConstraint.activate([
            root.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            root.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            root.topAnchor.constraint(equalTo: container.topAnchor),
            root.bottomAnchor.constraint(equalTo: container.bottomAnchor),
            root.widthAnchor.constraint(equalToConstant: 430),
            progress.heightAnchor.constraint(equalToConstant: 14),
        ])

        window.contentView = container
        window.center()
        window.makeKeyAndOrderFront(nil)
    }

    private func updateProgressLabel(_ text: String,
                                     font: NSFont,
                                     color: NSColor = .labelColor) -> NSTextField {
        let label = NSTextField(labelWithString: text)
        label.font = font
        label.textColor = color
        label.lineBreakMode = .byWordWrapping
        label.maximumNumberOfLines = 0
        return label
    }

    @objc private func updateProgressTimerFired(_ timer: Timer) {
        pollState()
    }

    private func pollState() {
        let state = UpdateProgressState.read(from: launch.statePath)
            ?? UpdateProgressState(phase: "starting", message: "Starting update...")
        guard state.phase != lastPhase || state.message != lastMessage else { return }

        lastPhase = state.phase
        lastMessage = state.message
        messageLabel.stringValue = state.message

        switch state.phase {
        case "failed":
            progress.stopAnimation(nil)
            progress.isHidden = true
            detailLabel.stringValue = "The existing app was left in place. Open the log for details."
            openReleaseButton.isHidden = false
            closeButton.isHidden = false
            NSApp.activate(ignoringOtherApps: true)
        case "complete":
            progress.stopAnimation(nil)
            progress.isHidden = true
            detailLabel.stringValue = "The updated app is opening. This window will close shortly."
            closeButton.isHidden = false
            scheduleClose(after: 4)
        case "installing":
            detailLabel.stringValue = "Presspeech has quit so Homebrew can replace the app bundle. It will reopen automatically."
        case "relaunching":
            detailLabel.stringValue = "Closing the updater so macOS opens the newly installed app."
            scheduleClose(after: 0.5)
        default:
            detailLabel.stringValue = "Presspeech will reopen automatically when the update finishes."
        }
    }

    private func scheduleClose(after delay: TimeInterval) {
        guard closeWorkItem == nil else { return }
        let item = DispatchWorkItem { NSApp.terminate(nil) }
        closeWorkItem = item
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: item)
    }

    private func scheduleCopiedAppCleanup() {
        guard isSafeUpdateProgressCleanupPath(launch.cleanupAppPath) else { return }
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/bin/sh")
        proc.arguments = ["-c", "sleep 2; /bin/rm -rf \"$1\"", "cleanup", launch.cleanupAppPath]
        proc.environment = systemToolProcessEnvironment()
        try? proc.run()
    }

    @objc private func openUpdateLogClicked(_ sender: NSButton) {
        NSWorkspace.shared.open(URL(fileURLWithPath: launch.logPath))
    }

    @objc private func openReleasePageClicked(_ sender: NSButton) {
        NSWorkspace.shared.open(GITHUB_RELEASES_PAGE)
    }

    @objc private func closeUpdateProgressClicked(_ sender: NSButton) {
        NSApp.terminate(nil)
    }
}

@MainActor
final class PresspeechApp: NSObject, NSApplicationDelegate, NSWindowDelegate, NSMenuDelegate,
                            NSTableViewDataSource, NSTableViewDelegate {
    private static let launchAtLoginMenuItemIdentifier = NSUserInterfaceItemIdentifier(
        "com.local.presspeech.menu.launch-at-login"
    )
    private var statusItem: NSStatusItem!
    private var templateImage: NSImage?
    private var recordingImage: NSImage?
    private var errorImage: NSImage?
    private let audio = AudioCapture()
    private let hotkey = HotkeyListener()
    private let asr = TranscriptionWorker()
    private let settings = Settings.shared

    private var isRecording = false
    private var isFinishingRecording = false
    private var isBusy = false
    private var recordingPasteTarget: DictationPasteTarget?
    private var isReady = false
    private var isCoreRuntimeReady = false
    private var isSpeechModelReady = false
    private var isTerminating = false
    private var isResettingSpeechModelCache = false
    private var isSwitchingSpeechModel = false
    private var fallbackSpeechModelProfileAfterStartupFailure: SpeechModelProfile?
    private var startupTask: Task<Void, Never>?
    private var updateCheckLoopTask: Task<Void, Never>?
    private var manualUpdateCheckTask: Task<Void, Never>?
    private var startupStatusTitle = "Loading speech model…"
    private var speechModelStartupProgressFraction: Double?
    private var startupFailure: StartupFailure?
    private var didTouchAudioEngine = false
    private var permissionReadinessTimer: Timer?
    private var lastPermissionReadinessMissingKey: String?
    /// Recording-time system-audio mute state machine. Main-actor
    /// only; transitions are driven by muteIfNeededForRecording /
    /// unmuteIfWeMuted and the SystemAudio.*Async completions. The
    /// pure decision logic lives in systemAudioMuteProbeDecision /
    /// systemAudioMuteCommandDecision / systemAudioUnmuteRequestDecision.
    private var systemAudioMutePhase: SystemAudioMutePhase = .idle
    /// Set when the recording ends while the probe or mute command is
    /// still in flight; the in-flight completion honours it.
    private var systemAudioUnmuteRequested = false
    private var maxDurationWorkItem: DispatchWorkItem?
    private var postReleaseCaptureWorkItem: DispatchWorkItem?
    private var audioIdleStopWorkItem: DispatchWorkItem?
    private var isRestartingAudioInput = false
    private var pendingAudioRouteRefresh = false
    private var audioConfigurationChangeSuppressedUntil: TimeInterval?
    private var workspacePowerObservers: [NSObjectProtocol] = []
    private var workspaceAccessibilityObserver: NSObjectProtocol?
    private var shouldResumeRuntimeAfterWake = false
    private var didLogDeferredWakeRecovery = false
    private var didOfferSetupChecklistThisLaunch = false
    private var setupChecklistWindow: NSWindow?
    private var setupChecklistRefreshTimer: Timer?
    private var renderedSetupChecklistSnapshot: SetupChecklistSnapshot?
    private var dictationScratchpadWindow: NSWindow?
    private weak var dictationScratchpadTextView: NSTextView?
    private var correctionsManagerWindow: NSWindow?
    private weak var correctionsManagerSearchField: NSSearchField?
    private weak var correctionsManagerTableView: NSTableView?
    private var correctionsManagerContextMenu: NSMenu?
    private weak var correctionsManagerSummaryLabel: NSTextField?
    private weak var correctionsManagerEditButton: NSButton?
    private weak var correctionsManagerDeleteButton: NSButton?
    private var correctionsManagerVisibleIndices: [Int] = []
    private var hotkeyTestSucceeded = false
    private var recordingLevelTimer: Timer?
    private var recordingVisualLevel: Float = 0
    private var recordingHUDPhase: CGFloat = 0
    private var recordingHUDAccessibilityOptions = RecordingHUDAccessibilityOptions.current()
    private var lastRecordingLevelSequence: UInt64 = 0
    private var staleRecordingLevelTicks = 0
    private var recordingHUDPanel: NSPanel?
    private var recordingHUDView: RecordingHUDView?
    private var recordingHUDAnimationToken = 0
    private var delayedBusyHUDWorkItem: DispatchWorkItem?
    private var dictationNoticeHUDWorkItem: DispatchWorkItem?
    private var errorFlashWorkItem: DispatchWorkItem?
    private var dictationNotice: DictationNotice?
    private var systemAudioMuteWatchdog: Process?

    /// Last N transcripts, newest first. Shown in the History submenu.
    private var history: [String] = []

    /// In-session click counter per permission. Click #2 onwards
    /// resets the matching TCC entry before re-requesting — belt
    /// and braces for stuck DENIED entries macOS occasionally caches.
    private var permClickCount: [Permission: Int] = [:]

    /// Latest release detected by the periodic check. nil = no update,
    /// or user has skipped it.
    private var pendingUpdate: GitHubRelease?
    private var isCheckingForUpdates = false
    /// True while the async brew-install preflight for "Update now"
    /// is running; guards against a second click spawning a second
    /// update helper.
    private var isPreparingUpdate = false
    private var reminderPausedUpdateVersion: String?
    private var reminderPausedUntil: Date?

    private struct CorrectionImportSummary {
        let total: Int
        let newCount: Int
        let updatedCount: Int
        let unchangedCount: Int
    }

    private enum CorrectionImportChoice {
        case merge
        case replace
    }

    private enum CorrectionEditorMode {
        case correction
        case shortcut
    }

    private var correctionSyncTimer: Timer?
    private var correctionSyncFileFingerprint: CorrectionSyncFileFingerprint?
    private var correctionSyncBaselineCorrections: [TranscriptCorrection] = []
    private var isApplyingCorrectionSyncFile = false
    /// Serial queue for the periodic sync-file scan (validate + hash
    /// + read). The UI recommends putting the sync file in iCloud
    /// Drive, where open(2) on a dataless file can block for seconds
    /// while the content downloads — far too long for the main
    /// thread, which also services the session-wide hotkey event tap.
    /// `correctionSyncScanInFlight` (main-actor) guarantees scans
    /// never overlap; results hop back to the main actor, where the
    /// existing merge/apply logic runs unchanged.
    private static let correctionSyncScanQueue = DispatchQueue(label: "PresspeechCorrectionSyncScan",
                                                               qos: .utility)
    private var correctionSyncScanInFlight = false
    /// Scan request that arrived while a scan was in flight; re-issued
    /// (with the strongest flags seen) when the in-flight scan lands.
    private var pendingCorrectionSyncScan: (force: Bool, presentErrors: Bool)?
    private var correctionSharePicker: NSSharingServicePicker?
    private var correctionShareCleanupDelegate: CorrectionShareCleanupDelegate?
    private var pendingSharedCorrectionsURL: URL?

    // MARK: - Lifecycle

    private func completeReadinessIfPossible(reason: String) {
        let missing = (isReady || isCoreRuntimeReady) ? missingPermissions() : []
        switch readinessTransition(isReady: isReady,
                                   isCoreRuntimeReady: isCoreRuntimeReady,
                                   missingPermissions: missing) {
        case .rebuildMenuOnly:
            if isReady {
                permClickCount.removeAll()
                stopPermissionReadinessMonitor()
            }
            rebuildMenu()
            return
        case .blockForPermissions(let missing):
            enterPermissionBlockedState(missing: missing, reason: reason)
            return
        case .startHotkeyListener:
            break
        }

        hotkey.onPress = { [weak self] in self?.handleHotkeyPress() }
        hotkey.onRelease = { [weak self] in self?.handleRelease() }
        hotkey.onCancel = { [weak self] in self?.cancelActiveRecording(reason: "escape") }
        hotkey.isRecordingActive = { [weak self] in self?.isRecording == true }
        // Missing permissions are deliberately not a blocker here: that press
        // enters the permission-blocked state and provides its own feedback.
        hotkey.recordingStartBlocker = { [weak self] in
            resolvedRecordingStartBlocker(for: self) { app in
                app.recordingStartBlocker()
            }
        }
        guard hotkey.start() else {
            isReady = false
            isRecording = false
            isBusy = false
            hotkey.onPress = nil
            hotkey.onRelease = nil
            hotkey.onCancel = nil
            hotkey.isRecordingActive = nil
            hotkey.recordingStartBlocker = nil
            hotkey.resetToggleState()
            hotkey.stop()
            log("readiness failed (\(reason)): hotkey listener unavailable")
            setMenuBarState(.error)
            if missingPermissions().isEmpty {
                startupFailure = StartupFailure(stage: .hotkeyListener,
                                                detail: "The keyboard event tap could not be started.")
            } else {
                startPermissionReadinessMonitor(reason: reason)
            }
            rebuildMenu()
            return
        }

        isReady = true
        startupStatusTitle = "Ready"
        startupFailure = nil
        stopPermissionReadinessMonitor()
        setMenuBarState(.idle)

        rebuildMenu()
        startUpdateCheckLoop()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        if settings.normalizeSpeechModelProfileForCurrentBuild() {
            log("ASR: reset unsupported saved speech model selection to \(settings.speechModelProfile.shortName)")
        }

        migrateLegacyIdentityFilesIfNeeded(settings: settings)
        if settings.didMigrateLegacyIdentity {
            // macOS does not transfer privacy grants between bundle
            // identifiers. Remove the former rows so System Settings
            // shows one current Presspeech identity, then let the
            // normal setup checklist request fresh grants.
            for permission in Permission.allCases {
                TCC.reset(permission, bundleID: LEGACY_SETTINGS_SUITE)
            }
            log("identity migration: cleared former privacy-permission rows")
        }
        recoverStaleTCCAfterUpgrade()
        let previousExitNotice = previousExitNoticeAction(previousRunWasActive: settings.hasActiveRunMarker)
        recoverStaleSystemAudioMuteIfNeeded()
        settings.hasActiveRunMarker = true
        restoreUpdateReminderPause()

        NSApp.setActivationPolicy(settings.showInDock ? .regular : .accessory)
        installApplicationMenu()

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        configureStatusItemImage()
        setMenuBarState(.loading)
        startCorrectionSyncIfConfigured()
        rebuildMenu()

        audio.onConfigurationChange = { [weak self] in
            Task { @MainActor in
                self?.handleAudioConfigurationChange()
            }
        }
        installWorkspacePowerObservers()
        installWorkspaceAccessibilityObserver()

        // Configure hotkey listener up front so it picks up the user's
        // saved choice the moment the tap goes live.
        hotkey.setHotkey(hotkeyChoice(forKeycode: settings.hotkeyKeycode))
        hotkey.setTriggerMode(settings.triggerMode)

        startStartup(reason: "launch")
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { [weak self] in
            self?.maybeShowSetupChecklist(reason: "launch")
        }
        if previousExitNotice == .showNotice {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { [weak self] in
                self?.showPreviousExitNoticeIfAppropriate()
            }
        }
    }

    /// A status item can be temporarily omitted when the menu bar has no room.
    /// Reopening an already-running agent from Finder or Spotlight must still
    /// reveal a control surface; otherwise this LSUIElement app has no window,
    /// Dock icon, or application menu from which to recover.
    func applicationShouldHandleReopen(_ sender: NSApplication,
                                       hasVisibleWindows flag: Bool) -> Bool {
        guard !isTerminating else { return false }
        if flag {
            showAppForModal()
            if let window = sender.windows.first(where: {
                !($0 is NSPanel) && ($0.isVisible || $0.isMiniaturized)
            }) {
                if window.isMiniaturized {
                    window.deminiaturize(nil)
                }
                window.makeKeyAndOrderFront(nil)
            }
            log("app reopened; raised existing window")
        } else {
            log("app reopened; showing setup checklist")
            showSetupChecklist()
        }
        // The reopen event is fully handled above. Returning true would make
        // AppKit continue into untitled-document handling, which this agent
        // app does not use.
        return false
    }

    /// When the optional Dock icon is enabled, its contextual menu exposes the
    /// high-value controls needed as an alternative access route.
    func applicationDockMenu(_ sender: NSApplication) -> NSMenu? {
        buildDockMenu()
    }

    func applicationWillTerminate(_ notification: Notification) {
        isTerminating = true
        settings.hasActiveRunMarker = false
        startupTask?.cancel()
        startupTask = nil
        updateCheckLoopTask?.cancel()
        updateCheckLoopTask = nil
        manualUpdateCheckTask?.cancel()
        manualUpdateCheckTask = nil
        stopPermissionReadinessMonitor()
        stopSetupChecklistRefreshTimer()
        removeWorkspacePowerObservers()
        removeWorkspaceAccessibilityObserver()
        correctionSyncTimer?.invalidate()
        correctionSyncTimer = nil
        cleanupPendingSharedCorrections(reason: "terminate")
        audio.onConfigurationChange = nil
        cancelRecordingForTermination()
        // If the mute lifecycle is mid-flight or still holding the
        // mute, the watchdog must outlive us: the async unmute
        // requested by cancelRecordingForTermination may not run
        // before the process exits, and the watchdog unmutes + clears
        // the marker once our pid disappears.
        if systemAudioMutePhase == .idle {
            stopSystemAudioMuteWatchdog()
        }
    }

    private func installWorkspacePowerObservers() {
        removeWorkspacePowerObservers()
        let center = NSWorkspace.shared.notificationCenter
        workspacePowerObservers = [
            center.addObserver(forName: NSWorkspace.willSleepNotification,
                               object: nil,
                               queue: .main) { [weak self] _ in
                Task { @MainActor in
                    self?.handleSystemWillSleep()
                }
            },
            center.addObserver(forName: NSWorkspace.didWakeNotification,
                               object: nil,
                               queue: .main) { [weak self] _ in
                Task { @MainActor in
                    self?.handleSystemDidWake()
                }
            },
        ]
    }

    private func removeWorkspacePowerObservers() {
        let center = NSWorkspace.shared.notificationCenter
        for observer in workspacePowerObservers {
            center.removeObserver(observer)
        }
        workspacePowerObservers.removeAll()
    }

    private func installWorkspaceAccessibilityObserver() {
        removeWorkspaceAccessibilityObserver()
        let center = NSWorkspace.shared.notificationCenter
        workspaceAccessibilityObserver = center.addObserver(
            forName: NSWorkspace.accessibilityDisplayOptionsDidChangeNotification,
            object: NSWorkspace.shared,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.refreshRecordingHUDAccessibility()
            }
        }
    }

    private func removeWorkspaceAccessibilityObserver() {
        guard let observer = workspaceAccessibilityObserver else { return }
        NSWorkspace.shared.notificationCenter.removeObserver(observer)
        workspaceAccessibilityObserver = nil
    }

    private func refreshRecordingHUDAccessibility() {
        let options = RecordingHUDAccessibilityOptions.current()
        recordingHUDAccessibilityOptions = options
        recordingHUDView?.accessibilityOptions = options
        if options.usesStaticRecordingPresentation {
            recordingHUDView?.level = 0
            recordingHUDView?.phase = 0
        }

        // If Reduce Motion becomes active while the panel is expanding or
        // collapsing, invalidate that completion and settle immediately.
        guard options.reduceMotion,
              let panel = recordingHUDPanel,
              panel.isVisible else { return }
        recordingHUDAnimationToken += 1
        guard isRecording || isBusy || dictationNotice != nil else {
            panel.orderOut(nil)
            panel.alphaValue = 1
            return
        }
        panel.alphaValue = 1
        if let frame = currentRecordingHUDFrame(size: RECORDING_HUD_EXPANDED_SIZE) {
            panel.setFrame(frame, display: true)
        }
        panel.orderFrontRegardless()
    }

    private func cleanupPendingSharedCorrections(reason: String) {
        correctionSharePicker = nil
        correctionShareCleanupDelegate = nil

        guard let url = pendingSharedCorrectionsURL else { return }
        pendingSharedCorrectionsURL = nil

        let folder = url.deletingLastPathComponent().standardizedFileURL
        let tempRoot = FileManager.default.temporaryDirectory.standardizedFileURL.path
        let normalizedTempRoot = tempRoot.hasSuffix("/") ? tempRoot : "\(tempRoot)/"

        guard url.lastPathComponent == CORRECTIONS_FILE_NAME,
              folder.lastPathComponent.hasPrefix("Presspeech-"),
              folder.path.hasPrefix(normalizedTempRoot)
        else {
            log("correction share cleanup skipped (\(reason)): unexpected temp file")
            return
        }

        do {
            try FileManager.default.removeItem(at: folder)
            log("correction share cleanup completed (\(reason))")
        } catch {
            log("correction share cleanup failed (\(reason))")
        }
    }

    private func startStartup(reason: String) {
        guard startupTask == nil else {
            log("startup ignored (\(reason)): already in progress")
            rebuildMenu()
            return
        }

        prepareForStartupAttempt()
        let speechModelProfile = settings.speechModelProfile

        // Load ASR FIRST, then audio + hotkey. Reversing this order
        // makes the first-launch CoreML compile of the ANE Encoder
        // hang. The bench under experiments/swift-bench/ never opens
        // an audio session so it doesn't see this.
        startupTask = Task { @MainActor in
            var stage = StartupFailureStage.speechModel
            defer {
                startupTask = nil
                rebuildMenu()
                recoverRuntimeAfterWakeIfNeeded(reason: "startup finished after wake")
            }

            do {
                try await asr.load(profile: speechModelProfile) { [weak self] progress in
                    Task { @MainActor in
                        self?.updateSpeechModelStartupProgress(progress)
                    }
                }
                guard !Task.isCancelled, !isTerminating else { return }
                fallbackSpeechModelProfileAfterStartupFailure = nil
                isSpeechModelReady = true
                speechModelStartupProgressFraction = nil

                stage = .audioInput
                startupStatusTitle = "Starting audio input…"
                rebuildMenu()

                try await startAudioInputWithRetries(reason: reason,
                                                     initialStatusTitle: "Starting audio input…")
                guard !Task.isCancelled, !isTerminating else { return }

                isCoreRuntimeReady = true
                startupFailure = nil
                startupStatusTitle = "Finishing setup…"
                completeReadinessIfPossible(reason: reason)
            } catch {
                guard !Task.isCancelled, !isTerminating else { return }
                recordStartupFailure(stage: stage, error: error, reason: reason)
            }
        }
    }

    private func prepareForStartupAttempt() {
        cancelMaxDurationAutoRelease()
        cancelPostReleaseCapture()

        if isRecording || audio.isRunning {
            _ = audio.endRecording()
        }
        stopRecordingLevelMeter()
        unmuteIfWeMuted()

        isReady = false
        isCoreRuntimeReady = false
        isSpeechModelReady = false
        isRecording = false
        isBusy = false
        recordingPasteTarget = nil
        pendingAudioRouteRefresh = false
        shouldResumeRuntimeAfterWake = false
        didLogDeferredWakeRecovery = false
        startupFailure = nil
        startupStatusTitle = "Loading speech model…"
        speechModelStartupProgressFraction = nil

        hotkey.onPress = nil
        hotkey.onRelease = nil
        hotkey.onCancel = nil
        hotkey.isRecordingActive = nil
        hotkey.recordingStartBlocker = nil
        hotkey.resetToggleState()
        hotkey.stop()
        if didTouchAudioEngine {
            stopAudioEngineImmediately()
        }

        setMenuBarState(.loading)
        rebuildMenu()
    }

    private func updateSpeechModelStartupProgress(_ progress: DownloadProgress) {
        guard startupTask != nil, !isTerminating else { return }
        let next = speechModelStartupStatusTitle(progress)
        let nextProgressFraction = speechModelStartupProgressValue(progress)
        guard next != startupStatusTitle
            || nextProgressFraction != speechModelStartupProgressFraction else { return }
        startupStatusTitle = next
        speechModelStartupProgressFraction = nextProgressFraction
        rebuildMenu()
    }

    private func recordStartupFailure(stage: StartupFailureStage, error: Error, reason: String) {
        cancelPostReleaseCapture()
        if stage == .speechModel,
           let fallback = fallbackSpeechModelProfileAfterStartupFailure,
           fallback != settings.speechModelProfile,
           !isTerminating {
            let failedProfile = settings.speechModelProfile
            fallbackSpeechModelProfileAfterStartupFailure = nil
            settings.speechModelProfile = fallback
            isSwitchingSpeechModel = true
            isCoreRuntimeReady = false
            isSpeechModelReady = false
            isReady = false
            isRecording = false
            isBusy = false
            recordingPasteTarget = nil
            startupFailure = nil
            startupStatusTitle = "Falling back to \(fallback.shortName)…"
            speechModelStartupProgressFraction = nil
            setMenuBarState(.loading)
            log("ASR: \(failedProfile.shortName) failed to load during switch; falling back to \(fallback.shortName): \(startupFailureLogDetail(stage: stage, error: error))")
            rebuildMenu()
            DispatchQueue.main.async { [weak self] in
                guard let self, !self.isTerminating else { return }
                Task { @MainActor in
                    await self.asr.unload()
                    self.isSwitchingSpeechModel = false
                    self.startStartup(reason: "speech model fallback")
                }
            }
            return
        }

        fallbackSpeechModelProfileAfterStartupFailure = nil
        isCoreRuntimeReady = false
        if stage == .speechModel {
            isSpeechModelReady = false
        }
        isReady = false
        isRecording = false
        isBusy = false
        recordingPasteTarget = nil
        speechModelStartupProgressFraction = nil
        stopRecordingLevelMeter()

        hotkey.onPress = nil
        hotkey.onRelease = nil
        hotkey.onCancel = nil
        hotkey.isRecordingActive = nil
        hotkey.recordingStartBlocker = nil
        hotkey.resetToggleState()
        hotkey.stop()
        if didTouchAudioEngine {
            stopAudioEngineImmediately()
        }

        let detail = startupFailureDetail(stage: stage, error: error)
        startupFailure = StartupFailure(stage: stage, detail: detail)
        log("startup failed (\(reason), \(stage)): \(startupFailureLogDetail(stage: stage, error: error))")
        setMenuBarState(.error)
        if !missingPermissions().isEmpty {
            startPermissionReadinessMonitor(reason: reason)
        }
        maybeShowSetupChecklist(reason: "startup failure")
        rebuildMenu()
    }

    private func startAudioInputWithRetries(reason: String,
                                            initialStatusTitle: String) async throws {
        let totalAttempts = AUDIO_START_RETRY_DELAYS_SECONDS.count + 1
        var lastError: Error?

        for attempt in 1...totalAttempts {
            try Task.checkCancellation()
            guard !isTerminating else { throw CancellationError() }

            startupStatusTitle = attempt == 1
                ? initialStatusTitle
                : "Starting audio input… (\(attempt)/\(totalAttempts))"
            rebuildMenu()

            do {
                didTouchAudioEngine = true
                suppressAudioConfigurationChangesFromAppEngineUpdate()
                try audio.startEngine(inputDevicePreference: settings.inputDevice)
                stopAudioEngineImmediately()
                return
            } catch {
                lastError = error
                stopAudioEngineImmediately()
                log("audio startup attempt \(attempt)/\(totalAttempts) failed (\(reason)): \(singleLineLogDetail(audioStartupErrorDescription(error)))")

                guard let delay = audioStartupRetryDelaySeconds(afterFailedAttempt: attempt) else {
                    throw error
                }

                startupStatusTitle = audioStartupRetryStatusTitle(nextAttempt: attempt + 1,
                                                                  totalAttempts: totalAttempts,
                                                                  delaySeconds: delay)
                rebuildMenu()
                try await Task.sleep(nanoseconds: delay * 1_000_000_000)
            }
        }

        if let lastError {
            throw lastError
        }
    }

    // MARK: - Sleep/wake runtime recovery

    private func handleSystemWillSleep() {
        guard !isTerminating else { return }

        if shouldResumeRuntimeAfterSystemSleep(isTerminating: isTerminating,
                                               isCoreRuntimeReady: isCoreRuntimeReady,
                                               isReady: isReady,
                                               isRecording: isRecording,
                                               audioIsRunning: audio.isRunning) {
            shouldResumeRuntimeAfterWake = true
            didLogDeferredWakeRecovery = false
        }

        if isRecording || audio.isRunning {
            cancelActiveRecording(reason: "system sleep", runDeferredRefresh: false)
        }

        guard isCoreRuntimeReady || isReady else {
            rebuildMenu()
            return
        }

        pauseAudioRuntimeForSystemSleep()
    }

    private func handleSystemDidWake() {
        guard !isTerminating else { return }
        guard shouldResumeRuntimeAfterWake else { return }
        log("system wake detected")
        recoverRuntimeAfterWakeIfNeeded(reason: "system wake")
    }

    private func pauseAudioRuntimeForSystemSleep() {
        cancelMaxDurationAutoRelease()
        cancelPostReleaseCapture()
        stopRecordingLevelMeter()
        unmuteIfWeMuted()

        isReady = false
        isCoreRuntimeReady = false
        isRecording = false
        pendingAudioRouteRefresh = false
        hotkey.onPress = nil
        hotkey.onRelease = nil
        hotkey.onCancel = nil
        hotkey.isRecordingActive = nil
        hotkey.recordingStartBlocker = nil
        hotkey.resetToggleState()
        hotkey.stop()
        stopAudioEngineImmediately()

        startupFailure = nil
        startupStatusTitle = "Waiting for system wake…"
        setMenuBarState(isBusy ? .busy : .loading)
        log("system sleep: audio runtime paused")
        rebuildMenu()
    }

    private func recoverRuntimeAfterWakeIfNeeded(reason: String) {
        switch wakeRuntimeRecoveryAction(shouldResumeAfterWake: shouldResumeRuntimeAfterWake,
                                         isTerminating: isTerminating,
                                         hasStartupTask: startupTask != nil,
                                         isBusy: isBusy,
                                         isSpeechModelReady: isSpeechModelReady) {
        case .ignore:
            return
        case .deferUntilIdle:
            if !didLogDeferredWakeRecovery {
                didLogDeferredWakeRecovery = true
                log("system wake recovery deferred until idle")
            }
            rebuildMenu()
        case .startAudioRuntime:
            shouldResumeRuntimeAfterWake = false
            didLogDeferredWakeRecovery = false
            startAudioRuntimeAfterWake(reason: reason)
        case .startFullStartup:
            shouldResumeRuntimeAfterWake = false
            didLogDeferredWakeRecovery = false
            startStartup(reason: reason)
        }
    }

    private func startAudioRuntimeAfterWake(reason: String) {
        guard !isRestartingAudioInput else {
            return
        }
        guard startupTask == nil, !isBusy, !isTerminating else {
            shouldResumeRuntimeAfterWake = true
            recoverRuntimeAfterWakeIfNeeded(reason: reason)
            return
        }

        isReady = false
        isCoreRuntimeReady = false
        isRecording = false
        pendingAudioRouteRefresh = false
        isRestartingAudioInput = true
        startupFailure = nil
        startupStatusTitle = "Restarting audio input…"
        hotkey.onPress = nil
        hotkey.onRelease = nil
        hotkey.onCancel = nil
        hotkey.isRecordingActive = nil
        hotkey.recordingStartBlocker = nil
        hotkey.resetToggleState()
        hotkey.stop()
        stopAudioEngineImmediately()
        setMenuBarState(.loading)
        rebuildMenu()

        Task { @MainActor in
            defer { isRestartingAudioInput = false }
            do {
                try await startAudioInputWithRetries(reason: reason,
                                                     initialStatusTitle: "Restarting audio input…")
                guard !isTerminating else { return }
                isCoreRuntimeReady = true
                startupStatusTitle = "Finishing setup…"
                completeReadinessIfPossible(reason: reason)
            } catch {
                guard !isTerminating else { return }
                recordStartupFailure(stage: .audioInput, error: error, reason: reason)
            }
        }
    }

    // MARK: - Permission readiness

    private func enterPermissionBlockedState(missing: [Permission]? = nil, reason: String) {
        let missing = missing ?? missingPermissions()
        guard !missing.isEmpty else {
            completeReadinessIfPossible(reason: reason)
            return
        }

        cancelMaxDurationAutoRelease()
        cancelPostReleaseCapture()
        if isRecording || audio.isRunning || audio.isEngineStarted {
            _ = audio.endRecording()
            stopAudioEngineImmediately()
        }
        stopRecordingLevelMeter()
        unmuteIfWeMuted()

        isReady = false
        isRecording = false
        isBusy = false
        recordingPasteTarget = nil
        hotkey.onPress = nil
        hotkey.onRelease = nil
        hotkey.onCancel = nil
        hotkey.isRecordingActive = nil
        hotkey.recordingStartBlocker = nil
        hotkey.resetToggleState()
        hotkey.stop()

        logPermissionReadinessWait(missing)
        startPermissionReadinessMonitor(reason: reason)
        maybeShowSetupChecklist(reason: reason)
        setMenuBarState(.loading)
        rebuildMenu()
    }

    private func missingPermissions() -> [Permission] {
        Permission.allCases.filter { !Permissions.isGranted($0) }
    }

    @discardableResult
    private func logPermissionReadinessWait(_ missing: [Permission]) -> Bool {
        let key = missing.map(\.rawValue).joined(separator: "|")
        guard key != lastPermissionReadinessMissingKey else { return false }
        lastPermissionReadinessMissingKey = key
        log("readiness retry waiting for permissions: \(missing.map(\.rawValue).joined(separator: ", "))")
        return true
    }

    private func startPermissionReadinessMonitor(reason: String) {
        guard permissionReadinessTimer == nil else { return }
        log("permission readiness monitor started (\(reason))")
        permissionReadinessTimer = Timer.scheduledTimer(timeInterval: 2,
                                                        target: self,
                                                        selector: #selector(permissionReadinessTimerFired(_:)),
                                                        userInfo: nil,
                                                        repeats: true)
        permissionReadinessTimer?.tolerance = 0.5
    }

    private func stopPermissionReadinessMonitor() {
        guard permissionReadinessTimer != nil else { return }
        permissionReadinessTimer?.invalidate()
        permissionReadinessTimer = nil
        lastPermissionReadinessMissingKey = nil
        log("permission readiness monitor stopped")
    }

    @objc private func permissionReadinessTimerFired(_ timer: Timer) {
        guard isCoreRuntimeReady else {
            let missing = missingPermissions()
            guard !missing.isEmpty else {
                permClickCount.removeAll()
                stopPermissionReadinessMonitor()
                rebuildMenu()
                return
            }
            if logPermissionReadinessWait(missing) {
                rebuildMenu()
            }
            return
        }

        if isReady {
            let missing = missingPermissions()
            guard !missing.isEmpty else {
                permClickCount.removeAll()
                stopPermissionReadinessMonitor()
                rebuildMenu()
                return
            }
            enterPermissionBlockedState(missing: missing, reason: "permission monitor")
            return
        }

        completeReadinessIfPossible(reason: "permission monitor")
    }

    // MARK: - File imports

    func application(_ sender: NSApplication, openFiles filenames: [String]) {
        var didImport = false
        for filename in filenames {
            let url = URL(fileURLWithPath: filename)
            if importCorrectionsFromUserSelectedFile(url) {
                didImport = true
            }
        }
        sender.reply(toOpenOrPrint: didImport ? .success : .failure)
    }

    // MARK: - Menu bar appearance
    //
    // Same silhouette across all states; only colour shifts. The
    // template image is used for idle/loading/busy so it auto-adapts to
    // light/dark menu bar. For recording/error we swap to pre-rendered,
    // non-template images: NSStatusItem.button silently drops
    // contentTintColor on template images in some macOS configurations,
    // so baking the colour into the image is the only reliable way to
    // guarantee the recording state actually reads.

    private func configureStatusItemImage() {
        guard let button = statusItem.button else { return }
        // The PNG lives in Contents/Resources/ of our .app bundle
        // (the canonical macOS layout — same place release.sh /
        // dev-run.sh copy it). NSImage(named:) on the main bundle
        // finds it under that path automatically; Bundle.module is
        // deliberately not used here so codesign --deep doesn't have
        // to grapple with a SwiftPM resource bundle.
        let image = NSImage(named: "presspeech-menubar")
        image?.isTemplate = true
        image?.size = NSSize(width: 18, height: 18)
        templateImage = image
        let recordingSymbol = NSImage(systemSymbolName: "record.circle.fill",
                                      accessibilityDescription: nil)
        recordingSymbol?.size = NSSize(width: 18, height: 18)
        let errorSymbol = NSImage(systemSymbolName: "exclamationmark.triangle.fill",
                                  accessibilityDescription: nil)
        errorSymbol?.size = NSSize(width: 18, height: 18)
        recordingImage = (recordingSymbol ?? image).map { tintedCopy(of: $0, with: .systemRed) }
        errorImage = (errorSymbol ?? image).map { tintedCopy(of: $0, with: .systemYellow) }
        button.image = image
        button.imagePosition = .imageOnly
        button.setAccessibilityLabel("Presspeech")
        button.setAccessibilityHelp("Open dictation controls")
        if image == nil {
            button.title = "Presspeech"
            log("statusItem: presspeech-menubar.png not in Bundle.main — text fallback")
        }
        button.toolTip = "Presspeech"
        button.setAccessibilityValue(menuBarAccessibilityValue(for: .loading))
    }

    private func tintedCopy(of source: NSImage, with color: NSColor) -> NSImage {
        let size = source.size
        let rect = NSRect(origin: .zero, size: size)
        let tinted = NSImage(size: size)
        tinted.lockFocus()
        drawTintedIcon(source, in: rect, color: color)
        tinted.unlockFocus()
        tinted.isTemplate = false
        return tinted
    }

    private func drawTintedIcon(_ source: NSImage, in rect: NSRect, color: NSColor) {
        source.draw(in: rect,
                    from: NSRect(origin: .zero, size: source.size),
                    operation: .sourceOver,
                    fraction: 1.0)
        color.set()
        rect.fill(using: .sourceAtop)
    }

    private func setMenuBarState(_ state: MenuBarState) {
        guard let button = statusItem.button else { return }
        button.setAccessibilityValue(menuBarAccessibilityValue(for: state))
        switch state {
        case .loading:
            // Subtle dim while the model compiles. nil contentTintColor
            // = system default (black/white per theme); .tertiary gives
            // a "this is here but not yet active" feel.
            button.image = templateImage
            button.contentTintColor = .tertiaryLabelColor
        case .idle:
            // Default tint — macOS auto-handles light/dark menu bar.
            button.image = templateImage
            button.contentTintColor = nil
        case .recording:
            button.image = recordingImage ?? templateImage
            button.contentTintColor = nil
        case .busy:
            // Transcribe is typically <200 ms, briefer than a perceptible
            // colour change. Leave at default; the menu's first row says
            // "Transcribing" if the user pops it open.
            button.image = templateImage
            button.contentTintColor = nil
        case .error:
            button.image = errorImage ?? templateImage
            button.contentTintColor = nil
        }
    }

    private func startRecordingLevelMeter() {
        recordingLevelTimer?.invalidate()
        recordingLevelTimer = nil
        recordingVisualLevel = 0
        lastRecordingLevelSequence = 0
        staleRecordingLevelTicks = 0
        recordingHUDPhase = 0
        setMenuBarState(.recording)
        if settings.showRecordingWaveform {
            showRecordingHUD(mode: .recording, level: 0)
        }
        let timer = Timer(timeInterval: 1.0 / 24.0,
                          target: self,
                          selector: #selector(recordingLevelTimerFired(_:)),
                          userInfo: nil,
                          repeats: true)
        timer.tolerance = 1.0 / 48.0
        RunLoop.main.add(timer, forMode: .common)
        recordingLevelTimer = timer
    }

    private func stopRecordingLevelMeter(resetImage: Bool = true) {
        recordingLevelTimer?.invalidate()
        recordingLevelTimer = nil
        recordingVisualLevel = 0
        lastRecordingLevelSequence = 0
        staleRecordingLevelTicks = 0
        recordingHUDPhase = 0
        hideRecordingHUD()
        if resetImage, isRecording {
            setMenuBarState(.recording)
        }
    }

    @objc private func recordingLevelTimerFired(_ timer: Timer) {
        guard isRecording else {
            stopRecordingLevelMeter()
            return
        }
        let snapshot = audio.latestRecordingLevelSnapshot()
        if snapshot.sequence == lastRecordingLevelSequence {
            staleRecordingLevelTicks += 1
        } else {
            lastRecordingLevelSequence = snapshot.sequence
            staleRecordingLevelTicks = 0
        }
        let unsuppressedLevel = staleRecordingLevelTicks > 8 ? 0 : snapshot.level
        let rawLevel = visibleRecordingLevel(rawLevel: unsuppressedLevel)
        let attack: Float = rawLevel > recordingVisualLevel ? 0.65 : 0.28
        recordingVisualLevel += (rawLevel - recordingVisualLevel) * attack
        if !recordingHUDAccessibilityOptions.reduceMotion {
            recordingHUDPhase += 0.34 + (CGFloat(recordingVisualLevel) * 0.42)
        }
        if settings.showRecordingWaveform {
            if recordingHUDPanel?.isVisible == true {
                if !recordingHUDAccessibilityOptions.usesStaticRecordingPresentation {
                    updateRecordingHUD(mode: .recording, level: recordingVisualLevel)
                }
            } else {
                showRecordingHUD(mode: .recording, level: recordingVisualLevel)
            }
        } else {
            hideRecordingHUD()
        }
    }

    private func showRecordingHUD(mode: RecordingHUDMode, level: Float) {
        guard settings.showRecordingWaveform else { return }
        let panel = recordingHUDPanel ?? makeRecordingHUDPanel()
        recordingHUDPanel = panel
        let shouldAnimate = !panel.isVisible
        if let view = recordingHUDView {
            view.accessibilityOptions = recordingHUDAccessibilityOptions
            view.mode = mode
            view.level = recordingHUDAccessibilityOptions.usesStaticRecordingPresentation ? 0 : level
            view.phase = recordingHUDAccessibilityOptions.usesStaticRecordingPresentation ? 0 : recordingHUDPhase
            view.showsCancelHint = mode.isRecording && settings.triggerMode == .toggle
        }
        if shouldAnimate {
            animateRecordingHUDIn(panel)
        } else {
            guard let frame = currentRecordingHUDFrame(size: RECORDING_HUD_EXPANDED_SIZE) else {
                panel.orderOut(nil)
                return
            }
            recordingHUDAnimationToken += 1
            panel.alphaValue = 1
            panel.setFrame(frame, display: true)
            panel.orderFrontRegardless()
        }
    }

    private func updateRecordingHUD(mode: RecordingHUDMode, level: Float) {
        recordingHUDView?.mode = mode
        recordingHUDView?.level = recordingHUDAccessibilityOptions.usesStaticRecordingPresentation ? 0 : level
        recordingHUDView?.phase = recordingHUDAccessibilityOptions.usesStaticRecordingPresentation ? 0 : recordingHUDPhase
        recordingHUDView?.showsCancelHint = mode.isRecording && settings.triggerMode == .toggle
    }

    private func hideRecordingHUD() {
        delayedBusyHUDWorkItem?.cancel()
        delayedBusyHUDWorkItem = nil
        recordingHUDView?.mode = .recording
        recordingHUDView?.level = 0
        recordingHUDView?.phase = 0
        recordingHUDView?.showsCancelHint = false
        guard let panel = recordingHUDPanel else { return }
        recordingHUDAnimationToken += 1
        guard panel.isVisible else {
            panel.alphaValue = 1
            if let frame = currentRecordingHUDFrame(size: RECORDING_HUD_EXPANDED_SIZE) {
                panel.setFrame(frame, display: false)
            }
            return
        }

        let token = recordingHUDAnimationToken
        let options = recordingHUDAccessibilityOptions
        guard options.animatesHUDTransitions else {
            panel.orderOut(nil)
            panel.alphaValue = 1
            if let frame = currentRecordingHUDFrame(size: RECORDING_HUD_EXPANDED_SIZE) {
                panel.setFrame(frame, display: false)
            }
            return
        }
        guard let collapsedFrame = currentRecordingHUDFrame(size: RECORDING_HUD_COLLAPSED_SIZE) else {
            panel.orderOut(nil)
            panel.alphaValue = 1
            return
        }
        NSAnimationContext.runAnimationGroup { context in
            context.duration = RECORDING_HUD_ANIMATE_OUT_SECONDS
            context.timingFunction = CAMediaTimingFunction(name: .easeIn)
            panel.animator().setFrame(collapsedFrame, display: true)
            panel.animator().alphaValue = 0
        } completionHandler: { [weak panel, weak self] in
            Task { @MainActor [weak panel, weak self] in
                guard let self, let panel else { return }
                guard self.recordingHUDAnimationToken == token else { return }
                panel.orderOut(nil)
                panel.alphaValue = 1
                if let frame = self.currentRecordingHUDFrame(size: RECORDING_HUD_EXPANDED_SIZE) {
                    panel.setFrame(frame, display: false)
                }
            }
        }
    }

    private func makeRecordingHUDPanel() -> NSPanel {
        let size = RECORDING_HUD_EXPANDED_SIZE
        let panel = NSPanel(contentRect: NSRect(origin: .zero, size: size),
                            styleMask: [.borderless, .nonactivatingPanel],
                            backing: .buffered,
                            defer: false)
        let view = RecordingHUDView(frame: NSRect(origin: .zero, size: size))
        view.accessibilityOptions = recordingHUDAccessibilityOptions
        view.autoresizingMask = [.width, .height]
        panel.contentView = view
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.hasShadow = true
        panel.ignoresMouseEvents = true
        panel.hidesOnDeactivate = false
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
        recordingHUDView = view
        return panel
    }

    private func animateRecordingHUDIn(_ panel: NSPanel) {
        recordingHUDAnimationToken += 1
        let options = recordingHUDAccessibilityOptions
        guard options.animatesHUDTransitions else {
            guard let finalFrame = currentRecordingHUDFrame(size: RECORDING_HUD_EXPANDED_SIZE) else {
                panel.orderOut(nil)
                panel.alphaValue = 1
                return
            }
            panel.alphaValue = 1
            panel.setFrame(finalFrame, display: true)
            panel.orderFrontRegardless()
            return
        }
        guard let visibleFrame = screenForRecordingHUD()?.visibleFrame,
              let startFrame = recordingHUDFrame(size: RECORDING_HUD_COLLAPSED_SIZE,
                                                  visibleFrame: visibleFrame),
              let finalFrame = recordingHUDFrame(size: RECORDING_HUD_EXPANDED_SIZE,
                                                  visibleFrame: visibleFrame) else {
            panel.orderOut(nil)
            panel.alphaValue = 1
            return
        }
        panel.alphaValue = 0.7
        panel.setFrame(startFrame, display: true)
        panel.orderFrontRegardless()
        NSAnimationContext.runAnimationGroup { context in
            context.duration = RECORDING_HUD_ANIMATE_IN_SECONDS
            context.timingFunction = CAMediaTimingFunction(name: .easeOut)
            panel.animator().setFrame(finalFrame, display: true)
            panel.animator().alphaValue = 1
        }
    }

    private func currentRecordingHUDFrame(size: NSSize) -> NSRect? {
        recordingHUDFrame(size: size,
                          visibleFrame: screenForRecordingHUD()?.visibleFrame)
    }

    private func screenForRecordingHUD() -> NSScreen? {
        let mouse = NSEvent.mouseLocation
        if let screen = NSScreen.screens.first(where: { NSMouseInRect(mouse, $0.frame, false) }) {
            return screen
        }
        return NSScreen.main ?? NSScreen.screens.first
    }

    private func scheduleDelayedBusyHUD() {
        delayedBusyHUDWorkItem?.cancel()
        guard settings.showRecordingWaveform else { return }
        let work = DispatchWorkItem { [weak self] in
            guard let self, self.isBusy, !self.isRecording, !self.isTerminating else { return }
            self.showRecordingHUD(mode: .transcribing, level: 0)
        }
        delayedBusyHUDWorkItem = work
        DispatchQueue.main.asyncAfter(deadline: .now() + RECORDING_HUD_BUSY_DELAY_SECONDS, execute: work)
    }

    private func finishBusyHUD() {
        delayedBusyHUDWorkItem?.cancel()
        delayedBusyHUDWorkItem = nil
        hideRecordingHUD()
    }

    private func clearDictationNotice() {
        let hadNotice = dictationNotice != nil
        dictationNoticeHUDWorkItem?.cancel()
        dictationNoticeHUDWorkItem = nil
        dictationNotice = nil
        if hadNotice {
            hideRecordingHUD()
        }
        statusItem?.button?.toolTip = "Presspeech"
        statusItem?.button?.setAccessibilityHelp("Open dictation controls")
    }

    private func announceForAccessibility(_ message: String) {
        NSAccessibility.post(
            element: NSApp as Any,
            notification: .announcementRequested,
            userInfo: [
                .announcement: message,
                .priority: NSAccessibilityPriorityLevel.medium.rawValue,
            ]
        )
    }

    // Visible + audible, actionable feedback when a press produced no pasted
    // text. The menu keeps the recovery instruction until the next recording,
    // while the optional waveform panel briefly shows the same instruction at
    // the user's point of attention. The sound honours the feedback toggle;
    // the icon flash always fires for users who hide the waveform or run silent.
    private func signalDictationFailure(_ notice: DictationNotice) {
        dictationNotice = notice
        statusItem?.button?.toolTip = notice.statusTitle
        statusItem?.button?.setAccessibilityHelp(notice.accessibilityValue)
        if settings.playFeedbackSounds {
            Sounds.playError()
        }
        flashErrorMenuBarIcon()
        statusItem?.button?.setAccessibilityValue(notice.accessibilityValue)
        announceForAccessibility(notice.accessibilityValue)
        if settings.showRecordingWaveform {
            showRecordingHUD(mode: .notice(notice), level: 0)
            let work = DispatchWorkItem { [weak self] in
                guard let self, !self.isRecording, !self.isBusy else { return }
                self.dictationNoticeHUDWorkItem = nil
                self.hideRecordingHUD()
            }
            dictationNoticeHUDWorkItem = work
            DispatchQueue.main.asyncAfter(deadline: .now() + DICTATION_NOTICE_HUD_SECONDS,
                                           execute: work)
        }
    }

    private func flashErrorMenuBarIcon() {
        errorFlashWorkItem?.cancel()
        setMenuBarState(.error)
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.errorFlashWorkItem = nil
            // Only clear if nothing else has claimed the icon meanwhile — a
            // new recording, an in-flight transcription, a real (non-transient)
            // error state, or termination all own it and must not be stomped.
            guard self.isReady, !self.isRecording, !self.isBusy, !self.isTerminating else { return }
            self.setMenuBarState(.idle)
            if let notice = self.dictationNotice {
                self.statusItem.button?.toolTip = notice.statusTitle
                self.statusItem.button?.setAccessibilityValue(notice.accessibilityValue)
            }
            self.rebuildMenu()
        }
        errorFlashWorkItem = work
        DispatchQueue.main.asyncAfter(deadline: .now() + DICTATION_ERROR_FLASH_SECONDS, execute: work)
    }

    // MARK: - Recording loop

    private func recordingStartBlocker() -> RecordingStartBlocker? {
        if !isReady { return .notReady }
        if isRecording { return .alreadyRecording }
        if isBusy { return .busyTranscribing }
        if isTerminating { return .terminating }
        return nil
    }

    /// Only the global event listener enters here. Menu and Voice Control
    /// dictation actions call `handlePress()` directly, so they cannot falsely
    /// mark the configured keyboard shortcut as tested.
    private func handleHotkeyPress() {
        if !hotkeyTestSucceeded {
            hotkeyTestSucceeded = true
            updateSetupChecklist()
        }
        handlePress()
    }

    private func handlePress() {
        if let blocker = recordingStartBlocker() {
            log("press ignored: \(blocker.rawValue)")
            return
        }
        // Defensive reset: every normal terminal path clears this work item,
        // but a recovered audio/runtime failure must never make a later key
        // release look like a duplicate post-release request.
        cancelPostReleaseCapture()
        let missing = missingPermissions()
        guard missing.isEmpty else {
            enterPermissionBlockedState(missing: missing, reason: "hotkey press")
            return
        }
        // Snapshot the destination before audio startup, which can block while
        // rebuilding the engine. A focus change during that work must make the
        // eventual delivery fail closed, not retarget it to the new window.
        let pasteTarget = currentDictationPasteTarget()
        cancelAudioIdleStop()
        do {
            didTouchAudioEngine = true
            if !audio.isEngineStarted {
                suppressAudioConfigurationChangesFromAppEngineUpdate()
            }
            try audio.startRecording(inputDevicePreference: settings.inputDevice)
        } catch {
            recordingPasteTarget = nil
            stopAudioEngineImmediately()
            recordStartupFailure(stage: .audioInput, error: error, reason: "hotkey press")
            return
        }
        recordingPasteTarget = pasteTarget
        isRecording = true
        clearDictationNotice()
        startRecordingLevelMeter()
        if settings.playFeedbackSounds {
            Sounds.playStart()
        }
        muteIfNeededForRecording()
        log("press: recording")

        scheduleMaxDurationAutoRelease()
        rebuildMenu()
    }

    private func handleRelease() {
        guard isRecording, !isFinishingRecording, !isTerminating else { return }
        let missing = missingPermissions()
        guard missing.isEmpty else {
            enterPermissionBlockedState(missing: missing, reason: "hotkey release")
            return
        }

        // Do not cut the microphone at the keyboard-event boundary. Speech
        // and AVAudioEngine buffers can both extend just beyond that event;
        // keep the recording lifecycle claimed until a quiet tail or the hard
        // post-release bound says it is safe to drain the accumulator.
        isFinishingRecording = true
        stopRecordingLevelMeter(resetImage: false)
        cancelMaxDurationAutoRelease()
        let releasedAt = ProcessInfo.processInfo.systemUptime
        let releaseSampleSequence = audio.latestRecordingLevelSnapshot().sequence
        log("release: retaining bounded post-release audio")
        schedulePostReleaseCaptureCheck(after: POST_RELEASE_MIN_CAPTURE_SECONDS,
                                        releasedAt: releasedAt,
                                        releaseSampleSequence: releaseSampleSequence)
        rebuildMenu()
    }

    private func schedulePostReleaseCaptureCheck(after delay: TimeInterval,
                                                 releasedAt: TimeInterval,
                                                 releaseSampleSequence: UInt64) {
        postReleaseCaptureWorkItem?.cancel()
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.postReleaseCaptureWorkItem = nil
            self.checkPostReleaseCapture(releasedAt: releasedAt,
                                         releaseSampleSequence: releaseSampleSequence)
        }
        postReleaseCaptureWorkItem = work
        DispatchQueue.main.asyncAfter(deadline: .now() + max(0, delay), execute: work)
    }

    private func checkPostReleaseCapture(releasedAt: TimeInterval,
                                         releaseSampleSequence: UInt64) {
        guard isRecording, isFinishingRecording, !isTerminating else { return }
        let elapsed = max(0, ProcessInfo.processInfo.systemUptime - releasedAt)
        let measurement = audio.postReleaseTailMeasurement()
        guard audio.isRunning else {
            // A route/runtime failure should flow through the existing empty-
            // capture recovery instead of leaving the app permanently stuck
            // in its claimed recording lifecycle.
            finishRecordingAfterPostRelease(reason: "audio stopped",
                                            elapsed: elapsed,
                                            measurement: measurement)
            return
        }
        let receivedNewSamples = measurement.sampleSequence != releaseSampleSequence
        switch postReleaseCaptureDecision(elapsed: elapsed,
                                          measurement: measurement,
                                          receivedNewSamples: receivedNewSamples) {
        case .wait(let delay):
            schedulePostReleaseCaptureCheck(after: delay,
                                            releasedAt: releasedAt,
                                            releaseSampleSequence: releaseSampleSequence)
        case .finishSilence:
            finishRecordingAfterPostRelease(reason: "silence",
                                            elapsed: elapsed,
                                            measurement: measurement)
        case .finishMaximum:
            finishRecordingAfterPostRelease(reason: "maximum",
                                            elapsed: elapsed,
                                            measurement: measurement)
        }
    }

    private func finishRecordingAfterPostRelease(reason: String,
                                                 elapsed: TimeInterval,
                                                 measurement: PostReleaseTailMeasurement) {
        guard isRecording, isFinishingRecording, !isTerminating else { return }
        postReleaseCaptureWorkItem?.cancel()
        postReleaseCaptureWorkItem = nil
        isFinishingRecording = false
        log("post-release capture \(String(format: "%.3f", elapsed)) s (\(reason); rms \(String(format: "%.4f", measurement.tailRMS)), threshold \(String(format: "%.4f", measurement.silenceThreshold)))")

        isRecording = false
        let samples = audio.endRecording()
        // Keep returning speaker audio out of the retained microphone tail.
        // endRecording closes the capture boundary before the asynchronous
        // unmute lifecycle can complete.
        unmuteIfWeMuted()
        let dur: Double
        switch recordingReleaseAction(capturedSampleCount: samples.count) {
        case .discardTooShort(let duration):
            recordingPasteTarget = nil
            dur = duration
            if let hint = noAudioCapturedHint(capturedSampleCount: samples.count) {
                log(hint)
            }
            log("release: clip too short (\(String(format: "%.2f", dur)) s), discarding")
            setMenuBarState(.idle)
            rebuildMenu()
            if !runDeferredAudioRouteRefreshIfNeeded() {
                scheduleAudioIdleStop(reason: "short clip")
            }
            return
        case .transcribe(let duration):
            dur = duration
        }
        isBusy = true
        setMenuBarState(.busy)
        scheduleDelayedBusyHUD()
        rebuildMenu()
        log("release: \(String(format: "%.2f", dur)) s captured, transcribing")
        let dictationLanguage = settings.dictationLanguage

        Task { @MainActor in
            var completionNotice: DictationNotice?
            defer { recordingPasteTarget = nil }
            do {
                let t0 = Date()
                let text = try await asr.transcribe(samples: samples,
                                                    language: dictationLanguage.fluidLanguage)
                let dt = Date().timeIntervalSince(t0)
                if !isTerminating {
                    let missing = missingPermissions()
                    guard missing.isEmpty else {
                        isBusy = false
                        finishBusyHUD()
                        enterPermissionBlockedState(missing: missing, reason: "transcription complete")
                        return
                    }
                    let processed = processedDictationText(rawTranscript: text,
                                                           corrections: settings.transcriptCorrections,
                                                           spokenFormattingCommands: settings.spokenFormattingCommands,
                                                           dictationLanguage: dictationLanguage,
                                                           removeFillerWords: settings.removeFillerWords)
                    if processed.appliedCorrectionCount > 0 {
                        log("transcript corrections applied: \(processed.appliedCorrectionCount)")
                    }
                    if processed.appliedFormattingCommandCount > 0 {
                        log("spoken formatting commands applied: \(processed.appliedFormattingCommandCount)")
                    }
                    if processed.removedFillerWordCount > 0 {
                        log("filler words removed: \(processed.removedFillerWordCount)")
                    }
                    let cleaned = processed.text
                    log("\(String(format: "%.2f", dur)) s audio → \(String(format: "%.2f", dt)) s → \(cleaned.count) chars")
                    if cleaned.isEmpty {
                        completionNotice = dictationCompletionNotice(processedText: cleaned,
                                                                      insertionOutcome: nil,
                                                                      keepsRecentTranscripts: settings.recentTranscriptLimit.count > 0)
                    } else {
                        let missing = missingPermissions()
                        guard missing.isEmpty else {
                            isBusy = false
                            finishBusyHUD()
                            enterPermissionBlockedState(missing: missing, reason: "paste")
                            return
                        }
                        let deliveredText = pastedText(from: cleaned,
                                                       suffix: settings.pasteSuffix)
                        let insertionOutcome: TextInsertionOutcome
                        if let expectedTarget = recordingPasteTarget {
                            insertionOutcome = TextInserter.insert(
                                deliveredText,
                                restoreClipboard: settings.restoreClipboardAfterPaste,
                                expectedTarget: expectedTarget
                            )
                        } else {
                            insertionOutcome = TextInserter.copyWithoutPasting(deliveredText)
                        }
                        switch insertionOutcome {
                        case .inserted:
                            if settings.playFeedbackSounds {
                                Sounds.playDone()
                            }
                        case .copiedWithoutPasting:
                            log("paste skipped; focused window changed; transcript copied")
                        case .failed:
                            log("text insertion failed")
                        }
                        completionNotice = dictationCompletionNotice(
                            processedText: cleaned,
                            insertionOutcome: insertionOutcome,
                            keepsRecentTranscripts: settings.recentTranscriptLimit.count > 0
                        )
                        addToHistory(cleaned)
                    }
                }
            } catch {
                log("transcribe failed: \(error)")
                completionNotice = .transcriptionFailed
            }
            isBusy = false
            finishBusyHUD()
            if let completionNotice, !isTerminating {
                signalDictationFailure(completionNotice)
            } else {
                setMenuBarState(.idle)
            }
            rebuildMenu()
            let didRestartAudio = runDeferredAudioRouteRefreshIfNeeded()
            recoverRuntimeAfterWakeIfNeeded(reason: "transcription finished after wake")
            if !didRestartAudio {
                scheduleAudioIdleStop(reason: "recording finished")
            }
        }
    }

    private func cancelActiveRecording(reason: String, runDeferredRefresh: Bool = true) {
        cancelPostReleaseCapture()
        guard isRecording || audio.isRunning else {
            recordingPasteTarget = nil
            hotkey.resetToggleState()
            return
        }

        recordingPasteTarget = nil
        cancelMaxDurationAutoRelease()
        _ = audio.endRecording()
        isRecording = false
        stopRecordingLevelMeter()
        hotkey.resetToggleState()
        unmuteIfWeMuted()
        setMenuBarState(.idle)
        rebuildMenu()
        log("recording canceled (\(reason))")
        let didRestartAudio = runDeferredRefresh
            ? runDeferredAudioRouteRefreshIfNeeded()
            : false
        if !didRestartAudio {
            scheduleAudioIdleStop(reason: reason)
        }
    }

    // Quit cancels any in-flight recording instead of releasing it:
    // release intentionally starts transcription/paste/history work,
    // while termination only needs to discard audio and restore mute.
    private func cancelRecordingForTermination() {
        cancelMaxDurationAutoRelease()
        cancelPostReleaseCapture()
        hotkey.onPress = nil
        hotkey.onRelease = nil
        hotkey.onCancel = nil
        hotkey.isRecordingActive = nil
        hotkey.recordingStartBlocker = nil
        hotkey.stop()

        let hadActiveRecording = isRecording || audio.isRunning
        let hadMute = systemAudioMutePhase != .idle
        if hadActiveRecording {
            _ = audio.endRecording()
        }
        stopRecordingLevelMeter()
        stopAudioEngineImmediately()
        isRecording = false
        isBusy = false
        hotkey.resetToggleState()
        unmuteIfWeMuted()

        if hadActiveRecording || hadMute {
            log("terminate: active recording canceled")
        }
    }

    // Trade-off: the mute is asynchronous relative to recording
    // start. Audio capture is armed immediately when the engine opens
    // on press, while the probe + mute land a few milliseconds later,
    // so a sliver of system audio can bleed into the start of the clip.
    // That beats the alternative — the old synchronous AppleScript
    // calls ran behind the session-wide event tap on the main run
    // loop, stalling every keystroke system-wide (and risking macOS
    // disabling the tap after a >1 s stall).
    private func muteIfNeededForRecording() {
        guard settings.muteWhileRecording else { return }
        guard systemAudioMutePhase == .idle else {
            // A previous recording's lifecycle is still settling
            // (rapid press cycles). Skipping the mute for this
            // recording is safe in the never-stuck sense, but the
            // cost is not always "a few ms": if the previous probe is
            // still in flight when this press lands, it stands down
            // to .idle and THIS recording runs unmuted for its whole
            // duration. Accepted: it needs a press/release/press
            // faster than one AppleScript round-trip, and the
            // alternative (queueing nested mute lifecycles) is far
            // more complex than the failure it prevents.
            log("output mute skipped: previous mute lifecycle still settling")
            return
        }
        systemAudioMutePhase = .probing
        systemAudioUnmuteRequested = false
        // Only mute if we wouldn't be stomping a user-set mute.
        SystemAudio.mutedStateAsync { [weak self] mutedState in
            self?.continueMuteAfterProbe(mutedState: mutedState)
        }
    }

    private func continueMuteAfterProbe(mutedState: Bool?) {
        guard systemAudioMutePhase == .probing else {
            log("output mute probe completion ignored: unexpected phase")
            return
        }
        switch systemAudioMuteProbeDecision(mutedState: mutedState,
                                            unmuteAlreadyRequested: systemAudioUnmuteRequested) {
        case .standDown:
            systemAudioMutePhase = .idle
            systemAudioUnmuteRequested = false
            return
        case .armRecoveryAndMute:
            break
        }

        // Crash-recovery invariant: the marker + watchdog must exist
        // BEFORE the mute command can execute, so a crash at any
        // point after the mute leaves a recovery path. Both are armed
        // here on the main actor; the mute is only enqueued after
        // they exist, and SystemAudio's serial queue preserves that
        // order.
        do {
            try writeSystemAudioMuteMarker()
            try startSystemAudioMuteWatchdog()
        } catch {
            removeSystemAudioMuteMarker()
            stopSystemAudioMuteWatchdog()
            systemAudioMutePhase = .idle
            systemAudioUnmuteRequested = false
            log("output mute skipped: recovery watchdog unavailable (\(error.localizedDescription))")
            return
        }
        systemAudioMutePhase = .muting
        SystemAudio.muteAsync { [weak self] outcome in
            self?.finishMuteCommand(outcome: outcome)
        }
    }

    private func finishMuteCommand(outcome: SystemAudioMuteCommandOutcome) {
        guard systemAudioMutePhase == .muting else {
            log("output mute completion ignored: unexpected phase")
            return
        }
        switch systemAudioMuteCommandDecision(outcome: outcome,
                                              unmuteAlreadyRequested: systemAudioUnmuteRequested) {
        case .disarmRecovery:
            removeSystemAudioMuteMarker()
            stopSystemAudioMuteWatchdog()
            systemAudioMutePhase = .idle
            systemAudioUnmuteRequested = false
            log("output mute failed")
        case .stayMuted:
            systemAudioMutePhase = .muted
            log(outcome == .assumedMuted
                ? "output muted (verification failed; assuming muted, recovery stays armed)"
                : "output muted")
        case .beginUnmute:
            // The recording ended while the mute command ran.
            systemAudioUnmuteRequested = false
            beginSystemAudioUnmute()
        }
    }

    private func unmuteIfWeMuted() {
        switch systemAudioUnmuteRequestDecision(phase: systemAudioMutePhase) {
        case .nothingToDo:
            return
        case .deferUntilCommandSettles:
            systemAudioUnmuteRequested = true
        case .beginUnmute:
            beginSystemAudioUnmute()
        }
    }

    private func beginSystemAudioUnmute() {
        systemAudioMutePhase = .unmuting
        SystemAudio.unmuteAsync { [weak self] unmuted in
            self?.finishUnmuteCommand(unmuted: unmuted)
        }
    }

    private func finishUnmuteCommand(unmuted: Bool) {
        guard systemAudioMutePhase == .unmuting else {
            log("output unmute completion ignored: unexpected phase")
            return
        }
        if unmuted {
            systemAudioMutePhase = .idle
            systemAudioUnmuteRequested = false
            removeSystemAudioMuteMarker()
            stopSystemAudioMuteWatchdog()
            log("output unmuted")
        } else {
            // Stay "muted": the marker + watchdog remain armed, the
            // next recording's release retries the unmute, and the
            // watchdog recovers if we exit first.
            systemAudioMutePhase = .muted
            log("output unmute failed; crash-recovery marker left in place")
        }
    }

    private func startSystemAudioMuteWatchdog() throws {
        stopSystemAudioMuteWatchdog()
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/bin/sh")
        proc.arguments = [
            "-c",
            systemAudioMuteWatchdogScript(),
            "presspeech-audio-watchdog",
            "\(getpid())",
            systemAudioMuteMarkerURL().path,
        ]
        proc.environment = systemToolProcessEnvironment()
        try proc.run()
        systemAudioMuteWatchdog = proc
    }

    private func stopSystemAudioMuteWatchdog() {
        guard let proc = systemAudioMuteWatchdog else { return }
        if proc.isRunning {
            proc.terminate()
        }
        systemAudioMuteWatchdog = nil
    }

    // Uses the synchronous SystemAudio calls deliberately: this runs
    // once from applicationDidFinishLaunching, before the event tap
    // exists, so a main-thread AppleScript round-trip cannot stall
    // keystrokes here.
    private func recoverStaleSystemAudioMuteIfNeeded() {
        let marker = systemAudioMuteMarkerURL()
        guard FileManager.default.fileExists(atPath: marker.path) else { return }

        if let text = try? String(contentsOf: marker, encoding: .utf8),
           let pid = systemAudioMuteMarkerProcessID(from: text),
           pid != getpid(),
           Darwin.kill(pid, 0) == 0 {
            log("output mute recovery deferred: marker belongs to active process \(pid)")
            return
        }

        if SystemAudio.isMuted() {
            if SystemAudio.unmute() {
                log("output unmuted after interrupted recording")
            } else {
                log("output unmute after interrupted recording failed")
            }
        } else {
            log("stale output mute marker removed")
        }
        removeSystemAudioMuteMarker(at: marker)
    }

    private func scheduleMaxDurationAutoRelease() {
        let maximumDuration = settings.maxRecordingSeconds
        let work = DispatchWorkItem { [weak self] in
            guard let self, self.isRecording else { return }
            log("max recording duration reached, releasing")
            self.hotkey.resetToggleState()
            self.handleRelease()
        }
        maxDurationWorkItem = work
        DispatchQueue.main.asyncAfter(deadline: .now() + maximumDuration, execute: work)
    }

    private func cancelMaxDurationAutoRelease() {
        maxDurationWorkItem?.cancel()
        maxDurationWorkItem = nil
    }

    private func cancelPostReleaseCapture() {
        postReleaseCaptureWorkItem?.cancel()
        postReleaseCaptureWorkItem = nil
        isFinishingRecording = false
    }

    // MARK: - History

    private func addToHistory(_ text: String) {
        let next = limitedRecentTranscripts([text] + history,
                                            limit: settings.recentTranscriptLimit)
        guard next != history else { return }
        history = next
        rebuildMenu()
    }

    private func applyRecentTranscriptLimit() {
        let next = limitedRecentTranscripts(history, limit: settings.recentTranscriptLimit)
        guard next.count != history.count else { return }
        let removed = history.count - next.count
        history = next
        log("recent transcript history trimmed by \(removed) entr\(removed == 1 ? "y" : "ies")")
    }

    /// 60-char preview with ellipsis. Newlines collapsed so a multi-
    /// line transcript still renders as one menu row.
    private func previewLine(for text: String) -> String {
        let flat = text.replacingOccurrences(of: "\n", with: " ")
        return flat.count > 60 ? String(flat.prefix(60)) + "…" : flat
    }

    @objc private func historyClicked(_ sender: NSMenuItem) {
        guard let s = sender.representedObject as? String else { return }
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString(s, forType: .string)
        log("history copied to clipboard (\(s.count) chars)")
        clearDictationNotice()
        if isReady, !isRecording, !isBusy, !isTerminating {
            setMenuBarState(.idle)
        }
        rebuildMenu()
    }

    @objc private func clearHistoryClicked(_ sender: NSMenuItem) {
        guard !history.isEmpty else { return }
        let count = history.count
        history.removeAll()
        log("history cleared (\(count) entries)")
        if dictationNotice?.requiresInMemoryHistory == true {
            clearDictationNotice()
            if isReady, !isRecording, !isBusy, !isTerminating {
                setMenuBarState(.idle)
            }
        }
        rebuildMenu()
    }

    @objc private func quitClicked(_ sender: NSMenuItem) {
        NSApp.terminate(self)
    }

    @objc private func cancelRecordingClicked(_ sender: NSMenuItem) {
        cancelActiveRecording(reason: "menu")
    }

    @objc private func dictationMenuControlClicked(_ sender: NSMenuItem) {
        guard let action = DictationMenuControlAction(rawValue: sender.tag) else { return }
        switch action {
        case .stop:
            guard isRecording else { return }
            hotkey.resetToggleState()
            handleRelease()
        case .start:
            guard !isRecording else { return }
            handlePress()
            if isRecording {
                hotkey.recordingDidStartOutsideHotkey()
            }
        }
    }

    @objc private func copyDiagnosticsClicked(_ sender: NSMenuItem) {
        copyDiagnosticsToClipboard()
    }

    private func copyDiagnosticsToClipboard() {
        let text = diagnosticsText()
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString(text, forType: .string)
        log("diagnostics copied to clipboard")
    }

    private func openDiagnosticLog() {
        NSWorkspace.shared.open(Logger.shared.fileURL)
        log("diagnostics log opened")
    }

    private func showPreviousExitNoticeIfAppropriate() {
        guard !isTerminating else { return }
        showAppForModal()
        let alert = NSAlert()
        alert.alertStyle = .informational
        alert.messageText = "Presspeech Reopened After an Unexpected Exit"
        alert.informativeText = """
            Presspeech appears to have exited last time without a normal shutdown. Nothing was sent anywhere.

            You can copy a privacy-safe diagnostics report or open the local log if you want to file an issue.
            """
        alert.addButton(withTitle: "Copy Diagnostics")
        alert.addButton(withTitle: "Open Log")
        alert.addButton(withTitle: "Not Now")

        let response = alert.runModal()
        if response == .alertFirstButtonReturn {
            copyDiagnosticsToClipboard()
        } else if response == .alertSecondButtonReturn {
            openDiagnosticLog()
        }
    }

    @objc private func saveDiagnosticsClicked(_ sender: NSMenuItem) {
        showAppForModal()
        let panel = NSSavePanel()
        panel.title = "Save Diagnostics"
        panel.message = "Save a privacy-safe diagnostics report for a GitHub issue."
        panel.prompt = "Save"
        panel.nameFieldStringValue = "Presspeech Diagnostics.txt"
        panel.allowedContentTypes = [.plainText]
        panel.canCreateDirectories = true
        guard panel.runModal() == .OK, let url = panel.url else { return }

        do {
            try diagnosticsText().write(to: url, atomically: true, encoding: .utf8)
            log("diagnostics saved to \(privacySafeLogPath(url))")
        } catch {
            showDiagnosticsSaveError(error)
        }
    }

    @objc private func reportProblemClicked(_ sender: NSMenuItem) {
        NSWorkspace.shared.open(GITHUB_BUG_REPORT_PAGE)
    }

    @objc private func suggestImprovementClicked(_ sender: NSMenuItem) {
        NSWorkspace.shared.open(GITHUB_FEATURE_REQUEST_PAGE)
    }

    private func showDiagnosticsSaveError(_ error: Error) {
        log("diagnostics save failed: \(error.localizedDescription)")
        showAppForModal()
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "Diagnostics couldn't be saved"
        alert.informativeText = error.localizedDescription
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    // MARK: - Menu

    /// This app has no main nib, so AppKit cannot synthesize the application
    /// menu that a regular Dock app normally gets. The menu stays installed
    /// while Presspeech is an accessory app (and therefore remains hidden),
    /// then becomes available immediately if Show in Dock is enabled.
    ///
    /// Keep the actual settings controls in the status-menu interface: this
    /// command provides the standard macOS discovery path and Command-comma
    /// shortcut without introducing a second, divergent preferences UI.
    private func installApplicationMenu() {
        let mainMenu = NSMenu(title: "Main Menu")
        let applicationItem = NSMenuItem(title: "Presspeech", action: nil, keyEquivalent: "")
        let applicationMenu = NSMenu(title: "Presspeech")

        let about = NSMenuItem(title: "About Presspeech",
                               action: #selector(showAboutClicked(_:)),
                               keyEquivalent: "")
        about.target = self
        applicationMenu.addItem(about)
        applicationMenu.addItem(.separator())

        let settingsItem = NSMenuItem(title: "Settings…",
                                      action: #selector(showSettingsFromApplicationMenu(_:)),
                                      keyEquivalent: ",")
        settingsItem.target = self
        applicationMenu.addItem(settingsItem)
        applicationMenu.addItem(.separator())

        let servicesMenu = NSMenu(title: "Services")
        let servicesItem = NSMenuItem(title: "Services", action: nil, keyEquivalent: "")
        servicesItem.submenu = servicesMenu
        applicationMenu.addItem(servicesItem)
        NSApp.servicesMenu = servicesMenu

        applicationMenu.addItem(.separator())
        let hide = NSMenuItem(title: "Hide Presspeech",
                              action: #selector(NSApplication.hide(_:)),
                              keyEquivalent: "h")
        hide.target = NSApp
        applicationMenu.addItem(hide)

        let hideOthers = NSMenuItem(title: "Hide Others",
                                    action: #selector(NSApplication.hideOtherApplications(_:)),
                                    keyEquivalent: "h")
        hideOthers.target = NSApp
        hideOthers.keyEquivalentModifierMask = [.command, .option]
        applicationMenu.addItem(hideOthers)

        let showAll = NSMenuItem(title: "Show All",
                                 action: #selector(NSApplication.unhideAllApplications(_:)),
                                 keyEquivalent: "")
        showAll.target = NSApp
        applicationMenu.addItem(showAll)

        applicationMenu.addItem(.separator())
        let quit = NSMenuItem(title: "Quit Presspeech",
                              action: #selector(quitClicked(_:)),
                              keyEquivalent: "q")
        quit.target = self
        applicationMenu.addItem(quit)

        applicationItem.submenu = applicationMenu
        mainMenu.addItem(applicationItem)

        // NSTextView and NSTextField rely on the responder chain for their
        // standard editing commands. In a nib-backed app AppKit supplies this
        // menu automatically; without it, our scratchpad, dictionary editor,
        // and search field have no visible native command surface for
        // Undo/Cut/Copy/Paste/Select All when Show in Dock is enabled.
        let editItem = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(responderChainMenuItem(title: "Undo",
                                                action: Selector(("undo:")),
                                                keyEquivalent: "z"))
        let redo = responderChainMenuItem(title: "Redo",
                                          action: Selector(("redo:")),
                                          keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        editMenu.addItem(redo)
        editMenu.addItem(.separator())
        editMenu.addItem(responderChainMenuItem(title: "Cut",
                                                action: Selector(("cut:")),
                                                keyEquivalent: "x"))
        editMenu.addItem(responderChainMenuItem(title: "Copy",
                                                action: Selector(("copy:")),
                                                keyEquivalent: "c"))
        editMenu.addItem(responderChainMenuItem(title: "Paste",
                                                action: Selector(("paste:")),
                                                keyEquivalent: "v"))
        editMenu.addItem(.separator())
        editMenu.addItem(responderChainMenuItem(title: "Select All",
                                                action: Selector(("selectAll:")),
                                                keyEquivalent: "a"))
        editItem.submenu = editMenu
        mainMenu.addItem(editItem)

        // The app can own several independent utility windows. Register a
        // Window menu so AppKit can keep its window list current and route the
        // conventional close/minimize/zoom commands to whichever one is key.
        let windowItem = NSMenuItem(title: "Window", action: nil, keyEquivalent: "")
        let windowMenu = NSMenu(title: "Window")
        windowMenu.addItem(responderChainMenuItem(title: "Close Window",
                                                  action: #selector(NSWindow.performClose(_:)),
                                                  keyEquivalent: "w"))
        windowMenu.addItem(.separator())
        windowMenu.addItem(responderChainMenuItem(title: "Minimize",
                                                  action: #selector(NSWindow.performMiniaturize(_:)),
                                                  keyEquivalent: "m"))
        windowMenu.addItem(responderChainMenuItem(title: "Zoom",
                                                  action: #selector(NSWindow.performZoom(_:)),
                                                  keyEquivalent: ""))
        windowMenu.addItem(.separator())
        windowMenu.addItem(responderChainMenuItem(title: "Bring All to Front",
                                                  action: #selector(NSApplication.arrangeInFront(_:)),
                                                  keyEquivalent: ""))
        windowItem.submenu = windowMenu
        mainMenu.addItem(windowItem)
        NSApp.windowsMenu = windowMenu

        NSApp.mainMenu = mainMenu
    }

    /// A menu item whose target is intentionally nil, allowing AppKit to find
    /// the active text editor, window, or application through the responder
    /// chain.
    private func responderChainMenuItem(title: String,
                                        action: Selector,
                                        keyEquivalent: String) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: keyEquivalent)
        item.target = nil
        if !keyEquivalent.isEmpty {
            item.keyEquivalentModifierMask = [.command]
        }
        return item
    }

    /// Present a fresh copy of the existing settings hierarchy so values and
    /// enabled states always match the status menu. Prefer the status item as
    /// the visual anchor, but retain a screen-coordinate fallback for a menu
    /// bar whose items are temporarily hidden because it is full.
    @objc private func showSettingsFromApplicationMenu(_ sender: NSMenuItem) {
        guard !isTerminating,
              let settingsMenu = buildSettingsItem().submenu else { return }

        if let button = statusItem.button,
           button.window?.isVisible == true {
            settingsMenu.popUp(positioning: nil,
                               at: NSPoint(x: button.bounds.minX,
                                           y: button.bounds.minY),
                               in: button)
        } else if let contentView = NSApp.keyWindow?.contentView {
            settingsMenu.popUp(positioning: nil,
                               at: NSPoint(x: contentView.bounds.minX + 20,
                                           y: contentView.bounds.maxY - 20),
                               in: contentView)
        } else {
            settingsMenu.popUp(positioning: nil,
                               at: NSEvent.mouseLocation,
                               in: nil)
        }
    }

    private func rebuildMenu() {
        let menu = buildMenu()
        menu.delegate = self
        statusItem.menu = menu
    }

    /// Refresh dynamic menu state immediately before AppKit presents it.
    func menuWillOpen(_ menu: NSMenu) {
        if menu === correctionsManagerContextMenu,
           let table = correctionsManagerTableView {
            // `clickedRow` can describe an old pointer event when a shortcut
            // menu is opened from the keyboard or VoiceOver. Only use it for
            // the mouse-down event that is currently opening this menu.
            let clickedRow: Int
            switch NSApp.currentEvent?.type {
            case .leftMouseDown, .rightMouseDown, .otherMouseDown:
                clickedRow = table.clickedRow
            default:
                clickedRow = -1
            }
            let selection = correctionContextSelection(clickedRow: clickedRow,
                                                       selectedRows: table.selectedRowIndexes,
                                                       rowCount: table.numberOfRows)
            table.selectRowIndexes(selection, byExtendingSelection: false)
            let selectedCount = selectedManagedCorrectionIndices().count
            if let edit = menu.items.first(where: {
                $0.identifier?.rawValue == "correction-context-edit"
            }) {
                edit.isEnabled = selectedCount == 1
            }
            if let delete = menu.items.first(where: {
                $0.identifier?.rawValue == "correction-context-delete"
            }) {
                delete.title = selectedCount > 1 ? "Delete \(selectedCount) Items" : "Delete"
                delete.isEnabled = selectedCount > 0
            }
            return
        }

        // Login-item approval can be changed outside Presspeech. Refresh its
        // cached menu item so returning from System Settings never leaves a
        // stale mixed/on state behind.
        guard menu === statusItem.menu,
              let item = menuItem(with: Self.launchAtLoginMenuItemIdentifier, in: menu) else {
            return
        }
        configureLaunchAtLoginMenuItem(item, status: SMAppService.mainApp.status)
    }

    private func menuItem(with identifier: NSUserInterfaceItemIdentifier,
                          in menu: NSMenu) -> NSMenuItem? {
        for item in menu.items {
            if item.identifier == identifier { return item }
            if let submenu = item.submenu,
               let match = menuItem(with: identifier, in: submenu) {
                return match
            }
        }
        return nil
    }

    private func buildDockMenu() -> NSMenu {
        let menu = NSMenu()
        menu.autoenablesItems = false

        let status = NSMenuItem(title: menuStatusTitle(), action: nil, keyEquivalent: "")
        status.isEnabled = false
        if let failure = startupFailure {
            status.toolTip = failure.detail
        }
        menu.addItem(status)
        menu.addItem(.separator())

        let controlState = dictationMenuControlState(
            isReady: isReady,
            isRecording: isRecording,
            isBusy: isBusy,
            isTerminating: isTerminating,
            hasMissingPermissions: !missingPermissions().isEmpty
        )
        let dictationControl = NSMenuItem(title: controlState.title,
                                          action: #selector(dictationMenuControlClicked(_:)),
                                          keyEquivalent: "")
        dictationControl.target = self
        dictationControl.tag = controlState.action.rawValue
        dictationControl.isEnabled = controlState.isEnabled
        dictationControl.toolTip = controlState.help
        menu.addItem(dictationControl)

        if isRecording {
            let cancel = NSMenuItem(title: "Cancel Recording",
                                    action: #selector(cancelRecordingClicked(_:)),
                                    keyEquivalent: "")
            cancel.target = self
            menu.addItem(cancel)
        }
        if let newest = history.first {
            let copyLast = NSMenuItem(title: "Copy Last Transcript",
                                      action: #selector(historyClicked(_:)),
                                      keyEquivalent: "")
            copyLast.target = self
            copyLast.representedObject = newest
            menu.addItem(copyLast)
        }

        menu.addItem(.separator())
        menu.addItem(buildSettingsItem())
        menu.addItem(buildSupportItem())
        // AppKit appends the standard Dock commands, including Quit.
        return menu
    }

    private func buildMenu() -> NSMenu {
        let menu = NSMenu()
        menu.autoenablesItems = false

        // Status row.
        let statusTitle = menuStatusTitle()
        let status = NSMenuItem(title: statusTitle, action: nil, keyEquivalent: "")
        status.isEnabled = false
        if let failure = startupFailure {
            status.toolTip = failure.detail
        }
        menu.addItem(status)
        if shouldShowSpeechModelProgressRow {
            menu.addItem(buildSpeechModelProgressItem())
        }

        menu.addItem(.separator())

        let controlState = dictationMenuControlState(
            isReady: isReady,
            isRecording: isRecording,
            isBusy: isBusy,
            isTerminating: isTerminating,
            hasMissingPermissions: !missingPermissions().isEmpty
        )
        let dictationControl = NSMenuItem(title: controlState.title,
                                          action: #selector(dictationMenuControlClicked(_:)),
                                          keyEquivalent: "")
        dictationControl.target = self
        dictationControl.tag = controlState.action.rawValue
        dictationControl.isEnabled = controlState.isEnabled
        dictationControl.toolTip = controlState.help
        menu.addItem(dictationControl)

        if isRecording {
            let cancel = NSMenuItem(title: "Cancel Recording",
                                    action: #selector(cancelRecordingClicked(_:)),
                                    keyEquivalent: "")
            cancel.target = self
            menu.addItem(cancel)
        }
        menu.addItem(.separator())

        if let failure = startupFailure {
            let retry = NSMenuItem(title: failure.retryTitle,
                                   action: #selector(retryStartupClicked(_:)),
                                   keyEquivalent: "")
            retry.target = self
            retry.toolTip = failure.detail
            retry.isEnabled = startupTask == nil
            menu.addItem(retry)
            menu.addItem(.separator())
        }

        // Update submenu (lazy — only present when an update exists).
        if let release = pendingUpdate, !settings.skippedVersions.contains(release.version) {
            menu.addItem(buildUpdateItem(for: release))
            menu.addItem(.separator())
        }

        // Permission rows — visible only while something is missing.
        var addedPermRow = false
        for p in Permission.allCases where !Permissions.isGranted(p) {
            menu.addItem(buildPermissionItem(p))
            addedPermRow = true
        }
        if addedPermRow { menu.addItem(.separator()) }

        // History: keep one-click access to the last transcript, but
        // hide transcript preview text inside the submenu so the menu
        // stays stable even after long dictations.
        if let newest = history.first {
            let inline = NSMenuItem(title: "Copy Last Transcript",
                                    action: #selector(historyClicked(_:)),
                                    keyEquivalent: "")
            inline.target = self
            inline.representedObject = newest
            inline.toolTip = newest
            menu.addItem(inline)

            menu.addItem(buildRecentTranscriptsItem())

            menu.addItem(.separator())
        }

        // Settings submenu.
        menu.addItem(buildSettingsItem())
        menu.addItem(buildSupportItem())
        menu.addItem(.separator())

        // Route through our own selector rather than `NSApp.terminate(_:)`
        // directly. macOS auto-decorates items whose action is the
        // system terminate: selector with a destructive-action glyph
        // (visible in the state-column slot), which is the *only* item
        // in the menu that gets such an indicator — every other row
        // sits flush against the left edge. The wrapper produces the
        // identical behaviour with no auto-glyph.
        let quit = NSMenuItem(title: "Quit Presspeech",
                              action: #selector(quitClicked(_:)),
                              keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
        return menu
    }

    private func buildRecentTranscriptsItem() -> NSMenuItem {
        let parent = NSMenuItem(title: "Recent Transcripts", action: nil, keyEquivalent: "")
        let sub = NSMenu()
        sub.autoenablesItems = false

        for entry in history {
            let item = NSMenuItem(title: previewLine(for: entry),
                                  action: #selector(historyClicked(_:)),
                                  keyEquivalent: "")
            item.target = self
            item.representedObject = entry
            item.toolTip = entry
            sub.addItem(item)
        }

        sub.addItem(.separator())

        let clear = NSMenuItem(title: "Clear Recent Transcripts",
                               action: #selector(clearHistoryClicked(_:)),
                               keyEquivalent: "")
        clear.target = self
        sub.addItem(clear)

        parent.submenu = sub
        return parent
    }

    private func buildSupportItem() -> NSMenuItem {
        let parent = NSMenuItem(title: "Support", action: nil, keyEquivalent: "")
        let sub = NSMenu()
        sub.autoenablesItems = false

        let setup = NSMenuItem(title: "Setup Checklist…",
                               action: #selector(showSetupChecklistClicked(_:)),
                               keyEquivalent: "")
        setup.target = self
        sub.addItem(setup)

        let tryDictation = NSMenuItem(title: "Try Dictation…",
                                      action: #selector(showDictationScratchpadClicked(_:)),
                                      keyEquivalent: "")
        tryDictation.target = self
        tryDictation.isEnabled = setupChecklistIsComplete && !isTerminating
        tryDictation.toolTip = setupChecklistIsComplete
            ? "Open a private scratchpad and try the dictation hotkey."
            : "Finish Setup Checklist before trying dictation."
        sub.addItem(tryDictation)

        sub.addItem(.separator())

        let checkUpdates = NSMenuItem(title: isCheckingForUpdates ? "Checking for Updates…" : "Check for Updates…",
                                      action: #selector(checkForUpdatesClicked(_:)),
                                      keyEquivalent: "")
        checkUpdates.target = self
        checkUpdates.isEnabled = !isCheckingForUpdates && !isTerminating
        sub.addItem(checkUpdates)

        sub.addItem(.separator())

        let about = NSMenuItem(title: "About Presspeech",
                               action: #selector(showAboutClicked(_:)),
                               keyEquivalent: "")
        about.target = self
        sub.addItem(about)

        sub.addItem(.separator())

        let diagnostics = NSMenuItem(title: "Copy Diagnostics",
                                     action: #selector(copyDiagnosticsClicked(_:)),
                                     keyEquivalent: "")
        diagnostics.target = self
        sub.addItem(diagnostics)

        let saveDiagnostics = NSMenuItem(title: "Save Diagnostics…",
                                         action: #selector(saveDiagnosticsClicked(_:)),
                                         keyEquivalent: "")
        saveDiagnostics.target = self
        sub.addItem(saveDiagnostics)

        let reportProblem = NSMenuItem(title: "Report a Problem…",
                                       action: #selector(reportProblemClicked(_:)),
                                       keyEquivalent: "")
        reportProblem.target = self
        reportProblem.toolTip = "Open the privacy-aware bug report form on GitHub."
        sub.addItem(reportProblem)

        let suggestImprovement = NSMenuItem(title: "Suggest an Improvement…",
                                            action: #selector(suggestImprovementClicked(_:)),
                                            keyEquivalent: "")
        suggestImprovement.target = self
        suggestImprovement.toolTip = "Open the focused feature request form on GitHub."
        sub.addItem(suggestImprovement)

        let resetModel = NSMenuItem(title: isResettingSpeechModelCache ? "Resetting Speech Model Cache…" : "Reset Speech Model Cache…",
                                    action: #selector(resetSpeechModelCacheClicked(_:)),
                                    keyEquivalent: "")
        resetModel.target = self
        resetModel.isEnabled = !isRecording
            && !isBusy
            && !isTerminating
            && startupTask == nil
            && !isResettingSpeechModelCache
            && !isSwitchingSpeechModel
        resetModel.toolTip = "Delete the speech model cache and download a fresh verified copy."
        sub.addItem(resetModel)

        parent.submenu = sub
        return parent
    }

    private var shouldShowSpeechModelProgressRow: Bool {
        startupFailure == nil
            && ((startupTask != nil && !isSpeechModelReady)
                || isSwitchingSpeechModel
                || isResettingSpeechModelCache)
    }

    private func buildSpeechModelProgressItem() -> NSMenuItem {
        let item = NSMenuItem()
        let view = NSView(frame: NSRect(x: 0, y: 0, width: 260, height: 24))
        let progress = NSProgressIndicator(frame: NSRect(x: 14, y: 7, width: 232, height: 10))
        progress.style = .bar
        progress.controlSize = .small
        progress.minValue = 0
        progress.maxValue = 1
        progress.usesThreadedAnimation = true
        progress.toolTip = startupStatusTitle

        if let speechModelStartupProgressFraction {
            progress.isIndeterminate = false
            progress.doubleValue = speechModelStartupProgressFraction
        } else {
            progress.isIndeterminate = true
            progress.startAnimation(nil)
        }

        view.addSubview(progress)
        item.view = view
        return item
    }

    private func menuStatusTitle() -> String {
        if isRecording {
            return "Recording..."
        }
        if isBusy {
            return "Transcribing..."
        }
        if let dictationNotice {
            return dictationNotice.statusTitle
        }
        if isReady {
            let hk = hotkey.hotkey.name
            let verb = settings.triggerMode == .hold ? "Hold" : "Press"
            return "\(verb) \(hk) to dictate"
        }
        if let failure = startupFailure {
            return failure.statusTitle
        }
        if startupTask != nil || isRestartingAudioInput || isSwitchingSpeechModel {
            return startupStatusTitle
        }
        if !missingPermissions().isEmpty {
            return "Grant permissions to finish setup"
        }
        if isCoreRuntimeReady {
            return "Starting hotkey listener…"
        }
        return "Presspeech is not ready"
    }

    private func diagnosticsText() -> String {
        let generated = ISO8601DateFormatter().string(from: Date())
        let bundlePath = Bundle.main.bundlePath
        let installKind: String
        if bundlePath == "/Applications/Presspeech.app" {
            installKind = "Applications app"
        } else if bundlePath == "/tmp/Presspeech-dev.app" {
            installKind = "signed dev app"
        } else {
            installKind = "other"
        }

        let devices = availableAudioInputDevices()
        let savedInput = settings.inputDevice.trimmingCharacters(in: .whitespacesAndNewlines)
        let selectedInput = audioInputDevice(matching: savedInput, in: devices)
        let inputLabel: String
        if savedInput.isEmpty || isDefaultAggregateAudioInputPreference(savedInput) {
            inputLabel = "System default"
        } else if let selectedInput {
            inputLabel = "\(selectedInput.name) (available)"
        } else {
            inputLabel = "Saved device unavailable"
        }

        let startupText: String
        if let failure = startupFailure {
            startupText = "\(failure.statusTitle): \(failure.detail)"
        } else if startupTask != nil || isRestartingAudioInput || isSwitchingSpeechModel {
            startupText = startupStatusTitle
        } else {
            startupText = isCoreRuntimeReady ? "Runtime ready" : "Runtime not ready"
        }

        let permissionLines = Permission.allCases
            .map { "\($0.rawValue): \(Permissions.isGranted($0) ? "granted" : "missing")" }
        let availableInputLines = devices.isEmpty
            ? ["Available inputs: none reported"]
            : ["Available inputs (\(devices.count)):"] + devices.map { "  \($0.name)" }
        let pendingUpdateText = pendingUpdate.map { "v\($0.version)" } ?? "none"
        let lastUpdateCheckText = updateCheckDiagnosticText(
            checkedAt: settings.lastUpdateCheckAt,
            source: settings.lastUpdateCheckSource,
            result: settings.lastUpdateCheckResult,
            releaseVersion: settings.lastUpdateCheckVersion
        )
        let updateReminderText: String
        if let version = reminderPausedUpdateVersion,
           let until = reminderPausedUntil,
           Date() < until {
            updateReminderText = "v\(version) until \(ISO8601DateFormatter().string(from: until))"
        } else {
            updateReminderText = "none"
        }
        let memoryLines: [String]
        if let memory = currentAppMemoryUsage() {
            memoryLines = [
                "Resident: \(formattedByteCount(memory.residentBytes))",
                "Physical footprint: \(formattedByteCount(memory.physicalFootprintBytes))",
            ]
        } else {
            memoryLines = []
        }
        let launchAtLoginText: String
        switch SMAppService.mainApp.status {
        case .enabled:
            launchAtLoginText = "enabled"
        case .requiresApproval:
            launchAtLoginText = "requires approval"
        case .notRegistered:
            launchAtLoginText = "disabled"
        case .notFound:
            launchAtLoginText = "not found"
        @unknown default:
            launchAtLoginText = "unknown"
        }

        let speechModelProfile = settings.speechModelProfile
        let languageSettingText = DICTATION_LANGUAGE_DISPLAY[settings.dictationLanguage]
            ?? settings.dictationLanguage.rawValue

        let logLines: [String]
        do {
            logLines = try recentDiagnosticLogLines()
        } catch {
            logLines = ["Unavailable: \(error.localizedDescription)"]
        }

        let snapshot = DiagnosticsReportSnapshot(
            generated: generated,
            appVersion: currentBundleVersion(),
            appBuild: currentBundleBuild(),
            macOS: ProcessInfo.processInfo.operatingSystemVersionString,
            bundleID: Bundle.main.bundleIdentifier ?? "unknown",
            bundlePath: privacySafeBundlePath(bundlePath),
            installKind: installKind,
            status: menuStatusTitle(),
            startup: startupText,
            speechModelReady: isSpeechModelReady,
            coreRuntimeReady: isCoreRuntimeReady,
            readyForDictation: isReady,
            recordingActive: isRecording,
            transcribing: isBusy,
            memoryLines: memoryLines,
            permissionLines: permissionLines,
            settingLines: [
                "Hotkey: \(hotkey.hotkey.name)",
                "Trigger mode: \(TRIGGER_DISPLAY[settings.triggerMode] ?? settings.triggerMode.rawValue)",
                "Speech model: \(speechModelProfile.displayName)",
                "Language: \(languageSettingText)",
                "Paste behavior: \(PASTE_SUFFIX_DISPLAY[settings.pasteSuffix] ?? settings.pasteSuffix.rawValue)",
                "Remove filler words: \(settings.removeFillerWords)",
                "Spoken formatting commands: \(settings.spokenFormattingCommands)",
                "Recent transcripts: \(RECENT_TRANSCRIPT_LIMIT_DISPLAY[settings.recentTranscriptLimit] ?? settings.recentTranscriptLimit.rawValue) (\(history.count) in memory)",
                "Text corrections: \(settings.transcriptCorrections.count) configured",
                "Text correction sync: \(settings.transcriptCorrectionsSyncFile.isEmpty ? "off" : "configured")",
                "Text insertion: \(TextInserter.defaultStrategyDescription)",
                "Restore clipboard after paste: \(settings.restoreClipboardAfterPaste)",
                "Recording waveform: \(settings.showRecordingWaveform)",
                "Mute while recording: \(settings.muteWhileRecording)",
                "Feedback sounds: \(settings.playFeedbackSounds)",
                "Show in Dock: \(settings.showInDock)",
                "Launch at Login: \(launchAtLoginText)",
            ],
            updateLines: [
                "Update notifications: \(settings.checkForUpdates)",
                "Last update check: \(lastUpdateCheckText)",
                "Manual update check active: \(isCheckingForUpdates)",
                "Pending update: \(pendingUpdateText)",
                "Reminder paused: \(updateReminderText)",
                "Update helper log: \((UPDATE_HELPER_LOG_PATH as NSString).abbreviatingWithTildeInPath)",
            ],
            microphoneLines: ["Selected: \(inputLabel)"] + availableInputLines,
            logPath: (Logger.shared.fileURL.path as NSString).abbreviatingWithTildeInPath,
            recentLogLines: logLines
        )
        return diagnosticsReportText(from: snapshot)
    }

    @objc private func retryStartupClicked(_ sender: NSMenuItem) {
        startStartup(reason: "manual retry")
    }

    // MARK: - Setup checklist

    private func maybeShowSetupChecklist(reason: String) {
        guard !didOfferSetupChecklistThisLaunch else { return }
        guard startupFailure != nil
            || !missingPermissions().isEmpty else { return }
        didOfferSetupChecklistThisLaunch = true
        log("setup checklist shown (\(reason))")
        showSetupChecklist()
    }

    @objc private func showSetupChecklistClicked(_ sender: NSMenuItem) {
        showSetupChecklist()
    }

    private func showSetupChecklist() {
        showAppForModal()
        if let window = setupChecklistWindow {
            updateSetupChecklist()
            window.makeKeyAndOrderFront(nil)
            startSetupChecklistRefreshTimer()
            return
        }

        let contentSize = setupChecklistWindowContentSize(
            visibleFrame: NSScreen.main?.visibleFrame
        )
        let window = NSWindow(contentRect: NSRect(origin: .zero, size: contentSize),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable],
                              backing: .buffered,
                              defer: false)
        window.title = "Set Up Presspeech"
        // Match the readability floors used by
        // setupChecklistWindowContentSize(_:). A larger minimum here would
        // let AppKit defeat the constrained-display sizing above.
        window.contentMinSize = NSSize(width: 420, height: 320)
        window.isReleasedWhenClosed = false
        window.delegate = self
        setupChecklistWindow = window

        updateSetupChecklist()
        window.center()
        window.makeKeyAndOrderFront(nil)
        startSetupChecklistRefreshTimer()
    }

    func windowWillClose(_ notification: Notification) {
        guard let window = notification.object as? NSWindow,
              window === setupChecklistWindow else { return }
        stopSetupChecklistRefreshTimer()
    }

    private func startSetupChecklistRefreshTimer() {
        guard setupChecklistRefreshTimer == nil else { return }
        setupChecklistRefreshTimer = Timer.scheduledTimer(timeInterval: 1,
                                                          target: self,
                                                          selector: #selector(setupChecklistTimerFired(_:)),
                                                          userInfo: nil,
                                                          repeats: true)
        setupChecklistRefreshTimer?.tolerance = 0.25
    }

    private func stopSetupChecklistRefreshTimer() {
        setupChecklistRefreshTimer?.invalidate()
        setupChecklistRefreshTimer = nil
    }

    @objc private func setupChecklistTimerFired(_ timer: Timer) {
        guard setupChecklistWindow?.isVisible == true else {
            stopSetupChecklistRefreshTimer()
            return
        }
        updateSetupChecklist()
    }

    private func updateSetupChecklist() {
        guard let window = setupChecklistWindow else { return }
        let snapshot = setupChecklistSnapshot()
        guard snapshot != renderedSetupChecklistSnapshot else { return }

        let focusedIdentifier = (window.firstResponder as? NSView)?.identifier
        let scrollIdentifier = NSUserInterfaceItemIdentifier("setup-checklist-scroll")
        let previousScrollOriginY = (
            setupChecklistView(identifiedBy: scrollIdentifier,
                               in: window.contentView) as? NSScrollView
        )?.documentVisibleRect.minY
        window.contentView = makeSetupChecklistView(snapshot: snapshot)
        renderedSetupChecklistSnapshot = snapshot
        window.contentView?.layoutSubtreeIfNeeded()
        if let previousScrollOriginY,
           let scroll = setupChecklistView(identifiedBy: scrollIdentifier,
                                           in: window.contentView) as? NSScrollView,
           let document = scroll.documentView {
            document.layoutSubtreeIfNeeded()
            let restoredOriginY = restoredSetupChecklistScrollOriginY(
                previousOriginY: previousScrollOriginY,
                documentHeight: document.frame.height,
                viewportHeight: scroll.contentView.bounds.height
            )
            scroll.contentView.scroll(to: NSPoint(x: scroll.documentVisibleRect.minX,
                                                  y: restoredOriginY))
            scroll.reflectScrolledClipView(scroll.contentView)
        }
        if let focusedIdentifier,
           let replacement = setupChecklistView(
               identifiedBy: focusedIdentifier,
               in: window.contentView) {
            window.makeFirstResponder(replacement)
        }
        rebuildMenu()
    }

    private func setupChecklistSnapshot() -> SetupChecklistSnapshot {
        let permissions = Permission.allCases.map { permission in
            let granted = Permissions.isGranted(permission)
            let clicks = permClickCount[permission] ?? 0
            return SetupChecklistPermissionState(
                permission: permission,
                detail: setupDetail(for: permission),
                status: granted ? "Granted" : "Missing",
                buttonTitle: granted ? nil : (clicks >= 1 ? "Try Again" : "Grant"))
        }
        return SetupChecklistSnapshot(
            speechModel: speechModelSetupRowState(
                profile: settings.speechModelProfile,
                isSpeechModelReady: isSpeechModelReady,
                isStartupInProgress: startupTask != nil || isSwitchingSpeechModel,
                startupStatusTitle: startupStatusTitle,
                failure: startupFailure),
            audioInput: audioInputSetupRowState(
                isSpeechModelReady: isSpeechModelReady,
                isCoreRuntimeReady: isCoreRuntimeReady,
                isStartupInProgress: startupTask != nil || isRestartingAudioInput,
                startupStatusTitle: startupStatusTitle,
                failure: startupFailure),
            permissions: permissions,
            hotkey: hotkeySetupRowState(
                isReady: isReady,
                hotkeyTestSucceeded: hotkeyTestSucceeded,
                triggerMode: settings.triggerMode,
                hotkeyName: hotkey.hotkey.name,
                failure: startupFailure),
            showInDock: settings.showInDock,
            isComplete: setupChecklistCompletionState(
                isSpeechModelReady: isSpeechModelReady,
                isReady: isReady,
                permissionsGranted: permissions.allSatisfy { $0.status == "Granted" },
                hotkeyTestSucceeded: hotkeyTestSucceeded))
    }

    private func setupChecklistView(identifiedBy identifier: NSUserInterfaceItemIdentifier,
                                    in root: NSView?) -> NSView? {
        guard let root else { return nil }
        if root.identifier == identifier { return root }
        for child in root.subviews {
            if let match = setupChecklistView(identifiedBy: identifier, in: child) {
                return match
            }
        }
        return nil
    }

    private func makeSetupChecklistView(snapshot: SetupChecklistSnapshot) -> NSView {
        let root = NSStackView()
        root.orientation = .vertical
        // NSStackView on macOS uses NSLayoutConstraint.Attribute for
        // alignment and has no `.fill` case (UIKit-only). With
        // `.leading` every child hugged its own content, so the
        // right-edge Status / Grant column drifted between rows and
        // the NSBox separators — which have no intrinsic width —
        // collapsed to zero. After assembly we explicitly constrain
        // each arranged subview to the inner content width so
        // everything lines up at the same right edge.
        root.alignment = .leading
        root.spacing = 14
        root.edgeInsets = NSEdgeInsets(top: 20, left: 22, bottom: 18, right: 22)
        root.translatesAutoresizingMaskIntoConstraints = false

        let title = setupLabel("Set Up Presspeech", font: .systemFont(ofSize: 22, weight: .semibold))
        let subtitle = setupLabel("Finish these checks before dictating. Presspeech keeps this setup local to your Mac.",
                                  font: .systemFont(ofSize: 13),
                                  color: .secondaryLabelColor)
        subtitle.preferredMaxLayoutWidth = 476
        root.addArrangedSubview(title)
        root.addArrangedSubview(subtitle)
        root.addArrangedSubview(setupSeparator())

        root.addArrangedSubview(makeSetupChecklistRow(
            title: "Speech model",
            state: snapshot.speechModel,
            identifier: "speech-model",
            action: snapshot.speechModel.buttonTitle == nil
                ? nil : #selector(retryStartupFromSetupClicked(_:))))
        root.addArrangedSubview(makeSetupChecklistRow(
            title: "Audio input",
            state: snapshot.audioInput,
            identifier: "audio-input",
            action: snapshot.audioInput.buttonTitle == nil
                ? nil : #selector(retryStartupFromSetupClicked(_:))))

        for permission in snapshot.permissions {
            root.addArrangedSubview(makeSetupChecklistRow(
                title: permission.permission.rawValue,
                state: SetupChecklistRowState(
                    detail: permission.detail,
                    status: permission.status,
                    buttonTitle: permission.buttonTitle),
                identifier: "permission-\(permission.permission.rawValue.lowercased())",
                action: permission.buttonTitle == nil
                    ? nil : #selector(grantSetupPermissionClicked(_:)),
                tag: Permission.allCases.firstIndex(of: permission.permission) ?? -1))
        }

        root.addArrangedSubview(makeSetupChecklistRow(
            title: "Hotkey",
            state: snapshot.hotkey,
            identifier: "hotkey",
            action: snapshot.hotkey.buttonTitle == nil
                ? nil : #selector(retryStartupFromSetupClicked(_:))))

        if !snapshot.isComplete {
            let tipText = snapshot.hotkey.status == "Ready to test"
                ? "Test the hotkey before choosing Done. If it controls another feature or does not respond, choose a different key in Settings → Dictation → Hotkey."
                : "Tip: If clicking 'Grant' doesn't open a prompt or show Presspeech in System Settings, click 'Try Again' — Presspeech will reset its TCC permission entry and re-request, which clears stuck macOS state."
            let tip = setupLabel(tipText,
                                 font: .systemFont(ofSize: 11),
                                 color: .secondaryLabelColor)
            tip.preferredMaxLayoutWidth = 476
            root.addArrangedSubview(tip)
        }

        let footer = NSStackView()
        footer.orientation = .horizontal
        footer.alignment = .centerY
        footer.spacing = 10
        footer.translatesAutoresizingMaskIntoConstraints = false

        let dockAccess = NSButton(checkboxWithTitle: "Show in Dock",
                                  target: self,
                                  action: #selector(toggleDockFromSetup(_:)))
        dockAccess.state = snapshot.showInDock ? .on : .off
        dockAccess.identifier = NSUserInterfaceItemIdentifier("setup-show-in-dock")
        dockAccess.toolTip = "Right-click the Dock icon to open Presspeech controls."
        dockAccess.setAccessibilityHelp("Provides another way to reach Presspeech if its menu-bar item is hidden.")
        let close = NSButton(title: snapshot.isComplete ? "Done" : "Close",
                             target: self,
                             action: #selector(closeSetupChecklistClicked(_:)))
        close.bezelStyle = .rounded
        close.identifier = NSUserInterfaceItemIdentifier("setup-close")

        footer.addArrangedSubview(dockAccess)
        footer.addArrangedSubview(NSView())
        if snapshot.isComplete {
            let tryDictation = NSButton(title: "Try Dictation",
                                        target: self,
                                        action: #selector(showDictationScratchpadClicked(_:)))
            tryDictation.bezelStyle = .rounded
            tryDictation.identifier = NSUserInterfaceItemIdentifier("setup-try-dictation")
            footer.addArrangedSubview(tryDictation)
        }
        footer.addArrangedSubview(close)
        footer.setHuggingPriority(.defaultLow, for: .horizontal)
        // Force every arranged subview to fill the inner content width
        // (root width minus left + right insets). Without this the row
        // NSStackViews hug their content and the right-aligned Status /
        // Grant column drifts between rows; the NSBox separators have
        // no intrinsic width and collapse entirely.
        let innerWidthInset = -(root.edgeInsets.left + root.edgeInsets.right)
        for view in root.arrangedSubviews {
            view.widthAnchor.constraint(equalTo: root.widthAnchor,
                                        constant: innerWidthInset).isActive = true
        }

        let document = SetupChecklistDocumentView()
        document.translatesAutoresizingMaskIntoConstraints = false
        document.addSubview(root)

        let scroll = NSScrollView()
        scroll.identifier = NSUserInterfaceItemIdentifier("setup-checklist-scroll")
        scroll.translatesAutoresizingMaskIntoConstraints = false
        scroll.borderType = .noBorder
        scroll.drawsBackground = false
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true
        scroll.documentView = document
        scroll.setAccessibilityLabel("Setup checklist")

        let footerSeparator = setupSeparator()
        footerSeparator.translatesAutoresizingMaskIntoConstraints = false

        let container = NSView()
        container.addSubview(scroll)
        container.addSubview(footerSeparator)
        container.addSubview(footer)
        NSLayoutConstraint.activate([
            document.leadingAnchor.constraint(equalTo: scroll.contentView.leadingAnchor),
            document.trailingAnchor.constraint(equalTo: scroll.contentView.trailingAnchor),
            document.topAnchor.constraint(equalTo: scroll.contentView.topAnchor),
            document.widthAnchor.constraint(equalTo: scroll.contentView.widthAnchor),
            document.heightAnchor.constraint(greaterThanOrEqualTo: scroll.contentView.heightAnchor),

            root.leadingAnchor.constraint(equalTo: document.leadingAnchor),
            root.trailingAnchor.constraint(equalTo: document.trailingAnchor),
            root.topAnchor.constraint(equalTo: document.topAnchor),
            root.bottomAnchor.constraint(equalTo: document.bottomAnchor),

            scroll.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            scroll.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            scroll.topAnchor.constraint(equalTo: container.topAnchor),
            footerSeparator.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 22),
            footerSeparator.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -22),
            footerSeparator.topAnchor.constraint(equalTo: scroll.bottomAnchor),
            footer.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 22),
            footer.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -22),
            footer.topAnchor.constraint(equalTo: footerSeparator.bottomAnchor, constant: 12),
            footer.bottomAnchor.constraint(equalTo: container.bottomAnchor, constant: -16),
        ])
        return container
    }

    private var setupChecklistIsComplete: Bool {
        isSpeechModelReady
            && isReady
            && missingPermissions().isEmpty
    }

    private func setupDetail(for permission: Permission) -> String {
        switch permission {
        case .microphone:
            return "Captures your voice while dictating. Click 'Grant', then click 'OK' in the macOS prompt."
        case .accessibility:
            return "Pastes the transcript at your cursor. Click 'Grant' to open System Settings → Privacy & Security → Accessibility, then enable the toggle next to 'Presspeech'."
        case .inputMonitoring:
            return "Lets Presspeech detect the dictation hotkey. Click 'Grant' to open System Settings → Privacy & Security → Input Monitoring, then enable the toggle next to 'Presspeech'."
        }
    }

    private func makeSetupChecklistRow(title: String,
                                       state: SetupChecklistRowState,
                                       identifier: String,
                                       action: Selector? = nil,
                                       tag: Int = 0) -> NSView {
        let row = NSStackView()
        row.identifier = NSUserInterfaceItemIdentifier("setup-\(identifier)")
        row.orientation = .horizontal
        row.alignment = .centerY
        row.spacing = 14
        row.translatesAutoresizingMaskIntoConstraints = false

        let textStack = NSStackView()
        textStack.orientation = .vertical
        textStack.alignment = .leading
        textStack.spacing = 2

        textStack.addArrangedSubview(setupLabel(title, font: .systemFont(ofSize: 13, weight: .semibold)))
        
        let detailLabel = setupLabel(state.detail, font: .systemFont(ofSize: 12), color: .secondaryLabelColor)
        detailLabel.preferredMaxLayoutWidth = (state.buttonTitle != nil) ? 310 : 380
        textStack.addArrangedSubview(detailLabel)

        let statusLabel = setupLabel(state.status,
                                     font: .systemFont(ofSize: 12, weight: .medium),
                                     color: setupStatusColor(state.status))
        statusLabel.alignment = .right
        statusLabel.setContentHuggingPriority(.required, for: .horizontal)

        row.addArrangedSubview(textStack)
        row.addArrangedSubview(NSView())
        row.addArrangedSubview(statusLabel)

        if let buttonTitle = state.buttonTitle, let action {
            let button = NSButton(title: buttonTitle, target: self, action: action)
            button.bezelStyle = .rounded
            button.tag = tag
            button.identifier = NSUserInterfaceItemIdentifier("setup-\(identifier)-action")
            button.setAccessibilityLabel("\(buttonTitle) \(title)")
            button.setContentHuggingPriority(.required, for: .horizontal)
            row.addArrangedSubview(button)
        }

        row.setHuggingPriority(.defaultLow, for: .horizontal)
        return row
    }

    private func setupLabel(_ text: String, font: NSFont, color: NSColor = .labelColor) -> NSTextField {
        let label = NSTextField(labelWithString: text)
        label.font = font
        label.textColor = color
        label.lineBreakMode = .byWordWrapping
        label.maximumNumberOfLines = 0
        return label
    }

    private func setupStatusColor(_ status: String) -> NSColor {
        switch status {
        case "Granted", "Ready", "Detected", "Set":
            return .systemGreen
        case "Missing", "Needs retry", "Required":
            return .systemOrange
        default:
            return .secondaryLabelColor
        }
    }

    private func setupSeparator() -> NSBox {
        let separator = NSBox()
        separator.boxType = .separator
        return separator
    }

    @objc private func closeSetupChecklistClicked(_ sender: NSButton) {
        setupChecklistWindow?.close()
    }

    // MARK: - Try Dictation scratchpad

    @objc private func showDictationScratchpadClicked(_ sender: Any?) {
        guard setupChecklistIsComplete, !isTerminating else {
            showSetupChecklist()
            return
        }
        showAppForModal()

        if let window = dictationScratchpadWindow {
            window.makeKeyAndOrderFront(nil)
            if let textView = dictationScratchpadTextView {
                window.makeFirstResponder(textView)
            }
            return
        }

        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 680, height: 440),
                              styleMask: [.titled, .closable, .resizable],
                              backing: .buffered,
                              defer: false)
        window.title = "Try Dictation"
        window.minSize = NSSize(width: 520, height: 330)
        window.isReleasedWhenClosed = false
        window.delegate = self

        let root = NSStackView()
        root.orientation = .vertical
        root.alignment = .leading
        root.spacing = 12
        root.edgeInsets = NSEdgeInsets(top: 22, left: 24, bottom: 20, right: 24)
        root.translatesAutoresizingMaskIntoConstraints = false

        let title = setupLabel("Try Presspeech", font: .systemFont(ofSize: 22, weight: .semibold))
        let instruction = setupLabel("Click below, then \(settings.triggerMode == .hold ? "hold" : "press") \(hotkey.hotkey.name), speak, and \(settings.triggerMode == .hold ? "release" : "press it again"). Your words stay on this Mac.",
                                     font: .systemFont(ofSize: 13),
                                     color: .secondaryLabelColor)
        instruction.preferredMaxLayoutWidth = 632

        let scroll = NSScrollView()
        scroll.borderType = .bezelBorder
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true
        scroll.translatesAutoresizingMaskIntoConstraints = false

        let textView = NSTextView(frame: NSRect(x: 0, y: 0, width: 632, height: 240))
        textView.font = .systemFont(ofSize: 18)
        textView.string = ""
        textView.isRichText = false
        textView.importsGraphics = false
        textView.allowsUndo = true
        textView.textContainerInset = NSSize(width: 12, height: 12)
        textView.minSize = NSSize(width: 0, height: 210)
        textView.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude,
                                  height: CGFloat.greatestFiniteMagnitude)
        textView.isVerticallyResizable = true
        textView.isHorizontallyResizable = false
        textView.autoresizingMask = [.width]
        textView.textContainer?.widthTracksTextView = true
        textView.textContainer?.containerSize = NSSize(width: 0,
                                                       height: CGFloat.greatestFiniteMagnitude)
        scroll.documentView = textView

        let footer = NSStackView()
        footer.orientation = .horizontal
        footer.alignment = .centerY
        footer.spacing = 10
        footer.translatesAutoresizingMaskIntoConstraints = false

        let formattingExamples = settings.dictationLanguage == .french
            ? "“nouveau paragraphe” or “virgule”"
            : "“new paragraph” or “bullet point”"
        let hint = setupLabel("Tip: enable Text → Spoken formatting commands to say \(formattingExamples).",
                              font: .systemFont(ofSize: 11),
                              color: .secondaryLabelColor)
        let clear = NSButton(title: "Clear", target: self, action: #selector(clearDictationScratchpadClicked(_:)))
        clear.bezelStyle = .rounded
        let copy = NSButton(title: "Copy", target: self, action: #selector(copyDictationScratchpadClicked(_:)))
        copy.bezelStyle = .rounded

        footer.addArrangedSubview(hint)
        footer.addArrangedSubview(NSView())
        footer.addArrangedSubview(clear)
        footer.addArrangedSubview(copy)
        footer.setHuggingPriority(.defaultLow, for: .horizontal)

        root.addArrangedSubview(title)
        root.addArrangedSubview(instruction)
        root.addArrangedSubview(scroll)
        root.addArrangedSubview(footer)

        let container = NSView()
        container.addSubview(root)
        NSLayoutConstraint.activate([
            root.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            root.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            root.topAnchor.constraint(equalTo: container.topAnchor),
            root.bottomAnchor.constraint(equalTo: container.bottomAnchor),
            scroll.widthAnchor.constraint(equalTo: root.widthAnchor,
                                          constant: -(root.edgeInsets.left + root.edgeInsets.right)),
            scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 210),
            footer.widthAnchor.constraint(equalTo: scroll.widthAnchor),
            instruction.widthAnchor.constraint(equalTo: scroll.widthAnchor),
        ])

        window.contentView = container
        dictationScratchpadWindow = window
        dictationScratchpadTextView = textView
        window.center()
        window.makeKeyAndOrderFront(nil)
        window.makeFirstResponder(textView)
    }

    @objc private func clearDictationScratchpadClicked(_ sender: NSButton) {
        dictationScratchpadTextView?.string = ""
        if let window = dictationScratchpadWindow,
           let textView = dictationScratchpadTextView {
            window.makeFirstResponder(textView)
        }
    }

    @objc private func copyDictationScratchpadClicked(_ sender: NSButton) {
        guard let text = dictationScratchpadTextView?.string, !text.isEmpty else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }

    @objc private func retryStartupFromSetupClicked(_ sender: NSButton) {
        startStartup(reason: "setup checklist retry")
    }

    @objc private func grantSetupPermissionClicked(_ sender: NSButton) {
        guard Permission.allCases.indices.contains(sender.tag) else { return }
        requestPermissionFromMenu(Permission.allCases[sender.tag])
    }

    // MARK: - Permission row + click-twice-to-reset

    private func buildPermissionItem(_ p: Permission) -> NSMenuItem {
        let clicks = permClickCount[p] ?? 0
        let title: String
        if clicks >= 1 {
            // First click already happened; permission still denied,
            // so signal explicitly that a second click will reset
            // any stuck TCC state and re-request.
            title = "⚠ Grant \(p.rawValue) (try again — will reset stuck state)…"
        } else {
            title = "⚠ Grant \(p.rawValue) permission…"
        }
        let item = NSMenuItem(title: title,
                              action: #selector(grantPermissionClicked(_:)),
                              keyEquivalent: "")
        item.target = self
        item.representedObject = p.rawValue
        return item
    }

    @objc private func grantPermissionClicked(_ sender: NSMenuItem) {
        guard let raw = sender.representedObject as? String,
              let p = Permission(rawValue: raw) else { return }
        requestPermissionFromMenu(p)
    }

    private func requestPermissionFromMenu(_ p: Permission) {
        if Permissions.isGranted(p) {
            permClickCount[p] = nil
            log("perm click ignored: \(p.rawValue) already granted")
            completeReadinessIfPossible(reason: "permission already granted")
            return
        }

        let clicks = (permClickCount[p] ?? 0) + 1
        permClickCount[p] = clicks
        log("perm click #\(clicks): \(p.rawValue)")

        if clicks >= 2 {
            // Click #2+: scrub TCC before re-requesting. The most
            // common cause of "I clicked Grant but nothing happened"
            // is a stuck TCC entry that survived an upgrade. The
            // re-request happens in the reset's completion — issuing
            // it before tccutil finished would race the scrub it
            // depends on.
            log("  resetting TCC for \(p.rawValue) before retry")
            TCC.reset(p, bundleID: Bundle.main.bundleIdentifier ?? "com.local.presspeech") { [weak self] in
                guard let self, !self.isTerminating else { return }
                Permissions.request(p)
                self.startPermissionReadinessMonitor(reason: "permission grant")
                self.updateSetupChecklist()
                self.rebuildMenu()
            }
            rebuildMenu()
            return
        }
        Permissions.request(p)
        startPermissionReadinessMonitor(reason: "permission grant")
        updateSetupChecklist()
        rebuildMenu()
    }

    // MARK: - Settings submenu

    private func buildSettingsItem() -> NSMenuItem {
        let parent = NSMenuItem(title: "Settings", action: nil, keyEquivalent: "")
        let sub = NSMenu()
        sub.autoenablesItems = false

        sub.addItem(buildDictationSettingsItem())
        sub.addItem(buildTextSettingsItem())
        sub.addItem(buildBehaviorSettingsItem())

        parent.submenu = sub
        return parent
    }

    private func buildDictationSettingsItem() -> NSMenuItem {
        let parent = NSMenuItem(title: "Dictation", action: nil, keyEquivalent: "")
        let sub = NSMenu()
        sub.autoenablesItems = false

        sub.addItem(buildHotkeySettingsItem())
        sub.addItem(buildTriggerSettingsItem())
        sub.addItem(buildDictationLanguageSettingsItem())
        sub.addItem(buildInputDeviceItem())

        parent.submenu = sub
        return parent
    }

    private func buildTextSettingsItem() -> NSMenuItem {
        let parent = NSMenuItem(title: "Text", action: nil, keyEquivalent: "")
        let sub = NSMenu()
        sub.autoenablesItems = false

        sub.addItem(buildPasteSuffixSettingsItem())
        sub.addItem(buildRecentTranscriptLimitSettingsItem())
        sub.addItem(buildCorrectionsItem())

        let formatting = NSMenuItem(title: "Spoken formatting commands",
                                    action: #selector(toggleSpokenFormattingCommands(_:)),
                                    keyEquivalent: "")
        formatting.target = self
        formatting.state = settings.spokenFormattingCommands ? .on : .off
        formatting.toolTip = "English commands are available by default; selecting French as the Language Hint uses French line, paragraph, punctuation, quote, and parenthesis commands."
        sub.addItem(formatting)

        let filler = NSMenuItem(title: "Remove filler words (um, uh, ah, er, hmm)",
                                action: #selector(toggleRemoveFillerWords(_:)),
                                keyEquivalent: "")
        filler.target = self
        filler.state = settings.removeFillerWords ? .on : .off
        sub.addItem(filler)

        parent.submenu = sub
        return parent
    }

    private func buildBehaviorSettingsItem() -> NSMenuItem {
        let parent = NSMenuItem(title: "Behavior", action: nil, keyEquivalent: "")
        let sub = NSMenu()
        sub.autoenablesItems = false

        let waveform = NSMenuItem(title: "Show recording waveform",
                                  action: #selector(toggleRecordingWaveform(_:)),
                                  keyEquivalent: "")
        waveform.target = self
        waveform.state = settings.showRecordingWaveform ? .on : .off
        sub.addItem(waveform)

        sub.addItem(buildMaximumRecordingLengthSettingsItem())

        let mute = NSMenuItem(title: "Mute system audio while recording",
                              action: #selector(toggleMute(_:)),
                              keyEquivalent: "")
        mute.target = self
        mute.state = settings.muteWhileRecording ? .on : .off
        sub.addItem(mute)

        let sounds = NSMenuItem(title: "Play feedback sounds",
                                action: #selector(toggleFeedbackSounds(_:)),
                                keyEquivalent: "")
        sounds.target = self
        sounds.state = settings.playFeedbackSounds ? .on : .off
        sub.addItem(sounds)

        let restoreClipboard = NSMenuItem(title: "Restore clipboard after paste",
                                          action: #selector(toggleRestoreClipboard(_:)),
                                          keyEquivalent: "")
        restoreClipboard.target = self
        restoreClipboard.state = settings.restoreClipboardAfterPaste ? .on : .off
        restoreClipboard.toolTip = "After pasting dictated text, put your previous clipboard contents back."
        sub.addItem(restoreClipboard)

        let automaticUpdates = NSMenuItem(title: "Automatically check for updates",
                                          action: #selector(toggleCheckForUpdates(_:)),
                                          keyEquivalent: "")
        automaticUpdates.target = self
        automaticUpdates.state = settings.checkForUpdates ? .on : .off
        automaticUpdates.toolTip = "Periodically checks GitHub for a newer release and only notifies you."
        sub.addItem(automaticUpdates)

        let launchAtLogin = NSMenuItem(title: "Launch at Login",
                                       action: #selector(toggleLaunchAtLogin(_:)),
                                       keyEquivalent: "")
        launchAtLogin.target = self
        launchAtLogin.identifier = Self.launchAtLoginMenuItemIdentifier
        configureLaunchAtLoginMenuItem(launchAtLogin, status: SMAppService.mainApp.status)
        sub.addItem(launchAtLogin)

        let dock = NSMenuItem(title: "Show Presspeech in Dock",
                              action: #selector(toggleDock(_:)),
                              keyEquivalent: "")
        dock.target = self
        dock.state = settings.showInDock ? .on : .off
        sub.addItem(dock)

        parent.submenu = sub
        return parent
    }

    private func configureLaunchAtLoginMenuItem(_ item: NSMenuItem,
                                                status: SMAppService.Status) {
        item.title = "Launch at Login"
        item.toolTip = nil
        switch status {
        case .enabled:
            item.state = .on
        case .requiresApproval:
            item.state = .mixed
            item.title = "Launch at Login (Approval Required)"
            item.toolTip = "Open System Settings → General → Login Items to approve Presspeech."
        default:
            item.state = .off
        }
    }

    private func buildMaximumRecordingLengthSettingsItem() -> NSMenuItem {
        let current = settings.maxRecordingSeconds
        let parent = NSMenuItem(title: "Maximum Recording Length",
                                action: nil,
                                keyEquivalent: "")
        let sub = NSMenu()
        sub.autoenablesItems = false

        if !MAXIMUM_RECORDING_LENGTH_CHOICES.contains(where: { $0.seconds == current }) {
            let custom = NSMenuItem(title: "\(maximumRecordingLengthTitle(seconds: current)) (Custom)",
                                    action: nil,
                                    keyEquivalent: "")
            custom.state = .on
            sub.addItem(custom)
            sub.addItem(.separator())
        }

        for choice in MAXIMUM_RECORDING_LENGTH_CHOICES {
            let item = NSMenuItem(title: choice.title,
                                  action: #selector(selectMaximumRecordingLength(_:)),
                                  keyEquivalent: "")
            item.target = self
            item.state = choice.seconds == current ? .on : .off
            item.representedObject = Int(choice.seconds)
            item.isEnabled = !isRecording && !isTerminating
            sub.addItem(item)
        }

        parent.submenu = sub
        parent.toolTip = "Automatically stop and transcribe a recording at this length."
        return parent
    }

    private func buildHotkeySettingsItem() -> NSMenuItem {
        let hkParent = NSMenuItem(title: "Hotkey", action: nil, keyEquivalent: "")
        let hkSub = NSMenu()
        hkSub.autoenablesItems = false
        let current = hotkey.hotkey

        if !HOTKEY_CHOICES.contains(where: { $0.keycode == current.keycode }) {
            let currentItem = NSMenuItem(title: current.name,
                                         action: nil,
                                         keyEquivalent: "")
            currentItem.state = .on
            currentItem.toolTip = "Recorded custom hotkey"
            hkSub.addItem(currentItem)
            hkSub.addItem(.separator())
        }

        for choice in HOTKEY_CHOICES {
            let item = NSMenuItem(title: choice.name,
                                  action: #selector(selectHotkey(_:)),
                                  keyEquivalent: "")
            item.target = self
            item.state = (choice.keycode == current.keycode) ? .on : .off
            item.representedObject = Int(choice.keycode)
            item.toolTip = choice.isModifier
                ? "Pressing this key starts dictation globally. Choose an F-key if you use it while typing special characters."
                : "On an Apple keyboard, this may require Fn unless function keys are set to work as standard keys."
            hkSub.addItem(item)
        }

        hkSub.addItem(.separator())

        let record = NSMenuItem(title: "Record Hotkey…",
                                action: #selector(recordHotkeyClicked(_:)),
                                keyEquivalent: "")
        record.target = self
        record.isEnabled = !isRecording && !isBusy && !isTerminating
        hkSub.addItem(record)

        let reset = NSMenuItem(title: "Reset Hotkey to Default",
                               action: #selector(resetHotkeyClicked(_:)),
                               keyEquivalent: "")
        reset.target = self
        reset.isEnabled = current.keycode != DEFAULT_HOTKEY_KEYCODE
            && !isRecording
            && !isBusy
            && !isTerminating
        reset.toolTip = "Use Right Option for dictation."
        hkSub.addItem(reset)

        hkParent.submenu = hkSub
        return hkParent
    }

    private func buildTriggerSettingsItem() -> NSMenuItem {
        let tmParent = NSMenuItem(title: "Trigger", action: nil, keyEquivalent: "")
        let tmSub = NSMenu()
        tmSub.autoenablesItems = false
        for mode in [TriggerMode.hold, .toggle] {
            let item = NSMenuItem(title: TRIGGER_DISPLAY[mode] ?? mode.rawValue,
                                  action: #selector(selectTriggerMode(_:)),
                                  keyEquivalent: "")
            item.target = self
            item.state = (mode == settings.triggerMode) ? .on : .off
            item.representedObject = mode.rawValue
            tmSub.addItem(item)
        }
        tmParent.submenu = tmSub
        return tmParent
    }

    private func buildDictationLanguageSettingsItem() -> NSMenuItem {
        let langParent = NSMenuItem(title: "Language Hint", action: nil, keyEquivalent: "")
        let langSub = NSMenu()
        langSub.autoenablesItems = false
        for lang in DictationLanguage.allCases {
            let item = NSMenuItem(title: DICTATION_LANGUAGE_DISPLAY[lang] ?? lang.rawValue,
                                  action: #selector(selectDictationLanguage(_:)),
                                  keyEquivalent: "")
            item.target = self
            item.state = (lang == settings.dictationLanguage) ? .on : .off
            item.representedObject = lang.rawValue
            langSub.addItem(item)
            // Auto-detect is the right default for almost everyone; only
            // pin a specific language if you see wrong-script bleed-through
            // (e.g. Cyrillic letters in Polish output).
            if lang == .auto {
                langSub.addItem(.separator())
            }
        }
        langParent.submenu = langSub
        return langParent
    }

    private func buildPasteSuffixSettingsItem() -> NSMenuItem {
        let pasteParent = NSMenuItem(title: "After Pasting", action: nil, keyEquivalent: "")
        let pasteSub = NSMenu()
        pasteSub.autoenablesItems = false
        for suffix in [PasteSuffix.appendSpace, .none, .appendNewline] {
            let item = NSMenuItem(title: PASTE_SUFFIX_DISPLAY[suffix] ?? suffix.rawValue,
                                  action: #selector(selectPasteSuffix(_:)),
                                  keyEquivalent: "")
            item.target = self
            item.state = (suffix == settings.pasteSuffix) ? .on : .off
            item.representedObject = suffix.rawValue
            pasteSub.addItem(item)
        }
        pasteParent.submenu = pasteSub
        return pasteParent
    }

    private func buildRecentTranscriptLimitSettingsItem() -> NSMenuItem {
        let recentParent = NSMenuItem(title: "Recent Transcripts", action: nil, keyEquivalent: "")
        let recentSub = NSMenu()
        recentSub.autoenablesItems = false
        for limit in RecentTranscriptLimit.allCases {
            let item = NSMenuItem(title: RECENT_TRANSCRIPT_LIMIT_DISPLAY[limit] ?? limit.rawValue,
                                  action: #selector(selectRecentTranscriptLimit(_:)),
                                  keyEquivalent: "")
            item.target = self
            item.state = (limit == settings.recentTranscriptLimit) ? .on : .off
            item.representedObject = limit.rawValue
            recentSub.addItem(item)
        }
        recentParent.submenu = recentSub
        return recentParent
    }

    private func buildInputDeviceItem() -> NSMenuItem {
        let devices = availableAudioInputDevices()
        let rawSavedPreference = settings.inputDevice.trimmingCharacters(in: .whitespacesAndNewlines)
        let savedPreference = isDefaultAggregateAudioInputPreference(rawSavedPreference) ? "" : rawSavedPreference
        let selectedDevice = audioInputDevice(matching: savedPreference, in: devices)
        let canSwitch = !isRecording && !isBusy && !isTerminating
        let parent = NSMenuItem(title: "Microphone", action: nil, keyEquivalent: "")
        if !savedPreference.isEmpty && selectedDevice == nil {
            parent.toolTip = savedPreference
        }

        let sub = NSMenu()
        sub.autoenablesItems = false

        let system = NSMenuItem(title: "System default",
                                action: #selector(selectInputDevice(_:)),
                                keyEquivalent: "")
        system.target = self
        system.representedObject = ""
        system.state = (savedPreference.isEmpty || selectedDevice == nil) ? .on : .off
        system.isEnabled = canSwitch
        sub.addItem(system)

        if !savedPreference.isEmpty && selectedDevice == nil {
            let unavailable = NSMenuItem(title: "Unavailable: \(savedPreference)",
                                         action: nil,
                                         keyEquivalent: "")
            unavailable.isEnabled = false
            sub.addItem(unavailable)
        }

        if !devices.isEmpty {
            sub.addItem(.separator())
        }

        for device in devices {
            let item = NSMenuItem(title: device.name,
                                  action: #selector(selectInputDevice(_:)),
                                  keyEquivalent: "")
            item.target = self
            item.representedObject = device.uid
            item.toolTip = device.uid
            item.state = (selectedDevice?.uid == device.uid) ? .on : .off
            item.isEnabled = canSwitch
            sub.addItem(item)
        }

        parent.submenu = sub
        return parent
    }

    @objc private func selectInputDevice(_ sender: NSMenuItem) {
        guard !isRecording, !isBusy, !isTerminating,
              let preference = sender.representedObject as? String else { return }

        settings.inputDevice = preference
        let label = preference.isEmpty
            ? "system default"
            : (audioInputDevice(matching: preference)?.name ?? preference)
        log("input device selected: \(label)")
        restartAudioForInputDeviceChange()
    }

    private func restartAudioForInputDeviceChange() {
        restartAudioInput(reason: "input device change")
    }

    private func suppressAudioConfigurationChangesFromAppEngineUpdate() {
        let suppressedUntil = Date().timeIntervalSinceReferenceDate
            + AUDIO_CONFIGURATION_CHANGE_SUPPRESSION_SECONDS
        audioConfigurationChangeSuppressedUntil = max(audioConfigurationChangeSuppressedUntil ?? 0,
                                                      suppressedUntil)
    }

    private func shouldIgnoreAppOwnedAudioConfigurationChange() -> Bool {
        let now = Date().timeIntervalSinceReferenceDate
        if audioConfigurationChangeIsSuppressed(now: now,
                                                suppressedUntil: audioConfigurationChangeSuppressedUntil) {
            return true
        }
        if let suppressedUntil = audioConfigurationChangeSuppressedUntil,
           now >= suppressedUntil {
            audioConfigurationChangeSuppressedUntil = nil
        }
        return false
    }

    private func cancelAudioIdleStop() {
        audioIdleStopWorkItem?.cancel()
        audioIdleStopWorkItem = nil
    }

    private func scheduleAudioIdleStop(reason: String) {
        cancelAudioIdleStop()
        guard audio.isEngineStarted, !isRecording, !isBusy, !isTerminating else { return }

        let work = DispatchWorkItem { [weak self] in
            self?.closeIdleAudioInputIfNeeded(reason: reason)
        }
        audioIdleStopWorkItem = work
        DispatchQueue.main.asyncAfter(deadline: .now() + AUDIO_IDLE_STOP_DELAY_SECONDS, execute: work)
    }

    private func stopAudioEngineImmediately() {
        cancelAudioIdleStop()
        if audio.isEngineStarted {
            suppressAudioConfigurationChangesFromAppEngineUpdate()
        }
        audio.stopEngine()
    }

    private func closeIdleAudioInputIfNeeded(reason: String) {
        guard !isRecording, !isBusy, !isTerminating else { return }
        let wasEngineStarted = audio.isEngineStarted
        stopAudioEngineImmediately()
        if wasEngineStarted {
            log("AudioCapture: idle audio input closed (\(reason))")
        }
    }

    private func handleAudioConfigurationChange() {
        if shouldIgnoreAppOwnedAudioConfigurationChange() {
            log("AudioCapture: app-owned audio configuration change ignored")
            return
        }

        switch audioRouteChangeAction(isTerminating: isTerminating,
                                      isRestartingAudioInput: isRestartingAudioInput,
                                      isCoreRuntimeReady: isCoreRuntimeReady,
                                      isRecording: isRecording,
                                      isBusy: isBusy,
                                      hasStartupTask: startupTask != nil) {
        case .ignore:
            return
        case .rebuildMenuOnly:
            log("AudioCapture: audio configuration changed")
            rebuildMenu()
        case .deferRefresh:
            log("AudioCapture: audio configuration changed")
            pendingAudioRouteRefresh = true
            log("AudioCapture: audio route refresh deferred")
            rebuildMenu()
        case .restartNow:
            log("AudioCapture: audio configuration changed")
            rebuildMenu()
            restartAudioInput(reason: "audio configuration change")
        }
    }

    @discardableResult
    private func runDeferredAudioRouteRefreshIfNeeded() -> Bool {
        guard pendingAudioRouteRefresh,
              !isRecording, !isBusy, startupTask == nil, isCoreRuntimeReady, !isTerminating else { return false }
        pendingAudioRouteRefresh = false
        restartAudioInput(reason: "deferred audio configuration change")
        return true
    }

    private func restartAudioInput(reason: String) {
        guard !isRestartingAudioInput else { return }
        guard isCoreRuntimeReady else {
            rebuildMenu()
            return
        }

        pendingAudioRouteRefresh = false
        isRestartingAudioInput = true
        isReady = false
        isRecording = false
        isBusy = false
        hotkey.stop()
        setMenuBarState(.loading)
        rebuildMenu()
        stopAudioEngineImmediately()

        Task { @MainActor in
            defer { isRestartingAudioInput = false }
            do {
                try await startAudioInputWithRetries(reason: reason,
                                                     initialStatusTitle: "Restarting audio input…")
                isCoreRuntimeReady = true
                completeReadinessIfPossible(reason: reason)
            } catch {
                isCoreRuntimeReady = false
                isReady = false
                isRecording = false
                isBusy = false
                hotkey.stop()
                recordStartupFailure(stage: .audioInput, error: error, reason: reason)
            }
        }
    }

    private func buildCorrectionsItem() -> NSMenuItem {
        let corrections = settings.transcriptCorrections
        let title = corrections.isEmpty ? "Dictionary & Shortcuts" : "Dictionary & Shortcuts (\(corrections.count))"
        let parent = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        let sub = NSMenu()
        sub.autoenablesItems = false

        let manage = NSMenuItem(title: "Manage Dictionary & Shortcuts…",
                                action: #selector(showCorrectionsManagerClicked(_:)),
                                keyEquivalent: "")
        manage.target = self
        manage.toolTip = "Search, edit, and remove saved dictionary rules and voice shortcuts."
        sub.addItem(manage)

        sub.addItem(.separator())

        let add = NSMenuItem(title: "Add Dictionary Correction…",
                             action: #selector(addCorrectionClicked(_:)),
                             keyEquivalent: "")
        add.target = self
        sub.addItem(add)

        let addShortcut = NSMenuItem(title: "Add Voice Shortcut…",
                                     action: #selector(addVoiceShortcutClicked(_:)),
                                     keyEquivalent: "")
        addShortcut.target = self
        addShortcut.toolTip = "Replace a phrase you say with exact text, entirely on-device."
        sub.addItem(addShortcut)

        let addFromLast = NSMenuItem(title: "Add Correction from Last Transcript…",
                                     action: #selector(addCorrectionFromLastTranscriptClicked(_:)),
                                     keyEquivalent: "")
        addFromLast.target = self
        addFromLast.isEnabled = history.first != nil
        if let newest = history.first {
            addFromLast.toolTip = previewLine(for: newest)
        }
        sub.addItem(addFromLast)

        sub.addItem(.separator())

        let importItem = NSMenuItem(title: "Import Corrections…",
                                    action: #selector(importCorrectionsClicked(_:)),
                                    keyEquivalent: "")
        importItem.target = self
        sub.addItem(importItem)

        let exportItem = NSMenuItem(title: "Export Corrections…",
                                    action: #selector(exportCorrectionsClicked(_:)),
                                    keyEquivalent: "")
        exportItem.target = self
        exportItem.isEnabled = !corrections.isEmpty
        sub.addItem(exportItem)

        let shareItem = NSMenuItem(title: "Share Corrections…",
                                   action: #selector(shareCorrectionsClicked(_:)),
                                   keyEquivalent: "")
        shareItem.target = self
        shareItem.isEnabled = !corrections.isEmpty
        sub.addItem(shareItem)

        sub.addItem(.separator())

        if let syncURL = correctionSyncFileURL() {
            let syncLabel = NSMenuItem(title: "Syncing: \(syncURL.lastPathComponent)",
                                       action: nil,
                                       keyEquivalent: "")
            syncLabel.isEnabled = false
            syncLabel.toolTip = syncURL.path
            sub.addItem(syncLabel)

            let syncNow = NSMenuItem(title: "Sync Now",
                                     action: #selector(syncCorrectionsNowClicked(_:)),
                                     keyEquivalent: "")
            syncNow.target = self
            sub.addItem(syncNow)

            let stopSync = NSMenuItem(title: "Stop Syncing…",
                                      action: #selector(stopSyncingCorrectionsClicked(_:)),
                                      keyEquivalent: "")
            stopSync.target = self
            sub.addItem(stopSync)
        } else {
            let startSync = NSMenuItem(title: "Set Up Sync…",
                                       action: #selector(setUpCorrectionsSyncClicked(_:)),
                                       keyEquivalent: "")
            startSync.target = self
            sub.addItem(startSync)
        }

        sub.addItem(.separator())

        if corrections.isEmpty {
            let empty = NSMenuItem(title: "No corrections", action: nil, keyEquivalent: "")
            empty.isEnabled = false
            sub.addItem(empty)
            parent.submenu = sub
            return parent
        }

        if shouldShowInlineCorrectionMenuEntries(count: corrections.count) {
            for (index, correction) in corrections.enumerated() {
                let item = NSMenuItem(title: correctionMenuTitle(correction),
                                      action: nil,
                                      keyEquivalent: "")
                let itemSub = NSMenu()
                itemSub.autoenablesItems = false

                let edit = NSMenuItem(title: "Edit…",
                                      action: #selector(editCorrectionClicked(_:)),
                                      keyEquivalent: "")
                edit.target = self
                edit.representedObject = index
                itemSub.addItem(edit)

                let delete = NSMenuItem(title: "Delete",
                                        action: #selector(deleteCorrectionClicked(_:)),
                                        keyEquivalent: "")
                delete.target = self
                delete.representedObject = index
                itemSub.addItem(delete)

                item.submenu = itemSub
                sub.addItem(item)
            }
        } else {
            let summary = NSMenuItem(
                title: "\(corrections.count) saved — use Manage to search and edit",
                action: nil,
                keyEquivalent: ""
            )
            summary.isEnabled = false
            sub.addItem(summary)
        }

        sub.addItem(.separator())

        let removeAll = NSMenuItem(title: "Remove All Corrections…",
                                   action: #selector(removeAllCorrectionsClicked(_:)),
                                   keyEquivalent: "")
        removeAll.target = self
        sub.addItem(removeAll)

        parent.submenu = sub
        return parent
    }

    private func correctionMenuTitle(_ correction: TranscriptCorrection) -> String {
        "\(clippedCorrectionText(correction.source)) → \(clippedCorrectionText(correction.replacement))"
    }

    private func clippedCorrectionText(_ text: String) -> String {
        let flat = text.replacingOccurrences(of: "\n", with: " ")
        return flat.count > 32 ? String(flat.prefix(32)) + "…" : flat
    }

    // MARK: - Dictionary manager

    @objc private func showCorrectionsManagerClicked(_ sender: Any?) {
        showAppForModal()
        if let window = correctionsManagerWindow {
            refreshCorrectionsManager()
            window.makeKeyAndOrderFront(nil)
            if let search = correctionsManagerSearchField {
                window.makeFirstResponder(search)
            }
            return
        }

        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 760, height: 500),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable],
                              backing: .buffered,
                              defer: false)
        window.title = "Dictionary & Shortcuts"
        window.contentMinSize = NSSize(width: 560, height: 340)
        window.isReleasedWhenClosed = false
        window.delegate = self

        let root = NSStackView()
        root.orientation = .vertical
        root.alignment = .leading
        root.spacing = 12
        root.edgeInsets = NSEdgeInsets(top: 20, left: 22, bottom: 18, right: 22)
        root.translatesAutoresizingMaskIntoConstraints = false

        let title = setupLabel("Dictionary & Shortcuts",
                               font: .systemFont(ofSize: 22, weight: .semibold))
        let explanation = setupLabel(
            "Corrections replace recurring mishearings; shortcuts replace a spoken phrase with exact reusable text. Everything stays on this Mac unless you set up a sync file.",
            font: .systemFont(ofSize: 13),
            color: .secondaryLabelColor
        )

        let search = NSSearchField()
        search.placeholderString = "Search heard or pasted text"
        search.sendsSearchStringImmediately = true
        search.target = self
        search.action = #selector(correctionsManagerSearchChanged(_:))
        search.setAccessibilityLabel("Search dictionary and shortcuts")

        let table = NSTableView()
        table.allowsMultipleSelection = true
        table.allowsEmptySelection = true
        table.usesAlternatingRowBackgroundColors = true
        table.rowHeight = 28
        table.dataSource = self
        table.delegate = self
        table.target = self
        table.doubleAction = #selector(editManagedCorrectionFromTable(_:))
        table.setAccessibilityLabel("Saved dictionary rules and voice shortcuts")

        let tableMenu = NSMenu(title: "Dictionary Item")
        tableMenu.autoenablesItems = false
        tableMenu.delegate = self
        let contextEdit = NSMenuItem(title: "Edit…",
                                     action: #selector(editManagedCorrectionFromContextMenu(_:)),
                                     keyEquivalent: "")
        contextEdit.target = self
        contextEdit.identifier = NSUserInterfaceItemIdentifier("correction-context-edit")
        tableMenu.addItem(contextEdit)
        let contextDelete = NSMenuItem(title: "Delete",
                                       action: #selector(deleteManagedCorrectionsFromContextMenu(_:)),
                                       keyEquivalent: "")
        contextDelete.target = self
        contextDelete.identifier = NSUserInterfaceItemIdentifier("correction-context-delete")
        tableMenu.addItem(contextDelete)
        table.menu = tableMenu

        let heardColumn = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("correction-source"))
        heardColumn.title = "Heard / When you say"
        heardColumn.minWidth = 180
        heardColumn.width = 330
        heardColumn.sortDescriptorPrototype = NSSortDescriptor(
            key: TranscriptCorrectionSortField.source.rawValue,
            ascending: true
        )
        table.addTableColumn(heardColumn)

        let pasteColumn = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("correction-replacement"))
        pasteColumn.title = "Paste"
        pasteColumn.minWidth = 180
        pasteColumn.width = 360
        pasteColumn.sortDescriptorPrototype = NSSortDescriptor(
            key: TranscriptCorrectionSortField.replacement.rawValue,
            ascending: true
        )
        table.addTableColumn(pasteColumn)

        let scroll = NSScrollView()
        scroll.borderType = .bezelBorder
        scroll.hasVerticalScroller = true
        scroll.hasHorizontalScroller = false
        scroll.autohidesScrollers = true
        scroll.documentView = table
        scroll.translatesAutoresizingMaskIntoConstraints = false

        let footer = NSStackView()
        footer.orientation = .horizontal
        footer.alignment = .centerY
        footer.spacing = 10
        footer.translatesAutoresizingMaskIntoConstraints = false

        let summary = setupLabel("", font: .systemFont(ofSize: 12), color: .secondaryLabelColor)
        let addCorrection = NSButton(title: "Add Correction…",
                                     target: self,
                                     action: #selector(addManagedCorrectionClicked(_:)))
        let addShortcut = NSButton(title: "Add Shortcut…",
                                   target: self,
                                   action: #selector(addManagedShortcutClicked(_:)))
        let edit = NSButton(title: "Edit…",
                            target: self,
                            action: #selector(editManagedCorrectionClicked(_:)))
        let delete = NSButton(title: "Delete",
                              target: self,
                              action: #selector(deleteManagedCorrectionsClicked(_:)))
        for button in [addCorrection, addShortcut, edit, delete] {
            button.bezelStyle = .rounded
        }
        edit.isEnabled = false
        delete.isEnabled = false

        footer.addArrangedSubview(summary)
        footer.addArrangedSubview(NSView())
        footer.addArrangedSubview(addCorrection)
        footer.addArrangedSubview(addShortcut)
        footer.addArrangedSubview(edit)
        footer.addArrangedSubview(delete)
        footer.setHuggingPriority(.defaultLow, for: .horizontal)

        root.addArrangedSubview(title)
        root.addArrangedSubview(explanation)
        root.addArrangedSubview(search)
        root.addArrangedSubview(scroll)
        root.addArrangedSubview(footer)

        let container = NSView()
        container.addSubview(root)
        NSLayoutConstraint.activate([
            root.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            root.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            root.topAnchor.constraint(equalTo: container.topAnchor),
            root.bottomAnchor.constraint(equalTo: container.bottomAnchor),
            explanation.widthAnchor.constraint(equalTo: root.widthAnchor,
                                                constant: -(root.edgeInsets.left + root.edgeInsets.right)),
            search.widthAnchor.constraint(equalTo: explanation.widthAnchor),
            scroll.widthAnchor.constraint(equalTo: explanation.widthAnchor),
            scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 210),
            footer.widthAnchor.constraint(equalTo: explanation.widthAnchor),
        ])

        window.contentView = container
        correctionsManagerWindow = window
        correctionsManagerSearchField = search
        correctionsManagerTableView = table
        correctionsManagerContextMenu = tableMenu
        correctionsManagerSummaryLabel = summary
        correctionsManagerEditButton = edit
        correctionsManagerDeleteButton = delete
        refreshCorrectionsManager()
        window.center()
        window.makeKeyAndOrderFront(nil)
        window.makeFirstResponder(search)
    }

    @objc private func correctionsManagerSearchChanged(_ sender: NSSearchField) {
        refreshCorrectionsManager()
    }

    private func refreshCorrectionsManager(selecting correctionIndex: Int? = nil,
                                           preservingCorrectionIndices: [Int] = []) {
        guard let table = correctionsManagerTableView else { return }
        let corrections = settings.transcriptCorrections
        let query = correctionsManagerSearchField?.stringValue ?? ""
        correctionsManagerVisibleIndices = correctionSearchMatchIndices(in: corrections, query: query)
        if let descriptor = table.sortDescriptors.first,
           let key = descriptor.key,
           let field = TranscriptCorrectionSortField(rawValue: key) {
            correctionsManagerVisibleIndices = sortedTranscriptCorrectionIndices(
                correctionsManagerVisibleIndices,
                in: corrections,
                by: field,
                ascending: descriptor.ascending
            )
        }
        table.reloadData()

        if let correctionIndex,
           let row = correctionsManagerVisibleIndices.firstIndex(of: correctionIndex) {
            table.selectRowIndexes(IndexSet(integer: row), byExtendingSelection: false)
            table.scrollRowToVisible(row)
        } else if !preservingCorrectionIndices.isEmpty {
            let preserved = IndexSet(
                preservingCorrectionIndices.compactMap {
                    correctionsManagerVisibleIndices.firstIndex(of: $0)
                }
            )
            table.selectRowIndexes(preserved, byExtendingSelection: false)
        } else {
            table.deselectAll(nil)
        }

        let visibleCount = correctionsManagerVisibleIndices.count
        if query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            correctionsManagerSummaryLabel?.stringValue = "\(corrections.count) saved"
        } else {
            correctionsManagerSummaryLabel?.stringValue = "\(visibleCount) of \(corrections.count) shown"
        }
        updateCorrectionsManagerSelectionState()
    }

    private func selectedManagedCorrectionIndices() -> [Int] {
        guard let table = correctionsManagerTableView else { return [] }
        return table.selectedRowIndexes.compactMap { row in
            correctionsManagerVisibleIndices.indices.contains(row)
                ? correctionsManagerVisibleIndices[row]
                : nil
        }
    }

    private func updateCorrectionsManagerSelectionState() {
        let selectedCount = selectedManagedCorrectionIndices().count
        correctionsManagerEditButton?.isEnabled = selectedCount == 1
        correctionsManagerDeleteButton?.isEnabled = selectedCount > 0
        correctionsManagerDeleteButton?.title = selectedCount > 1 ? "Delete \(selectedCount)" : "Delete"
    }

    func numberOfRows(in tableView: NSTableView) -> Int {
        tableView === correctionsManagerTableView ? correctionsManagerVisibleIndices.count : 0
    }

    func tableView(_ tableView: NSTableView,
                   viewFor tableColumn: NSTableColumn?,
                   row: Int) -> NSView? {
        guard tableView === correctionsManagerTableView,
              correctionsManagerVisibleIndices.indices.contains(row) else { return nil }
        let corrections = settings.transcriptCorrections
        let index = correctionsManagerVisibleIndices[row]
        guard corrections.indices.contains(index) else { return nil }
        let correction = corrections[index]
        let isSource = tableColumn?.identifier.rawValue == "correction-source"
        let value = isSource ? correction.source : correction.replacement

        let cell = NSTableCellView()
        cell.identifier = tableColumn?.identifier
        let label = NSTextField(labelWithString: value.replacingOccurrences(of: "\n", with: " "))
        label.lineBreakMode = .byTruncatingTail
        label.maximumNumberOfLines = 1
        label.toolTip = value
        label.translatesAutoresizingMaskIntoConstraints = false
        cell.textField = label
        cell.addSubview(label)
        NSLayoutConstraint.activate([
            label.leadingAnchor.constraint(equalTo: cell.leadingAnchor, constant: 6),
            label.trailingAnchor.constraint(equalTo: cell.trailingAnchor, constant: -6),
            label.centerYAnchor.constraint(equalTo: cell.centerYAnchor),
        ])
        return cell
    }

    func tableViewSelectionDidChange(_ notification: Notification) {
        guard let table = notification.object as? NSTableView,
              table === correctionsManagerTableView else { return }
        updateCorrectionsManagerSelectionState()
    }

    func tableView(_ tableView: NSTableView,
                   sortDescriptorsDidChange oldDescriptors: [NSSortDescriptor]) {
        guard tableView === correctionsManagerTableView else { return }
        let selected = selectedManagedCorrectionIndices()
        refreshCorrectionsManager(preservingCorrectionIndices: selected)
    }

    @objc private func addManagedCorrectionClicked(_ sender: NSButton) {
        guard let correction = showCorrectionEditor(existing: nil, mode: .correction) else { return }
        saveCorrection(correction)
    }

    @objc private func addManagedShortcutClicked(_ sender: NSButton) {
        guard let correction = showCorrectionEditor(existing: nil, mode: .shortcut) else { return }
        saveCorrection(correction)
    }

    @objc private func editManagedCorrectionClicked(_ sender: NSButton) {
        editSelectedManagedCorrection()
    }

    @objc private func editManagedCorrectionFromContextMenu(_ sender: NSMenuItem) {
        editSelectedManagedCorrection()
    }

    @objc private func editManagedCorrectionFromTable(_ sender: NSTableView) {
        guard sender === correctionsManagerTableView,
              sender.clickedRow >= 0 else { return }
        // A double-click always acts on the row under the pointer, even if it
        // began as one row in a multiple selection.
        sender.selectRowIndexes(IndexSet(integer: sender.clickedRow),
                                byExtendingSelection: false)
        editSelectedManagedCorrection()
    }

    private func editSelectedManagedCorrection() {
        guard let index = selectedManagedCorrectionIndices().first else { return }
        let corrections = settings.transcriptCorrections
        guard corrections.indices.contains(index),
              let edited = showCorrectionEditor(existing: corrections[index], mode: .correction) else { return }
        saveCorrection(edited, replacing: index)
    }

    @objc private func deleteManagedCorrectionsClicked(_ sender: NSButton) {
        deleteSelectedManagedCorrections()
    }

    @objc private func deleteManagedCorrectionsFromContextMenu(_ sender: NSMenuItem) {
        deleteSelectedManagedCorrections()
    }

    private func deleteSelectedManagedCorrections() {
        let selected = selectedManagedCorrectionIndices().sorted(by: >)
        guard !selected.isEmpty else { return }

        if selected.count > 1 {
            showAppForModal()
            let alert = NSAlert()
            alert.alertStyle = .warning
            alert.messageText = "Delete \(selected.count) Selected Items?"
            alert.informativeText = "This removes the selected dictionary rules and shortcuts from this Mac."
            alert.addButton(withTitle: "Delete")
            alert.addButton(withTitle: "Cancel")
            guard alert.runModal() == .alertFirstButtonReturn else { return }
        }

        var corrections = settings.transcriptCorrections
        for index in selected where corrections.indices.contains(index) {
            corrections.remove(at: index)
        }
        updateTranscriptCorrections(corrections)
    }

    @objc private func addCorrectionClicked(_ sender: NSMenuItem) {
        guard let correction = showCorrectionEditor(existing: nil, mode: .correction) else { return }
        saveCorrection(correction)
    }

    @objc private func addVoiceShortcutClicked(_ sender: NSMenuItem) {
        guard let shortcut = showCorrectionEditor(existing: nil, mode: .shortcut) else { return }
        saveCorrection(shortcut)
    }

    @objc private func addCorrectionFromLastTranscriptClicked(_ sender: NSMenuItem) {
        guard let newest = history.first else { return }
        let prefill = correctionSourcePrefill(from: newest)
        guard !prefill.isEmpty else { return }
        guard let correction = showCorrectionEditor(existing: nil, mode: .correction, prefillSource: prefill) else { return }
        saveCorrection(correction)
    }

    @objc private func editCorrectionClicked(_ sender: NSMenuItem) {
        guard let index = sender.representedObject as? Int else { return }
        let corrections = settings.transcriptCorrections
        guard corrections.indices.contains(index) else { return }
        guard let correction = showCorrectionEditor(existing: corrections[index], mode: .correction) else { return }
        saveCorrection(correction, replacing: index)
    }

    @objc private func deleteCorrectionClicked(_ sender: NSMenuItem) {
        guard let index = sender.representedObject as? Int else { return }
        var corrections = settings.transcriptCorrections
        guard corrections.indices.contains(index) else { return }
        corrections.remove(at: index)
        updateTranscriptCorrections(corrections)
    }

    @objc private func removeAllCorrectionsClicked(_ sender: NSMenuItem) {
        guard !settings.transcriptCorrections.isEmpty else { return }
        showAppForModal()
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "Remove All Text Corrections?"
        alert.informativeText = "This removes every saved text correction from this Mac."
        alert.addButton(withTitle: "Remove All")
        alert.addButton(withTitle: "Cancel")
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        updateTranscriptCorrections([])
    }

    @objc private func importCorrectionsClicked(_ sender: NSMenuItem) {
        showAppForModal()
        let panel = NSOpenPanel()
        panel.title = "Import Text Corrections"
        panel.message = "Choose a Presspeech corrections file to import."
        panel.prompt = "Import"
        panel.allowedContentTypes = [TranscriptCorrectionsTransfer.contentType]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        guard panel.runModal() == .OK, let url = panel.url else { return }
        _ = importCorrectionsFromUserSelectedFile(url)
    }

    @objc private func exportCorrectionsClicked(_ sender: NSMenuItem) {
        showAppForModal()
        let panel = NSSavePanel()
        panel.title = "Export Text Corrections"
        panel.message = "Save a file you can AirDrop, store in iCloud Drive, or import on another Mac."
        panel.prompt = "Export"
        panel.nameFieldStringValue = CORRECTIONS_FILE_NAME
        panel.allowedContentTypes = [TranscriptCorrectionsTransfer.contentType]
        panel.canCreateDirectories = true
        guard panel.runModal() == .OK, let url = panel.url else { return }

        do {
            try TranscriptCorrectionsTransfer.write(settings.transcriptCorrections, to: url)
            log("correction export wrote \(settings.transcriptCorrections.count) corrections")
        } catch {
            showCorrectionTransferError(title: "Export Failed", error: error)
        }
    }

    @objc private func shareCorrectionsClicked(_ sender: NSMenuItem) {
        showAppForModal()
        do {
            cleanupPendingSharedCorrections(reason: "new share")

            let folder = FileManager.default.temporaryDirectory
                .appendingPathComponent("Presspeech-\(UUID().uuidString)", isDirectory: true)
            let url = folder.appendingPathComponent(CORRECTIONS_FILE_NAME)
            try TranscriptCorrectionsTransfer.write(settings.transcriptCorrections, to: url)
            pendingSharedCorrectionsURL = url

            let picker = NSSharingServicePicker(items: [url])
            let cleanupDelegate = CorrectionShareCleanupDelegate { [weak self] reason in
                self?.cleanupPendingSharedCorrections(reason: reason)
            }
            picker.delegate = cleanupDelegate
            correctionSharePicker = picker
            correctionShareCleanupDelegate = cleanupDelegate
            if let button = statusItem.button {
                picker.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
            } else {
                cleanupPendingSharedCorrections(reason: "missing status button")
            }
            log("correction share prepared \(settings.transcriptCorrections.count) corrections")
        } catch {
            showCorrectionTransferError(title: "Share Failed", error: error)
        }
    }

    @objc private func setUpCorrectionsSyncClicked(_ sender: NSMenuItem) {
        showAppForModal()
        let alert = NSAlert()
        alert.messageText = "Set Up Text Correction Sync"
        alert.informativeText = """
            Presspeech can keep corrections in one local file. Put that file in iCloud Drive, Dropbox, Syncthing, or another synced folder to keep multiple Macs aligned without a Presspeech account.

            Presspeech only reads and writes the file you choose.
            """
        alert.addButton(withTitle: "Create Sync File")
        alert.addButton(withTitle: "Use Existing File")
        alert.addButton(withTitle: "Cancel")

        switch alert.runModal() {
        case .alertFirstButtonReturn:
            createCorrectionsSyncFile()
        case .alertSecondButtonReturn:
            useExistingCorrectionsSyncFile()
        default:
            return
        }
    }

    @objc private func syncCorrectionsNowClicked(_ sender: NSMenuItem) {
        guard correctionSyncFileURL() != nil else { return }
        scheduleCorrectionSyncScan(force: true, presentErrors: true)
    }

    @objc private func stopSyncingCorrectionsClicked(_ sender: NSMenuItem) {
        showAppForModal()
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "Stop Syncing Text Corrections?"
        alert.informativeText = "Presspeech will keep the corrections already on this Mac. The sync file will not be deleted."
        alert.addButton(withTitle: "Stop Syncing")
        alert.addButton(withTitle: "Cancel")
        guard alert.runModal() == .alertFirstButtonReturn else { return }

        settings.transcriptCorrectionsSyncFile = ""
        correctionSyncTimer?.invalidate()
        correctionSyncTimer = nil
        correctionSyncFileFingerprint = nil
        correctionSyncBaselineCorrections = []
        rebuildMenu()
    }

    private func showAppForModal() {
        NSApp.unhide(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @discardableResult
    private func importCorrectionsFromUserSelectedFile(_ url: URL) -> Bool {
        showAppForModal()
        do {
            let imported = try TranscriptCorrectionsTransfer.readCounted(from: url)
            guard let choice = chooseCorrectionImportMode(imported: imported.corrections,
                                                          originalCount: imported.originalCount,
                                                          sourceName: url.lastPathComponent,
                                                          allowsEmptyReplace: false) else {
                return false
            }
            let next = corrections(afterApplying: imported.corrections, mode: choice)
            updateTranscriptCorrections(next)
            log("correction import read \(imported.corrections.count) corrections")
            return true
        } catch {
            showCorrectionTransferError(title: "Import Failed", error: error)
            return false
        }
    }

    private func createCorrectionsSyncFile() {
        showAppForModal()
        let panel = NSSavePanel()
        panel.title = "Create Text Correction Sync File"
        panel.message = "Choose where Presspeech should keep the sync file. A folder synced by iCloud Drive or another provider works best."
        panel.prompt = "Create"
        panel.nameFieldStringValue = CORRECTIONS_FILE_NAME
        panel.allowedContentTypes = [TranscriptCorrectionsTransfer.contentType]
        panel.canCreateDirectories = true
        guard panel.runModal() == .OK, let url = panel.url else { return }

        do {
            try TranscriptCorrectionsTransfer.write(settings.transcriptCorrections, to: url)
            settings.transcriptCorrectionsSyncFile = url.path
            startCorrectionSyncIfConfigured()
            log("correction sync created file with \(settings.transcriptCorrections.count) corrections")
        } catch {
            showCorrectionTransferError(title: "Sync Setup Failed", error: error)
        }
    }

    private func useExistingCorrectionsSyncFile() {
        showAppForModal()
        let panel = NSOpenPanel()
        panel.title = "Choose Text Correction Sync File"
        panel.message = "Choose an existing Presspeech corrections file."
        panel.prompt = "Use File"
        panel.allowedContentTypes = [TranscriptCorrectionsTransfer.contentType]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        guard panel.runModal() == .OK, let url = panel.url else { return }

        do {
            let imported = try TranscriptCorrectionsTransfer.readCounted(from: url)
            guard let choice = chooseCorrectionImportMode(imported: imported.corrections,
                                                          originalCount: imported.originalCount,
                                                          sourceName: url.lastPathComponent,
                                                          allowsEmptyReplace: true) else {
                return
            }
            let next = corrections(afterApplying: imported.corrections, mode: choice)
            settings.transcriptCorrectionsSyncFile = url.path
            updateTranscriptCorrections(next, writeToSync: false)
            if choice == .merge {
                guard writeCorrectionsToSyncFile(presentErrors: true) else {
                    settings.transcriptCorrectionsSyncFile = ""
                    rebuildMenu()
                    return
                }
            } else {
                correctionSyncFileFingerprint = correctionSyncFingerprint(for: url)
                correctionSyncBaselineCorrections = normalizedTranscriptCorrections(next)
            }
            startCorrectionSyncIfConfigured()
            log("correction sync linked file with \(imported.corrections.count) corrections")
        } catch {
            showCorrectionTransferError(title: "Sync Setup Failed", error: error)
        }
    }

    private func chooseCorrectionImportMode(imported: [TranscriptCorrection],
                                            originalCount: Int,
                                            sourceName: String,
                                            allowsEmptyReplace: Bool) -> CorrectionImportChoice? {
        let imported = normalizedTranscriptCorrections(imported)
        showAppForModal()
        if imported.isEmpty {
            let alert = NSAlert()
            alert.messageText = "No Text Corrections Found"
            alert.informativeText = allowsEmptyReplace
                ? "\(sourceName) does not contain any corrections. You can still use it as an empty sync file."
                : "\(sourceName) does not contain any corrections to import."
            alert.addButton(withTitle: allowsEmptyReplace ? "Use Empty File" : "OK")
            if allowsEmptyReplace { alert.addButton(withTitle: "Cancel") }
            let response = alert.runModal()
            return allowsEmptyReplace && response == .alertFirstButtonReturn ? .replace : nil
        }

        let summary = correctionImportSummary(for: imported)
        let countText = correctionImportCountText(sourceName: sourceName,
                                                  originalCount: originalCount,
                                                  keptCount: summary.total)
        let mergeCapWarning = correctionImportMergeCapWarningText(
            existingCount: settings.transcriptCorrections.count,
            newCount: summary.newCount
        )
        let alert = NSAlert()
        alert.messageText = "Import Text Corrections?"
        alert.informativeText = """
            \(countText)

            \(summary.newCount) new, \(summary.updatedCount) will update existing corrections, \(summary.unchangedCount) already match.

            Merge keeps local corrections that are not in the file. Replace All makes this Mac match the file exactly.\(mergeCapWarning.map { "\n\n" + $0 } ?? "")
            """
        alert.addButton(withTitle: "Merge")
        alert.addButton(withTitle: "Replace All")
        alert.addButton(withTitle: "Cancel")

        switch alert.runModal() {
        case .alertFirstButtonReturn:
            return .merge
        case .alertSecondButtonReturn:
            return .replace
        default:
            return nil
        }
    }

    private func correctionImportSummary(for imported: [TranscriptCorrection]) -> CorrectionImportSummary {
        let existingBySource = Dictionary(uniqueKeysWithValues: settings.transcriptCorrections.map {
            (normalizedTranscriptCorrectionSource($0.source), $0)
        })

        var newCount = 0
        var updatedCount = 0
        var unchangedCount = 0

        for correction in imported {
            let key = normalizedTranscriptCorrectionSource(correction.source)
            guard let existing = existingBySource[key] else {
                newCount += 1
                continue
            }
            if existing == correction {
                unchangedCount += 1
            } else {
                updatedCount += 1
            }
        }

        return CorrectionImportSummary(
            total: imported.count,
            newCount: newCount,
            updatedCount: updatedCount,
            unchangedCount: unchangedCount
        )
    }

    private func corrections(afterApplying imported: [TranscriptCorrection],
                             mode: CorrectionImportChoice) -> [TranscriptCorrection] {
        let imported = normalizedTranscriptCorrections(imported)
        switch mode {
        case .replace:
            return imported
        case .merge:
            var merged = settings.transcriptCorrections
            var indexBySource = Dictionary(uniqueKeysWithValues: merged.enumerated().map {
                (normalizedTranscriptCorrectionSource($0.element.source), $0.offset)
            })

            for correction in imported {
                let key = normalizedTranscriptCorrectionSource(correction.source)
                if let index = indexBySource[key] {
                    merged[index] = correction
                } else {
                    indexBySource[key] = merged.count
                    merged.append(correction)
                }
            }
            return merged
        }
    }

    private func updateTranscriptCorrections(_ corrections: [TranscriptCorrection],
                                             writeToSync: Bool = true) {
        if let error = settings.storeTranscriptCorrections(normalizedTranscriptCorrections(corrections)) {
            // The previous value is still in place. Surface the failed
            // save like export/sync-write failures do — silently
            // dropping the user's edit looked like data loss.
            showCorrectionTransferError(title: "Saving Corrections Failed", error: error)
            refreshCorrectionsManager()
            rebuildMenu()
            return
        }
        if writeToSync, !isApplyingCorrectionSyncFile {
            writeCorrectionsToSyncFile(presentErrors: false)
        }
        refreshCorrectionsManager()
        rebuildMenu()
    }

    private func correctionSyncFileURL() -> URL? {
        let path = settings.transcriptCorrectionsSyncFile
        guard !path.isEmpty else { return nil }
        return URL(fileURLWithPath: path)
    }

    private func startCorrectionSyncIfConfigured() {
        correctionSyncTimer?.invalidate()
        correctionSyncTimer = nil
        guard correctionSyncFileURL() != nil else {
            correctionSyncFileFingerprint = nil
            correctionSyncBaselineCorrections = []
            return
        }

        scheduleCorrectionSyncScan(force: true, presentErrors: false)
        // The timer always starts; if the initial async scan rejects
        // the path it stops the sync (and this timer) from its
        // main-actor completion.
        correctionSyncTimer = Timer.scheduledTimer(timeInterval: 4,
                                                   target: self,
                                                   selector: #selector(correctionSyncTimerFired(_:)),
                                                   userInfo: nil,
                                                   repeats: true)
        correctionSyncTimer?.tolerance = 1
    }

    @objc private func correctionSyncTimerFired(_ timer: Timer) {
        scheduleCorrectionSyncScan(force: false, presentErrors: false)
    }

    /// What a background sync-file scan found. Built off the main
    /// thread, applied on the main actor — so it must be Sendable and
    /// carry everything the apply step needs.
    private enum CorrectionSyncScanOutcome: Sendable {
        case rejectedPath(TranscriptCorrectionsSyncPathError)
        case fingerprintUnavailable
        case unchanged
        case loaded(corrections: [TranscriptCorrection], fingerprint: CorrectionSyncFileFingerprint)
        case readFailed(logDescription: String, alertMessage: String)
    }

    /// Runs on `correctionSyncScanQueue` (hence `nonisolated`). Pure
    /// with respect to app state: everything it needs arrives as
    /// parameters and the result goes back as a value.
    private nonisolated static func performCorrectionSyncScan(url: URL,
                                                  lastFingerprint: CorrectionSyncFileFingerprint?,
                                                  force: Bool) -> CorrectionSyncScanOutcome {
        do {
            try validateCorrectionSyncPath(url)
        } catch let error as TranscriptCorrectionsSyncPathError {
            return .rejectedPath(error)
        } catch {
            // validateCorrectionSyncPath only throws
            // TranscriptCorrectionsSyncPathError today; keep the
            // catch-all defensive rather than crashing the scan.
            return .readFailed(logDescription: "\(error)",
                               alertMessage: error.localizedDescription)
        }
        guard let fingerprint = correctionSyncFingerprint(for: url) else {
            return .fingerprintUnavailable
        }
        guard force || fingerprint != lastFingerprint else { return .unchanged }
        do {
            let corrections = try TranscriptCorrectionsTransfer.read(from: url)
            return .loaded(corrections: corrections, fingerprint: fingerprint)
        } catch {
            return .readFailed(logDescription: "\(error)",
                               alertMessage: error.localizedDescription)
        }
    }

    private func scheduleCorrectionSyncScan(force: Bool, presentErrors: Bool) {
        guard let url = correctionSyncFileURL() else { return }
        // Never let scans overlap — a dataless iCloud file can block
        // one scan for many timer periods. Requests that arrive while
        // a scan is in flight are coalesced (strongest flags win) and
        // re-issued when it completes, so a user's explicit
        // "Sync Corrections Now" is never silently dropped behind a
        // stalled timer scan.
        guard !correctionSyncScanInFlight else {
            let pending = pendingCorrectionSyncScan
            pendingCorrectionSyncScan = (force: (pending?.force ?? false) || force,
                                         presentErrors: (pending?.presentErrors ?? false) || presentErrors)
            return
        }
        correctionSyncScanInFlight = true
        let lastFingerprint = correctionSyncFileFingerprint
        Self.correctionSyncScanQueue.async { [weak self] in
            let outcome = Self.performCorrectionSyncScan(url: url,
                                                         lastFingerprint: lastFingerprint,
                                                         force: force)
            Task { @MainActor in
                guard let self else { return }
                self.correctionSyncScanInFlight = false
                self.applyCorrectionSyncScanOutcome(outcome,
                                                    scannedURL: url,
                                                    scanStartFingerprint: lastFingerprint,
                                                    force: force,
                                                    presentErrors: presentErrors)
                if let pending = self.pendingCorrectionSyncScan {
                    self.pendingCorrectionSyncScan = nil
                    self.scheduleCorrectionSyncScan(force: pending.force,
                                                    presentErrors: pending.presentErrors)
                }
            }
        }
    }

    private func applyCorrectionSyncScanOutcome(_ outcome: CorrectionSyncScanOutcome,
                                                scannedURL: URL,
                                                scanStartFingerprint: CorrectionSyncFileFingerprint?,
                                                force: Bool,
                                                presentErrors: Bool) {
        // The sync file may have been disconnected or repointed while
        // the scan ran; results for a stale path must not touch
        // current state.
        guard let url = correctionSyncFileURL(), url == scannedURL else { return }

        switch outcome {
        case .rejectedPath(let error):
            handleCorrectionSyncRejectedPath(error, presentErrors: presentErrors)
        case .fingerprintUnavailable:
            if presentErrors {
                showCorrectionTransferError(title: "Sync Failed",
                                            message: "Presspeech could not find the selected sync file.")
            }
        case .unchanged:
            break
        case .loaded(let corrections, let fingerprint):
            // If a local edit wrote the sync file (moving the
            // fingerprint) while the scan ran, this outcome holds
            // pre-edit content; applying it would roll the edit back
            // and rewind the baseline. Drop it — a forced scan is
            // re-issued so a "Sync Now" still completes against the
            // post-edit file.
            guard correctionSyncFileFingerprint == scanStartFingerprint else {
                if force {
                    scheduleCorrectionSyncScan(force: true, presentErrors: presentErrors)
                }
                return
            }
            // Non-forced scans only apply genuinely new content
            // (forced scans deliberately re-apply even an unchanged
            // file — that is what "Sync Now" promises).
            guard force || fingerprint != correctionSyncFileFingerprint else { return }
            isApplyingCorrectionSyncFile = true
            updateTranscriptCorrections(corrections, writeToSync: false)
            isApplyingCorrectionSyncFile = false
            correctionSyncFileFingerprint = fingerprint
            correctionSyncBaselineCorrections = normalizedTranscriptCorrections(corrections)
            log("correction sync read \(corrections.count) corrections")
        case .readFailed(let logDescription, let alertMessage):
            log("correction sync read failed: \(logDescription)")
            if presentErrors {
                showCorrectionTransferError(title: "Sync Failed", message: alertMessage)
            }
        }
    }

    @discardableResult
    private func writeCorrectionsToSyncFile(presentErrors: Bool) -> Bool {
        guard let url = correctionSyncFileURL() else { return true }
        do {
            try validateCorrectionSyncPath(url)
        } catch {
            handleCorrectionSyncRejectedPath(error, presentErrors: presentErrors)
            return false
        }
        do {
            var correctionsToWrite = normalizedTranscriptCorrections(settings.transcriptCorrections)
            if let knownFingerprint = correctionSyncFileFingerprint,
               let currentFingerprint = correctionSyncFingerprint(for: url),
               currentFingerprint != knownFingerprint {
                let remoteCorrections = try TranscriptCorrectionsTransfer.read(from: url)
                let merge = mergedTranscriptCorrectionsForSync(
                    base: correctionSyncBaselineCorrections,
                    local: correctionsToWrite,
                    remote: remoteCorrections
                )
                if !merge.conflictingSources.isEmpty {
                    stopCorrectionSyncAfterConflict(conflictingSources: merge.conflictingSources)
                    log("correction sync stopped after \(merge.conflictingSources.count) conflicting corrections")
                    return false
                }
                // Normalize (cap) the merge result BEFORE it fans out:
                // file, settings, and baseline must all hold the same
                // list. A raw over-cap merge result stored as baseline
                // made capped-out entries look like local deletions on
                // the next merge, silently removing them from the file.
                correctionsToWrite = normalizedTranscriptCorrections(merge.corrections)
                if let storeError = settings.storeTranscriptCorrections(correctionsToWrite) {
                    throw storeError
                }
            }

            let writtenData = try TranscriptCorrectionsTransfer.write(correctionsToWrite, to: url)
            // Fingerprint the exact bytes written, not a re-read of the
            // file: a sync provider replacing the file in the re-read
            // window would have its change fingerprinted as ours and
            // swallowed until the next local edit.
            correctionSyncFileFingerprint = correctionSyncFingerprint(forWrittenData: writtenData, at: url)
            correctionSyncBaselineCorrections = correctionsToWrite
            log("correction sync wrote \(correctionsToWrite.count) corrections")
            return true
        } catch {
            log("correction sync write failed: \(error)")
            if presentErrors {
                showCorrectionTransferError(title: "Sync Failed", error: error)
            }
            return false
        }
    }

    private func handleCorrectionSyncRejectedPath(_ error: Error, presentErrors: Bool) {
        log("correction sync rejected path: \(error)")
        guard shouldStopCorrectionSync(afterPathValidationError: error) else {
            if presentErrors {
                showCorrectionTransferError(title: "Sync Failed", error: error)
            }
            return
        }

        stopCorrectionSyncAfterRejectedPath(error: error, presentErrors: presentErrors)
    }

    private func stopCorrectionSyncAfterConflict(conflictingSources: [String]) {
        settings.transcriptCorrectionsSyncFile = ""
        correctionSyncTimer?.invalidate()
        correctionSyncTimer = nil
        correctionSyncFileFingerprint = nil
        correctionSyncBaselineCorrections = []
        rebuildMenu()

        let exampleCount = min(conflictingSources.count, 3)
        let examples = conflictingSources.prefix(exampleCount).joined(separator: "\n")
        let remaining = conflictingSources.count - exampleCount
        let remainingText = remaining > 0 ? "\n…and \(remaining) more." : ""
        showAppForModal()
        showCorrectionTransferError(
            title: "Text Correction Sync Conflict",
            message: """
            The sync file changed before this Mac wrote its latest text correction edits. Presspeech kept the corrections on this Mac and stopped syncing so it would not overwrite the file.

            Reconnect the sync file after importing or resolving the conflicting correction\(conflictingSources.count == 1 ? "" : "s"):
            \(examples)\(remainingText)
            """
        )
    }

    private func stopCorrectionSyncAfterRejectedPath(error: Error, presentErrors: Bool) {
        settings.transcriptCorrectionsSyncFile = ""
        correctionSyncTimer?.invalidate()
        correctionSyncTimer = nil
        correctionSyncFileFingerprint = nil
        correctionSyncBaselineCorrections = []
        log("correction sync stopped after rejected path")
        rebuildMenu()

        if presentErrors {
            showCorrectionTransferError(
                title: "Text Correction Sync Stopped",
                message: """
                Presspeech stopped syncing because the selected corrections file is no longer safe to use.

                \(error.localizedDescription)
                """
            )
        }
    }

    private func showCorrectionTransferError(title: String, error: Error) {
        showCorrectionTransferError(title: title, message: error.localizedDescription)
    }

    private func showCorrectionTransferError(title: String, message: String) {
        showAppForModal()
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = title
        alert.informativeText = message
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    private func showCorrectionEditor(existing: TranscriptCorrection?,
                                      mode: CorrectionEditorMode,
                                      prefillSource: String = "") -> TranscriptCorrection? {
        showAppForModal()
        let alert = NSAlert()
        let sourceTitle: String
        switch mode {
        case .correction:
            sourceTitle = "Heard"
            alert.messageText = existing == nil ? "Add Dictionary Correction" : "Edit Dictionary Rule"
            alert.informativeText = "Add the text Presspeech heard, then the exact text it should paste instead."
        case .shortcut:
            sourceTitle = "When you say"
            alert.messageText = "Add Voice Shortcut"
            alert.informativeText = "Choose a phrase to say and the exact text Presspeech should paste. Shortcuts are deterministic and stay on this Mac."
        }
        alert.addButton(withTitle: "Save")
        alert.addButton(withTitle: "Cancel")

        let viewWidth: CGFloat = 520
        let labelHeight: CGFloat = 18
        let fieldHeight: CGFloat = 76
        let viewHeight: CGFloat = (labelHeight * 2) + (fieldHeight * 2) + 24
        let accessory = NSView(frame: NSRect(x: 0, y: 0, width: viewWidth, height: viewHeight))

        let sourceLabel = NSTextField(labelWithString: sourceTitle)
        sourceLabel.font = .systemFont(ofSize: 12, weight: .medium)
        sourceLabel.frame = NSRect(x: 0, y: viewHeight - labelHeight, width: viewWidth, height: labelHeight)

        let sourceEditor = correctionTextEditor(
            frame: NSRect(x: 0, y: viewHeight - labelHeight - fieldHeight, width: viewWidth, height: fieldHeight),
            text: existing?.source ?? prefillSource.trimmingCharacters(in: .whitespacesAndNewlines)
        )
        sourceEditor.textView.setAccessibilityLabel(sourceTitle)

        let replacementLabel = NSTextField(labelWithString: "Paste")
        replacementLabel.font = .systemFont(ofSize: 12, weight: .medium)
        replacementLabel.frame = NSRect(x: 0, y: fieldHeight + 6, width: viewWidth, height: labelHeight)

        let replacementEditor = correctionTextEditor(
            frame: NSRect(x: 0, y: 0, width: viewWidth, height: fieldHeight),
            text: existing?.replacement ?? ""
        )
        replacementEditor.textView.setAccessibilityLabel("Paste")

        accessory.addSubview(sourceLabel)
        accessory.addSubview(sourceEditor.scrollView)
        accessory.addSubview(replacementLabel)
        accessory.addSubview(replacementEditor.scrollView)
        alert.accessoryView = accessory

        let controller = CorrectionEditorModalController(
            alert: alert,
            sourceTitle: sourceTitle,
            sourceEditor: sourceEditor.textView,
            replacementEditor: replacementEditor.textView,
            isEditing: existing != nil,
            existingCorrections: settings.transcriptCorrections
        )
        return controller.run()
    }

    private func correctionTextEditor(frame: NSRect, text: String) -> (scrollView: NSScrollView, textView: NSTextView) {
        let scroll = NSScrollView(frame: frame)
        scroll.borderType = .bezelBorder
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true

        let textView = NSTextView(frame: NSRect(x: 0, y: 0, width: frame.width, height: frame.height))
        textView.font = .systemFont(ofSize: 13)
        textView.string = text
        textView.isRichText = false
        textView.importsGraphics = false
        textView.allowsUndo = true
        textView.textContainerInset = NSSize(width: 6, height: 5)
        textView.minSize = NSSize(width: 0, height: frame.height)
        textView.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude,
                                  height: CGFloat.greatestFiniteMagnitude)
        textView.isVerticallyResizable = true
        textView.isHorizontallyResizable = false
        textView.textContainer?.widthTracksTextView = true
        textView.textContainer?.containerSize = NSSize(width: frame.width,
                                                       height: CGFloat.greatestFiniteMagnitude)
        scroll.documentView = textView
        return (scroll, textView)
    }

    private func saveCorrection(_ correction: TranscriptCorrection, replacing index: Int? = nil) {
        var corrections = settings.transcriptCorrections
        let key = normalizedTranscriptCorrectionSource(correction.source)

        if let index, corrections.indices.contains(index) {
            corrections[index] = correction
            var keepIndex = index
            for i in corrections.indices.reversed() {
                guard i != keepIndex, normalizedTranscriptCorrectionSource(corrections[i].source) == key else { continue }
                corrections.remove(at: i)
                if i < keepIndex { keepIndex -= 1 }
            }
        } else if let duplicate = corrections.firstIndex(where: { normalizedTranscriptCorrectionSource($0.source) == key }) {
            corrections[duplicate] = correction
        } else {
            corrections.append(correction)
        }

        updateTranscriptCorrections(corrections)
        if let savedIndex = settings.transcriptCorrections.firstIndex(where: {
            normalizedTranscriptCorrectionSource($0.source) == key
        }) {
            refreshCorrectionsManager(selecting: savedIndex)
        }
    }

    @objc private func selectHotkey(_ sender: NSMenuItem) {
        guard let kc = sender.representedObject as? Int else { return }
        _ = applyHotkeyChoice(hotkeyChoice(forKeycode: CGKeyCode(kc)))
    }

    @objc private func recordHotkeyClicked(_ sender: NSMenuItem) {
        showHotkeyRecorder()
    }

    @objc private func resetHotkeyClicked(_ sender: NSMenuItem) {
        if applyHotkeyChoice(hotkeyChoice(forKeycode: DEFAULT_HOTKEY_KEYCODE)) {
            log("HotkeyListener: reset hotkey to default")
        }
    }

    private func applyHotkeyChoice(_ choice: HotkeyChoice) -> Bool {
        let previous = hotkey.hotkey

        guard let recordable = recordableHotkeyChoice(forKeycode: choice.keycode) else {
            if case .rejected(let message) = hotkeyPreferenceUpdateResult(
                requested: choice,
                previous: previous,
                persistedKeycode: previous.keycode
            ) {
                showHotkeyRecordError(message)
            }
            return false
        }

        settings.hotkeyKeycode = recordable.keycode
        hotkey.setHotkey(recordable)
        hotkeyTestSucceeded = false

        switch hotkeyPreferenceUpdateResult(
            requested: recordable,
            previous: previous,
            persistedKeycode: settings.hotkeyKeycode
        ) {
        case .saved:
            rebuildMenu()
            updateSetupChecklist()
            return true
        case .rejected(let message):
            showHotkeyRecordError(message)
            return false
        case .rolledBack(let previous, let message):
            settings.hotkeyKeycode = previous.keycode
            hotkey.setHotkey(previous)
            showHotkeyRecordError(message)
            rebuildMenu()
            return false
        }
    }

    private func showHotkeyRecorder() {
        guard !isRecording, !isBusy, !isTerminating else { return }
        showAppForModal()

        let alert = NSAlert()
        alert.messageText = "Record Hotkey"
        alert.informativeText = "Press a right-side modifier key or an F-key, then confirm your choice. On an Apple keyboard, you may need to hold Fn to send an F-key."
        alert.addButton(withTitle: "Use Selected")
        alert.addButton(withTitle: "Cancel")
        let useButton = alert.buttons[0]
        useButton.isEnabled = false

        let status = NSTextField(labelWithString: "Waiting for key…")
        status.font = .systemFont(ofSize: 13)
        status.textColor = .secondaryLabelColor
        status.lineBreakMode = .byWordWrapping
        status.maximumNumberOfLines = 0
        status.frame = NSRect(x: 0, y: 0, width: 380, height: 42)
        alert.accessoryView = status

        let shouldRestoreHotkeyTap = isReady
        if shouldRestoreHotkeyTap {
            hotkey.stop()
        }

        var selected: HotkeyChoice?
        var monitor: Any?
        monitor = NSEvent.addLocalMonitorForEvents(matching: [.keyDown, .flagsChanged]) { event in
            let snapshot = HotkeyEventSnapshot(
                typeRawValue: event.type == .flagsChanged
                    ? CGEventType.flagsChanged.rawValue
                    : CGEventType.keyDown.rawValue,
                keycode: CGKeyCode(event.keyCode),
                flagsRawValue: event.cgEvent?.flags.rawValue ?? 0,
                isAutoRepeat: event.isARepeat
            )
            if hotkeyRecorderShouldPassThrough(snapshot) {
                return event
            }
            switch hotkeyRecordingDecision(for: snapshot) {
            case .accept(let choice):
                selected = choice
                status.stringValue = "Selected: \(choice.name). Choose Use Selected to save it."
                useButton.isEnabled = true
                return nil
            case .reject(let message):
                status.stringValue = message
                NSSound.beep()
                return nil
            case .ignore:
                return nil
            }
        }
        defer {
            if let monitor {
                NSEvent.removeMonitor(monitor)
            }
            let restartSucceeded: Bool
            if shouldRestoreHotkeyTap && !isTerminating {
                restartSucceeded = hotkey.start()
            } else {
                restartSucceeded = false
            }
            switch hotkeyRecorderRestartAction(
                shouldRestoreHotkeyTap: shouldRestoreHotkeyTap,
                isTerminating: isTerminating,
                restartSucceeded: restartSucceeded
            ) {
            case .none, .restoredListener:
                break
            case .recordFailure:
                recordStartupFailure(
                    stage: .hotkeyListener,
                    error: NSError(
                        domain: "Presspeech",
                        code: -5,
                        userInfo: [
                            NSLocalizedDescriptionKey: "The hotkey listener could not restart after recording a hotkey."
                        ]
                    ),
                    reason: "hotkey recorder"
                )
            }
        }

        let response = alert.runModal()
        guard response == .alertFirstButtonReturn,
              let selected else { return }
        if applyHotkeyChoice(selected) {
            log("HotkeyListener: recorded hotkey → \(selected.name)")
        }
    }

    private func showHotkeyRecordError(_ message: String) {
        showAppForModal()
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "Hotkey Not Changed"
        alert.informativeText = message
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    @objc private func selectTriggerMode(_ sender: NSMenuItem) {
        guard let raw = sender.representedObject as? String,
              let m = TriggerMode(rawValue: raw) else { return }
        settings.triggerMode = m
        hotkey.setTriggerMode(m)
        rebuildMenu()
    }

    @objc private func selectPasteSuffix(_ sender: NSMenuItem) {
        guard let raw = sender.representedObject as? String,
              let suffix = PasteSuffix(rawValue: raw) else { return }
        settings.pasteSuffix = suffix
        rebuildMenu()
    }

    @objc private func selectDictationLanguage(_ sender: NSMenuItem) {
        guard let raw = sender.representedObject as? String,
              let lang = DictationLanguage(rawValue: raw) else { return }
        settings.dictationLanguage = lang
        rebuildMenu()
    }

    @objc private func selectRecentTranscriptLimit(_ sender: NSMenuItem) {
        guard let raw = sender.representedObject as? String,
              let limit = RecentTranscriptLimit(rawValue: raw) else { return }
        settings.recentTranscriptLimit = limit
        applyRecentTranscriptLimit()
        if history.isEmpty, dictationNotice?.requiresInMemoryHistory == true {
            clearDictationNotice()
            if isReady, !isRecording, !isBusy, !isTerminating {
                setMenuBarState(.idle)
            }
        }
        rebuildMenu()
    }

    @objc private func selectMaximumRecordingLength(_ sender: NSMenuItem) {
        guard !isRecording,
              let seconds = sender.representedObject as? Int else { return }
        settings.maxRecordingSeconds = TimeInterval(seconds)
        rebuildMenu()
    }

    @objc private func toggleRecordingWaveform(_ sender: NSMenuItem) {
        settings.showRecordingWaveform.toggle()
        sender.state = settings.showRecordingWaveform ? .on : .off
        if settings.showRecordingWaveform, isRecording {
            showRecordingHUD(mode: .recording, level: recordingVisualLevel)
        } else {
            hideRecordingHUD()
        }
    }

    @objc private func toggleMute(_ sender: NSMenuItem) {
        settings.muteWhileRecording.toggle()
        sender.state = settings.muteWhileRecording ? .on : .off
    }

    @objc private func toggleRemoveFillerWords(_ sender: NSMenuItem) {
        settings.removeFillerWords.toggle()
        sender.state = settings.removeFillerWords ? .on : .off
    }

    @objc private func toggleSpokenFormattingCommands(_ sender: NSMenuItem) {
        settings.spokenFormattingCommands.toggle()
        sender.state = settings.spokenFormattingCommands ? .on : .off
    }

    @objc private func toggleFeedbackSounds(_ sender: NSMenuItem) {
        settings.playFeedbackSounds.toggle()
        sender.state = settings.playFeedbackSounds ? .on : .off
    }

    @objc private func toggleRestoreClipboard(_ sender: NSMenuItem) {
        settings.restoreClipboardAfterPaste.toggle()
        sender.state = settings.restoreClipboardAfterPaste ? .on : .off
    }

    @objc private func toggleDock(_ sender: NSMenuItem) {
        setShowInDock(!settings.showInDock)
    }

    @objc private func toggleDockFromSetup(_ sender: NSButton) {
        setShowInDock(sender.state == .on)
    }

    private func setShowInDock(_ showInDock: Bool) {
        guard settings.showInDock != showInDock else { return }
        settings.showInDock = showInDock
        NSApp.setActivationPolicy(showInDock ? .regular : .accessory)
        log("Dock access \(showInDock ? "enabled" : "disabled")")
        rebuildMenu()
    }

    @objc private func toggleLaunchAtLogin(_ sender: NSMenuItem) {
        do {
            switch launchAtLoginAction(for: SMAppService.mainApp.status) {
            case .unregister:
                try SMAppService.mainApp.unregister()
                log("launch at login disabled")
            case .register:
                try SMAppService.mainApp.register()
                log("launch at login enabled")
            case .openSystemSettings:
                SMAppService.openSystemSettingsLoginItems()
                log("launch at login approval opened in System Settings")
            }
        } catch {
            showLaunchAtLoginError(error)
        }
        rebuildMenu()
    }

    private func showLaunchAtLoginError(_ error: Error) {
        showAppForModal()
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "Launch at Login couldn't be changed"
        alert.informativeText = "\(error)"
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    @objc private func toggleCheckForUpdates(_ sender: NSMenuItem) {
        settings.checkForUpdates.toggle()
        sender.state = settings.checkForUpdates ? .on : .off
        log("update notifications \(settings.checkForUpdates ? "enabled" : "disabled")")
        if settings.checkForUpdates {
            Task { [weak self] in
                await self?.tickUpdateCheck(source: .settingsToggle)
            }
        } else {
            pendingUpdate = nil
            clearUpdateReminderPause()
            rebuildMenu()
        }
    }

    @objc private func resetSpeechModelCacheClicked(_ sender: NSMenuItem) {
        guard !isRecording,
              !isBusy,
              startupTask == nil,
              !isResettingSpeechModelCache,
              !isSwitchingSpeechModel,
              !isTerminating else { return }

        showAppForModal()
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "Reset Speech Model Cache?"
        let profile = settings.speechModelProfile
        alert.informativeText = profile.cacheResetDetail
        alert.addButton(withTitle: "Reset")
        alert.addButton(withTitle: "Cancel")
        guard alert.runModal() == .alertFirstButtonReturn else { return }

        isResettingSpeechModelCache = true
        prepareForStartupAttempt()
        startupStatusTitle = "Resetting speech model cache…"
        log("ASR: \(profile.shortName) cache reset started")
        rebuildMenu()

        Task { @MainActor in
            await asr.unload()
            let cacheDir = speechModelCacheDirectory(for: profile)
            do {
                let didRemoveCache = try await removeSpeechModelCacheDirectory(cacheDir)
                if didRemoveCache {
                    log("ASR: removed \(profile.shortName) cache \(privacySafeLogPath(cacheDir))")
                } else {
                    log("ASR: \(profile.shortName) cache reset requested; cache was already absent")
                }
                isResettingSpeechModelCache = false
                startStartup(reason: "speech model cache reset")
            } catch {
                isResettingSpeechModelCache = false
                log("ASR: speech model cache reset failed: \(error)")
                showSpeechModelCacheResetError(error)
                startStartup(reason: "speech model cache reset recovery")
            }
        }
    }

    private func showSpeechModelCacheResetError(_ error: Error) {
        showAppForModal()
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "Speech model cache couldn't be reset"
        alert.informativeText = "\(error)"
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    // MARK: - About dialog

    @objc private func showAboutClicked(_ sender: NSMenuItem) {
        showAppForModal()
        let alert = NSAlert()
        alert.messageText = "Presspeech \(currentBundleVersion())"
        alert.informativeText = """
            Lightweight push-to-talk dictation for Apple Silicon Macs.

            Hotkey:  \(hotkey.hotkey.name)
            Mode:    \(TRIGGER_DISPLAY[settings.triggerMode] ?? settings.triggerMode.rawValue)
            Model:   \(settings.speechModelProfile.aboutModelText)

            Local-only dictation. No cloud transcription, no telemetry.
            Network: model download, optional update check and install.
            Permissions: microphone audio, paste-at-cursor, push-to-talk hotkey.

            Maintained by Richard Courtman.
            github.com/rcourtman/presspeech · MIT licensed
            """
        // Use our app icon instead of NSAlert's default exclamation
        // mark. .icns lives in Contents/Resources/Presspeech.icns;
        // NSImage(named:) on Bundle.main resolves it by filename
        // sans extension.
        if let icon = NSImage(named: "Presspeech") {
            alert.icon = icon
        }
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "View on GitHub")
        if alert.runModal() == .alertSecondButtonReturn {
            NSWorkspace.shared.open(GITHUB_REPOSITORY_PAGE)
        }
    }

    // MARK: - Update flow

    private func startUpdateCheckLoop() {
        guard updateCheckLoopTask == nil else { return }
        updateCheckLoopTask = Task.detached { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(UPDATE_CHECK_FIRST_DELAY_SECONDS * 1_000_000_000))
            while !Task.isCancelled {
                await self?.tickUpdateCheck()
                try? await Task.sleep(nanoseconds: UInt64(UPDATE_CHECK_INTERVAL_SECONDS * 1_000_000_000))
            }
        }
    }

    /// Silent update check: failures are recorded in diagnostics but
    /// never alerted. `source` distinguishes the periodic timer tick
    /// from the user re-enabling the settings toggle.
    private func tickUpdateCheck(source: UpdateCheckSource = .automatic) async {
        guard settings.checkForUpdates else { return }
        let outcome = await UpdateCheck.fetchLatest()
        await MainActor.run {
            self.recordUpdateCheck(release: try? outcome.get(), source: source)
            guard let release = try? outcome.get() else { return }
            self.handleFetchedRelease(release)
        }
    }

    private func recordUpdateCheck(release: GitHubRelease?, source: UpdateCheckSource) {
        let skippedVersions = source == .manual ? [] : settings.skippedVersions
        let result = updateCheckResult(
            for: release,
            currentVersion: currentBundleVersion(),
            skippedVersions: skippedVersions
        )
        settings.lastUpdateCheckAt = Date()
        settings.lastUpdateCheckSource = source
        settings.lastUpdateCheckResult = result
        settings.lastUpdateCheckVersion = release?.version ?? ""

        let versionText = release.map { " v\($0.version)" } ?? ""
        log("update check \(source.rawValue): \(result.rawValue)\(versionText)")
    }

    private func handleFetchedRelease(_ release: GitHubRelease) {
        let current = currentBundleVersion()
        guard isNewer(release.version, than: current) else { return }
        if settings.skippedVersions.contains(release.version) {
            log("update available (v\(release.version)) but user skipped — staying quiet")
            return
        }
        let now = Date()
        if shouldSuppressUpdateForReminder(version: release.version,
                                           reminderVersion: reminderPausedUpdateVersion,
                                           reminderUntil: reminderPausedUntil,
                                           now: now) {
            if let reminderPausedUntil {
                log("update available (v\(release.version)) but reminder is paused until \(ISO8601DateFormatter().string(from: reminderPausedUntil))")
            }
            return
        }
        // Same version → the pause expired and the update is re-shown.
        // Newer version → it supersedes the paused one, so the stale
        // pause must not linger in diagnostics alongside the new
        // pending update. (An ACTIVE pause for this exact version
        // already returned above.)
        if shouldClearUpdateReminderPause(fetchedVersion: release.version,
                                          pausedVersion: reminderPausedUpdateVersion) {
            clearUpdateReminderPause()
        }
        log("update available: \(current) → v\(release.version)")
        pendingUpdate = release
        rebuildMenu()
    }


    private func buildUpdateItem(for release: GitHubRelease) -> NSMenuItem {
        let parent = NSMenuItem(title: "Update to v\(release.version)",
                                action: nil, keyEquivalent: "")
        let sub = NSMenu()
        sub.autoenablesItems = false

        let whatsNew = NSMenuItem(title: "What's new…",
                                  action: #selector(whatsNewClicked(_:)),
                                  keyEquivalent: "")
        whatsNew.target = self
        sub.addItem(whatsNew)

        let updateNow = NSMenuItem(title: "Update now…",
                                   action: #selector(updateNowClicked(_:)),
                                   keyEquivalent: "")
        updateNow.target = self
        sub.addItem(updateNow)

        let remindLater = NSMenuItem(title: "Remind me in 24 hours",
                                     action: #selector(remindMeLaterClicked(_:)),
                                     keyEquivalent: "")
        remindLater.target = self
        sub.addItem(remindLater)

        let skip = NSMenuItem(title: "Skip v\(release.version)",
                              action: #selector(skipVersionClicked(_:)),
                              keyEquivalent: "")
        skip.target = self
        sub.addItem(skip)

        parent.submenu = sub
        return parent
    }

    private func showReleaseNotes(for release: GitHubRelease) {
        showAppForModal()
        let alert = NSAlert()
        alert.messageText = "Presspeech v\(release.version)"
        var body = release.body.trimmingCharacters(in: .whitespacesAndNewlines)
        if body.isEmpty { body = "(No release notes available for this version.)" }
        else if body.count > 1500 { body = String(body.prefix(1500)) + "\n\n…" }
        alert.informativeText = body
        alert.addButton(withTitle: "Close")
        alert.addButton(withTitle: "Open in Browser")
        let response = alert.runModal()
        if response == .alertSecondButtonReturn,
           let url = URL(string: release.htmlURL) {
            NSWorkspace.shared.open(url)
        }
    }

    @objc private func whatsNewClicked(_ sender: NSMenuItem) {
        guard let release = pendingUpdate else { return }
        showReleaseNotes(for: release)
    }

    @objc private func updateNowClicked(_ sender: NSMenuItem) {
        guard let release = pendingUpdate else { return }
        startUpdate(for: release)
    }

    @objc private func remindMeLaterClicked(_ sender: NSMenuItem) {
        guard let release = pendingUpdate else { return }
        pauseUpdateReminder(for: release)
    }

    @objc private func skipVersionClicked(_ sender: NSMenuItem) {
        guard let release = pendingUpdate else { return }
        var skipped = settings.skippedVersions
        if !skipped.contains(release.version) {
            skipped.append(release.version)
            settings.skippedVersions = skipped
            log("user skipped v\(release.version); suppressing until a newer release")
        }
        pendingUpdate = nil
        clearUpdateReminderPause()
        rebuildMenu()
    }

    @objc private func checkForUpdatesClicked(_ sender: NSMenuItem) {
        guard !isCheckingForUpdates else { return }
        isCheckingForUpdates = true
        rebuildMenu()
        manualUpdateCheckTask = Task { [weak self] in
            let outcome = await UpdateCheck.fetchLatest()
            guard !Task.isCancelled,
                  let self,
                  !self.isTerminating else { return }
            self.manualUpdateCheckTask = nil
            self.recordUpdateCheck(release: try? outcome.get(), source: .manual)
            self.finishManualUpdateCheck(outcome)
        }
    }

    private func finishManualUpdateCheck(_ outcome: Result<GitHubRelease, UpdateCheckFailure>) {
        manualUpdateCheckTask = nil
        isCheckingForUpdates = false
        let release: GitHubRelease
        switch outcome {
        case .failure(let failure):
            rebuildMenu()
            showUpdateCheckFailedAlert(failure)
            return
        case .success(let fetched):
            release = fetched
        }

        let current = currentBundleVersion()
        guard isNewer(release.version, than: current) else {
            if pendingUpdate?.version == release.version {
                pendingUpdate = nil
            }
            rebuildMenu()
            showUpToDateAlert(currentVersion: current)
            return
        }

        if settings.skippedVersions.contains(release.version) {
            settings.skippedVersions = settings.skippedVersions.filter { $0 != release.version }
        }
        clearUpdateReminderPause()
        pendingUpdate = release
        rebuildMenu()
        showUpdateAvailableAlert(for: release, currentVersion: current)
    }

    private func showUpdateAvailableAlert(for release: GitHubRelease, currentVersion: String) {
        showAppForModal()
        let alert = NSAlert()
        alert.messageText = "Presspeech v\(release.version) is available"
        alert.informativeText = "You're running v\(currentVersion). Nothing is installed unless you choose Update Now."
        alert.addButton(withTitle: "Update Now")
        alert.addButton(withTitle: "What's New")
        // Dismissing pauses reminders for 24 h (and hides the update
        // menu item), so the button must say so — "Later" implied a
        // consequence-free dismissal.
        alert.addButton(withTitle: "Remind Me in 24 Hours")
        let response = alert.runModal()
        if response == .alertFirstButtonReturn {
            startUpdate(for: release)
        } else if response == .alertSecondButtonReturn {
            showReleaseNotes(for: release)
        } else {
            pauseUpdateReminder(for: release)
        }
    }

    private func pauseUpdateReminder(for release: GitHubRelease) {
        setUpdateReminderPause(version: release.version,
                               until: Date().addingTimeInterval(UPDATE_REMIND_LATER_SECONDS))
        pendingUpdate = nil
        if let reminderPausedUntil {
            log("user chose remind later for v\(release.version); paused until \(ISO8601DateFormatter().string(from: reminderPausedUntil))")
        }
        rebuildMenu()
    }

    // MARK: "Remind me later" pause state
    //
    // The in-memory fields drive menu/diagnostics decisions; the
    // Settings copies survive relaunches. The pause used to be
    // memory-only, so quitting inside the 24 h window re-prompted the
    // user ~30 s after the next launch. These two helpers are the ONLY
    // write paths so memory and defaults can never disagree.

    private func setUpdateReminderPause(version: String, until: Date) {
        reminderPausedUpdateVersion = version
        reminderPausedUntil = until
        settings.updateReminderPausedVersion = version
        settings.updateReminderPausedUntil = until
    }

    private func clearUpdateReminderPause() {
        reminderPausedUpdateVersion = nil
        reminderPausedUntil = nil
        settings.updateReminderPausedVersion = nil
        settings.updateReminderPausedUntil = nil
    }

    /// Restores a persisted pause at launch. Either half missing or
    /// corrupt (the validated Settings accessors degrade those to nil)
    /// means no pause: clear the leftover half rather than carrying
    /// incoherent state.
    private func restoreUpdateReminderPause() {
        guard let version = settings.updateReminderPausedVersion,
              let until = settings.updateReminderPausedUntil else {
            clearUpdateReminderPause()
            return
        }
        reminderPausedUpdateVersion = version
        reminderPausedUntil = until
    }

    private func showUpToDateAlert(currentVersion: String) {
        showAppForModal()
        let alert = NSAlert()
        alert.messageText = "Presspeech is up to date"
        alert.informativeText = "You're running v\(currentVersion)."
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    private func showUpdateCheckFailedAlert(_ failure: UpdateCheckFailure) {
        showAppForModal()
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "Couldn't check for updates"
        alert.informativeText = manualUpdateCheckFailureText(failure)
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    private func startUpdate(for release: GitHubRelease) {
        guard !isPreparingUpdate else {
            log("update click ignored: update preparation already in flight")
            return
        }
        guard let brew = findBrew() else {
            showManualUpdateRequired(for: release, reason: "Homebrew was not found on this Mac.")
            return
        }
        isPreparingUpdate = true
        isBrewInstall(brewPath: brew) { [weak self] isBrewManaged in
            guard let self else { return }
            self.isPreparingUpdate = false
            guard !self.isTerminating else { return }
            guard isBrewManaged else {
                self.showManualUpdateRequired(
                    for: release,
                    reason: "This copy of Presspeech was not detected as a Homebrew-managed app in /Applications."
                )
                return
            }
            self.spawnUpdateHelper(brewPath: brew, targetVersion: release.version)
        }
    }

    private func showManualUpdateRequired(for release: GitHubRelease, reason: String) {
        log("update click: manual update required: \(reason)")
        showAppForModal()
        let alert = NSAlert()
        alert.alertStyle = .informational
        alert.messageText = "Manual update needed"
        alert.informativeText = """
        \(reason)

        Open the release page, or if this Mac uses Homebrew run:

        brew update && brew upgrade --cask \(HOMEBREW_CASK_TOKEN)
        """
        alert.addButton(withTitle: "Open Release Page")
        alert.addButton(withTitle: "Close")
        let response = alert.runModal()
        if response == .alertFirstButtonReturn,
           let url = URL(string: release.htmlURL) {
            NSWorkspace.shared.open(url)
        }
    }

    private func showUpdateCouldNotStart(detail: String) {
        log("update: could not start helper: \(detail)")
        showAppForModal()
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "Update couldn't start"
        alert.informativeText = """
        \(detail)

        You can still update from Terminal:

        brew update && brew upgrade --cask \(HOMEBREW_CASK_TOKEN)
        """
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    /// `brew list --cask` routinely takes seconds. With the active
    /// session-wide event tap on the main run loop, a synchronous
    /// waitUntilExit() here would stall every keystroke system-wide
    /// (and a >1 s stall makes macOS disable the tap), so the check
    /// runs on a background queue and reports back to the main actor.
    private static let brewPreflightQueue = DispatchQueue(label: "PresspeechBrewPreflight",
                                                          qos: .userInitiated)

    private func isBrewInstall(brewPath: String,
                               completion: @escaping @MainActor @Sendable (Bool) -> Void) {
        guard Bundle.main.bundlePath == INSTALLED_APP_BUNDLE_PATH else {
            completion(false)
            return
        }

        Self.brewPreflightQueue.async {
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: brewPath)
            proc.arguments = ["list", "--cask", "--versions", HOMEBREW_CASK_INSTALLED_TOKEN]
            proc.environment = updateProcessEnvironment()
            proc.standardOutput = Pipe()
            proc.standardError = Pipe()
            let isBrewManaged: Bool
            do {
                try proc.run()
                proc.waitUntilExit()
                isBrewManaged = proc.terminationStatus == 0
            } catch {
                log("update: brew install check failed: \(error)")
                isBrewManaged = false
            }
            Task { @MainActor in completion(isBrewManaged) }
        }
    }

    private func findBrew() -> String? {
        for path in ["/opt/homebrew/bin/brew", "/usr/local/bin/brew"] {
            if FileManager.default.isExecutableFile(atPath: path) { return path }
        }
        return nil
    }

    private func launchUpdateProgressApp(statePath: String,
                                         logPath: String,
                                         targetVersion: String) throws -> String {
        let sourceAppURL = Bundle.main.bundleURL
        guard sourceAppURL.pathExtension == "app",
              let executableName = Bundle.main.executableURL?.lastPathComponent else {
            throw posixError(EINVAL)
        }

        let progressAppURL = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
            .appendingPathComponent("\(UPDATE_PROGRESS_APP_PREFIX)\(UUID().uuidString).app",
                                    isDirectory: true)
        try FileManager.default.copyItem(at: sourceAppURL, to: progressAppURL)

        let executableURL = progressAppURL
            .appendingPathComponent("Contents", isDirectory: true)
            .appendingPathComponent("MacOS", isDirectory: true)
            .appendingPathComponent(executableName)

        let proc = Process()
        proc.executableURL = executableURL
        proc.arguments = [
            UPDATE_PROGRESS_ARGUMENT,
            statePath,
            logPath,
            targetVersion,
            progressAppURL.path,
        ]
        proc.environment = systemToolProcessEnvironment()

        do {
            try proc.run()
            return progressAppURL.path
        } catch {
            try? FileManager.default.removeItem(at: progressAppURL)
            throw error
        }
    }

    private func spawnUpdateHelper(brewPath: String, targetVersion: String) {
        let statePath: String
        do {
            statePath = try createPrivateUpdateProgressStateFile()
        } catch {
            log("update: creating progress state failed: \(error.localizedDescription)")
            showUpdateCouldNotStart(detail: "Presspeech couldn't prepare the update progress window.")
            return
        }

        // Detached shell helper refreshes Homebrew, downloads the cask,
        // waits for THIS process to exit, upgrades/reinstalls the app,
        // verifies the installed bundle version, then re-opens
        // /Applications/Presspeech.app. We can't run the install step
        // in-process because it replaces the bundle we're executing from.
        let script = updateHelperScript(pid: getpid(),
                                        brewPath: brewPath,
                                        targetVersion: targetVersion,
                                        statePath: statePath)
        // Use NSTemporaryDirectory() (per-user, typically /var/folders/…/T/)
        // instead of /tmp, and create the script with O_EXCL/O_NOFOLLOW at
        // mode 0600 so an existing leaf path is never overwritten or followed.
        // bash is invoked as `/bin/bash <path>` so the execute bit is not
        // required.
        let helperPath: String
        do {
            helperPath = try writePrivateUpdateHelperScript(script)
        } catch {
            try? FileManager.default.removeItem(atPath: statePath)
            log("update: writing helper failed: \(error.localizedDescription)")
            showUpdateCouldNotStart(detail: "Presspeech couldn't write the update helper script.")
            return
        }
        let helperLog: PrivateOutputFile
        do {
            helperLog = try openPrivateUpdateHelperLog()
        } catch {
            try? FileManager.default.removeItem(atPath: helperPath)
            try? FileManager.default.removeItem(atPath: statePath)
            log("update: opening helper log failed: \(error.localizedDescription)")
            showUpdateCouldNotStart(detail: "Presspeech couldn't open the update helper log.")
            return
        }

        let progressAppPath: String
        do {
            progressAppPath = try launchUpdateProgressApp(statePath: statePath,
                                                          logPath: helperLog.path,
                                                          targetVersion: targetVersion)
        } catch {
            try? FileManager.default.removeItem(atPath: helperPath)
            try? FileManager.default.removeItem(atPath: statePath)
            helperLog.handle.closeFile()
            log("update: launching progress app failed: \(error.localizedDescription)")
            showUpdateCouldNotStart(detail: "Presspeech couldn't open the update progress window.")
            return
        }

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/bin/bash")
        proc.arguments = [helperPath]
        proc.environment = updateProcessEnvironment()
        proc.standardOutput = helperLog.handle
        proc.standardError = helperLog.handle
        do {
            try proc.run()
        } catch {
            try? FileManager.default.removeItem(atPath: helperPath)
            helperLog.handle.closeFile()
            try? writePrivateUpdateProgressState(phase: "failed",
                                                 message: "Presspeech couldn't launch the update helper.",
                                                 to: statePath)
            showUpdateCouldNotStart(detail: "Presspeech couldn't launch the update helper.")
            return
        }
        log("update helper spawned \(privacySafeLogPath(helperPath)), progress app \(privacySafeLogPath(progressAppPath)), logging to \(privacySafeLogPath(helperLog.path)); quitting for upgrade")
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
            NSApp.terminate(nil)
        }
    }

    // MARK: - TCC stale-state recovery on upgrade

    private func recoverStaleTCCAfterUpgrade() {
        let last = settings.lastSeenVersion
        let current = currentBundleVersion()
        guard !last.isEmpty else {
            // First-ever launch — just record the version. No state
            // to recover.
            settings.lastSeenVersion = current
            return
        }
        guard last != current else { return }
        log("upgrade detected: \(last) → \(current); checking for stale TCC state")
        let bundleID = Bundle.main.bundleIdentifier ?? "com.local.presspeech"
        for p in Permission.allCases {
            if Permissions.isGranted(p) { continue }
            // Fire-and-forget on TCC's serial queue: these resets are
            // best-effort scrubbing of stale DENIED entries, nothing
            // at launch depends on their completion, and the user's
            // first Grant click has its own reset-and-retry path.
            TCC.reset(p, bundleID: bundleID)
        }
        settings.lastSeenVersion = current
    }
}

// MARK: - Entry point

#if DEBUG
private enum SelfTestFailure: Error, CustomStringConvertible {
    case failed(String)

    var description: String {
        switch self {
        case .failed(let message): return message
        }
    }
}

private enum PresspeechSelfTest {
    private final class UnavailablePasteboardDataProvider: NSObject, NSPasteboardItemDataProvider {
        func pasteboard(_ pasteboard: NSPasteboard?,
                        item: NSPasteboardItem,
                        provideDataForType type: NSPasteboard.PasteboardType) {
            // Intentionally leave the advertised representation unavailable.
        }
    }

    static func run(arguments: [String]) -> Int32? {
        guard arguments.count >= 2, arguments[0] == "--self-test" else { return nil }
        guard arguments.count == 2 else { return fail("usage") }

        switch arguments[1] {
        case "hotkey":
            return runSuite("hotkey", testHotkey)
        case "readiness":
            return runSuite("readiness", testReadiness)
        case "paste":
            return runSuite("paste", testPasteSuffixFormatting)
        case "history":
            return runSuite("history", testRecentTranscriptLimit)
        case "corrections":
            return runSuite("corrections", testTranscriptCorrections)
        case "fillers":
            return runSuite("fillers", testFillerWordRemoval)
        case "audio-level":
            return runSuite("audio-level", testAudioLevelMetering)
        case "hud-accessibility":
            return runSuite("hud-accessibility", testRecordingHUDAccessibility)
        case "audio-conversion":
            return runSuite("audio-conversion", testAudioConversion)
        case "audio-input":
            return runSuite("audio-input", testAudioInputDeviceFiltering)
        case "model-status":
            return runSuite("model-status", testSpeechModelStartupStatus)
        case "audio-route":
            return runSuite("audio-route", testAudioRouteChangeDecision)
        case "recording-lifecycle":
            return runSuite("recording-lifecycle", testRecordingLifecycle)
        case "silent-capture":
            return runSuite("silent-capture", testSilentCaptureHint)
        case "power-state":
            return runSuite("power-state", testPowerStateRecoveryDecision)
        case "model-integrity":
            return runSuite("model-integrity", testModelIntegrity)
        case "update":
            return runSuite("update", testUpdate)
        case "hostile-env":
            return runSuite("hostile-env", testHostileRegistryEnvDetection)
        case "logging":
            return runSuite("logging", testPrivateLogAppend)
        case "diagnostics":
            return runSuite("diagnostics", testDiagnostics)
        case "identity-migration":
            return runSuite("identity-migration", testIdentityMigration)
        case "launch-at-login":
            return runSuite("launch-at-login", testLaunchAtLogin)
        case "all":
            return runSuite("all", testAll)
        default:
            return fail("unknown")
        }
    }

    private static func runSuite(_ name: String, _ body: () throws -> Void) -> Int32 {
        do {
            try body()
            print("PASS \(name)")
            return EXIT_SUCCESS
        } catch {
            print("FAIL \(name): \(error)")
            return EXIT_FAILURE
        }
    }

    private static func fail(_ message: String) -> Int32 {
        print("FAIL self-test: \(message)")
        return EXIT_FAILURE
    }

    private static func testAll() throws {
        try testHotkey()
        try testReadiness()
        try testPasteSuffixFormatting()
        try testRecentTranscriptLimit()
        try testTranscriptCorrections()
        try testFillerWordRemoval()
        try testAudioLevelMetering()
        try testRecordingHUDAccessibility()
        try testAudioConversion()
        try testAudioInputDeviceFiltering()
        try testSpeechModelStartupStatus()
        try testAudioRouteChangeDecision()
        try testRecordingLifecycle()
        try testSilentCaptureHint()
        try testPowerStateRecoveryDecision()
        try testModelIntegrity()
        try testUpdate()
        try testHostileRegistryEnvDetection()
        try testPrivateLogAppend()
        try testDiagnostics()
        try testIdentityMigration()
        try testLaunchAtLogin()
    }

    private static func testSilentCaptureHint() throws {
        try expect(noAudioCapturedHint(capturedSampleCount: 0) != nil,
                   equals: true,
                   "a capture with no samples should name the format mismatch")
        try expect(noAudioCapturedHint(capturedSampleCount: 1) == nil,
                   equals: true,
                   "a short but non-empty capture should not blame the format")
        try expect(noAudioCapturedHint(capturedSampleCount: 16_000) == nil,
                   equals: true,
                   "a full clip should not emit the silent-capture hint")
    }

    private static func testLaunchAtLogin() throws {
        try expect(launchAtLoginAction(for: .enabled),
                   equals: .unregister,
                   "an enabled login item should be disabled on click")
        try expect(launchAtLoginAction(for: .requiresApproval),
                   equals: .openSystemSettings,
                   "a pending login item should lead to its approval surface")
        try expect(launchAtLoginAction(for: .notRegistered),
                   equals: .register,
                   "a disabled login item should be registered on click")
        try expect(launchAtLoginAction(for: .notFound),
                   equals: .register,
                   "a missing login item should retry registration and surface any error")
    }

    private static func testRecordingHUDAccessibility() throws {
        let defaults = RecordingHUDAccessibilityOptions(
            reduceMotion: false,
            reduceTransparency: false,
            increaseContrast: false,
            differentiateWithoutColor: false
        )
        try expect(defaults.usesStaticRecordingPresentation,
                   equals: false,
                   "default HUD should retain its level-responsive waveform")
        try expect(defaults.usesOpaqueHUDBackground,
                   equals: false,
                   "default HUD may retain its translucent capsule")
        try expect(defaults.animatesHUDTransitions,
                   equals: true,
                   "default HUD may animate its short transitions")
        try expect(recordingHUDRecordingStatusText(options: defaults, showsCancelHint: true),
                   equals: nil,
                   "default HUD should keep the separate waveform and cancel hint")

        let reducedMotion = RecordingHUDAccessibilityOptions(
            reduceMotion: true,
            reduceTransparency: false,
            increaseContrast: false,
            differentiateWithoutColor: false
        )
        try expect(reducedMotion.usesStaticRecordingPresentation,
                   equals: true,
                   "Reduce Motion should replace the moving waveform with text")
        try expect(reducedMotion.animatesHUDTransitions,
                   equals: false,
                   "Reduce Motion should disable HUD expansion and collapse")
        try expect(recordingHUDRecordingStatusText(options: reducedMotion, showsCancelHint: false),
                   equals: "Recording",
                   "static hold-mode HUD should identify the recording state")
        try expect(recordingHUDRecordingStatusText(options: reducedMotion, showsCancelHint: true),
                   equals: "Recording — Esc cancels",
                   "static toggle-mode HUD should preserve its cancellation instruction")

        let visualContrast = RecordingHUDAccessibilityOptions(
            reduceMotion: false,
            reduceTransparency: true,
            increaseContrast: true,
            differentiateWithoutColor: true
        )
        try expect(visualContrast.usesOpaqueHUDBackground,
                   equals: true,
                   "Reduce Transparency or Increase Contrast should make the HUD background opaque")
        try expect(visualContrast.usesStaticRecordingPresentation,
                   equals: true,
                   "Differentiate Without Color should add a recording text label")
    }

    private static func testIdentityMigration() throws {
        let migrated = settingsDomainAfterIdentityMigration(
            current: [
                "hotkey_keycode": 58,
                "new_only": true,
            ],
            legacy: [
                "hotkey_keycode": 61,
                "trigger_mode": "hold",
            ]
        )
        try expect(migrated["hotkey_keycode"] as? Int,
                   equals: 58,
                   "identity migration should preserve settings already written under the current identity")
        try expect(migrated["trigger_mode"] as? String,
                   equals: "hold",
                   "identity migration should copy missing settings from the former domain")
        try expect(migrated["new_only"] as? Bool,
                   equals: true,
                   "identity migration should retain current-only settings")

        let formerURL = URL(fileURLWithPath: "/tmp/Corrections.\(LEGACY_CORRECTIONS_FILE_EXTENSION)")
        try expect(
            identityMigrationDestinationURL(forLegacyCorrectionURL: formerURL)?.lastPathComponent,
            equals: "Corrections.\(CORRECTIONS_FILE_EXTENSION)",
            "identity migration should rename configured correction files"
        )
        try expect(
            identityMigrationDestinationURL(
                forLegacyCorrectionURL: URL(fileURLWithPath: "/tmp/Corrections.json")
            ),
            equals: nil,
            "identity migration should leave unrelated file extensions alone"
        )
    }

    private static func testPrivateLogAppend() throws {
        try expect(
            privacySafeLogPath("/Users/example/Documents/Presspeech Diagnostics.txt"),
            equals: "Presspeech Diagnostics.txt",
            "log path labels should omit parent directories"
        )
        try expect(
            privacySafeLogPath("/"),
            equals: "<local path>",
            "log path labels should fall back when no filename is available"
        )
        try expect(
            privacySafeBundlePath("/Applications/Presspeech.app"),
            equals: "/Applications/Presspeech.app",
            "bundle path labels should keep the canonical install path"
        )
        try expect(
            privacySafeBundlePath("/Users/example/Downloads/Presspeech.app"),
            equals: "Presspeech.app",
            "bundle path labels should omit parent directories for nonstandard installs"
        )

        let fm = FileManager.default
        let root = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("presspeech-log-test-\(UUID().uuidString)", isDirectory: true)
        try fm.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? fm.removeItem(at: root) }

        let logFile = root.appendingPathComponent("Presspeech.log")
        try appendPrivateLogData(Data("one\n".utf8), to: logFile)
        try appendPrivateLogData(Data("two\n".utf8), to: logFile)

        let attrs = try fm.attributesOfItem(atPath: logFile.path)
        let permissions = (attrs[.posixPermissions] as? NSNumber)?.intValue ?? -1
        try expect(permissions & 0o777,
                   equals: 0o600,
                   "log file should be private to the current user")
        try expect(
            String(data: try Data(contentsOf: logFile), encoding: .utf8),
            equals: "one\ntwo\n",
            "log appends should preserve existing content"
        )

        let target = root.appendingPathComponent("target.log")
        try Data("target\n".utf8).write(to: target)
        let link = root.appendingPathComponent("link.log")
        try fm.createSymbolicLink(at: link, withDestinationURL: target)

        var symlinkRejected = false
        do {
            try appendPrivateLogData(Data("bad\n".utf8), to: link)
        } catch {
            symlinkRejected = true
        }
        try expect(symlinkRejected,
                   equals: true,
                   "log appends should reject leaf symlinks")
        try expect(
            String(data: try Data(contentsOf: target), encoding: .utf8),
            equals: "target\n",
            "log symlink rejection should leave the target untouched"
        )

        let hardlinkTarget = root.appendingPathComponent("hardlink-target.log")
        try Data("hardlink target\n".utf8).write(to: hardlinkTarget)
        let hardlink = root.appendingPathComponent("hardlink.log")
        guard Darwin.link(hardlinkTarget.path, hardlink.path) == 0 else {
            throw currentPOSIXError()
        }

        var hardlinkRejected = false
        do {
            try appendPrivateLogData(Data("bad\n".utf8), to: hardlink)
        } catch {
            hardlinkRejected = true
        }
        try expect(hardlinkRejected,
                   equals: true,
                   "log appends should reject hard-linked files")
        try expect(
            String(data: try Data(contentsOf: hardlinkTarget), encoding: .utf8),
            equals: "hardlink target\n",
            "log hard-link rejection should leave the target untouched"
        )
    }

    private static func testDiagnostics() throws {
        let transcriptSecret = "secret dictated phrase 58A03D"
        let correctionSecret = "private correction replacement 9F42"
        let report = diagnosticsReportText(
            from: DiagnosticsReportSnapshot(
                generated: "2026-05-28T10:00:00Z",
                appVersion: "9.8.7",
                appBuild: "123",
                macOS: "Version 26.0",
                bundleID: "com.local.presspeech",
                bundlePath: "/Applications/Presspeech.app",
                installKind: "Applications app",
                status: "Hold Right Option to dictate",
                startup: "Runtime ready",
                speechModelReady: true,
                coreRuntimeReady: true,
                readyForDictation: true,
                recordingActive: false,
                transcribing: false,
                memoryLines: ["Resident: 100 MB"],
                permissionLines: ["Microphone: granted", "Accessibility: granted", "Input Monitoring: granted"],
                settingLines: [
                    "Speech model: Multilingual (Parakeet TDT v3)",
                    "Language: Auto-detect",
                    "Recent transcripts: Last 5 (1 in memory)",
                    "Text corrections: 1 configured",
                    "Text correction sync: configured",
                ],
                updateLines: ["Pending update: none"],
                microphoneLines: ["Selected: System default", "Available inputs: none reported"],
                logPath: "~/Library/Logs/Presspeech.log",
                recentLogLines: ["[10:00:00] release: 1.23 s captured, transcribing"]
            )
        )
        try expect(report.contains(transcriptSecret), equals: false,
                   "diagnostics report should not include transcript contents")
        try expect(report.contains(correctionSecret), equals: false,
                   "diagnostics report should not include text correction contents")
        try expect(report.contains("Text corrections: 1 configured"), equals: true,
                   "diagnostics report should include correction counts")
        try expect(report.contains("Speech model: Multilingual (Parakeet TDT v3)"), equals: true,
                   "diagnostics report should include the speech model")
        try expect(report.contains("Recent log lines:"), equals: true,
                   "diagnostics report should include the recent log section")
        try expect(report.contains("Privacy: transcript text and text-correction contents are not included."),
                   equals: true,
                   "diagnostics report should state the privacy boundary")

        let fm = FileManager.default
        let root = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("presspeech-diagnostics-test-\(UUID().uuidString)", isDirectory: true)
        try fm.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? fm.removeItem(at: root) }

        let logFile = root.appendingPathComponent("Presspeech.log")
        for line in 1...6 {
            try appendPrivateLogData(Data("[10:00:0\(line)] line \(line)\n".utf8), to: logFile)
        }
        try expect(
            try recentDiagnosticLogLines(from: logFile, maxBytes: 4096, maxLines: 3),
            equals: ["[10:00:04] line 4", "[10:00:05] line 5", "[10:00:06] line 6"],
            "diagnostic log tail should return the newest bounded lines"
        )

        let target = root.appendingPathComponent("target.log")
        try Data("[10:00:00] target\n".utf8).write(to: target)
        let symlink = root.appendingPathComponent("symlink.log")
        try fm.createSymbolicLink(at: symlink, withDestinationURL: target)
        var symlinkRejected = false
        do {
            _ = try recentDiagnosticLogLines(from: symlink, maxBytes: 4096, maxLines: 3)
        } catch {
            symlinkRejected = true
        }
        try expect(symlinkRejected, equals: true,
                   "diagnostic log tail should reject leaf symlinks")

        let hardlink = root.appendingPathComponent("hardlink.log")
        guard Darwin.link(target.path, hardlink.path) == 0 else {
            throw currentPOSIXError()
        }
        var hardlinkRejected = false
        do {
            _ = try recentDiagnosticLogLines(from: hardlink, maxBytes: 4096, maxLines: 3)
        } catch {
            hardlinkRejected = true
        }
        try expect(hardlinkRejected, equals: true,
                   "diagnostic log tail should reject hard-linked files")
    }

    private static func testHotkey() throws {
        try testHotkeyPreferenceNormalization()
        try testHotkeyDiagnosticModifierNames()
        try testHotkeyRecorderControlEvents()
        try testHotkeyPreferenceUpdateResults()
        try testHotkeyRecorderRestartActions()
        try testRecordingStartBlockerResolution()
        try testHandledHotkeySuppression()
        try testFKeyAutoRepeatSuppressesWithoutAction()
        try testRightModifierReleaseWithLeftFlagStillSet()
        try testTogglePressFlipsOnceAndReleaseIsNoOp()
        try testToggleGatedPressDoesNotFlipToggleState()
        try testEscapePassesThroughWhenNotRecording()
        try testEscapeSuppressesCancelRepeatAndKeyUpWhileRecording()
    }

    private static func testHotkeyDiagnosticModifierNames() throws {
        try expect(
            diagnosticModifierName(for: event(.flagsChanged, keycode: 58)),
            equals: "Left Option",
            "hotkey diagnostics should name non-text modifier events"
        )
        try expect(
            diagnosticModifierName(for: event(.keyDown, keycode: 0)),
            equals: nil,
            "hotkey diagnostics must not expose ordinary typing keys"
        )
        try expect(
            diagnosticModifierName(for: event(.keyDown, keycode: 58)),
            equals: nil,
            "hotkey diagnostics should require a modifier event"
        )
    }

    private static func testHotkeyRecorderControlEvents() throws {
        for keycode: CGKeyCode in [36, 48, 49, 53, 76] {
            try expect(
                hotkeyRecorderShouldPassThrough(event(.keyDown, keycode: keycode)),
                equals: true,
                "hotkey recorder should preserve modal control key \(keycode)"
            )
        }
        try expect(
            hotkeyRecorderShouldPassThrough(event(.flagsChanged, keycode: 53)),
            equals: false,
            "hotkey recorder should only pass modal controls on key down"
        )
        try expect(
            hotkeyRecorderShouldPassThrough(event(.keyDown, keycode: 0)),
            equals: false,
            "hotkey recorder should continue rejecting ordinary typing keys"
        )
    }

    private static func testHotkeyPreferenceNormalization() throws {
        try expect(
            normalizedHotkeyKeycode(storedValue: NSNumber(value: Int(DEFAULT_HOTKEY_KEYCODE))),
            equals: DEFAULT_HOTKEY_KEYCODE,
            "stored hotkey normalization should keep supported numeric keycodes"
        )
        try expect(
            normalizedHotkeyKeycode(storedValue: " 96\n"),
            equals: CGKeyCode(96),
            "stored hotkey normalization should accept legacy string keycodes"
        )
        try expect(
            normalizedHotkeyKeycode(storedValue: NSNumber(value: 98)),
            equals: CGKeyCode(98),
            "stored hotkey normalization should accept recorded F-key keycodes"
        )
        try expect(
            hotkeyChoice(forKeycode: CGKeyCode(98)),
            equals: HotkeyChoice(name: "F7", keycode: 98, isModifier: false, modifierFlag: nil),
            "recorded F-key choices should get a stable display name"
        )
        try expect(
            normalizedHotkeyKeycode(storedValue: NSNumber(value: 999)),
            equals: nil,
            "stored hotkey normalization should reject unsupported keycodes"
        )
        try expect(
            normalizedHotkeyKeycode(storedValue: NSNumber(value: -1)),
            equals: nil,
            "stored hotkey normalization should reject negative keycodes"
        )
        try expect(
            hotkeyChoice(forKeycode: CGKeyCode(999)),
            equals: hotkeyChoice(forKeycode: DEFAULT_HOTKEY_KEYCODE),
            "unknown hotkey choices should fall back to the default"
        )

        try expect(
            hotkeyRecordingDecision(for: event(.keyDown, keycode: 98)),
            equals: .accept(HotkeyChoice(name: "F7", keycode: 98, isModifier: false, modifierFlag: nil)),
            "hotkey recorder should accept F-key presses outside the quick-pick list"
        )
        try expect(
            hotkeyRecordingDecision(for: event(.keyDown, keycode: 0)),
            equals: .reject("Choose a right-side modifier key or an F-key. Typing keys are not safe because Presspeech suppresses its dictation key globally."),
            "hotkey recorder should reject typing keys"
        )
        try expect(
            hotkeyRecordingDecision(for: event(.keyDown, keycode: 98, isAutoRepeat: true)),
            equals: .ignore,
            "hotkey recorder should ignore auto-repeat"
        )
        try expect(
            hotkeyRecordingDecision(for: event(.flagsChanged,
                                               keycode: 61,
                                               flags: CGEventFlags.maskAlternate.rawValue)),
            equals: .accept(HotkeyChoice(name: "Right Option",
                                         keycode: 61,
                                         isModifier: true,
                                         modifierFlag: .maskAlternate)),
            "hotkey recorder should accept right-side modifier presses"
        )
    }

    private static func testHotkeyPreferenceUpdateResults() throws {
        let f5 = hotkeyChoice(forKeycode: 96)
        let f7 = hotkeyChoice(forKeycode: 98)
        let invalid = HotkeyChoice(name: "A", keycode: 0, isModifier: false, modifierFlag: nil)

        try expect(
            hotkeyPreferenceUpdateResult(
                requested: f7,
                previous: f5,
                persistedKeycode: f7.keycode
            ),
            equals: .saved(f7),
            "hotkey preference update should save supported keys after persistence confirms them"
        )
        try expect(
            hotkeyPreferenceUpdateResult(
                requested: invalid,
                previous: f5,
                persistedKeycode: f5.keycode
            ),
            equals: .rejected("That key cannot be used for dictation."),
            "hotkey preference update should reject unsupported keys before mutating settings"
        )
        try expect(
            hotkeyPreferenceUpdateResult(
                requested: f7,
                previous: f5,
                persistedKeycode: f5.keycode
            ),
            equals: .rolledBack(
                previous: f5,
                message: "Presspeech could not save that hotkey, so it kept F5."
            ),
            "hotkey preference update should roll back when persisted settings disagree"
        )
    }

    private static func testHotkeyRecorderRestartActions() throws {
        try expect(
            hotkeyRecorderRestartAction(
                shouldRestoreHotkeyTap: false,
                isTerminating: false,
                restartSucceeded: false
            ),
            equals: .none,
            "hotkey recorder should not start a listener that was not active"
        )
        try expect(
            hotkeyRecorderRestartAction(
                shouldRestoreHotkeyTap: true,
                isTerminating: true,
                restartSucceeded: false
            ),
            equals: .none,
            "hotkey recorder should not restart the listener during termination"
        )
        try expect(
            hotkeyRecorderRestartAction(
                shouldRestoreHotkeyTap: true,
                isTerminating: false,
                restartSucceeded: true
            ),
            equals: .restoredListener,
            "hotkey recorder should treat a successful restart as recovered"
        )
        try expect(
            hotkeyRecorderRestartAction(
                shouldRestoreHotkeyTap: true,
                isTerminating: false,
                restartSucceeded: false
            ),
            equals: .recordFailure,
            "hotkey recorder should surface restart failure after an active listener was paused"
        )
    }

    private static func testRecordingStartBlockerResolution() throws {
        final class Owner {}

        var owner: Owner? = Owner()
        try expect(
            resolvedRecordingStartBlocker(for: owner) { _ in nil },
            equals: nil,
            "a live ready app should allow recording to start"
        )
        try expect(
            resolvedRecordingStartBlocker(for: owner) { _ in .busyTranscribing },
            equals: .busyTranscribing,
            "a live blocked app should preserve its blocker"
        )

        owner = nil
        try expect(
            resolvedRecordingStartBlocker(for: owner) { _ in nil },
            equals: .terminating,
            "a released app should reject new recordings as terminating"
        )
    }

    private static func testReadiness() throws {
        try expect(
            readinessTransition(isReady: false,
                                isCoreRuntimeReady: false,
                                missingPermissions: []),
            equals: .rebuildMenuOnly,
            "not-ready app without core runtime should wait and rebuild only"
        )
        try expect(
            readinessTransition(isReady: false,
                                isCoreRuntimeReady: true,
                                missingPermissions: [.microphone]),
            equals: .blockForPermissions([.microphone]),
            "core-ready app with missing microphone should block"
        )
        try expect(
            readinessTransition(isReady: true,
                                isCoreRuntimeReady: true,
                                missingPermissions: [.accessibility]),
            equals: .blockForPermissions([.accessibility]),
            "ready app with missing accessibility should block"
        )
        try expect(
            readinessTransition(isReady: false,
                                isCoreRuntimeReady: true,
                                missingPermissions: []),
            equals: .startHotkeyListener,
            "core-ready app with all permissions should start hotkey"
        )
        try expect(
            readinessTransition(isReady: true,
                                isCoreRuntimeReady: true,
                                missingPermissions: []),
            equals: .rebuildMenuOnly,
            "ready app with all permissions should remain ready and rebuild only"
        )

        try expect(
            productionSpeechModelProfile(rawValue: nil),
            equals: .multilingualV3,
            "missing speech model setting should use the production default"
        )
        try expect(
            productionSpeechModelProfile(rawValue: SpeechModelProfile.multilingualV3.rawValue),
            equals: .multilingualV3,
            "stored v3 speech model should remain valid"
        )
        try expect(
            productionSpeechModelProfile(rawValue: SpeechModelProfile.englishUnified.rawValue),
            equals: .multilingualV3,
            "deprecated Unified speech model setting should migrate back to v3"
        )
        try expect(
            productionSpeechModelProfile(rawValue: "unknown_model"),
            equals: .multilingualV3,
            "unknown speech model setting should migrate back to v3"
        )

        try expect(
            speechModelSetupRowState(profile: .multilingualV3,
                                     isSpeechModelReady: false,
                                     isStartupInProgress: true,
                                     startupStatusTitle: "Downloading speech model… 50%",
                                     failure: nil),
            equals: SetupChecklistRowState(detail: "Downloading speech model… 50%",
                                           status: "Loading",
                                           buttonTitle: nil),
            "setup checklist should show speech model progress"
        )
        try expect(
            speechModelSetupRowState(profile: .multilingualV3,
                                     isSpeechModelReady: false,
                                     isStartupInProgress: false,
                                     startupStatusTitle: "Loading speech model…",
                                     failure: StartupFailure(stage: .speechModel, detail: "download failed")),
            equals: SetupChecklistRowState(detail: "download failed",
                                           status: "Needs retry",
                                           buttonTitle: "Retry"),
            "setup checklist should offer retry for speech model failures"
        )
        try expect(
            speechModelSetupRowState(profile: .multilingualV3,
                                     isSpeechModelReady: true,
                                     isStartupInProgress: false,
                                     startupStatusTitle: "Loading speech model…",
                                     failure: nil),
            equals: SetupChecklistRowState(detail: "Parakeet TDT v3 is loaded locally.",
                                           status: "Ready",
                                           buttonTitle: nil),
            "setup checklist should show the speech model when ready"
        )
        try expect(
            audioInputSetupRowState(isSpeechModelReady: true,
                                    isCoreRuntimeReady: false,
                                    isStartupInProgress: false,
                                    failure: StartupFailure(stage: .audioInput, detail: "no input device")),
            equals: SetupChecklistRowState(detail: "no input device",
                                           status: "Needs retry",
                                           buttonTitle: "Retry"),
            "setup checklist should offer retry for audio input failures"
        )
        try expect(
            audioInputSetupRowState(isSpeechModelReady: false,
                                    isCoreRuntimeReady: false,
                                    isStartupInProgress: true,
                                    failure: nil),
            equals: SetupChecklistRowState(detail: "Available after the speech model loads.",
                                           status: "Waiting",
                                           buttonTitle: nil),
            "setup checklist should not start audio before the speech model is ready"
        )
        try expect(
            hotkeySetupRowState(isReady: false,
                                hotkeyTestSucceeded: false,
                                triggerMode: .hold,
                                hotkeyName: "Right Option",
                                failure: StartupFailure(stage: .hotkeyListener, detail: "event tap failed")),
            equals: SetupChecklistRowState(detail: "event tap failed",
                                           status: "Needs retry",
                                           buttonTitle: "Retry"),
            "setup checklist should offer retry for hotkey listener failures"
        )
        try expect(
            hotkeySetupRowState(isReady: true,
                                hotkeyTestSucceeded: true,
                                triggerMode: .toggle,
                                hotkeyName: "F5",
                                failure: nil),
            equals: SetupChecklistRowState(detail: "Press F5 to dictate.",
                                           status: "Detected",
                                           buttonTitle: nil),
            "setup checklist should show detected hotkey state"
        )
        try expect(
            hotkeySetupRowState(isReady: true,
                                hotkeyTestSucceeded: false,
                                triggerMode: .toggle,
                                hotkeyName: "F5",
                                failure: nil),
            equals: SetupChecklistRowState(
                detail: "Press F5 once, then press it again to stop the test.",
                status: "Ready to test",
                buttonTitle: nil),
            "setup checklist should explain how to finish a toggle-mode test"
        )
        try expect(
            setupChecklistCompletionState(isSpeechModelReady: true,
                                          isReady: true,
                                          permissionsGranted: true,
                                          hotkeyTestSucceeded: false),
            equals: false,
            "setup checklist should remain incomplete until a hotkey event is delivered"
        )
        try expect(
            setupChecklistCompletionState(isSpeechModelReady: true,
                                          isReady: true,
                                          permissionsGranted: true,
                                          hotkeyTestSucceeded: true),
            equals: true,
            "setup checklist should complete after runtime, permissions, and hotkey test succeed"
        )

        try expect(
            previousExitNoticeAction(previousRunWasActive: false),
            equals: .none,
            "clean previous exits should not show the abnormal-exit notice"
        )
        try expect(
            previousExitNoticeAction(previousRunWasActive: true),
            equals: .showNotice,
            "active run markers should show the abnormal-exit notice on next launch"
        )
        try expect(
            speechModelFailureDetail(errorDescription: "SHA-256 mismatch").contains("Reset Speech Model Cache"),
            equals: true,
            "speech model integrity failures should point to cache reset"
        )
        try expect(
            speechModelFailureDetail(errorDescription: "download timed out").contains("audio is not uploaded"),
            equals: true,
            "speech model download failures should preserve the local-audio privacy boundary"
        )
        try expect(
            speechModelFailureDetail(errorDescription: "Free some disk space, then retry loading the speech model."),
            equals: "Free some disk space, then retry loading the speech model.",
            "disk-space failures should not add unrelated reset-cache guidance"
        )
        try expect(
            startupFailureDetail(stage: .audioInput, errorDescription: "no input device"),
            equals: "no input device",
            "non-model startup failures should keep their original detail"
        )

        let coreAudioStopError = NSError(
            domain: "com.apple.coreaudio.avfaudio",
            code: 1_937_010_544,
            userInfo: ["failed call": "PerformCommand(*ioNode, kAUStartIO, NULL, 0)"]
        )
        let coreAudioErrorDescription = audioStartupErrorDescription(coreAudioStopError)
        try expect(
            coreAudioErrorDescription.contains("OSStatus 1937010544"),
            equals: true,
            "CoreAudio startup errors should include the decimal OSStatus"
        )
        try expect(
            coreAudioErrorDescription.contains("0x73746f70"),
            equals: true,
            "CoreAudio startup errors should include the hex OSStatus"
        )
        try expect(
            coreAudioErrorDescription.contains("'stop'"),
            equals: true,
            "CoreAudio startup errors should include printable four-character codes"
        )
        try expect(
            coreAudioErrorDescription.contains("PerformCommand(*ioNode, kAUStartIO, NULL, 0)"),
            equals: true,
            "CoreAudio startup errors should preserve the failed AVFAudio call"
        )
        try expect(
            startupFailureDetail(stage: .audioInput, error: coreAudioStopError).contains("restart CoreAudio"),
            equals: true,
            "exhausted CoreAudio startup failures should give OS recovery guidance"
        )
    }

    private static func testPasteSuffixFormatting() throws {
        try expect(
            pastedText(from: "hello world", suffix: .appendSpace),
            equals: "hello world ",
            "append-space suffix should preserve the existing default"
        )
        try expect(
            pastedText(from: "hello world", suffix: .none),
            equals: "hello world",
            "no suffix should paste corrected transcript unchanged"
        )
        try expect(
            pastedText(from: "hello world", suffix: .appendNewline),
            equals: "hello world\n",
            "append-newline suffix should add a single newline"
        )
        try expect(
            pastedText(from: "hello world ", suffix: .appendSpace),
            equals: "hello world  ",
            "suffix formatting should not trim or rewrite corrected text"
        )
        try expect(
            TextInserter.defaultStrategy,
            equals: .clipboardPaste,
            "clipboard paste should remain the default insertion strategy"
        )
        try expect(
            textInsertionStrategyChain(primary: .clipboardPaste),
            equals: [.clipboardPaste, .directUnicode],
            "clipboard paste should fall back to direct Unicode insertion"
        )
        try expect(
            textInsertionStrategyChain(primary: .directUnicode),
            equals: [.directUnicode],
            "direct Unicode insertion should not loop back to clipboard paste"
        )
        try expect(
            TextInserter.defaultStrategyDescription,
            equals: "Clipboard paste with Direct Unicode typing fallback",
            "diagnostics should describe the insertion fallback chain"
        )
        try expect(
            dictationDeliveryRoute(capturedTargetAvailable: true,
                                    targetStillFocused: true),
            equals: .inserted,
            "dictation should paste only while its captured target remains focused"
        )
        try expect(
            dictationDeliveryRoute(capturedTargetAvailable: true,
                                    targetStillFocused: false),
            equals: .copiedWithoutPasting,
            "a focus change should leave dictation on the clipboard without pasting"
        )
        try expect(
            dictationDeliveryRoute(capturedTargetAvailable: false,
                                    targetStillFocused: false),
            equals: .copiedWithoutPasting,
            "an unavailable target snapshot should fail closed to clipboard-only delivery"
        )
        let unicodeChunks = unicodeInsertionChunks(for: "ab👩‍💻cd", maxUTF16UnitsPerEvent: 4)
            .map { String(decoding: $0, as: UTF16.self) }
        try expect(
            unicodeChunks,
            equals: ["ab", "👩‍💻", "cd"],
            "direct Unicode insertion should keep extended grapheme clusters together while chunking"
        )
        try expect(
            unicodeInsertionChunks(for: "abc", maxUTF16UnitsPerEvent: 0),
            equals: [],
            "direct Unicode chunking should reject invalid chunk sizes"
        )
        let focusBoundUnicodeProbe = MainActor.assumeIsolated {
            var focusChecks = [true, false]
            var postedChunks: [[UInt16]] = []
            var copied = false
            let outcome = directUnicodeInsertionOutcome(
                chunks: [[1, 2], [3, 4], [5, 6]],
                targetStillFocused: { focusChecks.removeFirst() },
                postChunk: {
                    postedChunks.append($0)
                    return true
                },
                copyWithoutPasting: {
                    copied = true
                    return .copiedWithoutPasting
                }
            )
            return (outcome: outcome, postedChunks: postedChunks, copied: copied)
        }
        try expect(
            focusBoundUnicodeProbe.outcome,
            equals: .copiedWithoutPasting,
            "direct Unicode insertion should fail closed when focus changes between chunks"
        )
        try expect(
            focusBoundUnicodeProbe.postedChunks,
            equals: [[1, 2]],
            "direct Unicode insertion should not post chunks after focus changes"
        )
        try expect(
            focusBoundUnicodeProbe.copied,
            equals: true,
            "an interrupted Direct Unicode fallback should preserve the transcript on the clipboard"
        )
        let failedUnicodePostProbe = MainActor.assumeIsolated {
            var attempts = 0
            return directUnicodeInsertionOutcome(
                chunks: [[1], [2]],
                targetStillFocused: { true },
                postChunk: { _ in
                    attempts += 1
                    return false
                },
                copyWithoutPasting: { .copiedWithoutPasting }
            ) == .failed && attempts == 1
        }
        try expect(
            failedUnicodePostProbe,
            equals: true,
            "direct Unicode insertion should stop after the first failed chunk"
        )
        try expect(
            clipboardPasteKeyboardEventSteps(commandKey: 0x37, pasteKey: 0x09),
            equals: [
                KeyboardEventStep(virtualKey: 0x37, keyDown: true, flags: .maskCommand),
                KeyboardEventStep(virtualKey: 0x09, keyDown: true, flags: .maskCommand),
                KeyboardEventStep(virtualKey: 0x09, keyDown: false, flags: .maskCommand),
                KeyboardEventStep(virtualKey: 0x37, keyDown: false, flags: []),
            ],
            "clipboard paste should synthesize a full Command+V key sequence"
        )
        let focusBoundPasteProbe = MainActor.assumeIsolated {
            let steps = clipboardPasteKeyboardEventSteps(commandKey: 0x37,
                                                         pasteKey: 0x09)
            var posted: [KeyboardEventStep] = []
            let outcome = postFocusBoundClipboardPasteSteps(
                steps,
                pasteKey: 0x09,
                targetStillFocused: { false },
                postStep: {
                    posted.append($0)
                    return true
                }
            )
            return (outcome: outcome, posted: posted)
        }
        try expect(
            focusBoundPasteProbe.outcome,
            equals: .targetChanged,
            "clipboard paste should fail closed when focus changes after Command goes down"
        )
        try expect(
            focusBoundPasteProbe.posted,
            equals: [
                KeyboardEventStep(virtualKey: 0x37, keyDown: true, flags: .maskCommand),
                KeyboardEventStep(virtualKey: 0x37, keyDown: false, flags: []),
            ],
            "focus-bound clipboard paste should release Command without emitting V"
        )

        let pasteboardProbe = MainActor.assumeIsolated {
            let pasteboardName = NSPasteboard.Name("com.local.presspeech.self-test.\(UUID().uuidString)")
            let pasteboard = NSPasteboard(name: pasteboardName)
            let wrote = ClipboardPasteInserter.write("pasteboard probe", to: pasteboard)
            return (
                wrote: wrote,
                stored: pasteboard.string(forType: .string),
                markedTransient: pasteboard.types?.contains(TRANSIENT_PASTEBOARD_TYPE) == true,
                markedAutoGenerated: pasteboard.types?.contains(AUTO_GENERATED_PASTEBOARD_TYPE) == true
            )
        }
        try expect(
            pasteboardProbe.wrote,
            equals: true,
            "clipboard paste should report pasteboard write success"
        )
        try expect(
            pasteboardProbe.stored,
            equals: "pasteboard probe",
            "clipboard paste should write the intended string before posting Cmd+V"
        )
        try expect(
            pasteboardProbe.markedTransient || pasteboardProbe.markedAutoGenerated,
            equals: false,
            "clipboard content that will remain available should not be marked transient"
        )
        let contentsOptionsProbe = MainActor.assumeIsolated {
            (
                persistent: ClipboardPasteInserter.contentsOptions(transient: false),
                transient: ClipboardPasteInserter.contentsOptions(transient: true)
            )
        }
        try expect(
            contentsOptionsProbe.persistent,
            equals: NSPasteboard.ContentsOptions(),
            "persistent clipboard writes should retain normal Universal Clipboard behavior"
        )
        try expect(
            contentsOptionsProbe.transient,
            equals: .currentHostOnly,
            "temporary transcript clipboard writes should stay on the current Mac"
        )

        let transientPasteboardProbe = MainActor.assumeIsolated {
            let pasteboardName = NSPasteboard.Name("com.local.presspeech.self-test.\(UUID().uuidString)")
            let pasteboard = NSPasteboard(name: pasteboardName)
            let wrote = ClipboardPasteInserter.write(
                "temporary transcript",
                to: pasteboard,
                transient: true
            )
            return (
                wrote: wrote,
                stored: pasteboard.string(forType: .string),
                markedTransient: pasteboard.types?.contains(TRANSIENT_PASTEBOARD_TYPE) == true,
                markedAutoGenerated: pasteboard.types?.contains(AUTO_GENERATED_PASTEBOARD_TYPE) == true,
                source: pasteboard.string(forType: PASTEBOARD_SOURCE_TYPE)
            )
        }
        try expect(
            transientPasteboardProbe.wrote,
            equals: true,
            "temporary transcript clipboard writes should succeed"
        )
        try expect(
            transientPasteboardProbe.stored,
            equals: "temporary transcript",
            "temporary pasteboard metadata should not interfere with the transcript string"
        )
        try expect(
            transientPasteboardProbe.markedTransient && transientPasteboardProbe.markedAutoGenerated,
            equals: true,
            "temporary transcript clipboard writes should carry standard transient markers"
        )
        try expect(
            transientPasteboardProbe.source,
            equals: SETTINGS_SUITE,
            "temporary transcript clipboard writes should identify Presspeech as their source"
        )

        try expect(
            pasteboardChangeCountAllowsRestore(current: 7, expected: 7),
            equals: true,
            "clipboard restore should proceed when nothing else touched the pasteboard"
        )
        try expect(
            pasteboardChangeCountAllowsRestore(current: 8, expected: 7),
            equals: false,
            "clipboard restore should back off when the pasteboard changed under us"
        )

        let restoreProbe = MainActor.assumeIsolated {
            let pasteboardName = NSPasteboard.Name("com.local.presspeech.self-test.\(UUID().uuidString)")
            let pasteboard = NSPasteboard(name: pasteboardName)
            let binaryType = NSPasteboard.PasteboardType("com.local.presspeech.self-test.binary")
            let firstItem = NSPasteboardItem()
            firstItem.setString("previous clipboard", forType: .string)
            firstItem.setData(Data([0, 1, 2, 3]), forType: binaryType)
            let secondItem = NSPasteboardItem()
            secondItem.setString("https://example.com", forType: .URL)
            pasteboard.clearContents()
            let wroteOriginal = pasteboard.writeObjects([firstItem, secondItem])
            let snapshot = ClipboardPasteInserter.snapshot(of: pasteboard)

            _ = ClipboardPasteInserter.write("dictated text", to: pasteboard, transient: true)
            let afterWrite = pasteboard.string(forType: .string)
            let writeChangeCount = pasteboard.changeCount

            let restored = snapshot.map {
                ClipboardPasteInserter.restore($0,
                                               to: pasteboard,
                                               expectedChangeCount: writeChangeCount)
            } ?? false
            let afterRestore = pasteboard.string(forType: .string)
            let restoredBinary = pasteboard.pasteboardItems?.first?.data(forType: binaryType)
            let restoredURL = pasteboard.pasteboardItems?.dropFirst().first?.string(forType: .URL)
            let restoredTransientMarker = pasteboard.types?.contains(TRANSIENT_PASTEBOARD_TYPE) == true

            // A stale expected changeCount must leave the pasteboard alone.
            _ = ClipboardPasteInserter.write("newer text", to: pasteboard)
            let blocked = snapshot.map {
                ClipboardPasteInserter.restore($0,
                                               to: pasteboard,
                                               expectedChangeCount: writeChangeCount)
            } ?? false
            let afterBlocked = pasteboard.string(forType: .string)
            return (wroteOriginal: wroteOriginal,
                    snapshotCreated: snapshot != nil,
                    afterWrite: afterWrite,
                    restored: restored,
                    afterRestore: afterRestore,
                    restoredBinary: restoredBinary,
                    restoredURL: restoredURL,
                    restoredTransientMarker: restoredTransientMarker,
                    blocked: blocked,
                    afterBlocked: afterBlocked)
        }
        try expect(
            restoreProbe.wroteOriginal,
            equals: true,
            "clipboard restore test should create a multi-item source pasteboard"
        )
        try expect(
            restoreProbe.snapshotCreated,
            equals: true,
            "clipboard snapshot should preserve fully materialized pasteboard items"
        )
        try expect(
            restoreProbe.afterWrite,
            equals: "dictated text",
            "clipboard paste should overwrite the pasteboard with the transcript"
        )
        try expect(
            restoreProbe.restored,
            equals: true,
            "clipboard restore should succeed when the changeCount still matches"
        )
        try expect(
            restoreProbe.afterRestore,
            equals: "previous clipboard",
            "clipboard restore should put the previous contents back after paste"
        )
        try expect(
            restoreProbe.restoredBinary,
            equals: Data([0, 1, 2, 3]),
            "clipboard restore should preserve non-text representations"
        )
        try expect(
            restoreProbe.restoredURL,
            equals: "https://example.com",
            "clipboard restore should preserve multiple pasteboard items"
        )
        try expect(
            restoreProbe.restoredTransientMarker,
            equals: false,
            "clipboard restore should remove Presspeech's temporary transcript markers"
        )
        try expect(
            restoreProbe.blocked,
            equals: false,
            "clipboard restore should refuse to run against a stale changeCount"
        )
        try expect(
            restoreProbe.afterBlocked,
            equals: "newer text",
            "clipboard restore should not clobber content copied after the transcript"
        )

        let incompleteSnapshotProbe = MainActor.assumeIsolated {
            let pasteboardName = NSPasteboard.Name("com.local.presspeech.self-test.\(UUID().uuidString)")
            let pasteboard = NSPasteboard(name: pasteboardName)
            let unavailableType = NSPasteboard.PasteboardType(
                "com.local.presspeech.self-test.unavailable"
            )
            let provider = UnavailablePasteboardDataProvider()
            let item = NSPasteboardItem()
            let registered = item.setDataProvider(provider, forTypes: [unavailableType])
            pasteboard.clearContents()
            let wrote = pasteboard.writeObjects([item])
            let snapshot = ClipboardPasteInserter.snapshot(of: pasteboard)
            return (registered: registered, wrote: wrote, rejected: snapshot == nil)
        }
        try expect(
            incompleteSnapshotProbe.registered && incompleteSnapshotProbe.wrote,
            equals: true,
            "clipboard snapshot test should install a lazy data provider"
        )
        try expect(
            incompleteSnapshotProbe.rejected,
            equals: true,
            "clipboard snapshot should reject an unavailable representation instead of restoring partial data"
        )
    }

    private static func testRecentTranscriptLimit() throws {
        let transcripts = ["newest", "second", "third", "fourth", "fifth", "sixth"]

        try expect(
            limitedRecentTranscripts(transcripts, limit: .off),
            equals: [],
            "off should keep no recent transcripts"
        )
        try expect(
            limitedRecentTranscripts(transcripts, limit: .last1),
            equals: ["newest"],
            "last-one history should keep only the newest transcript"
        )
        try expect(
            limitedRecentTranscripts(transcripts, limit: .last5),
            equals: ["newest", "second", "third", "fourth", "fifth"],
            "last-five history should preserve the current default cap"
        )
        try expect(
            parseRecentTranscriptLimit(storedValue: NSNumber(value: 1)),
            equals: .last1,
            "numeric defaults writes should be accepted for last-one history"
        )
    }

    private static func testAudioLevelMetering() throws {
        var accumulator = AudioSampleAccumulator()
        accumulator.append([])
        accumulator.append([1, 2])
        accumulator.append([3, 4, 5])
        try expect(
            accumulator.sampleCount,
            equals: 5,
            "segmented audio accumulator should track total sample count"
        )
        let captured = accumulator.drain()
        try expect(
            accumulator.sampleCount,
            equals: 0,
            "segmented audio accumulator should reset after drain"
        )
        try expect(
            captured.flattened(),
            equals: [1, 2, 3, 4, 5],
            "segmented audio accumulator should preserve sample order when flattened"
        )

        var tailAccumulator = AudioSampleAccumulator()
        tailAccumulator.append([0.5, 0.5])
        tailAccumulator.append([0.001, 0.001])
        guard abs(tailAccumulator.tailRMS(maxSampleCount: 2) - 0.001) < 0.000_001 else {
            throw SelfTestFailure.failed("post-release RMS should inspect only the requested newest samples")
        }
        guard abs(tailAccumulator.tailRMS(maxSampleCount: 4) - sqrt(0.125_000_5)) < 0.000_001 else {
            throw SelfTestFailure.failed("post-release RMS should cross accumulator segment boundaries")
        }
        try expect(
            postReleaseSilenceThreshold(peakRMS: 0.01),
            equals: POST_RELEASE_ABSOLUTE_SILENCE_RMS,
            "quiet recordings should use the absolute post-release silence floor"
        )
        try expect(
            postReleaseSilenceThreshold(peakRMS: 1),
            equals: POST_RELEASE_MAX_SILENCE_RMS,
            "loud recordings should cap the relative post-release silence threshold"
        )

        let quietTail = PostReleaseTailMeasurement(tailRMS: 0.001,
                                                   silenceThreshold: 0.003)
        let voicedTail = PostReleaseTailMeasurement(tailRMS: 0.03,
                                                    silenceThreshold: 0.006)
        try expect(
            postReleaseCaptureDecision(elapsed: 0.04, measurement: quietTail),
            equals: .wait(0.04),
            "post-release capture should always retain its minimum safety window"
        )
        try expect(
            postReleaseCaptureDecision(elapsed: POST_RELEASE_MIN_CAPTURE_SECONDS,
                                       measurement: quietTail),
            equals: .finishSilence,
            "a quiet tail should finish immediately after the minimum window"
        )
        try expect(
            postReleaseCaptureDecision(elapsed: 0.2,
                                       measurement: quietTail,
                                       receivedNewSamples: false),
            equals: .wait(POST_RELEASE_CAPTURE_CHECK_SECONDS),
            "pre-release silence should not finish while the boundary audio buffer is pending"
        )
        try expect(
            postReleaseCaptureDecision(elapsed: 0.2, measurement: voicedTail),
            equals: .wait(POST_RELEASE_CAPTURE_CHECK_SECONDS),
            "a voiced tail should keep capture armed before the hard bound"
        )
        try expect(
            postReleaseCaptureDecision(elapsed: POST_RELEASE_MAX_CAPTURE_SECONDS,
                                       measurement: voicedTail),
            equals: .finishMaximum,
            "ongoing sound should never extend post-release capture past its bound"
        )

        try expect(
            normalizedAudioLevel(from: Array(repeating: 0, count: 128)),
            equals: 0,
            "silence should map to zero recording level"
        )

        let lowVoice = normalizedAudioLevel(from: Array(repeating: 0.004, count: 128))
        let quiet = normalizedAudioLevel(from: Array(repeating: 0.01, count: 128))
        let normal = normalizedAudioLevel(from: Array(repeating: 0.12, count: 128))
        let loud = normalizedAudioLevel(from: Array(repeating: 4.0, count: 128))

        guard lowVoice > 0 else {
            throw SelfTestFailure.failed("low close-mic voice should rise above the visual gate")
        }
        guard quiet > 0 else {
            throw SelfTestFailure.failed("quiet speech-like input should rise above zero")
        }
        guard normal > quiet else {
            throw SelfTestFailure.failed("higher RMS should produce a higher visual level")
        }
        try expect(
            loud,
            equals: 1,
            "out-of-range samples should clamp to maximum visual level"
        )

        try expect(
            normalizedAudioLevel(from: [.nan, .infinity, -.infinity]),
            equals: 0,
            "non-finite samples should not produce a visible level"
        )
        try expect(
            visibleRecordingLevel(rawLevel: .nan),
            equals: 0,
            "visible recording level should ignore non-finite input"
        )
        try expect(
            visibleRecordingLevel(rawLevel: 0.8),
            equals: 0.8,
            "visible recording level should pass through normal input immediately"
        )
        try expect(
            visibleRecordingLevel(rawLevel: 1.2),
            equals: 1,
            "visible recording level should clamp high input"
        )
    }

    private static func testAudioConversion() throws {
        guard let stereoFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32,
                                               sampleRate: 48_000,
                                               channels: 2,
                                               interleaved: false),
              let monoFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32,
                                             sampleRate: 48_000,
                                             channels: 1,
                                             interleaved: false),
              let targetFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32,
                                               sampleRate: 16_000,
                                               channels: 1,
                                               interleaved: false),
              let stereo = AVAudioPCMBuffer(pcmFormat: stereoFormat, frameCapacity: 480),
              let mono = AVAudioPCMBuffer(pcmFormat: monoFormat, frameCapacity: 480),
              let stereoChannels = stereo.floatChannelData,
              let monoChannel = mono.floatChannelData?[0] else {
            throw SelfTestFailure.failed("could not create audio conversion test buffers")
        }
        stereo.frameLength = 480
        for i in 0..<480 {
            stereoChannels[0][i] = 0.5
            stereoChannels[1][i] = 0.02
        }

        let rms = channelRMSValues(channels: stereoChannels, channelCount: 2, frameCount: 480)
        try expect(
            selectedMonoMixChannelIndices(channelRMS: rms),
            equals: [0],
            "manual mono mix should select the active close-mic channel when another channel is near-silent"
        )
        writeMonoMix(channels: stereoChannels,
                     selectedChannels: selectedMonoMixChannelIndices(channelRMS: rms),
                     frameCount: 480,
                     to: monoChannel)
        mono.frameLength = 480
        try expect(
            monoChannel[0],
            equals: 0.5,
            "manual mono mix should preserve the selected active channel"
        )

        for i in 0..<480 {
            stereoChannels[0][i] = 0.5
            stereoChannels[1][i] = -0.5
        }
        let balancedRMS = channelRMSValues(channels: stereoChannels, channelCount: 2, frameCount: 480)
        try expect(
            selectedMonoMixChannelIndices(channelRMS: balancedRMS),
            equals: [0, 1],
            "manual mono mix should average multiple similarly active channels"
        )
        writeMonoMix(channels: stereoChannels,
                     selectedChannels: selectedMonoMixChannelIndices(channelRMS: balancedRMS),
                     frameCount: 480,
                     to: monoChannel)
        try expect(
            monoChannel[0],
            equals: 0,
            "manual mono mix should average selected channels with equal weight"
        )

        guard let converted = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: 320),
              let converter = AVAudioConverter(from: monoFormat, to: targetFormat) else {
            throw SelfTestFailure.failed("could not create audio converter")
        }
        var error: NSError?
        let inputProvider = AudioConverterInputProvider(buffer: mono)
        let status = converter.convert(to: converted, error: &error) { _, outStatus in
            inputProvider.provide(outStatus: outStatus)
        }
        if status == .error {
            throw SelfTestFailure.failed("audio conversion failed: \(error?.localizedDescription ?? "?")")
        }
        guard converted.format.channelCount == 1,
              Int(converted.format.sampleRate) == 16_000,
              converted.frameLength > 0 else {
            throw SelfTestFailure.failed("audio conversion should produce 16 kHz mono samples")
        }
    }

    private static func testTranscriptCorrections() throws {
        try expect(
            correctionSourcePrefill(from: "  first line\n\nsecond\tline  "),
            equals: "first line second line",
            "correction source prefill should collapse transcript whitespace"
        )
        try expect(
            correctionSourcePrefill(from: String(repeating: "a", count: MAX_TRANSCRIPT_CORRECTION_SOURCE_BYTES + 4)).utf8.count,
            equals: MAX_TRANSCRIPT_CORRECTION_SOURCE_BYTES,
            "correction source prefill should stay inside correction source byte limits"
        )
        try expect(
            correctionSourcePrefill(from: String(repeating: "é", count: MAX_TRANSCRIPT_CORRECTION_SOURCE_BYTES)).utf8.count,
            equals: MAX_TRANSCRIPT_CORRECTION_SOURCE_BYTES,
            "correction source prefill should clip at character boundaries"
        )

        try expect(
            correctionEditorValidationIssue(
                source: "   ",
                replacement: "draft replacement",
                isEditing: false,
                existingCorrections: []
            ),
            equals: .missingSource,
            "correction editor should reject an empty source without discarding the replacement draft"
        )
        try expect(
            correctionEditorValidationIssue(
                source: "spoken phrase",
                replacement: "\n\t",
                isEditing: false,
                existingCorrections: []
            ),
            equals: .missingReplacement,
            "correction editor should reject an empty replacement"
        )
        try expect(
            correctionEditorValidationIssue(
                source: String(repeating: "é", count: 257),
                replacement: "replacement",
                isEditing: false,
                existingCorrections: []
            ),
            equals: .sourceTooLong,
            "correction editor should enforce the UTF-8 source limit before saving"
        )
        try expect(
            correctionEditorValidationIssue(
                source: "spoken phrase",
                replacement: "unsupported\u{0}replacement",
                isEditing: false,
                existingCorrections: []
            ),
            equals: .unsupportedReplacementCharacters,
            "correction editor should reject unsupported control characters before normalization"
        )

        let fullEditorCorrections = (0..<MAX_TRANSCRIPT_CORRECTIONS).map {
            TranscriptCorrection(source: "source-\($0)", replacement: "replacement-\($0)")
        }
        try expect(
            correctionEditorValidationIssue(
                source: "new source",
                replacement: "new replacement",
                isEditing: false,
                existingCorrections: fullEditorCorrections
            ),
            equals: .correctionLimitReached,
            "correction editor should explain why a new item cannot be added at the storage cap"
        )
        try expect(
            correctionEditorValidationIssue(
                source: " SOURCE-0 ",
                replacement: "updated replacement",
                isEditing: false,
                existingCorrections: fullEditorCorrections
            ),
            equals: nil,
            "correction editor should still allow updating a matching item at the storage cap"
        )
        try expect(
            correctionEditorValidationIssue(
                source: "renamed source",
                replacement: "updated replacement",
                isEditing: true,
                existingCorrections: fullEditorCorrections
            ),
            equals: nil,
            "editing an existing item should remain possible at the storage cap"
        )

        let normalized = normalizedTranscriptCorrections([
            TranscriptCorrection(source: "  Yeti   Nano  ", replacement: "  Blue mic  "),
            TranscriptCorrection(source: "yeti nano", replacement: "USB mic"),
            TranscriptCorrection(source: "", replacement: "ignored"),
            TranscriptCorrection(source: "empty replacement", replacement: "   ")
        ])
        try expect(
            normalized,
            equals: [TranscriptCorrection(source: "yeti nano", replacement: "USB mic")],
            "normalization should trim, drop incomplete entries, collapse duplicate sources, and keep the latest replacement"
        )

        let searchable = [
            TranscriptCorrection(source: "ship pans key", replacement: "Szypański"),
            TranscriptCorrection(source: "fluid audio", replacement: "FluidAudio"),
            TranscriptCorrection(source: "right option", replacement: "Right Option"),
        ]
        try expect(
            correctionSearchMatchIndices(in: searchable, query: "szypanski"),
            equals: [0],
            "correction search should ignore diacritics"
        )
        try expect(
            correctionSearchMatchIndices(in: searchable, query: "fluid audio"),
            equals: [1],
            "correction search should require every whitespace-separated term"
        )
        try expect(
            correctionSearchMatchIndices(in: searchable, query: "   "),
            equals: [0, 1, 2],
            "empty correction search should preserve stable source order"
        )
        try expect(
            correctionContextSelection(clickedRow: 2,
                                       selectedRows: IndexSet(integersIn: 0..<2),
                                       rowCount: 4),
            equals: IndexSet(integer: 2),
            "a shortcut menu on an unselected dictionary row should select only that row"
        )
        try expect(
            correctionContextSelection(clickedRow: 1,
                                       selectedRows: IndexSet(integersIn: 0..<2),
                                       rowCount: 4),
            equals: IndexSet(integersIn: 0..<2),
            "a shortcut menu on a selected dictionary row should preserve multi-selection"
        )
        try expect(
            correctionContextSelection(clickedRow: -1,
                                       selectedRows: IndexSet(integersIn: 1..<3),
                                       rowCount: 4),
            equals: IndexSet(integersIn: 1..<3),
            "keyboard shortcut-menu invocation should preserve dictionary selection"
        )
        try expect(
            sortedTranscriptCorrectionIndices([0, 1, 2],
                                              in: searchable,
                                              by: .source,
                                              ascending: true),
            equals: [1, 2, 0],
            "correction manager should sort heard text ascending"
        )
        try expect(
            sortedTranscriptCorrectionIndices([0, 1, 2],
                                              in: searchable,
                                              by: .replacement,
                                              ascending: false),
            equals: [0, 2, 1],
            "correction manager should sort paste text descending"
        )
        try expect(
            sortedTranscriptCorrectionIndices([2, 99, 0],
                                              in: searchable,
                                              by: .source,
                                              ascending: true),
            equals: [2, 0],
            "correction manager sorting should ignore stale indices"

        )
        try expect(shouldShowInlineCorrectionMenuEntries(count: 1), equals: true,
                   "small correction sets should remain directly editable from the menu")
        try expect(shouldShowInlineCorrectionMenuEntries(count: MAX_INLINE_CORRECTION_MENU_ENTRIES), equals: true,
                   "the inline correction menu limit should be inclusive")
        try expect(shouldShowInlineCorrectionMenuEntries(count: MAX_INLINE_CORRECTION_MENU_ENTRIES + 1), equals: false,
                   "large correction sets should use the searchable manager instead of growing the menu")

        let boundedCorrections = normalizedTranscriptCorrections(
            [
                TranscriptCorrection(source: String(repeating: "s", count: MAX_TRANSCRIPT_CORRECTION_SOURCE_BYTES + 1),
                                     replacement: "replacement"),
                TranscriptCorrection(source: "source",
                                     replacement: String(repeating: "r", count: MAX_TRANSCRIPT_CORRECTION_REPLACEMENT_BYTES + 1)),
                TranscriptCorrection(source: "nul\u{0}source", replacement: "replacement"),
                TranscriptCorrection(source: "valid", replacement: "replacement")
            ]
            + (0..<(MAX_TRANSCRIPT_CORRECTIONS + 3)).map {
                TranscriptCorrection(source: "source-\($0)", replacement: "replacement-\($0)")
            }
            + [
                TranscriptCorrection(source: "source-0", replacement: "updated")
            ]
        )
        try expect(
            boundedCorrections.count,
            equals: MAX_TRANSCRIPT_CORRECTIONS,
            "normalization should cap stored correction count"
        )
        try expect(
            boundedCorrections.first,
            equals: TranscriptCorrection(source: "valid", replacement: "replacement"),
            "normalization should keep valid corrections while dropping oversized and NUL-containing entries"
        )
        try expect(
            boundedCorrections.dropFirst().first,
            equals: TranscriptCorrection(source: "source-0", replacement: "updated"),
            "normalization should still let later duplicates update retained corrections"
        )
        try expect(
            boundedCorrections.contains(where: { $0.source == "source-\(MAX_TRANSCRIPT_CORRECTIONS)" }),
            equals: false,
            "normalization should drop new unique corrections after the cap"
        )

        let applied = TranscriptCorrector.apply(
            to: "parakeet tdt and parakeetish and PARakeet",
            corrections: [
                TranscriptCorrection(source: "parakeet", replacement: "Presspeech"),
                TranscriptCorrection(source: "parakeet tdt", replacement: "Parakeet TDT")
            ]
        )
        try expect(
            applied.text,
            equals: "Parakeet TDT and parakeetish and Presspeech",
            "corrections should prefer longer phrases and respect word boundaries"
        )
        try expect(
            applied.appliedCount,
            equals: 2,
            "correction count should track applied non-overlapping replacements"
        )

        let transferred = try TranscriptCorrectionsTransfer.decode(
            TranscriptCorrectionsTransfer.encode([
                TranscriptCorrection(source: "  Right Option  ", replacement: "R-Option")
            ])
        )
        try expect(
            transferred,
            equals: [TranscriptCorrection(source: "Right Option", replacement: "R-Option")],
            "document transfer should round-trip normalized corrections"
        )

        let legacyData = try JSONEncoder().encode([
            TranscriptCorrection(source: "  old phrase  ", replacement: "new phrase")
        ])
        try expect(
            try TranscriptCorrectionsTransfer.decode(legacyData),
            equals: [TranscriptCorrection(source: "old phrase", replacement: "new phrase")],
            "legacy bare-array correction files should remain importable"
        )

        var oversizedDecodeRejected = false
        do {
            _ = try TranscriptCorrectionsTransfer.decode(
                Data(repeating: 0x20, count: TranscriptCorrectionsTransfer.maxFileBytes + 1)
            )
        } catch let error as TranscriptCorrectionsTransferError {
            if case .fileTooLarge = error {
                oversizedDecodeRejected = true
            }
        }
        try expect(oversizedDecodeRejected, equals: true,
                   "correction transfer should reject oversized in-memory data before decoding")

        let transferTmpDir = URL(fileURLWithPath: NSTemporaryDirectory())
        let transferFileManager = FileManager.default
        let oversized = transferTmpDir
            .appendingPathComponent("presspeech-corrections-oversized-\(UUID().uuidString).json")
        try Data(repeating: 0x20, count: TranscriptCorrectionsTransfer.maxFileBytes + 1)
            .write(to: oversized)
        defer { try? transferFileManager.removeItem(at: oversized) }
        var oversizedRejected = false
        do {
            _ = try TranscriptCorrectionsTransfer.read(from: oversized)
        } catch let error as TranscriptCorrectionsTransferError {
            if case .fileTooLarge = error {
                oversizedRejected = true
            }
        }
        try expect(oversizedRejected, equals: true,
                   "correction transfer should reject oversized files before decoding")

        let nonFile = transferTmpDir
            .appendingPathComponent("presspeech-corrections-directory-\(UUID().uuidString)")
        try transferFileManager.createDirectory(at: nonFile, withIntermediateDirectories: false)
        defer { try? transferFileManager.removeItem(at: nonFile) }
        var nonFileRejected = false
        do {
            _ = try TranscriptCorrectionsTransfer.read(from: nonFile)
        } catch let error as TranscriptCorrectionsTransferError {
            if case .notRegularFile = error {
                nonFileRejected = true
            }
        }
        try expect(nonFileRejected, equals: true,
                   "correction transfer should reject non-file paths")

        let readTarget = transferTmpDir
            .appendingPathComponent("presspeech-corrections-read-target-\(UUID().uuidString).json")
        try TranscriptCorrectionsTransfer.write(
            [TranscriptCorrection(source: "source", replacement: "replacement")],
            to: readTarget
        )
        defer { try? transferFileManager.removeItem(at: readTarget) }
        let readLink = transferTmpDir
            .appendingPathComponent("presspeech-corrections-read-link-\(UUID().uuidString).json")
        try transferFileManager.createSymbolicLink(at: readLink, withDestinationURL: readTarget)
        defer { try? transferFileManager.removeItem(at: readLink) }
        var symlinkReadRejected = false
        do {
            _ = try TranscriptCorrectionsTransfer.read(from: readLink)
        } catch let error as TranscriptCorrectionsTransferError {
            if case .notRegularFile = error {
                symlinkReadRejected = true
            }
        }
        try expect(symlinkReadRejected, equals: true,
                   "correction transfer should reject reads through leaf symlinks")

        let writeTarget = transferTmpDir
            .appendingPathComponent("presspeech-corrections-write-target-\(UUID().uuidString).json")
        try Data("target\n".utf8).write(to: writeTarget)
        defer { try? transferFileManager.removeItem(at: writeTarget) }
        let writeLink = transferTmpDir
            .appendingPathComponent("presspeech-corrections-write-link-\(UUID().uuidString).json")
        try transferFileManager.createSymbolicLink(at: writeLink, withDestinationURL: writeTarget)
        defer { try? transferFileManager.removeItem(at: writeLink) }
        var symlinkWriteRejected = false
        do {
            try TranscriptCorrectionsTransfer.write(
                [TranscriptCorrection(source: "source", replacement: "replacement")],
                to: writeLink
            )
        } catch let error as TranscriptCorrectionsTransferError {
            if case .notRegularFile = error {
                symlinkWriteRejected = true
            }
        }
        try expect(symlinkWriteRejected, equals: true,
                   "correction transfer should reject writes through leaf symlinks")
        try expect(
            String(data: try Data(contentsOf: writeTarget), encoding: .utf8),
            equals: "target\n",
            "correction transfer symlink rejection should leave the target untouched"
        )

        let remoteOnlyChange = mergedTranscriptCorrectionsForSync(
            base: [TranscriptCorrection(source: "old phrase", replacement: "old")],
            local: [TranscriptCorrection(source: "old phrase", replacement: "old")],
            remote: [TranscriptCorrection(source: "old phrase", replacement: "remote")]
        )
        try expect(
            remoteOnlyChange,
            equals: TranscriptCorrectionSyncMergeResult(
                corrections: [TranscriptCorrection(source: "old phrase", replacement: "remote")],
                conflictingSources: []
            ),
            "sync merge should accept remote changes when local has not changed"
        )

        let nonConflictingMerge = mergedTranscriptCorrectionsForSync(
            base: [
                TranscriptCorrection(source: "shared", replacement: "old"),
                TranscriptCorrection(source: "removed locally", replacement: "old")
            ],
            local: [TranscriptCorrection(source: "shared", replacement: "local")],
            remote: [
                TranscriptCorrection(source: "shared", replacement: "old"),
                TranscriptCorrection(source: "removed locally", replacement: "old"),
                TranscriptCorrection(source: "remote only", replacement: "remote")
            ]
        )
        try expect(
            nonConflictingMerge,
            equals: TranscriptCorrectionSyncMergeResult(
                corrections: [
                    TranscriptCorrection(source: "shared", replacement: "local"),
                    TranscriptCorrection(source: "remote only", replacement: "remote")
                ],
                conflictingSources: []
            ),
            "sync merge should combine non-conflicting local edits, local deletes, and remote additions"
        )

        let conflictingMerge = mergedTranscriptCorrectionsForSync(
            base: [TranscriptCorrection(source: "same source", replacement: "old")],
            local: [TranscriptCorrection(source: "same source", replacement: "local")],
            remote: [TranscriptCorrection(source: "same source", replacement: "remote")]
        )
        try expect(
            conflictingMerge,
            equals: TranscriptCorrectionSyncMergeResult(corrections: [],
                                                        conflictingSources: ["same source"]),
            "sync merge should report same-source edits that changed differently on both sides"
        )

        let normalizedSyncPath = normalizedCorrectionSyncFilePath(" /tmp/presspeech/../Presspeech Corrections.presspeech-corrections\n")
        try expect(
            normalizedSyncPath,
            equals: "/tmp/Presspeech Corrections.presspeech-corrections",
            "correction sync path normalization should trim and standardize absolute paths"
        )
        try expect(
            normalizedCorrectionSyncFilePath("relative/path.presspeech-corrections"),
            equals: nil,
            "correction sync path normalization should reject relative paths"
        )
        try expect(
            normalizedCorrectionSyncFilePath("/tmp/\u{0}presspeech.presspeech-corrections"),
            equals: nil,
            "correction sync path normalization should reject NUL bytes"
        )
        try expect(
            normalizedCorrectionSyncFilePath("/" + String(repeating: "x", count: MAX_CORRECTION_SYNC_PATH_BYTES)),
            equals: nil,
            "correction sync path normalization should reject oversized paths"
        )

        // Reject leaf-symlinks at the sync path so an attacker who can
        // plant a symlink at the persisted sync-file location cannot use
        // the periodic auto-write to overwrite an unrelated file.
        let tmpDir = URL(fileURLWithPath: NSTemporaryDirectory())
        let fm = FileManager.default
        let nonexistent = tmpDir.appendingPathComponent("presspeech-sync-test-missing-\(UUID().uuidString).json")
        try validateCorrectionSyncPath(nonexistent) // missing files are allowed (first-time write)

        let regular = tmpDir.appendingPathComponent("presspeech-sync-test-regular-\(UUID().uuidString).json")
        try Data("{}".utf8).write(to: regular)
        defer { try? fm.removeItem(at: regular) }
        try validateCorrectionSyncPath(regular)

        let target = tmpDir.appendingPathComponent("presspeech-sync-test-target-\(UUID().uuidString).json")
        try Data("{}".utf8).write(to: target)
        defer { try? fm.removeItem(at: target) }
        let link = tmpDir.appendingPathComponent("presspeech-sync-test-link-\(UUID().uuidString).json")
        try fm.createSymbolicLink(at: link, withDestinationURL: target)
        defer { try? fm.removeItem(at: link) }
        var rejected = false
        do {
            try validateCorrectionSyncPath(link)
        } catch is TranscriptCorrectionsSyncPathError {
            rejected = true
        }
        try expect(rejected, equals: true,
                   "validateCorrectionSyncPath should reject a leaf symlink")
        try expect(
            shouldStopCorrectionSync(afterPathValidationError: TranscriptCorrectionsSyncPathError.isSymbolicLink),
            equals: true,
            "unsafe sync paths should stop configured correction sync"
        )
        try expect(
            shouldStopCorrectionSync(afterPathValidationError: NSError(domain: "PresspeechTest", code: 1)),
            equals: false,
            "unrelated sync errors should not clear the configured correction sync path"
        )
        try expect(
            correctionSyncFingerprint(for: link),
            equals: nil,
            "correction sync fingerprinting should not follow leaf symlinks"
        )

        let sameSizeA = tmpDir.appendingPathComponent("presspeech-sync-fingerprint-a-\(UUID().uuidString).json")
        let sameSizeB = tmpDir.appendingPathComponent("presspeech-sync-fingerprint-b-\(UUID().uuidString).json")
        try Data("aaaa".utf8).write(to: sameSizeA)
        try Data("bbbb".utf8).write(to: sameSizeB)
        defer {
            try? fm.removeItem(at: sameSizeA)
            try? fm.removeItem(at: sameSizeB)
        }
        let sharedModifiedAt = Date(timeIntervalSince1970: 1_700_000_000)
        try fm.setAttributes([.modificationDate: sharedModifiedAt], ofItemAtPath: sameSizeA.path)
        try fm.setAttributes([.modificationDate: sharedModifiedAt], ofItemAtPath: sameSizeB.path)

        guard let fingerprintA = correctionSyncFingerprint(for: sameSizeA),
              let fingerprintB = correctionSyncFingerprint(for: sameSizeB) else {
            throw SelfTestFailure.failed("correction sync fingerprint should read regular files")
        }
        try expect(
            fingerprintA.size,
            equals: fingerprintB.size,
            "same-size sync files should have equal size metadata in the fingerprint"
        )
        try expect(
            fingerprintA == fingerprintB,
            equals: false,
            "correction sync fingerprint should detect content changes even when file size matches"
        )

        // The full legal correction set must encode within the
        // transfer cap: 512 entries at the per-field caps is ~2.4 MB
        // encoded, which silently failed to save under the old 2 MiB
        // cap. Also pin that it really is over 2 MiB, documenting why
        // the cap moved to 4 MiB.
        let worstCaseSet = (0..<MAX_TRANSCRIPT_CORRECTIONS).map { index in
            TranscriptCorrection(
                source: String(format: "%06d-", index)
                    + String(repeating: "s", count: MAX_TRANSCRIPT_CORRECTION_SOURCE_BYTES - 7),
                replacement: String(repeating: "r", count: MAX_TRANSCRIPT_CORRECTION_REPLACEMENT_BYTES)
            )
        }
        let worstCaseData = try TranscriptCorrectionsTransfer.encode(worstCaseSet)
        try expect(
            worstCaseData.count > 2 * 1024 * 1024,
            equals: true,
            "worst-case legal correction set should exceed the old 2 MiB cap (why the cap is now larger)"
        )
        try expect(
            worstCaseData.count <= TranscriptCorrectionsTransfer.maxFileBytes,
            equals: true,
            "worst-case legal correction set must fit the transfer cap with JSON-overhead headroom"
        )
        try expect(
            try TranscriptCorrectionsTransfer.decode(worstCaseData).count,
            equals: MAX_TRANSCRIPT_CORRECTIONS,
            "worst-case legal correction set should round-trip through the transfer cap"
        )

        // Near the correction cap a merge can briefly exceed it. The
        // sync baseline must store the same normalized (capped) list
        // that is written to the file — a raw over-cap baseline makes
        // the capped-out entry look like a local deletion later.
        let sharedNearCap = (0..<(MAX_TRANSCRIPT_CORRECTIONS - 1)).map {
            TranscriptCorrection(source: "shared-\($0)", replacement: "same")
        }
        let nearCapMerge = mergedTranscriptCorrectionsForSync(
            base: sharedNearCap,
            local: sharedNearCap + [TranscriptCorrection(source: "local-extra", replacement: "local")],
            remote: sharedNearCap + [TranscriptCorrection(source: "remote-extra", replacement: "remote")]
        )
        try expect(
            nearCapMerge.conflictingSources,
            equals: [],
            "near-cap merge with disjoint additions should not conflict"
        )
        try expect(
            nearCapMerge.corrections.count,
            equals: MAX_TRANSCRIPT_CORRECTIONS + 1,
            "near-cap merge result can exceed the cap before normalization"
        )
        let nearCapNormalized = normalizedTranscriptCorrections(nearCapMerge.corrections)
        try expect(
            nearCapNormalized.count,
            equals: MAX_TRANSCRIPT_CORRECTIONS,
            "normalizing the near-cap merge result should drop the over-cap entry"
        )
        try expect(
            nearCapNormalized.contains(TranscriptCorrection(source: "local-extra", replacement: "local")),
            equals: true,
            "normalization keeps the earlier (local) addition at the cap"
        )
        try expect(
            nearCapNormalized.contains(TranscriptCorrection(source: "remote-extra", replacement: "remote")),
            equals: false,
            "the capped-out remote addition is exactly what the baseline must also drop"
        )

        // Fingerprinting the bytes we wrote must agree with a fresh
        // disk fingerprint when nobody touched the file in between —
        // the sync path uses the in-memory form so a provider replacing
        // the file in the write-to-fingerprint window is still detected
        // by the next scan.
        let fingerprintWriteTarget = tmpDir
            .appendingPathComponent("presspeech-sync-written-fingerprint-\(UUID().uuidString).json")
        let fingerprintWrittenData = try TranscriptCorrectionsTransfer.write(
            [TranscriptCorrection(source: "fingerprint", replacement: "match")],
            to: fingerprintWriteTarget
        )
        defer { try? fm.removeItem(at: fingerprintWriteTarget) }
        guard let fingerprintFromDisk = correctionSyncFingerprint(for: fingerprintWriteTarget) else {
            throw SelfTestFailure.failed("disk fingerprint should be readable right after a write")
        }
        try expect(
            correctionSyncFingerprint(forWrittenData: fingerprintWrittenData, at: fingerprintWriteTarget),
            equals: fingerprintFromDisk,
            "fingerprint of written bytes should match the disk fingerprint of an untouched file"
        )

        // Counted decode keeps the file's pre-normalization entry count
        // so the import dialog can disclose truncation.
        let countedOriginal = (0..<(MAX_TRANSCRIPT_CORRECTIONS + 5)).map {
            TranscriptCorrection(source: "counted-\($0)", replacement: "kept")
        }
        let countedEncoder = JSONEncoder()
        countedEncoder.dateEncodingStrategy = .iso8601
        let countedDocument = TranscriptCorrectionsDocument(
            schemaVersion: TranscriptCorrectionsTransfer.schemaVersion,
            exportedAt: Date(),
            appVersion: currentBundleVersion(),
            corrections: countedOriginal
        )
        let counted = try TranscriptCorrectionsTransfer.decodeCounted(countedEncoder.encode(countedDocument))
        try expect(
            counted.originalCount,
            equals: MAX_TRANSCRIPT_CORRECTIONS + 5,
            "counted decode should report the file's pre-normalization entry count"
        )
        try expect(
            counted.corrections.count,
            equals: MAX_TRANSCRIPT_CORRECTIONS,
            "counted decode should still normalize down to the correction cap"
        )
        let countedLegacy = try TranscriptCorrectionsTransfer.decodeCounted(
            try JSONEncoder().encode([TranscriptCorrection(source: "  legacy  ", replacement: "entry")])
        )
        try expect(
            countedLegacy,
            equals: TranscriptCorrectionsTransfer.CountedDecodeResult(
                corrections: [TranscriptCorrection(source: "legacy", replacement: "entry")],
                originalCount: 1
            ),
            "counted decode should support legacy bare-array files"
        )

        // Import dialog copy: state the original count when entries
        // will be dropped, and warn before a cap-overflowing merge.
        try expect(
            correctionImportCountText(sourceName: "file.presspeech-corrections",
                                      originalCount: 3,
                                      keptCount: 3),
            equals: "file.presspeech-corrections contains 3 corrections.",
            "import count text should stay simple when nothing is dropped"
        )
        let truncatedImportText = correctionImportCountText(
            sourceName: "big.presspeech-corrections",
            originalCount: MAX_TRANSCRIPT_CORRECTIONS + 88,
            keptCount: MAX_TRANSCRIPT_CORRECTIONS
        )
        try expect(
            truncatedImportText.contains("contains \(MAX_TRANSCRIPT_CORRECTIONS + 88) entries"),
            equals: true,
            "import count text should state the file's original entry count when entries are dropped"
        )
        try expect(
            truncatedImportText.contains("first \(MAX_TRANSCRIPT_CORRECTIONS)"),
            equals: true,
            "import count text should state how many corrections will actually be kept"
        )
        try expect(
            correctionImportMergeCapWarningText(existingCount: 10, newCount: 10),
            equals: nil,
            "merge cap warning should stay silent when the merged set fits"
        )
        try expect(
            correctionImportMergeCapWarningText(existingCount: MAX_TRANSCRIPT_CORRECTIONS,
                                                newCount: 8)?.contains("8 would be dropped"),
            equals: true,
            "merge cap warning should state how many corrections a merge would drop"
        )
    }

    private static func testFillerWordRemoval() throws {
        // Mid-sentence filler with surrounding commas → orphan comma
        // gets collapsed.
        let mid = FillerWordRemover.apply(to: "So, um, I was going.")
        try expect(mid.text, equals: "So, I was going.", "mid-sentence filler should leave a single comma")
        try expect(mid.removedCount, equals: 1, "mid-sentence filler removal count")

        // Sentence-initial filler with leading-comma cleanup AND
        // capitalisation restored (the original 'U' was uppercase).
        let initial = FillerWordRemover.apply(to: "Um, hello.")
        try expect(initial.text, equals: "Hello.", "sentence-initial filler should re-capitalise the next word")
        try expect(initial.removedCount, equals: 1, "sentence-initial filler removal count")

        let secondSentence = FillerWordRemover.apply(to: "This is the first sentence. Um this is the second sentence.")
        try expect(
            secondSentence.text,
            equals: "This is the first sentence. This is the second sentence.",
            "sentence-initial filler after a period should re-capitalise the next word"
        )
        try expect(secondSentence.removedCount, equals: 1, "second-sentence filler removal count")

        let secondSentenceWithComma = FillerWordRemover.apply(to: "This is the first sentence. Um, this is the second sentence.")
        try expect(
            secondSentenceWithComma.text,
            equals: "This is the first sentence. This is the second sentence.",
            "sentence-initial filler after a period should not leave an orphan comma"
        )
        try expect(secondSentenceWithComma.removedCount, equals: 1, "second-sentence comma filler removal count")

        let secondSentenceQuestion = FillerWordRemover.apply(to: "This is the first sentence. Um? this is the second sentence.")
        try expect(
            secondSentenceQuestion.text,
            equals: "This is the first sentence. This is the second sentence.",
            "sentence-initial filler with its own punctuation should take that punctuation with it"
        )
        try expect(secondSentenceQuestion.removedCount, equals: 1, "second-sentence question filler removal count")

        let capitalizedMidSentence = FillerWordRemover.apply(to: "This is not a sentence boundary Um this stays lowercase.")
        try expect(
            capitalizedMidSentence.text,
            equals: "This is not a sentence boundary this stays lowercase.",
            "capitalized fillers away from sentence starts should not force capitalization"
        )
        try expect(capitalizedMidSentence.removedCount, equals: 1, "capitalized mid-sentence filler removal count")

        // Bare filler with adjacent punctuation collapses to empty.
        let bare = FillerWordRemover.apply(to: "Um.")
        try expect(bare.text, equals: "", "bare filler with trailing punctuation should leave empty string")
        try expect(bare.removedCount, equals: 1, "bare filler removal count")

        // Filler with no surrounding punctuation just leaves a space
        // that gets collapsed away.
        let inline = FillerWordRemover.apply(to: "I'm uh going to the store.")
        try expect(inline.text, equals: "I'm going to the store.", "inline filler should collapse the leftover whitespace")
        try expect(inline.removedCount, equals: 1, "inline filler removal count")

        // Compound interjection "uh-huh" must NOT match — the hyphen is
        // part of the boundary class.
        let uhHuh = FillerWordRemover.apply(to: "Yeah, uh-huh.")
        try expect(uhHuh.text, equals: "Yeah, uh-huh.", "uh-huh must not be stripped")
        try expect(uhHuh.removedCount, equals: 0, "uh-huh removal count")

        // Words that *contain* a filler substring must not match. "her"
        // contains "er", "sum" contains "um", "exercise" contains "er".
        let contains = FillerWordRemover.apply(to: "Her sum exercise is harder.")
        try expect(contains.text, equals: "Her sum exercise is harder.", "filler substrings inside larger words must be preserved")
        try expect(contains.removedCount, equals: 0, "no removals when fillers are embedded in real words")

        // Multiple fillers in one utterance all get stripped.
        let multi = FillerWordRemover.apply(to: "Um, ah, I uh think so.")
        try expect(multi.text, equals: "I think so.", "multiple fillers should all be removed and artifacts cleaned up")
        try expect(multi.removedCount, equals: 3, "multi-filler removal count")

        // Empty input should be a no-op.
        let empty = FillerWordRemover.apply(to: "")
        try expect(empty.text, equals: "", "empty input passes through unchanged")
        try expect(empty.removedCount, equals: 0, "empty input has zero removals")

        // No fillers present → identical text, zero removals.
        let clean = FillerWordRemover.apply(to: "Hello world.")
        try expect(clean.text, equals: "Hello world.", "filler-free input passes through unchanged")
        try expect(clean.removedCount, equals: 0, "filler-free input has zero removals")

        // Elongated fillers — common in real dictation. The word-
        // boundary lookahead would have rejected these without the
        // per-pattern trailing-repeat allowance.
        let elongatedUm = FillerWordRemover.apply(to: "Ummm, hello.")
        try expect(elongatedUm.text, equals: "Hello.", "ummm should be stripped like um")
        try expect(elongatedUm.removedCount, equals: 1, "elongated um removal count")

        let elongatedUh = FillerWordRemover.apply(to: "Uhhh I think so.")
        try expect(elongatedUh.text, equals: "I think so.", "uhhh should be stripped like uh")
        try expect(elongatedUh.removedCount, equals: 1, "elongated uh removal count")

        let elongatedAh = FillerWordRemover.apply(to: "Ahhh, that makes sense.")
        try expect(elongatedAh.text, equals: "That makes sense.", "ahhh should be stripped like ah")
        try expect(elongatedAh.removedCount, equals: 1, "elongated ah removal count")

        // `hm+` covers both "hm" (single m) and "hmmm" (extended). The
        // earlier fixed-list "hmm" entry rejected the single-m form.
        let shortHm = FillerWordRemover.apply(to: "Hm, interesting.")
        try expect(shortHm.text, equals: "Interesting.", "short hm should be stripped like hmm")
        try expect(shortHm.removedCount, equals: 1, "short hm removal count")

        // Words containing the new repeat-friendly patterns must still
        // pass through. "ohm" embeds "hm" but has a leading letter.
        let embedded = FillerWordRemover.apply(to: "An ohm is a unit.")
        try expect(embedded.text, equals: "An ohm is a unit.", "ohm must not match hm")
        try expect(embedded.removedCount, equals: 0, "ohm should produce zero removals")

        // Two consecutive fillers used to leave ",," because the
        // comma-collapse pass was single-pass/non-overlapping: it
        // consumed one ", ," pair and the whitespace-before-punctuation
        // pass then glued the leftover " ," into ",,".
        let consecutive = FillerWordRemover.apply(to: "So, um, uh, yes.")
        try expect(consecutive.text, equals: "So, yes.", "consecutive fillers should collapse to a single comma")
        try expect(consecutive.removedCount, equals: 2, "consecutive filler removal count")

        // Three consecutive fillers exercise runs longer than one
        // collapse step.
        let tripleRun = FillerWordRemover.apply(to: "He said, um, uh, er, no.")
        try expect(tripleRun.text, equals: "He said, no.", "a run of three fillers should collapse to a single comma")
        try expect(tripleRun.removedCount, equals: 3, "triple filler removal count")

        // Consecutive fillers mid-sentence keep exactly one comma,
        // matching the single-filler behavior above.
        let midRun = FillerWordRemover.apply(to: "I think, um, uh, we should go.")
        try expect(midRun.text, equals: "I think, we should go.", "mid-sentence consecutive fillers should keep one comma")
        try expect(midRun.removedCount, equals: 2, "mid-sentence consecutive filler removal count")

        // Trailing filler before terminal punctuation used to leave
        // ",." because no pass cleaned a comma glued onto a period.
        let trailing = FillerWordRemover.apply(to: "That's all, um.")
        try expect(trailing.text, equals: "That's all.", "trailing filler should not leave a comma before the period")
        try expect(trailing.removedCount, equals: 1, "trailing filler removal count")

        let beforeQuestion = FillerWordRemover.apply(to: "Is that right, um?")
        try expect(beforeQuestion.text, equals: "Is that right?", "filler before a question mark should not leave a comma")
        try expect(beforeQuestion.removedCount, equals: 1, "filler before question mark removal count")

        let beforeBang = FillerWordRemover.apply(to: "Stop, um!")
        try expect(beforeBang.text, equals: "Stop!", "filler before an exclamation mark should not leave a comma")
        try expect(beforeBang.removedCount, equals: 1, "filler before exclamation mark removal count")

        // Sentence-initial filler with its own terminal punctuation:
        // the leading-strip class must include "?" and "!" or the
        // orphaned punctuation survives ("Um? What?" → "? What?").
        let leadingQuestion = FillerWordRemover.apply(to: "Um? What?")
        try expect(leadingQuestion.text, equals: "What?", "leading filler question should take its punctuation with it")
        try expect(leadingQuestion.removedCount, equals: 1, "leading filler question removal count")

        let leadingBang = FillerWordRemover.apply(to: "Ah! Careful.")
        try expect(leadingBang.text, equals: "Careful.", "leading filler exclamation should take its punctuation with it")
        try expect(leadingBang.removedCount, equals: 1, "leading filler exclamation removal count")
    }

    private static func testAudioInputDeviceFiltering() throws {
        let pseudo = AudioInputDevice(id: 1,
                                      uid: "CADefaultDeviceAggregate-42159-0",
                                      name: "CADefaultDeviceAggregate-42159-0")
        let real = AudioInputDevice(id: 2,
                                    uid: "real-yeti-nano",
                                    name: "Yeti Nano")

        try expect(
            isDefaultAggregateAudioInputDevice(pseudo),
            equals: true,
            "CoreAudio default aggregate devices should be recognized"
        )
        try expect(
            isDefaultAggregateAudioInputDevice(real),
            equals: false,
            "named microphones should remain selectable"
        )
        try expect(
            normalizedInputDevicePreference(" Yeti Nano\n"),
            equals: "Yeti Nano",
            "input device preferences should be trimmed before storing"
        )
        try expect(
            normalizedInputDevicePreference(pseudo.uid),
            equals: nil,
            "input device preferences should reject CoreAudio default aggregates"
        )
        try expect(
            normalizedInputDevicePreference("real\u{0}device"),
            equals: nil,
            "input device preferences should reject NUL bytes"
        )
        try expect(
            normalizedInputDevicePreference(String(repeating: "x", count: MAX_INPUT_DEVICE_PREFERENCE_BYTES + 1)),
            equals: nil,
            "input device preferences should reject oversized values"
        )
        try expect(
            audioInputDevice(matching: pseudo.uid, in: [pseudo, real])?.uid,
            equals: nil,
            "CoreAudio default aggregate preferences should fall back to system default"
        )
        try expect(
            audioInputDevice(matching: " real-yeti-nano\n", in: [real])?.uid,
            equals: "real-yeti-nano",
            "input device preferences should resolve after trimming"
        )
        try expect(
            audioInputDevice(matching: "Yeti Nano", in: [real])?.uid,
            equals: "real-yeti-nano",
            "named microphone preferences should still resolve by display name"
        )
    }

    private static func testSpeechModelStartupStatus() throws {
        try expect(
            speechModelStartupStatusTitle(.init(fractionCompleted: 0,
                                                phase: .listing)),
            equals: "Checking speech model files…",
            "listing phase should be visible during first-launch model setup"
        )
        try expect(
            speechModelStartupStatusTitle(.init(fractionCompleted: 0.25,
                                                phase: .downloading(completedFiles: 2, totalFiles: 4))),
            equals: "Downloading speech model… 50% (2/4)",
            "download phase should show quantized progress"
        )
        try expect(
            speechModelStartupStatusTitle(.init(fractionCompleted: 0.5,
                                                phase: .downloading(completedFiles: 0, totalFiles: 0))),
            equals: "Loading cached speech model…",
            "cached model load should not pretend to download files"
        )
        try expect(
            speechModelStartupStatusTitle(.init(fractionCompleted: 1,
                                                phase: .compiling(modelName: "Encoder.mlmodelc"))),
            equals: "Preparing speech model…",
            "compile phase should be visible without exposing model internals"
        )
        try expect(
            speechModelStartupProgressValue(.init(fractionCompleted: 0,
                                                  phase: .listing)),
            equals: nil,
            "listing phase should show indeterminate model progress"
        )
        try expect(
            speechModelStartupProgressValue(.init(fractionCompleted: 0.25,
                                                  phase: .downloading(completedFiles: 2, totalFiles: 4))),
            equals: 0.5,
            "download phase should expose normalized model progress"
        )
        try expect(
            speechModelStartupProgressValue(.init(fractionCompleted: 0.5,
                                                  phase: .downloading(completedFiles: 0, totalFiles: 0))),
            equals: nil,
            "cached model load should show indeterminate model progress"
        )
        try expect(
            speechModelStartupProgressValue(.init(fractionCompleted: 1,
                                                  phase: .compiling(modelName: "Encoder.mlmodelc"))),
            equals: nil,
            "compile phase should show indeterminate model progress"
        )
        let requiredBytes = speechModelDownloadRequiredBytes(for: .multilingualV3,
                                                             headroomBytes: 100)
        try expect(
            requiredBytes,
            equals: 700 * 1024 * 1024 + 100,
            "speech model download requirement should include model estimate plus headroom"
        )
        try expect(
            speechModelDiskSpaceFailureDetail(profile: .multilingualV3,
                                              availableBytes: requiredBytes - 1,
                                              requiredBytes: requiredBytes)?.contains("Free some disk space"),
            equals: true,
            "low disk-space failures should explain how to recover"
        )
        try expect(
            speechModelDiskSpaceFailureDetail(profile: .multilingualV3,
                                              availableBytes: requiredBytes,
                                              requiredBytes: requiredBytes),
            equals: nil,
            "disk-space check should pass once required space is available"
        )
        try expect(
            speechModelDiskSpaceFailureDetail(profile: .multilingualV3,
                                              availableBytes: nil,
                                              requiredBytes: requiredBytes),
            equals: nil,
            "unknown disk-space readings should not block model startup"
        )
    }

    private static func testModelIntegrity() throws {
        try testSpeechModelCachePathSafety()

        let fm = FileManager.default
        let root = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("presspeech-model-integrity-\(UUID().uuidString)",
                                    isDirectory: true)
        let modelDir = root.appendingPathComponent("Toy.mlmodelc", isDirectory: true)
        try fm.createDirectory(at: modelDir, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: root) }

        let modelFile = modelDir.appendingPathComponent("model.mil")
        try Data("hello".utf8).write(to: modelFile)
        let expected = [
            ModelFileDigest(
                relativePath: "Toy.mlmodelc/model.mil",
                sha256: "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
            )
        ]
        try ModelIntegrity.verifyFiles(root: root,
                                       expectedFiles: expected,
                                       strictDirectories: ["Toy.mlmodelc"])

        var rejectedMismatch = false
        do {
            try ModelIntegrity.verifyFiles(
                root: root,
                expectedFiles: [
                    ModelFileDigest(relativePath: "Toy.mlmodelc/model.mil",
                                    sha256: String(repeating: "0", count: 64))
                ],
                strictDirectories: ["Toy.mlmodelc"]
            )
        } catch is ModelIntegrityError {
            rejectedMismatch = true
        }
        try expect(rejectedMismatch, equals: true,
                   "model integrity should reject digest mismatches")

        try Data("extra".utf8).write(to: modelDir.appendingPathComponent("extra.bin"))
        var rejectedUnexpectedFile = false
        do {
            try ModelIntegrity.verifyFiles(root: root,
                                           expectedFiles: expected,
                                           strictDirectories: ["Toy.mlmodelc"])
        } catch is ModelIntegrityError {
            rejectedUnexpectedFile = true
        }
        try expect(rejectedUnexpectedFile, equals: true,
                   "model integrity should reject unpinned files in strict model bundles")

        try fm.removeItem(at: modelDir.appendingPathComponent("extra.bin"))
        try fm.createDirectory(at: modelDir.appendingPathComponent("empty-extra", isDirectory: true),
                               withIntermediateDirectories: true)
        var rejectedUnexpectedDirectory = false
        do {
            try ModelIntegrity.verifyFiles(root: root,
                                           expectedFiles: expected,
                                           strictDirectories: ["Toy.mlmodelc"])
        } catch is ModelIntegrityError {
            rejectedUnexpectedDirectory = true
        }
        try expect(rejectedUnexpectedDirectory, equals: true,
                   "model integrity should reject unpinned directories in strict model bundles")

        var rejectedBadDigest = false
        do {
            try ModelIntegrity.verifyFiles(
                root: root,
                expectedFiles: [
                    ModelFileDigest(relativePath: "Toy.mlmodelc/model.mil",
                                    sha256: "not-a-sha256")
                ],
                strictDirectories: ["Toy.mlmodelc"]
            )
        } catch is ModelIntegrityError {
            rejectedBadDigest = true
        }
        try expect(rejectedBadDigest, equals: true,
                   "model integrity should reject malformed manifest digests")

        var rejectedDotSegment = false
        do {
            try ModelIntegrity.verifyFiles(
                root: root,
                expectedFiles: [
                    ModelFileDigest(
                        relativePath: "Toy.mlmodelc/./model.mil",
                        sha256: "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
                    )
                ],
                strictDirectories: ["Toy.mlmodelc"]
            )
        } catch is ModelIntegrityError {
            rejectedDotSegment = true
        }
        try expect(rejectedDotSegment, equals: true,
                   "model integrity should reject dot path segments")

        let symlinkedModelFile = modelDir.appendingPathComponent("model-link.mil")
        try fm.createSymbolicLink(at: symlinkedModelFile, withDestinationURL: modelFile)
        var rejectedSymlinkHashRead = false
        do {
            _ = try ModelIntegrity.sha256Hex(of: symlinkedModelFile,
                                             relativePath: "Toy.mlmodelc/model-link.mil")
        } catch is ModelIntegrityError {
            rejectedSymlinkHashRead = true
        }
        try expect(rejectedSymlinkHashRead, equals: true,
                   "model integrity hashing should not follow leaf symlinks")

        let localParakeetV3Cache = speechModelCacheDirectory(for: .multilingualV3)
        if fm.fileExists(atPath: localParakeetV3Cache.path) {
            try ModelIntegrity.verifyParakeetV3Model(at: localParakeetV3Cache)
        }
    }

    private static func testSpeechModelCachePathSafety() throws {
        let fm = FileManager.default
        let root = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("presspeech-cache-safety-\(UUID().uuidString)", isDirectory: true)
        let support = root.appendingPathComponent("FluidAudio", isDirectory: true)
        let cache = support.appendingPathComponent("Models/parakeet-v3", isDirectory: true)
        try fm.createDirectory(at: cache, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: root) }

        try expect(
            isSafeSpeechModelCacheDirectory(
                cache,
                fluidAudioSupportDirectory: support
            ),
            equals: true,
            "speech model cache reset should allow nested FluidAudio cache paths"
        )
        try expect(
            isExistingSpeechModelCacheDirectorySafeForRemoval(cache,
                                                             fluidAudioSupportDirectory: support),
            equals: true,
            "speech model cache reset should allow existing plain cache directories"
        )
        try expect(
            isSafeSpeechModelCacheDirectory(support, fluidAudioSupportDirectory: support),
            equals: false,
            "speech model cache reset should not remove the FluidAudio support root"
        )
        try expect(
            isSafeSpeechModelCacheDirectory(
                support.deletingLastPathComponent().appendingPathComponent("FluidAudioBackup/parakeet-v3", isDirectory: true),
                fluidAudioSupportDirectory: support
            ),
            equals: false,
            "speech model cache reset should reject sibling support directories"
        )
        try expect(
            isSafeSpeechModelCacheDirectory(
                support.appendingPathComponent("../Outside/parakeet-v3", isDirectory: true),
                fluidAudioSupportDirectory: support
            ),
            equals: false,
            "speech model cache reset should reject paths that normalize outside FluidAudio support"
        )

        let outside = root.appendingPathComponent("Outside", isDirectory: true)
        let outsideCache = outside.appendingPathComponent("parakeet-v3", isDirectory: true)
        try fm.createDirectory(at: outsideCache, withIntermediateDirectories: true)

        let leafLink = support.appendingPathComponent("Models/link-cache", isDirectory: true)
        try fm.createSymbolicLink(at: leafLink, withDestinationURL: outsideCache)
        try expect(
            isSafeSpeechModelCacheDirectory(leafLink, fluidAudioSupportDirectory: support),
            equals: true,
            "speech model cache reset path check should remain string-only"
        )
        try expect(
            isExistingSpeechModelCacheDirectorySafeForRemoval(leafLink,
                                                             fluidAudioSupportDirectory: support),
            equals: false,
            "speech model cache reset should reject leaf symlink directories before deletion"
        )

        let linkedParent = support.appendingPathComponent("LinkedModels", isDirectory: true)
        try fm.createSymbolicLink(at: linkedParent, withDestinationURL: outside)
        try expect(
            isExistingSpeechModelCacheDirectorySafeForRemoval(
                linkedParent.appendingPathComponent("parakeet-v3", isDirectory: true),
                fluidAudioSupportDirectory: support
            ),
            equals: false,
            "speech model cache reset should reject symlinked parent directories before deletion"
        )
        try expect(
            isSafeSpeechModelCacheDirectory(speechModelCacheDirectory(for: .multilingualV3)),
            equals: true,
            "FluidAudio v3 cache path should remain inside FluidAudio Application Support"
        )
        let defaultV3Cache = speechModelCacheDirectory(for: .multilingualV3)
        if fm.fileExists(atPath: defaultV3Cache.path) {
            try expect(
                isExistingSpeechModelCacheDirectorySafeForRemoval(defaultV3Cache),
                equals: true,
                "existing FluidAudio v3 cache path should remain removable"
            )
        }
    }

    private static func testUpdate() throws {
        try testUpdateCheckParsing()
        try testUpdateCheckState()
        try testUpdateHelperScript()
        try testUpdateProgressState()
    }

    private static func testUpdateCheckParsing() throws {
        let ok = HTTPURLResponse(url: GITHUB_LATEST_RELEASE_URL,
                                 statusCode: 200,
                                 httpVersion: nil,
                                 headerFields: nil)!
        let notFound = HTTPURLResponse(url: GITHUB_LATEST_RELEASE_URL,
                                       statusCode: 404,
                                       httpVersion: nil,
                                       headerFields: nil)!
        let releaseData = Data(
            #"{"tag_name":"v9.8.7","body":"Notes","html_url":"https://github.com/rcourtman/presspeech/releases/tag/v9.8.7"}"#.utf8
        )

        try expect(
            UpdateCheck.parseLatest(data: releaseData, response: ok),
            equals: .success(GitHubRelease(tagName: "v9.8.7",
                                           version: "9.8.7",
                                           body: "Notes",
                                           htmlURL: "https://github.com/rcourtman/presspeech/releases/tag/v9.8.7")),
            "update parsing should decode typed GitHub release payloads"
        )
        try expect(
            UpdateCheck.parseLatest(data: releaseData, response: notFound),
            equals: .failure(.httpStatus(404)),
            "update parsing should reject non-2xx HTTP responses with the status code"
        )
        let rateLimited = HTTPURLResponse(url: GITHUB_LATEST_RELEASE_URL,
                                          statusCode: 403,
                                          httpVersion: nil,
                                          headerFields: nil)!
        try expect(
            UpdateCheck.parseLatest(data: releaseData, response: rateLimited),
            equals: .failure(.httpStatus(403)),
            "update parsing should surface HTTP 403 distinctly (GitHub rate limiting)"
        )
        let oversizedReleaseData = Data(
            """
            {"tag_name":"v9.8.7","body":"\(String(repeating: "x", count: UpdateCheck.maxReleaseResponseBytes))","html_url":"https://github.com/rcourtman/presspeech/releases/tag/v9.8.7"}
            """.utf8
        )
        try expect(
            oversizedReleaseData.count > UpdateCheck.maxReleaseResponseBytes,
            equals: true,
            "oversized release response fixture should exceed the parser limit"
        )
        try expect(
            UpdateCheck.parseLatest(data: oversizedReleaseData, response: ok),
            equals: .failure(.unexpectedResponse),
            "update parsing should reject oversized release responses before decoding"
        )
        try expect(
            UpdateCheck.parseLatest(data: Data(#"{"tag_name":""}"#.utf8), response: ok),
            equals: .failure(.unexpectedResponse),
            "update parsing should reject empty release tags"
        )
        try expect(
            UpdateCheck.parseLatest(data: Data(#"{"tag_name":"latest"}"#.utf8), response: ok),
            equals: .failure(.unexpectedResponse),
            "update parsing should reject non-version release tags"
        )
        try expect(
            UpdateCheck.parseLatest(data: Data(#"{"tag_name":"v01.2.3"}"#.utf8), response: ok),
            equals: .failure(.unexpectedResponse),
            "update parsing should reject non-normal semver tags"
        )
        try expect(
            UpdateCheck.parseLatest(
                data: Data(#"{"tag_name":"v999999999999999999999999.2.3"}"#.utf8),
                response: ok
            ),
            equals: .failure(.unexpectedResponse),
            "update parsing should reject oversized numeric version parts"
        )
        try expect(
            parseSemver("999999999999999999999999.2.3"),
            equals: [Int.max, 2, 3],
            "tolerant version parsing should not overflow on oversized components"
        )
        try expect(
            normalizedSkippedUpdateVersions([
                "junk",
                "v1.2.3",
                "1.2.3",
                " V2.0.0\n",
                "01.2.3",
                "3.999999999999999999999999.0"
            ]),
            equals: ["1.2.3", "2.0.0"],
            "skipped update versions should normalize valid versions and discard malformed entries"
        )
        try expect(
            normalizedSkippedUpdateVersions((0..<(MAX_SKIPPED_UPDATE_VERSIONS + 3)).map { "1.0.\($0)" }),
            equals: (3..<(MAX_SKIPPED_UPDATE_VERSIONS + 3)).map { "1.0.\($0)" },
            "skipped update versions should keep only the most recent bounded entries"
        )
        try expect(
            UpdateCheck.parseLatest(
                data: Data(#"{"tag_name":"9.8.7","html_url":"https://example.test/v9.8.7"}"#.utf8),
                response: ok
            ),
            equals: .success(GitHubRelease(tagName: "9.8.7",
                                           version: "9.8.7",
                                           body: "",
                                           htmlURL: GITHUB_RELEASES_PAGE.absoluteString)),
            "update parsing should fall back from non-project release URLs"
        )
        try expect(
            UpdateCheck.parseLatest(
                data: Data(#"{"tag_name":"v9.8.7","html_url":"https://github.com/rcourtman/presspeech/releases/tag/v9.8.8"}"#.utf8),
                response: ok
            ),
            equals: .success(GitHubRelease(tagName: "v9.8.7",
                                           version: "9.8.7",
                                           body: "",
                                           htmlURL: GITHUB_RELEASES_PAGE.absoluteString)),
            "update parsing should fall back when release URL tag does not match the payload tag"
        )
        // Manual-check alert copy: each failure kind gets its own
        // explanation instead of blaming the network for everything.
        try expect(
            manualUpdateCheckFailureText(.network).contains("internet connection"),
            equals: true,
            "network failure text should point at connectivity"
        )
        try expect(
            manualUpdateCheckFailureText(.httpStatus(403)).contains("rate limiting"),
            equals: true,
            "HTTP 403 failure text should mention rate limiting"
        )
        try expect(
            manualUpdateCheckFailureText(.httpStatus(500)).contains("HTTP 500"),
            equals: true,
            "HTTP failure text should include the status code"
        )
        try expect(
            manualUpdateCheckFailureText(.unexpectedResponse).contains("couldn't read"),
            equals: true,
            "unexpected-response failure text should describe an unreadable response"
        )
        try expect(
            UpdateCheck.normalizedReleaseVersion(from: " V1.2.3\n"),
            equals: "1.2.3",
            "release version normalization should allow one leading v"
        )
        try expect(
            normalizedStoredAppVersion(" v2.3.4\n"),
            equals: "2.3.4",
            "stored app version normalization should canonicalize release-style versions"
        )
        try expect(
            normalizedStoredAppVersion("2.3"),
            equals: nil,
            "stored app version normalization should reject incomplete versions"
        )
        try expect(
            normalizedStoredAppVersion("v999999999999999999999999.2.3"),
            equals: nil,
            "stored app version normalization should reject oversized numeric components"
        )
        try expect(
            UpdateCheck.sanitizedReleaseURL("http://github.com/rcourtman/presspeech/releases/tag/v9.8.7",
                                            expectedTag: "v9.8.7"),
            equals: GITHUB_RELEASES_PAGE.absoluteString,
            "release URL sanitizing should require HTTPS"
        )
        try expect(
            UpdateCheck.sanitizedReleaseURL("https://user@github.com/rcourtman/presspeech/releases/tag/v9.8.7",
                                            expectedTag: "v9.8.7"),
            equals: GITHUB_RELEASES_PAGE.absoluteString,
            "release URL sanitizing should reject userinfo"
        )
        try expect(
            UpdateCheck.sanitizedReleaseURL("https://github.com/rcourtman/presspeech/releases/tag/v9.8.7?download=1",
                                            expectedTag: "v9.8.7"),
            equals: GITHUB_RELEASES_PAGE.absoluteString,
            "release URL sanitizing should reject query strings"
        )
    }

    private static func testUpdateCheckState() throws {
        let release = GitHubRelease(tagName: "v1.2.4",
                                    version: "1.2.4",
                                    body: "",
                                    htmlURL: GITHUB_RELEASES_PAGE.absoluteString)
        try expect(
            updateCheckResult(for: nil, currentVersion: "1.2.3", skippedVersions: []),
            equals: .failed,
            "nil update checks should be recorded as failed or unavailable"
        )
        try expect(
            updateCheckResult(for: release, currentVersion: "1.2.4", skippedVersions: []),
            equals: .upToDate,
            "equal release versions should be recorded as up to date"
        )
        try expect(
            updateCheckResult(for: release, currentVersion: "1.2.3", skippedVersions: []),
            equals: .available,
            "newer releases should be recorded as available"
        )
        try expect(
            updateCheckResult(for: release, currentVersion: "1.2.3", skippedVersions: ["1.2.4"]),
            equals: .skipped,
            "skipped newer releases should be recorded distinctly"
        )

        let now = Date(timeIntervalSince1970: 1_000)
        try expect(
            shouldSuppressUpdateForReminder(version: "1.2.4",
                                            reminderVersion: "1.2.4",
                                            reminderUntil: now.addingTimeInterval(60),
                                            now: now),
            equals: true,
            "active reminders should suppress the matching update version"
        )
        try expect(
            shouldSuppressUpdateForReminder(version: "1.2.5",
                                            reminderVersion: "1.2.4",
                                            reminderUntil: now.addingTimeInterval(60),
                                            now: now),
            equals: false,
            "reminders should not suppress newer versions"
        )
        try expect(
            shouldSuppressUpdateForReminder(version: "1.2.4",
                                            reminderVersion: "1.2.4",
                                            reminderUntil: now.addingTimeInterval(-1),
                                            now: now),
            equals: false,
            "expired reminders should not suppress updates"
        )
        try expect(
            updateCheckDiagnosticText(checkedAt: nil,
                                      source: nil,
                                      result: nil,
                                      releaseVersion: ""),
            equals: "never",
            "missing update-check metadata should render as never"
        )

        // Stale-pause clearing: equal version (expired pause about to
        // be re-shown) and a newer superseding release both clear; an
        // older fetched version or no pause leaves things alone.
        try expect(
            shouldClearUpdateReminderPause(fetchedVersion: "1.2.4", pausedVersion: "1.2.4"),
            equals: true,
            "a fetched release matching the paused version should clear the pause"
        )
        try expect(
            shouldClearUpdateReminderPause(fetchedVersion: "1.2.5", pausedVersion: "1.2.4"),
            equals: true,
            "a newer fetched release should clear a stale pause for the superseded version"
        )
        try expect(
            shouldClearUpdateReminderPause(fetchedVersion: "1.2.3", pausedVersion: "1.2.4"),
            equals: false,
            "an older fetched release should keep the existing pause"
        )
        try expect(
            shouldClearUpdateReminderPause(fetchedVersion: "1.2.4", pausedVersion: nil),
            equals: false,
            "no pause means nothing to clear"
        )

        // Persisted pause expiry validation, mirroring the
        // lastUpdateCheck* pattern: corrupt → nil, in-range round-trip,
        // cleared/missing → nil.
        let pauseNow = Date(timeIntervalSince1970: 2_000)
        let validPauseUntil = pauseNow.addingTimeInterval(UPDATE_REMIND_LATER_SECONDS)
        try expect(
            normalizedUpdateReminderPauseExpiry(storedValue: validPauseUntil, now: pauseNow),
            equals: validPauseUntil,
            "a stored pause expiry inside the pause window should round-trip"
        )
        try expect(
            normalizedUpdateReminderPauseExpiry(storedValue: pauseNow.addingTimeInterval(-60), now: pauseNow),
            equals: pauseNow.addingTimeInterval(-60),
            "an already-expired stored pause expiry is legitimate state and should round-trip"
        )
        try expect(
            normalizedUpdateReminderPauseExpiry(storedValue: "not a date", now: pauseNow),
            equals: nil,
            "a corrupt (non-Date) stored pause expiry should degrade to nil"
        )
        try expect(
            normalizedUpdateReminderPauseExpiry(storedValue: nil, now: pauseNow),
            equals: nil,
            "a cleared pause expiry should read back as nil"
        )
        try expect(
            normalizedUpdateReminderPauseExpiry(
                storedValue: pauseNow.addingTimeInterval(UPDATE_REMIND_LATER_SECONDS + 60),
                now: pauseNow
            ),
            equals: nil,
            "an out-of-range future pause expiry should degrade to nil instead of suppressing indefinitely"
        )
        // The paused-version half persists through the same validated
        // app-version normalization tested in testUpdateCheckParsing
        // (normalizedStoredAppVersion: corrupt → nil, round-trip).

        try expect(
            UpdateCheckSource(rawValue: "settings_toggle"),
            equals: .settingsToggle,
            "settings-toggle update checks should round-trip through their persisted raw value"
        )
        try expect(
            UpdateCheckSource.settingsToggle.diagnosticLabel,
            equals: "settings toggle",
            "settings-toggle update checks should label themselves distinctly in diagnostics"
        )
    }

    private static func testUpdateHelperScript() throws {
        try expect(
            shellSingleQuoted("a'b"),
            equals: "'a'\"'\"'b'",
            "shell quoting should preserve embedded single quotes"
        )
        try expect(
            (UPDATE_HELPER_LOG_PATH as NSString).deletingLastPathComponent,
            equals: (NSHomeDirectory() as NSString).appendingPathComponent("Library/Logs"),
            "update helper log should live in the user's log directory"
        )
        let updateEnv = updateProcessEnvironment(current: [
            "LANG": "C\nbad",
            "USER": "presspeech-user",
            "LOGNAME": "presspeech-logname",
            "__CF_USER_TEXT_ENCODING": "0x1F5:0x0:0x0",
            "BASH_ENV": "/tmp/pwn.sh",
            "ENV": "/tmp/pwn.sh",
            "SHELLOPTS": "xtrace",
            "RUBYOPT": "-r/tmp/pwn.rb",
            "HOMEBREW_BOTTLE_DOMAIN": "https://example.test",
        ])
        try expect(updateEnv["HOME"], equals: Optional(NSHomeDirectory()),
                   "update environment should set HOME explicitly")
        try expect(updateEnv["PATH"],
                   equals: Optional("/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"),
                   "update environment should use a deterministic PATH")
        try expect(updateEnv["LANG"], equals: Optional("en_US.UTF-8"),
                   "update environment should reject unsafe locale values")
        try expect(updateEnv["USER"], equals: Optional("presspeech-user"),
                   "update environment should preserve a safe USER value")
        try expect(updateEnv["LOGNAME"], equals: Optional("presspeech-logname"),
                   "update environment should preserve a safe LOGNAME value")
        for key in ["BASH_ENV", "ENV", "SHELLOPTS", "RUBYOPT", "HOMEBREW_BOTTLE_DOMAIN"] {
            try expect(updateEnv[key], equals: String?.none,
                       "update environment should not inherit \(key)")
        }
        let systemEnv = systemToolProcessEnvironment(current: [
            "LANG": "en_GB.UTF-8",
            "USER": "presspeech-user",
            "BASH_ENV": "/tmp/pwn.sh",
            "DYLD_INSERT_LIBRARIES": "/tmp/pwn.dylib",
            "PATH": "/tmp/bin",
        ])
        try expect(systemEnv["PATH"], equals: Optional("/usr/bin:/bin:/usr/sbin:/sbin"),
                   "system tool environment should not include Homebrew or inherited PATH entries")
        try expect(systemEnv["LANG"], equals: Optional("en_GB.UTF-8"),
                   "system tool environment should preserve a safe locale")
        try expect(systemEnv["USER"], equals: Optional("presspeech-user"),
                   "system tool environment should preserve a safe USER value")
        for key in ["BASH_ENV", "DYLD_INSERT_LIBRARIES"] {
            try expect(systemEnv[key], equals: String?.none,
                       "system tool environment should not inherit \(key)")
        }

        let script = updateHelperScript(pid: 123,
                                        brewPath: "/opt/homebrew/bin/brew",
                                        targetVersion: "9.8.7",
                                        statePath: "/tmp/presspeech-update.state",
                                        appPath: "/Applications/Presspeech.app",
                                        releasesPageURL: "https://example.test/releases")
        for fragment in [
            "umask 077",
            "TARGET_VERSION='9.8.7'",
            "STATE_PATH='/tmp/presspeech-update.state'",
            "PRESSPEECH_PID=123",
            "SCRIPT_PATH=\"$0\"",
            "trap cleanup EXIT",
            "/bin/rm -f \"$SCRIPT_PATH\"",
            "printf '[%s] %s\\n' \"$(timestamp)\" \"$*\"",
            "printf '%s\\t%s\\n' \"$phase\" \"$message\" >\"$tmp\"",
            "CASK_TAP='rcourtman/presspeech'",
            "CASK_TOKEN='rcourtman/presspeech/presspeech'",
            "CASK_INSTALLED_TOKEN='presspeech'",
            "PlistBuddy -c \"Print :CFBundleShortVersionString\"",
            "version_at_least \"$installed\" \"$TARGET_VERSION\"",
            "state \"preparing\" \"Preparing Homebrew for Presspeech v$TARGET_VERSION...\"",
            "state \"downloading\" \"Downloading Presspeech v$TARGET_VERSION...\"",
            "state \"installing\" \"Installing Presspeech v$TARGET_VERSION...\"",
            "run_brew tap \"$CASK_TAP\"",
            "run_brew update --force",
            "run_brew fetch --cask --force \"$CASK_TOKEN\"",
            "run_brew upgrade --cask --force --appdir=\"$APP_DIR\" \"$CASK_TOKEN\"",
            "run_brew reinstall --cask --force --appdir=\"$APP_DIR\" \"$CASK_TOKEN\"",
            "installed_target_version",
            "sleep 2",
            "state \"complete\" \"Presspeech v$TARGET_VERSION is installed.\"",
            "/usr/bin/open \"$APP_PATH\""
        ] {
            guard script.contains(fragment) else {
                throw SelfTestFailure.failed("update helper script missing fragment: \(fragment)")
            }
        }
        for fragment in ["LOG=", ">>\"$LOG\"", ">\"$LOG\"", "prepare_log"] {
            guard !script.contains(fragment) else {
                throw SelfTestFailure.failed("update helper script should not reopen a log path: \(fragment)")
            }
        }

        let tmp = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("presspeech-update-self-test-\(UUID().uuidString).sh")
        try script.write(to: tmp, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: tmp) }

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/bin/bash")
        proc.arguments = ["-n", tmp.path]
        proc.standardOutput = Pipe()
        proc.standardError = Pipe()
        try proc.run()
        proc.waitUntilExit()
        guard proc.terminationStatus == 0 else {
            throw SelfTestFailure.failed("update helper script should pass bash -n")
        }

        let fm = FileManager.default
        let helperRoot = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("presspeech-update-helper-test-\(UUID().uuidString)", isDirectory: true)
        try fm.createDirectory(at: helperRoot, withIntermediateDirectories: false)
        defer { try? fm.removeItem(at: helperRoot) }

        let helperPath = try writePrivateUpdateHelperScript(script,
                                                            directory: helperRoot.path,
                                                            fileName: "helper.sh")
        var createdStat = stat()
        guard lstat(helperPath, &createdStat) == 0 else {
            throw SelfTestFailure.failed("update helper script file should exist")
        }
        try expect((createdStat.st_mode & S_IFMT) == S_IFREG,
                   equals: true,
                   "update helper script should be a regular file")
        try expect(Int(createdStat.st_mode & mode_t(0o777)),
                   equals: 0o600,
                   "update helper script should be private to the current user")
        try expect(Int(createdStat.st_nlink),
                   equals: 1,
                   "update helper script should not be hard-linked")
        try expect(
            String(data: try Data(contentsOf: URL(fileURLWithPath: helperPath)), encoding: .utf8),
            equals: script,
            "update helper script file should contain the generated script"
        )

        let existing = helperRoot.appendingPathComponent("existing.sh")
        try Data("existing\n".utf8).write(to: existing)
        var existingRejected = false
        do {
            _ = try writePrivateUpdateHelperScript("bad",
                                                   directory: helperRoot.path,
                                                   fileName: "existing.sh")
        } catch {
            existingRejected = true
        }
        try expect(existingRejected, equals: true,
                   "update helper script writer should reject existing files")
        try expect(
            String(data: try Data(contentsOf: existing), encoding: .utf8),
            equals: "existing\n",
            "update helper script writer should leave existing files untouched"
        )

        let target = helperRoot.appendingPathComponent("target.sh")
        try Data("target\n".utf8).write(to: target)
        let link = helperRoot.appendingPathComponent("linked.sh")
        try fm.createSymbolicLink(at: link, withDestinationURL: target)
        var symlinkRejected = false
        do {
            _ = try writePrivateUpdateHelperScript("bad",
                                                   directory: helperRoot.path,
                                                   fileName: "linked.sh")
        } catch {
            symlinkRejected = true
        }
        try expect(symlinkRejected, equals: true,
                   "update helper script writer should reject leaf symlinks")
        try expect(
            String(data: try Data(contentsOf: target), encoding: .utf8),
            equals: "target\n",
            "update helper script writer should leave symlink targets untouched"
        )

        let preferredLog = helperRoot.appendingPathComponent("Presspeech-update.log")
        let helperLog = try openPrivateUpdateHelperLog(preferredPath: preferredLog.path,
                                                       fallbackDirectory: helperRoot.path)
        helperLog.handle.write(Data("log\n".utf8))
        helperLog.handle.closeFile()
        try expect(helperLog.path, equals: preferredLog.path,
                   "update helper log should use the preferred path when safe")
        var logStat = stat()
        guard lstat(preferredLog.path, &logStat) == 0 else {
            throw SelfTestFailure.failed("update helper log file should exist")
        }
        try expect((logStat.st_mode & S_IFMT) == S_IFREG,
                   equals: true,
                   "update helper log should be a regular file")
        try expect(Int(logStat.st_mode & mode_t(0o777)),
                   equals: 0o600,
                   "update helper log should be private to the current user")
        try expect(Int(logStat.st_nlink),
                   equals: 1,
                   "update helper log should not be hard-linked")
        try expect(
            String(data: try Data(contentsOf: preferredLog), encoding: .utf8),
            equals: "log\n",
            "update helper log should receive helper output"
        )

        let linkedLogTarget = helperRoot.appendingPathComponent("linked-log-target.log")
        try Data("target log\n".utf8).write(to: linkedLogTarget)
        let linkedLog = helperRoot.appendingPathComponent("linked-log.log")
        try fm.createSymbolicLink(at: linkedLog, withDestinationURL: linkedLogTarget)
        let fallbackForSymlink = try openPrivateUpdateHelperLog(preferredPath: linkedLog.path,
                                                                fallbackDirectory: helperRoot.path)
        fallbackForSymlink.handle.write(Data("fallback\n".utf8))
        fallbackForSymlink.handle.closeFile()
        try expect(fallbackForSymlink.path == linkedLog.path,
                   equals: false,
                   "update helper log should fall back when preferred path is a symlink")
        try expect(
            String(data: try Data(contentsOf: linkedLogTarget), encoding: .utf8),
            equals: "target log\n",
            "update helper log fallback should leave symlink targets untouched"
        )

        let hardLogTarget = helperRoot.appendingPathComponent("hard-log-target.log")
        try Data("hard target\n".utf8).write(to: hardLogTarget)
        let hardLog = helperRoot.appendingPathComponent("hard-log.log")
        try fm.linkItem(at: hardLogTarget, to: hardLog)
        let fallbackForHardLink = try openPrivateUpdateHelperLog(preferredPath: hardLog.path,
                                                                 fallbackDirectory: helperRoot.path)
        fallbackForHardLink.handle.write(Data("hard fallback\n".utf8))
        fallbackForHardLink.handle.closeFile()
        try expect(fallbackForHardLink.path == hardLog.path,
                   equals: false,
                   "update helper log should fall back when preferred path is hard-linked")
        try expect(
            String(data: try Data(contentsOf: hardLogTarget), encoding: .utf8),
            equals: "hard target\n",
            "update helper log fallback should leave hard-linked targets untouched"
        )
    }

    private static func testUpdateProgressState() throws {
        let launch = UpdateProgressLaunch(arguments: [
            UPDATE_PROGRESS_ARGUMENT,
            "/tmp/presspeech.state",
            "/tmp/presspeech.log",
            "9.8.7",
            "/tmp/\(UPDATE_PROGRESS_APP_PREFIX)test.app",
        ])
        try expect(launch != nil, equals: true,
                   "update progress launch arguments should parse")
        try expect(launch?.targetVersion, equals: Optional("9.8.7"),
                   "update progress launch should retain target version")
        try expect(
            UpdateProgressLaunch(arguments: [UPDATE_PROGRESS_ARGUMENT, "", "/tmp/presspeech.log", "9.8.7", "/tmp/app"]) != nil,
            equals: false,
            "update progress launch should reject empty paths"
        )

        let statePath = try createPrivateUpdateProgressStateFile()
        defer { try? FileManager.default.removeItem(atPath: statePath) }

        var st = stat()
        guard lstat(statePath, &st) == 0 else {
            throw SelfTestFailure.failed("update progress state file should exist")
        }
        try expect((st.st_mode & S_IFMT) == S_IFREG, equals: true,
                   "update progress state file should be regular")
        try expect(Int(st.st_nlink), equals: 1,
                   "update progress state file should not be hard-linked")
        try expect(Int(st.st_mode & mode_t(0o777)), equals: 0o600,
                   "update progress state file should be private to the current user")

        let initial = UpdateProgressState.read(from: statePath)
        try expect(initial?.phase, equals: Optional("starting"),
                   "update progress state should default to starting")
        try expect(initial?.message, equals: Optional("Starting update..."),
                   "update progress state should default to the startup message")

        try writePrivateUpdateProgressState(phase: "failed\tbad",
                                            message: "Line 1\nLine 2",
                                            to: statePath)
        let failed = UpdateProgressState.read(from: statePath)
        try expect(failed?.phase, equals: Optional("failed bad"),
                   "update progress state should sanitize tab characters in phases")
        try expect(failed?.message, equals: Optional("Line 1 Line 2"),
                   "update progress state should sanitize newlines in messages")

        let safeCleanupPath = (NSTemporaryDirectory() as NSString)
            .appendingPathComponent("\(UPDATE_PROGRESS_APP_PREFIX)test.app")
        try expect(isSafeUpdateProgressCleanupPath(safeCleanupPath), equals: true,
                   "update progress cleanup should allow copied temp app bundles")
        try expect(isSafeUpdateProgressCleanupPath("/Applications/Presspeech.app"), equals: false,
                   "update progress cleanup should reject non-temp app bundles")
        let unsafeTempPath = (NSTemporaryDirectory() as NSString)
            .appendingPathComponent("Presspeech.app")
        try expect(isSafeUpdateProgressCleanupPath(unsafeTempPath), equals: false,
                   "update progress cleanup should reject temp app bundles without the copied-helper prefix")
    }

    private static func testHostileRegistryEnvDetection() throws {
        try expect(
            detectedHostileRegistryEnvVars(in: [:]),
            equals: [],
            "empty environment should not flag any registry override"
        )
        try expect(
            detectedHostileRegistryEnvVars(in: ["HF_TOKEN": "redacted",
                                                "PATH": "/usr/bin"]),
            equals: [],
            "unrelated env vars (incl. HF_TOKEN) must not flag as hostile"
        )
        try expect(
            detectedHostileRegistryEnvVars(in: ["REGISTRY_URL": "https://evil.example/"]),
            equals: ["REGISTRY_URL"],
            "REGISTRY_URL must be flagged"
        )
        try expect(
            detectedHostileRegistryEnvVars(in: ["MODEL_REGISTRY_URL": "https://evil.example/"]),
            equals: ["MODEL_REGISTRY_URL"],
            "MODEL_REGISTRY_URL must be flagged"
        )
        try expect(
            detectedHostileRegistryEnvVars(in: ["REGISTRY_URL": "",
                                                "MODEL_REGISTRY_URL": ""]),
            equals: ["MODEL_REGISTRY_URL", "REGISTRY_URL"],
            "an empty-string value still represents a tampered launch env"
        )
    }

    private static func testAudioRouteChangeDecision() throws {
        try expect(
            audioStartupRetryDelaySeconds(afterFailedAttempt: 1),
            equals: Optional(1 as UInt64),
            "first audio startup failure should retry after one second"
        )
        try expect(
            audioStartupRetryDelaySeconds(afterFailedAttempt: 2),
            equals: Optional(3 as UInt64),
            "second audio startup failure should retry after three seconds"
        )
        try expect(
            audioStartupRetryDelaySeconds(afterFailedAttempt: 3),
            equals: Optional(8 as UInt64),
            "third audio startup failure should retry after eight seconds"
        )
        try expect(
            audioStartupRetryDelaySeconds(afterFailedAttempt: 4),
            equals: UInt64?.none,
            "audio startup should stop retrying after the configured backoff schedule"
        )
        try expect(
            audioRouteChangeAction(isTerminating: true,
                                   isRestartingAudioInput: false,
                                   isCoreRuntimeReady: true,
                                   isRecording: false,
                                   isBusy: false,
                                   hasStartupTask: false),
            equals: .ignore,
            "route changes during termination should be ignored"
        )
        try expect(
            audioRouteChangeAction(isTerminating: false,
                                   isRestartingAudioInput: false,
                                   isCoreRuntimeReady: false,
                                   isRecording: false,
                                   isBusy: false,
                                   hasStartupTask: false),
            equals: .rebuildMenuOnly,
            "route changes before runtime readiness should only refresh the menu"
        )
        try expect(
            audioRouteChangeAction(isTerminating: false,
                                   isRestartingAudioInput: false,
                                   isCoreRuntimeReady: true,
                                   isRecording: true,
                                   isBusy: false,
                                   hasStartupTask: false),
            equals: .deferRefresh,
            "route changes during recording should defer the restart"
        )
        try expect(
            audioRouteChangeAction(isTerminating: false,
                                   isRestartingAudioInput: false,
                                   isCoreRuntimeReady: true,
                                   isRecording: false,
                                   isBusy: false,
                                   hasStartupTask: false),
            equals: .restartNow,
            "idle ready route changes should restart audio immediately"
        )
        try expect(
            audioConfigurationChangeIsSuppressed(now: 10, suppressedUntil: nil),
            equals: false,
            "configuration changes should not be suppressed without a suppression deadline"
        )
        try expect(
            audioConfigurationChangeIsSuppressed(now: 10, suppressedUntil: 11),
            equals: true,
            "configuration changes before the app-owned deadline should be ignored"
        )
        try expect(
            audioConfigurationChangeIsSuppressed(now: 11, suppressedUntil: 11),
            equals: false,
            "configuration changes at the suppression deadline should be handled normally"
        )
    }

    private static func testRecordingLifecycle() throws {
        try expect(
            dictationMenuControlState(isReady: true,
                                      isRecording: false,
                                      isBusy: false,
                                      isTerminating: false,
                                      hasMissingPermissions: false),
            equals: DictationMenuControlState(
                action: .start,
                title: "Start Dictation",
                isEnabled: true,
                help: "Start recording for the currently focused window; choose Stop and Transcribe when finished."
            ),
            "ready menu should expose an enabled start action"
        )
        try expect(
            dictationMenuControlState(isReady: true,
                                      isRecording: true,
                                      isBusy: false,
                                      isTerminating: false,
                                      hasMissingPermissions: false).action,
            equals: .stop,
            "recording menu should expose a stop-and-transcribe action"
        )
        try expect(
            dictationMenuControlState(isReady: true,
                                      isRecording: false,
                                      isBusy: true,
                                      isTerminating: false,
                                      hasMissingPermissions: false).isEnabled,
            equals: false,
            "menu start should stay disabled during transcription"
        )
        try expect(
            dictationMenuControlState(isReady: true,
                                      isRecording: false,
                                      isBusy: false,
                                      isTerminating: false,
                                      hasMissingPermissions: true).isEnabled,
            equals: false,
            "menu start should stay disabled while a permission is missing"
        )
        try expect(menuBarAccessibilityValue(for: .recording),
                   equals: "Recording",
                   "menu-bar accessibility value should announce recording state")
        try expect(DictationNotice.copiedToClipboard.statusTitle,
                   equals: "Transcript copied — press ⌘V to paste",
                   "focus-safe delivery should explain immediate clipboard recovery")
        try expect(DictationNotice.insertionFailed.statusTitle,
                   equals: "Couldn't paste — use Copy Last Transcript",
                   "failed insertion should direct the user to in-memory recovery")
        try expect(DictationNotice.noSpeechDetected.hudTitle,
                   equals: "No speech detected — try again",
                   "empty recognition should not look like a successful dictation")
        try expect(DictationNotice.copiedToClipboard.accessibilityValue,
                   equals: "Transcript copied. Press Command V to paste.",
                   "clipboard recovery should be explicit without relying on the Command glyph")
        try expect(dictationCompletionNotice(processedText: "hello",
                                              insertionOutcome: .copiedWithoutPasting,
                                              keepsRecentTranscripts: true),
                   equals: .copiedToClipboard,
                   "focus-safe clipboard delivery should produce the recovery notice")
        try expect(dictationCompletionNotice(processedText: "hello",
                                              insertionOutcome: .inserted,
                                              keepsRecentTranscripts: true),
                   equals: DictationNotice?.none,
                   "successful insertion should not leave a stale notice")
        try expect(dictationCompletionNotice(processedText: "",
                                              insertionOutcome: nil,
                                              keepsRecentTranscripts: false),
                   equals: .noSpeechDetected,
                   "an empty processed transcript should produce the no-speech notice")
        try expect(dictationCompletionNotice(processedText: "hello",
                                              insertionOutcome: .failed,
                                              keepsRecentTranscripts: false),
                   equals: .insertionFailedWithoutHistory,
                   "failed insertion should not offer unavailable history recovery")

        var externallyStartedToggle = HotkeyTransitionState()
        let f5 = hotkeyChoice(forKeycode: 96)
        externallyStartedToggle.recordingDidStartOutsideHotkey(triggerMode: .toggle)
        try expect(
            externallyStartedToggle.transition(for: event(.keyDown, keycode: f5.keycode),
                                                hotkey: f5,
                                                triggerMode: .toggle,
                                                isRecording: true,
                                                canStartRecording: false),
            equals: HotkeyTransitionResult(suppress: true, actions: [.release]),
            "toggle hotkey should stop a recording started from the menu"
        )

        try expect(
            recordingHUDFrame(size: NSSize(width: 232, height: 54),
                              visibleFrame: NSRect(x: 100, y: 40, width: 1_000, height: 700)),
            equals: NSRect(x: 484, y: 124, width: 232, height: 54),
            "recording HUD should stay centered above the active screen's lower edge"
        )
        try expect(
            recordingHUDFrame(size: RECORDING_HUD_EXPANDED_SIZE, visibleFrame: nil),
            equals: NSRect?.none,
            "recording HUD should stay hidden while macOS reports no screens"
        )
        try expect(
            setupChecklistWindowContentSize(
                visibleFrame: NSRect(x: 0, y: 0, width: 1_440, height: 900)
            ),
            equals: NSSize(width: 560, height: 620),
            "setup checklist should use its comfortable size when the display has room"
        )
        try expect(
            setupChecklistWindowContentSize(
                visibleFrame: NSRect(x: 0, y: 0, width: 1_024, height: 600)
            ),
            equals: NSSize(width: 560, height: 520),
            "setup checklist should leave screen-edge room on a short display"
        )
        try expect(
            setupChecklistWindowContentSize(visibleFrame: nil),
            equals: NSSize(width: 560, height: 620),
            "setup checklist should retain a usable fallback size when no screen is reported"
        )
        try expect(
            restoredSetupChecklistScrollOriginY(previousOriginY: 180,
                                                documentHeight: 900,
                                                viewportHeight: 500),
            equals: 180,
            "live setup refreshes should preserve a valid scroll position"
        )
        try expect(
            restoredSetupChecklistScrollOriginY(previousOriginY: 450,
                                                documentHeight: 760,
                                                viewportHeight: 500),
            equals: 260,
            "setup refreshes should clamp the old position when content becomes shorter"
        )
        try expect(
            restoredSetupChecklistScrollOriginY(previousOriginY: -20,
                                                documentHeight: 900,
                                                viewportHeight: 500),
            equals: 0,
            "setup refreshes should not restore an invalid negative position"
        )

        try expect(
            MAXIMUM_RECORDING_LENGTH_CHOICES.map(\.seconds),
            equals: [60, 120, 300, 600],
            "recording limit menu should offer one, two, five, and ten minutes"
        )
        try expect(
            DEFAULT_MAX_RECORDING_SECONDS,
            equals: 120,
            "recording limit should preserve the existing two-minute default"
        )
        try expect(
            normalizedMaximumRecordingSeconds(storedValue: NSNumber(value: 300)),
            equals: 300,
            "recording limit should accept numeric defaults writes"
        )
        try expect(
            normalizedMaximumRecordingSeconds(storedValue: " 75 "),
            equals: 75,
            "recording limit should accept trimmed string defaults writes"
        )
        try expect(
            normalizedMaximumRecordingSeconds(storedValue: 1),
            equals: MIN_MAX_RECORDING_SECONDS,
            "recording limit should preserve the minimum safety bound"
        )
        try expect(
            normalizedMaximumRecordingSeconds(storedValue: 3_600),
            equals: MAX_MAX_RECORDING_SECONDS,
            "recording limit should cap excessive in-memory capture"
        )
        try expect(
            normalizedMaximumRecordingSeconds(storedValue: Double.nan),
            equals: TimeInterval?.none,
            "recording limit should reject non-finite defaults values"
        )
        try expect(
            maximumRecordingLengthTitle(seconds: 60),
            equals: "1 minute",
            "recording limit should use a singular one-minute label"
        )

        try expect(
            recordingReleaseAction(capturedSampleCount: 3_999,
                                   sampleRate: 16_000,
                                   minimumClipSeconds: 0.25),
            equals: .discardTooShort(duration: 0.2499375),
            "release decision should discard clips under the minimum duration"
        )
        try expect(
            recordingReleaseAction(capturedSampleCount: 4_000,
                                   sampleRate: 16_000,
                                   minimumClipSeconds: 0.25),
            equals: .transcribe(duration: 0.25),
            "release decision should transcribe clips at the minimum duration"
        )
        try expect(
            recordingReleaseAction(capturedSampleCount: 4_000,
                                   sampleRate: 0,
                                   minimumClipSeconds: 0.25),
            equals: .discardTooShort(duration: 0),
            "release decision should handle invalid sample rates defensively"
        )

        let processed = processedDictationText(
            rawTranscript: "  Um, parakeet is fast.  ",
            corrections: [TranscriptCorrection(source: "parakeet", replacement: "Presspeech")],
            spokenFormattingCommands: false,
            dictationLanguage: .auto,
            removeFillerWords: true
        )
        try expect(
            processed,
            equals: DictationTextProcessingResult(text: "Presspeech is fast.",
                                                  appliedCorrectionCount: 1,
                                                  appliedFormattingCommandCount: 0,
                                                  removedFillerWordCount: 1),
            "dictation text processing should trim, apply corrections, then remove fillers"
        )

        let preservedFillers = processedDictationText(
            rawTranscript: "  Um, parakeet is fast.  ",
            corrections: [TranscriptCorrection(source: "parakeet", replacement: "Presspeech")],
            spokenFormattingCommands: false,
            dictationLanguage: .auto,
            removeFillerWords: false
        )
        try expect(
            preservedFillers,
            equals: DictationTextProcessingResult(text: "Um, Presspeech is fast.",
                                                  appliedCorrectionCount: 1,
                                                  appliedFormattingCommandCount: 0,
                                                  removedFillerWordCount: 0),
            "dictation text processing should preserve fillers when the setting is off"
        )

        let formatted = processedDictationText(
            rawTranscript: "Shopping list. New paragraph. Bullet point apples bullet point pears new line done question mark",
            corrections: [],
            spokenFormattingCommands: true,
            dictationLanguage: .english,
            removeFillerWords: false
        )
        try expect(
            formatted,
            equals: DictationTextProcessingResult(
                text: "Shopping list.\n\n• apples\n• pears\ndone?",
                appliedCorrectionCount: 0,
                appliedFormattingCommandCount: 5,
                removedFillerWordCount: 0
            ),
            "spoken formatting should deterministically turn documented phrases into structure and punctuation"
        )

        let frenchFormatted = processedDictationText(
            rawTranscript: "Liste deux-points nouveau paragraphe Bonjour virgule monde point d’interrogation nouvelle ligne terminé point",
            corrections: [],
            spokenFormattingCommands: true,
            dictationLanguage: .french,
            removeFillerWords: false
        )
        try expect(
            frenchFormatted,
            equals: DictationTextProcessingResult(
                text: "Liste:\n\nBonjour, monde?\nterminé.",
                appliedCorrectionCount: 0,
                appliedFormattingCommandCount: 6,
                removedFillerWordCount: 0
            ),
            "French language hint should select deterministic French formatting commands"
        )

        let frenchDelimiters = SpokenFormattingCommandProcessor.apply(
            to: "guillemet ouvrant bonjour guillemet fermant parenthèse ouvrante test parenthèse fermante point-virgule suite point d'exclamation",
            language: .french
        )
        try expect(
            frenchDelimiters.text,
            equals: "« bonjour » (test); suite!",
            "French formatting should preserve French quote spacing and clean parentheses"
        )
        try expect(
            frenchDelimiters.appliedCount,
            equals: 6,
            "French delimiter and punctuation commands should all be counted"
        )

        let frenchDoesNotRewriteEnglish = SpokenFormattingCommandProcessor.apply(
            to: "A practical point remains.",
            language: .english
        )
        try expect(
            frenchDoesNotRewriteEnglish.text,
            equals: "A practical point remains.",
            "French punctuation words should not run outside the French language hint"
        )
        try expect(
            frenchDoesNotRewriteEnglish.appliedCount,
            equals: 0,
            "non-selected language commands should not count as applied"
        )

        let formattingDisabled = processedDictationText(
            rawTranscript: "Start a new line here.",
            corrections: [],
            spokenFormattingCommands: false,
            dictationLanguage: .auto,
            removeFillerWords: false
        )
        try expect(
            formattingDisabled.text,
            equals: "Start a new line here.",
            "spoken formatting phrases should remain verbatim while the opt-in setting is off"
        )

        let markerText = systemAudioMuteMarkerText(pid: 12345,
                                                   date: Date(timeIntervalSince1970: 0))
        try expect(
            systemAudioMuteMarkerProcessID(from: markerText),
            equals: Optional(pid_t(12345)),
            "system audio mute marker should preserve the owning pid"
        )
        try expect(
            systemAudioMuteMarkerProcessID(from: "created=bad\n"),
            equals: pid_t?.none,
            "system audio mute marker parsing should ignore missing pids"
        )

        let script = systemAudioMuteWatchdogScript()
        for fragment in [
            #"PID="$1""#,
            #"MARKER="$2""#,
            #"/bin/kill -0 "$PID""#,
            "/usr/bin/osascript -e 'set volume without output muted'",
            #"/bin/rm -f "$MARKER""#,
        ] {
            guard script.contains(fragment) else {
                throw SelfTestFailure.failed("system audio mute watchdog script missing fragment: \(fragment)")
            }
        }

        // Mute command outcome: command failure and verified-unmuted
        // are definitive "not muted"; an ambiguous verification after
        // a successful command must be assumed muted so the recovery
        // marker + watchdog stay armed.
        try expect(
            systemAudioMuteCommandOutcome(commandSucceeded: true, verifiedMuted: true),
            equals: .muted,
            "verified mute should report muted"
        )
        try expect(
            systemAudioMuteCommandOutcome(commandSucceeded: true, verifiedMuted: nil),
            equals: .assumedMuted,
            "successful command with failed verification must assume muted"
        )
        try expect(
            systemAudioMuteCommandOutcome(commandSucceeded: true, verifiedMuted: false),
            equals: .failed,
            "verified-unmuted after the command is a definitive failure"
        )
        try expect(
            systemAudioMuteCommandOutcome(commandSucceeded: false, verifiedMuted: true),
            equals: .failed,
            "a failed command is not muted regardless of verification"
        )

        // Probe decision: only a definitive "output is live" while the
        // recording still wants the mute arms recovery and mutes.
        try expect(
            systemAudioMuteProbeDecision(mutedState: false, unmuteAlreadyRequested: false),
            equals: .armRecoveryAndMute,
            "live output during an active recording should mute"
        )
        try expect(
            systemAudioMuteProbeDecision(mutedState: true, unmuteAlreadyRequested: false),
            equals: .standDown,
            "a user-set mute must not be stomped"
        )
        try expect(
            systemAudioMuteProbeDecision(mutedState: nil, unmuteAlreadyRequested: false),
            equals: .standDown,
            "a failed probe must not risk stomping an unseen user mute"
        )
        try expect(
            systemAudioMuteProbeDecision(mutedState: false, unmuteAlreadyRequested: true),
            equals: .standDown,
            "a recording that already ended should not mute"
        )

        // Mute completion decision: assumed mutes behave exactly like
        // verified mutes (recovery stays armed); a definitive failure
        // disarms; a release that raced the command unmutes at once.
        try expect(
            systemAudioMuteCommandDecision(outcome: .muted, unmuteAlreadyRequested: false),
            equals: .stayMuted,
            "verified mute during recording should hold"
        )
        try expect(
            systemAudioMuteCommandDecision(outcome: .assumedMuted, unmuteAlreadyRequested: false),
            equals: .stayMuted,
            "assumed mute must keep recovery armed, not disarm it"
        )
        try expect(
            systemAudioMuteCommandDecision(outcome: .failed, unmuteAlreadyRequested: false),
            equals: .disarmRecovery,
            "definitive mute failure should disarm marker and watchdog"
        )
        try expect(
            systemAudioMuteCommandDecision(outcome: .muted, unmuteAlreadyRequested: true),
            equals: .beginUnmute,
            "release during the mute command should unmute immediately"
        )
        try expect(
            systemAudioMuteCommandDecision(outcome: .assumedMuted, unmuteAlreadyRequested: true),
            equals: .beginUnmute,
            "release during an assumed mute should also unmute immediately"
        )

        // Unmute request routing per lifecycle phase.
        try expect(
            systemAudioUnmuteRequestDecision(phase: .idle),
            equals: .nothingToDo,
            "no lifecycle → nothing to unmute"
        )
        try expect(
            systemAudioUnmuteRequestDecision(phase: .probing),
            equals: .deferUntilCommandSettles,
            "release during the probe defers to the probe completion"
        )
        try expect(
            systemAudioUnmuteRequestDecision(phase: .muting),
            equals: .deferUntilCommandSettles,
            "release during the mute command defers to its completion"
        )
        try expect(
            systemAudioUnmuteRequestDecision(phase: .muted),
            equals: .beginUnmute,
            "release while muted unmutes immediately"
        )
        try expect(
            systemAudioUnmuteRequestDecision(phase: .unmuting),
            equals: .nothingToDo,
            "release while an unmute is in flight should not double-issue"
        )

        let fm = FileManager.default
        let root = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("presspeech-mute-marker-\(UUID().uuidString)", isDirectory: true)
        try fm.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? fm.removeItem(at: root) }

        let marker = root.appendingPathComponent("system-audio-muted")
        try writeSystemAudioMuteMarker(to: marker, text: markerText)
        var markerStat = stat()
        guard lstat(marker.path, &markerStat) == 0 else {
            throw SelfTestFailure.failed("system audio mute marker should exist")
        }
        try expect((markerStat.st_mode & S_IFMT) == S_IFREG,
                   equals: true,
                   "system audio mute marker should be a regular file")
        try expect(Int(markerStat.st_mode & mode_t(0o777)),
                   equals: 0o600,
                   "system audio mute marker should be private")
        try expect(
            String(data: try Data(contentsOf: marker), encoding: .utf8),
            equals: markerText,
            "system audio mute marker should contain the expected pid"
        )

        let target = root.appendingPathComponent("target-marker")
        try Data("target\n".utf8).write(to: target)
        let symlink = root.appendingPathComponent("linked-marker")
        try fm.createSymbolicLink(at: symlink, withDestinationURL: target)
        var symlinkRejected = false
        do {
            try writeSystemAudioMuteMarker(to: symlink, text: "bad\n")
        } catch {
            symlinkRejected = true
        }
        try expect(symlinkRejected,
                   equals: true,
                   "system audio mute marker should reject leaf symlinks")
        try expect(
            String(data: try Data(contentsOf: target), encoding: .utf8),
            equals: "target\n",
            "system audio mute marker should leave symlink targets untouched"
        )
    }

    private static func testPowerStateRecoveryDecision() throws {
        try expect(
            shouldResumeRuntimeAfterSystemSleep(isTerminating: true,
                                                isCoreRuntimeReady: true,
                                                isReady: true,
                                                isRecording: true,
                                                audioIsRunning: true),
            equals: false,
            "sleep during termination should not schedule wake recovery"
        )
        try expect(
            shouldResumeRuntimeAfterSystemSleep(isTerminating: false,
                                                isCoreRuntimeReady: false,
                                                isReady: false,
                                                isRecording: false,
                                                audioIsRunning: false),
            equals: false,
            "sleep before runtime startup should not schedule wake recovery"
        )
        try expect(
            shouldResumeRuntimeAfterSystemSleep(isTerminating: false,
                                                isCoreRuntimeReady: false,
                                                isReady: false,
                                                isRecording: true,
                                                audioIsRunning: true),
            equals: true,
            "active recording should schedule wake recovery even if readiness is already down"
        )
        try expect(
            wakeRuntimeRecoveryAction(shouldResumeAfterWake: false,
                                      isTerminating: false,
                                      hasStartupTask: false,
                                      isBusy: false,
                                      isSpeechModelReady: true),
            equals: .ignore,
            "wake without a sleep-paused runtime should do nothing"
        )
        try expect(
            wakeRuntimeRecoveryAction(shouldResumeAfterWake: true,
                                      isTerminating: false,
                                      hasStartupTask: false,
                                      isBusy: true,
                                      isSpeechModelReady: true),
            equals: .deferUntilIdle,
            "wake during transcription should defer runtime recovery"
        )
        try expect(
            wakeRuntimeRecoveryAction(shouldResumeAfterWake: true,
                                      isTerminating: false,
                                      hasStartupTask: true,
                                      isBusy: false,
                                      isSpeechModelReady: true),
            equals: .deferUntilIdle,
            "wake during startup should defer runtime recovery"
        )
        try expect(
            wakeRuntimeRecoveryAction(shouldResumeAfterWake: true,
                                      isTerminating: false,
                                      hasStartupTask: false,
                                      isBusy: false,
                                      isSpeechModelReady: true),
            equals: .startAudioRuntime,
            "wake after a loaded model should restart audio without reloading the model"
        )
        try expect(
            wakeRuntimeRecoveryAction(shouldResumeAfterWake: true,
                                      isTerminating: false,
                                      hasStartupTask: false,
                                      isBusy: false,
                                      isSpeechModelReady: false),
            equals: .startFullStartup,
            "wake without a loaded model should fall back to full startup"
        )
    }

    private static func testHandledHotkeySuppression() throws {
        var state = HotkeyTransitionState()
        let f5 = hotkeyChoice(forKeycode: 96)
        let f7 = hotkeyChoice(forKeycode: 98)

        try expect(
            state.transition(for: event(.keyDown, keycode: f5.keycode), hotkey: f5, triggerMode: .hold, isRecording: false),
            equals: HotkeyTransitionResult(suppress: true, actions: [.press]),
            "F-key keyDown should suppress and press"
        )
        try expect(
            state.transition(for: event(.keyDown, keycode: 97), hotkey: f5, triggerMode: .hold, isRecording: false),
            equals: .pass,
            "non-hotkey keyDown should pass through"
        )
        try expect(
            state.transition(for: event(.keyUp, keycode: f5.keycode), hotkey: f5, triggerMode: .hold, isRecording: false),
            equals: HotkeyTransitionResult(suppress: true, actions: [.release]),
            "F-key keyUp should suppress and release"
        )

        try expect(
            state.transition(for: event(.keyDown, keycode: f7.keycode), hotkey: f7, triggerMode: .hold, isRecording: false),
            equals: HotkeyTransitionResult(suppress: true, actions: [.press]),
            "recorded F-key keyDown should suppress and press"
        )
    }

    private static func testFKeyAutoRepeatSuppressesWithoutAction() throws {
        var state = HotkeyTransitionState()
        let f5 = hotkeyChoice(forKeycode: 96)

        try expect(
            state.transition(for: event(.keyDown, keycode: f5.keycode), hotkey: f5, triggerMode: .hold, isRecording: false),
            equals: HotkeyTransitionResult(suppress: true, actions: [.press]),
            "initial F-key keyDown should press"
        )
        try expect(
            state.transition(for: event(.keyDown, keycode: f5.keycode, isAutoRepeat: true), hotkey: f5, triggerMode: .hold, isRecording: false),
            equals: .suppressOnly,
            "F-key autorepeat keyDown should suppress without action"
        )
    }

    private static func testRightModifierReleaseWithLeftFlagStillSet() throws {
        var state = HotkeyTransitionState()
        let rightOption = hotkeyChoice(forKeycode: 61)
        let alternate = CGEventFlags.maskAlternate.rawValue

        try expect(
            state.transition(for: event(.flagsChanged, keycode: rightOption.keycode, flags: alternate), hotkey: rightOption, triggerMode: .hold, isRecording: false),
            equals: HotkeyTransitionResult(suppress: true, actions: [.press]),
            "right modifier flagsChanged should press"
        )
        try expect(
            state.transition(for: event(.flagsChanged, keycode: rightOption.keycode, flags: alternate), hotkey: rightOption, triggerMode: .hold, isRecording: false),
            equals: HotkeyTransitionResult(suppress: true, actions: [.release]),
            "right modifier release should be recognized while left-side flag remains set"
        )
    }

    private static func testTogglePressFlipsOnceAndReleaseIsNoOp() throws {
        var state = HotkeyTransitionState()
        let f5 = hotkeyChoice(forKeycode: 96)

        try expect(
            state.transition(for: event(.keyDown, keycode: f5.keycode), hotkey: f5, triggerMode: .toggle, isRecording: false),
            equals: HotkeyTransitionResult(suppress: true, actions: [.press]),
            "first toggle press should start"
        )
        try expect(
            state.transition(for: event(.keyUp, keycode: f5.keycode), hotkey: f5, triggerMode: .toggle, isRecording: false),
            equals: .suppressOnly,
            "toggle release should be a no-op"
        )
        try expect(
            state.transition(for: event(.keyDown, keycode: f5.keycode), hotkey: f5, triggerMode: .toggle, isRecording: false),
            equals: HotkeyTransitionResult(suppress: true, actions: [.release]),
            "second toggle press should stop"
        )
    }

    private static func testToggleGatedPressDoesNotFlipToggleState() throws {
        var state = HotkeyTransitionState()
        let f5 = hotkeyChoice(forKeycode: 96)

        // A press the app would reject (e.g. a transcription in flight) must
        // report the reason but not flip the toggle — otherwise the next press
        // emits a swallowed .release.
        try expect(
            state.transition(for: event(.keyDown, keycode: f5.keycode), hotkey: f5, triggerMode: .toggle, isRecording: false, canStartRecording: false),
            equals: HotkeyTransitionResult(suppress: true,
                                           actions: [],
                                           startWasDeclined: true),
            "gated toggle press should report the decline without flipping state"
        )
        try expect(
            state.transition(for: event(.keyDown, keycode: f5.keycode), hotkey: f5, triggerMode: .toggle, isRecording: false, canStartRecording: true),
            equals: HotkeyTransitionResult(suppress: true, actions: [.press]),
            "press after a gated press should start immediately"
        )
        // The stop-side press must NOT be gated: once a recording is
        // active (canStartRecording is false by definition), the
        // press still has to stop it.
        try expect(
            state.transition(for: event(.keyDown, keycode: f5.keycode), hotkey: f5, triggerMode: .toggle, isRecording: true, canStartRecording: false),
            equals: HotkeyTransitionResult(suppress: true, actions: [.release]),
            "gate must not block the toggle press that stops a recording"
        )
        // Hold mode ignores the gate entirely — handlePress discarding
        // the press leaves no state behind in hold mode.
        try expect(
            state.transition(for: event(.keyDown, keycode: f5.keycode), hotkey: f5, triggerMode: .hold, isRecording: false, canStartRecording: false),
            equals: HotkeyTransitionResult(suppress: true, actions: [.press]),
            "hold-mode press should be unaffected by the gate"
        )
    }

    private static func testEscapePassesThroughWhenNotRecording() throws {
        var state = HotkeyTransitionState()
        let f5 = hotkeyChoice(forKeycode: 96)

        try expect(
            state.transition(for: event(.keyDown, keycode: ESCAPE_KEYCODE), hotkey: f5, triggerMode: .hold, isRecording: false),
            equals: .pass,
            "Escape keyDown should pass through when not recording"
        )
        try expect(
            state.transition(for: event(.keyDown, keycode: ESCAPE_KEYCODE, isAutoRepeat: true), hotkey: f5, triggerMode: .hold, isRecording: false),
            equals: .pass,
            "Escape autorepeat should pass through when not recording"
        )
        try expect(
            state.transition(for: event(.keyUp, keycode: ESCAPE_KEYCODE), hotkey: f5, triggerMode: .hold, isRecording: false),
            equals: .pass,
            "Escape keyUp should pass through when not recording"
        )
    }

    private static func testEscapeSuppressesCancelRepeatAndKeyUpWhileRecording() throws {
        var state = HotkeyTransitionState()
        let f5 = hotkeyChoice(forKeycode: 96)

        try expect(
            state.transition(for: event(.keyDown, keycode: ESCAPE_KEYCODE), hotkey: f5, triggerMode: .hold, isRecording: true),
            equals: HotkeyTransitionResult(suppress: true, actions: [.cancel]),
            "Escape keyDown should suppress and cancel while recording"
        )
        try expect(
            state.transition(for: event(.keyDown, keycode: ESCAPE_KEYCODE, isAutoRepeat: true), hotkey: f5, triggerMode: .hold, isRecording: false),
            equals: .suppressOnly,
            "Escape autorepeat from a canceled press should stay suppressed"
        )
        try expect(
            state.transition(for: event(.keyUp, keycode: ESCAPE_KEYCODE), hotkey: f5, triggerMode: .hold, isRecording: false),
            equals: .suppressOnly,
            "paired Escape keyUp should stay suppressed after cancel"
        )
        try expect(
            state.transition(for: event(.keyUp, keycode: ESCAPE_KEYCODE), hotkey: f5, triggerMode: .hold, isRecording: false),
            equals: .pass,
            "later Escape keyUp should pass through once the canceled press is complete"
        )
    }

    private static func event(
        _ type: CGEventType,
        keycode: CGKeyCode,
        flags: UInt64 = 0,
        isAutoRepeat: Bool = false
    ) -> HotkeyEventSnapshot {
        HotkeyEventSnapshot(
            typeRawValue: type.rawValue,
            keycode: keycode,
            flagsRawValue: flags,
            isAutoRepeat: isAutoRepeat
        )
    }

    private static func expect<T: Equatable>(
        _ actual: T,
        equals expected: T,
        _ message: String
    ) throws {
        guard actual == expected else {
            throw SelfTestFailure.failed("\(message): got \(actual), expected \(expected)")
        }
    }
}

if let status = PresspeechSelfTest.run(arguments: Array(CommandLine.arguments.dropFirst())) {
    exit(status)
}
#endif

let app = NSApplication.shared
if let launch = UpdateProgressLaunch(arguments: Array(CommandLine.arguments.dropFirst())) {
    let delegate = UpdateProgressAppDelegate(launch: launch)
    app.delegate = delegate
    app.run()
} else {
    let delegate = PresspeechApp()
    app.delegate = delegate
    // Refuse to start under a tampered launch environment that would
    // redirect FluidAudio's model download to an attacker-controlled host.
    // Runs after NSApplication.shared is initialised so NSAlert.runModal
    // has its event loop.
    refuseHostileRegistryEnvironmentAndExit()
    app.run()
}
