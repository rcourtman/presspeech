# Manual QA Checklist

Run this against the exact candidate artifacts before publishing a release.
Portable unit tests and package-import smoke tests do not establish native
microphone, keyboard-hook, focus, accessibility, installer, or update behavior.

Copy the applicable section into a dated private qualification record. Mark
every check **Pass**, **Fail**, **Blocked**, **Not run**, or **Not applicable**,
and add a concise observation for failures, blocked checks, and exclusions. Do
not put transcript text, audio, dictionary contents, user or computer names,
private paths, or credentials in the record.

## Public Release and Support Qualification

The repository, GitHub Pages site, release assets, and GitHub repository
settings form one onboarding path. Source checks cannot prove that the public
release or support controls are usable. Record these checks for both platform
releases, using a signed-in GitHub account that is **not** a repository
collaborator where noted:

- Before publication, confirm the deployed install guides and published
  pointers (including `releases/latest` for macOS) still resolve to the
  preceding published release. A source candidate may be ahead, but Pages must
  not expose its pinned URL, checksum, version, or structured data yet.
- After publication, run
  `python3 scripts/check-public-releases.py --require-published` with read-only
  GitHub API access. Confirm the deployed platform guide names the new version
  and that its pinned package and checksum downloads succeed in a clean browser.
- From the non-collaborator account, open the Bug report, Feature request, and
  Target-app compatibility report templates. Confirm each form renders and can
  be filled without an “issue creation is restricted” message; do not submit a
  test report. Also confirm the private vulnerability-reporting route reaches
  its private form rather than a public issue composer.
- Activate **Report a Problem…**, **Suggest an Improvement…**, and, when the
  build contains it, **Test App Compatibility…** from the installed candidate.
  Confirm each opens the documented fixed destination without app, version,
  diagnostics, or user data in the URL.
- On the target-app compatibility page, complete all eight worksheet outcomes.
  Confirm **Copy report block** and **Download report draft** become available
  only when complete. Copy must contain only the six aggregate counts and
  overall classification; the downloaded plain-text draft must add blank
  prompts for public versions and generic target context without prefilled app
  identity or version values. Neither action may include phrases or
  transcripts. Reset the worksheet and confirm selections are not restored
  after reload.
- In a signed-out browser, confirm the repository About description and topics
  expose both the released macOS app and Windows prerelease instead of
  presenting a Mac-only project. Confirm the Pages home page, Get started,
  macOS, Windows, Help, and Privacy routes are reachable from the primary
  navigation at desktop width, 360 CSS pixels, and 200% zoom.

A restricted public issue form is a failed support path, even when existing
issues remain readable and the templates in source are valid. A candidate is
not publicly qualified while install metadata is ahead of its assets or every
documented feedback route is unavailable to the users it asks to report.

## Windows Release Qualification

Record enough context to make each result reproducible:

- candidate version and commit; installer filename, byte size, and SHA-256;
- Authenticode status and publisher (or explicitly **Unsigned**);
- Windows edition, version, and OS build; x64 CPU; GPU and driver when present;
- clean/new profile or upgrade state, display scale, text scale, keyboard
  layout, selected microphone, selected model, and CPU or CUDA inference path;
- target application name and version for insertion checks.

Complete the full **Windows First Run** checklist across these configurations
and identify which configuration supplied every result. For a 0.1.13 candidate
or later build containing retained-dictation recovery, also complete the full
**Windows delivery recovery** checklist on every required configuration. Repeat the
install, model, microphone, hotkey, core delivery, recovery, and sleep/resume
paths on each. A run may cover only one row; do not combine partial results from
different artifacts or machines into a claimed end-to-end pass.

