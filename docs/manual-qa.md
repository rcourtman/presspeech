# Manual QA Checklist

Run this before publishing a release.

## Windows First Run

- On a clean Windows profile, launch the packaged app and confirm the setup
  window automatically checks the selected microphone without blocking model
  preparation.
- With microphone access enabled, speak during the check and confirm the status
  changes from **Listening — speak a few words…** to **Ready — input level
  detected**. Change the selected input and confirm setup checks the new device.
  After the model is ready, use **Try Dictation** and confirm it captures that
  selected device before setup is finished.
- Mute the selected microphone, choose **Check Again**, and confirm setup says
  it is connected but no input level was detected instead of claiming it is
  ready. Unmute it, speak, and check again successfully.
- Turn off **Let desktop apps access your microphone**, choose **Check Again**,
  and confirm setup reports that the microphone could not be opened without
  exposing raw device or PortAudio errors.
- Using only Tab, Shift-Tab, and Enter, open **Microphone Privacy Settings** and
  **Sound Input Settings** from setup. Confirm each button opens the expected
  Windows Settings page, then restore access and rerun the check successfully.
- In Setup, press Left Alt with each command's underlined letter and confirm the
  corresponding enabled command runs without moving focus to it. Confirm a
  disabled model command remains inactive. Repeat the mnemonics in Settings,
  the update prompt, and Try Dictation; confirm **Ctrl+S** saves Settings.
- Press **Escape** in Setup, Settings, and Try Dictation and confirm only the
  current window closes. Start an update download, press **Escape**, and confirm
  the prompt closes and its partial download is cancelled and cleaned up.
- Confirm initial keyboard focus lands on the microphone selector in Setup, the
  hotkey selector in Settings, Download Update in the update prompt, and the
  text area in Try Dictation.
- With Narrator, confirm the microphone selector and both recovery buttons
  expose meaningful names. Leave focus on the selector while the microphone
  check finishes and while the model becomes ready; confirm each changed status
  is announced once without moving focus. Repeat for an update completion or
  failure and a Settings save. During a download, confirm changing byte counts
  remain readable on demand but do not repeatedly interrupt Narrator.
- On a layout with AltGr (for example Polish or German), confirm typing an
  AltGr character does not begin dictation. In Setup, select **F8**, confirm the
  instructions update and the key works immediately, choose **Set Up Later**,
  restart, and confirm **F8** remains selected. Repeat the selector with
  Narrator and keyboard-only navigation.
- With a text editor focused, select **Left Win** and dictate in both hold and
  toggle modes; confirm the Start menu never opens and the transcript returns
  to the original editor. Repeat with **F11** in an app that normally assigns
  F11 and confirm that app command is not invoked. Confirm unrelated keys still
  work normally while Presspeech runs.
- Set Windows **Text size** to 225% and use a 1024 x 768 display (or VM). Confirm
  Setup, Settings, and the update prompt stay within the desktop, expose
  scrollbars when needed, and automatically scroll each control into view
  while navigating with Tab and Shift-Tab. Resize all three windows and repeat
  with Narrator enabled.
- At 100%, 150%, and 225% display scaling, start and stop a dictation. Confirm
  the **Listening…** and **Transcribing…** indicator text is not clipped, the
  surface remains above the taskbar on the active display, and it never takes
  keyboard focus or intercepts pointer input.
- Apply each Windows contrast theme in turn and confirm the dictation indicator
  uses the theme's selected-text colour pair and remains fully opaque. Toggle a
  contrast theme during an active recording and confirm the visible indicator
  updates without another hotkey press; its text must continue to distinguish
  **Listening…** from **Transcribing…** without relying on colour.
- While the model is preparing, confirm **Try Dictation** and **Finish Setup**
  remain disabled. Choose **Set Up Later**, restart, and confirm setup opens
  again with the selected microphone and Start with Windows choice preserved.
  Confirm the chosen autostart state is reflected under Task Manager **Startup
  apps**. After the model reaches Ready, confirm both actions become available.
- Confirm **Finish Setup** remains available if the microphone will be connected
  later; microphone readiness is advisory rather than a completion gate.
- Close every Presspeech window while leaving the notification-area process
  running, then launch Presspeech from the Start Menu. Before setup completion,
  confirm Setup opens; after completion, confirm Settings opens. Minimize each
  window and launch again, confirming the existing window is restored and
  foregrounded rather than a duplicate being created. Repeat while the
  notification-area icon is in overflow.
