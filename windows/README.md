# Presspeech for Windows

Fast, private, local push-to-talk dictation for Windows — a Windows port of
[presspeech](https://github.com/rcourtman/presspeech) (macOS). Hold a hotkey,
speak, release, and the transcript is typed at the cursor. No cloud, no
accounts, no telemetry — speech recognition runs entirely on your machine.

Default engine: **NVIDIA Parakeet-TDT-0.6B-v3** — the same model family
Presspeech uses on macOS. On an NVIDIA GPU (CUDA) it transcribes with
punctuation and capitalization in a fraction of real time (~50× realtime on an
RTX 3070). Parakeet loads in FP16 on CUDA to halve resident model tensors, with
an automatic FP32 retry if the half-precision load fails. Whisper
(`faster-whisper`) models are available as faster/lighter alternatives and as
automatic fallback.

## Install

Download the self-contained Windows x64 installer:

- [Presspeech-Setup-0.1.10-x64.exe](https://github.com/rcourtman/presspeech/releases/download/windows-v0.1.10/Presspeech-Setup-0.1.10-x64.exe)
- [Release notes and SHA-256 checksum](https://github.com/rcourtman/presspeech/releases/tag/windows-v0.1.10)

No Python installation or command-line setup is required. Presspeech installs
per-user under `%LOCALAPPDATA%\Programs\Presspeech`, adds a Start Menu shortcut,
and appears in **Settings → Apps → Installed apps** for normal uninstallation.

This first build is not code-signed. Windows SmartScreen may report **Unknown
publisher**; choose **More info → Run anyway** after verifying the checksum on
the release page.

Requirements:

- Windows 10 or 11, x64
- About 4.4 GB for the app and 2.5 GB for the first-run model cache
- A current NVIDIA driver is strongly recommended for fast Parakeet inference

First launch downloads the Parakeet model once (~2.5 GB) into
`%USERPROFILE%\.cache\huggingface`, then loads and warms it in the background.
Each Presspeech release pins its Transformers-backed Hugging Face models to
the immutable snapshots exercised by native QA, so a fresh install cannot
silently receive different model files from the same app version.
The first-run readiness window shows model loading, microphone selection, the
push-to-talk key, and Start with Windows in one place. Wait until it says the
model is ready before the first dictation.
If the push-to-talk key is pressed before readiness, Presspeech keeps showing
**Preparing speech model…** and does not open the microphone, play recording
cues, mute playback, or claim to be listening. Release and press again once the
preparation indicator disappears.
First recording triggers the Windows microphone permission prompt—allow it.

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

A short high tone confirms recording has started and a lower tone confirms it has
stopped. Audio cues are enabled by default and can be disabled in Settings.

After release, silence-aware post-roll stops as early as 80 ms while retaining
the original 400 ms safety ceiling whenever speech is still present. This keeps
final words intact without always paying the full delay.

Recordings stop automatically after two minutes. This bounds in-memory audio
and restores muted playback if Windows misses a hotkey release.

The model stays loaded during normal use so every dictation is immediately ready.
The internal unload support is retained for an explicit gaming mode rather than
being triggered merely because dictation has been idle.

When a Moonlight stream is focused, Presspeech automatically uses Moonlight's
clipboard-typing shortcut so transcripts reach the remote host, including macOS.
Microsoft Remote Desktop is also detected automatically and uses its redirected
clipboard with a small reliability delay. Normal Windows apps retain fast Ctrl+V.

Tray icon (bottom-right) menus include **Dictate** (toggle), **Try Dictation…**
(scratchpad that doesn't paste anywhere), **Setup…**, **Settings…**,
**Check for Updates…**, **Copy Diagnostics**, and **Exit**. The icon turns red
while recording.

## Settings

- Hotkey: right/left Alt, Ctrl, Shift, Win, or F8–F12
- Trigger: hold-to-talk or press-to-toggle
- Microphone: automatic selection or a specific safe Windows input device
- Engine/model: Parakeet TDT v3 (GPU, best), Whisper turbo/small/medium/base
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

## Notes

- A working microphone must be connected. Automatic selection prefers the
  Windows Sound Mapper, skips virtual/loopback and WDM-KS devices, and resamples
  to 16 kHz. A specific safe input can be selected in Settings.
- Single-instance (named mutex) — launching twice does nothing.
- All audio is processed in memory and discarded after transcription.
- Transcript content is never written to logs; diagnostics retain timings and
  character counts only.
- **Copy Diagnostics** includes configuration counts and runtime state, never
  transcripts, audio, or dictionary contents.
- Clipboard is used briefly to paste; it is overwritten.
- `python app.py --selftest` verifies the engine pipeline.
- `python benchmark.py` runs the repeatable local latency/accuracy evaluation;
  see `benchmarks/README.md` for the reviewed-reference workflow.
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

Install Inno Setup 6 and the pinned build dependency, then run the release
script from `windows/`:

```powershell
winget install --id JRSoftware.InnoSetup --exact
.\.venv\Scripts\python -m pip install -r requirements-build.txt
powershell -ExecutionPolicy Bypass -File .\build-release.ps1 -Version 0.1.10
```

The build uses a short temporary staging path to avoid Windows path-length
failures and writes the installer plus checksum under `dist\installer`. Build
outputs remain ignored by Git. Before creating the installer, the build runs a
model-free smoke test through the frozen executable to verify that its lazy ASR
backends and native runtime modules were actually packaged. The manual
`windows-release` GitHub workflow builds and publishes a Windows prerelease. Its
`expected_sha` input must be the exact 40-character `main` commit being released;
the workflow stops before the build if it was dispatched from another ref, the
branch has moved, or the version's existing release tag points to a different
commit. Same-version jobs are serialized, and the tag is verified again after
the build before creating the release and before replacing release assets. The
workflow then compares GitHub's published asset names, sizes, SHA-256 digests,
and download URLs with the local installer and checksum before reporting a
successful release.
If the repository later receives a code-signing
certificate, add its base64 PFX and password as
`WINDOWS_CERTIFICATE_BASE64` and `WINDOWS_CERTIFICATE_PASSWORD`; the same build
automatically signs both the app executable and installer.
