"""Presspeech for Windows - local push-to-talk dictation.

Hold a hotkey, speak, release, and the transcript is typed at the cursor.
Everything runs locally (Whisper via faster-whisper); no cloud, no accounts.
"""

import ctypes
import importlib
import math
import os
import platform
import re
import subprocess
import struct
import sys
import threading
import time
import traceback
import winsound
from concurrent.futures import ThreadPoolExecutor
from typing import NamedTuple

import numpy as np
import soxr
import sounddevice as sd
import pyperclip
from pynput import keyboard as pkb
from PIL import Image, ImageDraw
from pystray import Icon, Menu, MenuItem

import config as cfg
import engine
import ui
import updates
from british import to_british

KEY_MAP = {
    "right alt": {pkb.Key.alt_gr, pkb.Key.alt_r},
    # Windows/pynput can report the physical Right Alt key as either alt_gr or
    # alt_r. AltGr must not also satisfy the explicit Left Alt choice: doing so
    # can start dictation while a user types alternate-layout characters.
    "left alt": {pkb.Key.alt_l},
    "right ctrl": {pkb.Key.ctrl_r},
    "left ctrl": {pkb.Key.ctrl_l},
    "right shift": {pkb.Key.shift_r},
    "left shift": {pkb.Key.shift_l},
    "right win": {pkb.Key.cmd_r},
    "left win": {pkb.Key.cmd_l},
    "f8": {pkb.Key.f8},
    "f9": {pkb.Key.f9},
    "f10": {pkb.Key.f10},
    "f11": {pkb.Key.f11},
    "f12": {pkb.Key.f12},
}

FILLER_RE = re.compile(
    r"(?<![\w'-])(?:um+|uh+|ah+|er|erm|hm+)(?![\w'-])", re.IGNORECASE
)

FILLER_BOUNDARY_WRAPPERS = "\"'\u201c\u201d\u2018\u2019([{"
FILLER_SENTENCE_TERMINATORS = ".!?"
FILLER_ORPHAN_SEPARATORS = ",.;:!?"

SINGLE_INSTANCE_MUTEX = "Local\\PresspeechSingleInstance"
SINGLE_INSTANCE_ACTIVATE_EVENT = "Local\\PresspeechActivate"

POST_ROLL_MIN_SEC = 0.08
POST_ROLL_MAX_SEC = 0.4
POST_ROLL_CHECK_SEC = 0.04
POST_ROLL_TAIL_SEC = 0.09
POST_ROLL_ABS_SILENCE_RMS = 0.003
POST_ROLL_RELATIVE_SILENCE = 0.06
POST_ROLL_MAX_SILENCE_RMS = 0.012
# Backwards-compatible conservative value used by the benchmark's worst-case estimate.
POST_ROLL_SEC = POST_ROLL_MAX_SEC
MICROPHONE_CHECK_LISTEN_SEC = 2.0
MICROPHONE_CHECK_AUDIO_RMS = POST_ROLL_ABS_SILENCE_RMS
MICROPHONE_CHECK_LEVEL = "level"
MICROPHONE_CHECK_SILENT = "silent"
MICROPHONE_CHECK_UNAVAILABLE = "unavailable"
MODEL_WARMUP_SEC = 8.0
MODEL_IDLE_WAKE_SEC = 60.0
CPU_FIRST_RUN_MODEL = "base.en"
PASTE_DELAY_SEC = 0.01
RDP_PASTE_DELAY_SEC = 0.08
NO_SPEECH_FEEDBACK_SEC = 2.5
NO_SPEECH_OUTCOME = "no_speech"

VK_ESCAPE = 0x1B
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
LLKHF_LOWER_IL_INJECTED = 0x00000002
LLKHF_INJECTED = 0x00000010
LLKHF_INJECTED_MASK = LLKHF_LOWER_IL_INJECTED | LLKHF_INJECTED

# The low-level Windows hook reports virtual-key codes before pynput creates
# Key values. Keep this explicit mapping so a configured PTT transaction can
# be handled by Presspeech and withheld from the focused application. Without
# that isolation, Right/Left Win opens Start and F8-F12 can invoke an app
# command, moving or changing the destination while dictation is in progress.
HOTKEY_VIRTUAL_KEYS = {
    "left shift": (0xA0, pkb.Key.shift_l),
    "right shift": (0xA1, pkb.Key.shift_r),
    "left ctrl": (0xA2, pkb.Key.ctrl_l),
    "right ctrl": (0xA3, pkb.Key.ctrl_r),
    "left alt": (0xA4, pkb.Key.alt_l),
    "right alt": (0xA5, pkb.Key.alt_r),
    "left win": (0x5B, pkb.Key.cmd_l),
    "right win": (0x5C, pkb.Key.cmd_r),
    "f8": (0x77, pkb.Key.f8),
    "f9": (0x78, pkb.Key.f9),
    "f10": (0x79, pkb.Key.f10),
    "f11": (0x7A, pkb.Key.f11),
    "f12": (0x7B, pkb.Key.f12),
}

MOONLIGHT_PROCESSES = {"moonlight.exe"}
RDP_PROCESSES = {"mstsc.exe", "msrdc.exe"}

AUTO_INPUT_DEVICE = "auto"
MICROPHONE_PRIVACY_SETTINGS_URI = "ms-settings:privacy-microphone"
DEFAULT_INPUT_SETTINGS_URI = "ms-settings:sound-defaultinputproperties"
STARTUP_SETTINGS_URI = "ms-settings:startupapps"
BUG_REPORT_URL = (
    "https://github.com/rcourtman/presspeech/issues/new?template=bug_report.yml"
)
FEATURE_REQUEST_URL = (
    "https://github.com/rcourtman/presspeech/issues/new?template=feature_request.yml"
)
INPUT_DEVICE_SKIP_WORDS = (
    "stereo mix", "steam", "stream", "virtual", "loopback", "aux", "line in",
    "hyperx",
)
UNSAFE_INPUT_HOST_APIS = ("wdm-ks",)

LOG_PATH = os.path.join(cfg.CONFIG_DIR, "log.txt")
UPDATE_CHECK_INTERVAL_SEC = 24 * 60 * 60

# The frozen app imports UI and capture dependencies at startup. Exercise the
# model backends and other lazy imports explicitly before an installer can be
# created, without downloading weights or opening the microphone.
PACKAGE_SMOKE_IMPORTS = (
    ("torch", ("cuda",)),
    ("transformers", (
        "AutoModelForRNNT",
        "AutoModelForTDT",
        "AutoProcessor",
        "MoonshineStreamingForConditionalGeneration",
    )),
    ("faster_whisper", ("WhisperModel",)),
    ("onnxruntime", ("InferenceSession",)),
    ("ctranslate2", ()),
    ("sentencepiece", ("SentencePieceProcessor",)),
    ("tokenizers", ("Tokenizer",)),
    ("safetensors", ("safe_open",)),
    ("librosa", ("resample",)),
    ("soxr", ("resample",)),
    ("soundfile", ("SoundFile",)),
    ("comtypes", ()),
    ("pycaw.constants", ("AudioDeviceState", "EDataFlow")),
    ("pycaw.pycaw", ("AudioUtilities",)),
    ("tk_uia", ("add_acc_object", "enable", "label_for", "set_acc_name")),
)


class PasteTarget(NamedTuple):
    process_name: str
    window_handle: int
    process_identifier: int = 0


def _update_check_due(last_check_epoch, now_epoch=None):
    """Return whether the privacy-safe daily update check is due."""
    now_epoch = time.time() if now_epoch is None else now_epoch
    try:
        last_check_epoch = float(last_check_epoch or 0)
    except (TypeError, ValueError):
        return True
    return now_epoch - last_check_epoch >= UPDATE_CHECK_INTERVAL_SEC


def _filler_capitalization_targets(text, matches):
    """Locate sentence starts whose capitalized filler carried the casing."""
    targets = set()
    for match in matches:
        filler = match.group(0)
        if not filler or not filler[0].isupper():
            continue
        index = match.start()
        while index > 0:
            previous = text[index - 1]
            if previous.isspace() or previous in FILLER_BOUNDARY_WRAPPERS:
                index -= 1
                continue
            if previous in FILLER_SENTENCE_TERMINATORS:
                targets.add(sum(
                    character in FILLER_SENTENCE_TERMINATORS
                    for character in text[:index]))
            break
        else:
            targets.add(0)
    return targets


def _restore_filler_capitalization(text, targets):
    if not text or not targets:
        return text
    result = []
    terminator_ordinal = 0
    should_capitalize = 0 in targets
    for character in text:
        if should_capitalize:
            if character.islower():
                result.append(character.upper())
                should_capitalize = False
                continue
            if character.isalpha() or character.isdigit():
                should_capitalize = False

        result.append(character)
        if character in FILLER_SENTENCE_TERMINATORS:
            terminator_ordinal += 1
            if terminator_ordinal in targets:
                should_capitalize = True
        elif (should_capitalize and not character.isspace()
              and character not in FILLER_BOUNDARY_WRAPPERS
              and character not in FILLER_ORPHAN_SEPARATORS):
            should_capitalize = False
    return "".join(result)


def _remove_fillers(text):
    """Remove conservative filler words and repair their punctuation/casing."""
    matches = list(FILLER_RE.finditer(text))
    if not matches:
        return text
    capitalization_targets = _filler_capitalization_targets(text, matches)
    result = FILLER_RE.sub("", text)
    # Removing a filler must not leave comma runs, punctuation pairs, or a
    # lowercase sentence start when the filler carried the capital letter.
    result = re.sub(r"\s*,(?:\s*,)+", ",", result)
    result = re.sub(r"([.!?])\s+[,.;:!?]+\s*", r"\1 ", result)
    result = re.sub(r"\s+([.,!?;:])", r"\1", result)
    result = re.sub(r",+([.!?;:])", r"\1", result)
    result = re.sub(r"\s+", " ", result)
    result = re.sub(r"^[\s,.;:!?]+", "", result).strip()
    return _restore_filler_capitalization(result, capitalization_targets)


