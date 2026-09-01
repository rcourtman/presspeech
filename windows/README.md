# Presspeech for Windows

Fast, private, local push-to-talk dictation for Windows — a Windows port of
[presspeech](https://github.com/rcourtman/presspeech) (macOS). Hold a hotkey,
speak, release, and the transcript is typed at the cursor. No cloud, no
accounts, no telemetry — speech recognition runs entirely on your machine.

Preferred engine: **NVIDIA Parakeet-TDT-0.6B-v3** — the same model family
Presspeech uses on macOS. On an NVIDIA GPU (CUDA) it transcribes with
punctuation and capitalization in a fraction of real time (~50× realtime on an
RTX 3070). Parakeet loads in FP16 on CUDA to halve resident model tensors, with
an automatic FP32 retry if the half-precision load fails. On a fresh PC where
the packaged runtime cannot use CUDA, Presspeech instead selects Whisper
base.en with int8 CPU inference. The other Parakeet, Nemotron, and Whisper
models remain available in Settings, and explicit choices are not overridden.

## Install

Download the self-contained Windows x64 installer:

- [Presspeech-Setup-0.1.10-x64.exe](https://github.com/rcourtman/presspeech/releases/download/windows-v0.1.10/Presspeech-Setup-0.1.10-x64.exe)
- [Release notes and SHA-256 checksum](https://github.com/rcourtman/presspeech/releases/tag/windows-v0.1.10)

No Python installation or command-line setup is required. Presspeech installs
per-user under `%LOCALAPPDATA%\Programs\Presspeech`, adds a Start Menu shortcut,
and appears in **Settings → Apps → Installed apps** for normal uninstallation.

The current prerelease is not code-signed. Windows SmartScreen may report
**Unknown publisher**. Follow the public guide's
[download and PowerShell verification steps](https://rcourtman.github.io/presspeech/windows.html#download-verify-run),
then choose **More info → Run anyway** only if Windows offers that choice and
the guide reports that SHA-256 verification succeeded. Windows 11 Smart App
Control or managed policy may block an unsigned app without offering an
override; do not try to circumvent that policy.

Requirements:

- Windows 11, x64. Presspeech remains compatible with Windows 10 x64, but
  [Microsoft ended general Windows 10 support on 14 October 2025](https://support.microsoft.com/en-us/windows/deployment/updates-lifecycle/windows-10-support-has-ended-on-october-14-2025);
  use it only with Extended Security Updates or an edition that remains supported.
- About 4.4 GB for the app, plus about 141 MiB for the CPU default or 2.5 GB
  for the CUDA Parakeet model cache
- A current NVIDIA driver is recommended for the fastest and most accurate
  default; Windows PCs without usable CUDA automatically start with the smaller
  Whisper base.en CPU model

First launch detects whether the packaged Torch runtime can use NVIDIA CUDA,
then downloads either Parakeet (~2.5 GB) or Whisper base.en (~141 MiB) into
`%USERPROFILE%\.cache\huggingface`, and loads and warms it in the background.
Each Presspeech release pins every Windows Hugging Face model to an exact
repository commit reviewed for that app version, so a fresh install cannot
silently receive a different snapshot. Windows relies on the immutable Hugging
Face snapshot identity; unlike the macOS model cache, it does not independently
verify every downloaded model file against a SHA-256 manifest.
The first-run readiness window shows model loading, microphone selection and a
live microphone check, a selectable push-to-talk key, and Start with Windows in
one place. Speak while the check runs. It briefly opens the selected input,
discards its samples in memory, and distinguishes an input level from a
connected-but-silent device or one that cannot be opened. If it is silent,
unmute it and choose **Check Again**; if it cannot be opened, use the window's
direct links to Windows Microphone Privacy or Sound Input settings first. Wait
until it says the model is ready before the
first dictation. **Try Dictation** and **Finish Setup** remain disabled until
then. If preparation fails, use **Retry Speech Model**; the window keeps
tracking the retry instead of leaving the previous error on screen. Choose
**Set Up Later** to close the window without marking setup complete; it will
open again on the next launch. Microphone, hotkey, and Start with Windows
choices are kept when setup is deferred, and a newly selected microphone is
used immediately by **Try Dictation**. A microphone can still be connected
later and does not block **Finish Setup** once the speech model is ready.
If the push-to-talk key is pressed before readiness, Presspeech keeps showing
**Preparing speech model…** and does not open the microphone, play recording
cues, mute playback, or claim to be listening. Release and press again once the
preparation indicator disappears.
Before recording, open Windows microphone privacy settings and turn on
**Microphone access** and **Let desktop apps access your microphone**. Presspeech
is an unpackaged desktop app, so Windows uses that shared desktop-app control
rather than an app-specific Presspeech permission prompt. Also confirm the
selected device under **Settings → System → Sound → Input**.

On keyboard layouts where **Right Alt** enters `@`, `€`, or accented letters,
Windows treats that key as **AltGr**. Presspeech leaves AltGr available for
normal typing and does not start dictation from it. Choose **F8** or another
push-to-talk key in first-run setup; the choice applies immediately and remains
selected if setup is deferred.

### Install from source

For development, install Python 3.12 and create a project virtual environment:

```bat
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install -r requirements-cuda.txt
run.bat
```

## Use

1. Hold **Right Alt** (configurable).
2. Speak.
3. Release — the punctuated transcript appears at the cursor moments later.

The configured key is reserved for Presspeech while it is running, so it does
not also open a Windows surface or invoke an F8–F12 command in the focused app.
Other keys and AltGr layout input continue to pass through normally.

A short high tone confirms recording has started and a lower tone confirms it has
stopped. Audio cues are enabled by default and can be disabled in Settings.
If a press captures too little audio or the local recognizer detects no speech,
the indicator briefly says **No speech detected — try again** and a Windows
notification points back to Setup's microphone check instead of failing
silently. A quick retry cannot be hidden by the previous message's timeout.

After release, silence-aware post-roll stops as early as 80 ms while retaining
the original 400 ms safety ceiling whenever speech is still present. This keeps
final words intact without always paying the full delay.

Recordings stop and transcribe automatically at the maximum length selected in
Settings: 1, 2 (the default), 5, or 10 minutes. This bounds in-memory audio and
restores muted playback if Windows misses a hotkey release.
Press **Escape** during an active recording to cancel it immediately. The
buffered audio is discarded without transcription or clipboard changes; the
same action is available from **Cancel Dictation (Esc)** in the notification
area menu while recording.

The model stays loaded during normal use so every dictation is immediately ready.
The internal unload support is retained for an explicit gaming mode rather than
being triggered merely because dictation has been idle.

When a Moonlight stream is focused, Presspeech automatically uses Moonlight's
clipboard-typing shortcut so transcripts reach the remote host, including macOS.
Microsoft Remote Desktop is also detected automatically and uses its redirected
clipboard with a small reliability delay. Normal Windows apps retain fast Ctrl+V.
Each recording is bound to the window that was focused when it began. If focus
changes while the model is transcribing, Presspeech leaves the transcript on the
clipboard and notifies you instead of pasting private text into the wrong window.

The **Presspeech** icon in the Windows notification area (bottom-right) includes
**Dictate** (toggle), **Cancel Dictation (Esc)** while recording,
**Try Dictation…** (scratchpad that doesn't paste anywhere), **Setup…**, **Settings…**,
**Check for Updates…**, **Copy Diagnostics**, **Report a Problem…**, **Suggest an
Improvement…**, and **Exit**. The feedback actions open the focused public
GitHub forms without adding app or user data to the URL. The icon turns red
while recording. Setup, settings, update, and scratchpad controls expose names,
roles, values, and actions through Windows UI Automation for screen readers.
Each window starts focus on its main working control. Use **Left Alt** plus a
command's underlined letter to invoke it without tabbing, **Escape** to close the
current window (and cancel an active update download), and **Ctrl+S** to save
Settings. While one of these windows is open, screen readers also announce
important asynchronous status changes such as model readiness, microphone check
results, update completion or failure, and settings save results without moving
keyboard focus. Download byte counters remain visual rather than repeatedly
interrupting speech.
Windows may place the icon in the notification-area overflow. If the icon is
hard to find, launch Presspeech again from the Start Menu: the running app
restores its existing window, opens Setup during first run, or opens Settings
after setup. It does not start a second dictation process.

## Settings

- Hotkey: right/left Alt, Ctrl, Shift, Win, or F8–F12
- Trigger: hold-to-talk or press-to-toggle
- Maximum recording length: 1, 2 (default), 5, or 10 minutes
- Microphone: automatic selection or a specific safe Windows input device
- Engine/model: Parakeet TDT v3 and Nemotron (NVIDIA GPU recommended), or
  Whisper turbo/small/medium/base (base.en is the CPU first-run default)
- After pasting: space / newline / nothing
- Remove filler words (um, uh, er, …)
- British English spelling (color → colour, realize → realise)
- Audio cues when dictation starts and stops
- Mute every active Windows playback endpoint while recording, restoring each
  device's previous mute state afterwards
- A click-through **Listening… / Transcribing…** indicator on the active display
- Optional daily GitHub update checks; downloads and installation require
  approval, and the installer is verified by size and SHA-256 after download
  and again immediately before launch
- Dictionary: map a misheard phrase or spoken shortcut to exact text
  (e.g. "press speech" → `presspeech`), applied deterministically
- Start with Windows (registry `HKCU\...\Run`)

Saving a different speech model starts downloading/loading and warming it in
the background immediately. Settings shows whether the selected model is being
prepared, is ready, or needs attention, and offers a retry after a failure.
Dictation remains unavailable until the selected model reports ready; there is
no need to sacrifice a hotkey press to start the change or restart Presspeech.

If **Start with Windows** cannot be registered, Setup stays open and Settings
reports that the startup state was not updated instead of claiming success.
Use **Open Startup Settings** to review Presspeech under Windows
**Settings → Apps → Startup**, then retry **Finish Setup** or **Save**.

## Notes

- A working microphone must be connected. Automatic selection prefers the
  Windows Sound Mapper, skips virtual/loopback and WDM-KS devices, and resamples
  to 16 kHz. A specific safe input can be selected in Settings.
- Single-instance (named mutex) — launching again reuses the running process
  and restores its open window, or opens Setup before first-run completion and
  Settings afterward.
- All audio is processed in memory and discarded after transcription.
- Transcript content is never written to logs; diagnostics retain timings and
  character counts only.
- **Copy Diagnostics** includes configuration counts and runtime state, never
  transcripts, audio, or dictionary contents.
- Clipboard is used briefly to paste; it is overwritten.
- `python app.py --selftest` verifies the engine pipeline.
- `python benchmark.py` runs the repeatable local latency/accuracy evaluation;
  Whisper reports include the exact Silero VAD boundary policy so WER, quiet
  speech rejection, and silence false positives remain comparable across
  dependency updates;
  see `benchmarks/README.md` for the reviewed-reference workflow. A manifest
  sample marked with both `"expected_silence": true` and
  `"reference_reviewed": true` is scored as a non-speech fixture; reports count
  any non-empty transcript as a silence false positive. Whisper reports also
  record the VAD-retained speech duration for every trial and count reviewed
  speech clips that VAD rejected, so silence fixes cannot hide quiet-speech
  regressions behind aggregate WER. Reviewed speech clips score final-word
  retention on every trial as well, so an intermittent clipped ending cannot
  be hidden by the consensus transcript.
- If you see missing-DLL errors, install the Visual C++ Redistributable
  (x64) from Microsoft.

## Develop and test

Always run the Windows code with its project virtual environment:

```bat
.venv\Scripts\python -m unittest discover -s tests -v
.venv\Scripts\python app.py --selftest
```

The unit tests do not load a speech model. The self-test does, and therefore
also verifies the installed Torch/CUDA/model pipeline. Local benchmark audio,
results, virtual environments, caches, and logs are ignored by Git.

## Build the installer

Release builds use CPython 3.12.10 (the final 3.12 release with Windows
installers) and the fully resolved Windows dependency
set in `requirements-release.txt`; source-development installs intentionally
retain the lower bounds in `requirements.txt`. Install Inno Setup 6, create a
clean release environment, then run the build script from `windows/`:

```powershell
winget install --id JRSoftware.InnoSetup --exact
py -3.12 -m venv .release-venv
.\.release-venv\Scripts\python -m pip install --only-binary=:all: -r requirements-release.txt
.\.release-venv\Scripts\python -m pip install --no-deps -r requirements-cuda.txt
.\.release-venv\Scripts\python -m pip check
powershell -ExecutionPolicy Bypass -File .\build-release.ps1 `
  -Version 0.1.10 -Python .\.release-venv\Scripts\python.exe
```

`build-release.ps1` refuses to package with a different Python patch or any
missing/drifted dependency. When an intentional dependency update changes an
input requirements file, install [uv](https://docs.astral.sh/uv/) and regenerate
the resolved set from the repository root with
`python windows/release_requirements.py`.

The build uses a short temporary staging path to avoid Windows path-length
failures and writes the installer plus checksum under `dist\installer`. Build
outputs remain ignored by Git. Before creating the installer, the build runs a
model-free smoke test through the frozen executable to verify that its lazy ASR
backends and native runtime modules were actually packaged. The manual
`windows-release` GitHub workflow builds and publishes a Windows prerelease. Its
`expected_sha` input must be the exact 40-character `main` commit being released;
the workflow stops before the build if it was dispatched from another ref, the
branch has moved, the repository or Windows push workflows are not green for
that exact commit, or the version's existing release tag points to a different
commit. Same-version jobs are serialized, and the tag is verified again after
the build before creating the release. The installer and checksum are uploaded
while the release is still a draft, and it is published only after both uploads
succeed. If the atomic create command fails after leaving a private draft, the
same job inspects and completes it before discarding the exact build outputs. A
later rerun resumes a remaining draft only after its tag, title, prerelease
state, target commit, release notes, and any existing assets exactly match the
approved release. It uploads only missing fixed-name assets without clobbering,
then revalidates both the complete draft and the release tag immediately before
publication. Existing published assets are never replaced: a rerun must
reproduce them exactly. The workflow compares GitHub's published asset names,
sizes,
SHA-256 digests, and download URLs with the local installer and checksum before
reporting a successful release.
If the repository later receives a code-signing
certificate, add its base64 PFX and password as
`WINDOWS_CERTIFICATE_BASE64` and `WINDOWS_CERTIFICATE_PASSWORD`; the same build
automatically signs both the app executable and installer.
