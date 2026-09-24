# Sharing kit

The distribution stance for Presspeech: **one Show HN post, written as
engineering notes, posted once — then answer-driven replies only.**
No recurring promotion, no broadcast posts. The information "a good
free tool exists" should travel in postures that aren't marketing:
a note where engineers look up notes, and answers where people are
already asking.

**Hold as of 24 September 2026: the Show HN launch draft below is not ready to
post.** The [published releases](https://github.com/rcourtman/presspeech/releases)
are macOS 0.3.8 and Windows 0.1.12, not the proposed 0.3.9 / 0.1.13 fixes.
Before launching macOS 0.3.8, users need its [inherited-token
warning](../README.md#install-on-macos); before launching Windows 0.1.12,
they need its [telemetry, token, routing, and proxy
warning](../README.md#install-on-windows). Do not collapse these into a
generic "100% local" claim. Also, [new GitHub issues are currently
restricted](https://github.com/rcourtman/presspeech/issues), so a new
compatibility report cannot be promised as an available feedback route.
Finally, the roadmap's native and target-app text-delivery qualification is
incomplete. These are separate gates: a suitable published build with accurate
privacy disclosures, an open reporting route, and completed native checks
must each be verified before a launch post. Do not use unreleased source
behavior as evidence for a published download.

Until then, use this document only as internal draft material. If someone
already using Presspeech asks how to help, point to the privacy-safe
[target-app protocol](../docs/app-compatibility.md) and current
[support-route status](../SUPPORT.md), without implying that a saved worksheet
draft is submitted or monitored. Community results supplement, not replace,
native release checks.

**Observed 24 September 2026:** the published [macOS 0.3.8 release
notes](https://github.com/rcourtman/presspeech/releases/tag/v0.3.8) invite
compatibility reports but do not mention that new issues are restricted or
surface the inherited-token model-download warning. The published [Windows
0.1.12 release notes](https://github.com/rcourtman/presspeech/releases/tag/windows-v0.1.12)
likewise omit that build's model-download telemetry, token, and routing caveat.
The README and install guides carry the version-specific decisions, but a
visitor can download from a release page without reading them. Before linking
directly to either release page, the repository owner should correct its
published notes; until then, send users through the exact-version install
warning instead. Editing tracked release-notes files or this kit alone does
not change GitHub's published notes; see the [public release
qualification](../docs/manual-qa.md#public-release-and-support-qualification).

Recheck release-size and benchmark numbers against `docs/site-metadata.json`
and the benchmarks page. Run `python3 scripts/sync-docs.py --check` and
`python3 scripts/check-public-assets.py` before any future posting; these
checks do not establish native delivery or privacy safety.

## Assets

| Asset | Where | Use |
|---|---|---|
| Demo video (MP4) | `marketing/demo/dist/presspeech-demo.mp4` | most platforms |
| Demo video (WebM) | `marketing/demo/dist/presspeech-demo.webm` | web `<video>` embeds |
| Demo GIF | `marketing/demo/dist/presspeech-demo.gif` | platforms without video upload |
| Animated workflow SVG | `icon/demo.svg` (embedded in README + site) | GitHub-native surfaces |
| Social card (1280×640) | `icon/social-preview.png` | link previews |

Links:

- Repo: <https://github.com/rcourtman/presspeech>
- Site: <https://rcourtman.github.io/presspeech/>
- Benchmarks: <https://rcourtman.github.io/presspeech/benchmarks.html>
- Download: <https://github.com/rcourtman/presspeech/releases/latest/download/Presspeech.zip>
- Homebrew: `brew install --cask rcourtman/presspeech/presspeech`

GitHub's repository profile is a manual distribution surface and must match the
two-platform README. **Observed 24 September 2026:** the public
[repository profile](https://github.com/rcourtman/presspeech)
still describes only Apple Silicon Macs and says "no cloud or telemetry". That
does not describe the Windows prerelease or its version-specific model-download
telemetry caveat. Updating the profile requires a separate manual GitHub change;
editing this file does not change it.

Use this About description:

> Private local push-to-talk dictation for Apple Silicon Macs, plus an x64
> Windows preview — no account, subscription, or cloud transcription; bundled-library network behavior is version-specific.

Keep the existing topics and include `windows`, `offline`, and `on-device-ai`;
otherwise GitHub search presents Presspeech as a Mac-only project even while a
Windows build is available.

### Third-party showcase wording to correct

**Observed 24 September 2026:** the [FluidAudio README showcase](https://github.com/FluidInference/FluidAudio/blob/main/README.md)
describes Presspeech as pasting at the cursor "in about 100 ms." This is not
the measured claim: the [Presspeech benchmark](../docs/benchmarks.html) reports
92–152 ms p50 for **warm model inference** on four synthetic clips on an M4;
microphone capture and clipboard/paste delivery are excluded. macOS 0.3.8
also requires the original focused window to be verifiable before automatic
paste, otherwise it leaves the transcript for manual clipboard recovery.
This is an upstream page, not a repository-controlled asset. Do not cite it
as evidence for release-to-paste timing or assume editing this file will change it.
For a maintainer-requested upstream correction, prefer version-independent
copy without a paste-time promise:

> Open-source macOS push-to-talk dictation using FluidAudio's Parakeet TDT v3
> locally on Apple Silicon. Automatic paste requires verification of the
> original window; otherwise the transcript is available for manual paste.
> Published benchmarks time model inference, not end-to-end insertion.

## Claims and where they're backed

- **~100 ms model transcription on the documented clips** — benchmarks page,
  methodology included; clipboard and paste work are not part of that timing
- **8.4 MB signed, notarised download** — release asset size; about 500-600 MB for the local speech model
- **~80 MB RAM while idle, 0% CPU between dictations** — site stats
- **Recognition runs locally** — no cloud transcription or Presspeech account;
  model downloads and optional update checks are separate network paths, with
  version-specific dependency requests and token/telemetry caveats in the
  [privacy inventory](../docs/privacy.html#network-calls). Do not use "100%
  local" to describe the whole app or its installation.
- **Free, MIT, native Swift menu-bar app**
- State the requirements up front (Apple Silicon, macOS 14+; Homebrew optional for updates) — it costs a sentence and buys trust.

## Show HN (unapproved draft; post once only after the gates above)

> **Show HN: Presspeech – an 8.4 MB local dictation app for Apple Silicon**

Presspeech is a macOS menu-bar app: hold Right Option, speak, release,
and the transcript normally pastes after Presspeech confirms that the same
window is still focused. If it cannot verify that destination, it keeps the
transcript on the clipboard for manual paste instead. On the documented M4
benchmark clips, the local model call takes about 100 ms.
There is also a separate unsigned Windows prerelease; the footprint and
latency numbers below describe the released Mac app only.

The current macOS release strengthens microphone recovery, binds delivery to
the exact window where recording began, replaces timer-based clipboard
restoration with an explicit manual restore, and adds configurable hotkey
combinations. Target-app qualification is still in progress, so safe manual
recovery is part of the product contract rather than a universal-paste claim.

I built it because I wanted dictation that feels like a keyboard
shortcut rather than a mode you enter and leave. It is free and MIT
licensed, with no account, subscription, or cloud transcription.

Before launching macOS 0.3.8, note that its model download may include a
Hugging Face token inherited by Presspeech even though the public model needs
no account. If a token may be present in the app's launch environment, or you
are unsure, wait for 0.3.9 to be published. Windows 0.1.12 separately may
send Hugging Face usage telemetry and a configured or saved token during model
downloads; custom routing or a trusted TLS-inspecting proxy can affect who
receives it. The [version-specific privacy
guide](https://rcourtman.github.io/presspeech/privacy.html#network-calls)
explains both published builds. Dictation audio and transcripts are not sent
with those model requests.

How it works: audio is captured in memory and decoded once on key
release with the local Parakeet TDT v3 CoreML model through FluidAudio on the
Apple Neural Engine, then pasted after confirming the same window is still
focused or kept on the clipboard for manual paste when that destination cannot
be verified. The published latency benchmark times the model call only; it
does not measure clipboard or paste work.
Benchmarks and methodology:
https://rcourtman.github.io/presspeech/benchmarks.html

Numbers: ~100 ms model transcription on the documented clips; 8.4 MB notarised app plus about
500-600 MB for the local speech model; ~80 MB RAM idle; 0% CPU between
dictations. Transcription makes no network calls; model downloads, optional
update checks, and dependency-generated requests are documented separately
in the version-specific privacy guide above.

The app also includes deterministic voice shortcuts (a spoken phrase maps to
exact reusable text), opt-in spoken formatting commands such as “new
paragraph” and “bullet point”, and a focused Try Dictation scratchpad for
first-run setup. None of these features uses a rewriting model.

Limitations: Apple Silicon and macOS 14+ only; 25 European languages with
selectable language hints for 18; no streaming mode.

MIT licensed. Download:
https://github.com/rcourtman/presspeech/releases/latest/download/Presspeech.zip

Or install with Homebrew:
`brew install --cask rcourtman/presspeech/presspeech`

## Reddit / r/macapps (no standalone launch draft)

**Route check, 24 September 2026:** the moderators' [September App Pile
megathread](https://www.reddit.com/r/macapps/comments/1w4brkd/megathread_the_app_pile_september_2026/)
explicitly includes dictation apps among promotions to keep in
that megathread, and requires a concise problem, named-competitor comparison,
and price with a link. Their [Trust or Transparency
guidance](https://www.reddit.com/r/macapps/comments/1ryaeex/comment/obd1kbk/)
describes exceptions for established developers or a qualifying public
identity and policy site, as well as local-karma and promotion-frequency
conditions. Presspeech's [public repository
profile](https://github.com/rcourtman/presspeech) shows 33 stars at this
check, below the guidance's 100-star project signal; account history, flair,
and any other eligibility have not been verified. Do not treat an open-source
license or notarization as automatic main-feed eligibility.

Retire the standalone post copy rather than adapting it for a different route
while the release, reporting, and native-qualification holds above remain.
The distribution stance here is still one qualified Show HN post, followed by
answer-driven replies only. If that stance is ever reconsidered, recheck the
current subreddit rules and the posting account's actual eligibility before
drafting anything; do not use comments in another developer's post to evade
promotion limits. No Reddit submission is approved by this kit.

### Show HN posting notes

Post from a personal account (pseudonymous is
fine; being the author is required for Show HN). Weekday mornings US
Eastern get the most eyes. Stay in the thread for the first few
hours and answer technical questions plainly. Expectation-setting:
most Show HNs get a handful of points and sink — that's a fine
outcome; the note persists, gets indexed, and keeps answering
searches for years.

## Launch checklist

Do this once, then stop and measure instead of tweaking copy in a loop:

1. Review open issues for a repeatable failure in the headline hold, speak,
   release, paste workflow. Do not spend the one-time launch post while such a
   regression is awaiting a fix or native platform QA; resolve it first rather
   than weakening or qualifying the promise only in promotional copy.
2. Complete the [target-app compatibility protocol](../docs/app-compatibility.md)
   against representative native, browser, and Electron/Chromium targets on
   the release candidate. Do not spend the launch post while any incorrect or
   unsafe result remains, and describe recurring safe recovery rather than
   turning it into a universal paste claim.
3. Recheck the published macOS and Windows tags and their [version-specific
   model-download privacy guidance](../docs/privacy.html#network-calls). Do
   not present the 0.3.9 / 0.1.13 source controls as shipped while 0.3.8 /
   0.1.12 are the downloads. Resolve or carry the exact published-version
   warnings before inviting a new user to install or launch; this draft is
   held while the current issues remain.
4. Complete the [public release and support
   qualification](../docs/manual-qa.md#public-release-and-support-qualification).
   Confirm `main` is deployed to GitHub Pages and the README shows the direct
   download above Homebrew. From a signed-in non-collaborator account, confirm
   all three public issue templates can actually be submitted before asking
   users for bug, improvement, or compatibility reports. The visible
   "Issue creation is restricted" banner is a stop, not an invitation to
   redirect private reports elsewhere. Apply the exact two-platform GitHub
   About description and missing discovery topics above; a Mac-only profile
   hides a shipped platform. Request correction of the third-party FluidAudio
   showcase wording above before relying on it as a discovery surface; neither
   repository source edits nor launch copy alter that upstream description.
5. Record a baseline: latest release downloads, total release
   downloads, repo stars, repo views, unique views, clones, and top
   referrers.
6. Rework and approve the draft against the release actually available that
   day; do not post the dated text above unchanged. Then post Show HN once,
   linking the repo or site depending on which preview looks cleaner.
7. Stay available for the first few hours and answer only actual
   questions. Link the compare table for "why not X?" and the privacy
   page for trust questions.
8. After 24 hours and 7 days, record the same metrics. Judge the post
   by qualified installs and questions, not points alone.

If the direct download link materially outperforms the Homebrew command
in release downloads, keep direct download first. If questions cluster
around permissions, model download, or Gatekeeper wording, fix that copy
once in README + install page + FAQ and rerun `scripts/sync-docs.py
--check`.

## Answer material (ongoing, demand-driven)

For threads asking "is there a local dictation app for Mac?" or
similar. Reply, disclose, stop. These are prompts, not safe standalone copy:
check the published tag and [current privacy
guidance](../docs/privacy.html#network-calls) first, include the relevant
release-specific warning before any install/launch suggestion, and never ask
for a public report while issue creation is restricted. Do not imply that
local-only dictation means network-free model setup or that a downloaded
worksheet is a submission.

General:

> I maintain a free MIT-licensed one: Presspeech
> (https://github.com/rcourtman/presspeech). It has separate local apps for
> Apple Silicon Macs and x64 Windows PCs: hold a key, speak, release, and it
> normally pastes into the window where recording began, with a manual
> clipboard fallback when that destination cannot be verified. The published
> Mac benchmark measures about 100 ms for the local model call; the Windows
> build is still a preview. Published-version model-download caveats are in
> https://rcourtman.github.io/presspeech/privacy.html#network-calls.

Local-AI angle (r/LocalLLaMA and similar):

> If you want local ASR as a daily input method: Presspeech runs Parakeet
> TDT v3 on the ANE via CoreML — no Presspeech API key required, about 100 ms
> model transcription on the documented clips. Benchmarks + methodology:
> https://rcourtman.github.io/presspeech/benchmarks.html. I'm the
> maintainer; MIT licensed. The published model-download privacy caveats are
> version-specific: https://rcourtman.github.io/presspeech/privacy.html#network-calls.

Privacy angle:

> Presspeech transcribes on-device, but model downloads, optional update
> checks, and bundled-library requests have version-specific behavior.
> macOS 0.3.8 may inherit a Hugging Face token; Windows 0.1.12 may send a
> configured or saved token and usage telemetry in model requests. Read the
> current version-specific inventory before launching:
> https://rcourtman.github.io/presspeech/privacy.html#network-calls.
> I maintain it; it's free and MIT.

Current-user qualification (only if asked; do not solicit while issue intake
is restricted):

> If you already use Presspeech on macOS and want to help qualify a target app,
> the privacy-safe protocol uses five steady-focus and three focus-change
> attempts in blank disposable fields. Keep only aggregate counts, never the
> phrases or transcripts:
> https://rcourtman.github.io/presspeech/app-compatibility.html. GitHub
> currently restricts new issues; check for a matching thread that accepts
> comments, or keep the worksheet draft locally until reporting reopens. It
> is not submitted or monitored. Passing results matter too, but community
> results do not replace native release testing.

Comparison ("how is this different from Superwhisper / Wispr Flow /
VoiceInk / FluidVoice?"):

> Mostly scope. Presspeech only does push-to-talk dictation — verbatim,
> on-device, free — where those are fuller workspaces with AI
> formatting and more. Side-by-side facts (price, where audio is
> processed, measured latency, footprint) are here, with every
> competitor claim sourced and dated:
> https://rcourtman.github.io/presspeech/compare/. I maintain Presspeech,
> so read it with that in mind.

Open-source comparison ("why not Handy?"):

> Handy is the stronger choice if you want Linux or Intel Mac support,
> one cross-platform application, model switching, or its much larger
> open-source community. Presspeech is narrower: a small native macOS app
> with a published model-transcription benchmark, plus a separate Windows
> preview. Both are free, MIT licensed, and local. Current factual comparison:
> https://rcourtman.github.io/presspeech/compare/handy.html. I maintain
> Presspeech, so read it with that in mind.

Local Mac comparison ("why not FluidVoice?"):

> FluidVoice is the stronger choice if you want live preview, model switching,
> Intel Mac support, optional audio history, or local AI rewriting. Presspeech
> is narrower: one fixed model path, deterministic text handling, explicit
> paste-destination recovery, and no Presspeech-authored analytics or cloud-AI path. Both are free,
> local-first, and open source. Current factual comparison:
> https://rcourtman.github.io/presspeech/compare/fluidvoice.html. I maintain
> Presspeech, so read it with that in mind.

Windows comparison:

> Windows has two built-in choices that are easy to confuse: Windows documents
> Voice Typing with online speech recognition, while its privacy statement says
> Windows 11 Voice Typing may use both device-based and online recognition.
> Copilot+ PCs also offer on-device Fluid dictation corrections, which alone
> doesn't establish that the whole recognition path is offline. Voice Access
> can dictate offline as part of a full voice-control workflow. If you specifically
> want local push-to-talk, Presspeech and Handy are free open-source options
> with different model and packaging tradeoffs. I maintain Presspeech; the
> sourced comparison is here:
> https://rcourtman.github.io/presspeech/compare/windows-dictation.html

Custom vocabulary / proper names:

> Presspeech's released Dictionary & Shortcuts feature applies deterministic
> replacements after transcription; it does not currently teach or bias the
> speech model, so related inflections need separate rules. Corrected Polish
> experiments found that the tested lemma-only vocabulary did not generalise
> to held-out inflected forms; supplying the tested forms explicitly improved
> aggregate recall but also regressed individual clips, introduced unexpected
> terms, and took about 2.6 times the inference latency. That decoder path
> remains disabled.
> The evidence and privacy-safe benchmark method are linked from the benchmarks
> page. I maintain Presspeech.

Local Mac workspace comparison ("why not MacParakeet?"):

> MacParakeet is the stronger choice if you want file and meeting
> transcription, broad model choice, persistent history, optional AI features,
> or command-line automation in the same free Mac app. Presspeech is narrower:
> dictation only, no persistent transcript archive or Presspeech-authored analytics, deterministic
> text handling, and explicit manual recovery when it cannot verify the
> original paste window. Both run their core speech recognition locally. Current
> sourced comparison: https://rcourtman.github.io/presspeech/compare/macparakeet.html.
> I maintain Presspeech, so read it with that in mind.

## One-liner boilerplate

For directories and "what is this" replies:

> Presspeech is free, MIT-licensed local push-to-talk dictation with separate
> apps for Apple Silicon Macs and x64 Windows PCs: hold a key, speak, release,
> and the transcript normally returns to the window where recording began,
> with a manual clipboard fallback when that destination cannot be verified.
> There is no cloud transcription or Presspeech-authored analytics; bundled-
> library network behavior is version-specific (see
> https://rcourtman.github.io/presspeech/privacy.html#network-calls). The
> published Mac benchmark measures about 100 ms for the local model call; it
> does not include clipboard or paste work.

## House rules

- One account, your own (pseudonymous is fine). Always disclose
  "I maintain it" / "I built this".
- Answer questions that were actually asked; don't seed them.
- For r/macapps specifically, recheck its current moderator rules before any
  reply that promotes Presspeech: the September 2026 guidance requires local
  community karma, disclosure, and respect for its 30-day promotion limit;
  do not assume an answer-driven reply is exempt.
- The compare section answers "why not Superwhisper / Wispr Flow /
  VoiceInk / Handy / FluidVoice / MacParakeet / Apple Dictation / MacWhisper" and separates
  the Windows built-ins from local and cloud apps — link the relevant table or
  per-tool page instead of arguing in threads.
- Expect "Intel support?" and "why Homebrew?" — Apple Silicon is an
  honest scope decision because the latency story depends on the ANE;
  Homebrew is optional but remains the easiest update path.
- No second launch post. If the Show HN sinks, let it sink.
