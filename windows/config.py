"""Settings persistence for Presspeech for Windows."""

import json
import os
import threading

APP_NAME = "Presspeech"
VERSION = "0.1.10"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

DEFAULTS = {
    "hotkey": "right alt",
    "trigger": "hold",
    "input_device": "auto",
    "model": "parakeet-tdt-0.6b-v3",
    "precision": "fp16",
    "suffix": "space",
    "remove_fillers": True,
    "british": True,
    "audio_cues": True,
    "mute_playback_while_recording": True,
    "visual_indicator": True,
    "dictionary": [],
    "autostart": True,
    "setup_complete": False,
    "check_updates": True,
    "last_update_check_epoch": 0,
    "capture_next_benchmark": False,
    "capture_benchmark_remaining": 0,
    "capture_benchmark_session": "",
    "capture_benchmark_index": 1,
    "gpu_idle_unload_sec": 0,
}

_SAVE_LOCK = threading.Lock()

HOTKEYS = [
    "right alt", "right ctrl", "right shift", "right win",
    "left alt", "left ctrl",
    "f8", "f9", "f10", "f11", "f12",
]

MODELS = [
    "nemotron-speech-streaming-en-0.6b",
    "parakeet-tdt-0.6b-v3",
    "turbo",
    "small.en",
    "medium.en",
    "base.en",
]

MODEL_LABELS = {
    "nemotron-speech-streaming-en-0.6b": "Nemotron English 0.6B (GPU \u00b7 accurate + fast)",
    "parakeet-tdt-0.6b-v3": "Parakeet TDT v3 (GPU \u00b7 best)",
    "turbo": "Whisper turbo (GPU \u00b7 fast)",
    "small.en": "Whisper small.en",
    "medium.en": "Whisper medium.en",
    "base.en": "Whisper base.en (CPU \u00b7 fastest)",
}

SUFFIXES = {"space": " ", "newline": "\n", "none": ""}

_ENUM_SETTINGS = {
    "hotkey": set(HOTKEYS),
    "trigger": {"hold", "toggle"},
    "model": set(MODELS),
    "precision": {"auto", "fp16", "bf16"},
    "suffix": set(SUFFIXES),
}
_NONNEGATIVE_INT_SETTINGS = {
    "last_update_check_epoch",
    "capture_benchmark_remaining",
    "gpu_idle_unload_sec",
}


def _valid_dictionary(value):
    """Keep well-formed string pairs without losing the rest of the dictionary."""
    if not isinstance(value, list):
        return None
    return [
        rule for rule in value
        if (isinstance(rule, list) and len(rule) == 2 and
            all(isinstance(part, str) for part in rule))
    ]


def _default_settings():
    # DEFAULTS is public documentation for settings and contains a list. Each
    # load must own that list so a caller cannot alter later fallback results.
    return {
        key: value.copy() if isinstance(value, list) else value
        for key, value in DEFAULTS.items()
    }


def _validated_settings(payload):
    """Overlay known, correctly shaped persisted values on safe defaults."""
    settings = _default_settings()
    if not isinstance(payload, dict):
        return settings
    for key, default in DEFAULTS.items():
        if key not in payload:
            continue
        value = payload[key]
        if key == "dictionary":
            rules = _valid_dictionary(value)
            if rules is not None:
                settings[key] = rules
            continue
        # Use exact types so JSON integers cannot silently enable booleans and
        # booleans cannot enter timer/index arithmetic (bool subclasses int).
        if type(value) is not type(default):
            continue
        if key in _ENUM_SETTINGS and value not in _ENUM_SETTINGS[key]:
            continue
        if key == "input_device" and not value:
            continue
        if key in _NONNEGATIVE_INT_SETTINGS and value < 0:
            continue
        if key == "capture_benchmark_index" and value < 1:
            continue
        settings[key] = value
    return settings


def load():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return _default_settings()
    return _validated_settings(payload)


def save(settings):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    temporary = CONFIG_PATH + ".tmp"
    with _SAVE_LOCK:
        try:
            with open(temporary, "w", encoding="utf-8") as fh:
                json.dump(settings, fh, indent=2, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temporary, CONFIG_PATH)
        finally:
            try:
                os.remove(temporary)
            except OSError:
                pass
