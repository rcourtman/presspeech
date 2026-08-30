# Manual QA Checklist

Run this before publishing a release.

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
- Start dictating into a text field, switch to another window in the same app
  before transcription finishes, and confirm no text is pasted into the new
  window. Repeat with a window in another app. Confirm in both cases that the
  transcript is available on the clipboard for manual paste and Presspeech
  plays/shows its failed-paste cue.
- Repeat the focus-change check immediately after pressing the hotkey, before
  the waveform appears, so a cold or rebuilding audio engine cannot retarget
  the transcript during startup.
- Enable **Restore clipboard after paste**, trigger the focus-change path, and
  confirm the transcript remains available for manual paste rather than being
  replaced by the old clipboard contents.
- Test hold mode: hold the hotkey, speak, release, and confirm text pastes
  at the cursor.
- Test toggle mode: press once to start, press again to stop.
- Press Escape during an active recording and confirm it cancels without
  pasting.
- Confirm the recording waveform appears when enabled.
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
