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
  `python3 scripts/check-public-releases.py --require-published --check-release-notes`
  with read-only GitHub API access. Confirm the deployed platform guide names
  the new version and that its pinned package and checksum downloads succeed
  in a clean browser. The notes audit compares the latest published GitHub
  body for each platform with its tracked file, even while source metadata is
  preparing another version; a mismatch needs a correction on the public
  release page, not only a source edit. For a read-only notes audit without
  asset downloads or candidate metadata checks, run
  `python3 scripts/check-public-releases.py --notes-only` separately; it does
  not block Pages deployment.
- After Pages deploys the intended main commit, run
  `python3 scripts/check-live-pages.py` from that exact commit. It compares the
  public home, first-dictation, install, privacy, help, compatibility, and FAQ
  HTML with the checked-in files byte for byte. A passing source check or
  successful workflow run alone does not show which copy visitors receive.
  If this check differs or cannot fetch a page, mark public guidance
  **Blocked**, inspect the live first-launch and support warnings directly,
  and verify again after Pages catches up. Do not run it against an unreleased
  candidate worktree and mistake expected source-ahead-of-site drift for a
  deployment failure.
- Review each candidate's release notes before publication, then inspect the
  rendered GitHub release page as a standalone download entry point. Check its
  notes against that exact version's model-download privacy inventory and
  current support-route status. If a build has a pre-launch token,
  telemetry, routing, or proxy caveat, make the decision and its detailed guide
  discoverable from the release page itself; do not assume visitors first read
  the README or install guide. A request for compatibility reports must not
  imply that new issues can be submitted while issue creation is restricted.
  Run `python3 scripts/check-release-entry.py --platform macos --version X.Y.Z
  --file swift/release-notes/vX.Y.Z.md` or the corresponding `--platform windows
  --file windows/release-notes/X.Y.Z.md` before approving the entry. The
  automated lead-and-link check catches omissions, not misleading wording or
  a broken public guide; review the rendered notice and exact build behavior
  yourself. The macOS release script rechecks this before publication;
  Windows still requires the manual check until its workflow guard is approved.
  Editing a tracked release-notes file does not update notes already published
  on GitHub, and unreleased fixes must not be described as shipped.
  For the known 0.3.8 and 0.1.12 gaps, review the
  [internal correction draft](../marketing/RELEASE_ENTRY_CORRECTIONS.md) against
  the live pages; it is not evidence that either public page was corrected.
- From the non-collaborator account, open the Bug report, Feature request, and
  Target-app compatibility report templates. Confirm each form renders and can
  be filled without an “issue creation is restricted” message; do not submit a
  test report. Also confirm the private vulnerability-reporting route reaches
  its private form rather than a public issue composer.
- Activate **Report a Problem…**, **Suggest an Improvement…**, and, when the
  build contains it, **Test App Compatibility…** from the installed candidate.
  Confirm each opens the documented fixed destination without app, version,
  diagnostics, or user data in the URL.
- On the target-app compatibility page, classify all eight worksheet slots.
  From the homepage's **Test one target app** invitation, confirm the current
  issue-intake status and the worksheet's local, unmonitored-draft boundary are
  visible before following the link; do not present a downloaded draft as a
  submitted or monitored community report.
  Confirm its before-test privacy warning matches the currently published
  builds and links to their version-specific model-download guidance; update
  or retire the warning when those releases change. Do not recruit a fresh
  install for compatibility testing without showing that decision point. Before
  the first check action, confirm the page also states whether new public
  reports can currently be submitted and that the worksheet saves only a local,
  unmonitored draft while intake is restricted.
  Confirm **Copy report block** and **Download report draft** become available
  only when all slots are classified. Copy must contain only the eight aggregate
  counts and
  overall classification; the downloaded plain-text draft must add blank
  prompts for public versions and generic target context without prefilled app
  identity or version values. It must also say it was not submitted or
  monitored and link back to current support guidance. Neither action may
  include phrases or transcripts. Reset the worksheet and confirm selections
  are not restored after reload. Classify a completed focus-change attempt
  with no insertion but missing notice or recovery text as **No insertion, but
  recovery failed** and verify the overall result is **An incorrect or unsafe
  result occurred**. Verify the worksheet says to stop, accepts later
  **Not completed** slots as a reportable early stop, and marks a later
  completed focus-change check as noncomparable instead. A genuinely unrun
  slot with no completed failure must
  instead yield **Testing could not be completed**. Classify the first
  steady-focus attempt as **Incorrect or unsafe** and the seven remaining slots
  as **Not completed**; verify the block does not imply eight completed
  attempts and the overall
  result remains **An incorrect or unsafe result occurred**.
  Mark all eight slots **Not completed** and verify no report block, copy, or
  download is offered: zero observed checks are not compatibility evidence.
  Confirm the report draft and form distinguish matching issue threads from
  directly comparable observations: counts from different Presspeech or OS
  versions or conditions must not be pooled into a general success rate.
- In a signed-out browser, confirm the repository About description and topics
  expose both the released macOS app and Windows prerelease instead of
  presenting a Mac-only project. Check its privacy claims against the published
  builds too: do not use a blanket "no telemetry" description while a published
  build's model-download dependencies may emit usage telemetry. Confirm the
  Pages home page, Get started, macOS, Windows, Help, and Privacy routes are
  reachable from the primary navigation at desktop width, 360 CSS pixels,
  and 200% zoom.
- On Get started at desktop width, 360 CSS pixels, and 200% zoom, use each
  platform shortcut in the first-launch warning. It must reach that platform's
  visible decision and full-warning link without skipping to install or setup;
  the two decisions must sit side by side when space permits and stack without
  horizontal overflow on mobile. Confirm the warning and both platform decisions
  are available through heading navigation in a screen reader. Follow the four
  checkpoints against the currently published build on each platform: setup,
  microphone, hotkey, scratchpad, and paste-recovery instructions must not ask
  the user to use a control that exists only in an upcoming release. The wait
  decision may name that release, but must not imply it is already downloadable.
  On the home-page first-run card and Get started's fourth checkpoint, confirm
  a copied/manual-paste notice is identified as recovery, not an automatic-paste
  pass; a scratchpad pass must not imply that another target app is qualified.
- With keyboard focus on a primary-navigation link, narrow the viewport below
  720 CSS pixels. The links should collapse and focus should move to the visible
  Menu button. Open the menu and focus a link; Escape should collapse it and
  return focus to Menu. Open it again, move focus into page content, then press
  Escape; the page control should keep focus and the menu should stay open.
