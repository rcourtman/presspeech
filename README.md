<p align="center">
  <img src="icon/hero.svg" alt="Presspeech — private local dictation for Mac and Windows." width="900">
</p>

<p align="center">
  <a href="https://rcourtman.github.io/presspeech/install.html#model-download-privacy"><img src="https://img.shields.io/github/v/release/rcourtman/presspeech?label=release&color=10B981" alt="Latest macOS release: review the first-launch privacy warning"></a>
  <a href="https://github.com/rcourtman/presspeech/blob/main/LICENSE"><img src="https://img.shields.io/github/license/rcourtman/presspeech?color=10B981" alt="MIT licensed"></a>
  <a href="https://rcourtman.github.io/presspeech/install.html"><img src="https://img.shields.io/badge/Homebrew-Cask-10B981?logo=homebrew&logoColor=white" alt="Homebrew Cask: review macOS install warning"></a>
</p>

# Presspeech

**Hold a key, speak, let go — your words appear at the cursor.** Speech
recognition runs entirely on your computer. Free, open source, no account, no
subscription, no cloud transcription.

> **Before opening the published builds (macOS 0.3.8 or 0.3.9 / Windows 0.1.12):**
> on macOS 0.3.8 and Windows 0.1.12, a missing speech-model download starts on
> launch; macOS 0.3.9 asks first on a clean install. macOS 0.3.8 may include an
> inherited Hugging Face token in that request (0.3.9 does not); Windows 0.1.12
> may send Hugging Face usage telemetry or an available token, including to a
> custom download route. Audio and transcripts are never sent. If you are
> unsure, install but leave the app unopened (on Windows, clear the installer's
> final **Launch Presspeech** option, which starts checked) and read the
> [macOS](https://rcourtman.github.io/presspeech/install.html#model-download-privacy) or
> [Windows](https://rcourtman.github.io/presspeech/windows.html#model-download-privacy) launch decision first.

**Start here:** [first dictation guide](https://rcourtman.github.io/presspeech/getting-started.html) ·
[macOS install](https://rcourtman.github.io/presspeech/install.html) ·
[Windows install](https://rcourtman.github.io/presspeech/windows.html)

<p align="center">
  <img src="icon/demo.svg" alt="Demo: hold Right Option, speak, and on release the sentence quickly lands at the cursor." width="900">
</p>

> **~90–150 ms warm ASR inference** · **8.4 MB release zip** · **~80 MB RAM** · **0% CPU between dictations**

- **Fast and small.** A native Swift menu-bar app running NVIDIA's multilingual
  [Parakeet TDT v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) on the
  Apple Neural Engine via [FluidAudio](https://github.com/FluidInference/FluidAudio).
  See the [benchmark methodology](https://rcourtman.github.io/presspeech/benchmarks.html).
- **No cloud path.** Audio stays in memory and is discarded after
  transcription. Transcripts are never logged.
- **Pastes where you started.** If you switch windows, or Presspeech cannot
  verify the original destination, it skips automatic paste so you can paste
  manually from the clipboard instead of it typing into the wrong place. It
  cannot always detect a move to another field or browser tab in the same
  window, so keep the starting field and tab selected until the text lands.
- **Yours to tune.** Hold-to-talk or press-to-toggle, a custom hotkey, local
  correction rules and text shortcuts, spoken formatting, filler-word removal.

## Install

**macOS** — Released, signed, and notarised. Apple Silicon, macOS 14+.

```sh
brew install --cask rcourtman/presspeech/presspeech
```

Or use the [version-pinned download and checksum](https://rcourtman.github.io/presspeech/install.html#direct-download).
When you open the app, Setup Checklist covers the ~500–600 MB speech model and
the Microphone, Accessibility, and Input Monitoring permissions
([why each is needed](https://rcourtman.github.io/presspeech/install.html#permissions)).

**Windows** — Prerelease, unsigned installer, x64.
[Verify and install the Windows prerelease](https://rcourtman.github.io/presspeech/windows.html#download-verify-run).

Install links resolve to published releases. The `main` branch can contain
an unreleased candidate.

### Assistant Install Prompt

<details>
<summary>Have a shell-capable coding assistant install Presspeech for you</summary>

```text
Install Presspeech from https://github.com/rcourtman/presspeech on this Mac.

Before installing or launching macOS 0.3.8, disclose that a Hugging Face token inherited by Presspeech may be included in model-download requests; the public model needs no account token. If a token may be present in the environment that launches Presspeech—or the user is unsure—offer to wait until macOS 0.3.9 is published. Explain that an inherited lowercase `https_proxy` can route the model request through a proxy: a TLS-inspecting proxy trusted by macOS can read a 0.3.8 token, while a tunnelling proxy cannot. If the trust of a TLS-inspecting proxy is unclear, do not launch 0.3.8 while it is in use. A malformed inherited lowercase `https_proxy` or `http_proxy` URL may be logged verbatim by the bundled 0.3.8 client, including embedded proxy credentials, and then ignored, allowing a model request without the expected proxy. If the user depends on an inherited proxy and is unsure its URL is valid, leave 0.3.8 unopened; do not inspect or display proxy values or share logs containing them. From 0.3.9, Presspeech removes account-token authentication but still honors proxy settings; it refuses malformed settings before model loading. Downloading the ZIP or installing the app without opening it does not make the model request; the first download of a missing model starts when Presspeech launches. Do not inspect or display token values, change credential settings, or launch 0.3.8 without the user's informed choice. If the user chooses to wait, skip the `open` command below and leave the app unopened. Model downloads do not include dictation audio or transcripts. See https://rcourtman.github.io/presspeech/privacy.html#network-calls.

Before downloading or installing, run these read-only compatibility checks:
  uname -m
  sw_vers -productVersion

Stop if the Mac is not Apple Silicon (arm64) or macOS is older than 14. Do not download, install, or launch Presspeech on an unsupported Mac.

Presspeech has two notarised install paths: a direct release zip and a Homebrew Cask. After the compatibility checks pass, check whether Homebrew is already available:
  command -v brew

If Homebrew is available and the user chooses it, install with:
  brew install --cask rcourtman/presspeech/presspeech

Otherwise offer the direct notarised zip using the current version-pinned download and verification steps at https://rcourtman.github.io/presspeech/install.html#direct-download. Do not install Homebrew just for Presspeech. After verification, unzip and move Presspeech.app to Applications, but do not follow the guide's launch instruction yet. Do not run the Homebrew command when Homebrew is unavailable.

Only after the user makes an informed choice to launch 0.3.8:
  open /Applications/Presspeech.app

After launch, explain that macOS 0.3.8 starts its first local speech-model download (~500-600 MB) on launch; its Setup cannot defer that request. Existing installs with a valid cached model load without another download. Do not tell the user to choose Download Model or Set Up Later in 0.3.8. Before asking the user to enable Input Monitoring, explain that macOS's grant can expose typed keys; Presspeech requests keyboard events only to detect the configured hotkey and Escape to cancel an active recording, passes other keys through without saving, logging, or sending their values, and does not inspect mouse or trackpad events. Offer Apple's guide at https://support.apple.com/guide/mac-help/mchl4cedafb6/mac. Use Setup Checklist to finish the model, permissions, and hotkey readiness. The default dictation key is Right Option. Focus on setup and the first in-app test; explain that the scratchpad can still use the system clipboard and use only harmless words. Do not ask the user to star, review, or otherwise endorse the project.

A successful Try Dictation scratchpad test proves only the in-app path, not automatic paste into another app. Do not use a production field, live chat, form that can submit, or command shell as a first target. If the user asks to try another app, follow https://rcourtman.github.io/presspeech/getting-started.html#first-app with harmless words in a blank, disposable field. Count an automatic-paste pass only when the complete text appears once with no recovery notice. For macOS 0.3.8, a copied notice is manual recovery, not a paste pass: inspect the intended field first, then verify the clipboard still holds the complete transcript before manual paste; a later copy may have replaced it. Do not retry blindly. Use https://rcourtman.github.io/presspeech/app-compatibility.html before relying on repeated delivery to a specific app.

Only if the user asks about a future build: a clean install of 0.3.9 is planned to choose Download Model in Setup or Set Up Later to defer. That behavior is not in published 0.3.8; check GitHub Releases before describing it as available.
```

</details>

## Learn more

- [Getting started](https://rcourtman.github.io/presspeech/getting-started.html) ·
  [Troubleshooting](https://rcourtman.github.io/presspeech/troubleshooting.html) ·
  [FAQ](https://rcourtman.github.io/presspeech/faq.html)
- [Privacy and every network call](https://rcourtman.github.io/presspeech/privacy.html)
- [App compatibility](https://rcourtman.github.io/presspeech/app-compatibility.html) —
  test automatic paste in the apps you use
- [Compare with other dictation apps](https://rcourtman.github.io/presspeech/compare/)
- [Windows guide](https://rcourtman.github.io/presspeech/windows.html) ·
  [Roadmap](ROADMAP.md) · [Support](SUPPORT.md)

## Develop

```sh
git clone https://github.com/rcourtman/presspeech.git
cd presspeech/swift
./dev-run.sh
swift run Presspeech --self-test all
```

The macOS app is `swift/Sources/Presspeech/main.swift`; the Windows build lives
in [`windows/`](windows/README.md). See [CONTRIBUTING.md](CONTRIBUTING.md) for
checks, release steps, and the Windows toolchain.

## License

MIT. See [LICENSE](LICENSE).
