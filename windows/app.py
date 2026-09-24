"""Presspeech for Windows - local push-to-talk dictation.

Hold a hotkey, speak, release, and the transcript is typed at the cursor.
Everything runs locally (Whisper via faster-whisper); no cloud, no accounts.
"""

import ctypes
import importlib
import math
import os
import platform
import queue
import re
import subprocess
import struct
import sys
import threading
import time
import unicodedata
import winsound
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext

# Hugging Face clients read privacy and endpoint settings at import time.  This
# local bootstrap must precede every third-party import, including indirect
# imports added by a future dependency.
import model_network

import numpy as np
import soxr
import sounddevice as sd
import clipboard_delivery
import keyboard_delivery
import session_events
from paste_target import (
    PasteTarget, stable_focus_and_caption as _stable_focus_and_caption,
    matches as _paste_target_matches, same_window as _paste_target_same_window,
    input_integrity_blocks_delivery as _input_integrity_blocks_delivery,
    requires_terminal_review as _requires_terminal_review,
)
from pynput import keyboard as pkb
from PIL import Image, ImageDraw
from pystray import Icon, Menu, MenuItem

import config as cfg
import engine
import ui
import updates
from british import to_british
from audio_backend import AudioBackend

AUDIO_BACKEND = AudioBackend(sd)

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
MIN_TRANSCRIPTION_AUDIO_SECONDS = 0.25
MIN_TRANSCRIPTION_AUDIO_SAMPLES = int(16000 * MIN_TRANSCRIPTION_AUDIO_SECONDS)
MICROPHONE_CHECK_LISTEN_SEC = 2.0
MICROPHONE_CHECK_AUDIO_RMS = POST_ROLL_ABS_SILENCE_RMS
MICROPHONE_CHECK_LEVEL = "level"
MICROPHONE_CHECK_SILENT = "silent"
MICROPHONE_CHECK_UNAVAILABLE = "unavailable"
MICROPHONE_CHECK_BUSY = "busy"
MICROPHONE_START_TIMEOUT_SEC = 5.0
MODEL_WARMUP_SEC = 8.0
MODEL_IDLE_WAKE_SEC = 60.0
CPU_FIRST_RUN_MODEL = "base.en"
PASTE_DELAY_SEC = 0.01
RDP_PASTE_DELAY_SEC = 0.08
NO_SPEECH_FEEDBACK_SEC = 2.5
NOT_READY_FEEDBACK_SEC = 2.5
NO_SPEECH_OUTCOME = "no_speech"
NO_TEXT_OUTCOME = "no_text"
NO_CONTENT_OUTCOME = "no_content"
AUDIO_INCOMPLETE_OUTCOME = "audio_incomplete"
TRANSCRIPTION_START_FAILED_OUTCOME = "transcription_start_failed"

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
SUPPORT_GUIDE_URL = (
    "https://github.com/rcourtman/presspeech/blob/main/SUPPORT.md"
)
APP_COMPATIBILITY_GUIDE_URL = (
    "https://rcourtman.github.io/presspeech/app-compatibility.html"
)
INPUT_DEVICE_SKIP_WORDS = (
    "stereo mix", "steam", "stream", "virtual", "loopback", "aux", "line in",
    "hyperx",
)
UNSAFE_INPUT_HOST_APIS = ("wdm-ks",)

LOG_PATH = os.path.join(cfg.CONFIG_DIR, "log.txt")
UPDATE_CHECK_INTERVAL_SEC = 24 * 60 * 60
HOTKEY_OBSERVATION_FEEDBACK_SEC = 10.0

# The frozen app imports UI and capture dependencies at startup. Exercise the
# model backends and other lazy imports explicitly before an installer can be
# created, without downloading weights or opening the microphone.
PACKAGE_SMOKE_IMPORTS = (
    ("torch", ("cuda",)),
    ("hf_xet", ()),
    ("huggingface_hub.constants", (
        "ENDPOINT",
        "HF_HUB_DISABLE_IMPLICIT_TOKEN",
        "HF_HUB_DISABLE_TELEMETRY",
        "HF_HUB_DISABLE_XET",
        "HF_DEBUG",
    )),
    ("huggingface_hub.utils", ("build_hf_headers",)),
    ("huggingface_hub.file_download", ("is_xet_available",)),
    ("model_integrity", ("MODEL_FILE_SHA256",)),
    ("transformers", (
        "AutoModelForRNNT",
        "AutoModelForTDT",
        "AutoProcessor",
        "MoonshineStreamingForConditionalGeneration",
    )),
    ("faster_whisper", ("WhisperModel",)),
    ("transformers.utils.hub", ("SESSION_ID", "http_user_agent")),
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
    ("tk_uia", (
        "add_acc_object", "enable", "label_for", "set_acc_description",
        "set_acc_name",
    )),
)


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


