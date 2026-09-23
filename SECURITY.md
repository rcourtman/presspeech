# Security

Presspeech is a local-only dictation tool. Presspeech does not send audio or
transcripts to a network service. The published Windows 0.1.12 prerelease does
not disable the bundled Hugging Face libraries' default usage telemetry during
model downloads; those dependency-generated events are distinct from audio or
transcript upload. It also leaves default implicit authentication enabled, so
an available Hugging Face token from `HF_TOKEN`, `HUGGING_FACE_HUB_TOKEN`, or
the local token cache may accompany a public model request; those models do not
require an account token. Upcoming Windows 0.1.13 adds runtime-verified
telemetry and authentication opt-outs.
Finished text does enter the
shared system clipboard for paste and recovery. macOS 0.3.8 can make
those entries available through Universal Clipboard; builds containing the
0.3.9 clipboard protection use AppKit's current-device-only option for every
Presspeech-created transcript entry. In those builds, a restored previous
clipboard is likewise republished for the current device only because its
original cross-device scope cannot be recovered from the snapshot. macOS
Clipboard History and arbitrary local clipboard readers remain separate
boundaries. Builds with that protection also add the standard transient,
auto-generated, and concealed markers, asking cooperating clipboard managers
not to archive or visibly expose transcript entries; these advisory markers do
not constrain arbitrary local readers.

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

For source builds containing the SHA-256 dependency lock, the Windows packaging
environment is also fixed before PyInstaller runs. Its PyPI dependency graph is exact-version locked to CPython 3.12 on Windows x64,
and every selected wheel has a reviewed SHA-256 in
`windows/requirements-release.txt`. The release workflow disables dependency
resolution and source distributions while installing that lock, names the
canonical PyPI index explicitly, ignores pip option environment variables, and
disables all pip configuration files. CI verifies the same complete runtime,
including the separate CUDA pin; optional dependencies are not covered by
`pip check` alone. These build controls do not retroactively qualify older
published installers. CUDA Torch is kept on its dedicated PyTorch index at an
exact version and is installed with dependencies disabled so that index cannot
substitute another transitive package.

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
- FluidAudio also reads `HF_TOKEN`, `HUGGING_FACE_HUB_TOKEN`, and
  `HUGGINGFACEHUB_API_TOKEN` and would attach an inherited credential to
  public model requests. Presspeech has no authenticated model path. The
  macOS 0.3.9 candidate removes those variables from its own process before
  Foundation or FluidAudio can capture the launch environment; this does not
  change the user's shell, token store, or other processes. It logs variable
  names only, never credential values, and fails closed if a variable cannot
  be removed.

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
logging, disables the hf-xet transfer client and implicit authentication,
removes inherited User-Agent origin data, and replaces Transformers' random
per-process request ID with the fixed non-unique value `telemetry-off`. Model
calls explicitly decline account tokens and remote model code, and Transformers
backends require safetensors weights. The packaged-app self-test verifies the
policy values cached by the actual bundled libraries so an incompatible
dependency change fails the release build. Windows currently trusts the
pinned Hub snapshot and HTTPS storage path rather than independently hashing
every model file.

The pinned hf-xet 1.6.0 source has no verified telemetry opt-out, so Windows
0.1.13 instead sets `HF_HUB_DISABLE_XET=1` before Hub imports. The packaged-app
self-test checks the bundled Hub's cached setting and effective Xet availability.
Hub then uses regular HTTP downloads rather than the hf-xet transfer client. This prevents hf-xet's
separate transfer-performance telemetry, but model requests remain visible to
Hugging Face. A changed Hub download path still requires review.

Upcoming Windows 0.1.13 resolves an explicit inference-file set for each pinned
revision, tries local-only resolution first, and permits one anonymous download
only when that snapshot or a required file is missing. Explicit offline settings
are preserved. Present malformed input and backend parsing failures do not
initiate download retries. Model construction uses local paths with local-only,
no-token and existing remote-code/safetensors restrictions. Whisper's tokenizer
and configuration are copied into a private model-lifetime directory so cache
path deletion cannot select its upstream unpinned tokenizer fallback. Weight hard
links isolate path deletion, not in-place modification; Windows still does not
independently hash every model file.
