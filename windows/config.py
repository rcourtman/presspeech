"""Settings persistence for Presspeech for Windows."""

import json
import os

APP_NAME = "Presspeech"
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
    "autostart": False,
    "capture_next_benchmark": False,
    "capture_benchmark_remaining": 0,
    "capture_benchmark_session": "",
    "capture_benchmark_index": 1,
    "gpu_idle_unload_sec": 0,
}

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


def load():
    settings = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            settings.update(json.load(fh))
    except (OSError, ValueError):
        pass
    return settings


def save(settings):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(settings, fh, indent=2, ensure_ascii=False)