def _apply_dictionary_rules(text, rules):
    """Apply longest non-overlapping rules once against the original text."""
    active = [
        (index, spoken, replacement)
        for index, (spoken, replacement) in enumerate(
            cfg.validated_dictionary(rules) or [])
    ]
    # Prefer the most specific phrase regardless of the order rules were added.
    # The original order remains a deterministic tie-breaker for equal phrases.
    active.sort(key=lambda rule: (-len(rule[1]), rule[1].casefold(), rule[0]))
    matches = []
    for _index, spoken, replacement in active:
        pattern = r"(?<!\w)%s(?!\w)" % re.escape(spoken)
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            start, end = match.span()
            if any(start < other_end and other_start < end
                   for other_start, other_end, _replacement in matches):
                continue
            matches.append((start, end, replacement))

    # Every range belongs to the untouched transcript, so replacement text is
    # inserted literally and can never trigger a later dictionary rule.
    for start, end, replacement in sorted(matches, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def _make_cue_wave(frequency, duration=0.055, volume=0.14, sample_rate=24000):
    """Build a short, softly faded mono WAV for native Windows playback."""
    frame_count = int(duration * sample_rate)
    fade_in = max(1, int(0.006 * sample_rate))
    fade_out = max(1, int(0.014 * sample_rate))
    frames = bytearray(frame_count * 2)
    for i in range(frame_count):
        envelope = min(1.0, i / fade_in, (frame_count - 1 - i) / fade_out)
        value = int(32767 * volume * envelope *
                    math.sin(2.0 * math.pi * frequency * i / sample_rate))
        struct.pack_into("<h", frames, i * 2, value)
    header = (
        b"RIFF" + struct.pack("<I", 36 + len(frames)) + b"WAVEfmt " +
        struct.pack("<IHHIIHH", 16, 1, 1, sample_rate,
                    sample_rate * 2, 2, 16) +
        b"data" + struct.pack("<I", len(frames))
    )
    return header + bytes(frames)


CUE_SOUNDS = {
    "start": _make_cue_wave(880),
    "stop": _make_cue_wave(620),
}


def _mute_active_playback():
    """Mute every active render endpoint and return their prior states."""
    import comtypes
    from pycaw.constants import AudioDeviceState, EDataFlow
    from pycaw.pycaw import AudioUtilities

    comtypes.CoInitialize()
    try:
        saved = []
        failures = []
        devices = AudioUtilities.GetAllDevices(
            data_flow=EDataFlow.eRender.value,
            device_state=AudioDeviceState.Active.value,
        )
        for device in devices:
            try:
                volume = device.EndpointVolume
                was_muted = bool(volume.GetMute())
                volume.SetMute(1, None)
                if not bool(volume.GetMute()):
                    raise RuntimeError("mute state did not change")
                saved.append((device.id, was_muted))
            except Exception as exc:
                failures.append("%s: %s" % (device.FriendlyName, exc))
        if not saved:
            detail = "; ".join(failures) if failures else "no active playback endpoints"
            raise RuntimeError(detail)
        return saved, failures
    finally:
        comtypes.CoUninitialize()


def _restore_playback_mutes(saved_states):
    """Restore every saved render endpoint mute state."""
    import comtypes
    from pycaw.constants import EDataFlow
    from pycaw.pycaw import AudioUtilities

    comtypes.CoInitialize()
    try:
        devices = {
            device.id.lower(): device
            for device in AudioUtilities.GetAllDevices(
                data_flow=EDataFlow.eRender.value)
        }
        restored = 0
        failures = []
        for endpoint_id, was_muted in saved_states:
            device = devices.get(endpoint_id.lower())
            if device is None:
                failures.append("%s: endpoint disappeared" % endpoint_id)
                continue
            try:
                device.EndpointVolume.SetMute(1 if was_muted else 0, None)
                restored += 1
            except Exception as exc:
                failures.append("%s: %s" % (device.FriendlyName, exc))
        return restored, failures
    finally:
        comtypes.CoUninitialize()


def _resample_to_16k(audio, from_rate):
    """Band-limit and resample a mono float32 array to the ASR rate."""
    if from_rate == 16000:
        return audio
    if len(audio) < 2:
        return np.asarray(audio, dtype=np.float32)
    # Microphones commonly expose 44.1/48/96 kHz streams. Decimation or linear
    # interpolation without a low-pass filter folds content above 8 kHz into
    # the speech band. SoXR HQ is fast band-limited sinc interpolation and is
    # already part of the locked Windows runtime.
    converted = soxr.resample(
        np.asarray(audio, dtype=np.float32), from_rate, 16000, quality="HQ")
    return np.asarray(converted, dtype=np.float32)


def _foreground_paste_target():
    """Return the foreground window and its executable as one snapshot."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                    ctypes.POINTER(wintypes.DWORD)]
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                         wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return PasteTarget("", 0)
        process_id = wintypes.DWORD()
        if not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id)):
            return PasteTarget("", int(hwnd))
        handle = kernel32.OpenProcess(0x1000, False, process_id.value)
        if not handle:
            return PasteTarget("", int(hwnd), int(process_id.value))
        try:
            size = wintypes.DWORD(32768)
            path = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(
                    handle, 0, path, ctypes.byref(size)):
                return PasteTarget("", int(hwnd), int(process_id.value))
            return PasteTarget(
                os.path.basename(path.value).lower(), int(hwnd),
                int(process_id.value))
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return PasteTarget("", 0)


def _paste_target_matches(expected, current):
    """Match an exact window owner, using its executable if PID is unavailable."""
    if (not expected.window_handle or
            current.window_handle != expected.window_handle):
        return False
    if expected.process_identifier:
        return current.process_identifier == expected.process_identifier
    if expected.process_name:
        return current.process_name == expected.process_name
    return False


def _paste_route(process_name):
    """Choose a paste method for the application receiving the transcript."""
    name = (process_name or "").lower()
    if name in MOONLIGHT_PROCESSES:
        return "moonlight"
    if name in RDP_PROCESSES:
        return "rdp"
    return "local"


def _autostart_command(executable, source_path, frozen=False):
    """Return the registry command for source and packaged installations."""
    executable = os.path.abspath(executable)
    if frozen:
        return '"%s"' % executable
    if executable.lower().endswith("python.exe"):
        candidate = os.path.join(os.path.dirname(executable), "pythonw.exe")
        if os.path.exists(candidate):
            executable = candidate
    return '"%s" "%s"' % (executable, os.path.abspath(source_path))


def _startup_model(settings, cuda_available):
    """Choose a usable first-run default without overriding later choices."""
    configured = settings["model"]
    if (not settings.get("setup_complete", True)
            and not settings.get("model_explicit", False)
            and configured == cfg.DEFAULTS["model"]
            and not cuda_available):
        return CPU_FIRST_RUN_MODEL
    return configured


def _model_timing_summary(timing):
    """Format privacy-safe backend timing, including Whisper VAD retention."""
    if not isinstance(timing, dict) or not timing:
        return None
    speech_seconds = timing.get("speech_seconds")
    speech = ("-" if not isinstance(speech_seconds, (int, float))
              else "%.3fs" % speech_seconds)
    return (
        "model detail: backend=%s bucket=%s speech=%s lock=%.3fs "
        "prepare=%.3fs transfer=%.3fs generate=%.3fs decode=%.3fs" % (
            timing.get("backend", ""),
            timing.get("bucket_seconds", "-"),
            speech,
            timing.get("lock_wait", 0.0),
            timing.get("prepare", 0.0),
            timing.get("transfer", 0.0),
            timing.get("generate", timing.get("inference", 0.0)),
            timing.get("decode", 0.0),
        )
    )


def _make_icon(color):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((3, 3, 61, 61), fill=color + (255,))
    d.rounded_rectangle((23, 13, 41, 33), radius=7, fill=(255, 255, 255, 255))
    d.ellipse((19, 29, 45, 45), fill=(255, 255, 255, 255))
    d.rounded_rectangle((28, 38, 36, 52), radius=4, fill=(255, 255, 255, 255))
    d.ellipse((24, 48, 40, 56), fill=(255, 255, 255, 255))
    return img


class PresspeechApp:
    def __init__(self):
        self.settings = cfg.load()
        self.transcriber = engine.Transcriber(
            precision=self.settings.get("precision", "fp16"))
        self.buffer = []
        self.stream = None
        self.recording = False
        self._canceling_recording = False
        self.transcribing = False
        self.lock = threading.Lock()
        self.scratchpad = None
        self.settings_window = None
        self.setup_window = None
        self.update_window = None
        self.pending_update = None
        self.model_status = "pending"
        self.model_status_detail = "Waiting to load"
        self._model_retry_lock = threading.Lock()
        self._model_load_target = None
        self._model_load_generation = 0
        self._update_lock = threading.Lock()
        self.icon = None
        self.listener = None
        self._mutex_handle = None
        self._activation_event_handle = None
        self._key_held = False
        self._pressed_keys = set()
        self._held_hotkey_keys = frozenset()
        self._held_hotkey_trigger = None
        self._suppress_escape_keyup = False
        self._filter_pressed_vks = set()
        self._passthrough_hotkey_vks = set()
        self._suppressed_hotkey_vks = {}
        self._injecting_keys = False
        self._recording_paste_target = PasteTarget("", 0)
        self._recording_scratchpad = None
        self._rec_epoch = 0
        self._recording_limit_timer = None
        self._peak_rms = 0.0
        self._last_model_use = 0.0
        self._wake_in_progress = False
        self._wake_lock = threading.Lock()
        # Keep every model operation on one permanent OS thread. CUDA/cuDNN
        # execution state is thread-affine enough that creating a fresh worker
        # per dictation costs roughly one second even with fixed input shapes.
        self._model_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="presspeech-model")
        self._model_idle_epoch = 0
        self._playback_mute_lock = threading.Lock()
        self._playback_restore = None
        self.indicator = ui.DictationIndicator()
        self.input_device = None
        self.idle_icon = _make_icon((140, 140, 140))
        self.rec_icon = _make_icon((225, 60, 60))

    # ---------------- lifecycle ----------------

    def run(self):
        if not self._single_instance():
            print("Presspeech is already running; opening its controls.")
            sys.exit(0)
        threading.Thread(
            target=self._watch_activation_requests,
            name="presspeech-activation",
            daemon=True,
        ).start()
        self.icon = Icon(
            "Presspeech",
            self.idle_icon,
            "Presspeech - push-to-talk dictation",
            menu=Menu(
                MenuItem("Dictate", self.toggle_dictate, default=True),
                MenuItem(
                    "Cancel Dictation (Esc)", self.cancel_recording,
                    enabled=lambda _item: self.recording),
                MenuItem("Try Dictation\u2026", self.open_scratchpad),
                MenuItem("Setup\u2026", self.open_setup),
                MenuItem("Settings\u2026", self.open_settings),
                Menu.SEPARATOR,
                MenuItem("Check for Updates\u2026", self.check_for_updates),
                MenuItem("Copy Diagnostics", self.copy_diagnostics),
                MenuItem("Report a Problem\u2026", self.report_problem),
                MenuItem("Suggest an Improvement\u2026", self.suggest_improvement),
                Menu.SEPARATOR,
                MenuItem("Exit", self.exit_app),
            ),
        )
        threading.Thread(target=self.icon.run, daemon=True).start()
        self.listener = pkb.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
            win32_event_filter=self._win32_event_filter,
        )
        self.listener.start()
        self._log("running; hotkey=%s trigger=%s" % (self.settings["hotkey"], self.settings["trigger"]))
        with self._model_retry_lock:
            self._queue_model_load_locked(None)
        if not self.settings.get("setup_complete", False):
            threading.Timer(0.8, self.open_setup).start()
        if (self.settings.get("check_updates", True) and
                _update_check_due(self.settings.get("last_update_check_epoch", 0))):
            threading.Thread(
                target=self._update_check_worker, args=(False,), daemon=True).start()
        try:
            while True:
                threading.Event().wait(3600)
        except KeyboardInterrupt:
            pass

    def _single_instance(self, kernel32=None):
        """Own the app mutex or signal the running process to show its UI."""
        from ctypes import wintypes
        if kernel32 is None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateEventW.restype = wintypes.HANDLE
        kernel32.CreateEventW.argtypes = [
            wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.SetEvent.restype = wintypes.BOOL
        kernel32.SetEvent.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        # Create the event before claiming the mutex. Concurrent launches then
        # share an event handle before one becomes the winner, so the losing
        # process cannot signal during a narrow event-creation race.
        activation_event = kernel32.CreateEventW(
            None, False, False, SINGLE_INSTANCE_ACTIVATE_EVENT)
        if not activation_event:
            return False
        # CreateEvent may have reported that the shared activation event
        # already exists. Do not mistake that stale last-error value for the
        # result of the distinct ownership mutex call below.
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX)
        if not handle:
            kernel32.CloseHandle(activation_event)
            return False
        already_exists = ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS
        if already_exists:
            kernel32.SetEvent(activation_event)
            kernel32.CloseHandle(handle)
            kernel32.CloseHandle(activation_event)
            return False
        self._mutex_handle = handle
        self._activation_event_handle = activation_event
        return True

    def _watch_activation_requests(self, kernel32=None):
        """Wait for later Start Menu launches without polling."""
        from ctypes import wintypes
        if kernel32 is None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        while True:
            result = kernel32.WaitForSingleObject(
                self._activation_event_handle, 0xFFFFFFFF)  # INFINITE
            if result != 0:  # WAIT_OBJECT_0
                self._log("activation watcher stopped; Win32 result=%s" % result)
                return
            self._activate_from_launch()

    def _activate_from_launch(self):
        """Reveal the most relevant control surface after a repeated launch."""
        # Preserve the user's current task when a window already exists,
        # including a minimized or covered update/setup/settings window.
        for window in (
                self.update_window, self.setup_window,
                self.settings_window, self.scratchpad):
            if window is not None:
                ui.present_window(window)
                self._log("repeat launch restored an existing window")
                return
        if self.settings.get("setup_complete", False):
            self.open_settings()
            self._log("repeat launch opened settings")
        else:
            self.open_setup()
            self._log("repeat launch opened setup")

    def exit_app(self, icon=None, item=None):
        self._restore_playback_after_recording()
        self.indicator.close()
        if self.listener is not None:
            self.listener.stop()
        if self.update_window is not None:
            # os._exit() skips normal thread finalization, so explicitly
            # hand updater cleanup off before terminating daemon threads.
            self.update_window.cancel_and_cleanup()
        for win in (self.scratchpad, self.settings_window,
                    self.setup_window, self.update_window):
            if win is not None and win.root is not None:
                try:
                    win.root.after(0, win.root.destroy)
                except Exception:
                    pass
        if self.icon is not None:
            self.icon.stop()
        os._exit(0)

    # ---------------- hotkey ----------------

    def _is_hotkey(self, key):
        return key in KEY_MAP.get(self.settings["hotkey"], set())

    def _dispatch_and_suppress_win32_event(self, callback=None, key=None):
        """Run an internal key callback and always reserve the system event."""
        try:
            if callback is not None:
                callback(key)
        except Exception as exc:
            # Never leak a configured key into an arbitrary focused app merely
            # because starting or stopping dictation hit an unexpected error.
            # Exception type is enough for diagnostics and cannot contain a
            # transcript or device name.
            self._log("reserved hotkey action failed: %s" % type(exc).__name__)
        finally:
            # pynput implements selective system-wide suppression by raising
            # an internal exception from this call. Keep it in the finally so
            # callback failures cannot fall through to CallNextHookEx.
            self.listener.suppress_event()
        return False

    def _win32_event_filter(self, message, data):
        """Dispatch and consume physical PTT or recording-cancel transactions.

        pynput's selective Windows suppression prevents the corresponding
        on_press/on_release callback from being queued. Dispatch before calling
        suppress_event so Presspeech still observes the key while Windows and
        the focused app do not.
        """
        try:
            flags = int(getattr(data, "flags", 0))
        except (TypeError, ValueError):
            flags = 0
        # Ctrl+V is generated by pynput and must reach the target even when the
        # user's configured PTT key is Ctrl. It is already guarded separately
        # by _injecting_keys in the ordinary callbacks.
        if flags & LLKHF_INJECTED_MASK:
            return

        vk_code = getattr(data, "vkCode", None)
        is_press = message in (WM_KEYDOWN, WM_SYSKEYDOWN)
        is_release = message in (WM_KEYUP, WM_SYSKEYUP)
        if not (is_press or is_release):
            return

        if is_press:
            self._filter_pressed_vks.add(vk_code)
        else:
            self._filter_pressed_vks.discard(vk_code)

        # Once an apparent configured-key press has been identified as part of
        # a pass-through layout chord, keep repeats and its matching release
        # together even if Left Ctrl is released or Settings changes first.
        if vk_code in self._passthrough_hotkey_vks:
            if is_release:
                self._passthrough_hotkey_vks.discard(vk_code)
            return

        if vk_code == VK_ESCAPE:
            if is_press:
                if not self.recording and not self._suppress_escape_keyup:
                    return
                # Autorepeat remains consumed but must not request repeated
                # cancellation after the first press has cleared recording.
                if not self._suppress_escape_keyup:
                    self._suppress_escape_keyup = True
                    return self._dispatch_and_suppress_win32_event(
                        self._on_press, pkb.Key.esc)
            else:
                if not self._suppress_escape_keyup:
                    return
                self._suppress_escape_keyup = False
                return self._dispatch_and_suppress_win32_event(
                    self._on_release, pkb.Key.esc)
            return self._dispatch_and_suppress_win32_event()

        if is_release and vk_code in self._suppressed_hotkey_vks:
            key = self._suppressed_hotkey_vks.pop(vk_code)
            return self._dispatch_and_suppress_win32_event(
                self._on_release, key)

        configured = HOTKEY_VIRTUAL_KEYS.get(self.settings.get("hotkey"))
        if not is_press or configured is None or vk_code != configured[0]:
            return

        # Windows implements AltGr as synthetic Left Ctrl + Right Alt. Leave
        # that complete transaction available for layout characters; the
        # ordinary callback applies the same guard before starting dictation.
        if (self.settings.get("hotkey") == "right alt" and
                (0xA2 in self._filter_pressed_vks or
                 pkb.Key.ctrl_l in self._pressed_keys)):
            self._passthrough_hotkey_vks.add(vk_code)
            return

        if vk_code not in self._suppressed_hotkey_vks:
            key = configured[1]
            self._suppressed_hotkey_vks[vk_code] = key
            return self._dispatch_and_suppress_win32_event(
                self._on_press, key)
        # Consume key-down autorepeats as part of the same reserved PTT
        # transaction. The matched key-up remains reserved even if Settings is
        # changed while the key is physically held.
        return self._dispatch_and_suppress_win32_event()

    def _on_press(self, key):
        if self._injecting_keys:
            return
        self._pressed_keys.add(key)
        # Escape is a conventional, discoverable abort action. The Windows
        # event filter consumes its complete transaction only while recording,
        # so the foreground application is not also dismissed or changed.
        if key == pkb.Key.esc:
            self.cancel_recording()
            return
        hotkey_keys = KEY_MAP.get(self.settings["hotkey"], set())
        if key not in hotkey_keys:
            return
        # Windows implements AltGr as synthetic Left Ctrl + physical Right
        # Alt. Treat only bare Right Alt as the configured one-key hotkey, so
        # entering alternate-layout characters cannot begin dictation.
        if (self.settings["hotkey"] == "right alt" and
                pkb.Key.ctrl_l in self._pressed_keys):
            self._log("right alt ignored while left ctrl is held (AltGr chord)")
            return
        if self._key_held:
            return
        self._key_held = True
        # Settings apply immediately, but the release must complete the same
        # key-down transaction even if its hotkey or trigger mode is changed
        # while the key is held. Keep every alias because pynput can report
        # Right Alt as alt_gr on press and alt_r on release (or vice versa).
        self._held_hotkey_keys = frozenset(hotkey_keys)
        self._held_hotkey_trigger = self.settings["trigger"]
        self._log("key down: %s" % (key,))
        if self._held_hotkey_trigger == "toggle":
            if self.recording:
                self.request_stop()
            else:
                self.start_recording()
        else:
            self.start_recording()

    def _on_release(self, key):
        # Releases must always repair physical-key state. A user can release
        # Left Ctrl (or the configured hotkey itself) during the short Ctrl+V
        # injection window; dropping that callback would leave Ctrl marked as
        # held or the hotkey transaction stuck until another matching release.
        # Injected releases are harmless here: they either discard an absent
        # key or finish a real transaction that began before injection.
        self._pressed_keys.discard(key)
        if key in KEY_MAP["right alt"]:
            # pynput may use a different alias for the same physical key on
            # release, so clear both tracked Right Alt representations.
            self._pressed_keys.difference_update(KEY_MAP["right alt"])
        if not self._key_held or key not in self._held_hotkey_keys:
            return
        self._key_held = False
        trigger = self._held_hotkey_trigger
        self._held_hotkey_keys = frozenset()
        self._held_hotkey_trigger = None
        self._log("key up: %s" % (key,))
        if trigger != "toggle":
            self.request_stop()

    def toggle_dictate(self, icon=None, item=None):
        if self.recording:
            self.request_stop()
        else:
            self.start_recording()

    # ---------------- recording ----------------

    def _dictation_model_ready(self):
        """Gate capture until the configured model is loaded and fully warmed."""
        model_name = self.settings["model"]
        status = getattr(self, "model_status", "pending")
        loaded = self.transcriber.loaded(model_name)
        if status == "ready" and loaded:
            return True

        # Startup already owns the model executor while pending/loading. A
        # failed, explicitly unloaded, or newly selected model needs one fresh
        # load attempt; changing the state before submitting prevents repeats.
        needs_load = status in ("error", "unloaded") or (
            status == "ready" and not loaded)
        if needs_load:
            self.retry_model()

        self._set_indicator("loading")
        self._log("dictation ignored; speech model is not ready (status=%s)" % status)
        return False

    def retry_model(self):
        """Queue at most one model retry and report whether one was started."""
        with self._model_retry_lock:
            model_name = self.settings["model"]
            status = getattr(self, "model_status", "pending")
            if status in ("pending", "loading"):
                return False
            if status == "ready" and self.transcriber.loaded(model_name):
                return False
            # Publish loading before queueing work. Repeated UI or hotkey
            # requests then observe the in-flight state and cannot enqueue
            # duplicate loads.
            self.model_status = "loading"
            self.model_status_detail = "Loading %s" % model_name
            self._queue_model_load_locked(model_name)
            return True

    def _queue_model_load_locked(self, requested_model):
        """Submit one versioned model request while holding the state lock."""
        self._model_load_generation = (
            getattr(self, "_model_load_generation", 0) + 1)
        generation = self._model_load_generation
        self._model_load_target = requested_model or self.settings["model"]
        self._model_executor.submit(
            self._preload_model_worker, requested_model, generation)
        return generation

    def prepare_configured_model(self):
        """Begin applying a newly selected model without waiting for a hotkey."""
        model_name = self.settings["model"]
        with self._model_retry_lock:
            if (getattr(self, "model_status", "pending") == "ready" and
                    self.transcriber.loaded(model_name)):
                return False
            if (getattr(self, "_model_load_target", None) == model_name and
                    getattr(self, "model_status", "pending") in
                    ("pending", "loading")):
                return False
            # Publish the unavailable state before submitting. The Settings
            # window and a hotkey press then both report the same truthful
            # lifecycle even if another model operation is ahead in the
            # single-thread executor.
            self.model_status = "loading"
            self.model_status_detail = "Loading %s" % model_name
            self._queue_model_load_locked(model_name)
            return True

    def start_recording(self):
        # Keep transcription and paste delivery exclusive with capture. A
        # previous worker injects Ctrl+V and briefly suppresses hook callbacks;
        # overlapping that with a new recording could swallow its hotkey
        # release and leave the microphone open until the safety timer fires.
        if getattr(self, "_canceling_recording", False):
            self._log("dictation ignored; canceled recording is still closing")
            return False
        if getattr(self, "transcribing", False):
            self._set_indicator("transcribing")
            self._log("dictation ignored; previous transcription is still being delivered")
            return False
        if not self._dictation_model_ready():
            return False
        paste_target = _foreground_paste_target()
        with self.lock:
            # Recheck after foreground-process discovery so simultaneous tray
            # and hotkey starts cannot cross the busy boundary. Cancellation
            # can also begin after the unlocked fast-path check above.
            if (self.recording or getattr(self, "_canceling_recording", False)
                    or getattr(self, "transcribing", False)):
                return False
            self._rec_epoch += 1
            epoch = self._rec_epoch
            self.recording = True
            self.buffer = []
            self._peak_rms = 0.0
            self._model_idle_epoch += 1
            self._recording_paste_target = paste_target
            # Delivery belongs to this recording. Model work is serialized and
            # can finish after a scratchpad is opened, replaced, or closed; a
            # mutable app-wide target could redirect an earlier transcript.
            scratchpad = getattr(self, "scratchpad", None)
            if (paste_target.process_identifier == os.getpid() and
                    paste_target.window_handle ==
                    getattr(scratchpad, "window_handle", 0)):
                self._recording_scratchpad = scratchpad
            else:
                self._recording_scratchpad = None
        self._log("recording started")
        self._set_indicator("listening")
        self._wake_model_if_idle()
        self._schedule_recording_limit(epoch)
        threading.Thread(
            target=self._start_audio_worker, args=(epoch,), daemon=True).start()
        return True

    def _recording_epoch_active(self, epoch):
        with self.lock:
            return self.recording and epoch == self._rec_epoch

    def _start_audio_worker(self, epoch):
        # Finish the audible cue before muting, and mute before opening the mic,
        # so neither the cue nor existing speaker audio is captured.
        # Device discovery can outlive a quick release and re-press, so every
        # asynchronous stage remains owned by the recording that started it.
        if self.settings.get("audio_cues", True):
            self._play_cue_worker("start")
        if not self._recording_epoch_active(epoch):
            return
        self._mute_playback_for_recording(epoch)
        if self._recording_epoch_active(epoch):
            self._open_mic_worker(epoch)

    def _mute_playback_for_recording(self, epoch):
        if not self.settings.get("mute_playback_while_recording", True):
            return
        with self._playback_mute_lock:
            with self.lock:
                if not self.recording or epoch != self._rec_epoch:
                    return
            if self._playback_restore is not None:
                return
            try:
                saved, failures = _mute_active_playback()
                self._playback_restore = saved
                self._log("playback muted for recording: %d endpoint(s)" % len(saved))
                if failures:
                    self._log("could not mute some playback endpoints: %s" %
                              "; ".join(failures))
            except Exception as exc:
                self._log("could not mute playback: %s" % exc)

    def _restore_playback_after_recording(self):
        with self._playback_mute_lock:
            saved = self._playback_restore
            self._playback_restore = None
            if saved is None:
                return
            try:
                restored, failures = _restore_playback_mutes(saved)
                self._log("playback mute state restored: %d endpoint(s)" % restored)
                if failures:
                    self._log("could not restore some playback endpoints: %s" %
                              "; ".join(failures))
            except Exception as exc:
                self._log("could not restore playback mute state: %s" % exc)

    def _open_mic_worker(self, epoch):
        stream = None
        try:
            if not self._recording_epoch_active(epoch):
                return
            chosen = self._get_input_device()
            if not self._recording_epoch_active(epoch):
                return
            if chosen is None:
                with self.lock:
                    if not self.recording or epoch != self._rec_epoch:
                        return
                    self.recording = False
                self._cancel_recording_limit(epoch)
                self._restore_playback_after_recording()
                self._set_indicator(None)
                self._log("no working microphone found")
                self.notify("No microphone found",
                            "Check Settings > System > Sound > Input, then enable "
                            "microphone access for desktop apps in Windows privacy "
                            "settings and try again.")
                return
            self.input_device = chosen
            idx, rate = chosen
            stream = sd.InputStream(
                device=idx, samplerate=rate, channels=1, dtype="float32",
                callback=lambda indata, frames, time_info, status: self._audio_cb(
                    indata, frames, time_info, status, epoch),
            )
            stream.start()
            with self.lock:
                if not self.recording or epoch != self._rec_epoch:
                    accepted = False
                else:
                    self.stream = stream
                    accepted = True
            if not accepted:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
                return
        except Exception as exc:
            with self.lock:
                if not self.recording or epoch != self._rec_epoch:
                    stale = True
                else:
                    stale = False
                    self.input_device = None
                    self.recording = False
                    self.stream = None
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
            if stale:
                return
            self._cancel_recording_limit(epoch)
            self._restore_playback_after_recording()
            self._set_indicator(None)
            self._log("mic error: %s" % exc)
            self.notify(
                "Microphone error",
                "Presspeech couldn't open the selected input. Check Settings > "
                "System > Sound > Input and Windows microphone privacy settings, "
                "including 'Let desktop apps access your microphone', then try "
                "again. Details: %s" % str(exc))
            return
        self._log("mic open ok: %s" % (self.input_device,))
        if self.icon is not None:
            self.icon.icon = self.rec_icon

    def _audio_cb(self, indata, frames, time_info, status, epoch):
        chunk = indata.copy()
        chunk_rms = float(np.sqrt(np.mean(np.square(chunk)))) if chunk.size else 0.0
        with self.lock:
            if self.recording and epoch == self._rec_epoch:
                self.buffer.append(chunk)
                self._peak_rms = max(self._peak_rms, chunk_rms)

    def request_stop(self):
        """Stop after silence, retaining the full safety window for ongoing speech."""
        with self.lock:
            if not self.recording:
                return
            epoch = self._rec_epoch
        self._schedule_post_roll(
            POST_ROLL_MIN_SEC, epoch, time.perf_counter())

    def cancel_recording(self, icon=None, item=None):
        """Discard the active capture without transcribing or changing clipboard."""
        with self.lock:
            if not self.recording:
                return False
            self.recording = False
            self._canceling_recording = True
            stream = self.stream
            self.stream = None
            self.buffer = []
            self._peak_rms = 0.0
            self._recording_paste_target = PasteTarget("", 0)
            self._recording_scratchpad = None
            recording_limit_timer = getattr(
                self, "_recording_limit_timer", None)
            self._recording_limit_timer = None
        threading.Thread(
            target=self._cancel_recording_worker,
            args=(stream, recording_limit_timer),
            name="presspeech-cancel-recording",
            daemon=True,
        ).start()
        return True

    def _cancel_recording_worker(self, stream, recording_limit_timer):
        """Close a claimed cancellation without blocking the keyboard hook."""
        try:
            if recording_limit_timer is not None:
                recording_limit_timer.cancel()
            if stream is not None:
                try:
                    stream.stop()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass
            if self.icon is not None:
                self.icon.icon = self.idle_icon
            # Closing capture before restoring playback keeps returning speaker
            # audio and the cue out of the discarded microphone data.
            self._restore_playback_after_recording()
            self._play_cue("stop")
            self._set_indicator(None)
            self._log("recording canceled; audio discarded")
            self._schedule_model_idle_unload()
        finally:
            with self.lock:
                self._canceling_recording = False

    def _schedule_recording_limit(self, epoch):
        """Bound capture even if Windows never delivers the hotkey release."""
        maximum_seconds = cfg.recording_length_seconds(
            self.settings.get("max_recording_seconds"))
        timer = threading.Timer(
            maximum_seconds, self._recording_limit_reached, (epoch,))
        timer.daemon = True
        with self.lock:
            if not self.recording or epoch != self._rec_epoch:
                return
            previous = getattr(self, "_recording_limit_timer", None)
            self._recording_limit_timer = timer
        if previous is not None:
            previous.cancel()
        timer.start()

    def _recording_limit_reached(self, epoch):
        if self.stop_recording(expected_epoch=epoch):
            self._log("maximum recording duration reached; capture stopped")

    def _cancel_recording_limit(self, epoch):
        with self.lock:
            if epoch != self._rec_epoch:
                return
            timer = getattr(self, "_recording_limit_timer", None)
            self._recording_limit_timer = None
        if timer is not None:
            timer.cancel()

    def _schedule_post_roll(self, delay, epoch, released_at):
        timer = threading.Timer(delay, self._finish_after_roll, (epoch, released_at))
        timer.daemon = True
        timer.start()

    def _post_roll_tail(self):
        with self.lock:
            rate = self.input_device[1] if self.input_device is not None else 16000
            needed = max(1, int(rate * POST_ROLL_TAIL_SEC))
            remaining = needed
            parts = []
            for chunk in reversed(self.buffer):
                flat = chunk.reshape(-1)
                take = min(remaining, flat.size)
                if take:
                    parts.append(flat[-take:])
                    remaining -= take
                if remaining == 0:
                    break
            peak_rms = self._peak_rms
        if not parts:
            tail_rms = 0.0
        else:
            tail = np.concatenate(list(reversed(parts)))
            tail_rms = float(np.sqrt(np.mean(np.square(tail))))
        threshold = min(
            POST_ROLL_MAX_SILENCE_RMS,
            max(POST_ROLL_ABS_SILENCE_RMS,
                peak_rms * POST_ROLL_RELATIVE_SILENCE),
        )
        return tail_rms, threshold

    def _finish_after_roll(self, epoch, released_at):
        if epoch != self._rec_epoch:
            return
        elapsed = time.perf_counter() - released_at
        tail_rms, threshold = self._post_roll_tail()
        silent = tail_rms <= threshold
        if silent or elapsed >= POST_ROLL_MAX_SEC:
            reason = "silence" if silent else "maximum"
            self._log("post-roll %.3fs (%s; rms %.4f, threshold %.4f)" %
                      (elapsed, reason, tail_rms, threshold))
            self.stop_recording(expected_epoch=epoch)
            return
        remaining = POST_ROLL_MAX_SEC - elapsed
        self._schedule_post_roll(
            min(POST_ROLL_CHECK_SEC, max(0.0, remaining)), epoch, released_at)

    def stop_recording(self, expected_epoch=None):
        with self.lock:
            if (not self.recording or
                    (expected_epoch is not None and
                     expected_epoch != self._rec_epoch)):
                return False
            self.recording = False
            audio = np.concatenate(self.buffer) if self.buffer else np.zeros(0, dtype=np.float32)
            # Claim the delivery lifecycle before releasing the recording lock.
            # This closes the small window in which another hotkey press could
            # start capture while this method prepares and queues model work.
            self.transcribing = audio.size > 0
            paste_target = self._recording_paste_target
            scratchpad_target = getattr(self, "_recording_scratchpad", None)
            self._recording_scratchpad = None
            stream = self.stream
            self.stream = None
            self.buffer = []
            recording_limit_timer = getattr(
                self, "_recording_limit_timer", None)
            self._recording_limit_timer = None
        if recording_limit_timer is not None:
            recording_limit_timer.cancel()
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        if self.icon is not None:
            self.icon.icon = self.idle_icon
        # Restore playback only after closing the stream, so returning speaker
        # audio and the stop cue are never captured in the post-roll.
        self._restore_playback_after_recording()
        self._play_cue("stop")
        if audio.size == 0:
            self._show_no_speech_feedback()
            self._log("recording stopped; no audio captured")
            self._schedule_model_idle_unload()
            return True
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if self.input_device is not None and self.input_device[1] != 16000:
            audio = _resample_to_16k(audio, self.input_device[1])
        if audio.size / 16000.0 < 0.25:
            self._finish_transcribing(NO_SPEECH_OUTCOME)
            self._log("recording stopped; too short (%.2fs)" % (audio.size / 16000.0))
            self._schedule_model_idle_unload()
            return True
        self._capture_benchmark_if_armed(audio)
        self._set_indicator("transcribing")
        self._log("recording stopped; %.2fs captured, transcribing" % (audio.size / 16000.0))
        self._model_executor.submit(
            self._transcribe_worker, audio, paste_target, scratchpad_target)
        return True

    def _capture_benchmark_if_armed(self, audio):
        """Persist only explicitly armed recordings for local model comparison."""
        remaining = int(self.settings.get("capture_benchmark_remaining", 0) or 0)
        one_shot = bool(self.settings.get("capture_next_benchmark", False))
        if remaining <= 0 and not one_shot:
            return None
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "benchmarks", "audio")
        session = self.settings.get("capture_benchmark_session", "").strip()
        index = int(self.settings.get("capture_benchmark_index", 1) or 1)
        safe_session = re.sub(r"[^A-Za-z0-9_-]+", "-", session).strip("-")
        if remaining > 0 and safe_session:
            filename = "%s-%02d.wav" % (safe_session, index)
        else:
            filename = "live-%s.wav" % time.strftime("%Y%m%d-%H%M%S")
        output_path = os.path.join(output_dir, filename)
        try:
            import wave
            os.makedirs(output_dir, exist_ok=True)
            pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
            with wave.open(output_path, "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(16000)
                handle.writeframes(pcm.tobytes())
            self.settings["capture_next_benchmark"] = False
            if remaining > 0:
                self.settings["capture_benchmark_remaining"] = remaining - 1
                self.settings["capture_benchmark_index"] = index + 1
            cfg.save(self.settings)
            self._log("saved one-shot benchmark audio: %s" % output_path)
            left = max(0, remaining - 1) if remaining > 0 else 0
            self.notify("Benchmark clip saved", "%s (%d remaining)" %
                        (os.path.basename(output_path), left))
            return output_path
        except Exception as exc:
            self._log("benchmark capture failed: %s" % exc)
            self.notify("Benchmark capture failed", str(exc))
            return None

    # ---------------- input device selection ----------------

    @staticmethod
    def _device_selector(device, host_name):
        """Return a stable selector that does not depend on PortAudio device indexes."""
        return "%s::%s" % (host_name, device["name"])

    @staticmethod
    def _safe_input_device(device, host_name):
        if device["max_input_channels"] < 1:
            return False
        name = device["name"].lower()
        host_name = host_name.lower()
        if any(word in name for word in INPUT_DEVICE_SKIP_WORDS):
            return False
        return not any(word in host_name for word in UNSAFE_INPUT_HOST_APIS)

    def input_device_options(self):
        """Return (label, selector) pairs for Settings, excluding unsafe devices."""
        options = [("Automatic (recommended)", AUTO_INPUT_DEVICE)]
        try:
            devices = sd.query_devices()
            host_apis = sd.query_hostapis()
        except Exception as exc:
            self._log("could not list input devices: %s" % exc)
            return options
        for i, device in enumerate(devices):
            host_name = host_apis[device["hostapi"]]["name"]
            if not self._safe_input_device(device, host_name):
                continue
            label = "%s — %s (device %d)" % (device["name"], host_name, i)
            options.append((label, self._device_selector(device, host_name)))
        return options

    def _find_input_device(self, selected, probe=None):
        """Probe and return a usable input matching a stable selector."""
        if probe is None:
            probe = self._probe_input
        devices = sd.query_devices()
        host_apis = sd.query_hostapis()
        host_pref = {"mme": 0, "windows directsound": 1, "windows wasapi": 2}
        ranked = []
        for i, d in enumerate(devices):
            host_name = host_apis[d["hostapi"]]["name"]
            if not self._safe_input_device(d, host_name):
                continue
            name = d["name"].lower()
            selector = self._device_selector(d, host_name)
            score = host_pref.get(host_name.lower(), 9) * 10
            if name == "microsoft sound mapper - input":
                score -= 100
            elif "yeti nano" in name:
                score -= 10
            if "microphone" in name or "mic" in name:
                score -= 1
            else:
                score += 1
            selected_first = 0 if selected != AUTO_INPUT_DEVICE and selector == selected else 1
            ranked.append((selected_first, score, i, d, host_name, selector))
        ranked.sort(key=lambda t: (t[0], t[1], t[2]))
        if selected != AUTO_INPUT_DEVICE:
            # An explicit device choice is strict. If it disappears or cannot be
            # opened, fail safely instead of silently recording from another mic.
            ranked = [item for item in ranked if item[5] == selected]
            if not ranked:
                self._log("configured input is unavailable: %s" % selected)
                return None
        for _selected_first, _score, i, d, _host_name, selector in ranked:
            for rate in (16000, 48000, 44100):
                try:
                    sd.check_input_settings(device=i, samplerate=rate, channels=1,
                                            dtype="float32")
                except Exception:
                    continue
                if probe(i, rate):
                    chosen_for = "configured" if selector == selected else "automatic"
                    self._log("using %s input: %s at %d Hz" %
                              (chosen_for, d["name"], rate))
                    return (i, rate)
        return None

    def _get_input_device(self):
        if self.input_device is not None:
            return self.input_device
        selected = self.settings.get("input_device", AUTO_INPUT_DEVICE)
        self.input_device = self._find_input_device(selected)
        return self.input_device

    def check_input_device(self, selected):
        """Open an input and distinguish audible samples from silent buffers."""
        levels = []

        def probe(idx, rate):
            level = self._probe_input_level(
                idx, rate, listen_for=MICROPHONE_CHECK_LISTEN_SEC)
            if level is None:
                return False
            levels.append(level)
            return True

        try:
            chosen = self._find_input_device(selected, probe=probe)
            if chosen is None:
                return MICROPHONE_CHECK_UNAVAILABLE
            if levels and max(levels) >= MICROPHONE_CHECK_AUDIO_RMS:
                return MICROPHONE_CHECK_LEVEL
            return MICROPHONE_CHECK_SILENT
        except Exception as exc:
            self._log("microphone readiness check failed: %s" % exc)
            return MICROPHONE_CHECK_UNAVAILABLE

    def _open_windows_settings(self, uri, manual_recovery=None):
        """Open one of Presspeech's fixed Windows Settings destinations."""
        try:
            os.startfile(uri)
            return True
        except (AttributeError, OSError) as exc:
            self._log("could not open Windows Settings: %s" % exc)
            self.notify(
                "Could not open Windows Settings",
                manual_recovery or
                "Open Settings manually and search for Microphone privacy or "
                "Sound input settings.")
            return False

    def open_microphone_privacy_settings(self):
        return self._open_windows_settings(MICROPHONE_PRIVACY_SETTINGS_URI)

    def open_default_input_settings(self):
        return self._open_windows_settings(DEFAULT_INPUT_SETTINGS_URI)

    def open_startup_settings(self):
        return self._open_windows_settings(
            STARTUP_SETTINGS_URI,
            "Open Settings manually, then choose Apps and Startup.")

    @staticmethod
    def _probe_input(idx, rate):
        return PresspeechApp._probe_input_level(idx, rate) is not None

    @staticmethod
    def _probe_input_level(idx, rate, listen_for=0.0):
        """Return peak RMS from a short in-memory probe, or None if it cannot open."""
        got = threading.Event()
        heard = threading.Event()
        peak_rms = [0.0]
        stream = None

        def cb(indata, frames, t, status):
            chunk = np.asarray(indata)
            level = float(np.sqrt(np.mean(np.square(chunk)))) if chunk.size else 0.0
            peak_rms[0] = max(peak_rms[0], level)
            got.set()
            if level >= MICROPHONE_CHECK_AUDIO_RMS:
                heard.set()

        try:
            stream = sd.InputStream(
                device=idx, samplerate=rate, channels=1, dtype="float32",
                callback=cb)
            stream.start()
            if not got.wait(0.8):
                return None
            # Return early once a meaningful input level arrives. Otherwise
            # retain the stream briefly so a person has time to speak during
            # the explicit first-run check.
            heard.wait(max(0.0, listen_for))
            return peak_rms[0]
        except Exception:
            return None
        finally:
            if stream is not None:
                try:
                    stream.stop()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass

    # ---------------- transcription ----------------

    def _transcribe_worker(
            self, audio, paste_target=PasteTarget("", 0),
            scratchpad_target=None):
        outcome = None
        try:
            outcome = self._transcribe_worker_inner(
                audio, paste_target, scratchpad_target)
            return outcome
        finally:
            # Empty/error results still exercised the model. Refresh the idle
            # deadline here so an already-queued gaming-mode unload cannot
            # evict it immediately after that work completes.
            self._last_model_use = time.perf_counter()
            self._schedule_model_idle_unload()
            self._finish_transcribing(outcome)

    def _finish_transcribing(self, outcome=None):
        """Clear delivery state without hiding a subsequent recording HUD."""
        # Queue the final visual state while holding the lifecycle lock. A
        # waiting start can only show Listening after this command, and the
        # indicator's generation guard prevents a delayed result hide from
        # erasing that newer recording state.
        with self.lock:
            if outcome == NO_SPEECH_OUTCOME:
                self._set_temporary_indicator(
                    "no_speech", NO_SPEECH_FEEDBACK_SEC)
            else:
                self._set_indicator(None)
            self.transcribing = False
        if outcome == NO_SPEECH_OUTCOME:
            self._notify_no_speech()

    def _transcribe_worker_inner(
            self, audio, paste_target=PasteTarget("", 0),
            scratchpad_target=None):
        model_started = time.perf_counter()
        try:
            if not self.transcriber.loaded(self.settings["model"]):
                self.transcriber.load(self.settings["model"], notify=self.notify)
            text = self.transcriber.transcribe(audio, language="en")
            model_seconds = time.perf_counter() - model_started
        except Exception as exc:
            self._log(traceback.format_exc())
            if not engine.is_parakeet(self.settings["model"]):
                self.notify("Transcription failed", str(exc))
                return
            try:
                self.notify("Parakeet failed", "Falling back to Whisper base.en (%s)"
                            % str(exc)[:100])
                self.transcriber.load("base.en", notify=self.notify)
                model_started = time.perf_counter()
                text = self.transcriber.transcribe(audio, language="en")
                model_seconds = time.perf_counter() - model_started
            except Exception as exc2:
                self._log(traceback.format_exc())
                self.notify("Transcription failed", str(exc2))
                return
        timing = getattr(self.transcriber, "last_timing", {})
        timing_summary = _model_timing_summary(timing)
        if not text:
            self._log("transcription returned empty (model %.3fs)" % model_seconds)
            if timing_summary is not None:
                self._log(timing_summary)
            return NO_SPEECH_OUTCOME
        text = self._apply_text(text)
        # Dictation is private: retain performance data without persisting the
        # user's words in the diagnostic log. Whisper's detected-speech duration
        # distinguishes a VAD rejection from an empty decoder result.
        self._log("transcription complete: %d chars (model %.3fs)" %
                  (len(text), model_seconds))
        if timing_summary is not None:
            self._log(timing_summary)
        self._deliver_text(text, paste_target, scratchpad_target)

    def _deliver_text(
            self, text, paste_target=PasteTarget("", 0),
            scratchpad_target=None):
        """Deliver only to the destination captured when recording began."""
        if scratchpad_target is None:
            self._paste(text, paste_target)
            return
        if (scratchpad_target is getattr(self, "scratchpad", None) and
                getattr(scratchpad_target, "root", None) is not None):
            if _paste_target_matches(
                    paste_target, _foreground_paste_target()):
                scratchpad_target.append_text(text)
            else:
                # Preserve the same focus-change behavior as normal app
                # delivery. _paste copies the transcript, rechecks the target,
                # and refuses to inject Ctrl+V into the newly focused window.
                self._paste(text, paste_target)
            return
        # Try Dictation is a private sink. If its window disappeared while the
        # model was working, dropping the result is safer than pasting it into
        # whichever unrelated application has focus now.
        self._log("scratchpad transcription discarded; window closed")

    def _apply_text(self, text):
        text = _apply_dictionary_rules(text, self.settings["dictionary"])
        if self.settings["remove_fillers"]:
            text = _remove_fillers(text)
        if self.settings.get("british"):
            text = to_british(text)
        text += cfg.SUFFIXES.get(self.settings["suffix"], " ")
        return text

    def _paste(self, text, paste_target=PasteTarget("", 0)):
        pyperclip.copy(text)
        if not isinstance(paste_target, PasteTarget):
            # Legacy callers cannot prove which window owned the cursor when
            # recording began, so normalize them into the same fail-closed
            # clipboard-only path as an unavailable Win32 snapshot.
            paste_target = PasteTarget(str(paste_target or ""), 0)
        if not paste_target.window_handle:
            self._log("paste skipped; no foreground window was captured")
            self.notify(
                "Transcript copied, not pasted",
                "Presspeech couldn't identify the window focused when "
                "recording began. Paste from the clipboard when ready.")
            return
        process_name = paste_target.process_name
        route = _paste_route(process_name)
        time.sleep(RDP_PASTE_DELAY_SEC if route == "rdp" else PASTE_DELAY_SEC)
        if not self._paste_target_still_focused(paste_target):
            return
        keyboard = pkb.Controller()
        modifiers = [pkb.Key.ctrl_l]
        if route == "moonlight":
            # Moonlight's client-side shortcut types clipboard text on the host.
            modifiers.extend((pkb.Key.alt_l, pkb.Key.shift_l))
        self._injecting_keys = True
        pressed = []
        try:
            for key in modifiers:
                keyboard.press(key)
                pressed.append(key)
            # pynput emits each key event separately. A mouse click can move
            # focus after Ctrl (or Moonlight's longer modifier chord) but
            # before V, so revalidate at the last point before the event that
            # actually exposes the clipboard contents.
            if not self._paste_target_still_focused(paste_target):
                return
            if route == "moonlight":
                self._log("paste route: Moonlight clipboard typing")
            elif route == "rdp":
                self._log("paste route: Remote Desktop clipboard")
            else:
                self._log("paste route: local (%s)" % (process_name or "unknown"))
            keyboard.press("v")
            keyboard.release("v")
        finally:
            for key in reversed(pressed):
                keyboard.release(key)
            # Let hook callbacks consume the injected releases before re-enabling PTT.
            time.sleep(0.02)
            self._injecting_keys = False

    def _paste_target_still_focused(self, paste_target):
        current_target = _foreground_paste_target()
        if _paste_target_matches(paste_target, current_target):
            return True
        self._log(
            "paste skipped; focus changed from %s to %s" % (
                paste_target.process_name or "unknown",
                current_target.process_name or "unknown",
            ))
        self.notify(
            "Transcript copied, not pasted",
            "The focused window changed while Presspeech was transcribing. "
            "Paste from the clipboard when ready.")
        return False

    # ---------------- windows ----------------

    def open_scratchpad(self, icon=None, item=None):
        if self.scratchpad is None:
            self.scratchpad = ui.ScratchpadWindow(self)
        else:
            ui.present_window(self.scratchpad)

    def open_settings(self, icon=None, item=None):
        if self.settings_window is None:
            self.settings_window = ui.SettingsWindow(self)
        else:
            ui.present_window(self.settings_window)

    def open_setup(self, icon=None, item=None):
        if self.setup_window is None:
            self.setup_window = ui.SetupWindow(self)
        else:
            ui.present_window(self.setup_window)

    def check_for_updates(self, icon=None, item=None):
        if self.update_window is not None:
            ui.present_window(self.update_window)
            return
        if not self._update_lock.acquire(blocking=False):
            self.notify("Presspeech", "An update check is already running.")
            return
        threading.Thread(
            target=self._update_check_worker, args=(True, True), daemon=True).start()

    def _update_check_worker(self, manual=False, lock_held=False):
        if not lock_held and not self._update_lock.acquire(blocking=False):
            return
        try:
            update = updates.fetch_update(cfg.VERSION)
            self.settings["last_update_check_epoch"] = int(time.time())
            cfg.save(self.settings)
            if update is None:
                if manual:
                    self.notify("Presspeech", "Version %s is up to date." % cfg.VERSION)
                return
            self.pending_update = update
            if self.update_window is None:
                self.update_window = ui.UpdateWindow(self, update)
        except Exception as exc:
            self._log("update check failed: %s" % exc)
            if manual:
                self.notify("Update check failed", str(exc))
        finally:
            self._update_lock.release()

    def launch_update(self, installer_path, update):
        """Revalidate and run an installer after the second user approval."""
        with updates.locked_verified_installer(update, installer_path):
            subprocess.Popen([installer_path], cwd=os.path.dirname(installer_path))
        try:
            updates.schedule_installer_cleanup(installer_path)
        except Exception as exc:
            # The installer is already running. Cleanup failure must not turn a
            # successful, explicitly approved update into a second launch.
            self._log("could not schedule update installer cleanup: %s" % exc)
        time.sleep(0.15)
        self.exit_app()

    def diagnostics_text(self):
        """Return useful support facts without transcript or dictionary contents."""
        transcriber = self.transcriber
        model = getattr(transcriber, "model", None)
        dtype = str(getattr(model, "dtype", "not loaded"))
        device = str(getattr(transcriber, "_device", "not loaded"))
        active_input = self.input_device or "not opened yet"
        lines = [
            "Presspeech diagnostics",
            "Version: %s" % cfg.VERSION,
            "Build: %s" % ("packaged" if getattr(sys, "frozen", False) else "source"),
            "Windows: %s" % platform.platform(),
            "Configured model: %s" % self.settings.get("model", "unknown"),
            "Model status: %s" % getattr(self, "model_status", "unknown"),
            "Backend: %s" % (getattr(transcriber, "backend", None) or "not loaded"),
            "Device / dtype: %s / %s" % (device, dtype),
            "Configured microphone: %s" % self.settings.get("input_device", "auto"),
            "Active microphone: %s" % (active_input,),
            "Hotkey / trigger: %s / %s" % (
                self.settings.get("hotkey", "unknown"),
                self.settings.get("trigger", "unknown")),
            "Maximum recording length: %d seconds" %
            cfg.recording_length_seconds(
                self.settings.get("max_recording_seconds")),
            "Windows UI Automation: %s" % ui.accessibility_status(),
            "Automatic update checks: %s" % bool(
                self.settings.get("check_updates", True)),
            "Dictionary rule count: %d" % len(self.settings.get("dictionary", [])),
            r"Config path: %APPDATA%\Presspeech\config.json",
            r"Log path: %APPDATA%\Presspeech\log.txt",
            "Privacy: no transcript, audio, or dictionary contents included",
        ]
        return "\r\n".join(lines)

    def copy_diagnostics(self, icon=None, item=None):
        pyperclip.copy(self.diagnostics_text())
        self.notify("Presspeech", "Privacy-safe diagnostics copied to the clipboard.")

    def _open_feedback_page(self, url):
        """Open one fixed public feedback form without adding app or user data."""
        try:
            os.startfile(url)
        except (OSError, AttributeError) as exc:
            self._log("could not open feedback form: %s" % type(exc).__name__)
            self.notify(
                "Could not open GitHub",
                "Open github.com/rcourtman/presspeech/issues in your browser.")
            return False
        return True

    def report_problem(self, icon=None, item=None):
        return self._open_feedback_page(BUG_REPORT_URL)

    def suggest_improvement(self, icon=None, item=None):
        return self._open_feedback_page(FEATURE_REQUEST_URL)

    # ---------------- helpers ----------------

    def apply_autostart(self):
        """Apply the requested per-user startup registration, reporting failure."""
        enable = bool(self.settings["autostart"])
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                if enable:
                    winreg.SetValueEx(
                        key, "Presspeech", 0, winreg.REG_SZ,
                        _autostart_command(
                            sys.executable, __file__,
                            frozen=bool(getattr(sys, "frozen", False))))
                else:
                    try:
                        winreg.DeleteValue(key, "Presspeech")
                    except FileNotFoundError:
                        pass
        except Exception as exc:
            self._log("autostart error: %s" % exc)
            self.notify(
                "Start with Windows not updated",
                "Open Settings, then choose Apps and Startup to review "
                "Presspeech's startup state.")
            return False
        return True

    def notify(self, title, message):
        try:
            if self.icon is not None:
                self.icon.notify(message, title)
        except Exception:
            pass

    def _set_indicator(self, state):
        indicator = getattr(self, "indicator", None)
        if indicator is None:
            return
        try:
            if state is None:
                indicator.hide()
            elif self.settings.get("visual_indicator", True):
                indicator.show(state)
        except Exception:
            pass

    def _set_temporary_indicator(self, state, seconds):
        indicator = getattr(self, "indicator", None)
        if indicator is None:
            return
        try:
            if self.settings.get("visual_indicator", True):
                indicator.show_temporary(state, seconds)
        except Exception:
            pass

    def _notify_no_speech(self):
        self.notify(
            "No speech detected",
            "Try again and speak after the start cue. If this keeps happening, "
            "run the microphone check in Setup.")

    def _show_no_speech_feedback(self):
        self._set_temporary_indicator("no_speech", NO_SPEECH_FEEDBACK_SEC)
        self._notify_no_speech()

    def _play_cue(self, name):
        if not self.settings.get("audio_cues", True):
            return
        threading.Thread(
            target=self._play_cue_worker, args=(name,), daemon=True).start()

    @staticmethod
    def _play_cue_worker(name):
        try:
            winsound.PlaySound(
                CUE_SOUNDS[name], winsound.SND_MEMORY | winsound.SND_NODEFAULT)
        except (KeyError, RuntimeError):
            pass

    def _preload_model_worker(
            self, requested_model=None, request_generation=None):
        """Warm the configured model in the tray process before the first dictation."""
        model_state_lock = getattr(self, "_model_retry_lock", threading.Lock())
        configured_model = self.settings["model"]
        # Startup may make the documented one-time CPU default choice. A model
        # explicitly selected in Settings must be loaded exactly as selected.
        model_name = requested_model or _startup_model(
            self.settings, engine.cuda_available())
        with model_state_lock:
            if request_generation is None:
                self._model_load_generation = (
                    getattr(self, "_model_load_generation", 0) + 1)
                request_generation = self._model_load_generation
                self._model_load_target = model_name
            current_request = (
                request_generation == self._model_load_generation)
            cpu_first_run = (
                requested_model is None and model_name != configured_model and
                current_request and
                self.settings["model"] == configured_model)
            if cpu_first_run:
                self.settings["model"] = model_name
                self._model_load_target = model_name
        if cpu_first_run:
            cfg.save(self.settings)
            self._log(
                "NVIDIA CUDA unavailable on first run; selected CPU model: %s"
                % model_name)
            self.notify(
                "CPU speech model selected",
                "NVIDIA CUDA is unavailable; using Whisper base.en on CPU. "
                "You can choose another model in Settings.")
        with model_state_lock:
            if (request_generation == self._model_load_generation and
                    self.settings["model"] == model_name):
                self.model_status = "loading"
                self.model_status_detail = "Loading %s" % model_name
        self._set_indicator("loading")
        self._log("loading speech model: %s" % model_name)
        try:
            self.transcriber.load(model_name, notify=self.notify)
            self._log("warming speech model")
            self.transcriber.warmup(
                seconds=MODEL_WARMUP_SEC, all_buckets=True)
            # Warming long fixed shapes temporarily reserves CUDA workspace.
            # The cuDNN execution plans stay cached after releasing unused
            # allocator blocks, so first-pass speed is retained without
            # needlessly occupying gaming VRAM.
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
            self._last_model_use = time.perf_counter()
        except Exception as exc:
            with model_state_lock:
                if (request_generation == self._model_load_generation and
                        self.settings["model"] == model_name):
                    self.model_status = "error"
                    self.model_status_detail = str(exc)[:160]
                if (request_generation == self._model_load_generation and
                        getattr(self, "_model_load_target", None) == model_name):
                    self._model_load_target = None
            self._log("speech model load failed: %s\n%s" % (exc, traceback.format_exc()))
            if (request_generation == self._model_load_generation and
                    self.settings["model"] == model_name):
                self.notify(
                    "Model load failed",
                    "%s can be retried in Settings or Setup." % model_name)
                self._set_indicator(None)
            return
        model_dtype = getattr(self.transcriber.model, "dtype", "unknown")
        with model_state_lock:
            if (request_generation != self._model_load_generation or
                    self.settings["model"] != model_name):
                # A later Settings save has already queued its own load. Do
                # not briefly claim that dictation is ready with this stale
                # model while that request waits in the executor.
                self._log("loaded model superseded by newer selection: %s" % model_name)
                return
            self.model_status = "ready"
            if cpu_first_run:
                self.model_status_detail = (
                    "Whisper base.en on CPU (NVIDIA CUDA unavailable)")
            else:
                self.model_status_detail = "%s on %s (%s)" % (
                    model_name, getattr(self.transcriber, "_device", "unknown"),
                    model_dtype)
            if getattr(self, "_model_load_target", None) == model_name:
                self._model_load_target = None
        self._log("model ready: %s (%s)" % (model_name, model_dtype))
        self._schedule_model_idle_unload()
        self._set_indicator(None)

    def _wake_model_if_idle(self):
        loaded = self.transcriber.loaded(self.settings["model"])
        if (loaded and
                time.perf_counter() - self._last_model_use < MODEL_IDLE_WAKE_SEC):
            return
        with self._wake_lock:
            if self._wake_in_progress:
                return
            self._wake_in_progress = True
        self._model_executor.submit(self._wake_model_worker)

    def _wake_model_worker(self):
        started = time.perf_counter()
        try:
            reloaded = False
            if not self.transcriber.loaded(self.settings["model"]):
                self._log("reloading model on hotkey: %s" % self.settings["model"])
                self.transcriber.load(self.settings["model"], notify=self.notify)
                reloaded = True
            # A resident Parakeet needs only the smallest fixed shape to raise
            # GPU clocks after a long idle. It starts at key-down and normally
            # completes while the user is still speaking. A genuinely reloaded
            # model warms every supported duration bucket.
            self.transcriber.warmup(
                seconds=1.0, all_buckets=reloaded)
            self._last_model_use = time.perf_counter()
            self._schedule_model_idle_unload()
            self._log("idle model wake completed in %.3fs" %
                      (self._last_model_use - started))
        except Exception as exc:
            self._log("idle model wake failed: %s" % exc)
        finally:
            with self._wake_lock:
                self._wake_in_progress = False

    def _schedule_model_idle_unload(self):
        idle_seconds = int(self.settings.get("gpu_idle_unload_sec", 0) or 0)
        if idle_seconds <= 0:
            return
        self._model_idle_epoch += 1
        epoch = self._model_idle_epoch
        timer = threading.Timer(
            idle_seconds, self._queue_model_idle_unload, (epoch,))
        timer.daemon = True
        timer.start()

    def _queue_model_idle_unload(self, epoch):
        # Timer callbacks run on fresh threads. Keep CUDA/model teardown on the
        # permanent executor thread, and re-check the epoch again after any
        # transcription already queued ahead of this request has completed.
        if epoch != self._model_idle_epoch:
            return
        self._model_executor.submit(self._unload_model_if_idle, epoch)

    def _unload_model_if_idle(self, epoch):
        if epoch != self._model_idle_epoch or self.recording:
            return
        if not self.transcriber.loaded(self.settings["model"]):
            return
        idle_seconds = int(self.settings.get("gpu_idle_unload_sec", 0) or 0)
        elapsed = time.perf_counter() - self._last_model_use
        if elapsed < idle_seconds:
            self._schedule_model_idle_unload()
            return
        self.transcriber.unload()
        self.model_status = "unloaded"
        self.model_status_detail = "Model unloaded to release resources"
        self._log("model unloaded after %ds idle; hotkey remains active" % idle_seconds)

    @staticmethod
    def _log(message):
        print("[presspeech] %s" % message, flush=True)
        try:
            os.makedirs(cfg.CONFIG_DIR, exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(time.strftime("%H:%M:%S ") + message + "\n")
        except Exception:
            pass


def _selftest():
    settings = cfg.load()
    transcriber = engine.Transcriber(precision=settings.get("precision", "fp16"))
    model_name = settings["model"]
    PresspeechApp._log("self-test loading model: %s" % model_name)
    try:
        transcriber.load(
            model_name,
            notify=lambda title, msg: PresspeechApp._log("self-test: " + msg))
        PresspeechApp._log("self-test transcribing silent clip")
        text = transcriber.transcribe(
            np.zeros(16000, dtype=np.float32), language="en")
        PresspeechApp._log(
            "self-test OK: %d characters returned" % len(text))
    finally:
        transcriber.unload()


def _package_selftest():
    """Verify that the frozen executable contains every lazy runtime import."""
    if not getattr(sys, "frozen", False):
        raise RuntimeError("packaged self-test requires a frozen executable")
    for module_name, symbols in PACKAGE_SMOKE_IMPORTS:
        try:
            module = importlib.import_module(module_name)
        except Exception:
            # Keep release logs deterministic and free of runner-local paths.
            raise RuntimeError(
                "packaged import unavailable: %s" % module_name) from None
        for symbol in symbols:
            try:
                getattr(module, symbol)
            except Exception:
                raise RuntimeError(
                    "packaged import unavailable: %s.%s" %
                    (module_name, symbol)) from None


def _write_package_selftest_result(result):
    """Write the small build-script handshake without using app logging."""
    result_path = os.environ.get("PRESSPEECH_PACKAGE_SELFTEST_RESULT", "")
    if not result_path:
        return
    try:
        with open(result_path, "w", encoding="ascii", newline="\n") as handle:
            handle.write(result + "\n")
    except OSError:
        pass


if __name__ == "__main__":
    if "--package-selftest" in sys.argv:
        try:
            _package_selftest()
            outcome = "ok"
            exit_code = 0
        except Exception as exc:
            outcome = str(exc)
            exit_code = 1
        _write_package_selftest_result(outcome)
        # GUI-mode frozen executables have no reliable stdout/stderr, and a
        # few native libraries keep worker threads alive during finalisation.
        os._exit(exit_code)
    if "--selftest" in sys.argv:
        _selftest()
        # Some native CUDA worker threads outlive Python finalisation in a
        # frozen GUI executable. The test has completed and unloaded the model,
        # so avoid leaving a headless packaging-check process behind.
        if getattr(sys, "frozen", False):
            os._exit(0)
        sys.exit(0)
    PresspeechApp().run()
