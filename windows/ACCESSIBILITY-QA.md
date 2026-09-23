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

3. **Live status.** In Accessibility Insights, use **Listen to Events** on a
   status control and record its UIA events. Trigger a safe status transition,
   such as Setup's global-hotkey startup or a permitted microphone check, and
   confirm the changed status is exposed and Narrator announces it. If a model
   download is already part of the test, verify that phase changes are
   announced but byte-count updates do not repeatedly interrupt speech. For
   the on-screen indicator, use a short harmless Try Dictation phrase and
   confirm its state is accessible and does not take focus.

4. **Text size and contrast.** With Setup/Settings open, change Windows
   Accessibility **Text size** up to 225% and enable a Contrast theme. Check
   that dialog content remains reachable by scrolling, focused controls stay
   visible, and the indicator remains legible and on-screen while its state is
   visible. Also check the normal theme: high contrast is not a substitute for
   readable default colors. If validating a release that still claims Windows
   10 support, repeat the applicable checks there.

## Record the result

Record pass/fail, the app version, Windows version, display scale, text size,
Contrast theme, and any UIA/Narrator findings. Review FastPass failures and
identify known false positives instead of silently ignoring them. Do not attach
screenshots or UIA snapshots containing dictated text, clipboard contents, or
other personal data.

See Microsoft's [Windows accessibility testing guidance](https://learn.microsoft.com/en-us/windows/apps/design/accessibility/accessibility-testing)
for the testing tools and procedures, and the [Accessibility Insights event
monitor guide](https://accessibilityinsights.io/docs/windows/getstarted/eventmonitoring/)
for recording UIA events.