- With Setup, Settings, Try Dictation, and the update prompt open in turn,
  select the same notification-area command or launch Presspeech again and
  confirm the existing window is restored instead of being ignored.
- Begin recording in both hold and toggle modes, press **Escape**, and confirm
  capture stops, muted playback is restored, and no transcription is pasted or
  copied. Repeat with **Cancel Dictation (Esc)** in the notification-area menu.
- Tap the hotkey too briefly to produce a usable recording, then make a longer
  silent recording. Confirm both leave **No speech detected — try again** on
  the indicator briefly and issue a Windows notification with microphone-check
  recovery instead of disappearing silently. Retry immediately and confirm the
  new **Listening…** state is not hidden when the old message expires. Repeat
  with the visual indicator disabled and confirm the notification remains.
- Open Notepad normally and confirm dictation pastes automatically. Then open a
  separate Notepad instance with **Run as administrator**, dictate into it, and
  confirm Presspeech leaves the transcript on the clipboard, sends no simulated
  paste shortcut, and reports the Windows administrator boundary. Paste manually
  and confirm the complete transcript is available. Do not elevate Presspeech.


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
- Enable **Show in Dock** and confirm the macOS application menu contains
  **About Presspeech**, **Settings…**, Services, Hide, and Quit, plus standard
  **Edit** and **Window** menus. Choose **Settings…** and confirm the current
  Dictation, Text, and Behavior settings hierarchy opens at the Presspeech
  status item (or at the active window if the menu-bar item is crowded out).
  With a Presspeech window active, press Command-comma and confirm it opens the
  same hierarchy with current checkmarks and disabled states.
- In Try Dictation and the dictionary manager's search field, confirm
  Command-Z, Shift-Command-Z, Command-X/C/V, and Command-A match the enabled
  Edit-menu commands and act on the focused text. With each Presspeech utility
  window active, confirm Command-W closes it, Command-M minimizes it, and the
  Window menu lists open Presspeech windows without quitting the menu-bar app.
- Resize Setup Checklist vertically and confirm the checklist rows scroll while
  **Show in Dock**, **Try Dictation** (when ready), and **Done** remain visible.
- Reach runtime and permission readiness without pressing the configured
  hotkey. Confirm its row says **Ready to test** and the footer still says
  **Close**, not **Done**. Press the hotkey and confirm the row changes to
  **Detected** and the footer changes to **Done**. Start dictation from the menu
  on a fresh launch and confirm that action does not falsely mark the hotkey as
  detected.
- While the checklist is short enough to scroll, leave it on the lower
  permission or hotkey rows as model progress or a permission state changes.
  Confirm the live refresh keeps the same scroll position instead of jumping
  back to **Speech model**.

- On a display whose usable height is less than 700 points, confirm Setup
  Checklist opens wholly inside the visible screen and every setup row remains
  reachable by scrolling or keyboard navigation.
- Enable **Settings → Behavior → Launch at Login**, then turn Presspeech off in
  **System Settings → General → Login Items & Extensions**. Confirm the app's
  setting changes to **Launch at Login (Approval Required)** and selecting it
  opens Login Items instead of removing the pending login item. Approve it,
  reopen the Presspeech menu, and confirm the setting is on.
- Confirm **Support -> Copy Diagnostics** copies a report with no
  transcript text or text-correction contents.
- Confirm **Support -> Save Diagnostics...** writes the same privacy-safe
  report.

## Hotkeys

- In **Settings -> Hotkey**, choose **Right Option** and dictate once.
- Record an F-key such as F7 with **Record Hotkey...**. Confirm the dialog
  previews F7 without closing or changing the current setting, then choose
  **Use Selected** and dictate once. Repeat using only Tab and Return.