def _apply_dictionary_rules(text, rules, *, return_effects=False):
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
    if return_effects:
        return (text, any(not replacement for _, _, replacement in matches),
                any(bool(replacement) for _, _, replacement in matches))
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
                # Endpoint labels may identify a person, room, or organisation.
                # Keep only the failure category in the persistent app log.
                failures.append(type(exc).__name__)
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
                failures.append("endpoint disappeared")
                continue
            try:
                device.EndpointVolume.SetMute(1 if was_muted else 0, None)
                restored += 1
            except Exception as exc:
                failures.append(type(exc).__name__)
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
    """Return the foreground window, process and available focus signals."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                    ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
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
        thread_identifier = user32.GetWindowThreadProcessId(
            hwnd, ctypes.byref(process_id))
        if not thread_identifier:
            return PasteTarget("", int(hwnd))
        # GetWindowTextW sends a window message for in-process captions. The
        # private scratchpad needs no tab guard, so avoid querying our own UI
        # from a worker that might be waiting on the Tk thread. For external
        # windows, bracket focus with title reads rather than mixing a control
        # from one browser tab with the title of another.
        focus_handle, caption_fingerprint = _stable_focus_and_caption(
            user32, int(hwnd), int(thread_identifier),
            read_caption=process_id.value != os.getpid())
        handle = kernel32.OpenProcess(0x1000, False, process_id.value)
        if not handle:
            return PasteTarget(
                "", int(hwnd), int(process_id.value),
                _process_integrity_level(process_id.value), focus_handle,
                caption_fingerprint)
        try:
            size = wintypes.DWORD(32768)
            path = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(
                    handle, 0, path, ctypes.byref(size)):
                return PasteTarget(
                    "", int(hwnd), int(process_id.value),
                    _process_integrity_level(process_id.value), focus_handle,
                    caption_fingerprint)
            return PasteTarget(
                os.path.basename(path.value).lower(), int(hwnd),
                int(process_id.value),
                _process_integrity_level(process_id.value), focus_handle,
                caption_fingerprint)
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return PasteTarget("", 0)


def _process_integrity_level(process_identifier):
    """Return a process's mandatory-integrity RID, or zero when unavailable."""
    process_handle = None
    token_handle = None
    try:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE, wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ]
        advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
        advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
        advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)

        process_handle = kernel32.OpenProcess(
            0x1000, False, int(process_identifier))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not process_handle:
            return 0
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(
                process_handle, 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
            return 0
        token_handle = token.value

        needed = wintypes.DWORD()
        # TokenIntegrityLevel is variable-sized. The first call obtains the
        # TOKEN_MANDATORY_LABEL buffer size and is expected to fail with
        # ERROR_INSUFFICIENT_BUFFER.
        advapi32.GetTokenInformation(
            token, 25, None, 0, ctypes.byref(needed))
        if not needed.value:
            return 0
        information = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(
                token, 25, information, needed.value, ctypes.byref(needed)):
            return 0
        # TOKEN_MANDATORY_LABEL starts with SID_AND_ATTRIBUTES, whose first
        # field is the SID pointer. The final SID sub-authority is the
        # mandatory-integrity RID (low, medium, high, or system).
        sid = ctypes.cast(
            information, ctypes.POINTER(ctypes.c_void_p)).contents.value
        if not sid:
            return 0
        count = advapi32.GetSidSubAuthorityCount(sid)
        if not count or not count.contents.value:
            return 0
        authority = advapi32.GetSidSubAuthority(
            sid, count.contents.value - 1)
        return int(authority.contents.value) if authority else 0
    except Exception:
        return 0
    finally:
        try:
            if token_handle:
                kernel32.CloseHandle(token_handle)
        except Exception:
            pass
        try:
            if process_handle:
                kernel32.CloseHandle(process_handle)
        except Exception:
            pass


def _paste_target_blocks_simulated_input(paste_target, source_integrity=None):
    """Fail closed unless the target is proven reachable under UIPI."""
    target_integrity = getattr(paste_target, "integrity_level", 0)
    if type(target_integrity) is not int or target_integrity <= 0:
        # A failed target-token query can be exactly the elevated/protected
        # destination that rejects SendInput. Keep the prior clipboard item
        # until the user explicitly chooses Copy in Delivery Recovery.
        return True
    if source_integrity is None:
        source_integrity = _process_integrity_level(os.getpid())
    # An unreadable own-token query is likewise not evidence of equal or
    # lower target privilege. Retain the text before replacing the clipboard.
    return _input_integrity_blocks_delivery(target_integrity, source_integrity)


def _paste_route(process_name):
    """Choose a paste method, or None when the app image is unavailable."""
    # A window/PID match cannot distinguish local, RDP, or Moonlight input.
    if not isinstance(process_name, str) or not process_name:
        return None
    name = process_name.lower()
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


def _needs_first_run_download_choice(settings, model_name):
    """Require consent for either default model's first-run network fetch."""
    return (
        not settings.get("setup_complete", True) and
        (engine.is_parakeet(model_name) or model_name == CPU_FIRST_RUN_MODEL))


def _first_run_download_detail(model_name):
    if model_name == CPU_FIRST_RUN_MODEL:
        return "English-only Whisper base.en model download is about 141 MiB"
    return "Full Parakeet model download is about 2.5 GB"


def _model_timing_summary(timing):
    """Format privacy-safe backend timing, including Whisper VAD retention."""
    if not isinstance(timing, dict) or not timing:
        return None
    speech_seconds = timing.get("speech_seconds")
    speech = ("-" if not isinstance(speech_seconds, (int, float))
              else "%.3fs" % speech_seconds)
    return (
        "model detail: backend=%s bucket=%s chunks=%s max_chunk=%s speech=%s lock=%.3fs "
        "prepare=%.3fs transfer=%.3fs generate=%.3fs decode=%.3fs" % (
            timing.get("backend", ""),
            timing.get("bucket_seconds", "-"),
            timing.get("chunk_count", "-"),
            ("-" if not isinstance(timing.get("max_chunk_seconds"), (int, float))
             else "%.3fs" % timing["max_chunk_seconds"]),
            speech,
            timing.get("lock_wait", 0.0),
            timing.get("prepare", 0.0),
            timing.get("transfer", 0.0),
            timing.get("generate", timing.get("inference", 0.0)),
            timing.get("decode", 0.0),
        )
    )


def _diagnostic_microphone_lines(configured, active):
    """Describe microphone state without exporting user-controlled labels."""
    configured_state = (
        "Automatic" if configured == AUTO_INPUT_DEVICE
        else "Specific input (name omitted)"
    )
    active_state = "Not opened"
    if (isinstance(active, tuple) and len(active) >= 2 and
            type(active[1]) in (int, float) and
            math.isfinite(active[1]) and active[1] > 0):
        active_state = "Open at %d Hz (name omitted)" % active[1]
    return (
        "Configured microphone: %s" % configured_state,
        "Active microphone: %s" % active_state,
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
        # Covers readiness/target checks before recording becomes true so
        # Settings cannot commit a different dictation configuration midway
        # through that start transition.
        self._starting_recording = False
        self._canceling_recording = False
        self._microphone_check_in_progress = False
        self.transcribing = False
        self.lock = threading.Lock()
        self.scratchpad = None
        self.settings_window = None
        self.setup_window = None
        self.update_window = None
        # Open commands arrive from the tray, hotkey worker, activation event,
        # and Tk. Serialize first construction so two callers cannot queue
        # duplicate dialogs while the UI thread is still building one.
        self._window_open_lock = threading.Lock()
        self.delivery_recovery_window = None
        self._delivery_recovery_window_lock = threading.Lock()
        self.pending_update = None
        self.model_status = "pending"
        self.model_status_detail = "Waiting to load"
        self.model_download_progress = None
        self._initial_model_download_consented = False
        self._initial_model_download_consent_model = None
        self._model_retry_lock = threading.Lock()
        self._model_load_target = None
        self._model_load_generation = 0
        self._update_lock = threading.Lock()
        self._update_installing = False
        self.icon = None
        self.listener = None
        self._hotkey_listener_lock = threading.Lock()
        self._hotkey_status = "not started"
        self._hotkey_status_detail = "Global hotkey has not started"
        self._last_hotkey_observation = None
        self._session_repair_lock = threading.Lock()
        self._session_repair_generation = 0
        self._session_repair_running = False
        self._session_monitor = None
        self._session_available = True
        self._exiting = False
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
        # WH_KEYBOARD_LL callbacks have a hard Windows timeout and are
        # silently removed when they exceed it. Keep the hook limited to raw
        # transaction bookkeeping, a non-blocking enqueue, and suppression;
        # target discovery, logging, UI work, and recording control run here.
        self._hotkey_action_lock = threading.Lock()
        self._hotkey_repairing = False
        # Listener replacement clears the raw-hook transaction atomically.
        # Keep this separate from _hotkey_action_lock: a slow recording action
        # must never make the time-limited native hook wait behind the worker.
        self._hotkey_transaction_lock = threading.Lock()
        self._hotkey_action_queue = queue.SimpleQueue()
        self._hotkey_action_generation = 0
        self._hotkey_action_thread = threading.Thread(
            target=self._run_hotkey_actions,
            name="presspeech-hotkey-actions",
            daemon=True,
        )
        self._hotkey_action_thread.start()
        # Clipboard and input failures can leave delivery uncertain. Keep the
        # transcript in process memory until an explicit Copy, Discard or Exit.
        # Never write this recovery queue to configuration, diagnostics, or the log.
        self._undelivered_dictations = []
        self._undelivered_lock = threading.Lock()
        self._recording_paste_target = PasteTarget("", 0)
        self._recording_scratchpad = None
        self._rec_epoch = 0
        self._recording_limit_timer = None
        self._peak_rms = 0.0
        # PortAudio reports a discontinuity after it discards microphone
        # samples. Keep this per recording, not as mutable model-worker state.
        self._input_overflowed = False
        # Monotonic within one recording. The post-roll timer snapshots this
        # at the hold/toggle stop gesture and also checks when the newest
        # accepted callback began. The timestamp prevents a callback that was
        # already copying pre-release audio from satisfying the boundary gate.
        self._audio_sequence = 0
        self._last_audio_callback_started_at = 0.0
        self._capture_ready = False
        self._capture_ready_at = 0.0
        # A release during microphone startup is a failed attempt, not a
        # request to begin accepting audio during the post-roll window.
        self._stop_before_ready = False
        self._first_audio_callback = None
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
        # This is a reusable discovery cache for the configured selector. An
        # open recording owns a separate snapshot because Settings can
        # invalidate the cache while that stream still needs its original
        # sample rate for post-roll analysis and resampling.
        self.input_device = None
        self._cached_input_selector = None
        self._cached_input_topology = None
        self._recording_input_device = None
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
                MenuItem("Repair Global Hotkey", self.repair_hotkey),
                Menu.SEPARATOR,
                MenuItem(
                    "Review Undelivered Dictation\u2026",
                    self.open_delivery_recovery,
                    enabled=lambda _item: self.has_undelivered_dictation()),
                MenuItem(
                    "Copy Undelivered Dictation (replaces clipboard)",
                    self.copy_undelivered_dictation,
                    enabled=lambda _item: self.has_undelivered_dictation()),
                MenuItem(
                    "Discard Undelivered Dictation",
                    self.discard_undelivered_dictation,
                    enabled=lambda _item: self.has_undelivered_dictation()),
                MenuItem("Check for Updates\u2026", self.check_for_updates),
                MenuItem("Copy Diagnostics", self.copy_diagnostics),
                MenuItem(
                    "Test App Compatibility\u2026", self.test_app_compatibility),
                MenuItem("Report a Problem\u2026", self.report_problem),
                MenuItem("Suggest an Improvement\u2026", self.suggest_improvement),
                Menu.SEPARATOR,
                MenuItem("Exit", self.exit_app),
            ),
        )
        threading.Thread(target=self.icon.run, daemon=True).start()
        hotkey_started = self._start_hotkey_listener()
        self._session_monitor = session_events.SessionEventMonitor(
            self._on_windows_session_pause,
            self._on_windows_session_resume,
            self._log,
        )
        try:
            self._session_monitor.start()
        except Exception:
            # A missing notification channel must not prevent ordinary
            # dictation; the tray's manual Repair action remains available.
            self._log("Windows session monitor unavailable")
            self._session_monitor.stop()
            self._session_monitor = None
        self._log("running; hotkey=%s trigger=%s" % (self.settings["hotkey"], self.settings["trigger"]))
        with self._model_retry_lock:
            self._queue_model_load_locked(None)
        if not self.settings.get("setup_complete", False):
            threading.Timer(0.8, self.open_setup).start()
        elif not hotkey_started:
            # A completed setup must not leave the app apparently running with
            # its primary control unavailable. The tray Dictate command still
            # works while Settings exposes a keyboard-accessible repair action.
            threading.Timer(0.8, self.open_settings).start()
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
        # Reopening the app is navigation, never consent to overwrite a newer
        # clipboard with retained private text. Keep existing controls reachable.
        if self.has_undelivered_dictation():
            self._notify_undelivered_dictation()
            if self.open_delivery_recovery():
                self._log("repeat launch opened delivery recovery")
                return
        # Preserve the user's current task when a window already exists,
        # including a minimized or covered update/setup/settings window.
        for window in (
                getattr(self, "delivery_recovery_window", None),
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
        self._exiting = True
        monitor = getattr(self, "_session_monitor", None)
        if monitor is not None:
            monitor.stop()
        self._clear_undelivered_dictations()
        self._restore_playback_after_recording()
        self.indicator.close()
        if self.listener is not None:
            self.listener.stop()
        if self.update_window is not None:
            # os._exit() skips normal thread finalization, so explicitly
            # hand updater cleanup off before terminating daemon threads.
            self.update_window.cancel_and_cleanup()
        for win in (self.scratchpad, self.settings_window,
                    self.setup_window, self.update_window,
                    getattr(self, "delivery_recovery_window", None)):
            if win is not None and win.root is not None:
                try:
                    win.root.after(0, win.root.destroy)
                except Exception:
                    pass
        if self.icon is not None:
            self.icon.stop()
        os._exit(0)

    # ---------------- hotkey ----------------

    def hotkey_available(self):
        """Return whether listener startup was reported successful."""
        return getattr(self, "_hotkey_status", "not started") == "ready"

    def hotkey_listener_status(self):
        """Report listener startup separately from a recently observed key.

        Windows can silently remove a low-level hook while its thread remains
        alive. Neither a successful start nor an old key event proves that the
        hook still works, so never describe either as ongoing verification.
        """
        status = getattr(self, "_hotkey_status", "not started")
        if status == "ready":
            hotkey = self.settings["hotkey"]
            observation = getattr(self, "_last_hotkey_observation", None)
            age = (time.monotonic() - observation[1]
                   if observation is not None and observation[0] == hotkey
                   else None)
            detail = (
                "Key just detected \u2014 %s" % hotkey.title()
                if age is not None and 0 <= age <= HOTKEY_OBSERVATION_FEEDBACK_SEC
                else "Listener started \u2014 %s" % hotkey.title())
        else:
            detail = getattr(
                self, "_hotkey_status_detail",
                "Global hotkey has not started")
        return status, detail

    def _reset_hotkey_transaction(self):
        """Discard physical-key bookkeeping owned by a stopped listener."""
        action_lock = getattr(self, "_hotkey_action_lock", None)
        with action_lock if action_lock is not None else nullcontext():
            transaction_lock = getattr(self, "_hotkey_transaction_lock", None)
            with transaction_lock if transaction_lock is not None else nullcontext():
                # A callback queued by the previous native hook must not start
                # or stop a recording after Repair has replaced that hook.
                # Serializing this generation change with the raw filter also
                # prevents a stale callback from repopulating cleared state.
                self._hotkey_action_generation = (
                    getattr(self, "_hotkey_action_generation", 0) + 1)
                self._key_held = False
                self._pressed_keys.clear()
                self._held_hotkey_keys = frozenset()
                self._held_hotkey_trigger = None
                self._suppress_escape_keyup = False
                self._filter_pressed_vks.clear()
                self._passthrough_hotkey_vks.clear()
                self._suppressed_hotkey_vks.clear()
                self._last_hotkey_observation = None

    def _start_hotkey_listener(self, force=False):
        """Create a fresh listener after startup failure or listener exit."""
        with self._hotkey_listener_lock:
            current = self.listener
            if not force and self.hotkey_available() and current is not None:
                try:
                    if current.is_alive():
                        return True
                except Exception:
                    pass

            if force and current is not None:
                # Windows can silently remove a low-level hook while pynput's
                # message-loop thread remains alive. A user-requested repair
                # must therefore replace even an apparently healthy listener.
                try:
                    current.stop()
                except Exception as exc:
                    self._hotkey_status = "error"
                    self._hotkey_status_detail = (
                        "Global hotkey could not restart. Exit and reopen "
                        "Presspeech.")
                    self._log(
                        "global hotkey could not stop: %s" % type(exc).__name__)
                    return False

            self._hotkey_status = "starting"
            self._hotkey_status_detail = "Starting global hotkey\u2026"
            self._reset_hotkey_transaction()
            listener_generation = self._hotkey_action_generation
            try:
                listener = None

                def event_filter(message, data):
                    return self._win32_event_filter(
                        message, data, listener=listener,
                        generation=listener_generation)

                listener = pkb.Listener(
                    on_press=self._on_press,
                    on_release=self._on_release,
                    win32_event_filter=event_filter,
                )
                self.listener = listener
                listener.start()
            except Exception as exc:
                self._hotkey_status = "error"
                self._hotkey_status_detail = (
                    "Global hotkey could not start. Choose Repair Global Hotkey.")
                self._log(
                    "global hotkey could not start: %s" % type(exc).__name__)
                return False

            self._hotkey_status = "ready"
            # "ready" gates the app's existing startup flow. It does not mean
            # Windows has confirmed this hook or will keep it installed.
            self._hotkey_status_detail = "Global hotkey listener started"
            threading.Thread(
                target=self._watch_hotkey_listener,
                args=(listener,),
                name="presspeech-hotkey-watch",
                daemon=True,
            ).start()
            return True

    def _watch_hotkey_listener(self, listener):
        """Expose a listener exit that pynput otherwise leaves on its thread."""
        failure = None
        try:
            listener.join()
        except Exception as exc:
            # Exception details can contain a key value or library internals;
            # the type is enough for privacy-safe diagnostics.
            failure = type(exc).__name__

        with self._hotkey_listener_lock:
            if (getattr(self, "_exiting", False) or
                    self.listener is not listener):
                return
            self._hotkey_status = "error"
            self._hotkey_status_detail = (
                "Global hotkey stopped. Choose Repair Global Hotkey.")
            self._reset_hotkey_transaction()
        suffix = (": %s" % failure) if failure else ""
        self._log("global hotkey listener stopped%s" % suffix)
        self.notify(
            "Global hotkey needs repair",
            "Open the Presspeech notification-area menu and choose Repair "
            "Global Hotkey. Dictate remains available from the menu.")

    def _retire_queued_hotkey_actions_for_repair(self):
        """Make an idle repair atomic with respect to queued hook actions."""
        action_lock = getattr(self, "_hotkey_action_lock", None)
        with action_lock if action_lock is not None else nullcontext():
            # A press may be queued while start_recording has not yet published
            # recording=True. Either the worker wins this lock and publishes
            # that state, or Repair wins and retires the queued press. Without
            # this boundary, Repair could clear the held-key transaction just
            # after the worker starts recording, leaving no release to stop it.
            # Menu starts bypass the action lock, so also respect their claimed
            # start transition before replacing the listener.
            if (getattr(self, "_hotkey_repairing", False) or
                    getattr(self, "recording", False) or
                    getattr(self, "_starting_recording", False) or
                    getattr(self, "_canceling_recording", False) or
                    getattr(self, "transcribing", False)):
                return False
            # Close the gap between the idle check and listener replacement.
            # The worker drops later actions in this generation; menu starts
            # also check this flag before claiming a recording transition.
            self._hotkey_repairing = True
            transaction_lock = getattr(self, "_hotkey_transaction_lock", None)
            with transaction_lock if transaction_lock is not None else nullcontext():
                self._hotkey_action_generation = (
                    getattr(self, "_hotkey_action_generation", 0) + 1)
            return True

    def _attempt_hotkey_repair(self):
        """Return ready, busy, or failed without displaying a notification."""
        if not self._retire_queued_hotkey_actions_for_repair():
            return "busy"
        try:
            return ("ready" if self._start_hotkey_listener(force=True)
                    else "failed")
        except Exception:
            self._hotkey_status = "error"
            self._hotkey_status_detail = (
                "Global hotkey needs repair. Choose Repair Global Hotkey.")
            self._log("global hotkey repair failed")
            return "failed"
        finally:
            action_lock = getattr(self, "_hotkey_action_lock", None)
            with action_lock if action_lock is not None else nullcontext():
                self._hotkey_repairing = False

    def repair_hotkey(self, icon=None, item=None):
        """Attempt user-requested recovery with a new pynput listener."""
        # A missing resume notification must not leave a returned desktop
        # permanently unable to dictate. The user can only choose this menu
        # action from an interactive session.
        with self.lock:
            self._session_available = True
        result = self._attempt_hotkey_repair()
        if result == "busy":
            self.notify(
                "Finish the active dictation first",
                "Stop or cancel dictation, then choose Repair Global Hotkey.")
            return False
        if result == "ready":
            self.notify(
                "Global hotkey listener restarted",
                "Try %s in Try Dictation to confirm it responds." %
                self.settings["hotkey"].title())
            return True
        self.notify(
            "Global hotkey still unavailable",
            "Try another key in Settings, or exit and reopen Presspeech.")
        return False

    def _on_windows_session_pause(self):
        """Discard a held capture if Windows takes the user's desktop away."""
        if getattr(self, "_exiting", False):
            return
        with self.lock:
            self._session_available = False
        if self.cancel_recording():
            self._log("Windows session unavailable; active capture discarded")

    def _on_windows_session_resume(self):
        """Queue a fresh hook; an alive pynput thread is not proof it survived."""
        with self._session_repair_lock:
            if getattr(self, "_exiting", False):
                return
            with self.lock:
                self._session_available = True
            self._session_repair_generation += 1
            self._hotkey_status = "starting"
            self._hotkey_status_detail = "Reconnecting global hotkey…"
            if self._session_repair_running:
                return
            self._session_repair_running = True
        try:
            threading.Thread(
                target=self._repair_hotkey_after_session,
                name="presspeech-session-hotkey-repair", daemon=True,
            ).start()
        except Exception:
            with self._session_repair_lock:
                self._session_repair_running = False
            self._hotkey_status = "error"
            self._hotkey_status_detail = (
                "Global hotkey needs repair. Choose Repair Global Hotkey.")
            self._log("global hotkey session repair could not start")

    def _repair_hotkey_after_session(self):
        finished = False
        try:
            while not getattr(self, "_exiting", False):
                with self._session_repair_lock:
                    generation = self._session_repair_generation
                deadline = time.monotonic() + 60
                while not getattr(self, "_exiting", False):
                    result = self._attempt_hotkey_repair()
                    if result != "busy":
                        break
                    if time.monotonic() >= deadline:
                        self._hotkey_status = "error"
                        self._hotkey_status_detail = (
                            "Global hotkey needs repair. Choose Repair Global Hotkey.")
                        self.notify(
                            "Global hotkey needs repair",
                            "Finish dictation, then choose Repair Global Hotkey "
                            "from the notification-area menu.")
                        break
                    time.sleep(0.25)
                if getattr(self, "_exiting", False):
                    break
                if result == "ready":
                    self._log("global hotkey reconnected after Windows session change")
                elif result == "failed":
                    self.notify(
                        "Global hotkey needs repair",
                        "Choose Repair Global Hotkey from the notification-area "
                        "menu, or exit and reopen Presspeech.")
                with self._session_repair_lock:
                    if generation == self._session_repair_generation:
                        self._session_repair_running = False
                        finished = True
                        return
        except Exception:
            self._hotkey_status = "error"
            self._hotkey_status_detail = (
                "Global hotkey needs repair. Choose Repair Global Hotkey.")
            try:
                self._log("global hotkey session repair failed")
                self.notify(
                    "Global hotkey needs repair",
                    "Choose Repair Global Hotkey from the notification-area menu.")
            except Exception:
                pass
        finally:
            # A new event arriving after the success path's atomic generation
            # check starts its own worker; do not clear that new worker's flag.
            if not finished:
                with self._session_repair_lock:
                    self._session_repair_running = False

    def _is_hotkey(self, key):
        return key in KEY_MAP.get(self.settings["hotkey"], set())

    def _perform_hotkey_action(self, callback, key):
        """Run one serialized hook action outside Windows' hook callback."""
        try:
            callback(key)
        except Exception as exc:
            # Exception type is enough for diagnostics and cannot contain a
            # transcript or device name.
            try:
                self._log(
                    "reserved hotkey action failed: %s" % type(exc).__name__)
            except Exception:
                pass

    def _run_hotkey_actions(self):
        """Preserve physical event order without doing work on the hook."""
        while True:
            generation, callback, key = self._hotkey_action_queue.get()
            with self._hotkey_action_lock:
                if (getattr(self, "_exiting", False) or
                        getattr(self, "_hotkey_repairing", False) or
                        generation != self._hotkey_action_generation):
                    continue
                self._perform_hotkey_action(callback, key)

    def _queue_hotkey_action(self, callback, key, generation):
        """Enqueue a reserved key action without waiting for its work."""
        self._hotkey_action_queue.put((generation, callback, key))

    def _dispatch_and_suppress_win32_event(
            self, callback=None, key=None, *, listener=None, generation=None):
        """Queue an internal action and always reserve the physical event."""
        try:
            if callback is not None:
                self._queue_hotkey_action(
                    callback, key,
                    (getattr(self, "_hotkey_action_generation", 0)
                     if generation is None else generation),
                )
        except Exception:
            # Queue allocation is the only expected failure surface here. Do
            # not log from the time-limited hook or expose internal details,
            # but make the listener's degraded state visible for repair.
            self._hotkey_status = "error"
            self._hotkey_status_detail = (
                "Global hotkey action failed. Choose Repair Global Hotkey.")
        finally:
            # pynput implements selective system-wide suppression by raising
            # an internal exception from this call. Enqueue first, then return
            # from the native hook immediately; action failures can never leak
            # the reserved key to the focused app.
            (listener if listener is not None else self.listener).suppress_event()
        return False

    def _win32_event_filter(
            self, message, data, *, listener=None, generation=None):
        """Dispatch and consume physical PTT or recording-cancel transactions.

        pynput's selective Windows suppression prevents the corresponding
        on_press/on_release callback from being queued. Queue the internal
        action before calling suppress_event so Presspeech still observes the
        key while Windows and the focused app do not.
        """
        transaction_lock = getattr(self, "_hotkey_transaction_lock", None)
        with transaction_lock if transaction_lock is not None else nullcontext():
            return self._win32_event_filter_transaction(
                message, data, listener=listener, generation=generation)

    def _win32_event_filter_transaction(
            self, message, data, *, listener=None, generation=None):
        """Handle one raw event while listener transaction state is locked."""
        current_generation = getattr(self, "_hotkey_action_generation", 0)
        if generation is not None and generation != current_generation:
            # A stopped listener can finish a callback while its replacement
            # starts. Never let that stale native hook mutate the replacement's
            # transaction state or enqueue an action in its generation.
            return
        dispatch_generation = (
            current_generation if generation is None else generation)
        try:
            flags = int(getattr(data, "flags", 0))
        except (TypeError, ValueError):
            flags = 0
        # Ctrl+V is generated with SendInput and must reach the target even when
        # the user's configured PTT key is Ctrl. It is already guarded
        # separately by _injecting_keys in the ordinary callbacks.
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
                        self._on_press, pkb.Key.esc, listener=listener,
                        generation=dispatch_generation)
            else:
                if not self._suppress_escape_keyup:
                    return
                self._suppress_escape_keyup = False
                return self._dispatch_and_suppress_win32_event(
                    self._on_release, pkb.Key.esc, listener=listener,
                    generation=dispatch_generation)
            return self._dispatch_and_suppress_win32_event(
                listener=listener, generation=dispatch_generation)

        if is_release and vk_code in self._suppressed_hotkey_vks:
            key = self._suppressed_hotkey_vks.pop(vk_code)
            return self._dispatch_and_suppress_win32_event(
                self._on_release, key, listener=listener,
                generation=dispatch_generation)

        hotkey_name = self.settings.get("hotkey")
        configured = HOTKEY_VIRTUAL_KEYS.get(hotkey_name)
        if not is_press or configured is None or vk_code != configured[0]:
            return

        # Windows implements AltGr as synthetic Left Ctrl + Right Alt. Leave
        # that complete transaction available for layout characters; the
        # ordinary callback applies the same guard before starting dictation.
        if (hotkey_name == "right alt" and
                (0xA2 in self._filter_pressed_vks or
                 pkb.Key.ctrl_l in self._pressed_keys)):
            self._passthrough_hotkey_vks.add(vk_code)
            return

        if vk_code not in self._suppressed_hotkey_vks:
            key = configured[1]
            self._suppressed_hotkey_vks[vk_code] = key
            # Only a physical configured-key event proves that this listener
            # received a hook callback. Keep the feedback brief: Windows does
            # not notify us if it silently removes the hook afterwards.
            self._last_hotkey_observation = (
                hotkey_name, time.monotonic())
            return self._dispatch_and_suppress_win32_event(
                self._on_press, key, listener=listener,
                generation=dispatch_generation)
        # Consume key-down autorepeats as part of the same reserved PTT
        # transaction. The matched key-up remains reserved even if Settings is
        # changed while the key is physically held.
        return self._dispatch_and_suppress_win32_event(
            listener=listener, generation=dispatch_generation)

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

        if status == "awaiting_download_consent":
            self._log("dictation deferred; first-run model download needs a choice")
            self.open_setup()
            return False

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
            self.model_status_detail = "Checking local model files…"
            self.model_download_progress = None
            self._queue_model_load_locked(model_name)
            return True

    def confirm_initial_model_download(self):
        """Start a missing first-run model download after explicit choice."""
        with self._model_retry_lock:
            if (getattr(self, "model_status", "pending") !=
                    "awaiting_download_consent" or
                    not _needs_first_run_download_choice(
                        self.settings, self.settings.get("model"))):
                return False
            self._initial_model_download_consented = True
            model_name = self.settings["model"]
            self._initial_model_download_consent_model = model_name
            self.model_status = "loading"
            self.model_status_detail = "Checking local model files…"
            self.model_download_progress = None
            self._queue_model_load_locked(model_name)
            return True

    def select_cpu_model_after_download_declined(self):
        """Choose the smaller English-only CPU model instead of Parakeet."""
        with self._model_retry_lock:
            if (getattr(self, "model_status", "pending") !=
                    "awaiting_download_consent" or
                    not engine.is_parakeet(self.settings.get("model"))):
                return False
            self.settings["model"] = "base.en"
            self.settings["model_explicit"] = True
            cfg.save(self.settings)
            # Choosing the smaller CPU model here is also explicit consent to
            # fetch its missing files; do not present the same choice twice.
            self._initial_model_download_consented = True
            model_name = self.settings["model"]
            self._initial_model_download_consent_model = model_name
            self.model_status = "loading"
            self.model_status_detail = "Checking local model files…"
            self.model_download_progress = None
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
            self.model_status_detail = "Checking local model files…"
            self.model_download_progress = None
            self._queue_model_load_locked(model_name)
            return True

    def start_recording(self):
        # Claim the transition before model and foreground discovery. The
        # Settings window uses this same lock and lifecycle flag when saving.
        with self.lock:
            if not getattr(self, "_session_available", True):
                return False
            if getattr(self, "_hotkey_repairing", False):
                return False
            if getattr(self, "_update_installing", False):
                update_installing = True
                check_running = False
            elif getattr(self, "_microphone_check_in_progress", False):
                update_installing = False
                check_running = True
            else:
                update_installing = False
                check_running = False
                if getattr(self, "_starting_recording", False):
                    self._log("dictation ignored; another recording is starting")
                    return False
                self._starting_recording = True
        if update_installing:
            self.notify(
                "Update starting",
                "Wait for the update installer before starting another dictation.")
            return False
        if check_running:
            self.notify(
                "Microphone check in progress",
                "Wait for the microphone check to finish, then start dictation.")
            return False
        try:
            return self._start_recording_claimed()
        finally:
            with self.lock:
                self._starting_recording = False

    def _start_recording_claimed(self):
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
        if self.has_undelivered_dictation():
            self._log("dictation deferred; an undelivered transcript is waiting")
            self._notify_undelivered_dictation()
            self.open_delivery_recovery()
            return False
        if not self._dictation_model_ready():
            return False
        paste_target = _foreground_paste_target()
        with self.lock:
            # Recheck after foreground-process discovery so simultaneous tray
            # and hotkey starts cannot cross the busy boundary. Cancellation
            # can also begin after the unlocked fast-path check above.
            if (not getattr(self, "_session_available", True) or
                    getattr(self, "_hotkey_repairing", False) or
                    getattr(self, "_update_installing", False) or self.recording or
                    getattr(self, "_canceling_recording", False)
                    or getattr(self, "transcribing", False)):
                return False
            # The previous recording can finish delivery while foreground
            # discovery above is in progress. It may retain text and clear
            # transcribing before this lock is acquired, so the early recovery
            # check alone cannot authorize a new capture.
            if self.has_undelivered_dictation():
                self._log("dictation deferred; recovery became pending during startup")
                return False
            self._rec_epoch += 1
            epoch = self._rec_epoch
            self.recording = True
            self.buffer = []
            self._peak_rms = 0.0
            self._input_overflowed = False
            self._recording_input_device = None
            self._audio_sequence = 0
            self._last_audio_callback_started_at = 0.0
            self._capture_ready = False
            self._capture_ready_at = 0.0
            self._stop_before_ready = False
            self._first_audio_callback = threading.Event()
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
            # A quick release must never let this older start publish a stale
            # Connecting state after stop has already shown its result.
            self._set_indicator("connecting")
        self._log("microphone starting")
        self._wake_model_if_idle()
        self._schedule_recording_limit(epoch)
        threading.Thread(
            target=self._start_audio_worker, args=(epoch,), daemon=True).start()
        return True

    def _recording_epoch_active(self, epoch):
        with self.lock:
            return (self.recording and epoch == self._rec_epoch and
                    not getattr(self, "_stop_before_ready", False))

    def _start_audio_worker(self, epoch):
        # A cue or Listening indicator must not claim that capture has begun
        # before PortAudio has actually delivered a buffer. Open first, then
        # wait for that callback; discard all input until the cue has finished
        # and playback is muted. This also keeps the cue out of transcription.
        if not self._open_mic_worker(epoch):
            return
        with self.lock:
            first_callback = (self._first_audio_callback
                              if self.recording and epoch == self._rec_epoch
                              else None)
        if first_callback is None:
            return
        deadline = time.monotonic() + MICROPHONE_START_TIMEOUT_SEC
        while not first_callback.is_set():
            if not self._recording_epoch_active(epoch):
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._abort_unresponsive_microphone(epoch)
                return
            first_callback.wait(min(remaining, 0.05))
        if not self._recording_epoch_active(epoch):
            return
        if self.settings.get("audio_cues", True):
            self._play_cue_worker("start")
        if not self._recording_epoch_active(epoch):
            return
        self._mute_playback_for_recording(epoch)
        with self.lock:
            if (not self.recording or epoch != self._rec_epoch or
                    getattr(self, "_stop_before_ready", False)):
                return
            self._capture_ready_at = time.perf_counter()
            self._capture_ready = True
            # Publish readiness while the epoch is still owned. Otherwise a
            # quick release could hide the overlay and then have this worker
            # revive the stale Listening state or red tray icon.
            self._set_indicator("listening")
            if self.icon is not None:
                self.icon.icon = self.rec_icon
        self._log("microphone ready; recording audio")

    def _abort_unresponsive_microphone(self, epoch):
        with self.lock:
            if not self.recording or epoch != self._rec_epoch:
                return
            self.recording = False
            self._canceling_recording = True
            self._capture_ready = False
            stream = self.stream
            self.stream = None
            self._recording_input_device = None
            self._recording_paste_target = PasteTarget("", 0)
            self._recording_scratchpad = None
            self.input_device = None
            self._cached_input_selector = None
            self._cached_input_topology = None
            self.buffer = []
            self._peak_rms = 0.0
            self._input_overflowed = False
        try:
            self._cancel_recording_limit(epoch)
            if stream is not None:
                try:
                    stream.stop()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass
            self._restore_playback_after_recording()
            if self.icon is not None:
                self.icon.icon = self.idle_icon
            self._set_indicator(None)
            self._schedule_model_idle_unload()
            self._log("microphone started but delivered no audio buffers")
            self.notify(
                "Microphone not responding",
                "Presspeech could not receive audio from the selected input. "
                "Check the microphone in Setup or reconnect it, then try again.")
        finally:
            with self.lock:
                self._canceling_recording = False

    def _mute_playback_for_recording(self, epoch):
        if not self.settings.get("mute_playback_while_recording", True):
            return
        with self._playback_mute_lock:
            with self.lock:
                if (not self.recording or epoch != self._rec_epoch or
                        getattr(self, "_stop_before_ready", False)):
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
                self._log("could not mute playback: %s" % type(exc).__name__)

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
                self._log("could not restore playback mute state: %s" %
                          type(exc).__name__)

    def _open_mic_worker(self, epoch):
        stream = None
        try:
            with AUDIO_BACKEND.operation() as audio_lease:
                if not self._recording_epoch_active(epoch):
                    return False
                chosen = self._get_input_device(epoch=epoch, audio_lease=audio_lease)
                if not self._recording_epoch_active(epoch):
                    return False
                if chosen is None:
                    with self.lock:
                        if (not self.recording or epoch != self._rec_epoch or
                                getattr(self, "_stop_before_ready", False)):
                            return False
                        self.recording = False
                    self._cancel_recording_limit(epoch)
                    self._restore_playback_after_recording()
                    self._set_indicator(None)
                    self._log("no working microphone found")
                    self.notify("No microphone found",
                                "Check the selected microphone in Setup and "
                                "Settings > System > Sound > Input. If two inputs "
                                "have the same name, disconnect one; Presspeech "
                                "cannot choose a specific one. Enable microphone "
                                "access for desktop apps in Windows privacy settings "
                                "and try again. If per-app desktop controls are "
                                "available, check a verified Presspeech entry there.")
                    return False
                with self.lock:
                    if (not self.recording or epoch != self._rec_epoch or
                            getattr(self, "_stop_before_ready", False)):
                        return False
                    # Capture the rate before starting the stream: native
                    # backends may invoke the audio callback from start(), and
                    # Settings may invalidate the reusable cache at any time.
                    self._recording_input_device = chosen
                idx, rate = chosen
                stream = AUDIO_BACKEND.open_input_stream(
                    device=idx, samplerate=rate, channels=1, dtype="float32",
                    callback=lambda indata, frames, time_info, status: self._audio_cb(
                        indata, frames, time_info, status, epoch),
                )
                stream.start()
                with self.lock:
                    if (not self.recording or epoch != self._rec_epoch or
                            getattr(self, "_stop_before_ready", False)):
                        accepted = False
                    else:
                        self.stream = stream
                        accepted = True
                if not accepted:
                    try:
                        stream.stop()
                    except Exception:
                        pass
                    try:
                        stream.close()
                    except Exception:
                        pass
                    return False
        except Exception as exc:
            with self.lock:
                if (not self.recording or epoch != self._rec_epoch or
                        getattr(self, "_stop_before_ready", False)):
                    stale = True
                else:
                    stale = False
                    self.input_device = None
                    self._cached_input_selector = None
                    self._cached_input_topology = None
                    self._recording_input_device = None
                    self.recording = False
                    self.stream = None
            if stream is not None:
                try:
                    stream.stop()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass
            if stale:
                return False
            self._cancel_recording_limit(epoch)
            self._restore_playback_after_recording()
            self._set_indicator(None)
            self._log("mic error: %s" % type(exc).__name__)
            self.notify(
                "Microphone error",
                "Presspeech couldn't open the selected input. Check Settings > "
                "System > Sound > Input and Windows microphone privacy settings, "
                "including 'Let desktop apps access your microphone'. If your "
                "build has per-app desktop controls, check a verified "
                "Presspeech entry there. Choose Check "
                "Microphone in Setup or another "
                "input in Settings, then try again.")
            return False
        self._log("mic open ok: %s" % (chosen,))
        return True

    def _audio_cb(self, indata, frames, time_info, status, epoch):
        callback_started_at = time.perf_counter()
        chunk = indata.copy()
        # Post-roll treats a sequence advance as proof that new microphone
        # samples arrived after release. A zero-frame callback is not audio;
        # counting it could end capture on a quiet pre-release tail.
        if not chunk.size:
            return
        chunk_rms = float(np.sqrt(np.mean(np.square(chunk))))
        with self.lock:
            if (self.recording and epoch == self._rec_epoch and
                    not getattr(self, "_stop_before_ready", False)):
                self._first_audio_callback.set()
                # A callback can begin copying cue-era samples before the
                # gate opens and acquire this lock only afterwards.
                if (not self._capture_ready or
                        callback_started_at < self._capture_ready_at):
                    return
                self.buffer.append(chunk)
                self._peak_rms = max(self._peak_rms, chunk_rms)
                # With the stream's unspecified block size, input_overflow
                # means samples before this callback were discarded. Do not
                # treat a pre-readiness/start-cue overflow as lost dictation.
                if getattr(status, "input_overflow", False):
                    self._input_overflowed = True
                self._audio_sequence = getattr(self, "_audio_sequence", 0) + 1
                self._last_audio_callback_started_at = callback_started_at

    def request_stop(self):
        """Stop after silence, retaining the full safety window for ongoing speech."""
        released_at = time.perf_counter()
        with self.lock:
            if not self.recording:
                return
            epoch = self._rec_epoch
            # A quick tap can end while PortAudio is opening or the start cue
            # is playing. Do not let the later callback/cue turn that released
            # gesture into a new Listening session. Compare the timestamp too:
            # the startup worker may have published readiness while this
            # release was waiting for the lock.
            before_ready = (getattr(self, "_stop_before_ready", False) or
                            not self._capture_ready or
                            self._capture_ready_at > released_at)
            if before_ready:
                self._stop_before_ready = True
            else:
                release_audio_sequence = getattr(self, "_audio_sequence", 0)
        if before_ready:
            self.stop_recording(expected_epoch=epoch)
            return
        self._schedule_post_roll(
            POST_ROLL_MIN_SEC, epoch, released_at,
            release_audio_sequence)

    def cancel_recording(self, icon=None, item=None):
        """Discard the active capture without transcribing or changing clipboard."""
        with self.lock:
            if not self.recording:
                return False
            self.recording = False
            self._canceling_recording = True
            self._cancel_stop_cue_pending = getattr(self, "_capture_ready", True)
            self._capture_ready = False
            stream = self.stream
            self.stream = None
            self.buffer = []
            self._peak_rms = 0.0
            self._input_overflowed = False
            self._recording_paste_target = PasteTarget("", 0)
            self._recording_scratchpad = None
            self._recording_input_device = None
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
            if getattr(self, "_cancel_stop_cue_pending", True):
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

    def _schedule_post_roll(
            self, delay, epoch, released_at, release_audio_sequence):
        timer = threading.Timer(
            delay, self._finish_after_roll,
            (epoch, released_at, release_audio_sequence))
        timer.daemon = True
        timer.start()

    def _post_roll_tail(self):
        with self.lock:
            recording_device = self._recording_input_device
            rate = recording_device[1] if recording_device is not None else 16000
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
            audio_sequence = getattr(self, "_audio_sequence", 0)
            callback_started_at = getattr(
                self, "_last_audio_callback_started_at", 0.0)
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
        return tail_rms, threshold, audio_sequence, callback_started_at

    def _finish_after_roll(
            self, epoch, released_at, release_audio_sequence):
        if epoch != self._rec_epoch:
            return
        elapsed = time.perf_counter() - released_at
        tail_rms, threshold, audio_sequence, callback_started_at = (
            self._post_roll_tail())
        silent = tail_rms <= threshold
        reached_maximum = elapsed >= POST_ROLL_MAX_SEC
        # InputStream leaves blocksize unspecified, so PortAudio may choose a
        # host-dependent (and varying) callback size.  Elapsed wall time alone
        # cannot prove that a new boundary buffer reached _audio_cb. Sequence
        # advancement alone is insufficient because a callback may have begun
        # copying pre-release audio before request_stop acquired the lock.
        received_post_release_audio = (
            audio_sequence != release_audio_sequence
            and callback_started_at >= released_at)
        if reached_maximum or (received_post_release_audio and silent):
            reason = "maximum" if reached_maximum else "silence"
            self._log("post-roll %.3fs (%s; rms %.4f, threshold %.4f)" %
                      (elapsed, reason, tail_rms, threshold))
            self.stop_recording(expected_epoch=epoch)
            return
        remaining = POST_ROLL_MAX_SEC - elapsed
        self._schedule_post_roll(
            min(POST_ROLL_CHECK_SEC, max(0.0, remaining)), epoch, released_at,
            release_audio_sequence)

    def stop_recording(self, expected_epoch=None):
        with self.lock:
            if (not self.recording or
                    (expected_epoch is not None and
                     expected_epoch != self._rec_epoch)):
                return False
            self.recording = False
            capture_was_ready = (self._capture_ready and
                                 not getattr(self, "_stop_before_ready", False))
            self._capture_ready = False
            # A callback can arrive between an early release and this lock.
            # Its post-release samples must not become a dictation.
            audio = (np.concatenate(self.buffer)
                     if capture_was_ready and self.buffer else
                     np.zeros(0, dtype=np.float32))
            input_overflowed = getattr(self, "_input_overflowed", False)
            self._input_overflowed = False
            # Claim the delivery lifecycle before releasing the recording lock.
            # This closes the small window in which another hotkey press could
            # start capture while this method prepares and queues model work.
            self.transcribing = audio.size > 0
            paste_target = self._recording_paste_target
            scratchpad_target = getattr(self, "_recording_scratchpad", None)
            self._recording_scratchpad = None
            recording_device = self._recording_input_device
            self._recording_input_device = None
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
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        if self.icon is not None:
            self.icon.icon = self.idle_icon
        # Restore playback only after closing the stream, so returning speaker
        # audio and the stop cue are never captured in the post-roll.
        self._restore_playback_after_recording()
        if capture_was_ready:
            self._play_cue("stop")
        if audio.size == 0:
            if capture_was_ready:
                if input_overflowed:
                    self._finish_transcribing(AUDIO_INCOMPLETE_OUTCOME)
                    self._log("recording stopped; no audio after microphone input overflow")
                else:
                    self._show_no_speech_feedback()
                    self._log("recording stopped; no audio captured")
            else:
                self._show_not_ready_feedback()
                self._log("recording stopped before microphone was ready")
            self._schedule_model_idle_unload()
            return True
        try:
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            if recording_device is not None and recording_device[1] != 16000:
                audio = _resample_to_16k(audio, recording_device[1])
        except Exception as exc:
            # The stop path claimed transcribing before closing the stream.
            # A failed native resample must not leave every later hotkey press
            # stuck behind a transcription that was never queued.
            self._log("recording audio preparation failed: %s" %
                      type(exc).__name__)
            self._finish_transcribing(TRANSCRIPTION_START_FAILED_OUTCOME)
            self._schedule_model_idle_unload()
            return True
        if audio.size < MIN_TRANSCRIPTION_AUDIO_SAMPLES:
            if input_overflowed:
                self._finish_transcribing(AUDIO_INCOMPLETE_OUTCOME)
                self._log("recording stopped; too short after microphone input overflow")
            else:
                self._finish_transcribing(NO_SPEECH_OUTCOME)
                self._log("recording stopped; too short (%.2fs)" % (
                    audio.size / 16000.0))
            self._schedule_model_idle_unload()
            return True
        if input_overflowed:
            # A fixture with missing samples cannot establish model quality.
            # Leave an armed capture for the next intact dictation.
            self._log("benchmark capture skipped: microphone input overflow")
        else:
            self._capture_benchmark_if_armed(audio)
        self._set_indicator("transcribing")
        self._log("recording stopped; %.2fs captured, transcribing" % (audio.size / 16000.0))
        try:
            self._model_executor.submit(
                self._transcribe_worker, audio, paste_target, scratchpad_target,
                input_overflowed)
        except Exception as exc:
            # In particular, ThreadPoolExecutor rejects work after shutdown.
            # No worker will run its usual finally block to clear this state.
            self._log("transcription queue failed: %s" % type(exc).__name__)
            self._finish_transcribing(TRANSCRIPTION_START_FAILED_OUTCOME)
            return True
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
            from benchmark_capture import write_capture_wav
            os.makedirs(output_dir, exist_ok=True)
            # The benchmark must replay the same float32 samples sent to ASR.
            # PCM16 clips/quantizes quiet tails and can change the very model
            # behaviour that the recorded-tail probe is meant to measure.
            write_capture_wav(output_path, audio)
            self.settings["capture_next_benchmark"] = False
            if remaining > 0:
                self.settings["capture_benchmark_remaining"] = remaining - 1
                self.settings["capture_benchmark_index"] = index + 1
            cfg.save(self.settings)
            # The optional session name and user profile directory are part of
            # this path. Keep them out of the persistent diagnostic log.
            self._log("benchmark audio saved")
            left = max(0, remaining - 1) if remaining > 0 else 0
            # Session names may identify a speaker or workplace. Notifications
            # can remain visible after this local benchmark has ended.
            self.notify("Benchmark clip saved",
                        "Saved in the local benchmarks/audio folder "
                        "(%d remaining)." % left)
            return output_path
        except Exception as exc:
            self._log("benchmark capture failed: %s" % type(exc).__name__)
            # OS errors can name the private session or output path; tray
            # notifications are not a safe channel for their raw text.
            self.notify(
                "Benchmark capture failed",
                "The armed clip could not be saved. Check the local benchmark "
                "output directory before trying again.")
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
        """Return safe live inputs while preserving an unavailable saved choice."""
        options = [("Automatic (recommended)", AUTO_INPUT_DEVICE)]
        candidates = []
        selector_counts = {}
        try:
            with AUDIO_BACKEND.operation():
                devices = sd.query_devices()
                host_apis = sd.query_hostapis()
        except Exception as exc:
            self._log("could not list input devices: %s" % type(exc).__name__)
        else:
            for i, device in enumerate(devices):
                host_name = host_apis[device["hostapi"]]["name"]
                if not self._safe_input_device(device, host_name):
                    continue
                selector = self._device_selector(device, host_name)
                label = "%s — %s (device %d)" % (device["name"], host_name, i)
                candidates.append((label, selector))
                selector_counts[selector] = selector_counts.get(selector, 0) + 1
            # A PortAudio index is not a persistent device identity. Two inputs
            # with the same saved host/name selector cannot be offered as two
            # reliable explicit choices, even if their current indexes differ.
            options.extend(
                (label, selector) for label, selector in candidates
                if selector_counts[selector] == 1)
        configured = self.settings.get("input_device", AUTO_INPUT_DEVICE)
        if (configured != AUTO_INPUT_DEVICE and
                all(selector != configured for _label, selector in options)):
            # A disconnected explicit choice must remain selected in Setup and
            # Settings. Falling back visually to Automatic would make merely
            # opening and saving either window silently switch microphones.
            host_name, separator, device_name = configured.partition("::")
            if separator and host_name and device_name:
                reason = ("multiple indistinguishable devices"
                          if selector_counts.get(configured, 0) > 1 else
                          "currently unavailable")
                label = "%s — %s (%s)" % (device_name, host_name, reason)
            else:
                label = "Configured microphone (currently unavailable)"
            options.append((label, configured))
        return options

    @AUDIO_BACKEND.guarded
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
                self._log("configured input is unavailable (name omitted)")
                return None
            if len(ranked) != 1:
                self._log("configured input is ambiguous (name omitted)")
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
                    self._log("using %s input (name omitted) at %d Hz" %
                              (chosen_for, rate))
                    return (i, rate)
        return None

    def _cached_input_device_is_current(self, selected):
        """Verify that a cached PortAudio index still names an allowed input."""
        cached = self.input_device
        if (not isinstance(cached, tuple) or len(cached) != 2 or
                type(cached[0]) is not int or cached[0] < 0):
            return False
        index, rate = cached
        try:
            # Windows can reuse PortAudio indexes after unplug/reconnect or
            # resume. Opening a reused index can succeed while capturing a
            # different device, so an open failure is not a sufficient cache
            # invalidation signal. Re-enumeration is cheap; retain the full
            # open-and-level probe for cache misses only.
            devices = sd.query_devices()
            device = devices[index]
            host_apis = sd.query_hostapis()
            host_name = host_apis[device["hostapi"]]["name"]
            if not self._safe_input_device(device, host_name):
                return False
            if selected == AUTO_INPUT_DEVICE:
                # An old index can now name a different *usable* microphone.
                # Automatic has no configured selector to compare, so reuse
                # only while the whole enumerated table still matches the
                # table from discovery. Ambiguous identical labels are never
                # cached: PortAudio offers no identity to distinguish them.
                topology = self._automatic_input_topology(
                    index, devices, host_apis)
                if (topology is None or topology !=
                        getattr(self, "_cached_input_topology", None)):
                    return False
            else:
                if self._device_selector(device, host_name) != selected:
                    return False
                # Reusing the cached index is no safer than a fresh lookup if
                # another currently safe input acquired the same selector.
                matches = 0
                for candidate in devices:
                    candidate_host = host_apis[candidate["hostapi"]]["name"]
                    if (self._safe_input_device(candidate, candidate_host) and
                            self._device_selector(candidate, candidate_host)
                            == selected):
                        matches += 1
                if matches != 1:
                    return False
            sd.check_input_settings(
                device=index, samplerate=rate, channels=1, dtype="float32")
        except Exception:
            return False
        return True

    @staticmethod
    def _automatic_input_topology(index, devices=None, host_apis=None):
        """Snapshot PortAudio's index mapping, or decline ambiguous caching."""
        try:
            if devices is None:
                devices = sd.query_devices()
            if host_apis is None:
                host_apis = sd.query_hostapis()
            topology = tuple(
                (host_apis[device["hostapi"]]["name"], device["name"],
                 device["max_input_channels"])
                for device in devices
            )
            if (type(index) is not int or not 0 <= index < len(topology) or
                    topology[index][2] < 1 or
                    topology.count(topology[index]) != 1):
                return None
            return topology
        except Exception:
            # If enumeration or metadata cannot be trusted, pay for a fresh
            # probe on the next recording rather than reusing the old index.
            return None

    def _get_input_device(self, epoch=None, audio_lease=None):
        scope = (AUDIO_BACKEND.operation() if audio_lease is None
                 else nullcontext(audio_lease))
        with scope as lease:
            if epoch is not None and not self._recording_epoch_active(epoch):
                return None
            selected = self.settings.get("input_device", AUTO_INPUT_DEVICE)
            # A slow lookup for an earlier choice can finish after Settings
            # invalidates the cache. Tag cached indexes with their configured
            # selector (and Automatic's device table) so stale work is not
            # reused by a later recording.
            cached_for = getattr(self, "_cached_input_selector", selected)
            if (self.input_device is not None and cached_for == selected and
                    self._cached_input_device_is_current(selected)):
                return self.input_device
            try:
                chosen = self._find_input_device(selected)
            except Exception as exc:
                self._log("could not query audio devices: %s" %
                          type(exc).__name__)
                chosen = None
            if chosen is None and self._rescan_audio_devices(
                    epoch=epoch, audio_lease=lease):
                chosen = self._find_input_device(selected)
            topology = (
                self._automatic_input_topology(chosen[0])
                if selected == AUTO_INPUT_DEVICE and chosen is not None
                else None
            )
            # A slow probe can finish after release/re-press. It must not reset
            # the backend or overwrite the newer recording's selected input.
            if epoch is not None:
                with self.lock:
                    if not self.recording or epoch != self._rec_epoch:
                        return None
                    if self.settings.get(
                            "input_device", AUTO_INPUT_DEVICE) == selected:
                        self.input_device = chosen
                        self._cached_input_selector = (
                            selected if chosen is not None else None)
                        self._cached_input_topology = topology
            else:
                if self.settings.get(
                        "input_device", AUTO_INPUT_DEVICE) == selected:
                    self.input_device = chosen
                    self._cached_input_selector = (
                        selected if chosen is not None else None)
                    self._cached_input_topology = topology
            return chosen

    def _rescan_audio_devices(self, epoch=None, audio_lease=None):
        """Re-enumerate only while no other native operation can be affected."""
        def still_current():
            if getattr(self, "stream", None) is not None:
                return False
            if epoch is None:
                return not getattr(self, "recording", False)
            return self._recording_epoch_active(epoch)

        scope = (AUDIO_BACKEND.operation() if audio_lease is None
                 else nullcontext(audio_lease))
        try:
            with scope as lease:
                return AUDIO_BACKEND.rescan(lease, still_current)
        except Exception as exc:
            self._log("could not re-scan audio devices: %s" %
                      type(exc).__name__)
            return False

    def check_input_device(self, selected, on_listening=None):
        """Open an input and distinguish audible samples from silent buffers."""
        # The explicit Setup probe and dictation must never open inputs at the
        # same time. Claim this operation before taking the audio-backend lease;
        # a hotkey press during the probe is deferred rather than opening a
        # second microphone stream.
        with self.lock:
            if (getattr(self, "_starting_recording", False) or
                    getattr(self, "recording", False) or
                    getattr(self, "_canceling_recording", False) or
                    getattr(self, "transcribing", False) or
                    getattr(self, "_microphone_check_in_progress", False)):
                return MICROPHONE_CHECK_BUSY
            self._microphone_check_in_progress = True
        try:
            return self._check_input_device_claimed(
                selected, on_listening=on_listening)
        finally:
            with self.lock:
                self._microphone_check_in_progress = False

    def _check_input_device_claimed(self, selected, on_listening=None):
        """Probe a microphone after claiming the exclusive check lifecycle."""
        levels = []

        def probe(idx, rate):
            level = self._probe_input_level(
                idx, rate, listen_for=MICROPHONE_CHECK_LISTEN_SEC,
                on_listening=on_listening)
            if level is None:
                return False
            levels.append(level)
            return True

        initial_error = None
        try:
            # Keep one discovery lease across lookup, reset, and retry. A
            # reconnect can leave PortAudio's process-wide device table stale,
            # and Setup's Check Microphone must recover it just as recording does.
            with AUDIO_BACKEND.operation() as audio_lease:
                try:
                    chosen = self._find_input_device(selected, probe=probe)
                except Exception as exc:
                    # A stale backend can fail the enumeration itself rather
                    # than return an empty lookup. Give that case the same one
                    # safe refresh before exposing failure to Setup.
                    initial_error = exc
                    chosen = None
                if chosen is None:
                    # A reset may reorder device indexes. Discard the old tuple
                    # before requesting it. Holding the app lock makes a new
                    # recording either keep its active tuple or start after the
                    # cache is empty; never clear the sample rate underneath an
                    # active stream merely because its separate check failed.
                    with self.lock:
                        can_rescan = (not self.recording and self.stream is None)
                        if can_rescan:
                            self.input_device = None
                            self._cached_input_selector = None
                            self._cached_input_topology = None
                    if (can_rescan and self._rescan_audio_devices(
                            audio_lease=audio_lease)):
                        initial_error = None
                        chosen = self._find_input_device(selected, probe=probe)
            if chosen is None:
                if initial_error is not None:
                    self._log(
                        "microphone readiness check failed: %s" %
                        type(initial_error).__name__)
                return MICROPHONE_CHECK_UNAVAILABLE
            if levels and max(levels) >= MICROPHONE_CHECK_AUDIO_RMS:
                return MICROPHONE_CHECK_LEVEL
            return MICROPHONE_CHECK_SILENT
        except Exception as exc:
            self._log("microphone readiness check failed: %s" %
                      type(exc).__name__)
            return MICROPHONE_CHECK_UNAVAILABLE

    def _open_windows_settings(self, uri, manual_recovery=None):
        """Open one of Presspeech's fixed Windows Settings destinations."""
        try:
            os.startfile(uri)
            return True
        except (AttributeError, OSError) as exc:
            self._log("could not open Windows Settings: %s" % type(exc).__name__)
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
    def _probe_input_level(idx, rate, listen_for=0.0, on_listening=None):
        """Return peak RMS from a short in-memory probe, or None if it cannot open."""
        got = threading.Event()
        peak_rms = [0.0]
        stream = None

        def cb(indata, frames, t, status):
            chunk = np.asarray(indata)
            level = float(np.sqrt(np.mean(np.square(chunk)))) if chunk.size else 0.0
            peak_rms[0] = max(peak_rms[0], level)
            got.set()

        try:
            stream = AUDIO_BACKEND.open_input_stream(
                device=idx, samplerate=rate, channels=1, dtype="float32",
                callback=cb)
            stream.start()
            if not got.wait(0.8):
                return None
            # A successful open is not proof that capture has delivered a
            # buffer. Let Setup invite speech only after that first callback;
            # no audio or device detail is passed to the UI.
            if on_listening is not None:
                try:
                    on_listening()
                except Exception:
                    # A status notification must not turn a working input into
                    # a failed check (for example, if Setup was just closed).
                    pass
            # Keep the explicit check open for its full listening interval,
            # even if the first callback already has a meaningful level. An
            # early level may precede the user's chance to speak. Returning
            # immediately also lets the result overtake the queued Listening
            # status before Setup/Settings next polls it.
            if listen_for > 0:
                time.sleep(listen_for)
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
            scratchpad_target=None, input_overflowed=False):
        outcome = None
        try:
            outcome = self._transcribe_worker_inner(
                audio, paste_target, scratchpad_target, input_overflowed)
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
            if outcome in (NO_SPEECH_OUTCOME, NO_TEXT_OUTCOME,
                           NO_CONTENT_OUTCOME, AUDIO_INCOMPLETE_OUTCOME,
                           TRANSCRIPTION_START_FAILED_OUTCOME):
                self._set_temporary_indicator(
                    outcome, NO_SPEECH_FEEDBACK_SEC)
            else:
                self._set_indicator(None)
            self.transcribing = False
        if outcome == NO_SPEECH_OUTCOME:
            self._notify_no_speech()
        elif outcome == NO_TEXT_OUTCOME:
            self._notify_no_text()
        elif outcome == NO_CONTENT_OUTCOME:
            self._notify_no_content()
        elif outcome == AUDIO_INCOMPLETE_OUTCOME:
            self._notify_audio_capture_incomplete()
        elif outcome == TRANSCRIPTION_START_FAILED_OUTCOME:
            self.notify(
                "Dictation could not start",
                "Presspeech could not prepare this recording for local "
                "transcription. Nothing was pasted or copied. Try again; "
                "if this repeats, restart Presspeech and check the "
                "microphone in Setup or Settings.")

    def _notify_audio_capture_incomplete(self):
        self.notify(
            "Audio capture incomplete",
            "The microphone dropped audio during dictation, so Presspeech "
            "cannot tell whether words were missed. Nothing was pasted; "
            "check the microphone and try again.")

    def _transcribe_worker_inner(
            self, audio, paste_target=PasteTarget("", 0),
            scratchpad_target=None, input_overflowed=False):
        model_started = time.perf_counter()
        try:
            if not self.transcriber.loaded(self.settings["model"]):
                self.transcriber.load(self.settings["model"], notify=self.notify)
        except Exception:
            # A missing/corrupt local model or constructor failure is not
            # permission to fetch a different model. Keep loading errors
            # separate from the existing fallback for a failed GPU decode.
            # Third-party exception text/tracebacks can contain private input
            # or local environment details, so persistent logs stay generic.
            self._log("speech model load failed; error details suppressed")
            self.notify("Model load failed",
                        "The selected model could not load. Retry it in Settings "
                        "or choose another model before recording again.")
            return
        try:
            # Leave the language unspecified so multilingual Whisper can
            # detect each independent dictation. English-only Whisper resolves
            # this to English, and the Transformers backends ignore the hint.
            text = self.transcriber.transcribe(audio)
            model_seconds = time.perf_counter() - model_started
        except Exception:
            # Recognizer exceptions are an untrusted text boundary: an
            # upstream decoder may include a partial hypothesis in its error
            # or traceback.  Keep the persistent diagnostic content-free.
            self._log("transcription failed; recognizer error details suppressed")
            if not engine.is_parakeet(self.settings["model"]):
                self.notify(
                    "Transcription failed",
                    "The local speech model could not complete this dictation. "
                    "Try again, or choose another model in Settings.")
                return
            try:
                self.notify(
                    "Parakeet failed",
                    "Checking for an already-installed English-only Whisper "
                    "base.en fallback. No model will be downloaded.")
                # A decode failure is not consent to fetch another model.  In
                # particular, first-run Setup consented to Parakeet, not to
                # the English-only fallback used for this one recording.
                self.transcriber.load(
                    "base.en", notify=self.notify, local_only=True)
            except engine.model_cache.ModelCacheMissingError:
                self._log("cached fallback unavailable; error details suppressed")
                self.notify(
                    "Transcription failed",
                    "Parakeet could not complete this dictation. The English-only "
                    "fallback is not installed; no model was downloaded. Try "
                    "again, or choose another model in Settings.")
                return
            except Exception:
                self._log("fallback model load failed; error details suppressed")
                self.notify(
                    "Transcription failed",
                    "Neither local speech model could complete this dictation. "
                    "Try again, or choose another model in Settings.")
                return
            try:
                model_started = time.perf_counter()
                text = self.transcriber.transcribe(audio)
                model_seconds = time.perf_counter() - model_started
            except Exception:
                self._log(
                    "fallback transcription failed; recognizer error details suppressed")
                self.notify(
                    "Transcription failed",
                    "Neither local speech model could complete this dictation. "
                    "Try again, or choose another model in Settings.")
                return
        timing = getattr(self.transcriber, "last_timing", {})
        timing_summary = _model_timing_summary(timing)
        if not text or not text.strip():
            self._log("transcription returned empty (model %.3fs)" % model_seconds)
            if timing_summary is not None:
                self._log(timing_summary)
            if input_overflowed:
                self._log("microphone input overflow; transcription returned no text")
                return AUDIO_INCOMPLETE_OUTCOME
            # Only Silero's explicit zero-speech result supports a no-speech
            # diagnosis. Parakeet (and a Whisper decoder after retained audio)
            # can return blank text despite audible speech; do not blame the
            # microphone or silently treat that as a confirmed VAD rejection.
            if engine.whisper_vad_rejected(timing):
                return NO_SPEECH_OUTCOME
            return NO_TEXT_OUTCOME
        text = self._apply_text(text)
        if not text:
            # Filler removal or a deliberate empty dictionary replacement can
            # erase a recognized phrase. Never paste just the configured suffix
            # over a selection or replace it with an empty clipboard item.
            self._log("transcription removed by text settings; no delivery attempted")
            if timing_summary is not None:
                self._log(timing_summary)
            if input_overflowed:
                self._log("microphone input overflow; text settings removed output")
                return AUDIO_INCOMPLETE_OUTCOME
            return NO_CONTENT_OUTCOME
        # Dictation is private: retain performance data without persisting the
        # user's words in the diagnostic log. Whisper's detected-speech duration
        # distinguishes a VAD rejection from an empty decoder result.
        self._log("transcription complete: %d chars (model %.3fs)" %
                  (len(text), model_seconds))
        if timing_summary is not None:
            self._log(timing_summary)
        if input_overflowed:
            # Recognition can be internally consistent despite a missing
            # microphone buffer. Preserve the text for review, but never
            # silently paste a potentially incomplete transcript.
            self._remember_undelivered_dictation(text, "audio-overflow")
            return
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
            # This is an in-process private sink, not a Ctrl+V into whichever
            # child control has focus. Changing controls inside the same
            # scratchpad must not divert its transcript to the clipboard.
            if _paste_target_same_window(
                    paste_target, _foreground_paste_target()):
                try:
                    scratchpad_target.append_text(text)
                except Exception:
                    # A failed in-process sink is still a delivery failure;
                    # never expose Tk error details or paste elsewhere.
                    self._remember_undelivered_dictation(
                        text, "scratchpad-unavailable")
            else:
                # Preserve the same focus-change behavior as normal app
                # delivery. _paste copies the transcript, rechecks the target,
                # and refuses to inject Ctrl+V into the newly focused window.
                self._paste(text, paste_target)
            return
        # Try Dictation is a private sink. Its window can close while the model
        # is working; retain the completed text for explicit recovery rather
        # than paste into an unrelated app or silently lose the dictation.
        self._remember_undelivered_dictation(text, "scratchpad-unavailable")

    def _apply_text(self, text):
        text, removed_by_rule, inserted_by_rule = _apply_dictionary_rules(
            text, self.settings["dictionary"], return_effects=True)
        if self.settings["remove_fillers"]:
            text = _remove_fillers(text)
        if self.settings.get("british"):
            text = to_british(text)
        if (not text or
                (removed_by_rule and not inserted_by_rule and
                 all(char.isspace() or unicodedata.category(char).startswith("P")
                     for char in text))):
            # An empty replacement can leave the recognizer's trailing period
            # behind. A punctuation-only remnant is not worth replacing the
            # user's selected text with; an explicit punctuation replacement
            # remains deliverable because inserted_by_rule is true.
            return ""
        text += cfg.SUFFIXES.get(self.settings["suffix"], " ")
        return text

    def has_undelivered_dictation(self):
        """Return whether uncertain delivery left private text in memory."""
        lock = getattr(self, "_undelivered_lock", None)
        if lock is None:
            return bool(getattr(self, "_undelivered_dictations", ()))
        with lock:
            return bool(self._undelivered_dictations)

    def _notify_undelivered_dictation(self):
        self.notify(
            "Dictation waiting for review",
            "Presspeech is keeping a recovery copy in process memory. "
            "Check the intended field first, then use the Delivery Recovery "
            "window or the notification-area Copy and Discard commands before "
            "recording again. Exiting discards this text.")

    def _remember_undelivered_dictation(self, text, reason):
        """Retain private text, never exception details or transcript contents."""
        lock = getattr(self, "_undelivered_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._undelivered_lock = lock
            self._undelivered_dictations = []
        with lock:
            if getattr(self, "_exiting", False):
                return
            self._undelivered_dictations.append(text)
        # Callers supply fixed status codes, never exception messages.
        self._log("dictation retained in memory; delivery status: %s" % reason)
        prefix = {
            "clipboard-unavailable": (
                "The clipboard write could not be verified; the previous "
                "clipboard item may have changed. "),
            "clipboard-changed": "The clipboard changed; no paste shortcut was sent. ",
            "target-unavailable": "The original input window could not be identified. ",
            "route-unavailable": (
                "The original app's paste method could not be determined; "
                "no paste shortcut was sent. "),
            "focus-changed": (
                "The original window or focused control could not be "
                "verified, or the window title changed; no paste shortcut "
                "was sent. "),
            "input-integrity-boundary": (
                "The original app's input privilege boundary blocks automatic "
                "paste or could not be verified; no paste shortcut was sent. "),
            "scratchpad-unavailable": (
                "Try Dictation could not confirm that the transcript reached "
                "its private editor; no paste shortcut was sent. "),
            "audio-overflow": (
                "The microphone dropped audio during dictation, so this "
                "transcript may be incomplete; no paste shortcut was sent. "),
            "terminal-newline": (
                "The transcript contains a line break and the original "
                "window appears to be a command terminal. Pasting could run "
                "a command; no paste shortcut was sent. "),
            "modifier-held": (
                "A Ctrl, Shift, Alt, Windows, or V key was held; no paste "
                "shortcut was sent. Release it before a manual paste. "),
            "modifier-state-unavailable": (
                "Keyboard modifier state could not be checked; no paste "
                "shortcut was sent. "),
            "delivery-check-unavailable": (
                "The final delivery check could not be completed; no paste "
                "shortcut was sent. "),
            "shortcut-uncertain": (
                "The paste shortcut may have run fully or partly; delivery "
                "could not be verified. "),
            "shortcut-rejected": (
                "Windows accepted no Presspeech paste key events; automatic "
                "paste did not run. The clipboard may have changed. "),
            "shortcut-unavailable": (
                "Presspeech could not prepare the paste shortcut; no paste "
                "key events were sent. The clipboard may have changed. "),
            "shortcut-focus-uncertain": (
                "The original focused field or window title could not be "
                "verified after the paste shortcut was sent. Text may have "
                "reached the original field or a different field. "),
        }[reason]
        review_instruction = (
            "Delivery Recovery does not show the words. Choose Copy for Manual "
            "Paste into a private editor to inspect them, or Discard and "
            "dictate again. "
            if reason == "audio-overflow" else
            "Review the words in a non-executing editor before any manual "
            "paste into a command terminal. "
            if reason == "terminal-newline" else
            "Check the intended field and any field that may have gained "
            "focus, then check the current clipboard before choosing Copy or "
            "Discard. "
            if reason in ("shortcut-uncertain", "shortcut-focus-uncertain") else
            "Check the intended field before trying again. ")
        self.notify("Dictation needs review", prefix +
                    review_instruction + "A recovery copy "
                    "is kept in process memory: use the Delivery Recovery window "
                    "or the notification-area Copy and Discard commands before "
                    "recording again. "
                    "Exiting discards it.")
        self.open_delivery_recovery()

    def _clear_undelivered_dictations(self):
        lock = getattr(self, "_undelivered_lock", None)
        if lock is not None:
            with lock:
                self._undelivered_dictations.clear()

    def discard_undelivered_dictation(self, icon=None, item=None):
        """Explicitly discard the oldest retained dictation without copying it."""
        lock = getattr(self, "_undelivered_lock", None)
        if lock is None:
            self.notify(
                "No undelivered dictation", "There is nothing waiting to discard.")
            return False
        with lock:
            if not self._undelivered_dictations or getattr(self, "_exiting", False):
                self.notify(
                    "No undelivered dictation", "There is nothing waiting to discard.")
                return False
            del self._undelivered_dictations[0]
            remaining = bool(self._undelivered_dictations)
        message = (
            "Another dictation is waiting for Copy or Discard."
            if remaining else "You can record again."
        )
        self.notify("Retained dictation discarded", message)
        return True

    def copy_undelivered_dictation(self, icon=None, item=None):
        """Explicitly copy the oldest retained transcript; never simulate input."""
        lock = getattr(self, "_undelivered_lock", None)
        if lock is None:
            self.notify("No undelivered dictation", "There is nothing waiting to copy.")
            return False
        with lock:
            if not self._undelivered_dictations or getattr(self, "_exiting", False):
                self.notify("No undelivered dictation", "There is nothing waiting to copy.")
                return False
            try:
                receipt = clipboard_delivery.write_text(self._undelivered_dictations[0])
            except Exception:
                self._log("clipboard recovery unavailable; dictation retained")
                self.notify("Clipboard unavailable", "The dictation is still kept in memory. "
                            "Check the current clipboard, then try Copy "
                            "Undelivered Dictation again or choose Discard.")
                return False
            if not clipboard_delivery.is_current(receipt):
                self._log("clipboard changed during recovery; dictation retained")
                self.notify("Clipboard changed", "The dictation is still kept in memory. "
                            "Check the clipboard before choosing Copy or Discard.")
                return False
            del self._undelivered_dictations[0]
            remaining = bool(self._undelivered_dictations)
        message = "Dictation copied. Check the intended field before pasting manually."
        if remaining:
            message += " Another dictation is waiting for Copy or Discard."
        self.notify("Dictation copied", message)
        return True

    def _paste_keys_held_in_hook(self):
        """Catch reserved hotkeys that may not show as asynchronously down."""
        transaction_lock = getattr(self, "_hotkey_transaction_lock", None)
        with transaction_lock if transaction_lock is not None else nullcontext():
            pressed = getattr(self, "_filter_pressed_vks", ())
            return any(key in pressed for key in keyboard_delivery._MODIFIER_KEYS)

    def _paste(self, text, paste_target=PasteTarget("", 0)):
        # Preserve the old clipboard if the hook already knows paste is unsafe.
        if self._paste_keys_held_in_hook():
            self._remember_undelivered_dictation(text, "modifier-held")
            return False
        if not isinstance(paste_target, PasteTarget):
            paste_target = PasteTarget(str(paste_target or ""), 0)
        # Known-invalid destinations must not replace the user's clipboard.
        # The later checks still catch focus, integrity and clipboard changes
        # that occur after this point-in-time preflight.
        if not paste_target.window_handle:
            self._remember_undelivered_dictation(text, "target-unavailable")
            return False
        if not self._paste_target_still_focused(paste_target):
            self._remember_undelivered_dictation(text, "focus-changed")
            return False
        if _paste_target_blocks_simulated_input(paste_target):
            self._remember_undelivered_dictation(text, "input-integrity-boundary")
            return False
        route = _paste_route(paste_target.process_name)
        if route is None:
            # The image query can fail despite readable HWND, PID and integrity.
            # Do not replace the clipboard or guess the local paste route.
            self._remember_undelivered_dictation(text, "route-unavailable")
            return False
        # A newline is executable input in many shells. The default space
        # suffix is no guarantee: the recognizer, dictionary, or a selected
        # newline suffix can all put a line break in the final transcript.
        # Defer before replacing the user's clipboard, not just before input.
        if _requires_terminal_review(text, paste_target.process_name):
            self._remember_undelivered_dictation(text, "terminal-newline")
            return False
        # The hook may not have observed a key already held before delivery.
        # Check the physical state before replacing the user's clipboard;
        # Controller.shortcut checks again immediately before SendInput.
        try:
            if keyboard_delivery.paste_keys_held():
                self._remember_undelivered_dictation(text, "modifier-held")
                return False
        except keyboard_delivery.ModifierStateError:
            self._remember_undelivered_dictation(text, "modifier-state-unavailable")
            return False
        try:
            receipt = clipboard_delivery.write_text(text)
        except Exception:
            self._remember_undelivered_dictation(text, "clipboard-unavailable")
            return False
        if not clipboard_delivery.is_current(receipt):
            self._remember_undelivered_dictation(text, "clipboard-changed")
            return False
        time.sleep(RDP_PASTE_DELAY_SEC if route == "rdp" else PASTE_DELAY_SEC)
        if not clipboard_delivery.is_current(receipt):
            self._remember_undelivered_dictation(text, "clipboard-changed")
            return False
        if not self._paste_target_still_focused(paste_target):
            self._remember_undelivered_dictation(text, "focus-changed")
            return False
        if _paste_target_blocks_simulated_input(paste_target):
            self._remember_undelivered_dictation(text, "input-integrity-boundary")
            return False
        # Keep these checks adjacent to the single input submission. The
        # earlier checks protect the delay and integrity lookup, which can both
        # outlive the originally focused target or clipboard value.
        if not self._paste_target_still_focused(paste_target):
            self._remember_undelivered_dictation(text, "focus-changed")
            return False
        if not clipboard_delivery.is_current(receipt):
            self._remember_undelivered_dictation(text, "clipboard-changed")
            return False
        if self._paste_keys_held_in_hook():
            self._remember_undelivered_dictation(text, "modifier-held")
            return False
        modifiers = [keyboard_delivery.VK_LCONTROL]
        if route == "moonlight":
            modifiers.extend((
                keyboard_delivery.VK_LMENU,
                keyboard_delivery.VK_LSHIFT,
            ))
        try:
            keyboard = keyboard_delivery.Controller()
        except Exception:
            # Controller construction cannot have submitted any input. Do not
            # tell the user the paste may already have reached a field.
            self._remember_undelivered_dictation(text, "shortcut-unavailable")
            return False
        failure = None
        cleanup_required = True

        def before_submit():
            # Controller setup and the physical-key scan can outlast the
            # earlier checks. Fail closed at the last pre-SendInput boundary.
            if not self._paste_target_still_focused(paste_target):
                return "focus-changed"
            if self._paste_keys_held_in_hook():
                return "modifier-held"
            if not clipboard_delivery.is_current(receipt):
                return "clipboard-changed"
            return True

        try:
            self._injecting_keys = True
            # Sequence equality and focused-window identity are last-point
            # guards, not acknowledgement that the target consumed the text.
            # Submit the complete chord in one SendInput call so physical or
            # separately injected input cannot interleave its chord events.
            keyboard.shortcut(
                modifiers, keyboard_delivery.VK_V,
                before_submit=before_submit)
        except keyboard_delivery.PreSubmitCheckError as exc:
            failure = exc.reason
        except keyboard_delivery.ModifierHeldError:
            failure = "modifier-held"
        except keyboard_delivery.ModifierStateError:
            failure = "modifier-state-unavailable"
        except keyboard_delivery.KeyboardDeliveryError as exc:
            failure = ("shortcut-rejected" if exc.accepted_count == 0
                       else "shortcut-uncertain")
            cleanup_required = exc.cleanup_required
        except Exception:
            failure = "shortcut-uncertain"
        finally:
            if (failure == "shortcut-uncertain" and keyboard is not None
                    and cleanup_required):
                # Zero accepted events or failure before submission need no
                # injected key-ups. For nonzero partial/unknown results, do
                # not assume which keys remain down: release all candidates.
                for key in (keyboard_delivery.VK_V, *reversed(modifiers)):
                    try:
                        keyboard.release(key)
                    except Exception:
                        try:
                            keyboard.release(key)
                        except Exception:
                            pass
            time.sleep(0.02)
            self._injecting_keys = False
        if failure:
            self._remember_undelivered_dictation(text, failure)
            return False
        # SendInput accepts events, not a paste-consumed acknowledgement. If
        # a newer clipboard owner appeared during submission or its brief
        # cleanup, do not silently report success. The shortcut might already
        # have reached the target; keep a recovery copy without overwriting
        # the newer clipboard item or trying another insertion strategy.
        if not clipboard_delivery.is_current(receipt):
            self._remember_undelivered_dictation(text, "shortcut-uncertain")
            return False
        # SendInput reports acceptance into the input stream, not which field
        # eventually consumed the paste shortcut. A focus change during
        # submission or its brief cleanup makes delivery uncertain despite an
        # unchanged clipboard receipt. Retain the text rather than claiming a
        # successful insertion; this cannot undo a paste that already landed.
        if not self._paste_target_still_focused(
                paste_target, after_shortcut=True):
            self._remember_undelivered_dictation(
                text, "shortcut-focus-uncertain")
            return False
        return True

    def _paste_target_still_focused(
            self, paste_target, *, after_shortcut=False):
        current_target = _foreground_paste_target()
        if _paste_target_matches(paste_target, current_target):
            return True
        # Executable basenames can contain user or workplace names. Window
        # identity is enough to decide delivery; logs need only the outcome.
        self._log(
            "paste outcome uncertain; original target could not be verified"
            if after_shortcut else
            "paste skipped; original target could not be verified")
        return False

    # ---------------- windows ----------------

    def _report_delivery_recovery_window_failure(self, exc, window=None):
        """Keep tray recovery usable when the richer window cannot be built."""
        if (window is None or
                getattr(self, "delivery_recovery_window", None) is window):
            self.delivery_recovery_window = None
        self._log(
            "delivery recovery window unavailable: %s" % type(exc).__name__)
        self.notify(
            "Delivery Recovery unavailable",
            "Use Copy Undelivered Dictation (replaces clipboard) or "
            "Discard Undelivered Dictation in the notification-area menu.")

    def open_delivery_recovery(self, icon=None, item=None):
        """Show explicit recovery controls without copying private text."""
        if not self.has_undelivered_dictation():
            self.notify(
                "No undelivered dictation",
                "There is nothing waiting to copy or discard.")
            return False
        lock = getattr(self, "_delivery_recovery_window_lock", None)
        if lock is None:
            # Lightweight test embedders may construct the app without running
            # __init__. The production instance always owns this lock.
            lock = threading.Lock()
            self._delivery_recovery_window_lock = lock
        try:
            with lock:
                window = getattr(self, "delivery_recovery_window", None)
                if window is None:
                    window = ui.DeliveryRecoveryWindow(self)
                    if getattr(window, "_build_failed", False) is True:
                        return False
                    # Construction registers before queueing the UI build.
                    # Closing or failing on the UI thread clears that exact
                    # object. Never re-adopt it after the constructor returns.
                    if getattr(self, "delivery_recovery_window", None) is not window:
                        return False
                else:
                    ui.present_window(window)
        except Exception as exc:
            self._report_delivery_recovery_window_failure(exc)
            return False
        return True

    def open_scratchpad(self, icon=None, item=None):
        with self._window_open_lock:
            if self.scratchpad is None:
                ui.ScratchpadWindow(self)
            else:
                ui.present_window(self.scratchpad)

    def open_settings(self, icon=None, item=None):
        with self._window_open_lock:
            if self.settings_window is None:
                ui.SettingsWindow(self)
            else:
                ui.present_window(self.settings_window)

    def open_setup(self, icon=None, item=None):
        with self._window_open_lock:
            if self.setup_window is None:
                ui.SetupWindow(self)
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
            if not manual:
                # A failed request still exposes a connection to GitHub. Save
                # the attempt before sending it so restarts cannot turn an
                # offline or rate-limited check into repeated background calls.
                # If persistence fails, do not make an unthrottled automatic
                # request; the manual command remains available for retries.
                self.settings["last_update_check_epoch"] = int(time.time())
                cfg.save(self.settings)
            update = updates.fetch_update(cfg.VERSION)
            if manual:
                self.settings["last_update_check_epoch"] = int(time.time())
                cfg.save(self.settings)
            if update is None:
                if manual:
                    self.notify("Presspeech", "Version %s is up to date." % cfg.VERSION)
                return
            self.pending_update = update
            if self.update_window is None:
                ui.UpdateWindow(self, update)
        except Exception as exc:
            self._log("update check failed: %s" % type(exc).__name__)
            if manual:
                self.notify(
                    "Update check failed",
                    updates.user_facing_error(
                        exc, "Could not check for updates. Please try again."))
        finally:
            self._update_lock.release()

    def launch_update(self, installer_path, update):
        """Revalidate and run only when exiting cannot discard a dictation."""
        with self.lock:
            if getattr(self, "_update_installing", False):
                raise updates.UpdateInstallBusy(
                    "An update installer is already starting.")
            if (getattr(self, "_starting_recording", False) or
                    getattr(self, "recording", False) or
                    getattr(self, "_canceling_recording", False) or
                    getattr(self, "transcribing", False)):
                raise updates.UpdateInstallBusy(
                    "Finish or cancel the current dictation before installing.")
            if self.has_undelivered_dictation():
                raise updates.UpdateInstallBusy(
                    "Copy or discard the waiting dictation in Delivery Recovery "
                    "before installing. Exiting now would discard it.")
            # Exclude a new capture during verification, process launch, and
            # the brief handoff to exit_app(). A failed launch releases this
            # reservation so the user can continue dictating.
            self._update_installing = True
        try:
            with updates.locked_verified_installer(update, installer_path):
                subprocess.Popen([installer_path], cwd=os.path.dirname(installer_path))
            try:
                updates.schedule_installer_cleanup(installer_path)
            except Exception as exc:
                # The installer is already running. Cleanup failure must not turn a
                # successful, explicitly approved update into a second launch.
                self._log("could not schedule update installer cleanup: %s" %
                          type(exc).__name__)
            time.sleep(0.15)
            self.exit_app()
        finally:
            with self.lock:
                self._update_installing = False

    def diagnostics_text(self):
        """Return support state without user text, device labels, or raw errors."""
        transcriber = self.transcriber
        model = getattr(transcriber, "model", None)
        dtype = str(getattr(model, "dtype", "not loaded"))
        device = str(getattr(transcriber, "_device", "not loaded"))
        active_input = (
            getattr(self, "_recording_input_device", None) or
            self.input_device)
        microphone_lines = _diagnostic_microphone_lines(
            self.settings.get("input_device", AUTO_INPUT_DEVICE), active_input)
        lines = [
            "Presspeech diagnostics",
            "Version: %s" % cfg.VERSION,
            "Build: %s" % ("packaged" if getattr(sys, "frozen", False) else "source"),
            "Windows: %s" % platform.platform(),
            "Configured model: %s" % self.settings.get("model", "unknown"),
            "Model status: %s" % getattr(self, "model_status", "unknown"),
            "Backend: %s" % (getattr(transcriber, "backend", None) or "not loaded"),
            "Device / dtype: %s / %s" % (device, dtype),
            *microphone_lines,
            "Hotkey / trigger: %s / %s" % (
                self.settings.get("hotkey", "unknown"),
                self.settings.get("trigger", "unknown")),
            "Global hotkey status: %s" %
            self.hotkey_listener_status()[0],
            "Maximum recording length: %d seconds" %
            cfg.recording_length_seconds(
                self.settings.get("max_recording_seconds")),
            "Windows UI Automation: %s" % ui.accessibility_status(),
            "Window action failures: %d" % ui.callback_failure_count(),
            "Undelivered dictation waiting: %s" %
            self.has_undelivered_dictation(),
            "Automatic update checks: %s" % bool(
                self.settings.get("check_updates", True)),
            "Dictionary rule count: %d" % len(self.settings.get("dictionary", [])),
            r"Config path: %APPDATA%\Presspeech\config.json",
            r"Log path: %APPDATA%\Presspeech\log.txt",
            "Privacy: no transcript, audio, dictionary contents, exact microphone "
            "names, raw error details, or raw log lines included",
        ]
        return "\r\n".join(lines)

    def copy_diagnostics(self, icon=None, item=None):
        try:
            # Diagnostics can include system and runtime details. Use the same
            # history/cloud-excluded transaction as transcript copies while
            # keeping deliberate local Ctrl+V available.
            receipt = clipboard_delivery.write_text(self.diagnostics_text())
            # A successful write can be superseded before the tray callback
            # reports success. Do not tell the user diagnostics are available
            # for paste unless the same clipboard generation is still current.
            if not clipboard_delivery.is_current(receipt):
                self._log("diagnostics clipboard ownership unconfirmed")
                self.notify(
                    "Clipboard not confirmed",
                    "Diagnostics could not be confirmed on the clipboard. "
                    "Check its contents before choosing Copy Diagnostics again.")
                return False
        except Exception as exc:
            # Another Windows process can temporarily hold the clipboard.
            # A tray callback must not disappear without telling the user
            # whether the support report could be confirmed on the clipboard.
            self._log("could not copy diagnostics: %s" % type(exc).__name__)
            self.notify(
                "Clipboard unavailable",
                "Diagnostics could not be confirmed on the clipboard. "
                "Check its contents; if needed, close any app using the "
                "clipboard and choose Copy Diagnostics again.")
            return False
        self.notify("Presspeech", "Privacy-safe diagnostics copied to the clipboard.")
        return True

    def _open_support_page(self, url):
        """Open one fixed public support page without adding app or user data."""
        try:
            os.startfile(url)
        except (OSError, AttributeError) as exc:
            self._log("could not open support page: %s" % type(exc).__name__)
            self.notify(
                "Could not open browser",
                "Open rcourtman.github.io/presspeech or "
                "github.com/rcourtman/presspeech in your browser.")
            return False
        return True

    def test_app_compatibility(self, icon=None, item=None):
        return self._open_support_page(APP_COMPATIBILITY_GUIDE_URL)

    def report_problem(self, icon=None, item=None):
        return self._open_support_page(SUPPORT_GUIDE_URL)

    def suggest_improvement(self, icon=None, item=None):
        return self._open_support_page(SUPPORT_GUIDE_URL)

    # ---------------- helpers ----------------

    def apply_autostart(self):
        """Apply the requested per-user startup registration, reporting failure."""
        enable = bool(self.settings["autostart"])
        try:
            import winreg
            run_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
            if enable:
                # A clean profile need not have a per-user Run key yet.
                # Preserve the existing-key path and create only when the
                # user opts in and that key is actually absent.
                try:
                    key = winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER, run_key, 0,
                        winreg.KEY_SET_VALUE,
                    )
                except FileNotFoundError:
                    key = winreg.CreateKeyEx(
                        winreg.HKEY_CURRENT_USER, run_key, 0,
                        winreg.KEY_SET_VALUE,
                    )
                with key as registry_key:
                    winreg.SetValueEx(
                        registry_key, "Presspeech", 0, winreg.REG_SZ,
                        _autostart_command(
                            sys.executable, __file__,
                            frozen=bool(getattr(sys, "frozen", False))))
            else:
                # With the default opt-out, an absent key or value already
                # means success. Do not create a registry key merely to
                # complete Setup, but still report permission errors.
                try:
                    key = winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER, run_key, 0,
                        winreg.KEY_SET_VALUE,
                    )
                except FileNotFoundError:
                    return True
                with key as registry_key:
                    try:
                        winreg.DeleteValue(registry_key, "Presspeech")
                    except FileNotFoundError:
                        pass
        except Exception as exc:
            self._log("autostart error: %s" % type(exc).__name__)
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

    def _notify_no_text(self):
        self.notify(
            "No text recognized",
            "The local recognizer returned no text. Try again. If this keeps "
            "happening, check the microphone in Setup or try another model.")

    def _notify_no_content(self):
        self.notify(
            "Nothing to insert",
            "Text settings removed the recognized words. Presspeech did not "
            "change the clipboard or attempt a paste. Review filler removal "
            "and dictionary rules in Settings if this was unexpected.")

    def _show_no_speech_feedback(self):
        self._set_temporary_indicator("no_speech", NO_SPEECH_FEEDBACK_SEC)
        self._notify_no_speech()

    def _show_not_ready_feedback(self):
        self._set_temporary_indicator("not_ready", NOT_READY_FEEDBACK_SEC)
        self.notify(
            "Microphone was not ready",
            "Try again and wait for the start cue or Listening status before "
            "speaking. If this keeps happening, check the microphone in Setup.")

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
                "NVIDIA CUDA is unavailable; using English-only Whisper "
                "base.en on CPU. "
                "You can choose another model in Settings.")
        with model_state_lock:
            if (request_generation == self._model_load_generation and
                    self.settings["model"] == model_name):
                self.model_status = "loading"
                self.model_status_detail = "Checking local model files…"
                self.model_download_progress = None

        def report_model_progress(phase, done=None, total=None):
            with model_state_lock:
                if (request_generation != self._model_load_generation or
                        self.settings.get("model") != model_name):
                    return
                if phase == "downloading":
                    self.model_status_detail = "Downloading model files…"
                    if done is not None:
                        self.model_download_progress = (done, total)
                elif phase == "verifying":
                    self.model_status_detail = "Verifying model files…"
                    self.model_download_progress = None
                elif phase == "loading":
                    self.model_status_detail = "Loading speech model…"
                    self.model_download_progress = None

        self._set_indicator("loading")
        self._log("loading speech model: %s" % model_name)
        try:
            consent_required = (
                not (getattr(self, "_initial_model_download_consented", False) and
                     getattr(self, "_initial_model_download_consent_model", None)
                     == model_name) and
                _needs_first_run_download_choice(self.settings, model_name))
            if consent_required:
                try:
                    # The loader itself remains local-only until Setup gets an
                    # explicit choice. This avoids a cache-check/download race.
                    self.transcriber.load(
                        model_name, notify=self.notify, local_only=True,
                        progress_callback=report_model_progress)
                except engine.model_cache.ModelCacheMissingError:
                    with model_state_lock:
                        if (request_generation != self._model_load_generation or
                                self.settings.get("model") != model_name):
                            return
                        self.model_status = "awaiting_download_consent"
                        self.model_download_progress = None
                        self.model_status_detail = _first_run_download_detail(
                            model_name)
                        self._model_load_target = None
                    self._set_indicator(None)
                    self._log(
                        "first-run model download deferred pending user choice")
                    return
            else:
                self.transcriber.load(
                    model_name, notify=self.notify,
                    progress_callback=report_model_progress)
            with model_state_lock:
                if (request_generation == self._model_load_generation and
                        self.settings.get("model") == model_name):
                    self.model_status_detail = "Warming speech model…"
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
            integrity_failure = isinstance(
                exc, engine.model_cache.ModelCacheIntegrityError)
            with model_state_lock:
                if (request_generation == self._model_load_generation and
                        self.settings["model"] == model_name):
                    self.model_status = "error"
                    self.model_download_progress = None
                    self.model_status_detail = (
                        "Model integrity check failed; do not use these files"
                        if integrity_failure else
                        "Model load failed; use Retry Speech Model")
                if (request_generation == self._model_load_generation and
                        getattr(self, "_model_load_target", None) == model_name):
                    self._model_load_target = None
            self._log("speech model load failed; error details suppressed")
            if (request_generation == self._model_load_generation and
                    self.settings["model"] == model_name):
                if integrity_failure:
                    self.notify(
                        "Model integrity check failed",
                        "Presspeech refused model files that did not match its "
                        "pinned SHA-256 manifest. Do not use them. Review your "
                        "network and proxy trust before clearing this model's "
                        "cache and retrying.")
                else:
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
            self.model_download_progress = None
            if cpu_first_run:
                self.model_status_detail = (
                    "English-only Whisper base.en on CPU "
                    "(NVIDIA CUDA unavailable)")
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
            self._log("idle model wake failed: %s" % type(exc).__name__)
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
    # Validate the values cached by the real bundled libraries, not only the
    # environment from which they were imported.
    model_network.harden_loaded_runtime(require_loaded=True)


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
