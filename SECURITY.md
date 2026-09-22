# Security

Presspeech is a local-only dictation tool. It does not transmit audio,
transcripts, or telemetry to any network service.

## Reporting a vulnerability

If you discover a security issue (e.g. a way the app could be coerced
into leaking transcripts, escalate privileges, or be hijacked into
performing unwanted clipboard / paste actions), please **don't open a
public issue**.

Use GitHub's private
[Report a vulnerability](https://github.com/rcourtman/presspeech/security/advisories/new)
form instead. Include the affected version and platform, the impact, and
the smallest reproduction you can provide without exposing real transcript
content. Reports stay private while the issue is investigated and a fix is
prepared.

## Trust model for app releases

The macOS archive is signed with Developer ID, notarised by Apple, and
published together with `Presspeech.zip.sha256` in an immutable GitHub
release. The release script refuses to publish unless release immutability is
enabled, uploads both files through GitHub CLI's draft-first flow, and verifies
the resulting release attestation, asset digests, and protected tag before it
updates the Homebrew Cask. Direct-download users can run
`shasum -a 256 -c Presspeech.zip.sha256`; Homebrew independently enforces the
same archive hash from the tap.

The Windows release workflow builds only an approved main commit with green
macOS and Windows CI, publishes the installer and checksum together, requires
the resulting release to be immutable, and verifies GitHub's release
attestation against both local assets. The Windows updater considers only
releases that GitHub marks immutable, then checks the published metadata,
checksum file, and installer again before launch. The Windows build remains
unsigned until a code-signing certificate is configured; release attestation
proves which immutable Presspeech release supplied the bytes, but it is not a
substitute for Authenticode publisher identity.

## What's in scope

- Anything that lets a non-Presspeech process read transcripts in flight,
  or trigger Presspeech paste actions.
- Privilege-escalation paths through the app bundle's launcher.
- TCC bypasses or impersonation that misuse Presspeech's granted
  permissions.

## What's out of scope

- Issues that require already having local user privileges (e.g. an
  attacker who can already read `~/Library/Logs/Presspeech.log` doesn't
  need a vulnerability — they're already on the box).
- Vulnerabilities in upstream dependencies (please report those to
  the upstream project).
- Anything that requires the user to ship a custom build with
  transcript logging deliberately enabled — Presspeech as shipped never
  writes transcript content to disk.

## Trust model for the speech model

Presspeech's transcription is local, but the speech-recognition weights
themselves are downloaded once on first launch. That download is
handled by the upstream [FluidAudio](https://github.com/FluidInference/FluidAudio)
library, which fetches the CoreML conversion from
[`FluidInference/parakeet-tdt-0.6b-v3-coreml` on Hugging Face](https://huggingface.co/FluidInference/parakeet-tdt-0.6b-v3-coreml).
That model is derived from NVIDIA's
[`nvidia/parakeet-tdt-0.6b-v3`](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3).
The download uses HTTPS.

What that means for trust:

- The download is HTTPS, with standard macOS TLS certificate
  validation. A passive network attacker cannot tamper with the
  payload.
- FluidAudio does not verify a cryptographic checksum itself, so
  Presspeech adds its own manifest check around the v3 CoreML files it
  loads. Startup downloads the model through FluidAudio, verifies the
  downloaded model bundle and vocabulary against SHA-256 hashes pinned
  in `swift/Sources/Presspeech/main.swift`, and only then asks FluidAudio
  to compile/load the models. The manifest is tied to a specific
  `FluidInference/parakeet-tdt-0.6b-v3-coreml` repository commit; a
  legitimate upstream model change must ship as an explicit Presspeech
  update with refreshed hashes from `scripts/update-model-manifest.py`.
- FluidAudio reads `REGISTRY_URL` and `MODEL_REGISTRY_URL` from the
  process environment to override the download base URL. Presspeech
  refuses to launch if either is set — they are a persistence vector
  on macOS (e.g. via a `~/Library/LaunchAgents/*.plist`
  `EnvironmentVariables` block) and Presspeech does not document them as
  a feature. If you see Presspeech refuse with this error, audit your
  LaunchAgents, shell rc files, and any parent process for an
  injected value before relaunching.

If model integrity is a hard requirement for your environment, keep
Presspeech updated so the pinned manifest stays aligned with the
maintainer-vetted upstream model commit. Pre-populating
`~/Library/Application Support/FluidAudio/Models/` from a trusted
machine is still supported; Presspeech verifies that cache before loading
it.

On Windows, each selectable model is likewise pinned to a full Hugging Face
repository commit. Upcoming Windows 0.1.13 applies its network policy before importing
Transformers, faster-whisper, or huggingface_hub: it fixes the public
`https://huggingface.co` endpoint, disables Hub telemetry and request debug
logging, disables implicit authentication,
removes inherited User-Agent origin data, and replaces Transformers' random
per-process request ID with the fixed non-unique value `telemetry-off`. Model
calls explicitly decline account tokens and remote model code, and Transformers
backends require safetensors weights. The packaged-app self-test verifies the
policy values cached by the actual bundled libraries so an incompatible
dependency change fails the release build. Windows currently trusts the
pinned Hub snapshot and HTTPS storage path rather than independently hashing
every model file.

The process also sets `HF_XET_TELEMETRY_ENABLED=0` for forward compatibility.
The pinned hf-xet 1.6.0 source does not expose this switch; setting it and
importing the package does not prove a runtime telemetry opt-out. The package
check verifies the Hub constants and Transformers request header behavior;
future hf-xet releases still require an independent network-policy review.
