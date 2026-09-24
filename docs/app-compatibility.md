# Test Presspeech with a target app

The public, navigable version of this protocol is at
<https://rcourtman.github.io/presspeech/app-compatibility.html>.

**Published-build privacy check (24 September 2026):** The current downloads are
macOS 0.3.8 and Windows 0.1.12. Both start a missing-model download on launch.
The macOS request may include an inherited Hugging Face token; the Windows
request may send Hugging Face usage telemetry or a locally available token,
with additional routing and proxy risks. Read the [macOS model-download
warning](../README.md#install-on-macos) or [Windows model-download
warning](../README.md#install-on-windows) before launching or triggering a
model download. If you are unsure about those version-specific risks, wait for
0.3.9 or 0.1.13 to be published rather than launching solely for this check.
If your model is already ready, do not reset its cache or switch models for
this protocol.

**Reporting availability checked 24 September 2026:** GitHub currently reports
that issue creation is restricted for this repository. You can still run the
protocol and keep its aggregate counts locally, but a new-report link may not
accept a submission. Check for a comparable open report and whether comments
are enabled before sharing; if no route is available, retain the result
privately and retry when issue creation is restored. Do not post transcripts,
clipboard contents, or other private data elsewhere as a workaround.

Presspeech binds each recording to the window where it began. It should paste
only when it can still verify that destination. A focus change normally leaves
the transcript available for manual paste. On macOS, if Presspeech detects another copy replacing
the clipboard during delivery, it stops and preserves that newer copy;
0.3.8 shows **Couldn't paste**, while builds with revised wording say
**Delivery uncertain**. A failed input event does not prove that no text reached
the destination, so inspect the target before retrying or using **Copy Last
Transcript**.

On macOS, this is a window-level identity check, not a field- or tab-level
check. Moving to another field or browser tab within that same window while
dictation is finishing can send the transcript to the new focus. Keep the
original field and tab focused until insertion or a recovery notice appears.
The separate-window checks below do not establish safety for same-window moves.

Target apps expose focus and paste behavior differently. A result from one app
version, operating-system version, and Presspeech build is therefore evidence
for that exact combination, not a promise that every field in the app works.
This short check makes successful and unsuccessful community reports
comparable without publishing anyone's dictated text.

This is not a transcription-quality test. The recognized wording can differ
from what was spoken and still pass this delivery check; the question is
whether the same finished transcript reaches the intended field once or is
recovered safely.

## Choose useful coverage

If you already depend on one app, test that app. Otherwise, choose a safe
disposable field in a target class that does not yet have a comparable report.
These live searches make missing coverage visible without collecting anything
on the Presspeech site:

- [Native desktop app reports](https://github.com/rcourtman/presspeech/issues?q=is%3Aissue%20in%3Atitle%20%22%5BCompatibility%5D%3A%22%20in%3Abody%20%22Native%20desktop%20app%22)
- [Browser page or web editor reports](https://github.com/rcourtman/presspeech/issues?q=is%3Aissue%20in%3Atitle%20%22%5BCompatibility%5D%3A%22%20in%3Abody%20%22Browser%20page%20or%20web%20editor%22)
- [Electron/Chromium desktop app reports](https://github.com/rcourtman/presspeech/issues?q=is%3Aissue%20in%3Atitle%20%22%5BCompatibility%5D%3A%22%20in%3Abody%20%22Electron%2FChromium%20desktop%20app%22)

For native apps, establish a baseline in a platform-native plain-text or
rich-text field. For a browser target, record the browser version, web app or
editor version when public, and generic field type; omit account, tab,
document, and form names. For Electron/Chromium, use two separate windows of
the same app for at least one focus-change attempt. Tabs are not a substitute.

**Especially useful now:** if you use an Electron/Chromium app on macOS, include
both the steady-focus checks and a focus change between two windows of that
same app. The open [paste-target validation issue](https://github.com/rcourtman/presspeech/issues/33)
needs evidence that steady focus can paste while a same-process window change
still recovers safely. If new issues remain restricted but comments on that
issue are available, you can add this exact macOS Electron result there with
the eight counts, Presspeech/OS/target-app versions, and generic field type.
Otherwise keep the draft private. A community report does not replace native
release qualification.

Use the same issue thread for the same platform, target-app version, and
generic field type, even when the outcome differs; open a separate report when
one of those boundaries differs. This is thread matching, not evidence pooling.
Keep each observation labelled with its exact Presspeech and operating-system
versions and relevant conditions. Do not add counts across different versions
or conditions or infer a general paste-success rate from self-selected reports.
The class links search standardized compatibility-report bodies; older general
bug reports are not counted as completed protocol evidence.

## Before testing

1. Read the published-build privacy check above before launching or
   downloading a model. Use the current official Presspeech build and finish
   its setup checks. On macOS, this baseline requires 0.3.8 or later; update
   before testing if the About window shows 0.3.7.
2. Open a blank, disposable field in the target app and a second blank field in
   a local scratch app. Never test in a production message, document, account,
   command shell, or field where pasted text can submit, send, or execute. Test
   a remote/virtual environment or terminal-based editor only when it is
   isolated, disposable, and non-executing; otherwise skip that target.
3. On macOS 0.3.8 or later, leave **Settings -> Behavior -> Keep Previous
   Clipboard for Manual Restore** off. The automatic timer in legacy 0.3.7 is
   not a comparable baseline. Windows leaves the transcript on the current
   clipboard. Upcoming
   Windows 0.1.13 asks Windows to exclude it from Clipboard History and Cloud
   Clipboard; published 0.1.12 does not.
4. Use only harmless phrases created for the test. On Windows 0.1.12, disable
   clipboard history and cross-device clipboard sync. On every platform,
   macOS 0.3.8 can expose transcript entries to Universal Clipboard;
   disable Handoff if that test text must stay on the Mac. Builds containing
   local-only transcript clipboard writes prevent Universal Clipboard transfer
   while preserving local Command-V. Apple's
   [Screen Sharing](https://support.apple.com/guide/mac-help/mh14066/mac) has a separate
   **Edit → Use Shared Clipboard** setting; those transcript writes are not a
   tested guarantee against that remote transfer. Turn shared clipboard off
   if you do not intend to transfer test text to the remote Mac. On every
   platform, disable third-party clipboard managers if you do not want even
   that test text retained outside Presspeech. If macOS Clipboard History in
   Spotlight is enabled on macOS 26 or later, clear it after the check if you
   do not want the harmless text retained there.
5. Note the exact Presspeech, operating-system, and target-app versions. Also
   note whether the target is a native app, browser page, Electron/Chromium
   app, terminal, remote desktop, or elevated Windows app, plus the generic
   field type (for example, plain text, rich text, or browser content editor).
   Note the active keyboard layout/input source only if it differs from your
   usual layout or helps explain an outcome; record its public name, never
   typed content.

## Check steady-focus delivery

Plan five attempts. Stop early if an attempt is incorrect or unsafe; do not
repeat it just to fill the worksheet. Use a different harmless phrase each
time so stale or duplicated delivery is visible; for example, say a colour,
an animal, and the attempt number. If English suits the selected language
hint, an optional full
set is: **amber rabbit one**, **blue otter two**, **copper robin three**,
**green badger four**, **ivory falcon five**, **purple fox six**, **silver
heron seven**, and **yellow turtle eight**. Otherwise use the same pattern in
the test language. Recognition accuracy is not scored; compare the finished
target text with the scratch paste, and never publish either version.

For each attempt:

1. Put the cursor in the blank target field and keep that window focused from
   the start of recording until Presspeech finishes. Do not copy anything or
   allow a clipboard tool to replace its contents during delivery.
2. Dictate the harmless phrase once.
3. Note what appeared in the target and whether Presspeech showed a recovery
   notice.
4. If delivery succeeded or Presspeech showed its copied/manual-paste notice,
   paste into the separate local scratch field before copying anything else.
   With restoration off and no intervening clipboard change, this should be
   the finished transcript. After a delivery warning on macOS, do not assume
   the clipboard contains the transcript or that the destination received
   nothing: inspect the target before retrying. If text is missing, use
   **Copy Last Transcript** if offered, removing any partial text before
   pasting the full transcript. If Recent Transcripts is off, there is no
   in-app copy-recovery entry; correct or remove partial text before dictating
   again. Record the failed delivery; deliberate recovery afterward does not
   turn it into a successful attempt.
5. Record one outcome:
   - **Pasted once:** the target received one copy and no recovery notice
     appeared. Its text matches the scratch copy, including the configured
     space or newline suffix.
   - **Recovered safely:** the target received nothing, Presspeech showed its
     copied/manual-paste notice, and the scratch paste recovered the complete
     transcript.
   - **Incorrect or unsafe:** text was stale, partial, duplicated, unavailable,
     or delivered to another field, or paste failed without a recovery notice.
6. Clear both disposable fields before the next attempt.

After an incorrect or unsafe result, stop testing. Mark that attempt
**Incorrect or unsafe**, then mark every unrun steady-focus and focus-change
slot **Not completed**. Do not retry in a real document. If you cannot
continue for another reason, mark unrun slots **Not completed**; a completed
failure is never an unrun check. An early stop is not a passing five-attempt
baseline.

If you know another copy changed the clipboard during delivery, record that
attempt as an interruption under **Relevant conditions** and repeat with a fresh
harmless phrase if it remains safe to continue. Exclude known interruptions
from the five scheduled check slots. Do not use this exclusion for unexplained
failures; report those as **Incorrect or unsafe** above.

A copied/manual-paste result is a safe recovery, not an automatic-paste pass.
Keep its count separate so reports do not hide app classes where insertion is
consistently unavailable. The four aggregate steady-focus counts, including
not completed, should total five.

## Check focus safety

Use blank, disposable fields in two separate windows. Start a dictation in the
first window, move focus to the second before stopping the recording, then
finish. In hold mode, move focus before releasing the hotkey; in toggle mode,
move it before the stop press.

With no intervening clipboard change, the safe result is:

- no text is inserted into either field;
- Presspeech shows the copied/manual-paste notice; and
- the complete transcript is available for deliberate manual paste.

Plan three attempts, recording known clipboard-change interruptions
separately as above. For an Electron/Chromium app, use two separate windows of
the same app for at least one attempt because same-process windows are a
distinct identity check. Do not substitute tabs or fields in one window, and
do not use a field where Return, Enter, or a paste action can submit or execute
text.

Classify each slot as **copied for manual paste without inserting anywhere**,
**inserted into a field**, **no insertion, but recovery failed**, or **not
completed**. Recovery failed means a completed attempt inserted nowhere but
the notice or complete transcript was unavailable; it is an incorrect result,
not an unrun check. The four counts should total three.

Text reaching either test window or any unrelated destination is a safety
failure. Stop testing; mark that slot as inserted and each unrun slot as not
completed. Do not retry in a real document. If the behavior could
expose or execute sensitive content, use the private reporting route in
[`SECURITY.md`](../SECURITY.md). Otherwise, report only privacy-safe outcomes
through a public route when one is available; if intake is restricted, retain
the aggregate locally and retry later.

## Record categories without recording words

The [public protocol page](https://rcourtman.github.io/presspeech/app-compatibility.html#worksheet)
includes an optional in-page worksheet for the eight check outcomes. It
calculates the two aggregate count sets and prepares a report-ready block with
the canonical overall classification.
Selections stay only in the page controls: the worksheet does not send
selections, write them to browser storage, or provide a field for a phrase,
transcript, or report context. Use **Reset worksheet** to clear them. **Copy
report block** places only the labelled eight counts and overall classification
on the clipboard. **Download report draft** saves those counts with blank
prompts for public versions and generic target context; the worksheet does not
collect or prefill those details. The plain-text draft contains no phrases or
transcripts, and the user decides what to share. It also says the download is
not submitted or monitored, points to the support guide for current reporting
routes, and advises keeping the file private if no suitable route is available.
The browser or operating system controls the downloaded file. It can preserve
a classified result and a reminder of useful context while no public reporting
route is available. Once all eight check slots are classified, the worksheet reveals
the existing-report search and a link to the GitHub issue list, where the
current issue-creation status is visible. Selections are not submitted; if
intake is restricted, keep the draft locally and retry later.

Without the worksheet, tally the same eight categories manually:

```text
Five steady-focus results
Pasted once: [0-5]
Recovered safely: [0-5]
Incorrect or unsafe: [0-5]
Not completed: [0-5]

Three focus-change results
Copied for manual paste without inserting anywhere: [0-3]
Inserted into any field: [0-3]
No insertion, but recovery failed: [0-3]
Not completed: [0-3]

Overall result: [classification from the definitions below]
```

The first four values must total five and the final four must total three.
Record known clipboard-change interruptions separately under relevant
conditions; do not turn them into another category or include their text.
If an incorrect or unsafe steady-focus result stops testing early, count it
under **Incorrect or unsafe** and every unrun slot under **Not completed**.
If unexpected insertion stops the focus-safety check early, count that attempt
as **Inserted into a field** and each unrun remainder as **Not completed**.
If a completed attempt inserts nowhere but does not show a recovery notice or
does not make the complete transcript available, count it as **No insertion,
but recovery failed**, not **Not completed**. Either completed failure makes
the overall result **An incorrect or unsafe result occurred**; reserve
**Testing could not be completed** for unrun slots when no completed failure
occurred.

An older seven-count draft with five classified steady-focus attempts can add
**Steady-focus not completed: 0**. An older six-count worksheet draft may
combine failed recovery and unrun focus-change slots as **Other or not
completed**. If that count is zero, both new focus-change counts are zero.
If it is nonzero, do not guess which happened or present it as a comparable
eight-count baseline. Keep the old draft private; if safe, rerun the protocol
with the current categories. If a safety failure caused the stop, do not repeat
it in a real field and use the appropriate reporting route when available.

## Keep separate boundaries separate

- The optional macOS clipboard-restoration path has a stricter repeated-trial
  release check in [`manual-qa.md`](manual-qa.md). Leave it off for this
  community baseline so target-app compatibility is not confused with explicit
  restoration. If previous clipboard content is pasted, follow the
  [recovery guide](troubleshooting.md#previous-clipboard-content-is-pasted)
  and report a bug.
- A normally running Windows app cannot send the paste shortcut into a target
  running as administrator. A copied/manual-paste notice is the expected
  boundary. Do not elevate Presspeech as a workaround.
- Remote-desktop, virtual-machine, terminal-based editor, browser-editor, and
  assistive-technology paths are useful reports only when the field is isolated,
  disposable, and cannot submit or execute a paste. An ordinary command-shell
  prompt is outside this community protocol. Identify the class without sharing
  host names, account names, document titles, commands, or other private
  context.

## Share the result

When public reporting is available, first [browse existing compatibility
reports](https://github.com/rcourtman/presspeech/issues?q=is%3Aissue%20in%3Atitle%20%22%5BCompatibility%5D%3A%22),
then submit one
[target-app compatibility report](https://github.com/rcourtman/presspeech/issues/new?template=compatibility_report.yml)
per platform, target app, version, and generic field type. If issue creation is
restricted, check whether comments are enabled on a matching report; otherwise,
retain the result locally and retry when a public route returns. Do not send
reports or private data elsewhere as a workaround. Reports in which every
attempt passed are useful: they document successful delivery and safe recovery
for that exact tested configuration. Self-selected reports cannot establish a
population success rate. For a new report, put the platform, public app name/version,
and generic field type after the fixed `[Compatibility]:` title prefix; include
no private document, tab, account, server, form, or window name. Paste the
worksheet output into **Eight-check outcome counts** and select the overall
result it shows, or enter the same counts manually.

If the same platform, app version, and field type already has a compatibility
report and comments are enabled, add a comment there instead of opening a
duplicate. Paste the
worksheet block, then add the Presspeech and operating-system versions,
generic hardware if useful, and relevant conditions. Do not repeat the target
app or field type unless the existing report is ambiguous. Add your counts
even when the outcome differs: variation under comparable conditions is
important evidence. Use a separate report for a different platform or field
type.

If issue creation is still restricted and your macOS Electron result directly
addresses [issue #33](https://github.com/rcourtman/presspeech/issues/33), you
may instead comment there if GitHub permits it. Include the eight counts plus
Presspeech, operating-system, and target-app versions and the generic field
type. Do not post unrelated results in that thread; retain those drafts
privately when no suitable route accepts them.

Use this shape for an observation added to an existing report:

```text
Presspeech version: [x.y.z]
Operating-system version: [version]
Hardware (optional, no serial or device names): [generic model/chip]

[paste the worksheet's eight counts and Overall result]

Relevant conditions: [trigger mode, keyboard layout/input source when
relevant, suffix, clipboard manager/history, assistive technology, or a minimal
reproduction; omit private context]
```

For the form's **Overall result**, a complete automatic-paste pass means all
five steady-focus attempts pasted once and all three focus-change attempts
recovered safely. Focus-change recovery is expected, so it does not count as a
manual-recovery limitation. Choose that option only when manual-paste recovery
occurred while the original target stayed focused. An incorrect or unsafe
result takes precedence over either successful classification.

Report only counts and classifications. Never include the phrases, recognized
transcripts, clipboard contents, audio, dictionary or shortcut contents,
document or window titles, private paths, credentials, or screenshots that
contain user data. **Copy Diagnostics** produces a privacy-safe summary if you
choose to include it; exact microphone names, raw error details, and raw local
log lines are not copied into that report.

An incorrect or unsafe result should also include the smallest observed
sequence in the report; do not repeat a safety failure solely for reporting.
Security-sensitive behavior belongs in the private
process in [`SECURITY.md`](../SECURITY.md), not a public issue.

Community reports are exploratory evidence. Maintainers still run the broader
native checklist before release, and a small set of passing observations does
not override a reproducible failure.
