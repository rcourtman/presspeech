# Manual QA Checklist

Run this before publishing a release.

## Windows First Run

- On a clean Windows profile, launch the packaged app and confirm the setup
  window automatically checks the selected microphone without blocking model
  preparation.
- With microphone access enabled, confirm the status changes from **Checking…**
  to **Ready — microphone audio is available**. Change the selected input and
  confirm setup checks the new device.
- Turn off **Let desktop apps access your microphone**, choose **Check Again**,
  and confirm setup reports **Needs attention** without exposing raw device or
  PortAudio errors.
- Using only Tab, Shift-Tab, and Enter, open **Microphone Privacy Settings** and
  **Sound Input Settings** from setup. Confirm each button opens the expected
  Windows Settings page, then restore access and rerun the check successfully.
- With Narrator, confirm the microphone selector, changing check status, and
  both recovery buttons expose meaningful names.
- Set Windows **Text size** to 225% and use a 1024 x 768 display (or VM). Confirm
  Setup and Settings stay within the desktop, expose scrollbars when needed,
  and automatically scroll each control into view while navigating with Tab
  and Shift-Tab. Resize both windows and repeat with Narrator enabled.
- While the model is preparing, confirm **Try Dictation** and **Finish Setup**
  remain disabled. Choose **Set Up Later**, restart, and confirm setup opens
  again. After the model reaches Ready, confirm both actions become available.
- Confirm **Finish Setup** remains available if the microphone will be connected
  later; microphone readiness is advisory rather than a completion gate.
- Begin recording in both hold and toggle modes, press **Escape**, and confirm
  capture stops, muted playback is restored, and no transcription is pasted or
  copied. Repeat with **Cancel Dictation (Esc)** in the notification-area menu.

## Signed App Smoke

```sh
cd swift
./dev-run.sh
```

- Confirm `/tmp/Presspeech-dev.app` launches and the menu-bar item appears.
- Open **Support -> Setup Checklist...** and confirm model, permissions,
  audio input, and hotkey rows render.
- Navigate the checklist using Tab and Shift-Tab. Activate a permission action
  that remains missing and changes from **Grant** to **Try Again**; confirm
  keyboard focus remains on that replacement action when the row is redrawn.
- With VoiceOver enabled, confirm each permission action includes its context,
  such as **Grant Microphone** and **Grant Accessibility**, rather than being
  announced only as **Grant**.
- Complete setup, close every Presspeech window, then open the already-running
  app again from Finder or Spotlight. Confirm Setup Checklist appears instead
  of a second app instance. Enable **Show in Dock**, right-click the
  Dock icon, and confirm dictation controls, Settings, Support, and the standard
  macOS Quit command are all available. Disable the option and confirm the Dock
  icon is removed without closing Setup Checklist.
- Enable **Show in Dock**, minimize Setup Checklist, then reopen Presspeech from
  Finder or the Dock. Confirm the minimized window is restored and brought to
  the front rather than leaving Presspeech without a visible control surface.
- On a clean preference profile, confirm setup asks for **Language & model**
  before downloading a speech model. Choose English and confirm the setup
  row switches to the English optimized model; reset preferences and repeat
  with Multilingual.
- Confirm **Support -> Copy Diagnostics** copies a report with no
  transcript text or text-correction contents.
- Confirm **Support -> Save Diagnostics...** writes the same privacy-safe
  report.

## Hotkeys

- In **Settings -> Hotkey**, choose **Right Option** and dictate once.
- Record an F-key such as F7 with **Record Hotkey...**, then dictate once.
- Record a right-side modifier such as **Right Control**, then dictate once.
- Try recording a normal letter key and confirm it is rejected.
- Use **Reset Hotkey to Default** and confirm the menu returns to
  **Right Option**.
- Cancel the hotkey recorder and confirm the existing hotkey still works.

## Dictation

- With a text field focused, choose **Start Dictation** from the menu, speak,
  then choose **Stop and Transcribe**. Confirm the transcript returns to the
  original text field. Repeat in toggle mode and confirm the hotkey can stop a
  menu-started recording.
- Enable macOS Voice Control and confirm the Presspeech status item is named
  **Presspeech**, its recording state is announced, and the named start/stop
  menu actions can be selected by voice.
- With VoiceOver enabled, trigger a focus-change recovery and a no-speech
  result. Confirm each recovery instruction is announced once without moving
  VoiceOver focus away from the target app, including when feedback sounds and
  the recording waveform are disabled.
- Start dictating into a text field, switch to another window in the same app
  before transcription finishes, and confirm no text is pasted into the new
  window. Repeat with a window in another app. Confirm in both cases that the
  transcript is available on the clipboard for manual paste and Presspeech
  plays/shows its failed-paste cue.
- Repeat the focus-change check immediately after pressing the hotkey, before
  the waveform appears, so a cold or rebuilding audio engine cannot retarget
  the transcript during startup.
- In each focus-change case, confirm the HUD says **Copied — press ⌘V to
  paste**, the menu keeps the same recovery instruction after the HUD closes,
  and copying the last transcript clears the notice.
- Dictate silence long enough to pass the short-clip cutoff and confirm the HUD
  and menu report **No speech detected — try again** rather than playing the
  successful-dictation cue.
- Enable **Restore clipboard after paste**, trigger the focus-change path, and
  confirm the transcript remains available for manual paste rather than being
  replaced by the old clipboard contents.
- Test hold mode: hold the hotkey, speak, release, and confirm text pastes
  at the cursor.
- Test toggle mode: press once to start, press again to stop.
- Press Escape during an active recording and confirm it cancels without
  pasting.
- Confirm the recording waveform appears when enabled.
- In System Settings → Accessibility → Display, enable **Differentiate without
  color** and confirm the recording HUD uses a **Recording** text label while
  the menu-bar recording and error states use distinct record/alert shapes.
- Enable **Reduce motion** before and during a recording. Confirm the HUD
  switches to a static text state and appears/disappears without expanding or
  collapsing; in toggle mode it still says **Esc cancels**.
- Enable **Reduce transparency** and **Increase contrast** before and during a
  recording. Confirm the HUD updates live to an opaque capsule and the
  high-contrast variant gains a bright border and fully opaque text.
- Confirm **Mute system audio while recording** still unmutes after release
  and cancel.

## Permissions And TCC

- On a clean or reset machine, launch Presspeech and use **Setup Checklist...**
  to request Microphone, Accessibility, and Input Monitoring.
- Confirm each granted permission removes or updates its setup row after the
  app is reopened if macOS requires it.
- Confirm the app handles a missing permission by staying not-ready instead
  of recording.

## Updates

- Use **Support -> Check for Updates...** on a current build and confirm it
  reports no pending update.
- If testing from a brew install with an older release available, confirm
  the update item starts the Homebrew helper and writes an update log.

## Benchmark Helpers

```sh
cd experiments/swift-bench
./run-release-asr-checks.sh --self-test
./run-release-asr-checks.sh
./add-real-dictation-fixture.sh --self-test
./run-real-dictation-regression.sh --self-test
./run-real-model-comparison.sh --self-test
./run-tail-word-regression.sh --self-test
./bench-power.sh --self-test
sudo -v
./bench-power.sh --file test-audio/short-clean.wav --backend v3 --trials 1 --out-dir /tmp/presspeech-power-results
```

The real `bench-power.sh` run requires interactive sudo because
`powermetrics` requires it. `run-release-asr-checks.sh` runs private
real-dictation regressions only when local clips exist under
`experiments/swift-bench/real-audio/`.
