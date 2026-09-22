# Opt-in macOS native interaction acceptance

The debug executable includes a bounded acceptance fixture for real Quartz event
taps, the hotkey recorder decision, hold/toggle callbacks, and clipboard paste
into two fixture-owned AppKit text views. It runs the production implementations;
its recording callbacks only increment counters. It never constructs
`PresspeechApp`, `Settings`, an audio engine, or a speech model.

## Non-interactive verification

These commands do not activate windows or post keyboard events:

```sh
swift build --package-path swift
swift/.build/debug/Presspeech --self-test native-interaction-policy
swift/.build/debug/Presspeech --self-test all
```

The following command must fail immediately, before the fixture is constructed:

```sh
swift/.build/debug/Presspeech --self-test native-interactions
```

`all` deliberately excludes native interactions. Release builds contain neither
the fixture nor its debug hooks and reject self-test flags before app launch. Do not use `dev-run.sh` for this check: that
script relaunches the installed application.

## Reviewed native run

Review the safeguards and limitations below before running this exact command
from the repository root. It temporarily activates two clearly named test
windows and posts real keyboard events. Other Presspeech/Parakey applications
must already be quit: the fixture checks known bundle IDs and executable names
before snapshotting the clipboard or activating a window and repeats that check
during delivery. It does not stop or restart another app. There is no reliable
installed-app recording-state signal, so an idle-looking running app is still a
preflight failure. Keep the installed app running if interrupting it would be
unacceptable, and use only the non-interactive checks until a suitable manual QA
session is available. Release physical modifiers, and do not type, click, scroll,
change focus, or copy during the test.
Existing Accessibility, Input Monitoring, and event-posting permission is
required; the fixture never requests permission or opens System Settings.

```sh
swift/.build/debug/Presspeech --self-test native-interactions --allow-native-input
```

Only this exact argument sequence is accepted. The active interaction phase has
an overall 30-second guard and two-second per-check deadlines, plus a deliberate
3.25-second no-automatic-restore observation and a one-second cleanup drain.
Snapshotting an existing lazy clipboard provider and operating system API calls
are synchronous and cannot be promised a hard wall-clock bound.
A nonzero exit or missing final `PASS native-interactions` means acceptance has
not passed. Preserve only fixed test labels, counts, and PASS/FAIL output in test
evidence; do not add clipboard contents, foreground window titles, or transcripts.

The fixture checks:

- A real locally delivered event enters the production recorder decision.
- The actual production global tap receives an uncommon Command+Control+Option
  punctuation combination, suppresses matched down/repeat/up events, releases a
  hold after modifiers have been released, and passes an extra-modifier mismatch.
- Toggle mode rejects an unavailable start without changing its toggle state,
  starts/stops on subsequent presses, and suppresses Escape while cancelling.
- In the 0.3.8 fixture and later builds containing the manual-restore option,
  production Command+V inserts a fixed marker into the owned text view. After
  observing that exact field consume it, the fixture leaves the transcript on
  the clipboard beyond the retired setting's maximum three-second timer window,
  then explicitly requests the guarded manual restoration. Posting alone never
  schedules restoration.
- A newer fixture copy survives an explicit request using an older restore token.
- Focusing the second owned window makes a target captured from the first window
  fall back to copy-only without posting paste events.

## Isolation and abort behavior

The eligible original clipboard is copied in memory with all items, types, and
data using production snapshot/restore functions and the production 64 MB / 256
representation limits. Incomplete or over-limit snapshots abort before window
activation or mutation. Nothing serializes or logs that snapshot. Debug-only
hooks retain the generation returned by actual ownership acquisition during
production writes and restorations. An external change-count mismatch aborts and
prevents restoration over the newer copy; a later count is never adopted as the
fixture's ownership.
Final restoration requires the clipboard still have the fixture-owned count.
No arbitrary current clipboard text is read by the native checks.

Before every test key-down and production paste event, the fixture checks its
foreground PID, exact owned key window, clipboard change count, deadline, and
sticky abort state. An upstream HID tap repeats the check at delivery. External
keyboard input, mouse clicks, scrolls, and application activation abort the test;
external input itself is passed through. Mouse movement alone does not abort.
The fixture's production listener ignores all events lacking its own-process
tag. A tail session tap provides a downstream suppression observation; the
production listener is installed at the session head after existing listeners.
Thus the test establishes delivery and suppression at those observation points,
not the internal behavior of every third-party global listener.

A narrow exception permits a paired **left Command key-up** after a focus abort
if the guard already forwarded that fixture's Command-down. Dropping this release
could leave a synthetic modifier held. No unpaired Command-up or new key-down is
allowed by the guard after abort. Cleanup drains queued fixture events while the
guard remains installed. Quartz input is asynchronous; this fixture cannot offer
an atomic focus-and-event-delivery transaction or prevent every operating-system
or third-party event-tap failure. A cleanup timeout fails acceptance.

Saved hotkey keycodes are read without migrations or writes from both current
and legacy preference suites. Every posted keycode is screened: sentinel A (0),
paste V (9), Escape (53), left Command (55), and the selected punctuation,
regardless of modifier mask. A conflict aborts preflight. No right-side modifier
or F-key event is posted. This is defense in addition to the running-app refusal,
not a substitute for proving that another instance cannot be recording. A
third-party application with a conflicting global shortcut can still interfere;
interference is a failure, not a reason to weaken the guards.

Afterward the fixture closes only its own windows and reactivates the previous
application only if the fixture still owns foreground focus. It does not inspect
that application's contents or choose a window within it. If the user switched
elsewhere, cleanup preserves that choice. Native fixture diagnostics stay on
stderr rather than appending to the user's production log. AppKit may maintain
its ordinary process-level framework state; the fixture never writes Presspeech
preferences or restarts the installed app.

## Limits of the evidence

A successful run establishes behavior in these controlled AppKit windows. The
timer-window check proves that Presspeech does not automatically replace its
owned transcript during that interval; it does not prove Electron/VS Code AX
availability, slow or asynchronous clipboard consumption, restoration
correctness for every external clipboard provider, VoiceOver focus/announcements
in the full recorder dialog, keyboard-layout label refresh, a second physical
keyboard layout, right-modifier hardware behavior, or microphone/model behavior.
Those remain separate manual checks. Explicit user confirmation is not a Quartz
clipboard-consumption acknowledgement.
The fixture exercises the recorder's real decision function through a native
local monitor, not its modal confirmation UI or persistence; the existing pure
hotkey suite covers persistence and invalid input.