Windows 10 remains a documented compatibility path only for editions that
still receive security updates, including devices covered by active Extended
Security Updates where required. Microsoft ended general Windows 10 support
on [14 October 2025](https://support.microsoft.com/en-us/windows/deployment/updates-lifecycle/windows-10-support-has-ended-on-october-14-2025).
To retain the Windows 10 compatibility claim, qualify both Windows 10
configurations below as well as both Windows 11 configurations; otherwise
explicitly narrow the supported scope before promotion.

| Required configuration | Install state | Inference path | Result |
| --- | --- | --- | --- |
| Windows 11 x64 without usable CUDA | clean profile | `base.en` on CPU | |
| Windows 11 x64 with a supported NVIDIA GPU | clean profile | default Parakeet model on CUDA | |
| Windows 10 x64 without usable CUDA, on an edition still receiving security updates | clean profile | `base.en` on CPU | |
| Windows 10 x64 with a supported NVIDIA GPU, on an edition still receiving security updates | clean profile | default Parakeet model on CUDA | |

For each configuration, also record this release-gate matrix:

| Required path | Result |
| --- | --- |
| Verify candidate, install per-user, and launch from Start | |
| First model preparation through first successful dictation | |
| Ten consecutive dictations into Notepad | |
| Ten short-phrase onset checks across hold/toggle; first word or syllable retained | |
| A physical hold across the OS key-repeat interval remains one capture until release | |
| Dictation into a current Chromium browser text field | |
| Dictation into a current Electron application text field | |
| Focus-change clipboard recovery and elevated-target recovery | |
| Locked/replaced clipboard recovery with notifications disabled and tray icon in overflow | |
| Explicit recovery Copy, Discard, Leave Waiting, and Exit behavior | |
| Clipboard History exclusion (and Cloud Clipboard exclusion when a disposable paired device is available) | |
| Microphone disconnect/reconnect rescan and in-flight selection change | |
| Microsoft Remote Desktop and Moonlight insertion routes | |
| Sleep/resume, then microphone, hotkey, and dictation recovery | |
| In-place candidate install over the preceding public Windows release | |
| In-app update, cancelled/failed update recovery, uninstall, and reinstall | |
| Keyboard-only, Narrator, and Accessibility Insights checks | |
| Windows 11 Voice Access operation of named controls and menu-based dictation | |
| High-contrast themes across Setup, Settings, Update, Delivery Recovery, and Try Dictation | |

A **Fail**, **Blocked**, or **Not run** result in either table blocks promotion
from prerelease to stable. An unsigned candidate also remains a prerelease.
Keep the build a prerelease until the missing native evidence is completed or
the supported scope is explicitly narrowed. This is an evidence gate, not a
request to bypass SmartScreen or managed security policy. Microsoft
[notes that unsigned apps may be blocked entirely](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)
by policy or Smart App Control; pursue trusted signing rather than treating an
override as a successful install path.

For the recovery rows, record the checklist step and aggregate outcome, not the
synthetic phrase or clipboard contents. A CI or unit-test pass is not a result
for these rows. The opt-in native test can check one synthetic clipboard write
on a disposable Windows runner, but it does not exercise the packaged
candidate, Clipboard History or Cloud Clipboard UI, input injection,
notification settings, or packaged UI. If Cloud Clipboard cannot be tested
safely with a disposable
paired device, mark only that sub-check **Not applicable** with the reason; do
not use it to waive local Clipboard History exclusion.

Before the interaction checks, run
[Accessibility Insights for Windows](https://learn.microsoft.com/en-us/windows/win32/winauto/accessibility-testingtools)
FastPass on each Presspeech window. Use Live Inspect to verify the UI Automation
name, role, value, state, and action of every control, then retain only the
privacy-safe pass/fail summary. Microsoft recommends both programmatic and
keyboard access testing in addition to assistive-technology testing.

## Windows First Run

- On each test PC, set `$installer` to the exact candidate path, record
  `(Get-Item $installer).Length`, run
  `Get-FileHash $installer -Algorithm SHA256`, and run
  `Get-AuthenticodeSignature $installer`. Confirm the size and hash match the
  candidate manifest and the signature result matches the stated signing
  status. Stop on any mismatch. Do not weaken SmartScreen, Smart App Control,
  or organisation policy to make the installer run.
- Install for the current user and confirm Windows **Installed apps** shows the
  candidate version, the Start Menu shortcut launches that installed copy, and
  no separate Python installation is needed.
- On a clean Windows profile, launch the packaged app and confirm the setup
  window does not open the microphone until **Check Microphone** is activated,
  and model status continues updating while no check is running. Confirm
  **Start Presspeech with Windows** is initially
  unchecked. Opt in, defer setup, and verify the saved choice is registered;
  then turn it off and confirm the choice remains off after completing setup.
- On a clean Windows x64 profile with usable NVIDIA CUDA and no cached pinned
  Parakeet snapshot, confirm Setup identifies the multilingual path and its
  approximate 2.5 GB first download. With keyboard-only navigation and Narrator,
  verify the **Download Parakeet model**, **Use English-only CPU model**,
  and **Choose another model in Settings** actions have useful names, appear
  after the microphone controls in Tab order, and can be activated. Confirm
  they are absent when the model is already cached. Before activating any model
  choice, confirm no model download has started. Choose **Set Up Later** and
  confirm setup remains incomplete and no model download continues; reopen
  Setup and verify the decision is offered
  again. In a separate clean test profile, choose the CPU option and confirm
  the selected model is English-only Whisper base.en (~141 MiB). In another
  clean profile, choose the multilingual download and confirm it starts only
  after that choice. With a complete pinned Parakeet snapshot already cached,
  confirm startup loads it without asking to download it again.
- While Setup is awaiting the model choice, press the configured dictation
  hotkey. Confirm Setup is presented again and Presspeech does not open the
  microphone or claim to be listening.
- With microphone access enabled, activate **Check Microphone**, speak during
  the check, and confirm the status changes from **Listening — speak a few
  words…** to **Ready — input level detected**. Change the selected input and
  confirm it says **Not checked** without opening the device; activate **Check
  Microphone** to test the newly selected device. After the model is ready, use
  **Try Dictation** and confirm it captures that selected device before setup
  is finished. Repeat by changing the selected input while a check is active;
  confirm completion leaves the new input **Not checked** and does not start a
  second check until you activate **Check Microphone**.
- On at least one configuration, select an input that diagnostics reports at
  44.1 or 48 kHz. Start a toggle-mode recording, open Settings while continuing
  to speak, change the microphone selection, save, and then stop recording.
  Confirm the complete transcript is at normal speed with its final words
  present. Confirm the next dictation uses the saved selection; switching
  between Automatic and that explicit input is sufficient when only one safe
  physical microphone is available.
- Mute the selected microphone, choose **Check Microphone**, and confirm setup
  says it is connected but no input level was detected instead of claiming it
  is ready. Unmute it, speak, and check again successfully.
- Select a specific USB or Bluetooth microphone, disconnect and reconnect it
  while Presspeech remains open, then choose **Check Microphone**. Confirm the
  check detects input without a dictation attempt or app restart and the
  selector no longer labels the input unavailable. Reopen Setup and Settings while it is
  disconnected and confirm the unavailable device remains selected; saving or
  deferring setup must not switch it to Automatic or another microphone. While
  it is disconnected, connect or enable a different input so Windows can
  reorder device indexes, then try dictation. Confirm Presspeech does not
  capture from the substitute; reconnect the selected input and confirm the
  next dictation uses it.
- Turn off **Let desktop apps access your microphone**, choose **Check
  Microphone**, and confirm setup reports that the microphone could not be
  opened without exposing raw device or PortAudio errors.
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
- From each initial focus, traverse every enabled control with Tab and then
  Shift-Tab. Confirm focus follows the visible reading order in both directions;
  in Setup's final row it must move through **Try Dictation**, **Retry Speech
  Model** when enabled, **Set Up Later**, then **Finish Setup**, without jumping
  right and back left.
- Invoke **Check Microphone**, **Retry Speech Model**, and **Download Update**
  using the keyboard. While each operation is active, confirm focus moves to the
  microphone or model selector, or to **Later**, before the invoked command is
  disabled. When model readiness changes with **Retry Speech Model**, **Try
  Dictation**, or **Finish Setup** focused, confirm focus moves to **Set Up
  Later** before the command becomes unavailable; focus must not remain on a
  disabled command. Repeat these transitions with Narrator and confirm Tab and
  Shift-Tab continue from the announced usable control.
- With Narrator, confirm the microphone selector and both recovery buttons
  expose meaningful names. Leave focus on the selector while the microphone
  check finishes and while the model becomes ready; confirm each changed status
  is announced once without moving focus. Repeat for an update completion or
  failure and a Settings save. During a download, confirm changing byte counts
  remain readable on demand but do not repeatedly interrupt Narrator.
- With Voice Access running on Windows 11, separately test spoken interaction
  with Setup, Settings, Try Dictation, and the notification-area **Dictate**
  action. Use the controls' names or Voice Access number overlays; start and
  stop a harmless scratchpad dictation through **Dictate**, and test whether
  the action remains reachable when the Presspeech icon is in notification-area
  overflow. Confirm the configured microphone and controls remain usable while
  Voice Access is active, and that ordinary test text does not trigger an
  unintended Voice Access command. The global hotkey deliberately ignores
  Windows-injected key events, so a spoken **Press and hold [key]** command is
  not a substitute for testing the menu/control route. Record any point Voice
  Access cannot operate as a limitation; do not infer support from unit tests
  or UI Automation names alone. Use Microsoft's [Voice Access overview](https://support.microsoft.com/en-us/accessibility/windows/voice-access/use-voice-access-to-control-your-pc-author-text-with-your-voice)
  and [screen-item interaction guidance](https://support.microsoft.com/en-us/accessibility/windows/voice-access/use-voice-to-interact-with-items-on-the-screen)
  for supported commands and alternatives.
- On a layout with AltGr (for example Polish or German), confirm typing an
  AltGr character does not begin dictation. In Setup, select **F8**, confirm the
  instructions update and the key works immediately, choose **Set Up Later**,
  restart, and confirm **F8** remains selected. Repeat the selector with
  Narrator and keyboard-only navigation.
- Before finishing Setup, select **Press to toggle** and confirm its instructions
  update immediately. Choose **Set Up Later**, restart, and confirm both the
  style and instructions remain selected. When the model is ready, use **Try
  Dictation** and confirm one hotkey press starts and the next stops. Switch to
  **Hold to talk** in Setup and confirm the next dictation stops on release.
  Repeat both radio buttons with Narrator and keyboard-only navigation.
- On each required CPU and NVIDIA configuration, use hold mode with a physical
  key press held beyond the Windows key-repeat delay. Confirm exactly one
  **Listening…** session remains active until physical release and then stops
  once; no autorepeat may restart or cancel the capture. Test the configured
  default key and, on a layout where Right Alt acts as AltGr, the selected
  alternative key. Record only pass/fail, not dictated content. The model-free
  autorepeat test does not replace this native input check.
- On each clean CPU and NVIDIA configuration, use **Try Dictation** to repeat
  one short, harmless phrase ten times, alternating five hold-mode and five
  toggle-mode attempts. Begin speaking immediately after the visible
  **Listening…** cue. Compare each result privately with the expected phrase
  and record only the aggregate number with the complete opening word/syllable
  and the number with any opening loss. Do not retain the phrase, transcript,
  or audio in the qualification record. Any opening loss blocks qualification
  pending local diagnosis; distinguish capture timing from recognition variation
  before changing inference policy.
- With a text editor focused, select **Left Win** and dictate in both hold and
  toggle modes; confirm the Start menu never opens and the transcript returns
  to the original editor. Repeat with **F11** in an app that normally assigns
  F11 and confirm that app command is not invoked. Confirm unrelated keys still
  work normally while Presspeech runs.
- Set Windows **Text size** to 225% and use a 1024 x 768 display (or VM). Confirm
  text in Setup, Settings, the update prompt, Delivery Recovery, and Try
  Dictation grows with the setting while each window stays within the desktop.
  Change Text size while Setup or Settings is open and confirm its text follows
  without restarting Presspeech. Confirm the structured dialogs expose
  scrollbars when needed and automatically scroll each control into view while
  navigating with Tab and Shift-Tab; confirm Try Dictation exposes a visible
  transcript scrollbar and keeps its editor, status, and Dictate command usable.
  Resize every window and repeat with Narrator enabled.
- At 100%, 150%, and 225% display scaling, start and stop a dictation. Confirm
  the **Listening…** and **Transcribing…** indicator text is not clipped, the
  surface remains above the taskbar on the active display, and it never takes
  keyboard focus or intercepts pointer input.
- At each 100%, 150%, and 225% Windows **Text size** value, start a dictation
  and confirm the indicator text grows with the setting without clipping.
  Change Text size while **Listening…** remains visible and confirm the text
  and indicator bounds update without another hotkey press or app restart.
- Apply each Windows contrast theme in turn and confirm the dictation indicator
  uses the theme's selected-text colour pair and remains fully opaque. Toggle a
  contrast theme during an active recording and confirm the visible indicator
  updates without another hotkey press; its text must continue to distinguish
  **Listening…** from **Transcribing…** without relying on colour.
- With each Windows contrast theme, inspect Setup, Settings, Update, Delivery
  Recovery, and Try Dictation. Confirm text, controls, focus, selection, and
  disabled states remain discernible, and that status or error meaning is not
  conveyed by colour alone. Change the theme while each window is open and
  confirm it repaints legibly; repeat keyboard-only and Narrator navigation.
  Record any inaccessible or ambiguous state as a failure rather than
  inferring support from the indicator's custom palette.
- In Setup, focus each Parakeet/CPU/other-model choice and let its state move
  from awaiting consent to preparing; focus should advance to **Dictation
  hotkey**, not jump back to the microphone picker. Focus **Retry Speech Model**,
  **Try Dictation**, or **Finish Setup** before each becomes unavailable and
  confirm focus moves to adjacent **Set Up Later**. Repeat keyboard-only and
  with Narrator; verify the destination control and status remain understandable.
- While the model is preparing, confirm **Try Dictation** and **Finish Setup**
  remain disabled. Choose **Set Up Later**, restart, and confirm setup opens
  again with the selected microphone, dictation style, and Start with Windows
  choice preserved. Confirm the chosen autostart state is reflected under Task
  Manager **Startup apps**. After the model reaches Ready, confirm both actions
  become available.
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
- Begin a recording with Settings open and change several controls without
  saving. Confirm **Save** is disabled and Ctrl+S does not apply any change
  during recording, transcription, or delivery. Confirm the status announces
  each wait state, then announces that saving is available when delivery ends;
  the pending edits must remain available and save normally afterward.
- Begin recording in both hold and toggle modes, press **Escape**, and confirm
  capture stops, muted playback is restored, and no transcription is pasted or
  copied. Repeat with **Cancel Dictation (Esc)** in the notification-area menu.
- Start recording with **Try Dictation** focused, then close that window with
  its title-bar close control. Confirm capture is cancelled, muted playback is
  restored, the listening indicator disappears, and no hidden recording or
  transcription continues. Repeat while Try Dictation is open but a recording
  belongs to Notepad; closing the scratchpad must not cancel that recording.
- In hold mode, release directly on the final consonant of several short
  phrases; in toggle mode, press the hotkey at the same boundary. Confirm the
  final word is retained. Repeat with quiet room tone and steady background
  noise: quiet input should begin transcription promptly, and ongoing sound
  must never hold capture more than about 0.4 seconds after the stop gesture.
- On the NVIDIA Parakeet path, dictate a human-reviewed passage longer than 60
  seconds with no long pause at the internal boundaries. Confirm the complete
  passage arrives once, without joined/split words or duplicated phrases, and
  that the privacy-safe log reports `backend=parakeet`, multiple chunks, and
  `max_chunk` no greater than 60 seconds. Repeat at the 10-minute recording
  limit while monitoring GPU memory; transcription must stay on Parakeet and
  complete without an out-of-memory error.
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

## Windows Install and Removal

- Install the packaged app on a clean Windows profile, clearing **Launch
  Presspeech** on the final installer page. Confirm installation does not create
  a Presspeech entry under **Settings → Apps → Startup** before the app has run
  and the first-run choice has been saved.
- Complete or defer Setup with **Start Presspeech with Windows** selected. Confirm
  the enabled Presspeech startup entry launches the installed executable, then
  install the next build over it and confirm the choice remains enabled.
- Remove Presspeech through **Settings → Apps → Installed apps**. Confirm
  its Start Menu and optional desktop shortcuts, install directory, and
  **Start with Windows** entry are gone. Sign out and back in and confirm Windows
  does not attempt to launch the removed executable. Preferences, diagnostics,
  and shared Hugging Face model files are expected to remain.

## macOS Release Qualification

Qualify the notarised candidate archive, not a locally rebuilt substitute.
Record enough context to make each result reproducible:

- candidate version and commit; archive filename, byte size, and SHA-256;
- `codesign --verify --deep --strict`, `spctl --assess --type execute`, and
  `xcrun stapler validate` results for the installed candidate;
- Mac model and Apple silicon generation; macOS version and build; clean/new
  profile or upgrade state; keyboard layout; selected microphone; and whether
  VoiceOver, Full Keyboard Access, or Voice Control was enabled;
- target application name, version, app class (native, browser, Electron,
  remote desktop), generic field type, and whether one or two windows were
  used. Do not record window, document, account, or server names.

Cover both the oldest supported macOS release and the newest public macOS
release supported by the candidate. Use a clean or independently reset TCC
profile for at least one run. A single run may satisfy more than one row, but
do not combine partial results from different candidate archives into an
end-to-end pass.

| Required configuration | Install state | Result |
| --- | --- | --- |
| Apple silicon on macOS 14 | clean profile or independently reset TCC grants | |
| Apple silicon on the newest supported public macOS | clean profile or independently reset TCC grants | |
| Either configuration | upgrade from the preceding public Presspeech release | |

Record this release-gate matrix against the exact installed candidate:

| Required path | Result |
| --- | --- |
| Verify signature, notarisation, staple, version, archive size, and SHA-256 | |
| First launch through model, microphone, Accessibility / Device Control and Data Access, Input Monitoring, and keyboard-event-posting readiness | |
| Clean first model download with synthetic values in all inherited Hugging Face token variables | |
| Ten consecutive dictations into TextEdit with the previous-clipboard option off | |
| Ten consecutive dictations into a current Electron/Chromium target with the previous-clipboard option off | |
| Automatic insertion and clipboard-only Command-V recovery with a non-US keyboard layout | |
| Electron issue #33: steady-focus paste once; a switch between two windows of the same app must use clipboard recovery | |
| Ten TextEdit and ten slow Electron manual-restore trials for issue #36 | |
| Custom hotkey in hold and toggle modes on two keyboard layouts | |
| Hotkey conflict rejection, persistence, Full Keyboard Access, and VoiceOver checks for issue #34 | |
| Focus-change recovery between native-app windows and between applications | |
| Sleep/resume, microphone route change, and first dictation afterward | |
| In-place upgrade with preferences, hotkey, and TCC grants retained | |
| Setup and Try Dictation with VoiceOver, keyboard-only navigation, and Voice Control menu start/stop before the hotkey is tested | |
| Menu-bar/Dock access and dictation recovery with VoiceOver and keyboard-only navigation | |

For the synthetic-credential row, use a disposable macOS profile or VM with no
Hugging Face login/cache. Launch the installed candidate executable directly
with nonfunctional marker values in `HF_TOKEN`, `HUGGING_FACE_HUB_TOKEN`, and
`HUGGINGFACEHUB_API_TOKEN` (do not use or inspect real token values). Confirm
the normal first-download choice appears, the public model download succeeds,
and any local log mentions variable names only—not marker values. Do not copy
raw logs or capture/share request headers. This packaged-app check complements
`swift run Presspeech --self-test hostile-env`, which exercises actual process
environment removal and Foundation visibility with synthetic values.

For issue #33, pass only if a steady-focus Electron target receives the complete
transcript once, while switching to a second window of that same app before
delivery inserts nothing and leaves the complete transcript available for
manual paste. A matching process identifier alone does not authorize paste;
any insertion after the window switch is a failure.

For the non-US keyboard-layout row, compare an English/US input source with at
least one non-US source (preferably one with different character mappings, such
as Russian, when available). In TextEdit and a current Electron/Chromium target,
confirm a steady-focus dictation inserts once under each layout. Then cause a
clipboard-only recovery by changing focus before delivery and confirm physical
Command-V pastes that transcript once under each layout. A displayed hotkey
label or successful physical-hotkey test alone does not establish that
synthetic paste and manual recovery work. Record only the input-source names
and aggregate outcomes; do not retain dictated phrases, transcripts, or field
contents.

For the keyboard-only delivery-recovery check, use a harmless test transcript
and create a clipboard-only delivery outcome, then open each menu using the
keyboard: first the status-item menu, then the Dock menu with **Show in Dock**
enabled. Traverse the available actions in both directions with the keyboard.
Confirm **Copy Last Transcript** is reachable in the action order after the
dictation control, is enabled when retained history is available, and copies
the latest transcript when activated. Confirm any microphone recovery action
is likewise reachable when shown, and that disabled or absent recovery actions
are not presented as actionable. With VoiceOver, verify the recovery notice
and action names explain what will happen without relying on color or the
Command-key glyph. Do not record transcript contents; restore the test clipboard
only through an explicit user-confirmed action.

For the two ten-trial clipboard rows, use distinct harmless markers and record
only aggregate pass/fail counts. Observe that the intended field consumed each
transcript before explicitly restoring the old clipboard; posting Command-V or
waiting a fixed interval is not evidence of consumption. A stale, partial,
duplicate, misdirected, or unrecoverable result is a failure, not a retry to
exclude from the denominator.

Run the opt-in debug fixture in
[`native-interaction-qa.md`](native-interaction-qa.md) from the candidate commit
and retain its fixed PASS/FAIL lines. The fixture establishes production event
tap and controlled AppKit-window behavior, but its debug executable is not the
candidate artifact and cannot replace the external Electron, physical keyboard,
assistive-technology, microphone, or packaged-app rows above.

A **Fail**, **Blocked**, or **Not run** result prevents claiming the affected
delivery, clipboard, hotkey, or accessibility gate as qualified. Preserve that
status until the same artifact passes or the supported scope is explicitly
narrowed; publication or a passing source fixture does not turn missing native
evidence into a pass.

## macOS App Checklist

For source-level iteration only, build and launch the development wrapper:

```sh
cd swift
./dev-run.sh
```

This command does not qualify a release archive. For release qualification,
install the exact candidate recorded above and start with the first checklist
item after the development-wrapper launch check.

- Confirm `/tmp/Presspeech-dev.app` launches and the menu-bar item appears.
- Open **Support -> Setup Checklist...** and confirm model, permissions,
  audio input, and hotkey rows render.
- On a clean user profile without a speech-model cache, launch Presspeech and
  confirm Setup Checklist appears before the first model request. The model row
  must say **Not downloaded**, explain the ~500–600 MB transfer and the
  free-space estimate for download plus CoreML preparation, state the
  local-audio/transcript boundary, and expose **Download Model**. Confirm that
  this estimate is visible before activating the action, not only after a
  failed disk-space check. With VoiceOver and keyboard-only navigation, confirm
  the action has a useful name and is reachable. Close the
  checklist without activating it; confirm no model download starts and the app
  remains not ready. Reopen Presspeech and confirm the choice is still offered
  without starting a download. Activate **Download Model** and confirm the
  transfer starts only then, the changing progress value remains available in
  its row without replacing the VoiceOver reading element, and readiness
  proceeds normally. Progress-only refreshes should not trigger repeated
  app-wide announcements. Interrupt an approved download and relaunch; confirm
  it resumes without asking again.
- Upgrade a profile from the preceding public macOS build with a valid cached
  model and confirm it loads automatically without a first-download prompt.
- Select a specific USB or Bluetooth microphone, disconnect it while Presspeech
  remains open, and confirm Setup Checklist reports **Using default** (not
  **Configured**) while explaining that the saved microphone is unavailable and the
  system default is in use. Choose another input, reconnect the saved device,
  select it again, and confirm the row returns to **Configured** with the
  selected input identified and instructions to use **Try Dictation** to check
  that sound reaches Presspeech. Do not record device names in qualification
  notes.
- After model and permission checks are complete, choose **Try Dictation** and
  speak into the scratchpad. Confirm the transcript appears there; the
  checklist's **Configured** status must not be treated as proof that audio
  samples have been received. If a test input returns no samples, confirm the
  existing no-audio recovery directs the user back to microphone selection.
- Enable macOS keyboard navigation for controls (or Full Keyboard Access),
  then navigate the checklist using Tab and Shift-Tab. Activate a permission
  action that remains missing and changes to **Try Again**;
  confirm keyboard focus remains on that replacement action when the row is
  redrawn.
  Continue through the complete loop in both directions after each redraw;
  confirm no removed action retains focus and no current permission, Dock,
  Try Dictation, or Close/Done control is skipped.
- With VoiceOver enabled, confirm permission actions describe their route and
  target: **Continue to request Microphone access** before the first microphone
  prompt, **Open Settings for Microphone** after a denial, and **Open Settings
  for Accessibility** (macOS 14) or **Open Settings for Device Control and Data
  Access** (macOS 27 and later). A repeated attempt should be announced as
  **Try again for [permission]**, not as a grant that has already happened.
- With VoiceOver enabled, navigate directly to each setup status field. Confirm
  its announcement includes both the row context and current value (for example,
  **Speech model status, Not downloaded** or **Microphone status, Missing**),
  and that the context remains correct as the value changes after a refresh.
- With VoiceOver enabled, leave focus on another checklist control while a
  permission becomes granted, then return to Presspeech from System Settings.
  Confirm the status change is announced once when Setup Checklist is active;
  **Ready to test**, recovery-needed, and completed-setup transitions should
  each have concise, useful announcements. Retry a failing model or audio setup
  with different recovery details and confirm the change is announced without
  reading the raw details aloud. Confirm announcements do not repeat on
  unchanged refreshes or interrupt the user while System Settings is in front.
- During a speech-model download, leave the VoiceOver cursor on a lower row's
  title, detail, and status in turn while the progress text changes. Confirm
  routine live refreshes keep the cursor on that element instead of resetting
  reading position to **Set Up Presspeech**. Repeat while a permission action
  changes to **Try Again** without adding or removing the row.
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
- With the default **Press and hold** mode, confirm the setup tip and the
  one-time VoiceOver readiness announcement explain that users who prefer not
  to hold the key can choose **Press to toggle** in **Settings → Dictation →
  Trigger**. Select toggle mode and confirm the setup tip reflects the active
  mode and explains how to switch back.
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
- Record **Command-comma**, confirm the preview, restart Presspeech and confirm
  the entire combination persists. A bare comma must still type normally;
  Command-comma must start/stop dictation without opening the target app's
  preferences. Repeat with Control-Option plus a letter and with Shift added.
- With **Show in Dock** enabled and Command-comma still selected, confirm the
  Settings command opens when Presspeech is active without starting dictation;
  switch to another app and confirm the same chord remains the global dictation
  hotkey there. This checks that Presspeech preserves its own App-menu command
  without disabling the user's global binding elsewhere.
- In hold mode, release modifiers before the trigger key, then repeat in the
  opposite order. Confirm dictation stops exactly once on trigger-key release.
  Hold the trigger through autorepeat and verify only one recording starts.
- Start holding the bare trigger key, then add the modifiers during repeat.
  Confirm ordinary typing is not converted into dictation midway through.
- In toggle mode, test start/stop, a declined press during model loading or
  transcription, and Escape cancellation. The next ready press must work.
- Test a second keyboard layout. Confirm the physical key remains the trigger,
  its displayed label follows the layout, and Caps Lock does not change matching.
- Try recording an unmodified letter, Shift-only letter, Escape, Command-Tab,
  and Command-Space. Confirm unsafe combinations cannot be saved, and rejection
  clears any previous modifier-only preview instead of saving that stale choice.
- Confirm VoiceOver announces accepted combinations and rejected selections;
  cancel the dialog and verify the previous binding is unchanged.
- Attempt to change the hotkey or trigger mode while recording/transcribing.
  Confirm both sets of choices are disabled until the current operation
  finishes, and that the original hotkey gesture still stops the recording.
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
- With VoiceOver enabled, navigate to the Presspeech status item and confirm
  it remains named **Presspeech** while its current value changes through
  **Ready**, **Recording**, **Transcribing**, and **Ready** again. Confirm the
  completed transcription and recovery values are available without relying on
  the icon or colour. Presspeech must not request a spoken recording-start
  announcement while the microphone is capturing; after capture ends, confirm
  the **Transcribing** and completion/recovery value changes reach VoiceOver
  without repeating on unchanged states.
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
- Stop a long harmless dictation and revoke Input Monitoring while it is still
  transcribing. Confirm Presspeech does not paste into System Settings or any
  other newly focused window, leaves the complete transcript on the clipboard,
  shows **Copied — press ⌘V to paste**, and returns to the missing-permission
  setup state. Regrant the permission and confirm a fresh dictation can paste
  automatically; the interrupted dictation must never paste later.
- For macOS 0.3.9 and later, use a disposable test account to repeat the
  in-flight revocation check separately for focused-window Accessibility and
  Quartz keyboard-event posting (the `PostEvent` TCC service). Revoke each
  grant while a harmless dictation is transcribing and leave it missing
  through completion. Confirm Presspeech does not paste that dictation and
  leaves the complete transcript on the clipboard. While access is missing,
  Setup/Diagnostics must identify the missing check. After restoring access,
  confirm the old transcript is not pasted later and a fresh dictation pastes
  normally. Record the grant and revocation timing, without recording
  transcript or target-window contents. A grant revoked and restored entirely
  before the next permission check may not be observed; do not treat this test
  as proof that every short-lived revocation is detected.
- In an Electron/Chromium app such as VS Code, open two separate windows with
  editable fields. Dictate without leaving the first window and confirm the
  text is pasted automatically rather than falling back to **Copied — press
  ⌘V to paste**. Start another dictation in the first window, move to the
  second window before transcription finishes, and confirm Presspeech leaves
  the text on the clipboard instead of pasting into that same-process window.
  This is the acceptance check for the accessibility-focus gap tracked in
  [issue #33](https://github.com/rcourtman/presspeech/issues/33).
  If the steady-focus attempt falls back, inspect the corresponding local-log
  line in `~/Library/Logs/Presspeech.log`. Record its fixed failure category:
  a `focused-window query failed (AX error …)` result means the target did not
  provide usable exact-window evidence, while `frontmost application changed
  during focused-window query` means window-server focus changed during the
  bounded lookup. Other categories must be retained verbatim for triage.
  Do not add the target name, window title, field contents, or transcript to
  that log extract. Every fallback is still an automatic-paste failure.
- Dictate silence long enough to pass the short-clip cutoff and confirm the HUD
  and menu report **No speech detected — try again** rather than playing the
  successful-dictation cue.
- Tap the hotkey too briefly to reach the short-clip cutoff and confirm the HUD,
  menu, error cue, and VoiceOver report that the recording was too short instead
  of returning silently to Ready. Retry immediately and confirm the old notice
  cannot hide the new recording state.
- In Setup Checklist, confirm **Audio input** names the saved microphone (or
  system default) and that **Choose…** opens the same current, keyboard-navigable
  device list as Settings → Dictation → Microphone. Disconnect a selected
  removable microphone while idle and confirm Setup reports the saved device as
  unavailable and the system-default fallback, without silently selecting a
  different explicit device.
- With a native test input that starts successfully but returns no tap samples,
  confirm the HUD identifies missing microphone audio and the menu says **No
  microphone audio — choose Check Microphone**. The direct **Check Microphone…**
  action must open Setup, whose Audio input row changes to **Check input** and
  exposes **Choose…**; VoiceOver must announce the same recovery instruction.
  Selecting an input must clear the stale notice before audio restarts.
- The following manual-restore checks apply to macOS 0.3.8 and later, which
  contain **Keep Previous Clipboard for Manual Restore**. They do not apply to
  legacy macOS 0.3.7's automatic restore timer.
- With **Keep Previous Clipboard for Manual Restore** off, dictate distinct
  non-sensitive markers into TextEdit and an Electron/Chromium target. Confirm
  the exact transcript lands and remains available for immediate manual paste.
- In builds containing local-only transcript clipboard writes, repeat automatic
  paste, focus-change recovery, **Copy Last Transcript**, and the Try Dictation
  scratchpad's **Copy** action. Also select part of the scratchpad and exercise
  standard Command-C and Command-X. Confirm every result remains available to
  local Command-V and that Cut removes only the selected text. With Handoff and
  Universal Clipboard enabled on a second test Apple device, confirm none of
  those unique markers appears there. If a cooperating clipboard manager
  exposes item handling, confirm it skips the transcript rather than archiving
  or visibly previewing it because the item is marked transient, auto-generated,
  and concealed; do not treat those community markers as protection from
  arbitrary local readers or macOS Clipboard History.
- Enable **Keep Previous Clipboard for Manual Restore**, seed an old harmless
  marker, and perform ten dictations each in TextEdit and a slow
  Electron/Chromium target, mixing short and multi-sentence transcripts. Verify
  each new transcript arrived before choosing **Restore Previous Clipboard…**
  and confirming. Only then should manual paste yield the original marker.
  In builds containing local-only clipboard protection, confirm the restored
  marker remains available to local Command-V but does not appear through
  Universal Clipboard on the second test device.
  No fixed delay is a substitute for observing consumption. This is the manual
  qualification boundary for [issue #36](https://github.com/rcourtman/presspeech/issues/36).
- With preservation still enabled, seed a new old marker and force
  clipboard-only recovery twice: once by changing windows during transcription
  and once in a target that does not expose exact focused-window identity.
  In each case, verify the complete transcript remains available for manual
  Command-V and **Restore Previous Clipboard…** restores the marker that
  preceded dictation. Recovery must not discard the opted-in snapshot merely
  because Presspeech declined to paste automatically.
- On macOS 15.4 or later, first trigger the system pasteboard-access prompt,
  then configure Presspeech as **Always Deny** in the corresponding System
  Settings privacy pane. With previous-clipboard preservation still enabled,
  dictate once and confirm the transcript is delivered normally, the system
  does not prompt again, and the disabled restore row says **Previous Clipboard
  Access Denied**. Confirm its help text explains that only the previous
  clipboard snapshot was skipped. Restore the system setting after this check.
- Open the confirmation and cancel it. The clipboard and offer must remain
  unchanged. Return must activate Cancel, not restoration. Confirm that a
  later explicit restore works once; repeating an old action cannot write again.
- Complete two dictations before restoring. Confirm both arrived, then restore
  and check the original marker returns, not the first transcript. Repeat five
  times. The original five-minute deadline must not renew with each dictation.
- Leave an offer untouched for five minutes. The transcript must stay on the
  clipboard and the restore action become unavailable. Expiry must not rewrite
  text, remove transient markers, or restore old contents.
- With preservation enabled, seed more than 64 MB of harmless generated text
  on the clipboard and dictate once. Confirm dictation still completes, the
  transcript remains on the clipboard, and the menu reports **Previous
  Clipboard Too Large to Keep** instead of offering a partial restore. Dictate
  again before copying anything and confirm the report remains unavailable
  rather than offering to restore the first transcript. Copy a new marker and
  confirm that report disappears without rewriting the marker.
- While a confirmation is open, finish another dictation or copy a third
  harmless marker. The old confirmation must not restore a different offer or
  overwrite the newer copy. Disabling the option, Copy Last Transcript, other
  app copy actions and quitting must retire the saved snapshot without an
  automatic restore. Test both status and Dock menus with recent history off.
- Upgrade a test settings domain with old automatic restoration enabled: the
  new manual option remains off and the timer/delay controls are gone. A fresh
  explicit choice persists; diagnostics describe manual restoration and contain
  no clipboard snapshot bytes.
- With preservation enabled, change focus before transcription finishes.
  Confirm copy-only recovery remains available and no restore is pending for
  that manual copy. A pure self-test or controlled AppKit fixture does not
  qualify external app consumption, accessibility or clipboard providers.
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
  to request Microphone, Accessibility, and Input Monitoring. On macOS 27 and
  later, confirm the app and System Settings both call the Accessibility pane
  **Device Control and Data Access** and that the legacy deep link still opens
  that renamed pane.
- Close Setup Checklist while a permission is still missing, then choose its
  permission action from the menu-bar menu. Confirm the checklist comes forward
  before the system prompt or Settings pane opens, stays available while that
  system UI is in front, and shows the updated permission state when you return
  to Presspeech. Repeat with the first microphone **Continue** action and an
  **Open Settings** action in a clean/reset profile.
- Confirm each granted permission removes or updates its setup row after the
  app is reopened if macOS requires it.
- On a managed test Mac where microphone access is restricted by macOS or
  device policy, confirm Setup Checklist labels the microphone **Restricted**,
  explains that an administrator may need to change the policy, and offers no
  permission action. The status menu should show a disabled restriction
  notice instead of resetting or re-requesting microphone access; other
  missing permission rows should remain actionable.
- On a disposable test account, independently reset the Presspeech `PostEvent`
  TCC service while leaving focused-window Accessibility available. Confirm the
  Accessibility row returns to **Missing**, dictation cannot start, and **Copy
  Diagnostics** reports focused-window access as granted but keyboard-event
  posting as missing. Choose **Open Settings** (or **Try Again** after a stale
  denial), re-enable the requested access, and confirm both diagnostic
  subchecks and the setup row return to granted before repeating a TextEdit
  paste.
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
`powermetrics` requires it. `run-release-asr-checks.sh` requires private
real-dictation clips under `experiments/swift-bench/real-audio/` by default;
keep them and their references local. Use `--allow-missing-real-audio` only for
a lightweight run, which cannot report a production release-gate pass. Its
generated multi-window public corpus is also required so the release check
cannot silently exercise only short speech.
The wrapper also fails closed unless each package manifest matches its resolved
lock and the app and benchmark pin the same FluidAudio revision. During an
intentional candidate-API pin, use
`--include-candidate-models --allow-candidate-dependency` only for candidate
evidence; restore the exact app pin before recording a production release pass.

## Windows delivery recovery (upcoming 0.1.13)

Use a disposable Windows desktop and benign synthetic text for clipboard/input
qualification. The native unit probe checks the ABI, synthetic Unicode payload
lifetime, and exclusion-format bytes when
`PRESSPEECH_NATIVE_CLIPBOARD_TEST=1` is explicitly set on a disposable
Windows runner; it is skipped otherwise. Test doubles cover fault control flow.
Neither qualifies Clipboard History, Cloud Clipboard, real input delivery, or
the packaged recovery UI. Do not run these steps against a user's active
clipboard.

- Hold the clipboard from a separate process during delivery. Confirm retained
  text is recoverable, recording is paused, the Delivery Recovery window opens
  above ordinary apps, and no transcript appears in the window, logs or files.
- Disable Presspeech notifications and move its notification-area icon into
  overflow. Trigger recovery and confirm the window still exposes Copy,
  Discard and Leave Waiting. Navigate and invoke each action using only Tab,
  Shift-Tab, Enter, Escape and its underlined access key; repeat with Narrator
  and confirm status changes are announced once without exposing the words.
  From Leave Waiting, verify Tab reaches Copy for Manual Paste and then
  Discard Dictation in the same order as their visible positions; Shift-Tab
  must reverse that sequence. Confirm focus remains visibly discernible and
  Narrator announces each control's purpose. Initial focus must be on Leave
  Waiting, so an Enter already in flight cannot copy or discard text when the
  asynchronous window first appears.
- Replace the clipboard after its write, during the route delay, and between
  modifier-down and V. Confirm detected changes skip V, release attempted keys,
  and retain text without replacing the newer copy automatically.
- Choose Leave Waiting and close with Escape separately. Confirm both retain
  recovery and keep recording paused. Press the dictation hotkey and launch
  Presspeech from Start separately; each must bring the existing recovery
  window back without copying, clearing, or duplicating it.
- Choose Copy while the clipboard is locked, then after it is released. Failure
  leaves the window open with inline recovery guidance; successful owned Copy
  disables Copy/Discard, reports that recording is available, and clears that
  entry. Retain two benign synthetic dictations and confirm Copy and Discard
  each resolve only the oldest waiting entry, update the status to say another
  remains, and keep recording paused until the queue is empty. Test an external
  copy immediately after write and confirm it is not adopted as the recovery
  write's receipt.
- Enable Windows Clipboard History, deliver a unique harmless phrase, overwrite
  the current clipboard, and open Win+V. Confirm the Presspeech phrase is absent.
  When a disposable paired test device is available, enable Cloud Clipboard and
  confirm the phrase is not offered there. While the Presspeech item is current,
  confirm ordinary Ctrl+V and explicit recovery still work. These native checks
  qualify Windows' ExcludeClipboardContentFromMonitorProcessing behavior; the
  opt-in native probe only confirms the marker is present, while doubled tests
  qualify write ordering and fail-closed control flow.
- Choose Discard and Exit separately. Both forget private recovery; Discard must
  leave a newer external clipboard untouched. No late worker may retain after Exit.
- Force `SendInput` to return zero for modifier-down, V-down, V-up and
  modifier-up separately, and inject an exception after an event may have
  reached Windows. Every case must retain recovery and run applicable release
  attempts; review the field before retrying. A fully accepted shortcut is not
  evidence of target consumption.
- Repeat ordinary local, elevated-window, RDP and Moonlight dictation checks;
  repeat local delivery with a non-US active keyboard layout, and use non-ASCII
  text and emoji to qualify the Unicode clipboard path. Verify the non-delayed
  clipboard data survives its private owner window being destroyed, and that
  the exclusion marker does not regress either remote delivery route.
