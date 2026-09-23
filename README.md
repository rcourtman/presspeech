<p align="center">
  <img src="icon/hero.svg" alt="Presspeech — private local dictation for Mac and Windows." width="900">
</p>

<p align="center">
  <a href="https://github.com/rcourtman/presspeech/releases/latest"><img src="https://img.shields.io/github/v/release/rcourtman/presspeech?label=release&color=10B981" alt="Latest release"></a>
  <a href="https://github.com/rcourtman/presspeech/actions/workflows/check.yml"><img src="https://github.com/rcourtman/presspeech/actions/workflows/check.yml/badge.svg" alt="Build status"></a>
  <a href="https://github.com/rcourtman/presspeech/blob/main/LICENSE"><img src="https://img.shields.io/github/license/rcourtman/presspeech?color=10B981" alt="MIT licensed"></a>
  <a href="https://rcourtman.github.io/presspeech/install.html"><img src="https://img.shields.io/badge/macOS-Released%20%C2%B7%20notarised-10B981" alt="macOS: released and notarised"></a>
  <a href="https://rcourtman.github.io/presspeech/windows.html"><img src="https://img.shields.io/badge/Windows-Prerelease%20%C2%B7%20unsigned-D97706" alt="Windows: prerelease and unsigned"></a>
  <a href="https://github.com/rcourtman/homebrew-presspeech"><img src="https://img.shields.io/badge/Homebrew-Cask-10B981?logo=homebrew&logoColor=white" alt="Homebrew Cask"></a>
  <a href="https://rcourtman.github.io/presspeech/"><img src="https://img.shields.io/badge/Docs-GitHub%20Pages-10B981" alt="Documentation site"></a>
</p>

# Presspeech

**Private push-to-talk dictation for Mac and Windows.** Hold is the default;
choose **Press to toggle** to start and stop with separate presses. Presspeech
transcribes locally before pasting at the cursor. If it cannot safely identify
the same destination, the transcript stays on the clipboard for manual paste.
No account, subscription, or cloud transcription.

