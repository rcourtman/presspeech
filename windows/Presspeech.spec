# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-directory build for the Windows desktop app."""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata


ROOT = Path(SPECPATH)

datas = []
binaries = []
hiddenimports = [
    "torch",
    "sentencepiece",
    "tokenizers",
    "pystray._win32",
    "pynput.keyboard._win32",
    "pynput.mouse._win32",
]

# These libraries select implementations dynamically, which static import
# analysis cannot see. Keep this list explicit so packaging failures surface
# during the packaged self-test rather than on a user's first dictation.
for package in ("faster_whisper", "ctranslate2", "pycaw", "comtypes", "sounddevice"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

# Transformers' auto classes resolve model packages from string mappings at
# runtime. Collect the model namespace so a newly referenced architecture does
# not become a first-launch-only ModuleNotFoundError in the frozen app.
hiddenimports += collect_submodules("transformers.models", on_error="ignore")

for distribution in (
    "transformers",
    "huggingface-hub",
    "tokenizers",
    "safetensors",
    "sentencepiece",
    "faster-whisper",
    "ctranslate2",
):
    try:
        datas += copy_metadata(distribution)
    except Exception:
        pass

analysis = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["IPython", "jupyter", "matplotlib", "pandas", "comtypes.test"],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Presspeech",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="x86_64",
    icon=str(ROOT / "assets" / "presspeech.ico"),
    version=os.environ.get("PRESSPEECH_VERSION_FILE"),
)

collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Presspeech",
)
