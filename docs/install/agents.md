# Install Presspeech with a shell-capable assistant

Choose the prompt for the computer where Presspeech should be installed. The
Windows prompt deliberately stops rather than weakening operating-system policy
for the unsigned prerelease.

## macOS

```text
Install Presspeech from https://github.com/rcourtman/presspeech on this Mac.

Before installing or launching macOS 0.3.8, disclose that a Hugging Face token inherited by Presspeech may be included in model-download requests; the public model needs no account token. If a token may be present in the environment that launches Presspeech—or the user is unsure—offer to wait until macOS 0.3.9 is published. Explain that an inherited lowercase `https_proxy` can route the model request through a proxy: a TLS-inspecting proxy trusted by macOS can read a 0.3.8 token, while a tunnelling proxy cannot. If the trust of a TLS-inspecting proxy is unclear, do not launch 0.3.8 while it is in use. Upcoming 0.3.9 removes account-token authentication but still honors proxy settings. Downloading the ZIP or installing the app without opening it does not make the model request; the first download of a missing model starts when Presspeech launches. Do not inspect or display token values, change credential settings, or launch 0.3.8 without the user's informed choice. If the user chooses to wait, skip the `open` command below and leave the app unopened. Model downloads do not include dictation audio or transcripts. See https://rcourtman.github.io/presspeech/privacy.html#network-calls.

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

After launch, explain that macOS 0.3.8 starts its first local speech-model download (~500-600 MB) on launch. In 0.3.9, a clean install must choose Download Model in Setup; choose Set Up Later to defer. Existing installs and cached models continue loading automatically. Before asking the user to enable Input Monitoring, explain that macOS's grant can expose typed keys; Presspeech requests keyboard events only to detect the configured hotkey and Escape to cancel an active recording, passes other keys through without saving, logging, or sending their values, and does not inspect mouse or trackpad events. Offer Apple's guide at https://support.apple.com/guide/mac-help/mchl4cedafb6/mac. Use Setup Checklist to finish the model, permissions, and hotkey readiness. The default dictation key is Right Option. Focus on setup and the first private test; do not ask the user to star, review, or otherwise endorse the project.
```

## Windows

