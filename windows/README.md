# Presspeech for Windows

Fast, private, local push-to-talk dictation for Windows — a Windows port of
[presspeech](https://github.com/rcourtman/presspeech) (macOS). Hold a hotkey,
speak, and release. Presspeech normally pastes into the original window when
it can verify that destination; otherwise published 0.1.12 leaves the transcript
on the clipboard for manual paste. A move to another field or browser tab in
the same window may go undetected, so keep the starting field and tab selected
until insertion or recovery. Speech recognition runs on your machine; Presspeech
has no account or cloud transcription service.
The published Windows 0.1.12 build leaves Hugging Face Hub/Transformers default usage
telemetry enabled during model downloads. These libraries may send usage data
to Hugging Face, and model-request metadata includes a random per-process
session ID. Its pinned `huggingface-hub` 1.29.0 client may also make a
best-effort request to `/api/agent-harnesses` if its local registry cache is
missing or stale, then may add an `agent/<id>` label to model
request metadata based on inherited agent-related environment markers.
Model downloads do not send dictation audio or transcripts; the exact telemetry
fields are not independently itemised. Its pinned `hf-xet` 1.6.0
predates the later, separate Xet transfer-telemetry implementation. The 0.1.12
loader also leaves implicit authentication enabled: an available `HF_TOKEN`,
`HUGGING_FACE_HUB_TOKEN`, or token in the local Hugging Face cache may accompany
a model request. It honors inherited `HF_ENDPOINT` and
`HUGGINGFACE_CO_STAGING` settings, so requests can go to a configured endpoint
instead of the public Hub; an available token may accompany the request there.
If `HF_HUB_USER_AGENT_ORIGIN` is set, its value is also added to request
metadata. These public models do not require an account token. Upcoming 0.1.13
disables Hub telemetry before import, pins the public endpoint, clears the
staging/origin settings, disables implicit
authentication, and explicitly sends no account token; see the
[version-scoped
network-call inventory](https://rcourtman.github.io/presspeech/privacy.html#network-calls).
If you prefer to avoid this possible usage telemetry, are concerned that a
token or custom route may be configured, or are unsure, wait for 0.1.13 before
launching 0.1.12. Installing the package alone does not start
the model request; keep the app unopened if you choose to wait.

Both published Windows 0.1.12 and upcoming Windows 0.1.13 use pinned `huggingface-hub` 1.29.0 and HTTPX 0.28.1 with the default `trust_env=True`: `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and `NO_PROXY` affect proxy routing; `SSL_CERT_FILE` and `SSL_CERT_DIR` change TLS CA roots. Upcoming 0.1.13 still honors these proxy and CA settings. A proxy that only tunnels HTTPS sees connection metadata, not request contents. A TLS-inspecting HTTPS proxy trusted by the client can read any 0.1.12 token it receives; upcoming 0.1.13 disables account-token authentication. If a TLS-inspecting HTTPS proxy is in use and its trust is unclear, don't launch while it is in use.

Already used Windows 0.1.12? If you ran a model download with a token available
and an inherited `HF_ENDPOINT` or staging setting may have sent it to a
destination you do not trust—or an untrusted TLS-inspecting HTTPS proxy could read the request—treat the token as disclosed to that destination or proxy. A proxy that only tunnels HTTPS cannot read the token.
Revoke the token and create a replacement at [Hugging Face Access Tokens](https://huggingface.co/settings/token).
Do not include token values in logs or support requests.

Preferred engine: **NVIDIA Parakeet-TDT-0.6B-v3** — the same model family
Presspeech uses on macOS. On an NVIDIA GPU (CUDA) it transcribes with
punctuation and capitalization in a fraction of real time (~50× realtime on an
RTX 3070). Parakeet loads in FP16 on CUDA to halve resident model tensors, with
an automatic FP32 retry if the half-precision load fails. On a fresh PC where
the packaged runtime cannot use CUDA, Presspeech instead selects Whisper
base.en with int8 CPU inference; that smaller default is English-only. The
other Parakeet, Nemotron, and Whisper models remain available in Settings, and
explicit choices are not overridden. Parakeet TDT v3 and Whisper turbo are the
multilingual choices; both are intended for a supported NVIDIA GPU.
If Parakeet fails while transcribing, Presspeech tries Whisper base.en only
when that English-only model is already installed. It does not download an
alternate model automatically; a missing fallback leaves the dictation
undelivered and asks you to retry or choose a model in Settings.

## Install

Use the public [Windows download and verification
guide](https://rcourtman.github.io/presspeech/windows.html#download-verify-run)
for the current self-contained x64 installer and its matching SHA-256. The
source tree can be ahead of the published prerelease; the deployed guide stays
on a version whose installer and checksum are both available.

No Python installation or command-line setup is required. Presspeech installs
per-user under `%LOCALAPPDATA%\Programs\Presspeech`, adds a Start Menu shortcut,
and appears in **Settings → Apps → Installed apps** for normal uninstallation.

The current prerelease is not code-signed. Windows SmartScreen may report
**Unknown publisher**. Follow the public guide's
[download, checksum, and optional release-attestation verification steps](https://rcourtman.github.io/presspeech/windows.html#download-verify-run),
then choose **More info → Run anyway** only if Windows offers that choice and
the guide reports that SHA-256 verification succeeded. Windows 11 Smart App
Control or managed policy may block an unsigned app without offering an
override; do not try to circumvent that policy.

Requirements:

- Windows 11, x64. Presspeech remains compatible with Windows 10 x64, but
  [Microsoft ended general Windows 10 support on 14 October 2025](https://support.microsoft.com/en-us/windows/deployment/updates-lifecycle/windows-10-support-has-ended-on-october-14-2025);
  use it only with Extended Security Updates or an edition that remains supported.
- About 4.4 GB for the app, plus about 141 MiB for the CPU default or 2.5 GB
  for the CUDA Parakeet model cache
- A current NVIDIA driver is recommended for the fastest and most accurate
  multilingual default; Windows PCs without usable CUDA automatically start
  with the smaller English-only Whisper base.en CPU model

Upcoming 0.1.13 asks before downloading missing first-run default model files:
multilingual Parakeet (up to ~2.5 GB) with usable CUDA, or English-only
Whisper base.en (~141 MiB) without usable CUDA. On the Parakeet path, choose
that download, the smaller CPU model, another model in Settings, or
**Set Up Later**. On the CPU path, choose its download, another model in
Settings, or **Set Up Later**. Model files are fetched from `huggingface.co`;
deferring does not start the pending default-model download, and a complete
cached snapshot loads without another prompt. Published 0.1.12 does not include
this choice.

First launch detects whether the packaged Torch runtime can use NVIDIA CUDA.
It selects Parakeet for usable CUDA or Whisper base.en otherwise.
Published 0.1.12 starts a missing selected-model download automatically
on launch, without asking first. Only upcoming 0.1.13 asks you to confirm a
missing first-run default-model download. Once it starts, pinned files are fetched
into `%USERPROFILE%\.cache\huggingface` and loaded and warmed in the background.
Each Presspeech release pins every Windows Hugging Face model to an exact
repository commit reviewed for that app version, so a fresh install cannot
silently receive a different snapshot. Published Windows 0.1.12 does not
independently verify model-file contents against SHA-256. Upcoming 0.1.13
verifies every allowed inference file—including present optional configuration—against its
in-app SHA-256 manifest before loading the model. A mismatch stops loading;
review network and proxy trust before clearing this model's cache and retrying.
On Windows, Presspeech rehashes the reviewed model files on every load. Python
3.12 reports file creation time, not change time, as `st_ctime_ns` on Windows;
a metadata-only verification record could miss a same-size rewrite whose
last-write time was restored. The extra hashing may lengthen model preparation.
The standard HTTPX proxy and CA settings still apply, so a proxy
can observe model-request metadata and block a download, but modified model
bytes fail verification. Upcoming 0.1.13 also applies the following privacy
policy before any model library imports: it fixes downloads to the public
`https://huggingface.co` endpoint, disables Hugging Face Hub telemetry,
disables the hf-xet transfer client, disables implicit authentication and
inherited endpoint/staging/User-Agent-origin settings, and prevents
Transformers from adding a random per-launch session identifier. The packaged
runtime also verifies that rendered Hub headers contain no agent-attribution
label. The pinned `hf-xet` 1.6.0 build has no verified telemetry opt-out, so
Presspeech sets `HF_HUB_DISABLE_XET=1` before imports and checks the bundled Hub's cached
setting and effective Xet availability in the packaged runtime. Hub falls back
to regular HTTP downloads; Hugging Face still observes the model request. Every model
call also declines account tokens and remote model code; Transformers weights
are required to use safetensors. Inherited Hugging Face endpoint or staging
settings therefore cannot redirect the app's pinned model request.
Upcoming 0.1.13 tries the complete pinned inference files locally first, including
when Hub tree metadata is absent. A missing snapshot or required file permits one
anonymous fetch of the reviewed inference files. Missing optional generation,
tokenizer or feature-extractor configuration uses the pinned loader's existing
local defaults; it does not itself trigger a fetch. Present optional JSON is
validated, and required alternative feature-extractor layouts are recognized.
`HF_HUB_OFFLINE=1` and
`TRANSFORMERS_OFFLINE=1` prevent that fallback. Invalid cached JSON, empty files,
permission failures, checksum mismatches and backend parsing errors are reported
without turning them into network retries. In 0.1.13, present required and
optional inference files are SHA-256-verified against the pinned manifest before
they reach a model backend; published 0.1.12 does not have this check.

Whisper loads copied tokenizer/configuration files from a private temporary
folder, preventing an ordinary Hub-cache reset from triggering the library's
separate unpinned tokenizer download. The weight file normally uses a hard link,
which survives cache-path deletion but does not prevent in-place modification.
If hard links are unsupported or the cache and temporary folder are on different
filesystems, loading temporarily copies the weights and needs extra startup time
and disk space up to the selected model's weight size. The folder remains until
model unload and is removed after failed loading or normal unload. A process
crash can leave that private temporary folder behind; no automatic broad cache
cleanup is performed.

The upcoming 0.1.13 first-run readiness window shows model loading, microphone
selection and an on-demand microphone check, a selectable dictation hotkey,
hold-to-talk or press-to-toggle style, global-listener status, and Start with
Windows in one place. Published 0.1.12 checks the selected microphone
automatically during setup and when its selection changes; its Setup has no
dictation-style selector, and Start with Windows is on for a new profile.
Choose **Press to toggle** in Settings and turn startup off before choosing
**Finish Setup**, **Set Up Later**, or closing Setup if you do not want it at
sign-in. In 0.1.13, Setup
leaves the selected device closed until you choose **Check Microphone**;
changing the selected input also does not open it. Start with Windows is off
for a new profile; select it if you want
Presspeech ready after sign-in. Existing saved choices are retained. The hotkey
and style apply immediately and remain selected if setup is deferred. When you
choose **Check Microphone**, the check briefly opens the selected input,
discards its samples in memory, and distinguishes an input level from a
connected-but-silent device or one that cannot be opened. Setup says
**Connecting microphone…** until the input has delivered its first audio
buffer; speak when it says **Listening — speak a few words…**. If it is silent,
unmute it and choose **Check Microphone** again (in published 0.1.12, use
**Check Again** to repeat its automatic check); if it cannot be opened, use the
window's direct links to Windows Microphone Privacy or Sound Input settings
first. If a USB or Bluetooth microphone was disconnected, reconnect it and
choose **Check Microphone** again (or **Check Again** in 0.1.12); Presspeech
refreshes device discovery without requiring a restart. A
specifically selected microphone remains selected while unavailable instead of
silently changing to Automatic or another input. Before opening the microphone
for each recording, Presspeech also confirms that a cached Windows audio-device
index still names the configured microphone, rejecting stale entries when
re-enumeration shows device reordering after reconnect or resume. For
**Automatic**, a changed device list causes a fresh selection instead of
reusing the old index; indistinguishable duplicate device labels are not
cached. If two safe inputs have the same host API and device name, upcoming
0.1.13 cannot identify either as a specific saved choice: the picker omits
both, and an existing saved choice remains visible but cannot be used to open
either microphone while the ambiguity remains. Disconnect one or use **Automatic**
only if either input is acceptable. Wait
until it says the model is ready before the
first dictation. **Try Dictation** remains disabled until then, and **Finish
Setup** requires both the speech model and global hotkey to be ready. If
preparation fails, use **Retry Speech Model**; the window keeps
tracking the retry instead of leaving the previous error on screen. Choose
**Set Up Later** to close the window without marking setup complete; it will
open again on the next launch. Microphone, hotkey, dictation style, and Start
with Windows choices are kept when setup is deferred, and a newly selected
microphone is used immediately by **Try Dictation**. A microphone can still be
connected later and does not block **Finish Setup** once the speech model is
ready. During an active or starting dictation, Setup holds its microphone
selection and **Check Microphone** action until dictation finishes or is
canceled; a check already in progress also postpones a new recording. This
prevents the setup probe and recording from opening microphone streams at the
same time. If the dictation hotkey is pressed before readiness, Presspeech keeps
showing **Preparing speech model…** and does not open the microphone, play
recording cues, mute playback, or claim to be listening. Release and press
again once the preparation indicator disappears.
Setup identifies local-cache checking, download, model loading, and warm-up as
separate preparation phases. While files download, Setup shows the received
bytes and file size for the current transfer when available. This is not a
cumulative model count: Hub transfers can be parallel, and the number and total
size of missing files vary with the local cache. The overall model indicator
stays indeterminate rather than implying whole-model completion; Setup does
not estimate a percentage or remaining time. Screen readers are notified of
phase changes, not each changing byte count.
Before recording, open Windows microphone privacy settings and turn on
**Microphone access**, **Let apps access your microphone**, and **Let desktop
apps access your microphone**. On Windows 11 builds that offer individual
microphone controls for desktop apps, also allow Presspeech there if that
control is shown. Windows may ask for microphone permission on first access;
approve it only if you want Presspeech to use the microphone. If Windows says
the shared settings are managed by your organization, contact your
administrator; Presspeech cannot override that policy. Also confirm the
selected device under
**Settings → System → Sound → Input**.

On keyboard layouts where **Right Alt** enters `@`, `€`, or accented letters,
Windows treats that key as **AltGr**. Presspeech leaves AltGr available for
normal typing and does not start dictation from it. Choose **F8** or another
dictation hotkey in first-run setup.

### Upcoming 0.1.13: delivery recovery

These recovery controls are not part of the 0.1.12 download above. Builds containing
this change retain a finished dictation in process memory when clipboard access
fails, a newer copy replaces it before the paste shortcut, the original target
cannot be used, a paste key is held, or keyboard delivery becomes uncertain.
If the hotkey hook detects a held Ctrl, Shift, Alt, Windows, or V key before
writing, the previous clipboard item remains unchanged. Release the key and
use Delivery Recovery to copy or discard the waiting text. Check the intended field
first: an input error can happen after part or all of the paste has completed.
If Presspeech already knows that the original window is missing, no longer
focused, or elevated, or cannot verify either app's input integrity level,
it also leaves the previous clipboard item unchanged and waits for an explicit
recovery choice. A change after that check may still
leave the dictated text on the current clipboard.
If the original focused window or Win32 control cannot be verified just after
the paste shortcut is submitted, Presspeech also opens Delivery Recovery even
when Windows accepted the shortcut. The text may already be in the original
or another field: check the original and any newly focused field before
copying again. This check cannot identify a different browser tab or
custom-rendered field sharing one HWND,
and it cannot undo a paste or prove that a target consumed it.

If a clipboard write fails after replacement begins, Windows cannot restore
the previous item. Presspeech clears its partial item while it still owns the
clipboard and keeps the dictation for explicit recovery. If Windows rejects
that cleanup, text may remain on the current clipboard; check it before copying
the recovery text.

The keyboard-accessible **Delivery Recovery** window opens without displaying
or copying the dictated words. Check the intended field first, then choose
**Copy for Manual Paste** to copy explicitly or **Discard Dictation** to forget
the recovery copy without changing the clipboard. **Leave Waiting** closes the
window while Presspeech keeps the recovery copy and pauses new recording. A
blocked hotkey or a second launch reopens the window. If **Try Dictation** is
open, its **Review Delivery…** button also reopens the window without copying
anything; it is enabled only while a dictation is waiting. Equivalent **Review
Undelivered Dictation…**, **Copy Undelivered Dictation (replaces clipboard)**,
and **Discard Undelivered Dictation** commands remain in the notification-area
menu; exiting discards the recovery copy. None of these navigation actions silently
overwrites the current clipboard.

**Copy for Manual Paste** does replace the current clipboard item, including
an image or formatted content, and Presspeech does not restore that item.
Choose **Leave Waiting** if you need to save it elsewhere first. Normal
automatic paste also replaces the current clipboard with transcript text;
preservation applies only when Presspeech detects an unsafe destination before
the write. Windows Clipboard History or a third-party manager is not a
guaranteed way to recover an earlier item.

Presspeech's recovery copy stays in process memory, without transcript logs,
settings storage or a recovery file. Depending on where delivery became
uncertain, the attempted paste may already have placed some or all text in the
field or on the clipboard. Every dictation clipboard write, including explicit
recovery, carries Microsoft's
[ExcludeClipboardContentFromMonitorProcessing](https://learn.microsoft.com/en-us/windows/win32/dataxchg/clipboard-formats#cloud-clipboard-and-clipboard-history-formats)
format before the transcript is published. Windows is therefore instructed not
to retain it in Clipboard History or Cloud Clipboard while leaving it on the
current clipboard for Ctrl+V. This operating-system control does not prevent
another local process or third-party clipboard manager from reading the current
item. Clipboard sequence checks reduce replacement races; they do not make
Ctrl+V atomic or acknowledge that the target application consumed the text.
The upcoming build also checks the clipboard receipt immediately before
`SendInput` and after the shortcut returns. A detected change before submission
skips the shortcut; a detected change afterward keeps an in-memory recovery
copy and reports uncertain delivery. A copy after either check can still race
the target's asynchronous paste handling.
Before replacing the clipboard, the upcoming build checks the physical state
of Ctrl, Shift, Alt, Windows, and V as well as its keyboard-hook state. A key
already held at this preflight leaves the previous clipboard item unchanged and
keeps the dictation for explicit recovery. It checks again immediately before
the paste shortcut; a key pressed between checks can still leave the dictated
text on the current clipboard. These point-in-time checks cannot prevent a key
pressed immediately afterward or prove that the target consumed the paste.
Windows can also return zero from a key-state query when access fails, which
cannot be distinguished from an up key at this API boundary.
Review the target field before copying or pasting a retained dictation.

## Install from source

For development, install Python 3.12 and create a project virtual environment:

```bat
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install -r requirements-cuda.txt
run.bat
```

## Use

1. Hold **Right Alt** (configurable).
2. Speak.
3. Release — Presspeech normally pastes the punctuated transcript into the
   window where the recording began. Published 0.1.12 leaves text on the
   clipboard for manual paste if it cannot verify that destination. Upcoming
   0.1.13 keeps an undelivered transcript in memory for explicit Copy or
   Discard; an already-known invalid destination does not change the clipboard.

If using the configured key is inconvenient, select **Dictate** from the
Presspeech notification-area menu to start recording, then select it again to
stop. This menu action toggles recording in either dictation style.

For an in-app, click-driven test that does not type into another app, open
**Try Dictation…** from the notification-area menu. Builds containing the
upcoming 0.1.13 change also provide a **Try Dictation…** button in Settings.
In the scratchpad, use **Dictate** to start and stop, then check the result
there. Use only harmless words: the scratchpad can still use the system
clipboard (see the [clipboard privacy boundary](https://rcourtman.github.io/presspeech/privacy.html#operating-system-clipboard-services)).
In upcoming 0.1.13 builds, Copy and Cut of selected scratchpad text use the
same history/cloud-excluded clipboard path as dictation delivery. Cut removes
the selection only after Presspeech confirms the copy.
In builds containing the upcoming 0.1.13 recovery change, closing Try
Dictation while a finished recording is still transcribing keeps its completed
text in Delivery Recovery instead of silently losing it or pasting into another
app. Choose Copy or Discard there before recording again. Closing the window
while it is still recording cancels that capture without a transcript to recover.

The configured key is reserved for Presspeech while it is running, so it does
not also open a Windows surface or invoke an F8–F12 command in the focused app.
Other keys and AltGr layout input continue to pass through normally.

In published 0.1.12, the short high tone plays before the microphone opens;
allow the device time to start before speaking. The upcoming 0.1.13 build
instead shows **Connecting microphone…** until the input delivers its first
audio buffer. Its high tone then confirms the microphone is delivering input,
and **Listening…** confirms Presspeech is accepting speech after the tone and
playback-muting step. If the input never supplies a buffer, Presspeech closes
it and reports a microphone error instead of claiming to listen. Releasing
before readiness reports that the microphone was not ready. A lower tone
confirms an active recording has stopped. Audio cues are enabled by default
and can be disabled in Settings.
If a press captures too little audio, or Whisper's VAD rejects the recording,
the indicator briefly says **No speech detected — try again** and a Windows
notification points back to Setup's microphone check. If a recognizer instead
returns blank text without a VAD rejection, the indicator says **No text
recognized — try again**; that does not claim the microphone captured silence.
A quick retry cannot be hidden by the previous message's timeout.

After release, silence-aware post-roll stops as early as 80 ms while retaining
the original 400 ms safety ceiling whenever speech is still present. This keeps
final words intact without always paying the full delay. The Try Dictation
button uses this same stop path as the hotkey and notification-area command.

Recordings stop and transcribe automatically at the maximum length selected in
Settings: 1, 2 (the default), 5, or 10 minutes. This bounds in-memory audio and
restores muted playback if Windows misses a hotkey release. Parakeet recordings
longer than 60 seconds are transcribed through overlapping inputs of at most 60
seconds rather than one quadratic full-attention tensor. Model token timestamps
crop each result to its owned interval, preserving boundary context without
guessing at repeated transcript strings.
Press **Escape** during an active recording to cancel it immediately. The
buffered audio is discarded without transcription or clipboard changes; the
same action is available from **Cancel Dictation (Esc)** in the notification
area menu while recording.

The model stays loaded during normal use so every dictation is immediately ready.
The internal unload support is retained for an explicit gaming mode rather than
being triggered merely because dictation has been idle.

When a Moonlight stream is focused, Presspeech automatically uses Moonlight's
clipboard-typing shortcut so transcripts reach the remote host, including macOS.
Microsoft Remote Desktop is also detected automatically and uses its redirected
clipboard with a small reliability delay. Normal Windows apps retain fast Ctrl+V.
These routes depend on the remote client, not just Presspeech: RDP requires
client-to-remote plain-text clipboard redirection to be allowed by the
connection and host policy. If an ordinary local-to-remote text paste fails,
Presspeech cannot override that restriction. Moonlight instead types the local
clipboard text into the remote session through its own shortcut; it does not
require a synchronized remote clipboard. Before relying on either route, test
it with harmless text in a blank, non-submitting remote editor, then keep that
field focused until delivery finishes and inspect it before retrying. The
remote host can receive the text you intentionally deliver. A successful local
Notepad test does not qualify either remote path. See Microsoft's
[RDP clipboard-redirection guidance](https://learn.microsoft.com/en-us/azure/virtual-desktop/redirection-configure-clipboard)
and the [native remote-delivery checklist](../docs/manual-qa.md#windows-release-qualification).
Each recording is bound to the window that was focused when it began. If focus
changes while the model is transcribing, Presspeech does not paste private text
into the wrong window. Published 0.1.12 leaves it on the clipboard; upcoming
0.1.13 keeps it in Delivery Recovery without replacing the prior clipboard
item when the changed focus is known before the write.
Where Windows exposes a separate focused child control, Presspeech also checks
that control before sending Ctrl+V; moving between two native edit controls in
one window then uses Delivery Recovery instead of automatic paste. Custom-drawn
browser and Electron fields can share one Win32 control handle, so this is not
a guarantee of field identity within those apps. Keep the intended field
focused until delivery finishes, and review where the text landed.
If a control is identifiable only at the later delivery check, Presspeech
cannot confirm it was focused when recording began and uses Delivery Recovery.
Upcoming 0.1.13 also uses Delivery Recovery when Windows' focused-control
query fails or disagrees with the foreground window, even if both failed
observations concern the same window. In that case it does not replace the
previous clipboard item if the failure is known before copying. A completed
query with no focused child still uses the window-level check, so this is not
proof that a browser field or tab remained selected.
[Windows prevents](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput#remarks)
a standard app from sending simulated input into an app running as administrator.
Presspeech detects that boundary. Published 0.1.12 leaves the transcript on
the clipboard for manual paste; upcoming 0.1.13 preserves the previous
clipboard item and offers explicit Copy or Discard. You can also reopen the
target without **Run as administrator**. Do not elevate Presspeech to work
around it.

Treat command shells as execution surfaces, not ordinary text fields.
PowerShell, Command Prompt, Windows Terminal, and remote consoles can run
pasted text when it contains a newline. The default After pasting value is
**space**; selecting **newline** can submit a transcript before you inspect it.
Dictate command text into **Try Dictation** or a plain-text editor, review it,
then paste and run it deliberately. Windows Terminal's multiline-paste warning
depends on its settings; do not rely on it as a Presspeech safety boundary.

The **Presspeech** icon in the Windows notification area (bottom-right) includes
**Dictate** (toggle), **Cancel Dictation (Esc)** while recording,
**Try Dictation…** (scratchpad that doesn't paste anywhere), **Setup…**, **Settings…**,
**Check for Updates…**, **Copy Diagnostics**, **Report a Problem…**, **Suggest an
Improvement…**, **Repair Global Hotkey**, and **Exit**. Upcoming 0.1.13 / builds
containing **Test App Compatibility…** also open the privacy-safe repeated
target-app guide. **Report a Problem…** and **Suggest an Improvement…** both
open the repository's [SUPPORT.md](../SUPPORT.md) guide, not an issue form;
neither submits a report or attaches diagnostics. Follow the guide's current
GitHub availability notice before using any separately linked issue form.
Neither action adds app or user data to the support-guide URL. If no public
route accepts the report, keep it private and follow the guide's local-draft
steps rather than posting elsewhere. The icon turns red
while recording. Setup, settings, update, and scratchpad controls expose names,
roles, values, and actions through Windows UI Automation for screen readers.
Each window starts focus on its main working control. Use **Left Alt** plus a
command's underlined letter to invoke it without tabbing, **Escape** to close the
current window (and cancel an active update download), and **Ctrl+S** to save
Settings. While one of these windows is open, screen readers also announce
important asynchronous status changes such as model readiness, microphone check
results, update completion or failure, and settings save results without moving
keyboard focus. Download byte counters remain visual rather than repeatedly
interrupting speech.
For a repeatable native Windows UI Automation and Narrator sign-off, see the
[accessibility QA checklist](ACCESSIBILITY-QA.md); mocked unit tests do not
replace this platform check.
The Try Dictation scratchpad also keeps its Dictate command and live status in
sync when recording is stopped by the hotkey, Escape, the recording limit, or
an input failure. While a model is preparing, transcription is finishing, or
delivery recovery is required, it explains why another recording is not yet
available instead of leaving a stale actionable label. Its **Review Delivery…**
command gives an in-window keyboard path back to the recovery choices if that
window was closed. Its named editor and
visible transcript scrollbar remain reachable with Narrator and keyboard-only
navigation, and its initial size scales up without extending beyond the current
desktop.
Windows may place the icon in the notification-area overflow. If the icon is
hard to find, launch Presspeech again from the Start Menu: the running app
restores its existing window, opens Setup during first run, or opens Settings
after setup. It does not start a second dictation process.

If the configured key stops responding while menu-based **Dictate** still
works, choose **Repair Global Hotkey** from the notification-area menu, Setup,
or Settings. Presspeech replaces the Windows keyboard listener even when its
thread still appears healthy, then reports whether the new listener started.
Upcoming 0.1.13 also dispatches ordered press/release actions to a dedicated
worker so target discovery, recording setup, logging and recovery UI never run
inside Windows' time-limited low-level keyboard-hook callback.
In source builds preparing 0.1.13, locking or disconnecting the Windows session
or suspending the PC discards an active capture without transcribing it. After
unlocking, reconnecting, or an interactive resume, Presspeech replaces the
global hotkey listener even if its thread still appears alive; an in-progress
dictation or cancellation defers replacement until it finishes. If automatic
reconnection cannot complete, the status and notification point back to
**Repair Global Hotkey**. This is not part of the published 0.1.12 build.
Listener failures are also announced and included in privacy-safe diagnostics;
callback details and pressed keys are not included.

## Settings

Settings can be edited while Presspeech is busy, but **Save** waits until the
current recording, cancellation, transcription, and delivery have finished.
This keeps one dictation on one coherent microphone, model, hotkey, and text
processing configuration. The window announces when saving becomes available
again; unsaved edits remain in place while it waits.

- Hotkey: right/left Alt, Ctrl, Shift, Win, or F8–F12
- Trigger: hold-to-talk or press-to-toggle
- Maximum recording length: 1, 2 (default), 5, or 10 minutes
- Microphone: automatic selection or a specific safe Windows input device
- Engine/model: multilingual Parakeet TDT v3 or Whisper turbo (NVIDIA GPU
  recommended), or English-only Nemotron and Whisper small.en, medium.en, and
  base.en (base.en is the CPU first-run default). Whisper turbo detects the
  language of each dictation; the English-only Whisper models stay fixed to
  English.
- After pasting: space / newline / nothing (newline can submit text in a command
  shell; review commands outside the shell first)
- Remove filler words (um, uh, er, …)
- British English spelling (color → colour, realize → realise)
- Audio cues when dictation starts and stops
- Mute every active Windows playback endpoint while recording, restoring each
  device's previous mute state afterwards
- A click-through **Connecting microphone… / Listening… / Transcribing…**
  indicator on the active display in the upcoming 0.1.13 build
- Optional GitHub update checks at startup when the recorded check is at least
  24 hours old. Published 0.1.12 records only successful checks, so a failed
  check can repeat after each restart; upcoming 0.1.13 records automatic
  attempts before connecting and offers unrestricted manual retries. Downloads
  and installation require approval, mutable releases are ignored, and the
  installer is verified by size and SHA-256 after download and again
  immediately before launch. Upcoming 0.1.13 postpones installer launch while
  a dictation is starting, recording, canceling, transcribing, or waiting in
  Delivery Recovery. The verified installer stays in the update window for an
  explicit **Install Update** retry after the dictation is resolved; closing
  the update window removes that temporary download
- Dictionary: map a misheard phrase or spoken shortcut to exact text
  (e.g. "press speech" → `presspeech`), applied deterministically
- Start with Windows (registry `HKCU\...\Run`)

Saving a different speech model starts downloading/loading and warming it in
the background immediately. Settings shows whether the selected model is being
prepared, is ready, or needs attention, and offers a retry after a failure.
Dictation remains unavailable until the selected model reports ready; there is
no need to sacrifice a hotkey press to start the change or restart Presspeech.
A microphone selection can be edited during an active recording, but **Save**
waits until capture, transcription, and delivery finish. Saving then applies
the selection to the next dictation; the recording already in progress stays
on the input it opened.

If the **Start with Windows** choice cannot be applied, **Finish Setup** keeps
Setup open and Settings reports that the startup state was not updated instead
of claiming success. Use **Open Startup Settings** to review Presspeech under
Windows **Settings → Apps → Startup**, then retry **Finish Setup** or **Save**.
In the upcoming 0.1.13 build, leaving Start with Windows off on a new profile
does not require an existing per-user startup registry key; opting in creates
it if needed. A failed first-run registration also leaves
Setup incomplete, so it reopens on the next launch. **Set Up Later** can close
Setup after an explicit warning if the startup setting cannot be fixed now.

## Notes

- A working microphone must be connected. Automatic selection prefers the
  Windows Sound Mapper, skips virtual/loopback and WDM-KS devices, and resamples
  to 16 kHz. A specific safe input can be selected in Settings when its host
  API and device name distinguish it from other safe inputs.
- Single-instance (named mutex) — launching again reuses the running process
  and restores its open window, or opens Setup before first-run completion and
  Settings afterward.
- All audio is processed in memory and discarded after transcription.
- Transcript content is never written to logs; diagnostics retain timings and
  character counts only.
- **Copy Diagnostics** includes configuration counts, runtime state, and
  microphone availability, never transcripts, audio, dictionary contents,
  exact microphone names, raw error details, or raw log lines.
- Upcoming 0.1.13 marks transcript, recovery, Try Dictation scratchpad Copy/Cut,
  and user-requested diagnostics copies for exclusion from Windows Clipboard
  History and Cloud Clipboard;
  they remain available for local Ctrl+V. Published 0.1.12 does not apply this
  exclusion, and other local clipboard readers remain a separate boundary.
- `python app.py --selftest` verifies the engine pipeline.
- `python benchmark.py` runs the repeatable local latency/accuracy evaluation;
  reports include one aggregate input digest so paired runs can
  confirm the same effective audio, references, and scoring labels, rather
  than relying on matching file names alone;
  Whisper reports include the exact Silero VAD boundary policy so WER, quiet
  speech rejection, and silence false positives remain comparable across
  dependency updates. Reports also preserve the requested language policy and
  each speech-bearing Whisper trial's language result. On reviewed, labelled
  Whisper-turbo clips run with `--language auto`, they also separate correct,
  incorrect, missing, and VAD-rejected language-ID trials by language and task;
  this diagnostic is not an accuracy score. See the
  reviewed-reference workflow in `benchmarks/README.md`. A manifest sample
  marked with both `"expected_silence": true` and
  `"reference_reviewed": true` is scored as a non-speech fixture; reports count
  any non-empty transcript as a silence false positive. Whisper reports also
  record the VAD-retained speech duration for every trial and count reviewed
  speech clips that VAD rejected, so silence fixes cannot hide quiet-speech
  regressions behind aggregate WER. Reviewed speech clips score
  first- and final-word retention on every reviewed trial as well, including
  the full count of adjacent repeated boundary words, so
  intermittent differences at either dictation boundary cannot be hidden by
  the consensus transcript. Inspect the audio to distinguish clipping from
  substitutions or insertions.
- If you see missing-DLL errors, install the Visual C++ Redistributable
  (x64) from Microsoft.

## Develop and test

Always run the Windows code with its project virtual environment:

```bat
.venv\Scripts\python -m unittest discover -s tests -v
.venv\Scripts\python app.py --selftest
```

The unit tests do not load a speech model. The self-test does, and therefore
also verifies the installed Torch/CUDA/model pipeline. Local benchmark audio,
results, virtual environments, caches, and logs are ignored by Git.

## Build the installer

Release builds use CPython 3.12.10 (the final 3.12 release with Windows
installers) and the fully resolved Windows dependency
set in `requirements-release.txt`; source-development installs intentionally
retain the lower bounds in `requirements.txt`. Install Inno Setup 6, create a
clean release environment, then run the build script from `windows/`:

```powershell
winget install --id JRSoftware.InnoSetup --exact
py -3.12 -m venv .release-venv
$env:PIP_CONFIG_FILE = 'NUL'
.\.release-venv\Scripts\python -m pip install --isolated --no-deps --only-binary=:all: --require-hashes --index-url https://pypi.org/simple -r requirements-release.txt
.\.release-venv\Scripts\python -m pip install --isolated --no-deps --only-binary=:all: --require-hashes -r requirements-cuda-release.txt
.\.release-venv\Scripts\python -m pip check
powershell -ExecutionPolicy Bypass -File .\build-release.ps1 `
  -Version 0.1.12 -Python .\.release-venv\Scripts\python.exe
```

For source builds containing this hash lock, `requirements-release.txt` lists
reviewed SHA-256 artifact hashes for every PyPI package. The committed lock
currently selects one Windows-compatible wheel per package; regeneration with
`uv pip compile --generate-hashes` can list additional artifacts of the same pinned version.
Review that hash set with the dependency update. The same compiler derives the
CUDA release lock from the source Torch pin and the official index checksum for
the Windows CPython 3.12 wheel. Source development keeps using the separate
version/index requirement without imposing that release artifact on other interpreters.
Installation permits only
compatible wheels, never source archives or dependency resolution. Both CI and
release builds install the separate `requirements-cuda-release.txt` artifact lock
for the CPython 3.12 Windows x86-64 Torch wheel and verify the complete runtime;
`pip check` alone cannot detect an omitted optional Torch dependency.
Pip runs in isolated mode and `PIP_CONFIG_FILE=NUL` disables all configuration
files, including global and environment-specific settings. This does not
retroactively qualify older published installers.
`build-release.ps1` refuses to package with a different Python patch or any
missing/drifted dependency. When an intentional dependency update changes an
input requirements file, install
[uv](https://docs.astral.sh/uv/) and regenerate the resolved, hash-locked set
from the repository root with
`python windows/release_requirements.py`.

The build uses a short temporary staging path to avoid Windows path-length
failures and writes the installer plus checksum under `dist\installer`. Build
outputs remain ignored by Git. Before creating the installer, the build runs a
model-free smoke test through the frozen executable to verify that its lazy ASR
backends and native runtime modules were actually packaged. The manual
`windows-release` GitHub workflow builds and publishes a Windows prerelease. Its
`expected_sha` input must be the exact 40-character `main` commit being released;
the workflow stops before the build if it was dispatched from another ref, the
branch has moved, the repository or Windows push workflows are not green for
that exact commit, or the version's existing release tag points to a different
commit. Same-version jobs are serialized, and the tag is verified again after
the build before creating the release. The installer and checksum are uploaded
while the release is still a draft, and it is published only after both uploads
succeed. If the atomic create command fails after leaving a private draft, the
same job inspects and completes it before discarding the exact build outputs. A
later rerun resumes a remaining draft only after its tag, title, prerelease
state, target commit, release notes, and any existing assets exactly match the
approved release. It uploads only missing fixed-name assets without clobbering,
then revalidates both the complete draft and the release tag immediately before
publication. Existing published assets are never replaced: a rerun must
reproduce them exactly. The workflow compares GitHub's published asset names,
sizes,
SHA-256 digests, and download URLs with the local installer and checksum before
reporting a successful release. It also requires GitHub to report the release
as immutable and uses `gh release verify` plus `gh release verify-asset` for
both files, so a successful run has checked the generated release attestation
as well as REST metadata.

Before publication, the hosted Windows runner installs the finished installer
into a temporary directory, checks its version and uninstall registration,
runs the installed executable's model-free package test, creates the startup
entry the installed app would use, and confirms uninstall removes it.
Any failure blocks publication; install/uninstall logs are retained and printed
in the workflow diagnostics. This checks the packaged installation lifecycle,
not microphone capture, first dictation, upgrades, or interaction with target apps.
`pwsh -File windows/smoke-installer.ps1 -SelfTest` exercises its guards and process
handling without installing anything. Real qualification refuses developer and
self-hosted machines, including machines with an existing Presspeech installation.
A different `/DIR` alone would still share [Inno Setup's production uninstall identity](https://jrsoftware.org/ishelp/topic_setup_appid.htm).
If the repository later receives a code-signing
certificate, add its base64 PFX and password as
`WINDOWS_CERTIFICATE_BASE64` and `WINDOWS_CERTIFICATE_PASSWORD`; the same build
automatically signs both the app executable and installer.
