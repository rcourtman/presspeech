# Troubleshooting

## Menu Bar Item Is Missing

macOS can temporarily omit third-party status items when a crowded or notched
menu bar has too little space. Open `/Applications/Presspeech.app` again from
Applications, Finder, or Spotlight. The existing Presspeech process opens
Setup Checklist instead of launching a second copy.

Enable **Show in Dock** in that window for a persistent alternative.
Right-click the Dock icon to open dictation controls, Settings, and Support;
macOS also provides its standard Quit command there.

## Permissions Do Not Appear In System Settings

Presspeech needs Microphone, Accessibility, and Input Monitoring. Use
**Support -> Setup Checklist...** first; its rows open the matching System
Settings pane and request access through Apple's permission APIs.

If a permission row still does not appear:

1. Quit Presspeech.
2. Reopen `/Applications/Presspeech.app`.
3. Open **Support -> Setup Checklist...** and click the row again.
4. If it still does not appear, reset the affected TCC entry:

```sh
tccutil reset Microphone com.local.presspeech
tccutil reset Accessibility com.local.presspeech
tccutil reset ListenEvent com.local.presspeech
```

Then reopen Presspeech and request the permissions again.

For local development, use `swift/dev-run.sh`. It signs
`/tmp/Presspeech-dev.app` with the same bundle identifier and Developer ID
entitlements as the release app, so macOS can associate the same TCC grants
with the dev build.

## Speech Model Fails To Load

Presspeech downloads the Parakeet TDT v3 CoreML speech model and verifies the
cached files on startup. If the download is interrupted or the cache becomes
incomplete, use **Support -> Reset Speech Model Cache...**. Presspeech will
delete only the local model cache and download a fresh verified copy.

If the failure mentions a network timeout or connection problem, check the
network and retry. Audio is not uploaded; the network is used only for the
model download and optional update checks.

## Hotkey Stops Working

- Confirm **Settings -> Hotkey** still shows the intended key.
- Use **Settings -> Hotkey -> Reset Hotkey to Default** to return to Right
  Option.
- Confirm Input Monitoring is granted in System Settings.
- Open **Support -> Setup Checklist...** and retry the Hotkey row.

## System Audio Stays Muted

When **Mute system audio while recording** is enabled, Presspeech writes a
local recovery marker and starts a small local watchdog before muting. If
Presspeech crashes or is force-quit during a recording, the watchdog should
unmute the Mac as soon as the app process disappears. The next launch also
checks for a stale marker and repairs the mute state if needed.

If the Mac still stays muted, unmute it from Control Center or Sound
settings, then use **Support -> Copy Diagnostics** when filing an issue.

## Unexpected Exit Notice

If Presspeech says it reopened after an unexpected exit, it found a local marker
left from the previous run. Nothing is sent anywhere. Use **Copy
Diagnostics** or **Open Log** from the notice if you want to file a GitHub
issue.
