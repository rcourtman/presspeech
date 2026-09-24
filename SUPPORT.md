# Support

The app's **Report a Problem** and **Suggest an Improvement** actions open this
guide. They do not send diagnostics or submit a report; choose whether to use a
public route below, and keep any draft private when no route accepts it.

Setup and recovery guidance is available below. When GitHub permits it, user
support and issue discussion happen publicly there; you need a GitHub account
to open or comment on an issue. Before reporting, use the recovery guide that
matches the problem:

**Feedback availability checked 24 September 2026:** GitHub currently reports
that issue creation is restricted for this repository, and the repository does
not expose a public Discussions page. Existing issue threads may still accept
comments. Check the [issue list](https://github.com/rcourtman/presspeech/issues)
for a matching issue and whether GitHub permits commenting; if no usable thread
is available, keep the privacy-safe report draft locally and retry after issue
creation is restored. Do not keep retrying a blocked form or include private
data elsewhere as a workaround. Security reports must use the private route in
[SECURITY.md](SECURITY.md).

For a classified target-app compatibility check, including an early stop, the
worksheet's **Download report draft** saves its eight aggregate counts and
overall classification with blank prompts for public versions and generic
target context. The worksheet does not collect or prefill those details; the
draft contains no test phrases
or transcripts. The downloaded file also explains that it is not submitted or
monitored, points back to this guide for current reporting routes, and says to
keep it private and retry later if no suitable route is available.

While issue creation is restricted, you can prepare an unsent private draft
instead of losing the details. Record only what will help reproduce or measure
the problem:

```text
Presspeech version / operating-system version:
Affected stage (setup, recording, recognition, delivery, or controls):
Expected result / observed result:
Smallest safe reproduction steps, or how often it occurs:
Generic context if relevant (target-app class/version, language, keyboard
layout/input source, or hardware):
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

If the problem remains, search the [existing issues](https://github.com/rcourtman/presspeech/issues). A matching open thread may still accept a comment; check before preparing a public reply. New issue forms are not a usable route while GitHub restricts issue creation, so keep the draft private if no suitable thread is available.

<details>
<summary>Issue forms to use only when GitHub reopens issue creation</summary>

These links are retained for later. They do not bypass the current restriction.

- [Report a bug](https://github.com/rcourtman/presspeech/issues/new?template=bug_report.yml). In Presspeech, choose **Copy Diagnostics** first and paste that privacy-safe summary into the form.
- [Browse existing target-app compatibility reports](https://github.com/rcourtman/presspeech/issues?q=is%3Aissue%20in%3Atitle%20%22%5BCompatibility%5D%22), then [share a report](https://github.com/rcourtman/presspeech/issues/new?template=compatibility_report.yml) after running the [short privacy-safe protocol](https://rcourtman.github.io/presspeech/app-compatibility.html).
  Passing reports record repeated attempts in an exact tested configuration
  that failure-only threads miss. These self-selected results do not establish
  a general paste-success rate or a denominator for all users.
  If you do not have a particular app in mind, the protocol's live coverage
  links show native, browser, and Electron/Chromium reports separately so you
  can choose an unrepresented target class.
- [Suggest an improvement](https://github.com/rcourtman/presspeech/issues/new?template=feature_request.yml). Describe the recurring problem and a measurable result.
- [Ask a usage question](https://github.com/rcourtman/presspeech/issues/new/choose) by selecting **Open a blank issue**. Include the platform, Presspeech version, and what you already tried.

</details>

Issues are public. Never include dictated text, audio, dictionary or shortcut
contents, credentials, private paths, or other sensitive data. Diagnostics are
not sent automatically: the app's feedback actions only open this guide. If a
public route becomes available, you decide what to paste and submit.

For a paste or clipboard problem, the bug form asks for the target app class,
focus behavior, clipboard-restore setting, manual-recovery result, and repeat
count. Reproduce with harmless test text, report only whether the complete text
was recovered, and omit the text itself plus document, tab, account, server,
and window names.

Use the compatibility form rather than the general bug form when you can
classify all eight protocol slots, including slots marked **Not completed**
after an early stop. Do not repeat an unsafe attempt merely to fill the form.
Use the bug form for a problem outside text delivery or one where even a
disposable attempt is unsafe. Use the private process in [SECURITY.md](SECURITY.md)
if behavior could expose or execute sensitive content.
Match an existing report only when the platform, target app version, and
generic field type are comparable. When they match, comment with the
worksheet's eight counts and overall result, your Presspeech and operating-system
versions, generic hardware if useful, and relevant conditions; otherwise open
a separate report. Add the observation even when its outcome differs.

Before proposing a larger capability, read the [product roadmap](ROADMAP.md).
It describes current priorities, evidence gates, and workflows that are
deliberately outside Presspeech's scope.

Do not use a public issue for a security vulnerability. Follow the private
reporting process in [SECURITY.md](SECURITY.md) instead.
