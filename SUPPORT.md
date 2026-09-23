# Support

Setup and recovery guidance is available below. When GitHub permits it, user
support and issue discussion happen publicly there; you need a GitHub account
to open or comment on an issue. Before reporting, use the recovery guide that
matches the problem:

**Feedback availability checked 23 September 2026:** GitHub currently reports
that issue creation is restricted for this repository. The report forms linked
below may therefore be unavailable even though existing issues can be read.
Do not keep retrying a blocked form or include private data elsewhere as a
workaround. Check the [issue list](https://github.com/rcourtman/presspeech/issues)
for a matching discussion and whether GitHub permits commenting; if no usable
public route is available, keep the privacy-safe aggregate locally and retry
after issue creation is restored. Security reports must use the private route
in [SECURITY.md](SECURITY.md).

For a completed target-app compatibility check, the worksheet's **Download
report block** saves only its six aggregate counts and overall classification
as a plain-text file. It does not include test phrases or transcripts.

While issue creation is restricted, you can prepare an unsent private draft
instead of losing the details. Record only what will help reproduce or measure
the problem:

```text
Presspeech version / operating-system version:
Affected stage (setup, recording, recognition, delivery, or controls):
Expected result / observed result:
Smallest safe reproduction steps, or how often it occurs:
Generic context if relevant (target-app class/version, language, or hardware):
Recovery or workaround tried; did it succeed?
Attempts / failures, when countable:
```

For a text-delivery issue, use harmless test text and note whether focus changed,
whether Presspeech showed a recovery notice, and whether manual paste recovered
the complete test phrase; never save or share the phrase itself. Keep copied
diagnostics private until you review them, and do not attach raw logs, audio,
transcripts, or screenshots with user data. This draft is not sent or monitored;
submit it only if a suitable public route becomes available. For compatibility
checks, use the worksheet's counts-only download above.

- **First dictation or setup:** follow [Getting started](https://rcourtman.github.io/presspeech/getting-started.html).
- **macOS install or permissions:** check the [macOS install guide](https://rcourtman.github.io/presspeech/install.html), [FAQ](https://rcourtman.github.io/presspeech/faq.html), and [troubleshooting guide](https://rcourtman.github.io/presspeech/troubleshooting.html).
- **Windows install or model setup:** check the [Windows install guide](https://rcourtman.github.io/presspeech/windows.html) and [Windows technical guide](windows/README.md).

If the problem remains, search the [existing issues](https://github.com/rcourtman/presspeech/issues), then choose the closest route when GitHub permits it:

- [Report a bug](https://github.com/rcourtman/presspeech/issues/new?template=bug_report.yml). In Presspeech, choose **Copy Diagnostics** first and paste that privacy-safe summary into the form.
- [Browse existing target-app compatibility reports](https://github.com/rcourtman/presspeech/issues?q=is%3Aissue%20in%3Atitle%20%22%5BCompatibility%5D%22), then [share a report](https://github.com/rcourtman/presspeech/issues/new?template=compatibility_report.yml) after running the [short privacy-safe protocol](https://rcourtman.github.io/presspeech/app-compatibility.html). Passing reports are useful as well as failures because they show how often automatic paste and focus-change recovery worked across repeated attempts.
  If you do not have a particular app in mind, the protocol's live coverage
  links show native, browser, and Electron/Chromium reports separately so you
  can choose an unrepresented target class.
- [Suggest an improvement](https://github.com/rcourtman/presspeech/issues/new?template=feature_request.yml). Describe the recurring problem and a measurable result.
- [Ask a usage question](https://github.com/rcourtman/presspeech/issues/new/choose) by selecting **Open a blank issue**. Include the platform, Presspeech version, and what you already tried.

Issues are public. Never include dictated text, audio, dictionary or shortcut
contents, credentials, private paths, or other sensitive data. Diagnostics are
not sent automatically: opening a feedback form only opens a fixed GitHub URL,
and you decide what to paste and submit.

For a paste or clipboard problem, the bug form asks for the target app class,
focus behavior, clipboard-restore setting, manual-recovery result, and repeat
count. Reproduce with harmless test text, report only whether the complete text
was recovered, and omit the text itself plus document, tab, account, server,
and window names.

Use the compatibility form rather than the general bug form when you can run
the complete repeated protocol, including when every check passes. Use the bug
form for a problem you cannot safely repeat or that is outside text delivery.
Match an existing report only when the platform, target app version, and
generic field type are comparable. When they match, comment with the
worksheet's six counts and overall result, your Presspeech and operating-system
versions, generic hardware if useful, and relevant conditions; otherwise open
a separate report. Add the observation even when its outcome differs.

Before proposing a larger capability, read the [product roadmap](ROADMAP.md).
It describes current priorities, evidence gates, and workflows that are
deliberately outside Presspeech's scope.

Do not use a public issue for a security vulnerability. Follow the private
reporting process in [SECURITY.md](SECURITY.md) instead.