- At 360 CSS pixels and, separately, at 200% zoom, use only the keyboard on
  the comparison, benchmark, and privacy tables. Tab to each scrollable table,
  confirm its focus ring is visible, use Left/Right arrows to reach off-screen
  columns, and Tab out without trapping focus. With a screen reader, confirm
  the region is announced by its visible table caption. Repeat in Safari and
  a Chromium or Firefox browser; static source checks cannot establish browser
  scrolling or screen-reader behavior.

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
| Pinned model integrity: same-size cached-file rewrite with restored last-write time is rejected offline | |
| Ten consecutive dictations into Notepad | |
| Ten short-phrase onset checks across hold/toggle; first word or syllable retained | |
| A physical hold across the OS key-repeat interval remains one capture until release | |
| Five consecutive dictations into a current Chromium browser text field | |
| Same-window Chromium tab switch with different titles uses Delivery Recovery before paste | |
| Five consecutive dictations into a current Electron application text field | |
| Focus-change clipboard recovery and elevated-target recovery | |
| Held-modifier shortcut guard and manual recovery | |
| Automatic paste and manual recovery on US and a layout that moves V (for example US Dvorak) | |
| Locked/replaced clipboard recovery with notifications disabled and tray icon in overflow | |
| Explicit recovery Copy, Discard, Leave Waiting, and Exit behavior | |
| Non-text clipboard item preserved by known-invalid-target recovery; explicit Copy replacement clearly disclosed | |
| Clipboard History exclusion (and Cloud Clipboard exclusion when a disposable paired device is available) | |
| Try Dictation Copy/Cut uses protected clipboard and failed Cut leaves text intact | |
| Microphone disconnect/reconnect rescan and in-flight selection change | |
| Microsoft Remote Desktop and Moonlight insertion routes: repeated distinct text, original-field check, and focus-change recovery | |
| Sleep/resume, then microphone, hotkey, and dictation recovery | |
| In-place candidate install over the preceding public Windows release | |
| In-app update, cancelled/failed update recovery, uninstall, and reinstall | |
| Keyboard-only, Narrator, and Accessibility Insights checks | |
| Windows 11 Voice Access operation of named controls and menu-based dictation | |
| High-contrast themes across Setup, Settings, Update, Delivery Recovery, and Try Dictation | |

For the Notepad, browser, and Electron delivery rows, use distinct harmless
phrases in blank, non-submitting fields, with a harmless baseline clipboard
item before each attempt. After each attempt, inspect the target and paste the
current clipboard into a separate disposable local scratch field before
anything else copies to it. Compare the complete finished transcript,
including any configured suffix: stale, partial, duplicate, misdirected, or
missing text fails the automatic-delivery row. Record pasted-once, safe manual
recovery, and incorrect/unsafe counts separately; a recovery Copy after a
failure does not turn that attempt into an automatic-paste pass. Mark each
automatic-delivery row **Pass** only when every required attempt pasted once;
the three counts must sum to the required attempt count. A successful
`SendInput` call or unchanged clipboard sequence is not evidence that the
target consumed the text. Inspect the original field before retrying or using
recovery because it may already contain all or part of the transcript. If a
known external copy interrupted an attempt, record the interruption and rerun
with a fresh phrase; never exclude an unexplained failure. Retain only
aggregate counts, app versions, and generic field types, not the phrases,
clipboard contents, or screenshots.

For a candidate with the window-caption guard, use two disposable Chromium
tabs in the same window with different harmless titles and blank,
non-submitting text fields. Start dictation in the first, switch to the second
while still recording, then stop and confirm neither field receives an automatic
paste, the existing clipboard item is unchanged when the switch is detected
before the clipboard write, and Delivery Recovery retains the phrase for an
explicit decision. Repeat the switch after a harmless clipboard write if the
timing can be controlled, and verify no paste shortcut reaches the second tab;
the dictated text may already be on the current clipboard in that case. Also
test steady focus in the original tab: a caption change unrelated to a tab
switch may conservatively require recovery, but must not silently discard the
dictation. Same-title tabs and two fields within one tab remain an unresolved
identity limit, not a pass from the different-title test. Record only the
browser version and aggregate outcomes, not titles or dictated words.

