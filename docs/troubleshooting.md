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
- If another app reserves the key, record another supported right-side modifier
  or F-key.

### Try Dictation Works But Text Is Not Inserted

Confirm Accessibility is granted, click the destination text field, then dictate
without changing apps before transcription finishes. Presspeech binds a
recording to the app that had focus when it began. If focus changes, it leaves
the text on the clipboard instead of pasting into the wrong window; paste it
manually after confirming the intended destination.

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
in Settings.

### Microphone Is Silent Or Cannot Open

Select the intended input in Setup, speak during its local level check, and
choose **Check Again**. If the input cannot open, use Setup's links to Windows
Microphone Privacy and Sound Input settings. Turn on **Microphone access** and
**Let desktop apps access your microphone**, then confirm the input under
**Settings -> System -> Sound -> Input**. Windows does not show a separate
Presspeech toggle for this unpackaged desktop app.

### Hotkey Does Nothing

Confirm the model is ready and check the selected key in Setup or Settings. If
Right Alt types `@`, `€`, or accented letters, the keyboard uses it as AltGr;
Presspeech deliberately leaves that chord alone. Choose F8 or another available
modifier or F-key and retry in **Try Dictation**.

### Try Dictation Works But Text Is Not Inserted

Click the destination text field before pressing the hotkey and keep that window
focused until transcription finishes. If focus changes, Presspeech leaves the
transcript on the clipboard and shows a notification rather than pasting private
text into the wrong app. Return to the intended field and paste manually.

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

GitHub issues are public. Diagnostics omit transcript text, audio, and dictionary
contents. Do not add dictated text, audio, references, dictionary or shortcut
contents, credentials, or other private data yourself.