**Start here:** [official website](https://rcourtman.github.io/presspeech/) ·
[first private dictation](https://rcourtman.github.io/presspeech/getting-started.html) ·
[macOS install](https://rcourtman.github.io/presspeech/install.html) ·
[Windows install](https://rcourtman.github.io/presspeech/windows.html)

Choose the build that matches your computer:

| | macOS | Windows |
| --- | --- | --- |
| **Status** | Released, signed, and notarised | Prerelease; installer is currently unsigned |
| **System** | Apple Silicon, macOS 14+ | x64 PC; Windows 11 recommended |
| **Default language path** | Multilingual Parakeet | Multilingual Parakeet with usable NVIDIA CUDA; English-only Whisper base.en otherwise |
| **First model download** | About 500–600 MB | About 141 MiB on CPU or 2.5 GB with CUDA |
| **Start** | [Install on macOS](https://rcourtman.github.io/presspeech/install.html) | [Verify and install the Windows prerelease](https://rcourtman.github.io/presspeech/windows.html) |

**New to Presspeech?** Follow the
[four-checkpoint first-dictation guide](https://rcourtman.github.io/presspeech/getting-started.html)
from install, through Ready and the private scratchpad, to one simple target
app.

> **Published downloads and source can differ.** The `main` branch can contain
> an unreleased candidate. The install links below resolve only to published
> artifacts; features labelled **Upcoming** are not in those downloads yet.

> Presspeech now uses the `com.local.presspeech` identity throughout.
> When upgrading from an earlier identity, saved preferences and local
> dictionary rules migrate automatically. macOS privacy permissions must
> be granted once to the current identity.

<p align="center">
  <img src="icon/demo.svg" alt="Demo: hold Right Option, speak, and on release the sentence quickly lands at the cursor." width="900">
</p>

The released macOS build is a native Swift menu-bar app for Apple Silicon. Under
the hood, speech recognition runs locally through
[FluidAudio](https://github.com/FluidInference/FluidAudio), CoreML,
and the Apple Neural Engine. The default model is multilingual
[Parakeet TDT v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3).

> **~90–150 ms warm ASR inference** · **8.4 MB release zip** · **~80 MB RAM** · **0% CPU between dictations**

> Measured p50 is 92–152 ms across four synthetic TTS clips (2.5–9.5 s) on a
> Mac mini M4; the first inference after model load is excluded. This excludes
> microphone capture and paste, so it is not end-to-end dictation latency. See
> the [benchmark methodology and results](docs/benchmarks.html).

## Install on Windows

Download the self-contained installer—Python is not required:

- Open the [current Windows download and verification steps](https://rcourtman.github.io/presspeech/windows.html#download-verify-run).
  The deployed guide keeps the versioned installer, matching checksum, and
  guarded PowerShell commands together while the next prerelease is prepared.
- After verification, run the installer and launch Presspeech from the Start
  Menu.
- In the upcoming Windows build containing this change, Setup asks before
  downloading missing Parakeet model files (up to ~2.5 GB). Choose that
  download, the smaller English-only Whisper base.en CPU model (~141 MiB),
  another model in Settings, or **Set Up Later**. Published 0.1.12 does not
  include this prompt. Wait for **Preparing speech model…** to disappear
  before the first dictation.
- If a shell-capable assistant is doing the installation, give it the
  [guarded Windows prompt](https://rcourtman.github.io/presspeech/install/agents.md).
  It checks x64 compatibility, pins the current release, verifies the checksum,
  asks before launch, and stops rather than weakening Windows security policy.

The installer is currently unsigned, so SmartScreen may show **Unknown
publisher**. Choose **More info → Run anyway** only after SHA-256 verification
and if SmartScreen offers that choice. That option does not apply to a Smart
App Control block, which has no per-app exception. If Smart App Control or
managed policy blocks the installer, stop; do not try to circumvent the block.
The installed app is about
4.4 GB. On a fresh PC with NVIDIA CUDA, the upcoming Windows build containing
this change asks before the default multilingual Parakeet model's first
download (about 2.5 GB); without usable CUDA, Presspeech selects the smaller
Whisper base.en CPU model (about 141 MiB), which is English-only. Other
local models remain selectable in Settings; review the [Windows language and hardware
split](https://rcourtman.github.io/presspeech/windows.html#language-support)
before downloading if you need another language.

**Before installing or launching Windows 0.1.12:** its model downloads may send
Hugging Face usage telemetry and include an already-configured or locally saved
Hugging Face token. Custom download routing can change where the model
request—and a token it carries—goes.
If a Hugging Face token or custom download route is configured on this PC—or
you are unsure—wait until Windows 0.1.13 is published. The public models need
no account token; dictation audio and transcripts are not sent in model
downloads. See the
[Windows privacy decision and technical details](https://rcourtman.github.io/presspeech/windows.html#model-download-privacy)
and the [version-specific network inventory](https://rcourtman.github.io/presspeech/privacy.html#network-calls).

See [`windows/README.md`](windows/README.md) for Windows usage, hardware, and
source-build details.

## Install on macOS

Download the notarised app:

- [Download the latest published Presspeech.zip](https://github.com/rcourtman/presspeech/releases/latest/download/Presspeech.zip).
- For a version-pinned archive and its matching SHA-256, use the
  [current macOS install guide](https://rcourtman.github.io/presspeech/install.html#direct-download).
- Unzip it, move **Presspeech.app** to **Applications**, then open it.

Or install with Homebrew, which is the easiest path for updates:

```sh
brew install --cask rcourtman/presspeech/presspeech
open /Applications/Presspeech.app
```

On first launch, macOS shows its standard downloaded-app confirmation.
Choose **Open** after checking that it says Apple found no malicious software;
Presspeech then appears in the menu bar.

Requirements:

- Apple Silicon Mac
- macOS 14 (Sonoma) or later

The macOS 0.3.8 release starts its first local speech-model download
(about 500–600 MB) on launch. In 0.3.9, a clean install must choose
**Download Model** in Setup; close Setup to defer. CoreML also needs free space
to prepare the model, and Setup shows the current estimated space needed before
the download starts. Existing installs and cached models continue loading
automatically. Use **Setup Checklist…** to finish the
model, permission, and hotkey checks. The checklist stays incomplete until the
configured hotkey actually reaches Presspeech; if it does not respond or
controls another Mac feature, choose a different key under Settings.
Presspeech asks for Microphone,
Accessibility (shown as **Device Control and Data Access** on macOS 27 and
later), and Input Monitoring because it records while the hotkey is active,
observes the global hotkey, and pastes text at the cursor.
Accessibility is a broad system-control grant; review its scope and Apple's
guidance in the [macOS permission section](https://rcourtman.github.io/presspeech/install.html#permissions)
before granting it.

If the Presspeech item is hidden by a crowded or notched menu bar, open
**Presspeech.app** again from Applications, Finder, or Spotlight. The running
app opens Setup Checklist instead of starting a second copy. Enable **Show in
Dock** there for a persistent alternative; right-clicking its Dock icon
exposes dictation controls, Settings, and Support.

### Assistant Install Prompt

<details>
<summary>Have a shell-capable coding assistant install Presspeech for you</summary>

```text
Install Presspeech from https://github.com/rcourtman/presspeech.

Use either the direct notarised download or the Homebrew Cask.
Homebrew is preferred when it is already installed because it also
handles updates:

    https://github.com/rcourtman/presspeech/releases/latest/download/Presspeech.zip

    brew install --cask rcourtman/presspeech/presspeech
    open /Applications/Presspeech.app

Confirm the Mac is Apple Silicon and running macOS 14 or later.
After launch, use Presspeech's Setup Checklist from the menu bar to finish
the model, permissions, and hotkey readiness. Focus on setup and the first
private test; do not ask the user to star, review, or otherwise endorse the
project.
```

</details>

## Use

Hold-to-talk is the default on both platforms. In **Press to toggle** mode,
press the configured key once to start recording and again to stop. Choose the
mode under **Settings → Dictation → Trigger** on macOS or in Windows Settings
(also in Setup in the upcoming 0.1.13 build).

1. Hold the configured key, or press it once in toggle mode.
2. Speak.
3. Release the key, or press it again in toggle mode.
4. Presspeech pastes the transcript at the cursor when it can verify the
   original destination; otherwise it copies the transcript and tells you to
   paste manually.

The defaults and control surfaces differ:

- **macOS default:** **Right Option**. Open Presspeech from its menu-bar or
  optional Dock item; setup and app controls are grouped under
  **Setup Checklist…**, **Settings**, and **Support**.
- **Windows default:** **Right Alt**. Open Presspeech from its notification-area
  icon; if Right Alt acts as AltGr for your keyboard layout, choose F8 or
  another available key in Setup.

Each recording stays bound to the window that was focused when it began. If
you change windows while Presspeech is transcribing—or the destination does not
expose enough focused-window information—it copies the transcript instead of
risking delivery to the wrong place. The latter can happen in some
Electron/Chromium-based apps even when the window appears unchanged. macOS
shows **Copied — press ⌘V to paste** and keeps that recovery instruction in the
Presspeech menu until the next dictation. Windows shows a **Transcript copied,
not pasted** notification. Return to the intended field and paste manually with
⌘V on macOS or Ctrl+V on Windows; do not dictate the same text again first.

Treat a command shell as an execution surface, not an ordinary text field.
Terminal, PowerShell, Command Prompt, and remote consoles may run pasted text
as soon as it contains a newline. The default paste suffix is a space; if you
choose **Append newline**, it can submit a transcript before you inspect it.
On macOS, spoken formatting can also add line breaks. Dictate command text into
**Try Dictation** or a plain-text editor, review the exact result, then paste
and run it deliberately.

Both builds provide a private **Try Dictation** scratchpad, hold and toggle
trigger modes, configurable hotkeys, deterministic dictionary replacements,
filler removal, paste suffix choices, startup controls, update checks, and
privacy-safe diagnostics. Their menus and settings are intentionally native to
each platform rather than identical.

### macOS controls

- **Start Dictation / Stop and Transcribe** — control a recording from the
  menu without using the global hotkey; these named actions also work with
  macOS Voice Control
- **Setup Checklist…** — model, permissions, and hotkey readiness
- **Support → Try Dictation…** — a private scratchpad for verifying the
  hotkey and first transcription without switching apps
- **Support → Test App Compatibility… (macOS 0.3.8 and later)** —
  open the privacy-safe repeated test for automatic paste and focus-change
  recovery in one exact target app
- **Support → Report a Problem… / Suggest an Improvement…** — open the
  focused GitHub forms; copy the privacy-safe diagnostics first for a bug, and
  never post dictated text, audio, or dictionary contents. GitHub reported
  that issue creation is restricted on 23 September 2026; if a form is blocked,
  keep the report private and retry later rather than posting elsewhere
- **Presspeech → Settings…** or **Command-comma** — when **Show in Dock** is
  enabled, open the same settings hierarchy from the standard macOS app menu;
  if Command-comma is also the dictation hotkey, this Settings command wins
  while Presspeech is active and the hotkey remains global in other apps. That
  menu also exposes standard Edit and Window commands for Presspeech's
  scratchpad and manager windows
- **Settings → Dictation → Hotkey** — choose Right Option, Right Control, Right
  Command, selected F-keys, or **Record Hotkey…** for another F-key/right
  modifier or a key combined with Command, Control or Option (plus optional
  Shift), such as Command-comma. Confirm the preview before saving. Custom
  combinations take precedence over the same shortcut in other apps; conflicts
  cannot all be detected. Presspeech's Command-comma Settings command takes
  precedence while Presspeech is active and **Show in Dock** is enabled.
  Escape remains reserved for cancellation. Bindings track physical keys, and
  their labels follow the current keyboard layout.
  Hold mode ends when the trigger key is released, even if its modifiers were
  released first. Apple keyboards may require **Fn** to send an F-key
- **Settings → Dictation → Trigger** — hold-to-talk or press-to-toggle
- **Settings → Dictation → Language Hint** — auto-detect (default) or pin to one of
  the model's 25 supported European languages to bias decoding toward that
  script and reduce wrong-script bleed-through; this is a hint, not a guarantee
  of language identification or translation. Bosnian, Belarusian, and Serbian
  script hints are also available as script-filter aliases
- **Settings → Text → After Pasting** — append space, append newline, or no
  suffix
- **Settings → Text → Dictionary & Shortcuts** — correct recurring
  mishearings or map a spoken phrase to exact reusable text after
  transcription; rules are deterministic, local, searchable in a dedicated
  manager, and portable through export/import or a user-chosen sync file. They
  do not train or bias the speech model, so each distinct mishearing or
  inflected form that needs correction requires its own rule
- **Settings → Text → Spoken formatting commands** — opt in to exact
  commands such as “new line”, “new paragraph”, “bullet point”, “comma”,
  and “open quote”; when the Language Hint is French, the command set follows
  canonical French phrases such as “nouvelle ligne”, “nouveau paragraphe”,
  “virgule”, and “guillemet ouvrant”
- **Settings → Text → Remove filler words** — opt-in deterministic strip of
  "um", "uh", "ah", "er", "erm", "hm" (and elongated variants)
- **Settings → Behavior → Keep Previous Clipboard for Manual Restore
  (macOS 0.3.8 and later)** — off by default. Keeps a complete copy of the
  previous macOS clipboard in memory for up to five minutes, whether the
  transcript is pasted automatically or copied for manual paste. After
  checking that your latest dictated text arrived, choose **Restore Previous
  Clipboard…** from the main or Dock menu and confirm. Expiry only discards
  the saved copy; it never rewrites the clipboard. Another copy, disabling
  the option, or quitting retires the offer.
  Consecutive dictations preserve the original copy and original deadline.
  A complete snapshot is limited to 64 MB and 256 representations so an
  unusually large or complex clipboard cannot be retained without bound. If
  macOS or the source app cannot provide every representation, or either limit
  is exceeded, dictation continues without a partial snapshot while clipboard
  ownership remains stable, and the restore row explains why the previous
  clipboard is unavailable. Newer macOS versions may ask before Presspeech can
  read another app's clipboard for this opt-in feature.
  Builds containing the local-only clipboard protection restore the original
  items and representations on the current Mac only. The snapshot cannot retain
  the previous cross-device scope, so Presspeech does not risk making restored
  content newly available through Universal Clipboard.
  The automatic-restore option and delay presets from macOS 0.3.7 are retired;
  existing users must opt in again because manual recovery retains bytes
  longer.
  Confirmation is your decision, not proof that macOS acknowledged consumption
- **Settings → Behavior → Launch at Login** — keep dictation available after
  sign-in; if macOS needs approval, selecting the marked setting opens Login
  Items
- **Copy/Save Diagnostics** — privacy-safe support report with app state, settings counts, microphone availability, and update state; exact device names, raw error details, and logs stay local

### Windows controls

- **Dictate / Cancel Dictation (Esc)** — start, stop, or cancel from the
  notification-area menu without using the global hotkey
- **Try Dictation… / Setup… / Settings…** — test privately, revisit first-run
  readiness, or configure the hotkey, microphone, local model, text handling,
  audio feedback, and Start with Windows
- **Repair Global Hotkey** — replace the keyboard listener if menu-based
  Dictate still works but the configured key does not
- **Copy Diagnostics / Report a Problem… / Suggest an Improvement…** — copy a
  privacy-safe support report or open a focused GitHub form; upcoming 0.1.13 /
  builds containing **Test App Compatibility…** also open the repeated
  target-app test. Never post dictated text, audio, or dictionary contents

See the [Windows guide](https://rcourtman.github.io/presspeech/windows.html#first-launch)
for model readiness, AltGr-safe hotkey selection, and every Windows setting.

## Help qualify target apps

Automatic paste depends on how the destination exposes its focused window and
consumes clipboard content. Before relying on Presspeech in an important app,
run the [eight-check target-app
protocol](https://rcourtman.github.io/presspeech/app-compatibility.html) in
blank disposable fields. It records five steady-focus attempts and three
focus-change attempts as aggregate counts: pasted once, recovered safely, or
incorrect/unsafe. Never publish the phrases or transcripts.

Passing reports matter as much as failures because they provide the denominator
for platform/app/version/field combinations. If no app is already in mind, the
protocol's live coverage links separate native, browser, and Electron/Chromium
reports so an unrepresented target class is easy to choose. [Browse existing compatibility
reports](https://github.com/rcourtman/presspeech/issues?q=is%3Aissue%20in%3Atitle%20%22%5BCompatibility%5D%22)
before opening the focused [compatibility report
form](https://github.com/rcourtman/presspeech/issues/new?template=compatibility_report.yml).
GitHub reported issue creation as restricted on 23 September 2026, so verify
the route accepts reports first; if it does not, keep the aggregate counts
private and retry later rather than posting sensitive data elsewhere. The
worksheet's **Download report block** saves only those six counts and their
overall classification as a plain-text file; it does not save phrases or
transcripts.
If the same platform, app version, and generic field type already has a report,
add the worksheet's counts and overall result there with your Presspeech/OS
versions and relevant conditions—even when your outcome differs—instead of
opening a duplicate.
Community observations are exploratory evidence, not a promise of universal
support or a substitute for native release qualification.

## Privacy

Presspeech is local-first:

- Audio is captured in memory, transcribed locally, then discarded.
- No cloud transcription.
- No Presspeech-authored analytics, accounts, or crash reporter. Bundled
  speech-library network behavior differs by published version; see the
  [privacy inventory](https://rcourtman.github.io/presspeech/privacy.html#network-calls).
- Published Windows 0.1.12 may include a locally available Hugging Face account
  token in model-download requests and honors inherited `HF_ENDPOINT` and
  `HUGGINGFACE_CO_STAGING` settings; a configured endpoint may therefore
  receive that token. An inherited `HF_HUB_USER_AGENT_ORIGIN` is also added to
  request metadata. Its pinned Hub 1.29.0 client may also fetch
  `/api/agent-harnesses` when its local registry cache is missing or stale and
  may add an `agent/<id>` label based on inherited agent-related environment
  markers. Upcoming 0.1.13 disables Hub telemetry before imports, checks for
  agent attribution, fixes the endpoint, and removes inherited values. See the
  version-scoped [network inventory](https://rcourtman.github.io/presspeech/privacy.html#network-calls).
- Transcript content is never written to logs.
- Recent transcript history is in-memory only and clears on quit.
- Text corrections stay local unless you choose a sync file yourself.
- Completed transcripts pass through the operating-system clipboard. macOS
  0.3.8 can expose those entries to macOS Universal Clipboard. Builds containing
  local-only transcript clipboard writes keep every Presspeech transcript on
  the current Mac and add standard transient, auto-generated, and concealed
  markers for cooperating clipboard managers while preserving local Command-V.
  They also republish a restored previous clipboard on the current Mac only. macOS Clipboard History in
  Spotlight on macOS 26 or later and other local clipboard readers remain
  separate boundaries. Published Windows 0.1.12 Windows clipboard writes can be
  retained or synced; Upcoming Windows 0.1.13 asks Windows to exclude every
  dictation write from Clipboard History and Cloud Clipboard while preserving
  local Ctrl-V.

Network calls made by Presspeech are limited to:

- speech model downloads normally from the public Hugging Face Hub and its storage CDN (first launch, integrity-failure re-download, or user-triggered cache reset); upcoming macOS 0.3.9 asks new installs before the first download and removes inherited Hugging Face account tokens from its own download process. Published Windows 0.1.12 honors inherited `HF_ENDPOINT`/`HUGGINGFACE_CO_STAGING` routing and `HF_HUB_USER_AGENT_ORIGIN` metadata, and may send a configured or cached Hugging Face token to the configured endpoint. Its pinned Hub client may also request `/api/agent-harnesses` when its registry cache is missing or stale and add an `agent/<id>` label to model-request metadata. Upcoming Windows 0.1.13 fixes those settings, disables Hub telemetry, checks for agent attribution, and disables implicit authentication,
- optional GitHub release checks (fixed `presspeech-update-check` on macOS or `presspeech-windows-update-check` on Windows; no version, device, or user identifiers; mutable release responses are ignored),
- user-triggered bug-report and feature-request links, plus the compatibility
  guide link in macOS 0.3.8 / upcoming Windows 0.1.13 or builds containing that
  action; these open fixed public pages in the default browser without adding
  app or user data to the URL,
- user-approved install/update downloads from GitHub Releases directly or through Homebrew (formulae.brew.sh, the GitHub APIs, the tap). Windows accepts only release metadata marked immutable and verifies the release asset's size and SHA-256 before offering to run it and again immediately before launch.

## How It Works

```text
CGEventTap hotkey or accessible menu action
  → AVAudioEngine capture
  → 16 kHz mono Float32 audio
  → FluidAudio / Parakeet TDT v3 CoreML model / ANE
  → local dictionary rules and voice shortcuts
  → optional spoken formatting and filler removal
  → clipboard paste at cursor
```

The app is intentionally small: one SwiftPM target, one main Swift app
file, AppKit menu-bar UI, AVFoundation audio capture, CoreGraphics
events, and CoreML inference.

## Develop

```sh
git clone https://github.com/rcourtman/presspeech.git
cd presspeech/swift
./dev-run.sh
```

Useful checks:

```sh
swift build
swift run Presspeech --self-test all
../ship-swift.sh --dry-run   # release script lives at the repo root
```

Before publishing a release, run the manual checklist in
`docs/manual-qa.md`. User-facing recovery help lives on the
[troubleshooting page](https://rcourtman.github.io/presspeech/troubleshooting.html);
its concise Markdown reference is `docs/troubleshooting.md`.

Key files:

- `swift/Sources/Presspeech/main.swift` — app implementation
- `swift/Package.swift` — SwiftPM manifest
- `swift/dev-run.sh` — signed local dev build
- `ship-swift.sh` — signed, notarised release workflow
- `entitlements.plist` — hardened-runtime microphone entitlements
- `experiments/swift-bench/` — latency benchmark harness

Release notes live in `swift/release-notes/`.

For the Windows implementation:

```bat
cd windows
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install -r requirements-cuda.txt
.venv\Scripts\python -m unittest discover -s tests -v
run.bat
```

See [`windows/README.md`](windows/README.md) for hardware, setup, and usage
details. Before treating a candidate as stable, complete the artifact-bound CPU
and NVIDIA qualification record in [`docs/manual-qa.md`](docs/manual-qa.md).

## Links

- [Support and troubleshooting](SUPPORT.md)
- [Contributing](CONTRIBUTING.md) and [Code of Conduct](CODE_OF_CONDUCT.md)
- [Test target-app compatibility](https://rcourtman.github.io/presspeech/app-compatibility.html)
- [Product roadmap](ROADMAP.md)
- [Getting started and first dictation](https://rcourtman.github.io/presspeech/getting-started.html)
- [Latest release](https://github.com/rcourtman/presspeech/releases/latest)
- [Direct download](https://github.com/rcourtman/presspeech/releases/latest/download/Presspeech.zip)
- [Windows install guide](https://rcourtman.github.io/presspeech/windows.html)
- [Documentation site](https://rcourtman.github.io/presspeech/)
- [Benchmarks and methodology](https://rcourtman.github.io/presspeech/benchmarks.html)
- [Compare Mac and Windows dictation options](https://rcourtman.github.io/presspeech/compare/)
- [Homebrew tap](https://github.com/rcourtman/homebrew-presspeech)
- [FluidAudio](https://github.com/FluidInference/FluidAudio)
- [Parakeet TDT v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)

If Presspeech saves you keystrokes, a star helps other people find it.

## License

MIT. See [LICENSE](LICENSE).
