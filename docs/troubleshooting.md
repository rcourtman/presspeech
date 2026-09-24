# Troubleshooting

The public [troubleshooting page](https://rcourtman.github.io/presspeech/troubleshooting.html) has the complete,
task-focused recovery path for macOS and Windows. This concise reference keeps
the same recovery steps available as plain Markdown.

## Start Here

**Check before model recovery:** Reopening a published build with a missing
model, retrying a failed load, or resetting the cache may make another model
request. Read the macOS 0.3.8 or Windows 0.1.12 privacy decision below before
taking that step. If you choose to wait with a missing or damaged model, do not
reopen Presspeech, retry, reset the cache, or select an uncached model. Model
requests do not include dictation audio or transcripts.

1. Reopen Presspeech's controls only after making the model-download decision
   above. Open `Presspeech.app` again on macOS or launch Presspeech from the
   Windows Start Menu. If you chose to wait with a missing model, leave the app
   closed. An already-running process restores Setup or Settings instead of
   starting a second dictation process.
2. Check the model, microphone, permission, and hotkey status in **Setup
   Checklist** on macOS or **Setup** on Windows.
3. Use **Try Dictation** with harmless words. Its in-app scratchpad can still
   use the system clipboard. If that test works, continue with the paste and
   focus checks below.

## macOS

### Menu Bar Item Is Missing

macOS can temporarily omit third-party status items when a crowded or notched
menu bar has too little space. Open `/Applications/Presspeech.app` again from
Applications, Finder, or Spotlight. The existing process opens Setup Checklist.

Enable **Show in Dock** there for a persistent alternative. To hide the item
deliberately, choose **Settings → Behavior → Show Presspeech in Menu Bar**;
Presspeech enables Dock access if needed, and the Dock menu can restore the item.
Right-click the Dock icon to open dictation controls, Settings, Support, or Quit.

### Permission Is Missing Or Will Not Appear

Presspeech needs Microphone, Accessibility, and Input Monitoring. On macOS
27 and later, System Settings calls Accessibility **Device Control and Data
Access**, and Presspeech uses that current name in Setup Checklist. Open
**Support -> Setup Checklist...** and choose the affected row. If the grant
still does not appear, quit and reopen the copy in Applications, return to Setup
Checklist, and choose **Try Again**. Presspeech resets only the TCC service or
services represented by that row and asks macOS again. The Accessibility /
Device Control and Data Access row checks both focused-window access and
permission to send the paste shortcut; **Copy Diagnostics** reports those two
checks separately when the visible System Settings toggle and actual keyboard
delivery disagree.

### Speech Model Fails To Load

**Before reopening, retrying, or resetting macOS 0.3.8 with a missing or
damaged model:** Another download may include a Hugging Face token inherited by
Presspeech. The public model needs no account token. If one may be present in
the environment that launches Presspeech—or you are unsure—do not start another
download; when you can choose the timing, wait until macOS 0.3.9 is published
and installed. Leave a working model cache in place. Read the
[guidance for existing 0.3.8 users](https://rcourtman.github.io/presspeech/privacy.html#macos-0-3-8-after-use)
before resetting anything.

If you choose to make another model request after reading the warning above,
check the connection before retrying. If Presspeech reports an incomplete or
corrupt cache and you still choose to download again, use **Support -> Reset
Speech Model Cache...**. Presspeech deletes the local model cache, then downloads
and verifies a fresh copy; settings and dictionary rules remain intact. Do not
reset a working cache.

### Hotkey Stops Working

- Confirm **Settings -> Hotkey** still shows the intended key.
- Confirm Input Monitoring is granted in Setup Checklist.
- Use **Settings -> Hotkey -> Reset Hotkey to Default** to test Right Option.
- If another app reserves the key, record a different right-side modifier,
  F-key, or combination with Command, Control or Option. Custom combinations
  consume that shortcut globally; macOS or another app may reserve one first.
- For a custom combination, press exactly the saved modifiers; extra Command,
  Control, Option or Shift
  keys do not match. Caps Lock does not change the match. Bindings track physical
  keys across keyboard layouts; record it again to choose a different position.

### Try Dictation Works But Text Is Not Inserted

Confirm Accessibility (Device Control and Data Access on macOS 27 and later) is
granted in Setup Checklist, click the destination text
field, then dictate without changing apps before transcription finishes. The
row remains Missing if either focused-window access or keyboard-event posting
is unavailable, even when System Settings already shows Presspeech enabled; use
**Try Again** in that case. If the HUD or menu says
**Copied — press Command-V to paste** in published 0.3.8, Presspeech copied the
finished transcript when dictation completed. In builds with a separate
starting-window warning, **Can’t verify window — use Command-V** means it could
not identify the exact window when recording began; the transcript was likewise
copied. Builds with the revised notice say **Command-V if clipboard unchanged**.
These notices do not track later clipboard changes. If the clipboard still
holds the transcript, return to the intended field and paste manually instead
of dictating again. If the clipboard changed, use **Copy Last Transcript** when
Recent Transcripts is enabled; otherwise the earlier copy may no longer be
recoverable in Presspeech.

Presspeech also uses this fallback when it cannot verify the same destination.
Some Electron/Chromium-based apps do not consistently expose the focused-window
information the current Mac app checks, so either notice can appear even if the
window looks unchanged. Keep the original field and browser tab focused until
insertion or a recovery notice appears: the Mac check identifies the window,
not the field or tab within it. A failed input event does not prove that no text
reached the field. If Presspeech says **Couldn't paste — use Copy Last Transcript** in
0.3.8, or **Delivery uncertain** in builds with the revised notice, inspect the
destination before trying again. If text is missing, choose **Copy Last
Transcript** when offered, but remove any partial text before pasting the full
transcript. When **Recent Transcripts** is off, Presspeech has no in-app
copy-recovery entry; correct or remove any partial text before dictating again.

### Previous Clipboard Content Is Pasted

Update to macOS **0.3.8 or later** first. Legacy 0.3.7 used an automatic
restore timer that could race a slow target; turn **Restore clipboard after
paste** off while you update. No fixed delay proves that another app consumed
the paste.

In 0.3.8, automatic restoration and delay presets are retired. **Keep Previous
Clipboard for Manual Restore** is a fresh opt-in and is off by default, even
when the old automatic setting was enabled. It can retain an eligible complete
previous clipboard in memory for up to five minutes, limited to 64 MB and 256
representations. Presspeech never offers a partial restore if either limit is
exceeded or a representation is unavailable; the disabled restore row explains
why nothing was kept. After verifying the latest dictated text arrived, choose
**Restore Previous Clipboard…** and confirm. Cancel if the target is still
waiting to read the clipboard. Expiry, copying something else, disabling the
option or quitting discards the snapshot without an automatic rewrite.

If previous content still arrives on 0.3.8, remove it, leave the manual option
off, recover with **Copy Last Transcript** if available, and retry only with
non-sensitive text. Report the target app and attempt count with privacy-safe
diagnostics; do not include either clipboard value or the dictated text.

### System Audio Stays Muted

Unmute the Mac from Control Center or Sound settings, then reopen Presspeech.
The app uses a local watchdog and recovery marker to restore audio after an
interrupted recording, and checks the marker again at launch. If it happens
again, use **Support -> Copy Diagnostics** before quitting.

### Unexpected Exit Notice

The notice means a local marker remained after the previous process ended;
nothing was sent anywhere. Use **Copy Diagnostics** or **Open Log** from the
notice if you want to report what happened.

## Windows

### Notification Icon Is Missing

Select the up-arrow at the right of the taskbar to check notification-area
overflow. You can also launch Presspeech again from the Start Menu. The existing
process restores its open window, opens Setup during first run, or opens Settings
afterward.

### Speech Model Is Preparing Or Failed

**Before reopening, retrying, or selecting an uncached model in Windows
0.1.12:** A new model request may send Hugging Face usage telemetry and an
already-configured or saved token; custom routing can change its destination.
A TLS-inspecting HTTPS proxy trusted by the client can read any token sent. If
you want to avoid possible telemetry, are concerned about a token or custom
route, or are unsure, do not start another download; wait until Windows 0.1.13
is published and installed. If a TLS-inspecting proxy is in use and its trust
is unclear, do not launch while it is in use. Read the
[full Windows privacy decision](https://rcourtman.github.io/presspeech/windows.html#model-download-privacy)
before retrying.

If a download is already in progress and you have decided to continue, keep
Setup open and wait for the selected model to report ready. A hotkey press while
it is preparing is intentionally ignored. If Setup reports a failure, check the
connection and available disk space first; on an NVIDIA system, also check the
display driver. If you choose to make another model request after reading the
warning above, use **Retry Speech Model** or select another model in Settings.
An uncached alternative can also download files.

### Microphone Is Silent Or Cannot Open

Published Windows 0.1.12 checks the selected input automatically during Setup
and when its selection changes; choose **Check Again** to repeat that check.
Upcoming 0.1.13 and builds
containing this change keep the selected device closed until you choose
**Check Microphone**. Speak during its local level check; if the input is
silent, unmute it and choose **Check Again** in 0.1.12 or **Check Microphone**
in 0.1.13. If the input cannot open, use Setup's links to Windows Microphone
Privacy and Sound Input settings. Turn on **Microphone access** and **Let
desktop apps access your microphone**, then confirm the input under
**Settings -> System -> Sound -> Input**. Windows does not show a separate
Presspeech toggle for this unpackaged desktop app on standard Windows builds.
Some Windows 11 Experimental builds also offer per-app microphone controls for
desktop apps; if that setting is present, allow Presspeech there too. Windows
may ask for permission on first access; approve it only if you want Presspeech
to use the microphone. If a USB or Bluetooth input
was disconnected, reconnect it and choose **Check Again** in 0.1.12 or **Check
Microphone** in 0.1.13; the check refreshes microphone discovery without
requiring an app restart. Presspeech keeps an
unavailable specifically selected input selected rather than silently using a
different microphone. Before opening the microphone for each recording, it also
confirms that its cached Windows audio-device index still names that configured
input, rejecting a stale entry when re-enumeration shows that indexes changed
after reconnect or resume.

### Hotkey Does Nothing

Confirm the model is ready and check the selected key in Setup or Settings. If
Right Alt types `@`, `€`, or accented letters, the keyboard uses it as AltGr;
Presspeech deliberately leaves that chord alone. Choose F8 or another available
modifier or F-key and retry in **Try Dictation**.

### Try Dictation Works But Text Is Not Inserted

Click the destination text field before pressing the hotkey and keep that window
focused until transcription finishes. In published Windows 0.1.12, if the
notification says **Transcript copied, not pasted**, the finished transcript
was copied at completion. Check the intended field first, then press **Ctrl+V**
only if the clipboard still holds the complete transcript. A later copy can
replace it without changing the notice. If unsure, check it in a blank,
disposable text field before pasting into the intended field. If it has changed,
do not paste the newer item as your dictation.
This fallback is used when focus changed, the original window could not be
identified, or the target runs as administrator. Reopen an elevated target
normally when possible; do not run Presspeech as administrator as a workaround.

### Playback Stays Muted

Unmute the affected output from Windows Quick Settings or **Settings -> System
-> Sound**, then reopen Presspeech. Copy diagnostics if the previous mute state
is not restored after recording.

### Start With Windows Will Not Enable

Choose **Open Startup Settings**, find Presspeech under **Settings -> Apps ->
Startup**, and review the system setting. Return to Setup or Settings and retry
**Finish Setup** or **Save**. **Finish Setup** stays open if registration fails.
In upcoming Windows 0.1.13, **Set Up Later** instead shows a warning and closes
without completing setup; the guided setup returns on the next launch.

## Report A Problem Safely

Choose **Support -> Copy Diagnostics** on macOS or **Copy Diagnostics** from the
Windows notification-area menu. Include short reproduction steps, platform and
app version, whether the model reached ready, and whether Try Dictation worked.
For Windows model failures, include CPU, GPU, and driver details when available.

The app's **Report a Problem** and **Suggest an Improvement** actions open the
[support guide](https://github.com/rcourtman/presspeech/blob/main/SUPPORT.md);
they do not submit a report or attach diagnostics. **Copy Diagnostics** copies
the summary locally. Follow the support guide to check whether an existing
thread accepts comments; when public issue creation is available, use the [bug
report form](https://github.com/rcourtman/presspeech/issues/new?template=bug_report.yml).
For a security issue, use the [private vulnerability-reporting form](https://github.com/rcourtman/presspeech/security/advisories/new).

**Feedback availability checked 23 September 2026:** GitHub currently reports
that issue creation is restricted for this repository, so a new public bug
report cannot be submitted there. Check for a matching issue and whether
comments are available. If there is no usable public route, keep the
privacy-safe report locally and retry later; do not post private data elsewhere
as a workaround.
An unsent draft is not sent or monitored. It can record the Presspeech and
operating-system versions, affected stage, expected and observed result,
smallest safe reproduction steps or frequency, generic target-app context if
relevant, recovery tried and
whether it worked, and attempt/failure counts. For text delivery, use harmless
test text and record only whether complete text was recovered—never the phrase
itself. Keep diagnostics private until reviewed; do not include audio,
transcripts, raw logs, credentials, private paths, or identifying names.

GitHub issues are public. Diagnostics omit transcript text, audio, dictionary
contents, exact microphone names, raw error details, and raw log lines. Review
the separate local log before sharing any excerpt because device labels and
errors can contain private names or paths. Do not add dictated text, audio,
references, dictionary or shortcut contents, credentials, or other private data
yourself.

### Windows Delivery Recovery in Upcoming 0.1.13

These controls are not included in Windows 0.1.12. In builds containing the new
recovery actions, clipboard failure, a newer clipboard copy, an unavailable
original target, or uncertain keyboard delivery keeps the finished dictation in
memory and pauses new recording. Check the intended field before retrying: a
shortcut error does not prove that nothing was pasted.

If the original target is already missing, unfocused, or elevated before
delivery, Presspeech retains the dictation without replacing the prior
clipboard item. A later change can still leave the dictated text on the
current clipboard.

The **Delivery Recovery** window opens without showing or copying the dictated
words. Choose **Copy for Manual Paste** to copy explicitly, **Discard
Dictation** to forget the recovery copy and resume recording, or **Leave
Waiting** to close the window while recording remains paused. Discard leaves
the clipboard unchanged. Copy replaces the current clipboard item, including
an image or formatted copy, and Presspeech does not restore it; choose Leave
Waiting if you need to save that item first. A blocked hotkey or reopening
Presspeech brings the window back; neither action copies automatically.
Equivalent **Review Undelivered Dictation…**, **Copy Undelivered Dictation
(replaces clipboard)**, and **Discard Undelivered Dictation** commands remain
in the notification-area menu.

Depending on where delivery became uncertain, some or all text may already be
in the intended field or on the clipboard. If another writer changes the
clipboard during recovery, the recovery copy stays in process memory for
another deliberate Copy or Discard. Exit forgets it. These checks cannot
acknowledge target consumption or remove the final clipboard-check/input race.

Every 0.1.13 dictation clipboard item is marked for exclusion from Windows
Clipboard History and Cloud Clipboard before transcript text is published. This
does not remove the current item: deliberate Ctrl+V still works, and other local
software or a third-party clipboard manager can still read it.
