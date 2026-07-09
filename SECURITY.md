# Security

Presspeech is a local-only dictation tool. It does not transmit audio,
transcripts, or telemetry to any network service.

## Reporting a vulnerability

If you discover a security issue (e.g. a way the app could be coerced
into leaking transcripts, escalate privileges, or be hijacked into
performing unwanted clipboard / paste actions), please **don't open a
public issue**.

Instead, email the maintainer with the details. The repository owner's
contact is listed on their GitHub profile.

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
