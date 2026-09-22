# Test Presspeech with a target app

Presspeech binds each recording to the window where it began. It should paste
only when it can still verify that destination; otherwise it should leave the
complete transcript on the clipboard and explain how to paste it manually.

Target apps expose focus and paste behavior differently. A result from one app
version, operating-system version, and Presspeech build is therefore evidence
for that exact combination, not a promise that every field in the app works.
This short check makes successful and unsuccessful community reports
comparable without publishing anyone's dictated text.

This is not a transcription-quality test. The recognized wording can differ
from what was spoken and still pass this delivery check; the question is
whether the same finished transcript reaches the intended field once or is
recovered safely.

## Before testing

1. Use the current official Presspeech build and finish its setup checks.
2. Open a blank, disposable field in the target app and a second blank field in
   a local scratch app. Never test in a production message, document, terminal
   session, remote session, or account.
3. On macOS, turn **Settings -> Behavior -> Restore clipboard after paste**
   off. It is off by default. Windows always leaves the transcript on the
   clipboard.
4. Use only harmless phrases created for the test. Disable clipboard history,
   cross-device clipboard sync, or third-party clipboard managers if you do
   not want even that test text retained outside Presspeech.
5. Note the exact Presspeech, operating-system, and target-app versions. Also
   note whether the target is a native app, browser page, Electron/Chromium
   app, terminal, remote desktop, or elevated Windows app.

## Check steady-focus delivery

Run five attempts. Use a different harmless phrase each time so stale or
duplicated delivery is visible; for example, say a colour, an animal, and the
attempt number.

For each attempt:

1. Put the cursor in the blank target field and keep that window focused from
   the start of recording until Presspeech finishes.
2. Dictate the harmless phrase once.
3. Note what appeared in the target and whether Presspeech showed a recovery
   notice.
4. Before copying anything else, paste the clipboard into the separate local
   scratch field. With clipboard restoration off, this is the finished
   transcript.
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

A copied/manual-paste result is a safe recovery, not an automatic-paste pass.
Keep its count separate so reports do not hide app classes where insertion is
consistently unavailable.

## Check focus safety

Use blank, disposable fields in two separate windows. Start a dictation in the
first window, move focus to the second before stopping the recording, then
finish. In hold mode, move focus before releasing the hotkey; in toggle mode,
move it before the stop press.

The safe result is:

- no text is inserted into either field;
- Presspeech shows the copied/manual-paste notice; and
- the complete transcript is available for deliberate manual paste.

Repeat three times. For an Electron/Chromium app, use two separate windows of
the same app for at least one attempt because same-process windows are a
distinct identity check. Do not substitute tabs or fields in one window, and
do not use a field where Return, Enter, or a paste action can submit or execute
text.

Text reaching either test window or any unrelated destination is a safety
failure. Stop testing and report it; do not retry in a real document.

## Keep separate boundaries separate

- The optional macOS clipboard-restoration path has a stricter repeated-trial
  release check in [`manual-qa.md`](manual-qa.md). Leave it off for this
  community baseline so target-app compatibility is not confused with restore
  timing. If previous clipboard content is pasted, follow the
  [recovery guide](troubleshooting.md#previous-clipboard-content-is-pasted)
  and report a bug.
- A normally running Windows app cannot send the paste shortcut into a target
  running as administrator. A copied/manual-paste notice is the expected
  boundary. Do not elevate Presspeech as a workaround.
- Remote-desktop, virtual-machine, terminal, browser-editor, and assistive
  technology paths are useful reports, but identify the class without sharing
  host names, account names, document titles, commands, or other private
  context.

## Share the result

Submit one
[target-app compatibility report](https://github.com/rcourtman/presspeech/issues/new?template=compatibility_report.yml)
per target app and version. Reports in which every attempt passed are useful:
they provide the denominator that failure-only bug reports cannot.

Search existing issues first. If the same app/version and result already has a
compatibility report, add only your aggregate counts and environment there
instead of opening a duplicate.

Report only counts and classifications. Never include the phrases, recognized
transcripts, clipboard contents, audio, dictionary or shortcut contents,
document or window titles, private paths, credentials, or screenshots that
contain user data. **Copy Diagnostics** produces a privacy-safe summary if you
choose to include it.

An incorrect or unsafe result should also include the smallest repeatable
sequence in the report. Security-sensitive behavior belongs in the private
process in [`SECURITY.md`](../SECURITY.md), not a public issue.

Community reports are exploratory evidence. Maintainers still run the broader
native checklist before release, and a small set of passing observations does
not override a reproducible failure.