For the remote-delivery row, use disposable local and remote machines and a
blank, non-submitting plain-text editor on the remote host—not a shell,
message composer, or real document. Record the client executable/version,
remote operating-system version, generic field type, and—for RDP—whether
client-to-host clipboard redirection is allowed; never record host or account
names. Qualify an RDP client and Moonlight separately, not as interchangeable
paste paths.
Before dictating into RDP, verify that an ordinary harmless local-to-remote
plain-text clipboard paste works. If client or host policy blocks redirection,
record **Blocked** for that configuration rather than treating local insertion
or Presspeech's local recovery copy as a remote pass; do not change managed
policy to make the test pass. Before Moonlight dictation, verify its own
client-side clipboard-typing shortcut with a harmless local marker; remote
clipboard synchronization is not its prerequisite. For each usable route,
make five consecutive dictations with distinct harmless phrases, including a
longer phrase and non-ASCII text where the selected model supports it. Clear
the remote editor between attempts, inspect it before any retry, and compare
with a separate local scratch paste before another copy. Count automatic
paste-once, safe manual recovery, and incorrect/unsafe outcomes separately;
stale, partial, duplicate, missing, or misdirected text fails the row. Then
start in the remote editor, switch to a blank local window before stopping,
and require no automatic insertion in either field plus complete deliberate
recovery. A local Notepad pass, posted shortcut, or intact local clipboard does
not establish that the remote host received fresh text. This qualifies only
the recorded client, host, policy, and field combination. Microsoft's
[RDP clipboard guidance](https://learn.microsoft.com/en-us/azure/virtual-desktop/redirection-configure-clipboard)
documents policy-dependent redirection; Moonlight's
[client implementation](https://github.com/moonlight-stream/moonlight-qt/blob/master/app/streaming/input/keyboard.cpp)
types local clipboard text into the host.

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
- In a separate disposable profile where the per-user
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` key is absent, leave
  **Start Presspeech with Windows** unchecked and complete Setup. Confirm the
  missing key does not block completion and is not created by opting out.
  Then opt in from Settings and confirm Presspeech creates its startup value;
  opt out again and confirm it removes only that value. Do not remove a shared
  Run key or other applications' entries to construct this test. If an isolated
  absent-key profile is unavailable, mark this sub-check **Not run**.
- On a clean Windows x64 profile with usable NVIDIA CUDA and no cached pinned
  Parakeet snapshot, confirm Setup identifies the multilingual path and its
  approximate 2.5 GB first download. With keyboard-only navigation and Narrator,
  verify the **Download Parakeet model (up to ~2.5 GB)**, **Select and download
  English-only CPU model (~141 MiB)**, and **Choose another model in
  Settings…** actions have useful names, appear before the microphone controls
  in visual and Tab order, and can be activated. Confirm initial focus lands on
  **Download Parakeet model (up to ~2.5 GB)**; Tab should reach the other model
  choices before the microphone selector. With Narrator and keyboard-only
  navigation, focus each download action and verify its accessible name
  identifies the model and size, and its accessible description or associated
  disclosure is available to Narrator and explains the request boundary: model
  requests target Hugging Face, no account token,
  model-library telemetry, dictation audio, or transcripts are sent, and
  configured proxy/CA settings still apply (a trusted TLS-inspecting proxy can
  read the request). Confirm the choices are absent and initial focus lands on
  the microphone selector when the model is already cached. Before the choice
  appears, confirm its Alt+D, Alt+U, and
  Alt+M shortcuts cannot activate hidden choices; once the choice appears,
  verify the matching shortcuts work. If the local-cache check reaches the
  consent state after Setup opens, confirm focus moves to the download choice
  only when the user has not started interacting with Setup. Before
  activating any model choice, confirm no model download has started. Choose
  **Set Up Later** and
  confirm setup remains incomplete and no model download continues; reopen
  Setup and verify the decision is offered
  again. In a separate clean test profile, choose the CPU option and confirm
  the selected model is English-only Whisper base.en (~141 MiB). In another
  clean profile, choose the multilingual download and confirm it starts only
  after that choice. With a complete pinned Parakeet snapshot already cached,
  confirm startup loads it without asking to download it again.
- On a clean Windows x64 profile without usable NVIDIA CUDA and no cached
  Whisper base.en snapshot, confirm Setup reports the English-only CPU model
  and its approximate 141 MiB download, and that no model download starts
  before **Download English-only CPU model (~141 MiB)** is activated. With
  keyboard-only navigation and Narrator, verify the size and download, defer, and
  alternate-model choices are clear. When the download action is focused,
  verify its accessible name identifies the model and size and its accessible
  description or associated disclosure is available to Narrator and explains
  the same request boundary. No model download should start before activation.
  Confirm initial focus lands on
  **Download English-only CPU model (~141 MiB)**; Tab should reach the
  alternate-model choice and then the microphone selector. Choose **Set Up
  Later** and confirm
  no download continues; reopen Setup and verify the choice remains available.
  In another
  clean profile, accept the download and confirm fetching begins only after
  that action and Setup reports its progress. With a complete local base.en
  snapshot, confirm startup loads it without asking again.
- After a selected pinned model has loaded successfully, close Presspeech on a
  **disposable test profile only**. In that profile's model cache, identify a
  reviewed weight file for the selected snapshot (`model.bin` or
  `model.safetensors`); do not alter a real user's shared cache. Record its
  byte length and last-write time, flip one byte **in place** without replacing
  the file, then restore its last-write time. Verify that length and last-write
  time still match the originals. Launch the installed candidate with
  `HF_HUB_OFFLINE=1` in its process environment. It must refuse the altered
  model with an integrity error before inference and must not contact the Hub
  or silently fetch a replacement. A successful earlier load, a unit-test
  mock, or a mismatch caused by changed size/time does not pass this check.
  Delete the disposable profile/cache afterward; if controlled mutation is
  unavailable, record **Not run**, not Pass.
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
  to speak, and change the microphone selection. Confirm **Save** is disabled;
  press **Ctrl+S** and confirm it does not commit the change while recording.
  Stop recording and wait for transcription and delivery to finish, then save
  the pending selection. Confirm the complete first transcript is at normal
  speed with its final words present and the next dictation uses the saved
  selection; switching between Automatic and that explicit input is sufficient
  when only one safe physical microphone is available.
- Mute the selected microphone, choose **Check Microphone**, and confirm setup
  says it is connected but no input level was detected instead of claiming it
  is ready. Before any input buffer arrives, confirm Setup says **Connecting
  microphone…** rather than inviting speech; once buffers arrive, confirm it
  announces **Listening — speak a few words…** without moving focus. Unmute
  it, speak during that Listening state, and check again successfully. If a
  device cannot deliver a buffer, confirm Listening is never announced. Record
  **Not run** for the no-buffer case if no suitable test device is available.
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
- Where two safe inputs have identical names under the same audio host API,
  confirm Setup and Settings do not offer either as a distinct explicit choice.
  If that selector was already saved, confirm it remains visible as ambiguous,
  **Check Microphone** does not open either input, and dictation does not
  silently capture from one. Disconnect one input and confirm the remaining
  explicit choice becomes usable. If this hardware is unavailable, record
  **Not run**, not Pass; Automatic is a separate, non-specific choice.
- With two distinct safe inputs, choose **Automatic** and make one dictation to
  establish its device cache. While idle, reconnect or enable an input so the
  PortAudio list changes and an old index can name a different usable input.
  Make another harmless dictation and confirm Automatic selects from the new
  list rather than reusing that stale index. Record the before/after device
  mapping; if the test PC never reorders its list, mark this check **Not run**,
  not Pass.
- Turn off **Let desktop apps access your microphone**, choose **Check
  Microphone**, and confirm setup reports that the microphone could not be
  opened without exposing raw device or PortAudio errors.
- On a Windows 11 Experimental build that exposes per-app desktop microphone
  permissions, use a clean test profile to check the first-access flow. Confirm
  Windows asks for access only when Presspeech first opens the microphone,
  allow Presspeech and confirm the check completes, then revoke that permission
  and confirm the check reports failure with a usable route back to privacy
  settings. Restore permission and check again. If the OS build or prompt is
  unavailable, record this supplemental check as **Not run**; it is not a
  substitute for or a default release gate for the supported Windows 11
  configurations above.
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
- Confirm initial keyboard focus lands on the first required model-download
  choice in Setup when a missing first-run model needs consent; otherwise it
  lands on the microphone selector. In Settings it lands on the hotkey
  selector, in the update prompt on Download Update, and in Try Dictation on
  the text area.
- In Settings, confirm the warning beside **After pasting** explains that a
  newline may submit dictated text in shells or terminals and recommends
  reviewing commands outside a shell. With Narrator, focus the selector and
  confirm its accessible name includes the same guidance. Restore **space**
  without testing a transcript in a command shell.
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
  or UI Automation names alone. After setup is complete, also launch Presspeech
  from the Start Menu, use Voice Access to activate Settings' **Try Dictation…**
  button, then operate the scratchpad's **Dictate** / **Stop Dictation** button
  by its accessible name. Confirm it opens the in-app scratchpad without
  starting a recording, and record any Voice Access limitation. Use Microsoft's
  [Voice Access overview](https://support.microsoft.com/en-us/accessibility/windows/voice-access/use-voice-access-to-control-your-pc-author-text-with-your-voice)
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
- With the configured hotkey idle, lock and unlock Windows, then dictate into a
  blank local field without choosing manual Repair. Repeat after sleep/resume
  with a desktop that does not require unlock, and after disconnect/reconnect
  of a remote Windows session when available. Confirm the hotkey is again
  reserved and starts exactly one capture; menu-based Dictate must still work.
  Lock once while an active harmless Try Dictation recording is held: the
  capture must be discarded, not transcribed or pasted after unlock, and a
  fresh press must start a new capture. Repeat with a slow cancellation or
  transcription in progress; automatic listener replacement must wait for it
  rather than interrupting delivery. If the automatic path reports failure,
  verify that **Repair Global Hotkey** restores the listener manually. Record
  event delivery and recovery separately; mock tests cannot establish either
  on a packaged Windows build.
- On the upcoming 0.1.13 candidate, repeat a cold microphone start with cues
  on and off and with a USB or Bluetooth input after reconnect/resume. Confirm
  **Connecting microphone…** appears first; neither the high cue nor
  **Listening…** may appear before the stream delivers input. Once the high cue
  finishes and playback muting is applied, **Listening…** appears and speech
  is accepted. Tap and release before readiness: there must be no false
  **Listening…** or stop cue, and the result must say the microphone was not
  ready. The early release must stop without a post-roll wait; if a slow input
  opens afterward, it must not revive the old recording or accept its audio.
  Press again and confirm a fresh dictation can start normally. If an input
  starts but supplies no buffers, it must close and report
  **Microphone not responding** rather than leaving a stuck recording. Check
  the selected input and repeat, without retaining audio or transcripts.
- On each clean CPU and NVIDIA configuration, use **Try Dictation** to repeat
  one short, harmless phrase ten times, alternating five hold-mode and five
  toggle-mode attempts. Begin speaking immediately after the visible
  **Listening…** cue. Compare each result privately with the expected phrase
  and record only the aggregate number with the complete opening word/syllable
  and the number with any opening loss. Do not retain the phrase, transcript,
  or audio in the qualification record. Any opening loss blocks qualification
  pending local diagnosis; distinguish capture timing from recognition variation
  before changing inference policy.
- In **Try Dictation**, use its **Dictate** button to start and stop several
  short, harmless phrases, ending a word at the stop click. Confirm the final
  word is present as it is when stopping with the hotkey or notification-area
  command. Repeat one quick stop before **Listening…** and confirm it
  reports the microphone was not ready rather than producing a partial
  transcript. Record aggregate outcomes only; do not retain audio or words.
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
  from awaiting consent to preparing; focus should advance to the **Microphone**
  selector, the next visible Setup control, rather than remain on a disabled or
  hidden model choice. Confirm Tab then follows the remaining visible controls
  in order. Focus **Retry Speech Model**, **Try Dictation**, or **Finish Setup**
  before each becomes unavailable and confirm focus moves to adjacent **Set Up
  Later**. Repeat keyboard-only and with Narrator; verify the destination
  control and status remain understandable.
- While the model is preparing, confirm **Try Dictation** and **Finish Setup**
  remain disabled. Choose **Set Up Later**, restart, and confirm setup opens
  again with the selected microphone, dictation style, and Start with Windows
  choice preserved. Confirm the chosen autostart state is reflected under Task
  Manager **Startup apps**. After the model reaches Ready, confirm both actions
  become available.
- During a missing-model download, confirm Setup's status advances through
  cache checking, downloading, loading, and warm-up. Verify the downloaded-byte
  count increases during transfer without a percentage or ETA, then check with
  Narrator that each phase is announced but byte-count refreshes do not
  repeatedly interrupt speech.
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
  the pending edits must remain available and save normally afterward. Repeat
  with keyboard focus on **Save** immediately before recording: focus must move
  to the adjacent dictionary list before **Save** is disabled, and Tab must
  continue normally. With focus on another control, the same status transition
  must not steal focus. Check both transitions with Narrator, then cancel the
  harmless recording without retaining dictated text.
- Begin recording in both hold and toggle modes, press **Escape**, and confirm
  capture stops, muted playback is restored, and no transcription is pasted or
  copied. Repeat with **Cancel Dictation (Esc)** in the notification-area menu.
- Start recording with **Try Dictation** focused, then close that window with
  its title-bar close control. Confirm capture is cancelled, muted playback is
  restored, the listening indicator disappears, and no hidden recording or
  transcription continues. Repeat while Try Dictation is open but a recording
  belongs to Notepad; closing the scratchpad must not cancel that recording.
- Stop a harmless Try Dictation recording, then close its window while the
  model is still transcribing. Confirm the completed text is retained in
  Delivery Recovery, is not pasted into whichever app now has focus, and can
  be copied or discarded exactly once. Repeat several times near the
  Transcribing-to-ready transition; no transcript should disappear or be
  duplicated. A close during active capture must still cancel without
  creating a recovery copy.
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
  silent recording. The short clip should show **No speech detected — try
  again**. On Whisper, a VAD-zero result should show the same; a blank decode
  without VAD-zero evidence should instead show **No text recognized — try
  again**, including on Parakeet. Any invented words on silence are a separate
  recognition failure to record, not a pass. Confirm the applicable indicator
  and notification do not disappear silently. Retry immediately and confirm
  the new **Listening…** state is not hidden when the old message expires.
  Repeat with the visual indicator disabled and confirm the notification remains.
- Open Notepad normally and confirm dictation pastes automatically. Then open a
  separate Notepad instance with **Run as administrator**, dictate into it, and
  confirm Presspeech sends no simulated paste shortcut and reports the Windows
  administrator boundary. Published 0.1.12 leaves the transcript on the
  clipboard for manual paste. In the upcoming 0.1.13 build, the existing
  clipboard item remains current and the transcript waits in Delivery Recovery
  until explicit Copy or Discard. Do not elevate Presspeech.

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
| In builds with proxy preflight, malformed inherited macOS proxy URL refuses before model loading without logging its synthetic credential marker | |
| Physical Command-V into each chosen native, browser, and Electron target works before Presspeech delivery is scored | |
| Ten consecutive dictations into TextEdit with the previous-clipboard option off | |
| Ten consecutive dictations into a current browser text field, plus three two-window focus-change recoveries, with the previous-clipboard option off | |
| Ten consecutive dictations into a current Electron/Chromium target with the previous-clipboard option off | |
| Automatic insertion and clipboard-only Command-V recovery on ordinary Dvorak, or another layout that moves V under Command | |
| Automatic insertion or visible safe recovery on a non-Latin input source such as Russian, plus physical Command-V recovery | |
| Active-layout Command-V hotkey conflict: recorder rejects it; an older binding that becomes Paste after a layout switch passes through | |
| Electron issue #33: steady-focus paste once; a switch between two windows of the same app must use clipboard recovery | |
| Ten TextEdit and ten slow Electron manual-restore trials for issue #36 | |
| First macOS pasteboard-access prompt during opt-in clipboard preservation, when supported: correct-field delivery or explicit copy-only recovery, never insertion into the prompt or another window | |
| Manual restore preserves a representative rich-text clipboard item, not just its plain-text fallback | |
| Custom hotkey in hold and toggle modes on two keyboard layouts | |
| Hotkey conflict rejection, persistence, Full Keyboard Access, and VoiceOver checks for issue #34 | |
| Focus-change recovery between native-app windows and between applications | |
| Clipboard-only recovery notice after a later harmless copy: no unconditional ⌘V promise; Copy Last Transcript restores the dictation when history is on | |
| History copy during uncertain delivery retains the destination-check warning rather than returning to Ready | |
| Same-window focus change between two controls with distinct AX identities: clipboard-only recovery | |
| Permission loss while recording: captured speech completes to clipboard-only recovery | |
| Screen Sharing shared-clipboard path: consecutive remote pastes stay fresh; a focus change recovers safely | |
| Sleep/resume, microphone route change, and first dictation afterward | |
| In-place upgrade with preferences, hotkey, and TCC grants retained | |
| Setup and Try Dictation with VoiceOver, keyboard-only navigation, and Voice Control menu start/stop before the hotkey is tested | |
| Menu-bar visibility preference, Dock fallback/restore, and dictation recovery with VoiceOver and keyboard-only navigation | |
| Two-display recording and clipboard-recovery HUD follows keyboard focus rather than the parked pointer | |
| Copy Last Transcript and Recent Transcripts recover the original dictation suffix, including after the suffix setting changes | |

Before scoring each target-app delivery row, copy a harmless marker by ordinary
means and paste it once with physical Command-V into the same blank,
non-submitting field and input source that will receive dictation. Verify the
marker appears exactly once, then clear the field. Do not use Presspeech to
create this baseline. If ordinary paste fails, record that target/version as a
failed baseline rather than attributing a later missing paste to Presspeech or
marking automatic delivery Pass. For the fixed TextEdit row, mark it Blocked;
for browser or Electron, select another representative target of the same app
class for the required row. Preserve the failed target as a separate
compatibility observation. A context-menu Paste that works while Command-V
fails does not establish this shortcut baseline.

For each ten-attempt TextEdit, browser, and Electron delivery row, use distinct
harmless phrases in blank, non-submitting fields and seed a harmless previous
clipboard item before every attempt. After each attempt, inspect the target,
then paste the current clipboard into a separate disposable local field before
anything else copies to it. Compare the complete resulting transcript,
including its configured suffix. Record **automatic paste once**, **safe manual
recovery**, and **incorrect or unsafe** counts separately; they must sum to ten
for each app class. Count an attempt as **safe manual recovery** only when
nothing was inserted automatically, a recovery notice appeared, and the
complete text could be pasted deliberately. This does not pass the
automatic-paste row. Mark that row **Pass** only when all ten attempts paste
once into the intended field. Stale, partial, duplicate,
misdirected, silent-missing, or unrecoverable text fails the row; posting a
paste event or showing a success notice is not proof that the target consumed
the text. Inspect the original field before retrying or using recovery because
it may already contain all or part of the transcript. Record a known external
copy interruption and rerun it with a fresh phrase, but never exclude an
unexplained failure. Retain only aggregate counts, app versions, and generic
field types—not phrases, clipboard contents, or screenshots.

For the synthetic-credential row, use a disposable macOS profile or VM with no
Hugging Face login/cache. Launch the installed candidate executable directly
with nonfunctional marker values in `HF_TOKEN`, `HUGGING_FACE_HUB_TOKEN`, and
`HUGGINGFACEHUB_API_TOKEN` (do not use or inspect real token values). Confirm
the normal first-download choice clearly says the public model needs no account
token, Presspeech removes inherited Hugging Face tokens before
downloading, and dictation audio and transcripts stay on the Mac. Confirm the
public model download succeeds and any local log mentions variable names
only—not marker values. Do not copy raw logs or capture/share request headers.
This packaged-app check complements the focused
`swift run Presspeech --self-test hostile-env` check, which exercises actual
process environment removal and Foundation visibility with synthetic values.

For the proxy-preflight row, use a separate disposable launch environment with
`https_proxy=http://user:presspeech-synthetic-proxy-marker@proxy.invalid:notaport`
and no real credentials. Launch the installed candidate with a missing model.
It must refuse before creating the model loader or making a download request;
the alert and app/library logs may name `https_proxy` but must not contain the
marker or full URL. Also test a syntactically valid proxy value in a controlled
network setup if available: Presspeech must not reject its syntax, though a
working proxy and TLS trust still require separate verification. Do not share
raw logs or proxy settings as qualification evidence.

For issue #33, pass only if a steady-focus Electron target receives the complete
transcript once, while switching to a second window of that same app before
delivery inserts nothing and leaves the complete transcript available for
manual paste. A matching process identifier alone does not authorize paste;
any insertion after the window switch is a failure.
For a candidate with the focused-control window fallback, include an Electron
target whose app-level `AXFocusedWindow` is unavailable but whose focused
control exposes `AXWindow`, if one is available. Check steady focus, a different
field in the same window with a distinct AX identity, and a second window. The
fallback must paste only with the original control and window still focused;
missing or changed identity must stay copy-only. Record only attribute
availability (including whether the app-level query returned no value or
attribute unsupported) and aggregate outcomes, never AX values, window titles,
or text. Other AX errors, including a disabled API or messaging failure, must
not authorize this fallback.

For the separate browser row, use a disposable, non-submitting field in a
current browser. Keep its field and tab selected for ten steady-focus attempts
with distinct harmless markers; count stale, partial, duplicate, or missing
insertions as failures. Then switch between two browser windows during three
dictations and require clipboard-only recovery with no insertion in either
window. Record the browser version and generic field type, not page or tab
names. This does not qualify a desktop Electron app or same-window tab moves.

Separately observe a same-window focus move using two harmless, non-submitting
fields in one native or browser window. Confirm with Accessibility Inspector,
without reading or retaining field contents, whether the two fields expose
distinct focused AX controls. Start in the first field, move to the second
before transcription finishes, and record whether text reaches either field
and whether a recovery notice appears. If the first field exposed a focused
control and the second has a distinct identity, require no automatic insertion
and a complete clipboard-only recovery. If the app exposes no focused control
at recording start or reuses one identity, record the window-only limitation
instead of claiming field safety. If available, repeat across two tabs of one
browser window; tabs can reuse a control. This diagnostic is not a substitute
for the required two-window focus gate. Record only aggregate outcomes and
generic field types, never text or tab names.

For the Screen Sharing row, use a disposable remote Mac with a blank,
non-executing text field, not a shell, message composer, or real document.
In Apple's [Screen Sharing app](https://support.apple.com/guide/mac-help/mh14066/mac),
note whether **Edit → Use Shared Clipboard**
is enabled; run the shared-clipboard path with it enabled. Dictate at least
five distinct harmless phrases into the same remote field, clearing it
between attempts. Compare each result with a local scratch paste before
copying anything else. Each attempt must either insert that attempt's complete
transcript once or show a clipboard-recovery notice without inserting stale,
partial, duplicated, or misdirected text. Then begin a dictation in the
remote field, move focus to a blank local window before stopping, and verify
neither window receives text automatically and the complete transcript can be
pasted deliberately. A stale earlier phrase, silent failure, or insertion
after the focus change fails this row. Record only aggregate outcomes, the
Screen Sharing and remote macOS versions, and the shared-clipboard setting;
do not retain phrases, hostnames, remote clipboard contents, or screenshots.
This qualifies only the tested configuration, not all remote desktop apps or
clipboard-sharing modes.

For the keyboard-layout rows, compare an English/US input source with two
distinct cases: ordinary Dvorak (or another layout that moves V under Command)
and a non-Latin input source such as Russian. One cannot stand in for the
other. Establish the physical Command-V baseline in each target under each
active input source before scoring Presspeech delivery. The non-Latin case
checks a different Command-layer mapping; a source-level Dvorak fixture does
not establish its behavior. Distinguish ordinary Dvorak from
[**Dvorak – QWERTY ⌘**](https://support.apple.com/en-bn/guide/mac-help/mh27976/mac):
the latter uses QWERTY positions while Command is held, so it does not satisfy
the moved-Command-V check; test it separately if available to catch a mismatch
between a displayed key label and the Command key-down translation. In TextEdit
and a current Electron/Chromium target,
confirm a steady-focus dictation inserts once under each layout when the active
Command-layer Paste key is resolvable. If it is not, require a visible
clipboard-only recovery instead of a guessed US-position shortcut. Then cause
a clipboard-only recovery by changing focus before delivery and confirm
physical Command-V pastes that transcript once under each layout. A displayed
hotkey label or successful physical-hotkey test alone does not establish that
synthetic paste and manual recovery work. Record only the input-source names,
whether automatic insertion or safe recovery occurred, and aggregate outcomes;
do not retain dictated phrases, transcripts, or field contents.

If an already-installed input source exposes no usable Command-V key mapping,
confirm Presspeech reports clipboard-only recovery and posts no guessed US-layout
shortcut; try physical Command-V, switching to a known input source first if
needed, and verify the retained transcript pastes once. Do not install an
untrusted layout solely to create this condition. Mark this conditional check
**Not applicable** when no such source is available.

For the hotkey/Paste conflict row, in a disposable test profile try recording
the physical Command-V chord under US and, if installed, a layout such as
Dvorak that moves V. The recorder must reject the active Paste chord without
changing the saved hotkey; physical Command-V must still paste a harmless
clipboard marker. Then save Command plus the physical key that is **not** Paste
under the first layout but becomes Paste under the second, switch layouts,
and verify physical Command-V passes through rather than starting dictation.
Use the menu to reset the hotkey before checking that a fresh dictation still
pastes automatically under the second layout. If the source exposes no usable
Paste-key mapping, Command-only hotkeys must be refused while Control, Option,
and F-key choices remain available. Record only input-source names and outcomes,
not markers or field contents.

For the keyboard-only delivery-recovery check, use a harmless test transcript
and create a clipboard-only delivery outcome, then open each menu using the
keyboard: first the status-item menu, then the Dock menu with **Show in Dock**
enabled. Traverse the available actions in both directions with the keyboard.
Confirm **Copy Last Transcript** is reachable in the action order after the
dictation control, is enabled when retained history is available, and copies
the latest transcript when activated. In a disposable plain-text field, check
that **Copy Last Transcript** from both the status and Dock menus, and an entry
in **Recent Transcripts**, copy the exact text originally offered for delivery,
including a configured final space or newline. Change the suffix setting
between dictation and Copy; an older entry must keep its own suffix, not adopt
the new setting. Use only non-executing fields for the newline case. Menu
previews and **Add Correction from Last Transcript** must still use the
processed words without the delivery suffix. Confirm any microphone recovery
action is likewise reachable when shown, and that disabled or absent recovery
actions are not presented as actionable. With VoiceOver, verify the recovery notice
and action names explain what will happen without relying on color or the
Command-key glyph. Do not record transcript contents; restore the test clipboard
only through an explicit user-confirmed action.

For the two-display HUD row, park the pointer on display A and use the
keyboard to focus a disposable, non-submitting field on display B. With the
waveform enabled, start and stop a harmless dictation using the hotkey. Confirm
the recording HUD, and **Transcribing** if it lasts long enough to appear, stay
on display B without moving keyboard focus or the pointer. Repeat with two windows
on display B: begin in the first,
switch focus to the second before transcription completes, and confirm the
clipboard-only recovery notice appears on B, no text is inserted into either
window, and the complete transcript can be pasted deliberately. Keep the
pointer on A throughout. Record only display/focus placement and aggregate
delivery outcomes, not text or window titles. A one-display run cannot pass
this row; native AppKit behavior is not established by the screen-choice
self-test.

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
  must say **Not downloaded**, explain the ~500–600 MB public Hugging Face model
  transfer needs no account token, disclose that inherited Hugging Face tokens
  are removed before download, give the free-space estimate for download plus
  CoreML preparation, state the local-audio/transcript boundary, and expose
  **Download Model**. Confirm the footer exposes **Set Up Later** as a separate
  defer action. Confirm that
  this estimate is visible before activating the action, not only after a
  failed disk-space check. With VoiceOver and keyboard-only navigation, confirm
  **Download Model** and **Set Up Later** actions have useful names and are
  reachable. Choose **Set Up Later** and confirm Setup closes without a model
  download; the app remains not ready. Reopen Presspeech and confirm both
  choices are still offered without starting a download. Activate
  **Download Model** and confirm the
  transfer starts only then, the changing progress value remains available in
  its row without replacing the VoiceOver reading element, and readiness
  proceeds normally. Progress-only refreshes should not trigger repeated
  app-wide announcements. Interrupt an approved download and relaunch; confirm
  it resumes without asking again.
- While a model load or download is in progress, open the menu-bar menu and
  navigate to its progress indicator with VoiceOver. Confirm it is named
  **Speech model progress**, exposes the current determinate percentage as its
  value, and provides the current model phase as accessibility help.
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
  With focus on **Download Model**, activate it and confirm focus moves to the
  next available checklist control when the download action disappears. In a
  separate permission check, leave focus on a grant action until that grant is
  confirmed; verify focus moves to the next available permission action (or a
  nearby remaining control) when the granted row's button disappears.
  Continue through the complete loop in both directions after each redraw;
  confirm no removed action retains focus and no current permission, Dock,
  Try Dictation, or Set Up Later/Close/Done control is skipped.
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
- In **Settings → Behavior**, turn off **Show Presspeech in Menu Bar**. Confirm
  Presspeech enables its Dock icon before hiding the status item and that the
  Dock menu's **Show Presspeech in Menu Bar** action restores it. With the item
  hidden, turn off **Show Presspeech in Dock** from the Dock menu's Settings;
  confirm the status item returns before the Dock icon is removed. Repeat with
  keyboard-only navigation and VoiceOver, checking the action name and checked
  state. Relaunch after each saved visibility state and confirm it persists.
  If the tested macOS version allows removing/restoring status items directly,
  verify that Presspeech follows that change and retains an accessible control
  route; temporary crowding/overflow alone must not be treated as the user's
  persistent visibility choice.
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
- In each focus-change case on a 0.3.9 candidate, confirm the HUD says
  **Copied — ⌘V if unchanged**, the menu says **Transcript copied — ⌘V if
  clipboard unchanged** after the HUD closes, and copying the last transcript
  clears the notice. Published 0.3.8 uses **Copied — press ⌘V to paste**.
- After a clipboard-only recovery with Recent Transcripts on, copy a different
  harmless marker before opening the menu. Confirm its notice does not promise
  unconditionally that ⌘V will paste the dictation. Choose **Copy Last
  Transcript** and verify the original dictation, including its suffix, is
  available for deliberate paste. Do not paste the newer marker into a real
  document; repeat with Recent Transcripts off to confirm no menu copy is
  promised. This checks guidance, not live clipboard-change detection.
- Stop a long harmless dictation and revoke Input Monitoring while it is still
  transcribing. Confirm Presspeech does not paste into System Settings or any
  other newly focused window, leaves the complete transcript on the clipboard,
  shows **Copied — ⌘V if unchanged** on a 0.3.9 candidate (published 0.3.8
  says **Copied — press ⌘V to paste**), and returns to the missing-permission
  setup state. Regrant the permission and confirm a fresh dictation can paste
  automatically; the interrupted dictation must never paste later.
- In a disposable test account, begin a harmless recording in a target field,
  then revoke Accessibility or keyboard-event posting access while recording.
  Stop using the menu if the hotkey no longer reaches Presspeech; the Stop
  action must remain enabled and its help must describe manual recovery. If
  audio was captured, the recording must still finish transcription and offer
  the complete resulting transcript on the clipboard for manual paste, rather
  than silently discarding it; no automatic paste may reach the original field or
  System Settings.
  Confirm the missing-permission setup state appears after recovery and a fresh
  dictation works after regranting. Record only the grant and action timing and
  aggregate result, never the dictated words or clipboard contents. If macOS
  does not apply the revocation to the running app, mark this check **Blocked**
  rather than treating it as a pass.
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
  When the target was unavailable at recording start, confirm the HUD says
  **Unverified — ⌘V if unchanged** on a 0.3.9 candidate, the menu keeps **Can’t
  verify window — ⌘V if clipboard unchanged**, and VoiceOver explains that the
  window could not be verified and the transcript was copied for manual paste
  only while the clipboard is unchanged. Published 0.3.8 says **Can’t verify
  window — use ⌘V** in the HUD and **Can’t verify window — press ⌘V to paste**
  in the menu. A focus change after a valid capture must retain the ordinary
  copied recovery rather than the unverified-starting-window reason.
  Do not add the target name, window title, field contents, or transcript to
  that log extract. Every fallback is still an automatic-paste failure.
- Confirm Settings → Text → Recent Transcripts labels **Off — No Copy Last
  Transcript** before selection. If a harmless test produces **Delivery
  uncertain**, clear Recent Transcripts and, on a separate attempt, switch
  them Off while that warning is present. The warning must remain, now say
  there is no menu copy, and ask the user to check the destination before
  retrying; the status item must not silently return to **Ready**. Do not paste
  a full transcript over possible partial text. If the test target does not
  produce uncertain delivery, mark this case Not run rather than treating a
  copied/manual-paste notice as proof.
- With **Delivery uncertain** and Recent Transcripts on, inspect the original
  field before choosing **Copy Last Transcript**. Confirm the menu status and
  status item's VoiceOver value still warn that delivery is uncertain after
  the copy, rather than returning to Ready. If an older History entry exists,
  copying it must not clear the same warning. Check copied text only in a
  separate disposable field; do not paste over possible partial text in the
  original field. If no safe target produces uncertain delivery, mark this
  check Not run.
- With a harmless multi-sentence transcript in recent history, hover **Copy
  Last Transcript**, a **Recent Transcripts** entry, and **Add Correction from
  Last Transcript…**. Their help tags must describe the actions without
  revealing transcript text; only the intentionally opened submenu may show
  its bounded preview.
  Repeat with VoiceOver configured to speak help tags; no full transcript
  should be announced merely by reaching a menu action. Confirm that copying
  an entry still places the complete transcript on the clipboard. Do not put
  the test phrase in the qualification record.
- Dictate silence long enough to pass the short-clip cutoff and confirm the HUD
  and menu report **No text to insert — try again** rather than playing the
  successful-dictation cue. An empty decoder result does not prove that speech
  was absent; also try short audible speech and record any unexplained blank.
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
- Separately test representation fidelity with disposable content, not a real
  document. In TextEdit rich-text mode, copy harmless text with two visibly
  different styles. Before dictation, paste it into a second rich-text document
  and a plain-text field to establish both baselines; if the source supplies
  only plain text, choose another local rich-text source. Copy the styled source
  again, dictate into a blank field, observe the complete transcript there,
  then explicitly choose **Restore Previous Clipboard…**. Paste into fresh
  rich-text and plain-text fields. The restored item must reproduce the
  baseline words and styling, without a transcript or missing format. Record
  the source app, generic content class, and result only, not content or
  clipboard bytes. If Presspeech reports that a representation was unavailable
  and offers no restore, record **Blocked** for that source, not a fidelity
  pass; use another source to complete this row. A plain-marker restore or the
  synthetic pasteboard self-test alone cannot qualify a real app's rich-content
  provider. AppKit pasteboard items can carry
  [multiple representations](https://developer.apple.com/documentation/appkit/nspasteboard).
- With preservation still enabled, seed a new old marker and force
  clipboard-only recovery twice: once by changing windows during transcription
  and once in a target that does not expose exact focused-window identity.
  In each case, verify the complete transcript remains available for manual
  Command-V and **Restore Previous Clipboard…** restores the marker that
  preceded dictation. Recovery must not discard the opted-in snapshot merely
  because Presspeech declined to paste automatically.
- Where macOS pasteboard-access alerts are active, use a disposable profile
  without a prior Presspeech pasteboard decision for the **first-prompt** row.
  Apple's [access-behavior documentation](https://developer.apple.com/documentation/appkit/nspasteboard/accessbehavior-swift.enum)
  says the default general-pasteboard behavior can ask on programmatic access;
  the opt-in snapshot reads the previous clipboard during delivery. Seed a
  harmless old marker, enable preservation, and dictate into a blank,
  non-submitting TextEdit field. When the system prompt appears, answer it
  without manually returning focus to the field before delivery completes.
  Record the decision and whether focus returned to that same field. The exact
  transcript must either arrive once in that field or remain available with an
  explicit copy-only recovery notice; it must not enter the permission prompt
  or another window. Inspect the field before any manual paste, then check the
  clipboard in a separate disposable field. Confirm the restore row reflects
  whether a complete previous-clipboard snapshot was available; denial must
  not present an unusable or partial snapshot as restorable. Retain only the
  decision, generic focus/delivery outcomes, and restore availability, never
  the marker, transcript, clipboard contents, or window title. If the candidate
  OS does not present this prompt, record **Not applicable** with the OS/build
  and access-behavior condition; do not infer a pass from the later Always Deny
  check.
- On macOS 15.4 or later where pasteboard-access controls are active, complete
  the first-prompt check above, then configure Presspeech as **Always Deny** in
  the corresponding System Settings privacy pane. With previous-clipboard
  preservation still enabled,
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
- With preservation and Recent Transcripts enabled, allow clipboard read
  access if macOS asks, seed an old harmless clipboard marker, and change
  focus before transcription finishes.
  Confirm copy-only recovery leaves the complete transcript available for
  manual paste and, while Presspeech still owns the clipboard, offers an
  explicit restore of the pre-dictation clipboard. Then choose **Copy Last
  Transcript** and confirm that this separate copy retires the restore offer
  without restoring the old contents. A pure self-test or controlled AppKit
  fixture does not qualify external app consumption, accessibility or
  clipboard providers.
- Test hold mode: hold the hotkey, speak, release, and confirm text pastes
  at the cursor.
- In hold mode, release directly on the last consonant of several short
  phrases and confirm the final word is retained. Repeat with quiet room tone
  and with steady background noise; the quiet case should begin transcription
  promptly, and ongoing noise must never hold capture more than about 0.4
  seconds after release. For the tap-callback boundary, vary the release timing
  across at least 20 short-phrase attempts and record only the number of missing
  final words and any capture materially longer than about 0.4 seconds; do not
  retain the phrases or recordings in the qualification record.
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
- Keep spoken formatting enabled and also enable **Remove filler words**.
  In a disposable field, dictate a phrase with “new paragraph” and “new
  line” plus an audible “um”. When the privacy-safe log reports a nonzero
  filler-removal count for that take, confirm the filler is gone but the
  paragraph and line breaks remain. If recognition omits the filler on every
  attempt, mark this check **Blocked**, not **Pass**.
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

- In a disposable editor with selected harmless text, configure a dictionary
  rule that replaces a reliably recognized test phrase with the empty string.
  Test the Space, Newline and None suffix choices separately. When that phrase
  is recognized, **Nothing to insert** must appear, the selected text and
  previous clipboard item must remain unchanged, and no Ctrl+V may be sent.
  Repeat with filler removal enabled and a filler-only recognized phrase when
  the recognizer produces one. Record an unrecognized phrase as **Not run** for
  this check, not a pass. A deliberate single-space dictionary replacement
  must remain deliverable; do not treat all whitespace as removed content.
- Hold the clipboard from a separate process during delivery. Confirm retained
  text is recoverable, recording is paused, the Delivery Recovery window opens
  above ordinary apps, and no transcript appears in the window, logs or files.
- On a disposable Windows test setup, briefly contend for the clipboard near
  the end of Presspeech's first acquisition window, then again while it
  reopens the clipboard to verify its write. If Presspeech's item remains
  current and the second hold ends within its separate bounded retry window,
  confirm one paste and no recovery prompt. If another process replaces the
  item instead, confirm no paste shortcut, no second Presspeech write, and
  explicit recovery. The model-free timing test cannot qualify this Win32 path.
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
- Replace the clipboard after its write, during the route delay, and just
  before the one-batch shortcut submission. Confirm detected changes skip the
  shortcut and retain text without replacing the newer copy automatically.
  Replace it during submission or within the brief post-shortcut check and
  confirm an uncertain-delivery notice, with no automatic second write.
  In a controlled candidate build, combine that late replacement with a
  focus change: the notice must tell the user to check the intended field,
  any field that gained focus, and the current clipboard before Copy or Discard.
  A later copy can still race the target's asynchronous paste consumption.
- With controlled fault injection, let `SendInput` accept the complete shortcut,
  then change or invalidate the focused-window observation before the final
  check. Confirm Delivery Recovery retains the text and reports uncertainty,
  the fixed log status is
  `paste outcome uncertain; original target could not be verified`, and no
  second clipboard write or shortcut is attempted. Check the original and
  any newly focused field before resolving the copy; an
  accepted shortcut may already have pasted. Do not treat an uncontrolled
  manual focus switch that missed this narrow interval as a pass.
- Switch to a different target window before delivery. Confirm the recovery
  notice appears and the Presspeech-authored target-verification log message is
  the fixed status `paste skipped; original target could not be verified`, not either executable
  name, window title, or dictated text. Seed a harmless prior clipboard item:
  when the changed focus is known before delivery, it must remain current until
  Copy is chosen in Delivery Recovery. Repeat with the original window closed,
  a different process reusing its HWND if reproducible, a different child edit
  control, and an elevated target. In a controlled candidate build, also make
  the target integrity-level query fail while its window and focused control
  stay valid; this must leave the prior clipboard item current and open
  Delivery Recovery without sending input. Repeat for an unreadable Presspeech
  integrity level. A focus or clipboard change after the
  preflight may still leave transcript text on the current clipboard; inspect
  the field before deciding to Copy or Discard. Record only pass/fail, not the
  raw log or clipboard content.
- Force `GetGUIThreadInfo` to fail at both capture and delivery for the same
  top-level window, then repeat with failure only after the clipboard write.
  Neither case may send the paste shortcut. The pre-write case must preserve
  the prior clipboard item; the late failure may leave transcript text current
  but must retain Delivery Recovery. Record outcomes without content.
- With F8 as the dictation hotkey, hold each of Ctrl, Shift, Alt, Windows and V in
  turn while transcription finishes. Confirm Presspeech sends no paste shortcut,
  does not release the physically held key, explains the retained dictation,
  and offers manual Copy or Discard. A key already held before delivery should
  leave the prior clipboard item unchanged, including when the hook did not
  observe the initial key-down but the physical-state preflight sees it.
  Release it before manually pasting.
  Repeat a normal paste with no other modifier held. This point-in-time check
  reduces wrong-shortcut risk but cannot make keyboard state and SendInput
  atomic; inspect the target for any unexpected command or partial insertion.
  If the Windows key moves focus first, record focus-change recovery instead;
  that is not evidence that the held-modifier guard ran.
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
- On a disposable profile, copy a harmless test image or formatted sample and
  verify it can be pasted before dictation. Trigger a known-invalid destination
  before Presspeech writes, then choose Leave Waiting: the existing non-text
  item must still paste, and no transcript should appear on the clipboard.
  Reopen Delivery Recovery and verify the visible warning and Copy button's
  Narrator description both say Copy replaces the current item and does not
  restore it; the notification-area Copy label must also say it replaces the
  clipboard. Choose Copy, confirm the current item is now transcript text for
  deliberate paste, and do not claim the earlier image is preserved. Separately
  confirm normal automatic paste also replaces a seeded non-text item; that is
  expected behavior, not a recovery-preservation pass. Record only outcomes,
  not the sample or transcript contents. Do not rely on Clipboard History or a
  third-party manager to retrieve the prior item.
- Enable Windows Clipboard History, deliver a unique harmless phrase, overwrite
  the current clipboard, and open Win+V. Confirm the Presspeech phrase is absent.
  When a disposable paired test device is available, enable Cloud Clipboard and
  confirm the phrase is not offered there. While the Presspeech item is current,
  confirm ordinary Ctrl+V and explicit recovery still work. These native checks
  qualify Windows' ExcludeClipboardContentFromMonitorProcessing behavior; the
  opt-in native probe only confirms the marker is present, while doubled tests
  qualify write ordering and fail-closed control flow.
- In Try Dictation, select a distinct harmless phrase and use Ctrl+C, then
  Ctrl+X on another selection. Confirm each copy remains available to ordinary
  Ctrl+V, Cut removes only the selected text, and neither item appears in
  Win+V after the current clipboard is replaced. Repeat with the clipboard
  held by another process: Copy/Cut must not fall through to Tk's unprotected
  default clipboard path, and a failed Cut must leave the selection in the
  scratchpad. In a controlled candidate build, replace the clipboard after the
  first copy confirmation but before Cut's final selection check completes;
  Cut must keep the selection and report the changed clipboard. This is a
  point-in-time guard, not an atomic guarantee against a later external copy.
  Record only pass/fail; never save the test clipboard contents.
- Choose Discard and Exit separately. Both forget private recovery; Discard must
  leave a newer external clipboard untouched. No late worker may retain after Exit.
- With an in-app update's installer verified and a harmless recovery dictation
  waiting, approve installation. Presspeech must postpone launch without
  exiting or clearing the dictation, and the update window must offer **Install
  Update** without another download. Resolve the dictation with Copy or
  Discard, then invoke Install Update and verify the installer starts once.
  Repeat the postponement while a dictation is recording and while it is
  transcribing. If the update window is closed instead, its temporary verified
  installer must be removed. Record this as native update/recovery behavior,
  not as proof that an accepted paste reached the original field.
- In a controlled candidate build, force the one-batch `SendInput` call to
  report zero accepted events, then a nonzero incomplete count for the local
  Ctrl-down, V-down, V-up, Ctrl-up chord. Every case must retain recovery.
  Zero must cause no cleanup key-up; a partial count must still attempt
  conservative key-up cleanup. Also inject an exception after submission may
  have begun: with no accepted count, best-effort release may include every
  chord key. Review the field before retrying. A fully accepted shortcut is
  not evidence of target consumption.
- Repeat ordinary local, elevated-window, RDP and Moonlight dictation checks.
  For the keyboard-layout row, compare US with a layout that moves V to a
  different physical key (for example US Dvorak). In a blank local editor,
  verify that Presspeech inserts once while focus stays steady under each
  layout; then force a safe focus-change recovery, use Delivery Recovery's
  explicit Copy action, and verify that Control plus the key producing V in
  the active layout (not the US V position) pastes the retained transcript
  once under each layout. Check the original field before copying, and count
  automatic failure as a failure even if recovery works. A generic non-US
  layout that leaves V in place does not complete this check. Use non-ASCII
  text and emoji separately to qualify the Unicode clipboard path. Verify the
  non-delayed clipboard data
  survives its private owner window being destroyed, and that the exclusion
  marker does not regress either remote delivery route.
- In a native test window containing two edit controls with distinct Win32
  focus HWNDs (verify both handles with a Windows inspection tool), start
  dictation in the first and move keyboard focus to the second before delivery.
  Confirm no Ctrl+V is sent, the text is retained for Delivery Recovery, and
  neither field receives an automatic paste. Repeat without moving focus and
  confirm the first control receives one paste. Also try a browser or Electron
  window with two fields and record what happens: those fields may share one
  Win32 focus HWND, so passing the native-control check does not establish
  field-level identity for custom-rendered apps. Do not claim it does. If a
  target exposes a focus HWND at delivery but not at recording start, confirm
  Presspeech uses Delivery Recovery rather than treating that later identity
  as proof of the original control.
