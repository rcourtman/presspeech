# Published release-entry corrections (internal draft)

Checked 24 September 2026. **Not published or approved for posting.** The
[macOS 0.3.8 release](https://github.com/rcourtman/presspeech/releases/tag/v0.3.8)
and [Windows 0.1.12 release](https://github.com/rcourtman/presspeech/releases/tag/windows-v0.1.12)
are standalone download pages. Neither currently gives the model-download
privacy decision present in the [version-specific inventory](../docs/privacy/network-calls.json).
The macOS page also omits the malformed-proxy warning: the [pinned FluidAudio
client](https://github.com/FluidInference/FluidAudio/blob/4dbf4f9f9a5ff3a53ade848d7ba4e3df13db859b/Sources/FluidAudio/ModelRegistry.swift)
logs the invalid URL string and ignores that proxy setting. Its notes invite
compatibility reports while the [issue index](https://github.com/rcourtman/presspeech/issues)
says new issue creation is restricted. The existing install guides warn users,
but a release-page visitor need not pass through those guides.

These are **proposed additions to the existing GitHub release notes**, not
replacements for the release history. Recheck the live release tags, inventory,
and issue-intake status immediately before any authorized edit. An authorized
release maintainer must correct the live notes; editing a tracked notes file or
this draft does not do so. Do not edit the published Windows notes file alone:
the Windows release-state validator compares it with the live release body.
After a live correction, reconcile the tracked notes and verify the rendered
page from a signed-out browser. Do not imply the upcoming 0.3.9 or 0.1.13
controls are present in these downloads.

The tracked `swift/release-notes/v0.3.8.md` and
`windows/release-notes/0.1.12.md` now include candidate first-use notices for
model requests, the macOS malformed-proxy behavior, clipboard services, and
(on Windows) the automatic microphone
check and sign-in startup default. They are **not** evidence of a public
correction. Review the complete tracked bodies alongside the live notes before
using them for an authorized release-page edit; do not overwrite release
history with only the excerpts below.

Run `python3 scripts/check-public-releases.py --notes-only` before and after
any authorized correction. It audits public/tracked-note parity and flags
missing first-use disclosure markers on both archived release pages and the absent
reporting handoff on macOS 0.3.8, without modifying GitHub. A passing marker
check is only a presence check; review the rendered warnings, current support
route, version-specific decisions, and links manually. The audit is expected
to fail while the current public entries remain uncorrected.

## macOS 0.3.8 — proposed lead notice

> **Before opening macOS 0.3.8:** A missing speech model starts downloading
> when Presspeech launches; downloading the ZIP from GitHub alone does not make
> that request. The model request may include a Hugging Face token inherited by
> Presspeech, although the public model needs no token. The bundled client can
> also use an inherited HTTPS proxy; a TLS-inspecting proxy trusted by macOS
> could read a token, whereas a tunnelling proxy cannot read the HTTPS request.
> If a token may be present in the app's launch environment, or you are unsure,
> leave the app unopened and wait until macOS 0.3.9 is published. If the trust
> of a TLS-inspecting proxy is unclear, do not launch 0.3.8 while it is in use.
> The bundled 0.3.8 client can also log a malformed inherited lowercase
> `https_proxy` or `http_proxy` URL verbatim, including embedded proxy
> credentials, then ignore it and make a model request without the proxy you
> expected. If you depend on an inherited proxy and are unsure its URL is
> valid, leave 0.3.8 unopened. Do not share proxy URLs or log excerpts
> containing them. Upcoming 0.3.9 refuses malformed proxy settings before
> model loading.
> Dictation audio and transcripts are not sent in model downloads. See the
> [version-specific privacy guide](https://rcourtman.github.io/presspeech/privacy.html#network-calls)
> and [guidance if you already used 0.3.8](https://rcourtman.github.io/presspeech/privacy.html#macos-0-3-8-after-use).
> Dictation still uses the system clipboard: this build may expose transcript
> entries through Universal Clipboard. Review the
> [clipboard-services boundary](https://rcourtman.github.io/presspeech/privacy.html#operating-system-clipboard-services)
> before sensitive dictation.

The existing macOS invitation to share compatibility results also needs this
qualification, if issue creation is still restricted when the owner edits it:

> **Compatibility reporting:** GitHub currently restricts new issues for this
> repository. The [compatibility worksheet](https://rcourtman.github.io/presspeech/app-compatibility.html)
> saves only a local, unmonitored draft. If you already tested, check whether a
> comparable existing issue accepts comments; otherwise keep the draft private
> until a suitable route reopens. Do not post dictated text or clipboard data.
> Check the [current support route](https://github.com/rcourtman/presspeech/blob/main/SUPPORT.md)
> before attempting to report.

## Windows 0.1.12 — proposed lead notice

> **Before launching Windows 0.1.12:** A missing model downloads when the app
> launches; downloading the installer and checksum from GitHub alone does not
> make that request. During model downloads this build may send Hugging Face
> usage telemetry and a configured or locally saved Hugging Face token. An
> inherited `HF_ENDPOINT` or staging setting can change the request destination.
> The bundled client also honors configured HTTPS proxies and CA settings; an
> untrusted TLS-inspecting proxy trusted by the client could read a token, while
> a tunnelling proxy cannot read the HTTPS request. If you prefer to avoid the
> possible telemetry, a token or custom route may be configured, or you are
> unsure, wait until Windows 0.1.13 is published. If you install 0.1.12 but
> choose to wait, clear **Launch Presspeech** on the final installer screen
> before choosing **Finish**. It starts checked by default; otherwise
> **Finish** opens 0.1.12 and can start the model request.
> Do not launch 0.1.12 while an untrusted TLS-inspecting proxy is in use.
> Published 0.1.12 also briefly opens the selected microphone for an
> automatic local readiness check in first-run Setup; its samples are
> discarded, not saved, sent, or transcribed. If you do not want that check,
> leave the app unopened. Upcoming 0.1.13 waits for an explicit **Check
> Microphone** choice. On a new 0.1.12 profile, **Start Presspeech with
> Windows** is selected by default; turn it off in Setup before finishing,
> choosing **Set Up Later**, or closing Setup if you do not want it at sign-in.
> Dictation audio and transcripts are not sent in model downloads. See the
> [version-specific privacy decision and after-use guidance](https://rcourtman.github.io/presspeech/windows.html#model-download-privacy).
> Dictation still uses the system clipboard: this build's transcript entries
> may enter Windows Clipboard History or Cloud Clipboard. Review the
> [clipboard-services boundary](https://rcourtman.github.io/presspeech/privacy.html#operating-system-clipboard-services)
> before sensitive dictation.
