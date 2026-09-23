# Install Presspeech with a shell-capable assistant

Choose the prompt for the computer where Presspeech should be installed. The
Windows prompt deliberately stops rather than weakening operating-system policy
for the unsigned prerelease.

## macOS

```text
Install Presspeech from https://github.com/rcourtman/presspeech on this Mac.

Presspeech has two notarised install paths: a direct release zip and a Homebrew Cask. Use Homebrew when it is already installed because it also handles updates. If Homebrew is missing, offer the direct download instead of forcing the user to install Homebrew first.

Run:
  uname -m
  sw_vers -productVersion
  brew install --cask rcourtman/presspeech/presspeech
  open /Applications/Presspeech.app

Direct download:
  https://github.com/rcourtman/presspeech/releases/latest/download/Presspeech.zip

Stop if the Mac is not Apple Silicon (arm64) or macOS is older than 14.

After launch, explain that macOS 0.3.8 starts its first local speech-model download (~500-600 MB) on launch. In 0.3.9, a clean install must choose Download Model in Setup; close Setup to defer. Existing installs and cached models continue loading automatically. Use Setup Checklist to finish the model, permissions, and hotkey readiness. The default dictation key is Right Option. Focus on setup and the first private test; do not ask the user to star, review, or otherwise endorse the project.
```

## Windows

```text
Install Presspeech from https://github.com/rcourtman/presspeech on this Windows PC.

Use only the published Windows prerelease selected by Presspeech's deployed metadata and version-pinned install guide:
  https://rcourtman.github.io/presspeech/windows.html#download-verify-run

The source branch can contain a newer unreleased candidate, so do not infer a download version from windows/config.py, release notes, or other files on main. The deployed metadata stays on a version whose installer and checksum are both public. The installer is not code-signed. Explain that before downloading; SHA-256 verification confirms that the file matches the asset published in this repository, but it is not a publisher signature.

Run these read-only checks in PowerShell:
  [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
  (Get-CimInstance Win32_OperatingSystem).Caption

Stop if the architecture is not X64. Windows 11 is recommended. If this is Windows 10, explain that general support has ended and continue only if the user confirms the PC has Extended Security Updates or an edition that remains supported.

Before downloading, explain the language and hardware split: a fresh system with usable NVIDIA CUDA selects multilingual Parakeet (~2.5 GB), while a fresh system without usable CUDA selects English-only Whisper base.en on CPU (~141 MiB). Published Windows 0.1.12 starts the selected model download on first launch. Upcoming 0.1.13 asks before downloading missing Parakeet files and offers the smaller CPU model or deferral. Other local models remain selectable in Settings, but the multilingual alternatives are intended for a supported NVIDIA GPU. If the user needs a language other than English and does not have usable NVIDIA CUDA, show them https://rcourtman.github.io/presspeech/windows.html#language-support and ask whether they still want to continue.

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

After verification succeeds, show the user the installer path and verified hash. Ask for explicit confirmation before launching it with:
  Start-Process -FilePath $installer

Do not automate a security-warning choice. If Microsoft Defender SmartScreen offers More info → Run anyway, the user must decide whether to proceed after checking the source and hash. If Windows 11 Smart App Control or managed policy blocks the unsigned installer without an override, stop; do not try to circumvent that policy.

After the user completes the installer, launch Presspeech from the Start Menu. Explain that first launch may download a local model (about 141 MiB on a fresh CPU-only PC or about 2.5 GB with usable NVIDIA CUDA; an incomplete Parakeet cache may need less). With 0.1.13, Setup asks before downloading missing Parakeet files and offers the smaller CPU model or deferral; with published 0.1.12, the model download starts automatically on first launch, so make sure the user understands the size before launching. Wait for model preparation, check the microphone, and finish Setup before testing the configured hotkey. Right Alt is the default; choose F8 or another available key if Right Alt acts as AltGr. Use Try Dictation for the first private test. Focus on setup and the first private test; do not ask the user to star, review, or otherwise endorse the project.
```