```text
Install Presspeech from https://github.com/rcourtman/presspeech on this Windows PC.

Before installing or launching published Windows 0.1.12, explain that its model downloads may send Hugging Face usage telemetry and an already-configured or locally saved Hugging Face token; custom download routing can change where a request—and any token it carries—goes. The bundled HTTP client also honors configured HTTPS proxies; a TLS-inspecting HTTPS proxy trusted by the client can read any 0.1.12 token it receives. Upcoming 0.1.13 removes account-token authentication but still honors proxy and CA settings. If the user cannot confirm that a TLS-inspection proxy is trusted, don't launch while it is in use. These public models need no account token. Offer to wait until Windows 0.1.13 is published if the user prefers to avoid this possible usage telemetry, is concerned that a Hugging Face token or custom download route may be configured on this PC, or is unsure. Downloading the installer and checksum from GitHub does not make a model request; 0.1.12 starts its selected model download when Presspeech launches. If the user chooses to wait but still wants to install, tell them to uncheck the installer's final "Launch Presspeech" option; do not start the app. Do not inspect or display token values, change credential settings, or launch 0.1.12 without the user's informed choice. Dictation audio and transcripts are not sent in model downloads. See https://rcourtman.github.io/presspeech/privacy.html#network-calls.

Use only the published Windows prerelease selected by Presspeech's deployed metadata and version-pinned install guide:
  https://rcourtman.github.io/presspeech/windows.html#download-verify-run

The source branch can contain a newer unreleased candidate, so do not infer a download version from windows/config.py, release notes, or other files on main. The deployed metadata stays on a version whose installer and checksum are both public. The installer is not code-signed. Explain that before downloading; SHA-256 verification confirms that the file matches the asset published in this repository, but it is not a publisher signature.

Run these read-only checks in PowerShell:
  [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
  (Get-CimInstance Win32_OperatingSystem).Caption

Stop if the architecture is not X64. Windows 11 is recommended. If this is Windows 10, explain that general support has ended and continue only if the user confirms the PC has Extended Security Updates or an edition that remains supported.

Before downloading, explain the language and hardware split: a fresh system with usable NVIDIA CUDA selects multilingual Parakeet (~2.5 GB), while a fresh system without usable CUDA selects English-only Whisper base.en on CPU (~141 MiB). Published Windows 0.1.12 starts the selected model download on first launch. Upcoming 0.1.13 asks before downloading missing first-run default model files on either path; Setup offers deferral on both and the smaller CPU model on the Parakeet path. Other local models remain selectable in Settings, but the multilingual alternatives are intended for a supported NVIDIA GPU. If the user needs a language other than English and does not have usable NVIDIA CUDA, show them https://rcourtman.github.io/presspeech/windows.html#language-support and ask whether they still want to continue.

Download the installer and its checksum from the same official release, then verify both the checksum-file shape and the installer hash:
  $ErrorActionPreference = 'Stop'
  $metadataUrl = 'https://rcourtman.github.io/presspeech/site-metadata.json'
  $metadata = Invoke-RestMethod -Uri $metadataUrl
  $version = [string]$metadata.windows_version
  if ($version -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') {
    throw 'The published Windows version is not valid. Do not download anything.'
  }
  $base = "https://github.com/rcourtman/presspeech/releases/download/windows-v$version"
  $folder = Join-Path ([IO.Path]::GetTempPath()) "Presspeech-$version"
  New-Item -ItemType Directory -Force -Path $folder | Out-Null
  $installer = Join-Path $folder "Presspeech-Setup-$version-x64.exe"
  Invoke-WebRequest "$base/Presspeech-Setup-$version-x64.exe" -OutFile $installer
  Invoke-WebRequest "$base/Presspeech-Setup-$version-x64.exe.sha256" -OutFile "$installer.sha256"
  $parts = (Get-Content -LiteralPath "$installer.sha256" -Raw).Trim() -split '\s+'
  if ($parts.Count -ne 2 -or
      $parts[0] -notmatch '^[0-9a-fA-F]{64}$' -or
      $parts[1] -ne (Split-Path $installer -Leaf)) {
    throw 'The published checksum file is not valid. Do not run the installer.'
  }
  $actual = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash
  if ($actual -ne $parts[0]) {
    throw 'SHA-256 verification failed. Do not run the installer.'
  }
  "SHA-256 verified: $actual"

If GitHub CLI is already installed and the user is already signed in, offer an optional provenance check. Run it only with the user's approval; do not inspect or display credentials, install GitHub CLI, or sign in for this check:
  $tag = "windows-v$version"
  gh release verify $tag --repo rcourtman/presspeech
  if ($LASTEXITCODE -ne 0) {
    throw 'Release attestation verification failed; do not run the installer.'
  }
  gh release verify-asset $tag $installer --repo rcourtman/presspeech
  if ($LASTEXITCODE -ne 0) {
    throw 'Installer attestation verification failed; do not run the installer.'
  }
This checks the immutable release and the installer's signed GitHub release attestation. It is stronger provenance evidence than the checksum served beside the installer, but it does not code-sign the installer or prove the program is safe. If the user agrees to the check and either command fails, stop; do not run the installer. If the user declines or gh is unavailable, say clearly that provenance was not verified; do not claim otherwise. Let the user decide whether the repository source and matching checksum are enough, and never launch without explicit confirmation.

Once the checksum succeeds and any requested attestation check also succeeds—or the user explicitly chooses checksum-only trust—show the user the installer path and verified hash. Ask for explicit confirmation before launching it with:
  Start-Process -FilePath $installer

Do not automate a security-warning choice. If Microsoft Defender SmartScreen offers More info → Run anyway, the user must decide whether to proceed after checking the source and hash. If Windows 11 Smart App Control or managed policy blocks the unsigned installer without an override, stop; do not try to circumvent that policy.

After the user completes the installer, launch Presspeech from the Start Menu only if they chose not to wait and explicitly confirmed launching 0.1.12. If they chose to wait, leave the app unopened and make sure the installer's final "Launch Presspeech" option was unchecked. Explain that first launch may download a local model (about 141 MiB on a fresh CPU-only PC or about 2.5 GB with usable NVIDIA CUDA; an incomplete cache may need less). With 0.1.13, Setup asks before downloading either missing first-run default model and offers Set Up Later; the Parakeet path also offers the smaller CPU model. With published 0.1.12, the model download starts automatically on first launch, so make sure the user understands the size before launching. Published 0.1.12 also checks the microphone automatically; upcoming 0.1.13 leaves it closed until the user chooses Check Microphone. Let them decide whether to run that test in versions that offer the button, then finish Setup before testing the configured hotkey. Right Alt is the default; choose F8 or another available key if Right Alt acts as AltGr. Use Try Dictation for the first private test. Focus on setup and the first private test; do not ask the user to star, review, or otherwise endorse the project.
```
