# Windows accessibility QA

The Windows unit tests check the UI Automation bridge and accessible state
updates with mocks. They cannot establish that the packaged app exposes the
expected UI Automation (UIA) tree/events to Windows or that Narrator announces
them correctly. Use this manual check for release candidates that change the
Windows UI, UIA integration, or Tk version.

## Test setup

- Use a disposable Windows x64 account or VM and the packaged candidate, not an
  elevated process. Keep real dictation, clipboard contents, and personal
  settings out of the test.
- Install and open [Accessibility Insights for Windows](https://accessibilityinsights.io/docs/windows/getstarted/setup/).
  Start Narrator with **Windows logo key + Ctrl + Enter**.
- Leave model downloads and microphone checks until deliberately needed. A
  microphone check opens the selected input; a Try Dictation recording captures
  audio in memory. Use only a harmless test phrase in the private scratchpad.

## Checklist

1. **UIA tree and automated checks.** Run Accessibility Insights **FastPass**
   on Setup, Settings, Try Dictation, and Delivery Recovery (and the update
   window when available). Review each finding rather than assuming every
   failure is actionable or that a clean scan proves usability. In **Live
   Inspect**, check that controls expose useful names, roles, and states: for
   example, Setup's microphone and hotkey selectors, Settings' labelled
   selectors and dictionary editor, the named scratchpad editor, and Recovery's
   buttons/status. Recovery UI must not expose the retained transcript text.

2. **Keyboard and Narrator.** Without using the pointer, traverse each window
   with Tab and Shift+Tab; use arrow keys within radio groups and selectors.
   Check that visible command order, access keys, Escape, and scrolling work;
   focused controls remain visible and focus does not stay on a command when
   that command becomes disabled. Have Narrator read and invoke controls; names,
   roles, values, and checked/disabled states should agree with the visible UI.
   A status update must not steal focus.
   In Try Dictation, leave a harmless dictation waiting for recovery and close
   Delivery Recovery with **Leave Waiting**. Confirm **Review Delivery…** is
   enabled, reachable by Tab and Left Alt+R, and reopens Recovery without
   copying. After resolving the item, confirm Review disables, focus does not
   remain on the disabled button, and Dictate becomes available again.
   In Delivery Recovery, confirm the warning that Copy replaces the current
   clipboard item—including non-text content—is visible and that Narrator
   announces the same consequence when **Copy for Manual Paste** is focused.
   The notification-area Copy label must also say it replaces the clipboard.
   Leave Waiting must remain reachable without copying or discarding.

3. **Live status.** In Accessibility Insights, use **Listen to Events** on a
   status control and record its UIA events. Trigger a safe status transition,
   such as Setup's global-hotkey startup or a permitted microphone check, and
   confirm the changed status is exposed and Narrator announces it. For a
   microphone check, confirm **Connecting microphone…** precedes **Listening —
   speak a few words…**, and Listening is not announced before the first input
   buffer arrives. If a model download is already part of the test, verify
   that phase changes are
   announced but byte-count updates do not repeatedly interrupt speech. For
   the on-screen indicator, use a short harmless Try Dictation phrase and
   confirm its state is accessible and does not take focus.
   On a build with microphone-start readiness gating, confirm the indicator
   announces **Connecting microphone…** before **Listening…**, and Try
   Dictation's status does not invite speech until that change. A quick release
   before readiness should announce **Microphone was not ready — try again**
   without leaving a stale Listening status. While Try Dictation is actively
   recording, confirm Setup disables its microphone selector and Check
   Microphone command, moves focus off either if needed, and announces a
   blocked racing action without claiming that another input was checked.
   A microphone check in progress must also keep Try Dictation unavailable
   until the check ends.
   In Settings, choose a harmless test input without saving it, then use
   **Check Microphone**. Confirm it opens only on that explicit command,
   announces Connecting, Listening (only after an audio buffer), and the
   result, and refreshes the picker after a reconnect without saving the
   choice. Save only if you intend to change the dictation input. Confirm
   microphone Privacy and Sound Input links are keyboard reachable; an active
   dictation must postpone the check without opening another input.

4. **Text size and contrast.** Test Setup, Settings, Try Dictation, Delivery
   Recovery, and the update window individually at Windows Accessibility
   **Text size** 225%. Change the setting while each window is open as well as
   before opening it. Check that all actions and explanatory text remain
   reachable by scrolling, focused controls stay visible, and text remains
   readable without clipping or obscuring a choice. Confirm Delivery Recovery
   still does not expose retained transcript text. Enable a Contrast theme and
   check each window plus the indicator; the indicator must remain legible and
   on-screen while its state is visible. Also check the normal theme: high
   contrast is not a substitute for readable default colors. Restore the
   user's original Windows text-size and contrast settings after the test. If
   validating a release that still claims Windows 10 support, repeat the
   applicable checks there.

## Record the result

Record pass/fail, the app version, Windows version, display scale, text size,
Contrast theme, and any UIA/Narrator findings. Review FastPass failures and
identify known false positives instead of silently ignoring them. Do not attach
screenshots or UIA snapshots containing dictated text, clipboard contents, or
other personal data.

For each candidate that needs this check, copy and complete this record so a
later reviewer can distinguish a packaged Windows result from mocked tests or
an incomplete run:

```text
Candidate version and package SHA-256:
Test date:
Windows edition, version, and build:
Display scale / resolution:
Accessibility Text size:
Contrast theme (or None):
Narrator version:
Accessibility Insights version:
Changed UI surfaces:

FastPass — Setup / Settings / Try Dictation / Delivery Recovery / Update:
UIA names, roles, values, and states:
Keyboard traversal, focus, and scrolling:
Narrator control and live-status announcements:
Text scaling and contrast (including normal theme):
Windows 10 checks, if this candidate claims Windows 10 support:

Findings and reviewed false positives (identify surface and impact):
Fixes and retest result:
Overall result: Pass / Fail / Not run
Unrun checks and reason:
```

Mark a check **Not run** rather than inferring a pass from source inspection,
unit tests, or another Windows configuration. A candidate that changes the
Windows UI, UIA integration, or Tk version has no native accessibility
sign-off until the applicable packaged-app checks above are completed and
reviewed; unresolved failures or unrun checks must remain explicit in the
release decision.

See Microsoft's [Windows accessibility testing guidance](https://learn.microsoft.com/en-us/windows/apps/design/accessibility/accessibility-testing)
for the testing tools and procedures, and the [Accessibility Insights event
monitor guide](https://accessibilityinsights.io/docs/windows/getstarted/eventmonitoring/)
for recording UIA events.
