# Troubleshooting

The public [troubleshooting page](https://rcourtman.github.io/presspeech/troubleshooting.html) has the complete,
task-focused recovery path for macOS and Windows. This concise reference keeps
the same recovery steps available as plain Markdown.

## Start Here

1. Reopen Presspeech's controls. Open `Presspeech.app` again on macOS or launch
   Presspeech again from the Windows Start Menu. The running process restores
   Setup or Settings instead of starting a second dictation process.
2. Check the model, microphone, permission, and hotkey status in **Setup
   Checklist** on macOS or **Setup** on Windows.
3. Use **Try Dictation**. If its private scratchpad works, continue with the
   paste and focus checks below.

## macOS

### Menu Bar Item Is Missing

macOS can temporarily omit third-party status items when a crowded or notched
menu bar has too little space. Open `/Applications/Presspeech.app` again from
Applications, Finder, or Spotlight. The existing process opens Setup Checklist.

Enable **Show in Dock** there for a persistent alternative. Right-click the Dock
icon to open dictation controls, Settings, Support, or Quit.

### Permission Is Missing Or Will Not Appear

Presspeech needs Microphone, Accessibility, and Input Monitoring. Open
**Support -> Setup Checklist...** and choose the affected row. If the grant
still does not appear, quit and reopen the copy in Applications, return to Setup
Checklist, and choose **Try Again**. Presspeech resets only that stuck TCC entry
and asks macOS again.

### Speech Model Fails To Load

Check the connection and retry first. If Presspeech reports an incomplete or
corrupt cache, use **Support -> Reset Speech Model Cache...**. Presspeech deletes
only the local model cache, then downloads and verifies a fresh copy; settings
and dictionary rules remain intact.

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

Confirm Accessibility is granted, click the destination text field, then dictate
without changing apps before transcription finishes. If the HUD or menu says
**Copied — press Command-V to paste**, the finished transcript is already on
the clipboard. Return to the intended field and paste it manually instead of
dictating it again.

Presspeech also uses this fallback when it cannot verify the same destination.
Some Electron/Chromium-based apps do not consistently expose the focused-window
information the current Mac app checks, so the notice can appear even if the
window looks unchanged. If Presspeech instead says **Couldn't paste — use Copy
Last Transcript**, choose that menu action before trying again.

### Previous Clipboard Content Is Pasted

On the currently published macOS **0.3.7**, turn off **Settings -> Behavior ->
Restore clipboard after paste** and retry with non-sensitive text. This leaves
the transcript in place instead of letting an automatic restore race a slow
target. Remove unintended text first, use **Copy Last Transcript** if available,
and include the target app's name with privacy-safe diagnostics in a bug report.
No fixed delay proves that another app consumed the paste.

**Upcoming 0.3.8 / builds containing Keep Previous Clipboard for Manual Restore:**
automatic restoration and delay presets are retired. The new manual option is
off by default, including when the old automatic setting was enabled. Enable it
only if you want an eligible complete previous clipboard snapshot kept in
memory for up to five minutes. The snapshot is limited to 64 MB and 256
representations; Presspeech never offers a partial restore if either limit is
exceeded or a representation is unavailable. The disabled restore row explains
why nothing was kept. After verifying the latest dictated text arrived, choose
**Restore Previous Clipboard…** and confirm. Cancel if the target is still
waiting to read the clipboard. macOS provides no consumption acknowledgement.
Expiry, copying something else, disabling the option or quitting discards the
saved snapshot without an automatic clipboard rewrite. These controls are not
available in the currently published 0.3.7 release.

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

Keep Setup open and wait for the selected model to report ready. A hotkey press
while it is preparing is intentionally ignored. If Setup reports a failure,
choose **Retry Speech Model**. Check the connection and available disk space; on
an NVIDIA system, also update the display driver or select a local Whisper model
in Settings. In upcoming 0.1.13, an error that says a cached input failed its
SHA-256 manifest is deliberately not retried over the network. Do not bypass the
check or keep retrying the same cache; copy the privacy-safe diagnostics and
report the named model input, then use another model or deliberately remove only
that model repository from the Hugging Face cache before requesting a fresh
download.

### Microphone Is Silent Or Cannot Open

Select the intended input in Setup, speak during its local level check, and
choose **Check Again**. If the input cannot open, use Setup's links to Windows
Microphone Privacy and Sound Input settings. Turn on **Microphone access** and
**Let desktop apps access your microphone**, then confirm the input under
**Settings -> System -> Sound -> Input**. Windows does not show a separate
Presspeech toggle for this unpackaged desktop app. If a USB or Bluetooth input
was disconnected, reconnect it and choose **Check Again**; the check refreshes
microphone discovery without requiring an app restart. Presspeech keeps an
unavailable specifically selected input selected rather than silently using a
different microphone.

### Hotkey Does Nothing

Confirm the model is ready and check the selected key in Setup or Settings. If
Right Alt types `@`, `€`, or accented letters, the keyboard uses it as AltGr;
Presspeech deliberately leaves that chord alone. Choose F8 or another available
modifier or F-key and retry in **Try Dictation**.

### Try Dictation Works But Text Is Not Inserted

Click the destination text field before pressing the hotkey and keep that window
focused until transcription finishes. If the notification says **Transcript
copied, not pasted**, the finished transcript is already on the clipboard.
Return to the intended field and press **Ctrl+V** instead of dictating it again.
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
**Finish Setup** or **Save**.

## Report A Problem Safely

Choose **Support -> Copy Diagnostics** on macOS or **Copy Diagnostics** from the
Windows notification-area menu. Include short reproduction steps, platform and
app version, whether the model reached ready, and whether Try Dictation worked.
For Windows model failures, include CPU, GPU, and driver details when available.

Use the [bug report
form](https://github.com/rcourtman/presspeech/issues/new?template=bug_report.yml),
or the [private vulnerability-reporting
form](https://github.com/rcourtman/presspeech/security/advisories/new) for a
security issue.

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

The **Delivery Recovery** window opens without showing or copying the dictated
words. Choose **Copy for Manual Paste** to copy explicitly, **Discard
Dictation** to forget the recovery copy and resume recording, or **Leave
Waiting** to close the window while recording remains paused. Discard leaves
the clipboard unchanged. A blocked hotkey or reopening Presspeech brings the
window back; neither action copies automatically. Equivalent **Review
Undelivered Dictation…**, **Copy Undelivered Dictation**, and **Discard
Undelivered Dictation** commands remain in the notification-area menu.

Depending on where delivery became uncertain, some or all text may already be
in the intended field or on the clipboard. If another writer changes the
clipboard during recovery, the recovery copy stays in process memory for
another deliberate Copy or Discard. Exit forgets it. These checks cannot
acknowledge target consumption or remove the final clipboard-check/input race.

Every 0.1.13 dictation clipboard item is marked for exclusion from Windows
Clipboard History and Cloud Clipboard before transcript text is published. This
does not remove the current item: deliberate Ctrl+V still works, and other local
software or a third-party clipboard manager can still read it.