- Record a right-side modifier such as **Right Control**, then dictate once.
- Try recording a normal letter key and confirm it is rejected.
- In the hotkey recorder, confirm Escape cancels and Tab, Space, and Return
  continue to operate the dialog controls rather than being rejected as keys.
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
- In an Electron/Chromium app such as VS Code, open two separate windows with
  editable fields. Dictate without leaving the first window and confirm the
  text is pasted automatically rather than falling back to **Copied — press
  ⌘V to paste**. Start another dictation in the first window, move to the
  second window before transcription finishes, and confirm Presspeech leaves
  the text on the clipboard instead of pasting into that same-process window.
  This is the acceptance check for the accessibility-focus gap tracked in
  [issue #33](https://github.com/rcourtman/presspeech/issues/33).
- Dictate silence long enough to pass the short-clip cutoff and confirm the HUD
  and menu report **No speech detected — try again** rather than playing the
  successful-dictation cue.
- Enable **Restore clipboard after paste**, trigger the focus-change path, and
  confirm the transcript remains available for manual paste rather than being
  replaced by the old clipboard contents.
- Test hold mode: hold the hotkey, speak, release, and confirm text pastes
  at the cursor.
- In hold mode, release directly on the last consonant of several short
  phrases and confirm the final word is retained. Repeat with quiet room tone
  and with steady background noise; the quiet case should begin transcription
  promptly, and ongoing noise must never hold capture more than about 0.4
  seconds after release.
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
- Confirm **Mute system audio while recording** stays muted through the short
  post-release capture window, then unmutes after transcription starts; cancel
  must still unmute immediately.

## Dictionary And Shortcuts

- Enable **Settings → Text → Spoken formatting commands** with Language Hint
  set to English. Dictate “first line new line second line question mark” and
  confirm the exact output has a newline and ends in `?`.
- Set Language Hint to French. Dictate “bonjour virgule nouvelle ligne monde
  point d’interrogation” and confirm the exact output is `bonjour,`, a newline,
  then `monde?`. Confirm the same French command words remain literal when the
  Language Hint is English.

- Enable **Settings → General → Show in Dock**, open **Dictionary &
  Shortcuts**, and verify the application menu includes **Edit** and **Window**.
  In the search field and correction editor, verify Command-A, Command-C,
  Command-V, Command-Z, and Command-Shift-Z reach the focused control and that
  unavailable commands are disabled. Verify Command-M minimizes the manager
  and that its window can be raised again from the Window menu.
- Add several rules, double-click a row in **Dictionary & Shortcuts**, and
  verify the clicked rule opens for editing. Resize columns until text is
  truncated and verify hovering a cell reveals its complete value.
- Open **Settings → Text → Dictionary & Shortcuts → Manage Dictionary &
  Shortcuts…**. Confirm the search field receives focus and the Heard / When
  you say and Paste columns resize with the window.
- Import or add at least 21 rules. Confirm the menu shows the saved count and
  manager instruction instead of a submenu for every rule; smaller sets of up
  to 20 still expose their existing direct Edit/Delete submenus.
- Add a replacement containing an accented name such as `Szypański`. Search
  for `szypanski` and confirm the rule remains visible. Search with terms split
  between the Heard and Paste columns and confirm all terms must match.
- Select one row, edit it, and confirm it remains selected when it still
  matches the search. Select several rows, delete them, and confirm the count,
  table, menu, and configured sync file (when enabled) stay aligned.
- Navigate the manager with Full Keyboard Access and VoiceOver. Confirm the
  search field, table/columns, selection, and Add/Edit/Delete buttons have
  meaningful names and disabled states.
- Select one row, then open its shortcut menu with Control-click and with
  VoiceOver's VO-Shift-M. Confirm **Edit…** edits that row. Select multiple
  rows and confirm their shortcut menu preserves the selection and offers
  **Delete N Items** with the same confirmation as the Delete button.
- Start adding a correction, leave each field empty in turn, and choose Save.
  Confirm the editor stays open, preserves both drafts, explains the specific
  missing field, and returns keyboard focus there. Repeat with VoiceOver and
  confirm the validation message is announced without closing the editor.
- Paste more than 512 bytes into Heard / When you say and more than 4096 bytes
  into Paste. Confirm Save keeps the editor open and identifies the applicable
  limit rather than silently dropping the item. At 512 saved items, confirm a
  new phrase is rejected with recovery guidance while an existing phrase can
  still be updated.

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
`experiments/swift-bench/real-audio/`; its generated multi-window public corpus
is required so the release check cannot silently exercise only short speech.
The wrapper also fails closed unless the app and benchmark package pin the same
FluidAudio revision. During an intentional candidate-API pin, use
`--include-candidate-models --allow-candidate-dependency` only for candidate
evidence; restore the exact app pin before recording a production release pass.
