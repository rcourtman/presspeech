#!/usr/bin/env python3
"""Synchronise GitHub Pages copy with release metadata.

The site is static on purpose, so release-time values must be written
into the checked-in docs before Pages deploys them. This script owns
the small set of generated values and provides a --check mode for CI.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import html
import json
import plistlib
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
INFO_PLIST = ROOT / "swift" / "Info.plist"
WINDOWS_CONFIG = ROOT / "windows" / "config.py"
DEFAULT_RELEASE_ZIP = ROOT / "swift" / "dist" / "Presspeech.zip"
METADATA_PATH = DOCS / "site-metadata.json"

MODEL_CACHE_SIZE = "~600 MB"
SETUP_CHECKLIST = "Setup Checklist\u2026"
DIAGNOSTICS_SUMMARY = "privacy-safe diagnostics report with app state, permission state, settings counts, microphone availability, memory, and update state; no transcript text, text-correction contents, exact microphone names, raw error details, or raw log lines"

SYNCED_PATHS = [
    ROOT / "README.md",
    ROOT / "windows" / "README.md",
    DOCS / "index.html",
    DOCS / "getting-started.html",
    DOCS / "install.html",
    DOCS / "windows.html",
    DOCS / "install" / "agents.md",
    DOCS / "faq.html",
    DOCS / "llms.txt",
    DOCS / "llms-full.txt",
    DOCS / "demo.svg",
    DOCS / "sitemap.xml",
    METADATA_PATH,
]

# Hand-written project/privacy copy. Not rewritten, but scanned for stale
# patterns so old platform and privacy claims fail loudly.
EXTRA_STALE_SCAN = [
    ROOT / "CONTRIBUTING.md",
    ROOT / "llms.txt",
    ROOT / "icon" / "menu-mockup.svg",
    DOCS / "privacy.html",
    DOCS / "privacy" / "network-calls.json",
    ROOT / "marketing" / "SHARING.md",
    ROOT / "marketing" / "demo" / "README.md",
    ROOT / "icon" / "hero.svg",
    ROOT / "icon" / "demo.svg",
    ROOT / "icon" / "social-preview.svg",
    ROOT / "icon" / "latency.svg",
]

# Public prose can gain a Windows download link outside the files rewritten by
# the syncers above. Scan every public documentation surface so a newly added
# page cannot silently advertise an older prerelease or a tag/asset mismatch.
PUBLIC_RELEASE_ROOTS = [DOCS, ROOT / "marketing"]
PUBLIC_RELEASE_FILES = [
    ROOT / "CONTRIBUTING.md",
    ROOT / "README.md",
    ROOT / "ROADMAP.md",
    ROOT / "SECURITY.md",
    ROOT / "SUPPORT.md",
    ROOT / "llms.txt",
    ROOT / "swift" / "README.md",
    ROOT / "windows" / "README.md",
    ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml",
    ROOT / ".github" / "ISSUE_TEMPLATE" / "compatibility_report.yml",
]
PUBLIC_RELEASE_SUFFIXES = {
    ".html", ".json", ".md", ".svg", ".txt", ".yaml", ".yml",
}
WINDOWS_RELEASE_REFERENCE_PATTERNS = [
    (
        re.compile(
            r"https://github\.com/rcourtman/presspeech/releases/tag/"
            r"windows-v(?P<version>\d+\.\d+\.\d+)"
        ),
        "release tag",
    ),
    (
        re.compile(
            r"https://github\.com/rcourtman/presspeech/releases/download/"
            r"windows-v(?P<version>\d+\.\d+\.\d+)"
        ),
        "download tag",
    ),
    (
        re.compile(r"Presspeech-Setup-(?P<version>\d+\.\d+\.\d+)-x64\.exe"),
        "installer filename",
    ),
    (
        re.compile(r"Download Windows (?P<version>\d+\.\d+\.\d+)"),
        "download label",
    ),
]

# Designed SVG assets that carry the release-size stat in hand-laid text.
# They can't be rewritten mechanically, so --check verifies the current
# size string appears and fails loudly when a release changes it.
ICON_STAT_SVGS = [
    ROOT / "icon" / "hero.svg",
    ROOT / "icon" / "demo.svg",
]

# Discovery surfaces must identify both builds and their different maturity.
# These are hand-designed or hand-written, so release syncing validates rather
# than rewrites them.
PLATFORM_ORIENTATION = {
    ROOT / "README.md": (
        "macOS",
        "Windows",
        "Released, signed, and notarised",
        "Prerelease",
        "**macOS default:** **Right Option**",
        "**Windows default:** **Right Alt**",
        "⌘V on macOS or Ctrl+V on Windows",
    ),
    DOCS / "index.html": (
        "macOS — released",
        "signed and notarised",
        "Windows — prerelease",
        "currently unsigned",
        "video below shows the macOS build",
        "On Windows, use the same hold–speak–release gesture",
    ),
    DOCS / "faq.html": (
        "released macOS app",
        "unsigned Windows prerelease",
        "Apple Silicon",
        "Windows 11",
    ),
    ROOT / "icon" / "hero.svg": ("Mac and Windows", "macOS release", "Windows prerelease"),
    ROOT / "icon" / "social-preview.svg": (
        "for Mac and Windows",
        "macOS release",
        "Windows prerelease",
    ),
}

# Every Windows install entry point must explain that checksum verification does
# not override operating-system policy. Windows 11 Smart App Control and managed
# PCs can intentionally withhold the usual SmartScreen bypass for unsigned apps.
WINDOWS_UNSIGNED_GUIDANCE = {
    ROOT / "README.md": ("Smart App Control", "managed policy", "do not try to circumvent"),
    ROOT / "windows" / "README.md": (
        "Smart App Control",
        "managed policy",
        "do not try to circumvent",
    ),
    DOCS / "getting-started.html": (
        "Smart App Control",
        "managed policy",
        "do not try to circumvent",
    ),
    DOCS / "windows.html": (
        "Smart App Control",
        "managed PCs",
        "do not try to circumvent",
    ),
    DOCS / "llms.txt": (
        "Smart App Control",
        "managed policy",
        "do not try to circumvent",
    ),
    DOCS / "llms-full.txt": (
        "Smart App Control",
        "managed policy",
        "should not try to circumvent",
    ),
    DOCS / "install" / "agents.md": (
        "Smart App Control",
        "managed policy",
        "do not try to circumvent",
    ),
}

# Windows chooses a first-run model from hardware availability. The compact
# CPU path is English-only, while the CUDA default is multilingual. Keep that
# distinction on every discovery/install surface so a model filename such as
# `base.en` is never the only warning before a large unsigned installation.
WINDOWS_LANGUAGE_GUIDANCE = {
    ROOT / "llms.txt": (
        "multilingual Parakeet",
        "English-only Whisper base.en",
    ),
    ROOT / "README.md": (
        "Default language path",
        "multilingual Parakeet",
        "English-only Whisper base.en",
    ),
    ROOT / "windows" / "README.md": (
        "multilingual Parakeet",
        "English-only Whisper base.en",
        "multilingual choices",
    ),
    DOCS / "index.html": (
        "multilingual Parakeet",
        "English-only Whisper base.en",
    ),
    DOCS / "getting-started.html": (
        "multilingual Parakeet",
        "English-only Whisper base.en",
        "before downloading",
    ),
    DOCS / "windows.html": (
        "Check your language before downloading",
        "multilingual Parakeet",
        "English-only Whisper base.en",
    ),
    DOCS / "install" / "agents.md": (
        "language and hardware split",
        "multilingual Parakeet",
        "English-only Whisper base.en",
    ),
    DOCS / "faq.html": (
        "multilingual Parakeet",
        "English-only Whisper base.en",
    ),
    DOCS / "llms.txt": (
        "multilingual Parakeet",
        "English-only Whisper base.en",
    ),
    DOCS / "llms-full.txt": (
        "multilingual Parakeet",
        "English-only Whisper base.en",
    ),
    DOCS / "privacy.html": (
        "multilingual Parakeet",
        "English-only Whisper base.en",
    ),
    DOCS / "privacy" / "network-calls.json": (
        "multilingual Parakeet",
        "English-only Whisper base.en",
    ),
    DOCS / "compare" / "handy.html": (
        "multilingual Parakeet",
        "English-only Whisper base.en",
    ),
}

# Presspeech itself has no transcript-sync feature, but normal delivery writes
# to each platform's general clipboard. Public privacy and retrieval surfaces
# must preserve the separate operating-system boundary, distinguish published
# Windows behavior from 0.1.13's history/cloud exclusion, and avoid implying
# that the exclusion constrains third-party clipboard readers.
CLIPBOARD_SERVICE_GUIDANCE = {
    ROOT / "README.md": (
        "macOS Clipboard History",
        "Spotlight on macOS 26",
        "macOS Universal Clipboard",
        "Windows clipboard",
        "Upcoming Windows 0.1.13 asks Windows to exclude",
    ),
    DOCS / "faq.html": (
        "macOS Clipboard History",
        "Spotlight on macOS 26",
        "macOS Universal Clipboard",
        "Upcoming Windows 0.1.13 asks Windows to exclude",
        "current clipboard remains readable",
    ),
    DOCS / "privacy.html": (
        "macOS Clipboard History",
        "On macOS 26 or later, enabled",
        "macOS Universal Clipboard",
        "Published Windows 0.1.12",
        "clipboard exclusion format",
        "Third-party clipboard managers",
    ),
    DOCS / "privacy" / "network-calls.json": (
        "macOS Clipboard History",
        "Spotlight on macOS 26",
        "macOS Universal Clipboard",
        "Published Windows 0.1.12",
        "ExcludeClipboardContentFromMonitorProcessing",
        "Third-party clipboard managers",
    ),
    DOCS / "windows.html": (
        "Published 0.1.12",
        "ExcludeClipboardContentFromMonitorProcessing",
        "third-party clipboard manager",
    ),
    DOCS / "llms.txt": (
        "macOS Clipboard History",
        "Spotlight on macOS 26",
        "macOS Universal Clipboard",
        "Published Windows 0.1.12",
        "upcoming 0.1.13 marks every dictation item",
        "third-party clipboard managers",
    ),
    DOCS / "llms-full.txt": (
        "macOS Clipboard History",
        "enabled on macOS 26",
        "macOS Universal Clipboard",
        "Published Windows 0.1.12",
        "ExcludeClipboardContentFromMonitorProcessing",
        "third-party clipboard managers",
    ),
    DOCS / "app-compatibility.html": (
        "macOS Clipboard History",
        "enabled on macOS 26",
        "macOS Universal Clipboard",
        "Published Windows 0.1.12",
        "Upcoming Windows 0.1.13",
        "third-party managers",
    ),
}

# Windows 0.1.12's public-model loader did not disable Hub's default implicit
# token behavior. Keep technical details on the privacy/release reference
# surfaces, while discovery pages use a short decision-oriented summary.
WINDOWS_MODEL_DOWNLOAD_PRIVACY_GUIDANCE = {
    ROOT / "SECURITY.md": (
        "published Windows 0.1.12 prerelease",
        "agent-harnesses",
        "agent-related environment markers",
        "HF_ENDPOINT",
        "HUGGINGFACE_CO_STAGING",
        "HF_HUB_USER_AGENT_ORIGIN",
        "configured endpoint",
        "Upcoming Windows 0.1.13",
        "Already used Windows 0.1.12?",
        "inherited",
        "staging setting",
        "destination you do not trust",
        "treat the token as disclosed",
        "revoke the token",
        "Hugging Face Access Tokens",
        "Do not include token values",
    ),
    DOCS / "privacy.html": (
        "published Windows 0.1.12",
        "agent-harnesses",
        "agent-related environment markers",
        "HF_TOKEN",
        "local Hugging Face cache",
        "HF_ENDPOINT",
        "HUGGINGFACE_CO_STAGING",
        "HF_HUB_USER_AGENT_ORIGIN",
        "configured endpoint",
        "These models do not require an account token",
        "Upcoming Windows 0.1.13",
        "disables implicit authentication",
        "Already used Windows 0.1.12?",
        "inherited",
        "staging setting",
        "destination you do not trust",
        "treat the token as disclosed",
        "revoke the token",
        "Hugging Face Access Tokens",
        "Do not include token values",
    ),
    DOCS / "privacy" / "network-calls.json": (
        "Published Windows 0.1.12",
        "agent-harnesses",
        "agent-related environment markers",
        "HF_TOKEN",
        "local Hugging Face cache",
        "HF_ENDPOINT",
        "HUGGINGFACE_CO_STAGING",
        "HF_HUB_USER_AGENT_ORIGIN",
        "configured endpoint",
        "public models do not require an account token",
        "Upcoming Windows 0.1.13",
        "implicit authentication",
    ),
    DOCS / "windows.html": (
        "Privacy for published Windows 0.1.12",
        "agent-harnesses",
        "agent-related environment markers",
        "HF_TOKEN",
        "local Hugging Face cache",
        "HF_ENDPOINT",
        "HUGGINGFACE_CO_STAGING",
        "HF_HUB_USER_AGENT_ORIGIN",
        "configured endpoint",
        "These models do not require an account token",
        "upcoming 0.1.13",
        "Already used Windows 0.1.12?",
        "inherited",
        "staging setting",
        "destination you do not trust",
        "treat the token as disclosed",
        "revoke the token",
        "Hugging Face Access Tokens",
        "Do not include token values",
    ),
    ROOT / "windows" / "README.md": (
        "0.1.12 loader also leaves implicit authentication enabled",
        "agent-harnesses",
        "agent-related environment markers",
        "HF_TOKEN",
        "local Hugging Face cache",
        "HF_ENDPOINT",
        "HUGGINGFACE_CO_STAGING",
        "HF_HUB_USER_AGENT_ORIGIN",
        "configured endpoint",
        "These public models do not require an account token",
        "Upcoming 0.1.13",
        "disables implicit authentication",
        "Already used Windows 0.1.12?",
        "inherited",
        "staging setting",
        "destination you do not trust",
        "treat the token as disclosed",
        "revoke the token",
        "Hugging Face Access Tokens",
        "Do not include token values",
    ),
}

WINDOWS_AGENT_DISCLOSURE = {
    ROOT / "README.md": ("agent-harnesses", "agent-related environment markers"),
    DOCS / "llms.txt": ("agent-harnesses", "agent-related environment markers"),
    DOCS / "llms-full.txt": ("agent-harnesses", "agent-related environment markers"),
}

MAC_MODEL_DOWNLOAD_PRIVACY_SUMMARY = {
    ROOT / "README.md": (
        "Before installing or launching macOS 0.3.8",
        "Hugging Face token inherited by Presspeech",
        "public model needs no account token",
        "wait until macOS 0.3.9 is published",
        "Dictation audio and transcripts are not sent",
        "version-specific network inventory",
        "follow-up guidance before another model download",
        "Do not inspect or display token values",
    ),
    DOCS / "index.html": (
        "Before installing or launching macOS 0.3.8",
        "Hugging Face token inherited by Presspeech",
        "public model needs no account token",
        "wait until macOS 0.3.9 is published",
        "Dictation audio and transcripts are not sent",
        "version-specific network inventory",
        "Already used macOS 0.3.8?",
        "macos-0-3-8-after-use",
    ),
    DOCS / "getting-started.html": (
        "Before installing or launching",
        "macOS 0.3.8 model-download requests",
        "Hugging Face token inherited by Presspeech",
        "public model needs no account token",
        "wait until macOS 0.3.9 is published",
        "Dictation audio and transcripts are not sent",
        "version-specific network inventory",
        "Already used macOS 0.3.8?",
        "macos-0-3-8-after-use",
    ),
    DOCS / "faq.html": (
        "Before installing or launching macOS 0.3.8",
        "Hugging Face token inherited by Presspeech",
        "public model needs no account token",
        "wait until macOS 0.3.9 is published",
        "Dictation audio and transcripts are not sent",
        'id="faq-macos-install-privacy"',
    ),
    DOCS / "install.html": (
        'id="model-download-privacy"',
        "Before installing or launching macOS 0.3.8",
        "Hugging Face token inherited by Presspeech",
        "public model needs no account token",
        "wait until macOS 0.3.9 is published",
        "Dictation audio and transcripts are not sent",
        "version-specific network inventory",
        "Already used macOS 0.3.8?",
        "macos-0-3-8-after-use",
    ),
    DOCS / "install" / "agents.md": (
        "macOS 0.3.8",
        "Hugging Face token inherited by Presspeech",
        "public model needs no account token",
        "wait until macOS 0.3.9 is published",
        "Do not inspect or display token values",
        "informed choice",
    ),
    DOCS / "llms.txt": (
        "macOS 0.3.8 may attach an inherited Hugging Face token",
        "public model needs no account token",
        "wait until macOS 0.3.9 is published",
        "dictation audio and transcripts are not sent",
        "leave a working model cache in place",
        "macos-0-3-8-after-use",
    ),
    DOCS / "llms-full.txt": (
        "macOS 0.3.8 can attach an inherited `HF_TOKEN`",
        "public model needs no account token",
        "If a token may be inherited by Presspeech",
        "wait until macOS 0.3.9 is published",
        "removes those credentials from its own process",
        "Dictation audio and transcripts are not sent in these requests",
        "If macOS 0.3.8 is already in use",
        "leave a working local model cache in place",
        "cache reset or integrity retry",
        "macos-0-3-8-after-use",
    ),
    DOCS / "privacy.html": (
        "Before installing or launching macOS 0.3.8",
        "Hugging Face token inherited by Presspeech",
        "public model needs no token",
        "wait until macOS 0.3.9 is published",
        "Dictation audio and transcripts are not sent",
        "HF_TOKEN",
        "HUGGING_FACE_HUB_TOKEN",
        "HUGGINGFACEHUB_API_TOKEN",
        "removes those credentials from its own process",
        'id="macos-0-3-8-after-use"',
        "leave its cache in place",
        "integrity retry or cache reset",
        "wait until 0.3.9 is installed",
    ),
}

WINDOWS_MODEL_DOWNLOAD_PRIVACY_SUMMARY = {
    ROOT / "README.md": (
        "Before installing or launching Windows 0.1.12",
        "usage telemetry",
        "already-configured or locally saved Hugging Face token",
        "Custom download routing can change where the model request",
        "if a Hugging Face token or custom download route is configured on this PC",
        "wait until Windows 0.1.13 is published",
        "public models need no account token",
        "Windows privacy decision and technical details",
        "Already used Windows 0.1.12?",
        "inherited",
        "staging setting",
        "destination you do not trust",
        "treat the token as disclosed",
        "revoke the token",
        "Hugging Face Access Tokens",
        "Do not include token values",
    ),
    DOCS / "index.html": (
        "Before installing or launching Windows 0.1.12",
        "usage telemetry",
        "already-configured or locally saved Hugging Face token",
        "Custom download routing can change where the model request",
        "if a Hugging Face token or custom download route is configured on this PC",
        "wait until Windows 0.1.13 is published",
        "public models need no account token",
        "Windows privacy decision and technical details",
        "Already used Windows 0.1.12?",
        "inherited",
        "staging setting",
        "destination you do not trust",
        "treat the token as disclosed",
        "revoke the token",
        "Hugging Face Access Tokens",
        "Do not include token values",
    ),
    DOCS / "getting-started.html": (
        "Before installing or launching Windows 0.1.12",
        "usage telemetry",
        "already-configured or locally saved Hugging Face token",
        "Custom download routing can change where the model request",
        "if a Hugging Face token or custom download route is configured on this PC",
        "wait until Windows 0.1.13 is published",
        "public models need no account token",
        "Windows privacy decision and technical details",
        "Already used Windows 0.1.12?",
        "inherited",
        "staging setting",
        "destination you do not trust",
        "treat the token as disclosed",
        "revoke the token",
        "Hugging Face Access Tokens",
        "Do not include token values",
    ),
    DOCS / "faq.html": (
        "Before installing or launching Windows 0.1.12",
        "usage telemetry",
        "include an available token",
        "custom routing can change where the token goes",
        "wait until Windows 0.1.13 is published",
        "dictation audio and transcripts are not sent",
        'id="faq-windows-install-privacy"',
    ),
}

# Published 0.1.12 and the unreleased 0.1.13 candidate have different
# authentication behavior. Keep each claim attached to the affected version.
WINDOWS_MODEL_DOWNLOAD_PRIVACY_SCOPE_SURFACES = (
    ROOT / "README.md",
    DOCS / "privacy.html",
    DOCS / "privacy" / "network-calls.json",
)

# Discovery and setup surfaces must not collapse focus-safe delivery into an
# "every app" promise. Automatic insertion is conditional; clipboard recovery
# is part of the product contract rather than an exceptional implementation
# detail.
DELIVERY_BOUNDARY_GUIDANCE = {
    ROOT / "README.md": ("original destination", "clipboard", "paste manually"),
    ROOT / "windows" / "README.md": (
        "cannot verify that destination",
        "clipboard",
        "manual paste",
    ),
    DOCS / "index.html": ("cannot safely verify the destination", "manual paste"),
    DOCS / "getting-started.html": ("cannot verify the same destination", "clipboard"),
    DOCS / "install.html": ("cannot verify that destination", "clipboard"),
    DOCS / "windows.html": ("cannot verify that destination", "clipboard"),
    DOCS / "faq.html": ("cannot verify that destination", "clipboard"),
    DOCS / "llms.txt": ("cannot verify the same destination", "clipboard"),
    DOCS / "llms-full.txt": ("verify the original destination", "clipboard"),
    ROOT / "marketing" / "SHARING.md": ("cannot be verified", "clipboard"),
}

# Keep the large macOS model transfer's consent behavior explicit by release:
# the linked 0.3.8 app starts on launch, while 0.3.9 gates a clean install on
# the user's choice. Vague "depending on the build" copy hides the behavior
# that matters most to someone deciding whether to start a 500+ MB download.
MAC_MODEL_DOWNLOAD_GUIDANCE = {
    ROOT / "README.md": ("0.3.8", "0.3.9", "500", "clean install", "Download Model", "defer"),
    DOCS / "index.html": ("0.3.8", "0.3.9", "500", "clean install", "Download Model", "defer"),
    DOCS / "getting-started.html": (
        "0.3.8", "0.3.9", "500", "clean install", "Download Model", "defer"
    ),
    DOCS / "install.html": ("0.3.8", "0.3.9", "500", "clean install", "Download Model", "defer"),
    DOCS / "faq.html": ("0.3.8", "0.3.9", "500", "clean install", "Download Model", "defer"),
    DOCS / "privacy.html": (
        "0.3.8", "0.3.9", "500", "clean install", "Download Model", "defer"
    ),
    DOCS / "privacy" / "network-calls.json": (
        "0.3.8", "0.3.9", "500", "clean install", "Download Model", "defer"
    ),
    DOCS / "llms-full.txt": (
        "0.3.8", "0.3.9", "500", "clean install", "Download Model", "defer"
    ),
    DOCS / "install" / "agents.md": (
        "0.3.8", "0.3.9", "500", "clean install", "Download Model", "defer"
    ),
}

COMPATIBILITY_OVERALL_RESULTS = (
    "All five steady-focus attempts pasted once; all three focus-change attempts "
    "recovered safely",
    "Manual-paste recovery occurred during steady focus; no incorrect or unsafe "
    "result occurred",
    "An incorrect or unsafe result occurred",
    "Testing could not be completed",
)

# Target-app evidence must remain discoverable, comparable, and safe. Keep the
# public entry points linked to the repeated protocol and filtered report
# index, and keep the issue form's field classification aligned with them.
COMPATIBILITY_EVIDENCE_GUIDANCE = {
    ROOT / "README.md": (
        "Help qualify target apps",
        "five steady-focus attempts",
        "three focus-change attempts",
        "live coverage links separate native, browser, and Electron/Chromium",
        "Browse existing compatibility",
    ),
    ROOT / "SUPPORT.md": (
        "Browse existing target-app compatibility reports",
        "live coverage",
        "generic field type",
    ),
    ROOT / "CONTRIBUTING.md": (
        "per platform/app/version/field type",
        "Browse existing compatibility",
    ),
    ROOT / ".github" / "ISSUE_TEMPLATE" / "compatibility_report.yml": (
        "id: field-type",
        "id: outcome-counts",
        "Single-line plain-text field",
        "Rich-text or contenteditable editor",
        "Five steady-focus results",
        "Three focus-change results",
        "Never run this protocol at a command shell",
        "after the fixed `[Compatibility]:` title prefix",
        "issues?q=is%3Aissue%20in%3Atitle",
        "Focus-change recovery is expected",
        "Manual-paste recovery occurred during steady focus",
        "including its six counts and Overall result",
        *COMPATIBILITY_OVERALL_RESULTS,
    ),
    DOCS / "index.html": (
        "Delivery is testable",
        "eight-check target-app protocol",
        "Browse compatibility reports",
    ),
    DOCS / "app-compatibility.md": (
        "generic field type",
        "Choose useful coverage",
        "Native desktop app reports",
        "Browser page or web editor reports",
        "Electron/Chromium desktop app reports",
        "Add your counts even when the outcome differs",
        "ordinary command-shell prompt is outside",
        "after the fixed `[Compatibility]:` title prefix",
        "issues?q=is%3Aissue%20in%3Atitle",
        "Focus-change recovery is expected",
        "manual-paste recovery occurred while the original target stayed focused",
        "worksheet's six counts and Overall result",
        "Download report draft",
        "blank prompts for public versions and generic target context",
    ),
    DOCS / "app-compatibility.html": (
        "generic field type",
        "Choose a useful target",
        "Browse native-app reports",
        "Browse browser reports",
        "Browse Electron/Chromium reports",
        "variation under comparable conditions",
        "ordinary command-shell prompt is outside",
        "fixed <code>[Compatibility]:</code> title prefix",
        "issues?q=is%3Aissue%20in%3Atitle",
        "Focus-change recovery is expected",
        "manual-recovery option only when it occurred while the original target stayed focused",
        "worksheet's six counts and Overall result",
        "Download report draft",
        "blank prompts for public version and generic target context",
        "Check new-report availability",
    ),
}

# The compatibility worksheet deliberately accepts categories only. Keep its
# implementation local and ephemeral so the page cannot silently turn a
# privacy-safe report helper into another collection surface.
COMPATIBILITY_WORKSHEET_PAGE = DOCS / "app-compatibility.html"
COMPATIBILITY_WORKSHEET_SCRIPT = DOCS / "compatibility-worksheet.js"
COMPATIBILITY_WORKSHEET_FORBIDDEN = (
    (r"\bfetch\s*\(", "fetch"),
    (r"\bXMLHttpRequest\b", "XMLHttpRequest"),
    (r"\bWebSocket\s*\(", "WebSocket"),
    (r"\bEventSource\s*\(", "EventSource"),
    (r"\bsendBeacon\b", "sendBeacon"),
    (r"\b(?:localStorage|sessionStorage|indexedDB)\b", "browser storage"),
    (r"\bdocument\s*\.\s*cookie\b", "cookies"),
    (r"https?://", "remote URL"),
)
# A newline is not cosmetic in a command shell: it can submit the pasted text.
# Keep the safe review workflow on the onboarding and retrieval surfaces most
# likely to be used before someone dictates into Terminal or PowerShell.
COMMAND_SHELL_GUIDANCE = {
    ROOT / "README.md": ("command shell", "Append newline", "review the exact result"),
    ROOT / "windows" / "README.md": ("command shells", "**newline**", "review it"),
    DOCS / "getting-started.html": (
        "Command shells can run pasted text",
        "Append newline",
        "review the exact text",
    ),
    DOCS / "windows.html": ("execution surfaces", "newline", "review it"),
    DOCS / "faq.html": ("command shell", "Append newline", "review the exact command"),
    DOCS / "llms.txt": ("Command-shell safety", "Append newline", "reviewing the exact result"),
    DOCS / "llms-full.txt": ("execution surfaces", "Append newline", "reviewing the exact result"),
}

# GitHub renders repository Markdown as soon as a release commit reaches main,
# before the matching assets are necessarily public. Pages has a publication
# gate, but repository-rendered entry points do not. Keep those entry points on
# URLs that can only resolve to a published artifact: GitHub's stable latest
# alias for macOS and the gated, version-pinned Pages guide for Windows.
REPOSITORY_INSTALL_GUIDANCE = {
    ROOT / "README.md": (
        "The `main` branch can contain",
        "an unreleased candidate",
        "releases/latest/download/Presspeech.zip",
        "install.html#direct-download",
        "windows.html#download-verify-run",
    ),
    ROOT / "windows" / "README.md": (
        "windows.html#download-verify-run",
        "source tree can be ahead of the published prerelease",
    ),
    DOCS / "llms.txt": (
        "releases/latest/download/Presspeech.zip",
        "install.html#direct-download",
    ),
    DOCS / "install" / "agents.md": (
        "site-metadata.json",
        "windows.html#download-verify-run",
        "$ErrorActionPreference = 'Stop'",
        "$version -notmatch",
    ),
}
REPOSITORY_UNPUBLISHED_DOWNLOAD_PATTERNS = {
    ROOT / "README.md": (
        re.compile(r"releases/download/v\d+\.\d+\.\d+/Presspeech\.zip"),
        re.compile(
            r"releases/download/windows-v\d+\.\d+\.\d+/"
            r"Presspeech-Setup-\d+\.\d+\.\d+-x64\.exe"
        ),
        re.compile(r"[0-9a-f]{64}\s+Presspeech\.zip"),
    ),
    ROOT / "windows" / "README.md": (
        re.compile(
            r"releases/download/windows-v\d+\.\d+\.\d+/"
            r"Presspeech-Setup-\d+\.\d+\.\d+-x64\.exe"
        ),
        re.compile(r"releases/tag/windows-v\d+\.\d+\.\d+"),
    ),
    DOCS / "llms.txt": (
        re.compile(r"releases/download/v\d+\.\d+\.\d+/Presspeech\.zip"),
        re.compile(
            r"releases/download/windows-v\d+\.\d+\.\d+/"
            r"Presspeech-Setup-\d+\.\d+\.\d+-x64\.exe"
        ),
        re.compile(r"Presspeech\.zip[^\n]*[0-9a-f]{64}"),
    ),
    DOCS / "install" / "agents.md": (
        re.compile(r"Presspeech for Windows \d+\.\d+\.\d+"),
        re.compile(r"\$version\s*=\s*['\"]\d+\.\d+\.\d+['\"]"),
        re.compile(
            r"releases/download/windows-v\d+\.\d+\.\d+/"
            r"Presspeech-Setup-\d+\.\d+\.\d+-x64\.exe"
        ),
    ),
}

# Compare pages quote fast-moving competitor pricing and claims. Each page must
# carry an exact "checked <day> <Month> <Year>" stamp; sync and --check fail
# once the oldest stamp ages out so a release forces a re-verify against the
# cited sources. scripts/check-compare-releases.py separately watches products
# whose first-party GitHub release can be checked between those reviews.
COMPARE_DIR = DOCS / "compare"
COMPARE_MAX_AGE_DAYS = 90
COMPARE_CHECKED_RE = re.compile(
    r"\bchecked (\d{1,2}) "
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r" (\d{4})\b"
)
MONTH_NUMBERS = {
    name: number
    for number, name in enumerate(
        (
            "January February March April May June "
            "July August September October November December"
        ).split(),
        start=1,
    )
}

STALE_PATTERNS = [
    (
        re.compile(
            r"\b(?:the )?apps? ship with no account, subscription, telemetry\b",
            re.IGNORECASE,
        ),
        "unqualified no-telemetry claim conflicts with published Windows dependencies",
    ),
    (
        re.compile(
            r"\bthere is no account, subscription, telemetry\b",
            re.IGNORECASE,
        ),
        "unqualified no-telemetry claim conflicts with published Windows dependencies",
    ),
    (
        re.compile(
            r"\bPresspeech has no analytics, event tracking, crash reporter\b",
            re.IGNORECASE,
        ),
        "first-party analytics claim does not distinguish bundled-library telemetry",
    ),
    (re.compile(r"2\.2 MB"), "old release zip size"),
    (re.compile(r'"softwareVersion": "0\.2\.1"'), "old structured-data version"),
    (
        re.compile(
            r"dictat(?:e|ion)\s+into\s+any(?:\s+[\w-]+){0,2}\s+app",
            re.IGNORECASE,
        ),
        "unsupported universal paste-delivery promise",
    ),
    (
        re.compile(r"(?:text|transcript) appears wherever (?:your|the) cursor", re.IGNORECASE),
        "unsupported universal paste-delivery promise",
    ),
    (
        re.compile(
            r"(?:push-to-talk dictation|text entry) at the cursor",
            re.IGNORECASE,
        ),
        "paste delivery presented without its focus boundary",
    ),
    (
        re.compile(r"transcribed locally, pasted, then discarded", re.IGNORECASE),
        "delivery pipeline omits clipboard recovery",
    ),
    (
        re.compile(r"Anywhere you can type", re.IGNORECASE),
        "unsupported universal paste-delivery promise",
    ),
    (re.compile(r"#install-one-liner"), "old README anchor install URL"),
    (re.compile(r"warning rows?", re.IGNORECASE), "old permission warning-row setup wording"),
    (re.compile(r"permission rows disappear", re.IGNORECASE), "old permission-row completion wording"),
    (
        re.compile(
            r"deliberately does not restore (?:your|the) previous clipboard contents",
            re.IGNORECASE,
        ),
        "pre-clipboard-restoration privacy wording",
    ),
    (re.compile(r"Platform: Apple Silicon Macs only", re.IGNORECASE), "pre-Windows platform wording"),
    (
        re.compile(
            r"Presspeech is a free MIT-licensed menu-bar app for (?:local |private )?"
            r"push-to-talk dictation on Apple Silicon Macs",
            re.IGNORECASE,
        ),
        "pre-Windows project summary",
    ),
    (
        re.compile(r"The project is a\s+single-file Swift menu-bar app", re.IGNORECASE),
        "pre-Windows contributor wording",
    ),
    (
        re.compile(r"cross-platform Windows or Linux support", re.IGNORECASE),
        "pre-Windows recommendation wording",
    ),
    (
        re.compile(r"no dock icon, no preferences window", re.IGNORECASE),
        "pre-optional-Dock-access onboarding wording",
    ),
    (
        re.compile(r"launching twice does nothing", re.IGNORECASE),
        "pre-repeat-launch-recovery Windows wording",
    ),
    (
        re.compile(
            r"Hold (?:<strong>)?Right Option(?:</strong>)? on macOS or "
            r"(?:<strong>)?Right Alt(?:</strong>)? on Windows",
            re.IGNORECASE,
        ),
        "first-dictation guidance ignores the configured hotkey",
    ),
    (
        re.compile(
            r"Hold Right Alt, speak, release, and check the result",
            re.IGNORECASE,
        ),
        "Windows private-test guidance ignores the configured hotkey",
    ),
    (
        re.compile(r"releases/latest/download/Presspeech\.zip\.sha256"),
        "macOS checksum link is not present in the current release",
    ),
    (
        re.compile(
            r"(?:100\s*ms.{0,40}release[- ]to[- ]paste|"
            r"release[- ]to[- ]paste.{0,40}(?:benchmark|100\s*ms))",
            re.IGNORECASE,
        ),
        "benchmark mislabeled as release-to-paste latency",
    ),
    (
        re.compile(
            r"transcript (?:pastes|appears)[^\n.]{0,80}(?:about|~)\s*100\s*ms",
            re.IGNORECASE,
        ),
        "model benchmark presented as complete paste latency",
    ),
    (
        re.compile(r"about\s+100\s+ms\s+(?:later|after release)", re.IGNORECASE),
        "model benchmark presented as complete paste latency",
    ),
    (
        re.compile(
            r"(?:~|about)\s*100\s*ms\s+from\s+(?:key\s+)?release\s+to\s+pasted\s+text",
            re.IGNORECASE,
        ),
        "model benchmark presented as release-triggered paste latency",
    ),
    (
        re.compile(r"END-TO-END LATENCY", re.IGNORECASE),
        "model-only benchmark labeled end-to-end",
    ),
    (
        re.compile(r"18\s+Latin/Cyrillic-script languages via Parakeet", re.IGNORECASE),
        "language-hint count presented as recognition-language count",
    ),
]


class SyncError(RuntimeError):
    pass


MAC_INSTALL_PROMPT = """Install Presspeech from https://github.com/rcourtman/presspeech on this Mac.

Before installing or launching macOS 0.3.8, disclose that a Hugging Face token inherited by Presspeech may be included in model-download requests; the public model needs no account token. If a token may be present in the environment that launches Presspeech—or the user is unsure—offer to wait until macOS 0.3.9 is published. Do not inspect or display token values, change credential settings, or launch 0.3.8 without the user's informed choice. Model downloads do not include dictation audio or transcripts. See https://rcourtman.github.io/presspeech/privacy.html#network-calls.

Presspeech has two notarised install paths: a direct release zip and a Homebrew Cask. Use Homebrew when it is already installed because it also handles updates. If Homebrew is missing, offer the direct download instead of forcing the user to install Homebrew first.

Run:
  uname -m
  sw_vers -productVersion
  brew install --cask rcourtman/presspeech/presspeech
  open /Applications/Presspeech.app

Direct download:
  https://github.com/rcourtman/presspeech/releases/latest/download/Presspeech.zip

Stop if the Mac is not Apple Silicon (arm64) or macOS is older than 14.

After launch, explain that macOS 0.3.8 starts its first local speech-model download (~500-600 MB) on launch. In 0.3.9, a clean install must choose Download Model in Setup; close Setup to defer. Existing installs and cached models continue loading automatically. Before asking the user to enable Input Monitoring, explain that macOS's grant can expose typed keys; Presspeech requests keyboard events only to detect the configured hotkey and Escape to cancel an active recording, passes other keys through without saving, logging, or sending their values, and does not inspect mouse or trackpad events. Offer Apple's guide at https://support.apple.com/guide/mac-help/mchl4cedafb6/mac. Use Setup Checklist to finish the model, permissions, and hotkey readiness. The default dictation key is Right Option. Focus on setup and the first private test; do not ask the user to star, review, or otherwise endorse the project."""

WINDOWS_INSTALL_PROMPT = r"""Install Presspeech from https://github.com/rcourtman/presspeech on this Windows PC.

Use only the published Windows prerelease selected by Presspeech's deployed metadata and version-pinned install guide:
  https://rcourtman.github.io/presspeech/windows.html#download-verify-run

The source branch can contain a newer unreleased candidate, so do not infer a download version from windows/config.py, release notes, or other files on main. The deployed metadata stays on a version whose installer and checksum are both public. The installer is not code-signed. Explain that before downloading; SHA-256 verification confirms that the file matches the asset published in this repository, but it is not a publisher signature.

Run these read-only checks in PowerShell:
  [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
  (Get-CimInstance Win32_OperatingSystem).Caption

Stop if the architecture is not X64. Windows 11 is recommended. If this is Windows 10, explain that general support has ended and continue only if the user confirms the PC has Extended Security Updates or an edition that remains supported.

Before downloading, explain the language and hardware split: a fresh system with usable NVIDIA CUDA selects multilingual Parakeet (~2.5 GB), while a fresh system without usable CUDA selects English-only Whisper base.en on CPU (~141 MiB). Published Windows 0.1.12 starts the selected model download on first launch. Upcoming 0.1.13 asks before downloading missing Parakeet files and offers the smaller CPU model or deferral. Other local models remain selectable in Settings, but the multilingual alternatives are intended for a supported NVIDIA GPU. If the user needs a language other than English and does not have usable NVIDIA CUDA, show them https://rcourtman.github.io/presspeech/windows.html#language-support and ask whether they still want to continue.

Download the installer and its checksum from the same official release, then verify both the checksum-file shape and the installer hash:
  $ErrorActionPreference = 'Stop'
  $metadataUrl = 'https://rcourtman.github.io/presspeech/site-metadata.json'
  $metadata = Invoke-RestMethod -Uri $metadataUrl
  $version = [string]$metadata.windows_version
  if ($version -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') {
    throw 'The published Windows version is not valid. Do not download anything.'
  }
  $base = "https://github.com/rcourtman/presspeech/releases/download/windows-v$version"
  $folder = Join-Path ([IO.Path]::GetTempPath()) "Presspeech-$version"
  New-Item -ItemType Directory -Force -Path $folder | Out-Null
  $installer = Join-Path $folder "Presspeech-Setup-$version-x64.exe"
  Invoke-WebRequest "$base/Presspeech-Setup-$version-x64.exe" -OutFile $installer
  Invoke-WebRequest "$base/Presspeech-Setup-$version-x64.exe.sha256" -OutFile "$installer.sha256"
  $parts = (Get-Content -LiteralPath "$installer.sha256" -Raw).Trim() -split '\s+'
  if ($parts.Count -ne 2 -or
      $parts[0] -notmatch '^[0-9a-fA-F]{64}$' -or
      $parts[1] -ne (Split-Path $installer -Leaf)) {
    throw 'The published checksum file is not valid. Do not run the installer.'
  }
  $actual = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash
  if ($actual -ne $parts[0]) {
    throw 'SHA-256 verification failed. Do not run the installer.'
  }
  "SHA-256 verified: $actual"

If GitHub CLI is already installed and the user is already signed in, offer an optional provenance check. Run it only with the user's approval; do not inspect or display credentials, install GitHub CLI, or sign in for this check:
  $tag = "windows-v$version"
  gh release verify $tag --repo rcourtman/presspeech
  if ($LASTEXITCODE -ne 0) {
    throw 'Release attestation verification failed; do not run the installer.'
  }
  gh release verify-asset $tag $installer --repo rcourtman/presspeech
  if ($LASTEXITCODE -ne 0) {
    throw 'Installer attestation verification failed; do not run the installer.'
  }
This checks the immutable release and the installer's signed GitHub release attestation. It is stronger provenance evidence than the checksum served beside the installer, but it does not code-sign the installer or prove the program is safe. If the user agrees to the check and either command fails, stop; do not run the installer. If the user declines or gh is unavailable, say clearly that provenance was not verified; do not claim otherwise. Let the user decide whether the repository source and matching checksum are enough, and never launch without explicit confirmation.

Once the checksum succeeds and any requested attestation check also succeeds—or the user explicitly chooses checksum-only trust—show the user the installer path and verified hash. Ask for explicit confirmation before launching it with:
  Start-Process -FilePath $installer

Do not automate a security-warning choice. If Microsoft Defender SmartScreen offers More info → Run anyway, the user must decide whether to proceed after checking the source and hash. If Windows 11 Smart App Control or managed policy blocks the unsigned installer without an override, stop; do not try to circumvent that policy.

After the user completes the installer, launch Presspeech from the Start Menu. Explain that first launch may download a local model (about 141 MiB on a fresh CPU-only PC or about 2.5 GB with usable NVIDIA CUDA; an incomplete Parakeet cache may need less). With 0.1.13, Setup asks before downloading missing Parakeet files and offers the smaller CPU model or deferral; with published 0.1.12, the model download starts automatically on first launch, so make sure the user understands the size before launching. Published 0.1.12 also checks the microphone automatically; upcoming 0.1.13 leaves it closed until the user chooses Check Microphone. Let them decide whether to run that test in versions that offer the button, then finish Setup before testing the configured hotkey. Right Alt is the default; choose F8 or another available key if Right Alt acts as AltGr. Use Try Dictation for the first private test. Focus on setup and the first private test; do not ask the user to star, review, or otherwise endorse the project."""


def agents_markdown(_metadata: dict[str, object]) -> str:
    return f"""# Install Presspeech with a shell-capable assistant

Choose the prompt for the computer where Presspeech should be installed. The
Windows prompt deliberately stops rather than weakening operating-system policy
for the unsigned prerelease.

## macOS

```text
{MAC_INSTALL_PROMPT}
```

## Windows

```text
{WINDOWS_INSTALL_PROMPT}
```
"""


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def replace_regex(text: str, pattern: str, replacement: str, *, path: Path, flags: int = 0) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count != 1:
        raise SyncError(f"{path}: expected one match for {pattern!r}, found {count}")
    return updated


def replace_literal(text: str, old: str, new: str, *, path: Path) -> str:
    if old == new:
        return text
    if old not in text:
        raise SyncError(f"{path}: expected literal not found: {old[:80]!r}")
    return text.replace(old, new, 1)


def replace_after_marker(
    text: str,
    marker: str,
    pattern: str,
    replacement: str,
    *,
    path: Path,
    flags: int = 0,
) -> str:
    """Replace one generated field after a unique object marker."""
    if text.count(marker) != 1:
        raise SyncError(f"{path}: expected one marker {marker!r}, found {text.count(marker)}")
    before, after = text.split(marker, 1)
    return before + marker + replace_regex(
        after, pattern, replacement, path=path, flags=flags
    )


def read_app_version() -> str:
    with INFO_PLIST.open("rb") as fh:
        plist = plistlib.load(fh)
    version = plist.get("CFBundleShortVersionString")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SyncError(f"{INFO_PLIST}: invalid CFBundleShortVersionString {version!r}")
    return version


def read_windows_version() -> str:
    text = read_text(WINDOWS_CONFIG)
    matches = re.findall(r'^VERSION\s*=\s*"(\d+\.\d+\.\d+)"\s*$', text, flags=re.M)
    if len(matches) != 1:
        raise SyncError(
            f"{WINDOWS_CONFIG}: expected one canonical VERSION assignment, found {len(matches)}"
        )
    return matches[0]


def release_size(bytes_count: int) -> str:
    mib = bytes_count / (1024 * 1024)
    if mib < 10:
        return f"{mib:.1f} MB"
    return f"{round(mib):.0f} MB"


def load_metadata() -> dict[str, object]:
    if not METADATA_PATH.exists():
        return {}
    return json.loads(read_text(METADATA_PATH))


def build_metadata(args: argparse.Namespace) -> dict[str, object]:
    existing = load_metadata()
    zip_path = Path(args.release_zip).resolve() if args.release_zip else None

    release_zip_bytes = existing.get("release_zip_bytes")
    release_zip_sha256 = existing.get("release_zip_sha256")
    if zip_path is not None:
        if not zip_path.exists():
            raise SyncError(f"release zip not found: {zip_path}")
        release_zip_bytes = zip_path.stat().st_size
        release_zip_sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    if not isinstance(release_zip_bytes, int):
        raise SyncError(
            f"{METADATA_PATH}: missing release_zip_bytes. "
            "Run scripts/sync-docs.py --release-zip swift/dist/Presspeech.zip from the release workflow."
        )
    if not isinstance(release_zip_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", release_zip_sha256
    ):
        raise SyncError(
            f"{METADATA_PATH}: missing or invalid release_zip_sha256. "
            "Run scripts/sync-docs.py --release-zip swift/dist/Presspeech.zip from the release workflow."
        )

    if args.date:
        last_updated = args.date
    elif args.check and isinstance(existing.get("last_updated"), str):
        last_updated = existing["last_updated"]
    else:
        last_updated = date.today().isoformat()

    return {
        "schema": 1,
        "version": read_app_version(),
        "windows_version": read_windows_version(),
        "release_zip_bytes": release_zip_bytes,
        "release_zip_sha256": release_zip_sha256,
        "release_zip_size": release_size(release_zip_bytes),
        "model_cache_size": MODEL_CACHE_SIZE,
        "last_updated": last_updated,
    }


def metadata_text(metadata: dict[str, object]) -> str:
    return json.dumps(metadata, indent=2, sort_keys=True) + "\n"


def display_date(value: object) -> str:
    try:
        raw = str(value)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw) is None:
            raise ValueError
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise SyncError(f"invalid metadata date {value!r}") from exc
    return f"{parsed.day} {parsed:%B %Y}"


def sync_readme(path: Path, metadata: dict[str, object]) -> str:
    text = read_text(path)
    size = str(metadata["release_zip_size"])
    text = replace_regex(
        text,
        r"\*\*[\d.]+ MB release zip\*\*",
        f"**{size} release zip**",
        path=path,
    )
    text = replace_regex(
        text,
        r'- \*\*(?:Copy Diagnostics|Copy/Save Diagnostics)\*\* — .*',
        "- **Copy/Save Diagnostics** — privacy-safe support report with app state, settings counts, microphone availability, and update state; exact device names, raw error details, and logs stay local",
        path=path,
    )
    return text


def sync_windows_readme(path: Path, metadata: dict[str, object]) -> str:
    text = read_text(path)
    version = str(metadata["windows_version"])
    text = replace_regex(
        text,
        r"  -Version \d+\.\d+\.\d+ -Python ",
        f"  -Version {version} -Python ",
        path=path,
    )
    return text


def sync_index(path: Path, metadata: dict[str, object]) -> str:
    text = read_text(path)
    version = str(metadata["version"])
    windows_version = str(metadata["windows_version"])
    size = str(metadata["release_zip_size"])
    last_updated = str(metadata["last_updated"])
    updated_display = display_date(last_updated)

    page_marker = '"@id": "https://rcourtman.github.io/presspeech/#webpage"'
    mac_marker = '"@id": "https://rcourtman.github.io/presspeech/#software"'
    windows_marker = '"@id": "https://rcourtman.github.io/presspeech/windows.html#software"'
    text = replace_after_marker(
        text,
        page_marker,
        r'"dateModified": "[^"]+"',
        f'"dateModified": "{last_updated}"',
        path=path,
    )
    text = replace_after_marker(
        text,
        mac_marker,
        r'"softwareVersion": "[^"]+"',
        f'"softwareVersion": "{version}"',
        path=path,
    )
    text = replace_after_marker(
        text,
        mac_marker,
        r'"installUrl": "[^"]+"',
        '"installUrl": "https://rcourtman.github.io/presspeech/install.html"',
        path=path,
    )
    text = replace_after_marker(
        text,
        mac_marker,
        r'"storageRequirements": "[^"]+"',
        f'"storageRequirements": "{size} signed release zip plus about 500-600 MB for the local speech model cache"',
        path=path,
    )
    text = replace_after_marker(
        text,
        windows_marker,
        r'"softwareVersion": "[^"]+"',
        f'"softwareVersion": "{windows_version}"',
        path=path,
    )
    text = replace_after_marker(
        text,
        windows_marker,
        r'"releaseNotes": "[^"]+"',
        '"releaseNotes": "https://github.com/rcourtman/presspeech/releases/tag/'
        f'windows-v{windows_version}"',
        path=path,
    )
    text = replace_after_marker(
        text,
        windows_marker,
        r'"downloadUrl": "[^"]+"',
        '"downloadUrl": "https://github.com/rcourtman/presspeech/releases/download/'
        f'windows-v{windows_version}/Presspeech-Setup-{windows_version}-x64.exe"',
        path=path,
    )
    unified_same_as = '          "https://huggingface.co/nvidia/parakeet-' 'unified-en-0.6b",\n'
    if unified_same_as in text:
        text = text.replace(unified_same_as, "", 1)
    text = replace_regex(
        text,
        r'<div class="stat"><strong>[\d.]+ MB</strong><span>signed release zip</span></div>',
        f'<div class="stat"><strong>{size}</strong><span>signed release zip</span></div>',
        path=path,
    )
    text = replace_regex(
        text,
        r"<strong>macOS(?: \d+\.\d+\.\d+)?:</strong>",
        f"<strong>macOS {version}:</strong>",
        path=path,
    )
    text = replace_regex(
        text,
        r"<strong>Windows(?: \d+\.\d+\.\d+)?:</strong>",
        f"<strong>Windows {windows_version}:</strong>",
        path=path,
    )
    text = replace_regex(
        text,
        r'<p class="quiet" data-release-status>.*?</p>',
        f'<p class="quiet" data-release-status>Current published downloads: macOS {version} and Windows {windows_version} prerelease. Install metadata updated <time datetime="{last_updated}">{updated_display}</time>.</p>',
        path=path,
    )

    return text


def sync_getting_started(path: Path, metadata: dict[str, object]) -> str:
    text = read_text(path)
    version = str(metadata["version"])
    windows_version = str(metadata["windows_version"])
    text = replace_regex(
        text,
        r"<h3>macOS(?: \d+\.\d+\.\d+)?</h3>",
        f"<h3>macOS {version}</h3>",
        path=path,
    )
    text = replace_regex(
        text,
        r"<h3>Windows(?: \d+\.\d+\.\d+)? prerelease</h3>",
        f"<h3>Windows {windows_version} prerelease</h3>",
        path=path,
    )
    return text


def sync_install_html(path: Path, metadata: dict[str, object]) -> str:
    text = read_text(path)
    digest = str(metadata["release_zip_sha256"])
    version = str(metadata["version"])
    last_updated = str(metadata["last_updated"])
    updated_display = display_date(last_updated)
    escaped_prompt = html.escape(MAC_INSTALL_PROMPT, quote=False)

    text = replace_regex(
        text,
        r'(<a class="button" href="https://github\.com/rcourtman/presspeech/'
        r'releases/(?:latest/download|download/v\d+\.\d+\.\d+)/Presspeech\.zip">)'
        r'(?:Download Presspeech\.zip|Download macOS \d+\.\d+\.\d+ \(\.zip\))'
        r'(</a>)',
        f'<a class="button" href="https://github.com/rcourtman/presspeech/releases/download/v{version}/Presspeech.zip">Download macOS {version} (.zip)</a>',
        path=path,
    )

    text = replace_regex(
        text,
        r"<title>Install Presspeech(?: on macOS)? - .*?</title>",
        "<title>Install Presspeech on macOS - Direct Download or Homebrew</title>",
        path=path,
    )
    text = replace_regex(
        text,
        r'<meta name="description" content="[^"]+">',
        '<meta name="description" content="Install Presspeech from the notarised zip or Homebrew Cask, launch the app, use Setup Checklist to finish the local model, permissions, and hotkey readiness, then start push-to-talk dictation.">',
        path=path,
    )
    text = replace_regex(
        text,
        r"<p>(?:The canonical install path is|Use the direct notarised download for the shortest path).*?</p>",
        "<p>Use the direct notarised download for the shortest path, or Homebrew if you want command-line install and updates. The app then guides model loading, macOS privacy grants, and hotkey readiness from Setup Checklist.</p>",
        path=path,
    )
    text = replace_regex(
        text,
        r'<p class="quiet" data-release-status>.*?</p>',
        f'<p class="quiet" data-release-status>Current published macOS release: {version}. Install metadata updated <time datetime="{last_updated}">{updated_display}</time>.</p>',
        path=path,
    )
    text = replace_regex(
        text,
        r"<p>(?:The Presspeech icon appears in the menu bar|Homebrew is the easiest path if you already use it or want command-line updates)\..*?</p>",
        "<p>Homebrew is the easiest path if you already use it or want command-line updates. On first launch, macOS shows its standard downloaded-app confirmation; choose <strong>Open</strong> after checking that it says Apple found no malicious software. The Presspeech icon then appears in the menu bar. The macOS 0.3.8 release starts its first ~500–600 MB model download on launch. In 0.3.9, a clean install must choose <strong>Download Model</strong> in Setup; close Setup to defer. Existing installs and cached models load automatically. If setup is not complete, Presspeech opens Setup Checklist; you can reopen it from the menu at any time.</p>",
        path=path,
    )
    text = replace_regex(
        text,
        r'<div class="fact"><strong>(?:Model download|First model download)</strong><span>.*?</span></div>',
        '<div class="fact"><strong>First model download</strong><span>Internet is required for the local model, about 500–600 MB. macOS 0.3.8 starts the first download on launch. In 0.3.9, a clean install must choose Download Model in Setup; close Setup to defer. Existing installs and cached models load automatically.</span></div>',
        path=path,
    )
    text = replace_regex(
        text,
        r'<p><a href="https://github\.com/rcourtman/presspeech/releases/'
        r'(?:latest/download|download/v\d+\.\d+\.\d+)/Presspeech\.zip">'
        r"Download Presspeech(?: \d+\.\d+\.\d+ \(\.zip\)|\.zip)</a> "
        r"from the (?:latest|current) GitHub release\.</p>",
        '<p><a href="https://github.com/rcourtman/presspeech/releases/'
        f'download/v{version}/Presspeech.zip">Download Presspeech {version} (.zip)</a> '
        "from the current GitHub release.</p>",
        path=path,
    )
    text = replace_regex(
        text,
        r"<p>(?:Download <a href=\"https://github\.com/rcourtman/presspeech/releases/latest/"
        r"download/Presspeech\.zip\.sha256\">.*?|The current archive's published SHA-256 is .*?"
        r"|In Downloads, verify the current archive against its published SHA-256:)</p>"
        r"(?:\s*<pre><code>.*?</code></pre>\s*"
        r"<p>Continue only if it reports <code>Presspeech\.zip: OK</code>\."
        r"(?: A checksum detects.*?)?</p>"
        r"(?:\s*<details>.*?</details>)?)?",
        "<p>In Downloads, verify the current archive against its published SHA-256:</p>\n"
        "              <pre><code>cd ~/Downloads\n"
        f"echo '{digest}  Presspeech.zip' | shasum -a 256 -c -</code></pre>\n"
        "              <p>Continue only if it reports <code>Presspeech.zip: OK</code>. A checksum detects a damaged or different download; by itself, it does not establish who built or published the file.</p>\n"
        "              <details>\n"
        "                <summary>Verify the release attestation (optional)</summary>\n"
        "                <p>For an additional check, install <a href=\"https://cli.github.com/\">GitHub CLI</a> and verify both the immutable release and the exact archive you downloaded. Replace the tag below if you downloaded a different version:</p>\n"
        f"                <pre><code>gh release verify v{version} --repo rcourtman/presspeech\n"
        f"gh release verify-asset v{version} ~/Downloads/Presspeech.zip --repo rcourtman/presspeech</code></pre>\n"
        "                <p>Both commands must succeed. This checks the release’s GitHub attestation and that the archive matches its attested asset; it does not prove the software is vulnerability-free or benign.</p>\n"
        "              </details>",
        path=path,
        flags=re.S,
    )
    text = replace_regex(
        text,
        r"<p>Presspeech needs Microphone, Accessibility(?: \(shown as Device Control and Data Access on macOS 27 and later\))?, and Input Monitoring\..*?</p>",
        "<p>Presspeech needs Microphone, Accessibility (shown as Device Control and Data Access on macOS 27 and later), and Input Monitoring. Setup Checklist shows each grant, explains why it is needed, and opens the relevant macOS prompt or Settings pane. macOS's Input Monitoring grant can expose typed keys; Presspeech's listener requests keyboard events only for the configured hotkey and Escape to cancel active dictation, passes other keys through without saving, logging, or sending their values, and does not inspect mouse or trackpad events. Review <a href=\"https://support.apple.com/guide/mac-help/mchl4cedafb6/mac\">Apple's Input Monitoring guidance</a> before deciding.</p>",
        path=path,
    )
    text = replace_regex(
        text,
        r"<p>(?:Click each warning row in the menu|Use the Grant buttons in Setup Checklist|"
        r"Use the context-specific <strong>Continue</strong>, <strong>Open Settings</strong>, "
        r"or <strong>Try Again</strong> actions in Setup Checklist)\..*?</p>",
        "<p>Use the context-specific <strong>Continue</strong>, "
        "<strong>Open Settings</strong>, or <strong>Try Again</strong> actions in "
        "Setup Checklist. The main menu also shows clickable permission rows "
        "while anything is missing, so setup can continue even after the "
        "checklist window is closed.</p>",
        path=path,
    )
    if "<strong>Grant the three permissions</strong>" in text:
        text = replace_regex(
            text,
            r"<strong>Grant the three permissions</strong>\s*<p>.*?</p>",
            "<strong>Finish Setup Checklist</strong>\n              <p>Open the Presspeech menu and choose <strong>Setup Checklist\u2026</strong>. Use it to finish the speech model, permissions, and hotkey check.</p>",
            path=path,
            flags=re.S,
        )
    text = replace_regex(
        text,
        r"<pre><code>Install Presspeech from https://github\.com/rcourtman/presspeech on this Mac\..*?</code></pre>",
        f"<pre><code>{escaped_prompt}</code></pre>",
        path=path,
        flags=re.S,
    )
    return text


def sync_windows_html(path: Path, metadata: dict[str, object]) -> str:
    text = read_text(path)
    version = str(metadata["windows_version"])
    last_updated = str(metadata["last_updated"])
    updated_display = display_date(last_updated)
    escaped_prompt = html.escape(WINDOWS_INSTALL_PROMPT, quote=False)

    replacements = [
        (r'"softwareVersion": "\d+\.\d+\.\d+"', f'"softwareVersion": "{version}"', 1),
        (r'"dateModified": "\d{4}-\d{2}-\d{2}"', f'"dateModified": "{last_updated}"', 1),
        (r"windows-v\d+\.\d+\.\d+", f"windows-v{version}", 1),
        (
            r"Presspeech-Setup-\d+\.\d+\.\d+-x64\.exe",
            f"Presspeech-Setup-{version}-x64.exe",
            1,
        ),
    ]
    for pattern, replacement, minimum in replacements:
        text, count = re.subn(pattern, replacement, text)
        if count < minimum:
            raise SyncError(f"{path}: expected at least {minimum} matches for {pattern!r}")
    text = replace_regex(
        text,
        r'<p class="quiet" data-release-status>.*?</p>',
        f'<p class="quiet" data-release-status>Current published Windows prerelease: {version}. Install metadata updated <time datetime="{last_updated}">{updated_display}</time>.</p>',
        path=path,
    )
    prompt_pattern = (
        r"<pre><code>Install Presspeech from https://github\.com/rcourtman/presspeech "
        r"on this Windows PC\..*?</code></pre>"
    )
    text, prompt_count = re.subn(
        prompt_pattern,
        lambda _: f"<pre><code>{escaped_prompt}</code></pre>",
        text,
        count=1,
        flags=re.S,
    )
    if prompt_count != 1:
        raise SyncError(
            f"{path}: expected one embedded Windows install prompt, found {prompt_count}"
        )
    return text


def sync_agents_md(path: Path, metadata: dict[str, object]) -> str:
    del path
    return agents_markdown(metadata)


def sync_faq(path: Path, metadata: dict[str, object]) -> str:
    del metadata
    text = read_text(path)
    text = replace_regex(
        text,
        r"<p>(?:<strong>macOS:</strong> )?Microphone, Accessibility(?: \(shown as Device Control and Data Access on macOS 27 and later\))?, and Input Monitoring\..*?</p>",
        "<p><strong>macOS:</strong> Microphone, Accessibility (shown as Device Control and Data Access on macOS 27 and later), and Input Monitoring. Setup Checklist tracks each grant. <strong>Windows:</strong> Turn on Microphone access and Let desktop apps access your microphone; Windows does not provide a separate Presspeech toggle for an unpackaged desktop app.</p>",
        path=path,
    )
    diagnostics_card = """            <article class="card">
              <h3>What is in diagnostics?</h3>
              <p>macOS Copy/Save Diagnostics and Windows Copy Diagnostics create privacy-safe reports with app, model, microphone availability, settings, and update state. They omit transcript text, audio, dictionary contents, exact microphone names, raw error details, and raw log lines; review the separate local log before sharing any part of it.</p>
            </article>
"""
    if "What is in diagnostics?" in text:
        text = replace_regex(
            text,
            r'(<h3>What is in diagnostics\?</h3>\s*)<p>.*?</p>',
            r'\1<p>macOS Copy/Save Diagnostics and Windows Copy Diagnostics create privacy-safe reports with app, model, microphone availability, settings, and update state. They omit transcript text, audio, dictionary contents, exact microphone names, raw error details, and raw log lines; review the separate local log before sharing any part of it.</p>',
            path=path,
        )
    else:
        text = replace_literal(text, "          </div>\n        </div>\n      </section>", diagnostics_card + "          </div>\n        </div>\n      </section>", path=path)
    return text


OLD_LLMS_PRIVACY_SUMMARY = (
    "The apps ship with no account, subscription, telemetry, or cloud transcription endpoint."
)


def sync_llms(path: Path, metadata: dict[str, object]) -> str:
    text = read_text(path)
    size = str(metadata["release_zip_size"])
    text = replace_regex(
        text,
        r"- (?:Release size|macOS footprint): about [\d.]+ MB signed zip; "
        r"(?:model cache is about 500-600 MB|model cache is about 600 MB on first launch)\.",
        f"- macOS footprint: about {size} signed zip; model cache is about 500-600 MB.",
        path=path,
    )
    setup_line = "- macOS setup: use Setup Checklist from the menu bar to finish the model, permissions, and hotkey readiness.\n"
    if setup_line not in text:
        text = replace_literal(
            text,
            "- Homebrew install: `brew install --cask rcourtman/presspeech/presspeech`.\n",
            "- Homebrew install: `brew install --cask rcourtman/presspeech/presspeech`.\n" + setup_line,
            path=path,
        )
    privacy_line = (
        "- Privacy: no cloud transcription or Presspeech-authored analytics, and no transcript persistence; "
        "macOS 0.3.8 may attach an inherited Hugging Face token to model-download requests, "
        "although the public model needs no account token. If a token may be present in the environment "
        "that launches Presspeech—or you are unsure—wait until macOS 0.3.9 is published; dictation audio "
        "and transcripts are not sent in those requests. If macOS 0.3.8 is already in use, leave a working "
        "model cache in place and wait for 0.3.9 before a planned re-download; see "
        "https://rcourtman.github.io/presspeech/privacy.html#macos-0-3-8-after-use. "
        "During Windows 0.1.12 model downloads, bundled libraries may send default usage telemetry to "
        "Hugging Face, model-request metadata includes a random per-process session ID, and pinned Hub "
        "1.29.0 may request /api/agent-harnesses and add an agent/<id> label based on inherited "
        "agent-related environment markers. An available "
        "Hugging Face token may accompany a model request. Inherited HF_ENDPOINT and "
        "HUGGINGFACE_CO_STAGING settings can change its destination, and HF_HUB_USER_AGENT_ORIGIN "
        "is included in request metadata if set; the token may accompany a request to that configured "
        "endpoint. These public models do not need an account token. Upcoming Windows 0.1.13 fixes "
        "these inherited settings but is not yet published. Dictation audio "
        "and transcripts are not sent in model downloads; exact telemetry fields are not independently "
        "itemised (see the privacy inventory)."
    )
    if re.search(r"(?m)^- Privacy:.*$", text):
        text = re.sub(r"(?m)^- Privacy:.*$", privacy_line, text, count=1)
    diagnostics_line = "- Diagnostics: macOS Copy/Save Diagnostics and Windows Copy Diagnostics report runtime and microphone availability without transcript, dictionary, exact microphone names, raw error details, or raw log lines.\n"
    if re.search(r"(?m)^- Diagnostics:.*$", text):
        text = re.sub(r"(?m)^- Diagnostics:.*$", diagnostics_line.rstrip("\n"), text, count=1)
    else:
        text = replace_literal(
            text,
            privacy_line + "\n",
            privacy_line + "\n" + diagnostics_line,
            path=path,
        )
    text = text.replace(
        "- Windows install and requirements: "
        "https://github.com/rcourtman/presspeech/blob/main/windows/README.md.",
        "- Windows install and requirements: "
        "https://rcourtman.github.io/presspeech/windows.html.",
        1,
    )
    if OLD_LLMS_PRIVACY_SUMMARY in text:
        text = replace_literal(
            text,
            OLD_LLMS_PRIVACY_SUMMARY,
            "The apps have no account or cloud transcription endpoint and Presspeech does not operate first-party analytics. "
            "Published Windows 0.1.12 leaves Hugging Face libraries' default usage telemetry enabled during model downloads; "
            "the libraries may send usage data, and model-request metadata includes a random per-process session ID. "
            "It also leaves implicit authentication enabled, so an available HF_TOKEN, HUGGING_FACE_HUB_TOKEN, or locally cached Hugging Face token may accompany a model request. Inherited HF_ENDPOINT and HUGGINGFACE_CO_STAGING settings can change its destination; if HF_HUB_USER_AGENT_ORIGIN is set, its value is included in request metadata. These models do not require an account token. Upcoming Windows 0.1.13 fixes these inherited settings but is not yet published. "
            "Dictation audio and transcripts are not sent in model downloads; see the version-specific privacy inventory for details and limits.",
            path=path,
        )
    old_windows_privacy = (
        "Published Windows 0.1.12 leaves Hugging Face libraries' default usage telemetry enabled during model downloads; "
        "the libraries may send usage data, and model-request metadata includes a random per-process session ID."
    )
    old_windows_privacy_with_token = old_windows_privacy + (
        " It also leaves implicit authentication enabled, so an available HF_TOKEN, "
        "HUGGING_FACE_HUB_TOKEN, or locally cached Hugging Face token may accompany a public model request; "
        "these models do not require an account token."
    )
    if old_windows_privacy_with_token in text and "HF_ENDPOINT" not in text:
        text = replace_literal(
            text,
            old_windows_privacy_with_token,
            old_windows_privacy + " It also leaves implicit authentication enabled, so an available HF_TOKEN, HUGGING_FACE_HUB_TOKEN, or locally cached Hugging Face token may accompany a model request. The 0.1.12 loader honors inherited HF_ENDPOINT and HUGGINGFACE_CO_STAGING settings, which can change its destination; if HF_HUB_USER_AGENT_ORIGIN is set, its value is included in request metadata. A token may accompany a request to that configured endpoint; these models do not require an account token. Upcoming Windows 0.1.13 fixes these inherited settings but is not yet published.",
            path=path,
        )
    if old_windows_privacy in text and "HF_ENDPOINT" not in text:
        text = replace_literal(
            text,
            old_windows_privacy,
            old_windows_privacy + " The 0.1.12 loader also honors inherited HF_ENDPOINT and HUGGINGFACE_CO_STAGING settings, which can change the request destination; if HF_HUB_USER_AGENT_ORIGIN is set, its value is included in request metadata. An available HF_TOKEN, HUGGING_FACE_HUB_TOKEN, or locally cached Hugging Face token may accompany a request to that configured endpoint; these models do not require an account token. Upcoming Windows 0.1.13 fixes these inherited settings but is not yet published.",
            path=path,
        )
    windows_page = "- Windows install: https://rcourtman.github.io/presspeech/windows.html\n"
    if windows_page not in text:
        text = replace_literal(
            text,
            "- Install: https://rcourtman.github.io/presspeech/install.html\n",
            "- Install on macOS: https://rcourtman.github.io/presspeech/install.html\n"
            + windows_page,
            path=path,
        )
    return text


def sync_llms_full(path: Path, metadata: dict[str, object]) -> str:
    del metadata
    text = read_text(path)
    # Keep the checked-in assistant reference's published-build disclosure when
    # refreshing other generated facts; this copy is not a release claim for 0.1.13.
    privacy_marker = "model-request metadata includes a random per-process session ID. "
    privacy_paragraph = text.partition("## Privacy")[2].lstrip().partition("\n\n")[0]
    if "agent-harnesses" not in privacy_paragraph:
        text = text.replace(
            privacy_marker,
            privacy_marker + "The pinned Hub 1.29.0 client may request /api/agent-harnesses when its local registry cache is missing or stale and may add an agent/<id> label to model-request metadata based on inherited agent-related environment markers. Upcoming Windows 0.1.13 disables Hub telemetry before imports and checks that rendered headers contain no agent label; these controls are not in published 0.1.12. ",
            1,
        )
    calls_marker = "1. Speech model downloads normally"
    before_calls, marker, calls_text = text.partition(calls_marker)
    if marker and "agent-harnesses" not in calls_text:
        telemetry_marker = "the exact telemetry fields are not independently itemised."
        calls_text = calls_text.replace(
            telemetry_marker,
            telemetry_marker + " The pinned Hub 1.29.0 client may request /api/agent-harnesses when its local registry cache is missing or stale and may add an agent/<id> label to model-request User-Agent metadata based on inherited agent-related environment markers. Upcoming Windows 0.1.13 disables Hub telemetry before imports and checks that rendered headers contain no agent label; these controls are not in published 0.1.12.",
            1,
        )
        text = before_calls + marker + calls_text
    old_privacy_summary = (
        "Presspeech has no analytics, event tracking, crash reporter, account system, "
        "transcript sync, or cloud transcription endpoint. Audio is captured while "
        "the hotkey is active, transcribed locally, then discarded."
    )
    if old_privacy_summary in text:
        text = replace_literal(
            text,
            old_privacy_summary,
            "Presspeech does not operate first-party analytics, event tracking, or crash reporting, "
            "and has no account system, transcript sync, or cloud transcription endpoint. "
            "During model downloads, the published Windows 0.1.12 prerelease leaves bundled "
            "Hugging Face libraries' default usage telemetry enabled. They may send usage data "
            "to Hugging Face, and model-request metadata includes a random per-process session ID. "
            "Dictation audio and transcripts are not sent in model downloads; exact telemetry "
            "fields are not independently itemised. An available HF_TOKEN, HUGGING_FACE_HUB_TOKEN, or token in the local Hugging Face cache may accompany a model request. The 0.1.12 loader honors inherited HF_ENDPOINT and HUGGINGFACE_CO_STAGING settings, which can change its destination; if HF_HUB_USER_AGENT_ORIGIN is set, its value is included in request metadata. These models do not require an account token. Upcoming Windows 0.1.13 fixes these inherited settings but is not yet published. Audio is captured while the hotkey is "
            "active, transcribed locally, then discarded.",
            path=path,
        )
    download_sentence = (
        "The macOS 0.3.8 release starts its first speech-model download (about 500-600 MB) "
        "on launch. In 0.3.9, a clean install must choose Download Model in Setup; "
        "closing Setup defers it. Existing installations and cached models load automatically. "
        "The model is stored under `~/Library/Application Support/FluidAudio/`.\n"
    )
    old_mac_download_copy = (
        "The first macOS speech-model download is about 500-600 MB into "
        "`~/Library/Application Support/FluidAudio/`. Depending on the build, it starts on "
        "launch or after a new install chooses Download Model in Setup; existing installs "
        "keep automatic startup.\n"
    )
    if old_mac_download_copy in text:
        text = replace_literal(text, old_mac_download_copy, download_sentence, path=path)
    text = re.sub(
        r"First launch downloads the default local speech model weights, about 500-600 MB, "
        r"into `~/Library/Application Support/FluidAudio/`\. The [^.]+ model downloads only if selected\.\n",
        download_sentence,
        text,
        count=1,
    )
    setup_sentence = (
        "Use Setup Checklist from the Presspeech menu bar item to finish the speech model, "
        "Microphone, Accessibility (Device Control and Data Access on macOS 27+), "
        "Input Monitoring, and hotkey readiness checks.\n"
    )
    if setup_sentence not in text:
        text = replace_literal(
            text,
            download_sentence,
            download_sentence + "\n"
            + setup_sentence,
            path=path,
        )
    old_model_privacy = (
        "Published Windows 0.1.12 leaves the bundled Hugging Face libraries' default usage telemetry enabled. "
        "The libraries may send library-defined usage data to Hugging Face, and Transformers includes a random per-process session identifier in model-request metadata; the exact telemetry fields are not independently itemised."
    )
    old_model_privacy_with_token = old_model_privacy + (
        " It also leaves default implicit authentication enabled, so a token from HF_TOKEN, "
        "HUGGING_FACE_HUB_TOKEN, or the local Hugging Face token cache may accompany a public model request; "
        "these public models do not require an account token."
    )
    if old_model_privacy_with_token in text and "HF_ENDPOINT" not in text:
        text = replace_literal(
            text,
            old_model_privacy_with_token,
            old_model_privacy + " It also leaves default implicit authentication enabled, so a token from HF_TOKEN, HUGGING_FACE_HUB_TOKEN, or the local Hugging Face token cache may accompany a model request. The 0.1.12 loader honors inherited HF_ENDPOINT and HUGGINGFACE_CO_STAGING settings, which can change its destination; if HF_HUB_USER_AGENT_ORIGIN is set, its value is included in request metadata. A token may accompany a request to that configured endpoint; these public models do not require an account token. Upcoming Windows 0.1.13 fixes these inherited settings but is not yet published.",
            path=path,
        )
    if old_model_privacy in text and "HF_ENDPOINT" not in text:
        text = replace_literal(
            text,
            old_model_privacy,
            old_model_privacy + " The 0.1.12 loader honors inherited HF_ENDPOINT and HUGGINGFACE_CO_STAGING settings, which can change the request destination; if HF_HUB_USER_AGENT_ORIGIN is set, its value is included in request metadata. A token from HF_TOKEN, HUGGING_FACE_HUB_TOKEN, or the local Hugging Face token cache may accompany a request to that configured endpoint; these public models do not require an account token. Upcoming Windows 0.1.13 fixes these inherited settings but is not yet published.",
            path=path,
        )
    diagnostics_sentence = (
        "For support, macOS Copy/Save Diagnostics and Windows Copy Diagnostics create privacy-safe "
        "local reports with runtime metadata and microphone availability. The reports exclude "
        "transcript text, dictionary/correction contents, exact microphone names, raw error details, and raw log lines.\n"
    )
    diagnostics_pattern = (
        r"^For support, macOS Copy/Save Diagnostics and Windows Copy Diagnostics .*$"
    )
    if re.search(diagnostics_pattern, text, flags=re.M):
        text = replace_regex(
            text,
            diagnostics_pattern,
            diagnostics_sentence.rstrip("\n"),
            path=path,
            flags=re.M,
        )
    else:
        text = replace_literal(
            text,
            "Machine-readable network surface:\n",
            diagnostics_sentence + "\nMachine-readable network surface:\n",
            path=path,
        )
    text = text.replace(
        "The current Windows installer, requirements, checksum link, and source-development steps are maintained in:\n"
        "https://github.com/rcourtman/presspeech/blob/main/windows/README.md",
        "The current Windows installer, requirements, checksum verification, and first-run steps are maintained at:\n"
        "https://rcourtman.github.io/presspeech/windows.html",
        1,
    )
    text = text.replace(
        "- Windows guide: https://github.com/rcourtman/presspeech/blob/main/windows/README.md",
        "- Windows install: https://rcourtman.github.io/presspeech/windows.html\n"
        "- Windows technical guide: https://github.com/rcourtman/presspeech/blob/main/windows/README.md",
        1,
    )
    text = text.replace(
        "- Install: https://rcourtman.github.io/presspeech/install.html",
        "- Install on macOS: https://rcourtman.github.io/presspeech/install.html",
        1,
    )
    return text


def sync_demo_svg(path: Path, metadata: dict[str, object]) -> str:
    del path, metadata
    # The docs site embeds the same animated demo the README uses, but
    # GitHub Pages serves only docs/, so mirror the canonical SVG here.
    return read_text(ROOT / "icon" / "demo.svg")


def sync_sitemap(path: Path, metadata: dict[str, object]) -> str:
    # A sitemap date is useful only when it tracks a significant change to the
    # named resource. ``expected_files`` supplies the release-synced resources
    # whose rendered contents actually changed; leave every other URL's date
    # alone instead of making the whole site look newly rewritten on release.
    changed = metadata.get("_changed_public_paths", ())
    if not isinstance(changed, (list, tuple, set, frozenset)):
        raise SyncError("internal sitemap change set has an invalid type")
    changed_paths = {Path(value).resolve() for value in changed}

    text = read_text(path)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise SyncError(f"{path}: cannot parse sitemap: {exc}") from exc

    namespace = "http://www.sitemaps.org/schemas/sitemap/0.9"
    entries = root.findall(f"{{{namespace}}}url")
    if not entries:
        raise SyncError(f"{path}: expected at least one sitemap URL entry")

    last_updated = str(metadata["last_updated"])
    changed_urls: list[str] = []
    for entry in entries:
        loc = entry.findtext(f"{{{namespace}}}loc")
        lastmod = entry.findtext(f"{{{namespace}}}lastmod")
        if not loc or not lastmod:
            raise SyncError(f"{path}: sitemap URL entry is missing loc or lastmod")
        if not loc.startswith("https://rcourtman.github.io/presspeech/"):
            continue
        relative = loc.removeprefix("https://rcourtman.github.io/presspeech/")
        if not relative:
            relative = "index.html"
        elif relative.endswith("/"):
            relative += "index.html"
        public_path = (DOCS / relative).resolve()
        if public_path in changed_paths and lastmod != last_updated:
            changed_urls.append(loc)

    for loc in changed_urls:
        pattern = (
            rf"(<url>\s*<loc>{re.escape(loc)}</loc>\s*<lastmod>)"
            r"\d{4}-\d{2}-\d{2}"
            r"(</lastmod>\s*</url>)"
        )
        text, count = re.subn(pattern, rf"\g<1>{last_updated}\g<2>", text, count=1)
        if count != 1:
            raise SyncError(f"{path}: could not update lastmod for {loc}")
    return text


SYNCERS = {
    ROOT / "README.md": sync_readme,
    ROOT / "windows" / "README.md": sync_windows_readme,
    DOCS / "index.html": sync_index,
    DOCS / "getting-started.html": sync_getting_started,
    DOCS / "install.html": sync_install_html,
    DOCS / "windows.html": sync_windows_html,
    DOCS / "install" / "agents.md": sync_agents_md,
    DOCS / "faq.html": sync_faq,
    DOCS / "llms.txt": sync_llms,
    DOCS / "llms-full.txt": sync_llms_full,
    DOCS / "demo.svg": sync_demo_svg,
    DOCS / "sitemap.xml": sync_sitemap,
}


def sync_icon_stats(
    previous_size: str, size: str, paths: list[Path] = ICON_STAT_SVGS
) -> list[str]:
    """Refresh the release-size caption inside designed artwork.

    The caption is a statistic, not artwork: when a release build changes the
    published size, the exact previous caption is swapped in place and every
    other designed byte is preserved. Anything more surprising than that exact
    swap is left for check_icon_stats to fail on.
    """
    updated: list[str] = []
    if not previous_size or previous_size == size:
        return updated
    pattern = re.compile(rf"(?<![0-9.]){re.escape(previous_size)}")
    for path in paths:
        if not path.exists():
            continue
        contents = read_text(path)
        if size in contents:
            continue
        replaced, count = pattern.subn(size, contents)
        if not count:
            continue
        write_text(path, replaced)
        display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
        updated.append(str(display))
        print(f"updated {display}")
    return updated


def check_icon_stats(metadata: dict[str, object]) -> list[str]:
    size = str(metadata["release_zip_size"])
    errors: list[str] = []
    for path in ICON_STAT_SVGS:
        if not path.exists():
            errors.append(f"{path.relative_to(ROOT)}: missing icon SVG")
            continue
        if size not in read_text(path):
            errors.append(
                f"{path.relative_to(ROOT)}: release size stat is stale — "
                f"expected {size!r} (designed asset; a release sync refreshes "
                "the caption, otherwise update the text by hand)"
            )
    return errors


def check_platform_orientation(
    surfaces: dict[Path, tuple[str, ...]] = PLATFORM_ORIENTATION,
) -> list[str]:
    errors: list[str] = []
    for path, required in surfaces.items():
        display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
        if not path.exists():
            errors.append(f"{display}: missing public discovery surface")
            continue
        contents = read_text(path)
        missing = [snippet for snippet in required if snippet not in contents]
        if missing:
            errors.append(
                f"{display}: platform status is ambiguous; missing "
                + ", ".join(repr(snippet) for snippet in missing)
            )
    return errors


def check_windows_unsigned_guidance(
    surfaces: dict[Path, tuple[str, ...]] = WINDOWS_UNSIGNED_GUIDANCE,
) -> list[str]:
    errors: list[str] = []
    for path, required in surfaces.items():
        display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
        if not path.exists():
            errors.append(f"{display}: missing Windows install guidance")
            continue
        contents = " ".join(read_text(path).split())
        missing = [phrase for phrase in required if phrase not in contents]
        if missing:
            errors.append(
                f"{display}: incomplete unsigned Windows guidance — "
                f"missing {', '.join(repr(phrase) for phrase in missing)}"
            )
    return errors


def check_windows_verified_download_flow(
    path: Path = DOCS / "windows.html",
) -> list[str]:
    """Keep the unsigned installer behind its compatibility and trust checks.

    The JSON-LD download URL remains direct for software catalogues. The visible
    primary action is different: it must take a person to the page's language,
    hardware, checksum, and signing guidance before a binary starts downloading.
    """
    display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
    if not path.exists():
        return [f"{display}: missing Windows install guide"]

    contents = read_text(path)
    anchors = []
    for match in re.finditer(r"<a\b(?P<attrs>[^>]*)>(?P<label>.*?)</a>", contents, re.I | re.S):
        attributes = {
            name.lower(): value
            for name, _quote, value in re.findall(
                r"([:\w-]+)\s*=\s*([\"'])(.*?)\2", match.group("attrs"), re.S
            )
        }
        anchors.append((match, attributes))
    primary_actions = [
        (match, attributes)
        for match, attributes in anchors
        if "button" in attributes.get("class", "").split()
        and "secondary" not in attributes.get("class", "").split()
    ]
    errors: list[str] = []
    if len(primary_actions) != 1:
        errors.append(
            f"{display}: expected one primary Windows download action, "
            f"found {len(primary_actions)}"
        )
        return errors

    primary, primary_attributes = primary_actions[0]
    if primary_attributes.get("href") != "#download-verify-run":
        errors.append(
            f"{display}: primary Windows download action must lead to "
            "#download-verify-run before downloading the unsigned installer"
        )
    primary_label = html.unescape(re.sub(r"<[^>]+>", "", primary.group("label"))).strip()
    if primary_label != "Review requirements and download":
        errors.append(
            f"{display}: primary Windows action must be named "
            "'Review requirements and download'"
        )

    required_before_action = (
        "<strong>Check your language before downloading.</strong>",
        "<strong>This prerelease is not code-signed.</strong>",
    )
    for marker in required_before_action:
        position = contents.find(marker)
        if position < 0 or position > primary.start():
            errors.append(
                f"{display}: Windows compatibility and signing warnings must "
                f"precede the primary download action; missing or late {marker!r}"
            )

    guide_position = contents.find('id="download-verify-run"')
    installer_href = re.compile(
        r"^https://github\.com/rcourtman/presspeech/releases/download/"
        r"windows-v[^\"/]+/Presspeech-Setup-[^\"]+-x64\.exe$"
    )
    visible_downloads = [
        match
        for match, attributes in anchors
        if installer_href.fullmatch(attributes.get("href", ""))
    ]
    if guide_position < 0:
        errors.append(f"{display}: missing #download-verify-run guidance target")
    if not visible_downloads:
        errors.append(f"{display}: verified install steps contain no installer link")
    elif guide_position >= 0 and any(match.start() < guide_position for match in visible_downloads):
        errors.append(
            f"{display}: visible installer links must follow #download-verify-run guidance"
        )
    return errors


def check_windows_language_guidance(
    surfaces: dict[Path, tuple[str, ...]] = WINDOWS_LANGUAGE_GUIDANCE,
) -> list[str]:
    errors: list[str] = []
    for path, required in surfaces.items():
        display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
        if not path.exists():
            errors.append(f"{display}: missing Windows language guidance")
            continue
        contents = " ".join(read_text(path).split()).casefold()
        missing = [phrase for phrase in required if phrase.casefold() not in contents]
        if missing:
            errors.append(
                f"{display}: incomplete Windows model-language guidance — "
                f"missing {', '.join(repr(phrase) for phrase in missing)}"
            )
    return errors


def check_clipboard_service_guidance(
    surfaces: dict[Path, tuple[str, ...]] = CLIPBOARD_SERVICE_GUIDANCE,
) -> list[str]:
    errors: list[str] = []
    for path, required in surfaces.items():
        display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
        if not path.exists():
            errors.append(f"{display}: missing clipboard privacy guidance")
            continue
        contents = " ".join(read_text(path).split())
        missing = [phrase for phrase in required if phrase not in contents]
        if missing:
            errors.append(
                f"{display}: incomplete operating-system clipboard guidance — "
                f"missing {', '.join(repr(phrase) for phrase in missing)}"
            )
    return errors


def check_windows_model_download_privacy_guidance(
    surfaces: dict[Path, tuple[str, ...]] = WINDOWS_MODEL_DOWNLOAD_PRIVACY_GUIDANCE,
) -> list[str]:
    errors: list[str] = []
    for path, required in surfaces.items():
        display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
        if not path.exists():
            errors.append(f"{display}: missing Windows model-download privacy disclosure")
            continue
        contents = " ".join(read_text(path).split()).casefold()
        missing = [phrase for phrase in required if phrase.casefold() not in contents]
        if missing:
            errors.append(
                f"{display}: incomplete version-scoped Windows model-download privacy "
                "guidance — missing "
                + ", ".join(repr(phrase) for phrase in missing)
            )
    return errors


def check_windows_model_download_privacy_scopes(
    surfaces: tuple[Path, ...] = WINDOWS_MODEL_DOWNLOAD_PRIVACY_SCOPE_SURFACES,
) -> list[str]:
    """Require explicit same-sentence version scoping for authentication claims."""
    errors: list[str] = []
    for path in surfaces:
        display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
        if not path.exists():
            errors.append(f"{display}: missing Windows download privacy scope")
            continue
        contents = read_text(path)
        if path.name == "network-calls.json":
            try:
                inventory = json.loads(contents)
            except json.JSONDecodeError as exc:
                errors.append(f"{display}: invalid network inventory: {exc.msg}")
                continue
            if not isinstance(inventory, dict):
                errors.append(f"{display}: invalid network inventory structure")
                continue
            calls = inventory.get("network_calls", [])
            model_call = next(
                (call for call in calls
                 if isinstance(call, dict)
                 and call.get("name") == "windows_first_launch_model_download"),
                None,
            )
            if not isinstance(model_call, dict) or not isinstance(model_call.get("data_sent"), str):
                errors.append(f"{display}: missing Windows model-download privacy detail")
                continue
            contents = model_call["data_sent"]
        elif path.suffix.lower() == ".html":
            contents = html.unescape(re.sub(r"<[^>]*>", " ", contents))

        # Protect version separators before sentence splitting.
        contents = re.sub(r"\b0\.1\.12\b", "publishedbuild", contents, flags=re.I)
        contents = re.sub(r"\b0\.1\.13\b", "candidatebuild", contents, flags=re.I)
        sentences = re.split(r"(?<=[.!?])\s+", " ".join(contents.casefold().split()))
        auth_enabled = [
            sentence for sentence in sentences
            if "leaves implicit authentication enabled" in sentence
        ]
        if not any("publishedbuild" in sentence for sentence in auth_enabled):
            errors.append(
                f"{display}: implicit authentication must be explicitly attributed "
                "to published Windows 0.1.12"
            )
        if any("publishedbuild" not in sentence for sentence in auth_enabled):
            errors.append(
                f"{display}: implicit-authentication exposure has an ambiguous or "
                "incorrect version subject"
            )
        if not any(
            "candidatebuild" in sentence
            and re.search(r"\bdisables\b.{0,120}\bimplicit authentication\b", sentence)
            for sentence in sentences
        ):
            errors.append(
                f"{display}: candidate Windows 0.1.13's authentication opt-out "
                "must be explicitly version-scoped"
            )
    return errors


def check_mac_model_download_guidance(
    surfaces: dict[Path, tuple[str, ...]] = MAC_MODEL_DOWNLOAD_GUIDANCE,
) -> list[str]:
    errors: list[str] = []
    for path, required in surfaces.items():
        display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
        if not path.exists():
            errors.append(f"{display}: missing macOS model-download version guidance")
            continue
        contents = " ".join(read_text(path).split()).casefold()
        missing = [phrase for phrase in required if phrase.casefold() not in contents]
        if missing:
            errors.append(
                f"{display}: incomplete macOS model-download version guidance — missing "
                + ", ".join(repr(phrase) for phrase in missing)
            )
    return errors


def check_windows_model_download_privacy_summary(
    surfaces: dict[Path, tuple[str, ...]] = WINDOWS_MODEL_DOWNLOAD_PRIVACY_SUMMARY,
) -> list[str]:
    errors: list[str] = []
    for path, required in surfaces.items():
        display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
        if not path.exists():
            errors.append(f"{display}: missing Windows model-download privacy summary")
            continue
        contents = " ".join(read_text(path).split()).casefold()
        missing = [phrase for phrase in required if phrase.casefold() not in contents]
        if missing:
            errors.append(
                f"{display}: incomplete decision-oriented Windows model-download privacy "
                "summary — missing "
                + ", ".join(repr(phrase) for phrase in missing)
            )
    return errors


def check_faq_install_privacy_order(path: Path = DOCS / "faq.html") -> list[str]:
    """Keep the FAQ's release warnings ahead of every install action."""
    display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
    if not path.exists():
        return [f"{display}: missing FAQ install privacy warning"]

    contents = read_text(path)
    start = contents.find("<h3>How do I install it?</h3>")
    end = contents.find("</article>", start)
    if start < 0 or end < 0:
        return [f"{display}: missing FAQ install answer"]
    answer = contents[start:end]
    rules = (
        (
            'id="faq-macos-install-privacy"',
            (
                "releases/latest/download/Presspeech.zip",
                "brew install --cask rcourtman/presspeech/presspeech",
            ),
            "macOS",
        ),
        (
            'id="faq-windows-install-privacy"',
            ("windows.html#download-verify-run",),
            "Windows",
        ),
    )
    errors: list[str] = []
    for warning, actions, platform in rules:
        warning_position = answer.find(warning)
        action_positions = [answer.find(action) for action in actions]
        if warning_position < 0 or any(
            position < 0 or warning_position > position
            for position in action_positions
        ):
            errors.append(
                f"{display}: {platform} model-download privacy warning must "
                "precede its install action"
            )
    return errors


def check_macos_model_download_privacy_summary(
    surfaces: dict[Path, tuple[str, ...]] = MAC_MODEL_DOWNLOAD_PRIVACY_SUMMARY,
    install_page: Path = DOCS / "install.html",
) -> list[str]:
    errors: list[str] = []
    for path, required in surfaces.items():
        display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
        if not path.exists():
            errors.append(f"{display}: missing macOS model-download privacy summary")
            continue
        contents = " ".join(read_text(path).split()).casefold()
        missing = [phrase for phrase in required if phrase.casefold() not in contents]
        if missing:
            errors.append(
                f"{display}: incomplete decision-oriented macOS model-download privacy "
                "guidance — missing "
                + ", ".join(repr(phrase) for phrase in missing)
            )

        if path == install_page:
            raw = read_text(path)
            warning_position = raw.find('id="model-download-privacy"')
            download_position = raw.find(
                'class="button" href="https://github.com/rcourtman/presspeech/'
                'releases/download/v'
            )
            if warning_position < 0 or download_position < 0 or warning_position > download_position:
                errors.append(
                    f"{display}: the macOS model-download privacy decision must "
                    "precede the direct download action"
                )
    return errors


def check_delivery_boundary_guidance(
    surfaces: dict[Path, tuple[str, ...]] = DELIVERY_BOUNDARY_GUIDANCE,
) -> list[str]:
    errors: list[str] = []
    for path, required in surfaces.items():
        display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
        if not path.exists():
            errors.append(f"{display}: missing delivery-boundary guidance")
            continue
        contents = " ".join(read_text(path).split())
        missing = [phrase for phrase in required if phrase not in contents]
        if missing:
            errors.append(
                f"{display}: incomplete focus-safe delivery guidance — "
                f"missing {', '.join(repr(phrase) for phrase in missing)}"
            )
    return errors


def check_compatibility_evidence_guidance(
    surfaces: dict[Path, tuple[str, ...]] = COMPATIBILITY_EVIDENCE_GUIDANCE,
) -> list[str]:
    errors: list[str] = []
    for path, required in surfaces.items():
        display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
        if not path.exists():
            errors.append(f"{display}: missing compatibility-evidence guidance")
            continue
        contents = " ".join(read_text(path).split())
        missing = [phrase for phrase in required if phrase not in contents]
        if missing:
            errors.append(
                f"{display}: incomplete compatibility-evidence guidance — "
                f"missing {', '.join(repr(phrase) for phrase in missing)}"
            )
    return errors


def check_compatibility_worksheet_contract(
    page_path: Path = COMPATIBILITY_WORKSHEET_PAGE,
    script_path: Path = COMPATIBILITY_WORKSHEET_SCRIPT,
) -> list[str]:
    errors: list[str] = []
    for path in (page_path, script_path):
        if not path.exists():
            display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
            errors.append(f"{display}: missing compatibility worksheet surface")
    if errors:
        return errors

    page = read_text(page_path)
    script = read_text(script_path)
    if '<script src="compatibility-worksheet.js" defer></script>' not in page:
        errors.append("docs/app-compatibility.html: worksheet script must be local and deferred")

    form_match = re.search(
        r'<form\b(?=[^>]*\bid="compatibility-worksheet")[^>]*>(?P<body>.*?)</form>',
        page,
        flags=re.S,
    )
    if form_match is None:
        errors.append("docs/app-compatibility.html: missing compatibility worksheet form")
    else:
        form = form_match.group(0)
        if re.search(r"<form\b[^>]*\b(?:action|method)\s*=", form, flags=re.I):
            errors.append("docs/app-compatibility.html: worksheet form must not submit")
        inputs = re.findall(r"<input\b[^>]*>", form, flags=re.I)
        if any(not re.search(r'\btype="radio"', tag, flags=re.I) for tag in inputs):
            errors.append("docs/app-compatibility.html: worksheet accepts radio categories only")
        options = [
            (name.group(1), value.group(1))
            for tag in inputs
            if (name := re.search(r'\bname="([^"]+)"', tag, flags=re.I))
            and (value := re.search(r'\bvalue="([^"]+)"', tag, flags=re.I))
        ]
        expected_options = [
            *(
                (f"steady-{index}", outcome)
                for index in range(1, 6)
                for outcome in ("pasted", "recovered", "unsafe")
            ),
            *(
                (f"focus-{index}", outcome)
                for index in range(1, 4)
                for outcome in ("copied", "inserted", "other")
            ),
        ]
        if (
            len(inputs) != len(expected_options)
            or sorted(options) != sorted(expected_options)
        ):
            errors.append(
                "docs/app-compatibility.html: worksheet must expose three categories "
                "for each of five steady and three focus-change attempts"
            )
        textareas = re.findall(r"<textarea\b[^>]*>", form, flags=re.I)
        summary_match = (
            re.search(r'\bid="worksheet-summary"', textareas[0], flags=re.I)
            if len(textareas) == 1
            else None
        )
        if (
            summary_match is None
            or not re.search(r"\breadonly\b", textareas[0], flags=re.I)
        ):
            errors.append(
                "docs/app-compatibility.html: worksheet must contain only one "
                "readonly report summary textarea"
            )
        if re.search(r"<select\b", form, flags=re.I) or re.search(
            r"\bcontenteditable\b", form, flags=re.I
        ):
            errors.append(
                "docs/app-compatibility.html: worksheet must not accept free text"
            )
        if re.search(r'<button\b(?![^>]*\btype="(?:button|reset)")[^>]*>', form, flags=re.I):
            errors.append("docs/app-compatibility.html: worksheet buttons must not submit")
        report_actions = re.search(
            r'<div\b(?=[^>]*\bid="worksheet-report-actions")(?=[^>]*\bhidden\b)[^>]*>'
            r'(?P<body>.*?)</div>',
            form,
            flags=re.I | re.S,
        )
        if report_actions is None:
            errors.append(
                "docs/app-compatibility.html: completed worksheet must reveal "
                "report actions"
            )
        else:
            actions = report_actions.group("body")
            browse_position = actions.find("issues?q=")
            new_position = actions.find("issues/new?template=compatibility_report.yml")
            if browse_position < 0 or new_position < 0 or browse_position > new_position:
                errors.append(
                    "docs/app-compatibility.html: worksheet handoff must check "
                    "matching reports before opening a new report"
                )
            if "Check new-report availability" not in actions:
                errors.append(
                    "docs/app-compatibility.html: new-report link must not imply "
                    "that issue creation is currently available"
                )
        save_button = re.search(
            r'<button\b(?=[^>]*\bid="save-worksheet-summary")(?=[^>]*\btype="button")'
            r'(?=[^>]*\bdisabled\b)[^>]*>Download report draft</button>',
            form,
            flags=re.I,
        )
        if save_button is None:
            errors.append(
                "docs/app-compatibility.html: aggregate download must be a disabled "
                "worksheet button"
            )

    for pattern, label in COMPATIBILITY_WORKSHEET_FORBIDDEN:
        if re.search(pattern, script):
            errors.append(
                "docs/compatibility-worksheet.js: local worksheet must not use " + label
            )
    missing_results = [
        result for result in COMPATIBILITY_OVERALL_RESULTS if result not in script
    ]
    if missing_results:
        errors.append(
            "docs/compatibility-worksheet.js: worksheet result labels must match "
            "the compatibility report form"
        )
    if "`Overall result: ${result.overall}`" not in script:
        errors.append(
            "docs/compatibility-worksheet.js: copied worksheet summary must include "
            "the canonical overall result"
        )
    if "reportActions.hidden = !result.complete" not in script:
        errors.append(
            "docs/compatibility-worksheet.js: report actions must remain hidden "
            "until all outcomes are complete"
        )
    if "save.disabled = !result.complete" not in script:
        errors.append(
            "docs/compatibility-worksheet.js: report download must remain disabled "
            "until all outcomes are complete"
        )
    if (
        'save.addEventListener("click", saveSummary)' not in script
        or "new Blob([`${formatReportDraft(summary.value)}\\n`]" not in script
        or 'link.download = "presspeech-compatibility-report-draft.txt"' not in script
        or "URL.createObjectURL(file)" not in script
        or "function formatReportDraft(summaryText)" not in script
        or '"Platform (macOS or Windows):"' not in script
        or '"Target app and public version:"' not in script
        or '"Generic field type:"' not in script
    ):
        errors.append(
            "docs/compatibility-worksheet.js: report download must save aggregate "
            "counts with blank context prompts in a named text file"
        )
    return errors


def check_command_shell_guidance(
    surfaces: dict[Path, tuple[str, ...]] = COMMAND_SHELL_GUIDANCE,
) -> list[str]:
    errors: list[str] = []
    for path, required in surfaces.items():
        display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
        if not path.exists():
            errors.append(f"{display}: missing command-shell safety guidance")
            continue
        contents = " ".join(read_text(path).split())
        missing = [phrase for phrase in required if phrase not in contents]
        if missing:
            errors.append(
                f"{display}: incomplete command-shell safety guidance — "
                f"missing {', '.join(repr(phrase) for phrase in missing)}"
            )
    return errors


def check_repository_install_guidance(
    required_surfaces: dict[Path, tuple[str, ...]] = REPOSITORY_INSTALL_GUIDANCE,
    forbidden_surfaces: dict[
        Path, tuple[re.Pattern[str], ...]
    ] = REPOSITORY_UNPUBLISHED_DOWNLOAD_PATTERNS,
) -> list[str]:
    errors: list[str] = []
    for path, required in required_surfaces.items():
        display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
        if not path.exists():
            errors.append(f"{display}: missing release-safe install guidance")
            continue
        contents = " ".join(read_text(path).split())
        missing = [phrase for phrase in required if phrase not in contents]
        if missing:
            errors.append(
                f"{display}: incomplete release-safe install guidance — "
                f"missing {', '.join(repr(phrase) for phrase in missing)}"
            )
    for path, patterns in forbidden_surfaces.items():
        display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
        if not path.exists():
            continue
        contents = read_text(path)
        for pattern in patterns:
            if pattern.search(contents):
                errors.append(
                    f"{display}: repository install entry point advertises "
                    "release-specific metadata before publication"
                )
    return errors


def expected_files(metadata: dict[str, object]) -> dict[Path, str]:
    expected: dict[Path, str] = {}
    for path, syncer in SYNCERS.items():
        if path == DOCS / "sitemap.xml":
            continue
        expected[path] = syncer(path, metadata)
    sitemap_metadata = dict(metadata)
    sitemap_metadata["_changed_public_paths"] = [
        path
        for path, want in expected.items()
        if path.is_relative_to(DOCS)
        and (not path.exists() or read_text(path) != want)
    ]
    expected[DOCS / "sitemap.xml"] = sync_sitemap(
        DOCS / "sitemap.xml", sitemap_metadata
    )
    expected[METADATA_PATH] = metadata_text(metadata)
    return expected


def stale_copy_errors(paths: list[Path]) -> list[str]:
    errors: list[str] = []
    for path in paths:
        if not path.exists() or path.suffix not in PUBLIC_RELEASE_SUFFIXES:
            continue
        text = read_text(path)
        for pattern, label in STALE_PATTERNS:
            if pattern.search(text):
                display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
                errors.append(f"{display}: stale copy found ({label})")
    return errors


def check_mac_release_phase_copy(
    metadata: dict[str, object], paths: list[Path] | None = None
) -> list[str]:
    """Reject prose that becomes false as soon as the configured Mac release ships.

    The release workflow publishes artifacts from an already-green main commit;
    it does not make a second source commit just to change "upcoming" to
    "released". Public copy therefore needs to describe configured Mac behavior
    without depending on which side of that publication event it is read.
    """
    version = str(metadata["version"])
    separator = r"(?:\s|[*_`]|<[^>]+>)*"
    patterns = (
        (
            re.compile(
                rf"\bupcoming(?:\s+macOS)?{separator}{re.escape(version)}\b",
                re.IGNORECASE,
            ),
            f"configured macOS {version} is still called upcoming",
        ),
        (
            re.compile(
                rf"\b{re.escape(version)}{separator}(?:release\s+)?candidate\b",
                re.IGNORECASE,
            ),
            f"configured macOS {version} is still called a candidate",
        ),
        (
            re.compile(
                rf"\bcurrently\s+published(?:\s+macOS)?{separator}"
                r"v?\d+\.\d+\.\d+\b",
                re.IGNORECASE,
            ),
            "phase-bound current-published wording",
        ),
        (
            re.compile(
                rf"\bpublished\s+macOS(?:\s+release)?(?:\s+remains)?{separator}"
                r"v?\d+\.\d+\.\d+\b",
                re.IGNORECASE,
            ),
            "phase-bound published-macOS wording",
        ),
    )
    errors: list[str] = []
    for path in paths if paths is not None else public_release_paths():
        if not path.exists():
            continue
        text = read_text(path)
        matches = [label for pattern, label in patterns if pattern.search(text)]
        if not matches:
            continue
        display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
        errors.append(
            f"{display}: release-phase copy will become stale — "
            + ", ".join(matches)
        )
    return errors


def check_windows_release_phase_copy(
    metadata: dict[str, object], paths: list[Path] | None = None
) -> list[str]:
    """Reject Windows copy that becomes false when the configured build ships."""
    version = str(metadata["windows_version"])
    separator = r"(?:\s|[*_`]|<[^>]+>)*"
    patterns = (
        (
            re.compile(
                rf"\bupcoming{separator}Windows{separator}{re.escape(version)}\b",
                re.IGNORECASE,
            ),
            f"configured Windows {version} is still called upcoming",
        ),
        (
            re.compile(
                rf"\bWindows{separator}{re.escape(version)}"
                rf"{separator}(?:release\s+)?candidate\b",
                re.IGNORECASE,
            ),
            f"configured Windows {version} is still called a candidate",
        ),
        (
            re.compile(
                rf"\bcurrently\s+published{separator}Windows{separator}"
                r"v?\d+\.\d+\.\d+\b",
                re.IGNORECASE,
            ),
            "phase-bound current-published Windows wording",
        ),
        (
            re.compile(
                rf"\bpublished{separator}Windows{separator}release"
                rf"{separator}remains{separator}v?\d+\.\d+\.\d+\b",
                re.IGNORECASE,
            ),
            "phase-bound published-Windows wording",
        ),
    )
    errors: list[str] = []
    for path in paths if paths is not None else public_release_paths():
        if not path.exists():
            continue
        text = read_text(path)
        matches = [label for pattern, label in patterns if pattern.search(text)]
        if not matches:
            continue
        display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
        errors.append(
            f"{display}: release-phase copy will become stale — "
            + ", ".join(matches)
        )
    return errors


def public_release_paths(
    roots: list[Path] = PUBLIC_RELEASE_ROOTS,
    files: list[Path] = PUBLIC_RELEASE_FILES,
) -> list[Path]:
    paths = set(files)
    for root in roots:
        paths.update(
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix in PUBLIC_RELEASE_SUFFIXES
        )
    return sorted(paths)


def check_windows_release_references(
    metadata: dict[str, object], paths: list[Path] | None = None
) -> list[str]:
    expected = str(metadata["windows_version"])
    errors: list[str] = []
    for path in paths if paths is not None else public_release_paths():
        if not path.exists():
            continue
        text = read_text(path)
        stale = {
            (label, match.group("version"))
            for pattern, label in WINDOWS_RELEASE_REFERENCE_PATTERNS
            for match in pattern.finditer(text)
            if match.group("version") != expected
        }
        if not stale:
            continue
        display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
        details = ", ".join(
            f"{label} {version}" for label, version in sorted(stale)
        )
        errors.append(
            f"{display}: stale Windows public release reference(s): {details}; "
            f"expected {expected}"
        )
    return errors


def check_compare_freshness(
    today: date | None = None, compare_dir: Path = COMPARE_DIR
) -> list[str]:
    today = today or date.today()
    errors: list[str] = []
    for path in sorted(compare_dir.glob("*.html")):
        text = read_text(path)
        stamps = COMPARE_CHECKED_RE.findall(text)
        if not stamps:
            errors.append(
                f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name}: "
                "competitor claims carry no exact "
                "'checked <day> <Month> <Year>' stamp"
            )
            continue
        checked_dates: list[date] = []
        for day, month, year in stamps:
            try:
                checked_dates.append(
                    date(int(year), MONTH_NUMBERS[month], int(day))
                )
            except ValueError:
                errors.append(
                    f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name}: "
                    f"invalid competitor-check date {day} {month} {year}"
                )
        if not checked_dates:
            continue
        oldest = min(checked_dates)
        newest = max(checked_dates)
        if newest > today:
            errors.append(
                f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name}: "
                "competitor claims have a future check date, "
                f"{newest.day} {newest.strftime('%B %Y')}"
            )
        if (today - oldest).days > COMPARE_MAX_AGE_DAYS:
            errors.append(
                f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name}: "
                f"competitor claims last checked {oldest.day} "
                f"{oldest.strftime('%B %Y')}, more than "
                f"{COMPARE_MAX_AGE_DAYS} days ago — re-verify against the cited sources "
                "and update the stamp"
            )
    return errors


def check_install_prompt_sync(metadata: dict[str, object]) -> list[str]:
    errors: list[str] = []
    required_windows_provenance = (
        "gh release verify $tag --repo rcourtman/presspeech",
        "gh release verify-asset $tag $installer --repo rcourtman/presspeech",
        "do not inspect or display credentials",
        "If the user agrees to the check and either command fails, stop",
        "user explicitly chooses checksum-only trust",
        "never launch without explicit confirmation",
    )
    missing_windows_provenance = [
        phrase for phrase in required_windows_provenance
        if phrase not in WINDOWS_INSTALL_PROMPT
    ]
    if missing_windows_provenance:
        errors.append(
            "Windows assistant prompt: optional attestation verification must "
            "remain explicit and credential-safe; missing "
            + ", ".join(repr(phrase) for phrase in missing_windows_provenance)
        )

    agents = read_text(DOCS / "install" / "agents.md")
    if agents != agents_markdown(metadata):
        errors.append("docs/install/agents.md: canonical install prompts are out of sync")

    install_html = read_text(DOCS / "install.html")
    escaped_prompt = html.escape(MAC_INSTALL_PROMPT, quote=False)
    if escaped_prompt not in install_html:
        errors.append("docs/install.html: embedded macOS install prompt is out of sync")
    windows_html = read_text(DOCS / "windows.html")
    escaped_windows_prompt = html.escape(WINDOWS_INSTALL_PROMPT, quote=False)
    if escaped_windows_prompt not in windows_html:
        errors.append("docs/windows.html: embedded Windows install prompt is out of sync")
    return errors


def diff_text(path: Path, current: str, expected: str) -> str:
    return "".join(
        difflib.unified_diff(
            current.splitlines(keepends=True),
            expected.splitlines(keepends=True),
            fromfile=f"{path.relative_to(ROOT)} (current)",
            tofile=f"{path.relative_to(ROOT)} (expected)",
        )
    )


def run_self_test() -> None:
    metadata: dict[str, object] = {
        "last_updated": "2026-01-02",
        "version": "8.7.6",
        "windows_version": "9.8.7",
        "release_zip_sha256": "a" * 64,
        "release_zip_size": "7.6 MB",
    }
    with tempfile.TemporaryDirectory() as tmp:
        release_zip = Path(tmp) / "Presspeech.zip"
        release_zip.write_bytes(b"release fixture\n")
        generated_metadata = build_metadata(
            argparse.Namespace(
                release_zip=str(release_zip), date="2026-01-02", check=False
            )
        )
        expected_digest = hashlib.sha256(release_zip.read_bytes()).hexdigest()
        if (
            generated_metadata["release_zip_bytes"] != release_zip.stat().st_size
            or generated_metadata["release_zip_sha256"] != expected_digest
        ):
            raise SyncError("self-test: release archive size or SHA-256 did not sync")

        stale_svg = Path(tmp) / "stale.svg"
        stale_svg.write_text(
            "<tspan>7.6 MB </tspan><tspan>80 MB</tspan><tspan>17.6 MB</tspan>",
            encoding="utf-8",
        )
        current_svg = Path(tmp) / "current.svg"
        current_svg.write_text("<tspan>7.7 MB</tspan>", encoding="utf-8")
        sync_icon_stats("7.6 MB", "7.7 MB", [stale_svg, current_svg])
        if stale_svg.read_text(encoding="utf-8") != (
            "<tspan>7.7 MB </tspan><tspan>80 MB</tspan><tspan>17.6 MB</tspan>"
        ):
            raise SyncError("self-test: designed-asset size caption did not sync")
        if current_svg.read_text(encoding="utf-8") != "<tspan>7.7 MB</tspan>":
            raise SyncError("self-test: current designed-asset caption was rewritten")

        legacy_llms = Path(tmp) / "legacy-llms.txt"
        legacy_llms.write_text(
            "- Release size: about 1 MB signed zip; model cache is about 600 MB on first launch.\n"
            "- Homebrew install: `brew install --cask rcourtman/presspeech/presspeech`.\n"
            "- Install: https://rcourtman.github.io/presspeech/install.html\n"
            "- Windows install: https://rcourtman.github.io/presspeech/windows.html\n"
            "- Privacy: no cloud transcription, no telemetry, no transcript persistence.\n"
            "\nBest short answer:\n"
            "Presspeech is private push-to-talk dictation for Apple Silicon Macs and Windows PCs. "
            "Each platform transcribes locally, normally pastes text at the cursor, and copies it for "
            "manual paste when the original destination cannot be verified. "
            f"{OLD_LLMS_PRIVACY_SUMMARY}\n",
            encoding="utf-8",
        )
        synced_llms = sync_llms(legacy_llms, metadata)
        if (
            OLD_LLMS_PRIVACY_SUMMARY in synced_llms
            or "random per-process session ID" not in synced_llms
            or "macOS 0.3.8 may attach an inherited Hugging Face token" not in synced_llms
            or "wait until macOS 0.3.9 is published" not in synced_llms
            or "leave a working model cache in place" not in synced_llms
            or "macos-0-3-8-after-use" not in synced_llms
        ):
            raise SyncError("self-test: inaccurate llms privacy claim was not corrected")
        legacy_llms.write_text(synced_llms, encoding="utf-8")
        if sync_llms(legacy_llms, metadata) != synced_llms:
            raise SyncError("self-test: llms privacy correction is not idempotent")

        index_page = Path(tmp) / "index.html"
        index_page.write_text(
            '"@id": "https://rcourtman.github.io/presspeech/#webpage"\n'
            '"dateModified": "2025-12-29"\n'
            '"@id": "https://rcourtman.github.io/presspeech/#software"\n'
            '"softwareVersion": "1.2.3"\n'
            '"installUrl": "https://example.com/old-mac"\n'
            '"storageRequirements": "1 MB old cache"\n'
            '"@id": "https://rcourtman.github.io/presspeech/windows.html#software"\n'
            '"softwareVersion": "2.3.4"\n'
            '"releaseNotes": "https://example.com/old-windows-notes"\n'
            '"downloadUrl": "https://example.com/old-windows-installer"\n'
            '<div class="stat"><strong>1.0 MB</strong><span>signed release zip</span></div>\n'
            '<p class="quiet"><strong>macOS:</strong> released. '
            '<strong>Windows:</strong> prerelease.</p>\n'
            '<p class="quiet" data-release-status>stale</p>\n'
            f"{SETUP_CHECKLIST} Copy Diagnostics Save Diagnostics\n",
            encoding="utf-8",
        )
        synced_index = sync_index(index_page, metadata)
        for expected in (
            '"softwareVersion": "8.7.6"',
            '"softwareVersion": "9.8.7"',
            "windows-v9.8.7/Presspeech-Setup-9.8.7-x64.exe",
            "<strong>7.6 MB</strong>",
            "<strong>macOS 8.7.6:</strong>",
            "<strong>Windows 9.8.7:</strong>",
            '"dateModified": "2026-01-02"',
            'datetime="2026-01-02">2 January 2026</time>',
        ):
            if expected not in synced_index:
                raise SyncError(f"self-test: homepage metadata did not sync {expected!r}")

        getting_started = Path(tmp) / "getting-started.html"
        getting_started.write_text(
            "<h3>macOS</h3>\n<h3>Windows prerelease</h3>\n",
            encoding="utf-8",
        )
        synced_getting_started = sync_getting_started(getting_started, metadata)
        if (
            "<h3>macOS 8.7.6</h3>" not in synced_getting_started
            or "<h3>Windows 9.8.7 prerelease</h3>" not in synced_getting_started
        ):
            raise SyncError("self-test: getting-started release versions did not sync")

        install_page = Path(tmp) / "install.html"
        install_page.write_text(read_text(DOCS / "install.html"), encoding="utf-8")
        synced_install = sync_install_html(install_page, metadata)
        if (
            "gh release verify v8.7.6 --repo rcourtman/presspeech" not in synced_install
            or "gh release verify-asset v8.7.6 ~/Downloads/Presspeech.zip"
            not in synced_install
            or "gh release verify v0.3.8" in synced_install
            or "First model download" not in synced_install
        ):
            raise SyncError("self-test: install verification commands did not follow release metadata")
        install_page.write_text(synced_install, encoding="utf-8")
        if sync_install_html(install_page, metadata) != synced_install:
            raise SyncError("self-test: install guidance sync was not idempotent")

        sitemap = Path(tmp) / "sitemap.xml"
        sitemap.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            "  <url>\n"
            "    <loc>https://rcourtman.github.io/presspeech/</loc>\n"
            "    <lastmod>2025-12-30</lastmod>\n"
            "  </url>\n"
            "  <url>\n"
            "    <loc>https://rcourtman.github.io/presspeech/faq.html</loc>\n"
            "    <lastmod>2025-12-31</lastmod>\n"
            "  </url>\n"
            "</urlset>\n",
            encoding="utf-8",
        )
        sitemap_metadata = dict(metadata)
        sitemap_metadata["_changed_public_paths"] = [DOCS / "index.html"]
        updated = sync_sitemap(sitemap, sitemap_metadata)
        if (
            updated.count("<lastmod>2026-01-02</lastmod>") != 1
            or "<lastmod>2025-12-31</lastmod>" not in updated
        ):
            raise SyncError("self-test: sitemap did not update only the changed resource")

        sitemap.write_text("<urlset></urlset>\n", encoding="utf-8")
        try:
            sync_sitemap(sitemap, metadata)
        except SyncError:
            pass
        else:
            raise SyncError("self-test: sitemap without <lastmod> entries did not fail loudly")

        windows_page = Path(tmp) / "windows.html"
        windows_page.write_text(
            '"softwareVersion": "1.2.3"\n'
            '"dateModified": "2025-12-30"\n'
            'windows-v1.2.3\n'
            'Presspeech-Setup-1.2.3-x64.exe\n'
            '<p class="quiet" data-release-status>stale</p>\n'
            '<pre><code>Install Presspeech from https://github.com/rcourtman/presspeech '
            'on this Windows PC.\nold prompt</code></pre>\n',
            encoding="utf-8",
        )
        synced_windows_page = sync_windows_html(windows_page, metadata)
        if (
            "1.2.3" in synced_windows_page
            or synced_windows_page.count("9.8.7") != 4
            or "Smart App Control or managed policy" not in synced_windows_page
        ):
            raise SyncError("self-test: Windows page release references were not all synced")

        synced_agents = agents_markdown(metadata)
        if (
            MAC_INSTALL_PROMPT not in synced_agents
            or WINDOWS_INSTALL_PROMPT not in synced_agents
            or "site-metadata.json" not in synced_agents
            or "9.8.7" in synced_agents
            or "Do not inspect or display token values" not in MAC_INSTALL_PROMPT
            or "launch 0.3.8 without the user's informed choice" not in MAC_INSTALL_PROMPT
            or (
                "gh release verify $tag --repo rcourtman/presspeech"
                not in WINDOWS_INSTALL_PROMPT
            )
            or (
                "gh release verify-asset $tag $installer --repo rcourtman/presspeech"
                not in WINDOWS_INSTALL_PROMPT
            )
            or "do not inspect or display credentials" not in WINDOWS_INSTALL_PROMPT
            or (
                "If the user agrees to the check and either command fails, stop"
                not in WINDOWS_INSTALL_PROMPT
            )
        ):
            raise SyncError("self-test: cross-platform assistant prompts were not generated")

        windows_readme = Path(tmp) / "windows-readme.md"
        windows_readme.write_text(
            "Use windows.html#download-verify-run; the source tree can be ahead "
            "of the published prerelease.\n"
            "  -Version 1.2.3 -Python .\\python.exe\n",
            encoding="utf-8",
        )
        synced_windows_readme = sync_windows_readme(windows_readme, metadata)
        if (
            "1.2.3" in synced_windows_readme
            or synced_windows_readme.count("9.8.7") != 1
            or "windows.html#download-verify-run" not in synced_windows_readme
        ):
            raise SyncError(
                "self-test: Windows README build version did not sync safely"
            )

        release_safe_readme = Path(tmp) / "README.md"
        release_safe_readme.write_text(
            "The `main` branch can contain an unreleased candidate.\n"
            "https://github.com/rcourtman/presspeech/releases/latest/download/Presspeech.zip\n"
            "https://rcourtman.github.io/presspeech/install.html#direct-download\n"
            "https://rcourtman.github.io/presspeech/windows.html#download-verify-run\n"
            "**1.0 MB release zip**\n"
            "- **Copy Diagnostics** — old summary\n",
            encoding="utf-8",
        )
        synced_release_safe_readme = sync_readme(release_safe_readme, metadata)
        if (
            "8.7.6" in synced_release_safe_readme
            or "a" * 64 in synced_release_safe_readme
            or "releases/latest/download/Presspeech.zip"
            not in synced_release_safe_readme
        ):
            raise SyncError(
                "self-test: candidate metadata leaked into the repository README"
            )

        unsafe_entrypoint = Path(tmp) / "unsafe-readme.md"
        unsafe_entrypoint.write_text(
            "https://github.com/rcourtman/presspeech/releases/download/"
            "v8.7.6/Presspeech.zip\n",
            encoding="utf-8",
        )
        safe_windows_entrypoint = Path(tmp) / "windows-readme-safe.md"
        safe_windows_entrypoint.write_text(
            "Use windows.html#download-verify-run; the source tree can be ahead "
            "of the published prerelease.\n",
            encoding="utf-8",
        )
        entrypoint_errors = check_repository_install_guidance(
            {
                release_safe_readme: (
                    "releases/latest/download/Presspeech.zip",
                    "install.html#direct-download",
                    "windows.html#download-verify-run",
                ),
                safe_windows_entrypoint: (
                    "windows.html#download-verify-run",
                    "source tree can be ahead of the published prerelease",
                ),
            },
            {
                unsafe_entrypoint: REPOSITORY_UNPUBLISHED_DOWNLOAD_PATTERNS[
                    ROOT / "README.md"
                ]
            },
        )
        if (
            len(entrypoint_errors) != 1
            or "release-specific metadata" not in entrypoint_errors[0]
        ):
            raise SyncError(
                "self-test: unsafe repository download link was not rejected"
            )

        faq = Path(tmp) / "faq.html"
        faq.write_text(
            "<p>Microphone, Accessibility, and Input Monitoring. Old macOS-only answer.</p>\n"
            "          </div>\n        </div>\n      </section>\n",
            encoding="utf-8",
        )
        synced_faq = sync_faq(faq, metadata)
        for expected in (
            "<strong>Windows:</strong> Turn on Microphone access",
            "Windows Copy Diagnostics",
            "exact microphone names, raw error details, and raw log lines",
        ):
            if expected not in synced_faq:
                raise SyncError(f"self-test: FAQ did not sync {expected!r}")

        llms_full = Path(tmp) / "llms-full.txt"
        old_diagnostics = (
            "For support, macOS Copy/Save Diagnostics and Windows Copy Diagnostics "
            "create privacy-safe local reports with bounded recent log lines.\n"
        )
        llms_full.write_text(
            "First launch downloads the default local speech model weights, about 500-600 MB, "
            "into `~/Library/Application Support/FluidAudio/`. The Parakeet model downloads "
            "only if selected.\n\n"
            "Use Setup Checklist from the Presspeech menu bar item to finish the speech model, "
            "Microphone, Accessibility, Input Monitoring, and hotkey readiness checks.\n\n"
            + old_diagnostics
            + "\nMachine-readable network surface:\n",
            encoding="utf-8",
        )
        synced_llms_full = sync_llms_full(llms_full, metadata)
        if (old_diagnostics in synced_llms_full
                or synced_llms_full.count("For support, macOS Copy/Save Diagnostics") != 1
                or "raw error details, and raw log lines" not in synced_llms_full
                or "The macOS 0.3.8 release starts its first speech-model download" not in synced_llms_full
                or "In 0.3.9, a clean install must choose Download Model" not in synced_llms_full):
            raise SyncError("self-test: llms-full diagnostics paragraph was not replaced")

        compare_dir = Path(tmp) / "compare"
        compare_dir.mkdir()
        page = compare_dir / "sample.html"
        page.write_text("<p>Sources: example (checked 1 January 2026).</p>", encoding="utf-8")
        if check_compare_freshness(today=date(2026, 3, 1), compare_dir=compare_dir):
            raise SyncError("self-test: fresh compare stamp was flagged")
        if not check_compare_freshness(today=date(2026, 4, 2), compare_dir=compare_dir):
            raise SyncError("self-test: stale compare stamp was not flagged")
        page.write_text("<p>Sources: example (checked 11 June 2026).</p>", encoding="utf-8")
        if check_compare_freshness(today=date(2026, 7, 1), compare_dir=compare_dir):
            raise SyncError("self-test: day-carrying compare stamp was not parsed")
        page.write_text("<p>Sources: example (checked June 2026).</p>", encoding="utf-8")
        if not check_compare_freshness(today=date(2026, 7, 1), compare_dir=compare_dir):
            raise SyncError("self-test: inexact compare stamp was not flagged")
        page.write_text("<p>Sources: example (checked 31 June 2026).</p>", encoding="utf-8")
        if not check_compare_freshness(today=date(2026, 7, 1), compare_dir=compare_dir):
            raise SyncError("self-test: invalid compare stamp was not flagged")
        page.write_text("<p>Sources: example (checked 2 July 2026).</p>", encoding="utf-8")
        if not check_compare_freshness(today=date(2026, 7, 1), compare_dir=compare_dir):
            raise SyncError("self-test: future compare stamp was not flagged")
        page.write_text("<p>Sources: example.</p>", encoding="utf-8")
        if not check_compare_freshness(today=date(2026, 3, 1), compare_dir=compare_dir):
            raise SyncError("self-test: missing compare stamp was not flagged")

        stale = Path(tmp) / "privacy.txt"
        stale.write_text(
            "Presspeech deliberately does not restore the previous clipboard contents.\n",
            encoding="utf-8",
        )
        if not stale_copy_errors([stale]):
            raise SyncError("self-test: stale clipboard privacy wording was not flagged")
        telemetry_copy = Path(tmp) / "telemetry.txt"
        telemetry_copy.write_text(
            "The apps ship with no account, subscription, telemetry, or cloud transcription endpoint.\n"
            "Presspeech has no analytics, event tracking, crash reporter, account system.\n"
            '"description": "There is no account, subscription, telemetry, or cloud transcription."\n',
            encoding="utf-8",
        )
        if len(stale_copy_errors([telemetry_copy])) != 3:
            raise SyncError("self-test: unqualified telemetry claims were not flagged")
        telemetry_copy.write_text(
            "Presspeech does not operate first-party analytics. Published Windows 0.1.12 "
            "does not disable bundled-library telemetry during model downloads.\n",
            encoding="utf-8",
        )
        if stale_copy_errors([telemetry_copy]):
            raise SyncError("self-test: version-scoped telemetry disclosure was rejected")
        stale_svg = Path(tmp) / "caption.svg"
        stale_svg.write_text(
            "On macOS there is no Dock icon, no preferences window.\n",
            encoding="utf-8",
        )
        if not stale_copy_errors([stale_svg]):
            raise SyncError("self-test: stale Dock-access SVG wording was not flagged")
        stale_windows = Path(tmp) / "windows-readme.txt"
        stale_windows.write_text(
            "Single-instance (named mutex) — launching twice does nothing.\n",
            encoding="utf-8",
        )
        if not stale_copy_errors([stale_windows]):
            raise SyncError("self-test: stale Windows repeat-launch wording was not flagged")
        stale_hotkey = Path(tmp) / "getting-started.html"
        stale_hotkey.write_text(
            "Hold <strong>Right Option</strong> on macOS or "
            "<strong>Right Alt</strong> on Windows.\n"
            "Hold Right Alt, speak, release, and check the result.\n",
            encoding="utf-8",
        )
        hotkey_errors = stale_copy_errors([stale_hotkey])
        if len(hotkey_errors) != 2:
            raise SyncError(
                "self-test: expected both configured-hotkey copy errors, "
                f"found {hotkey_errors!r}"
            )
        stale_checksum = Path(tmp) / "install.html"
        stale_checksum.write_text(
            "https://github.com/rcourtman/presspeech/releases/latest/download/"
            "Presspeech.zip.sha256\n",
            encoding="utf-8",
        )
        if not stale_copy_errors([stale_checksum]):
            raise SyncError("self-test: missing current-release checksum asset was not flagged")
        stale_delivery = Path(tmp) / "homepage.html"
        stale_delivery.write_text(
            "Private push-to-talk dictation into any Mac app.\n",
            encoding="utf-8",
        )
        if not stale_copy_errors([stale_delivery]):
            raise SyncError("self-test: qualified universal paste-delivery promise was not flagged")
        stale_delivery.write_text(
            "The text appears wherever your cursor already is.\n",
            encoding="utf-8",
        )
        if not stale_copy_errors([stale_delivery]):
            raise SyncError("self-test: wherever-cursor delivery promise was not flagged")
        stale_delivery.write_text("Anywhere you can type\n", encoding="utf-8")
        if not stale_copy_errors([stale_delivery]):
            raise SyncError("self-test: anywhere-you-can-type promise was not flagged")
        stale_delivery.write_text(
            "Private push-to-talk dictation at the cursor.\n",
            encoding="utf-8",
        )
        if not stale_copy_errors([stale_delivery]):
            raise SyncError("self-test: unqualified at-cursor promise was not flagged")
        stale_delivery.write_text(
            "Audio is transcribed locally, pasted, then discarded.\n",
            encoding="utf-8",
        )
        if not stale_copy_errors([stale_delivery]):
            raise SyncError("self-test: incomplete delivery pipeline was not flagged")

        stale_evidence = Path(tmp) / "claims.md"
        stale_evidence.write_text(
            "Published release-to-paste benchmark.\n"
            "The transcript pastes at the cursor in about 100 ms.\n"
            "~100 ms from key release to pasted text.\n"
            "END-TO-END LATENCY\n"
            "18 Latin/Cyrillic-script languages via Parakeet v3.\n",
            encoding="utf-8",
        )
        evidence_errors = stale_copy_errors([stale_evidence])
        expected_labels = (
            "benchmark mislabeled as release-to-paste latency",
            "model benchmark presented as complete paste latency",
            "model benchmark presented as release-triggered paste latency",
            "model-only benchmark labeled end-to-end",
            "language-hint count presented as recognition-language count",
        )
        if not all(any(label in error for error in evidence_errors) for label in expected_labels):
            raise SyncError(
                "self-test: unsupported benchmark or language claims were not all flagged: "
                f"{evidence_errors!r}"
            )

        release_reference = Path(tmp) / "new-public-page.md"
        release_reference.write_text(
            "[Download Windows 1.2.3]"
            "(https://github.com/rcourtman/presspeech/releases/download/"
            "windows-v1.2.3/Presspeech-Setup-1.2.4-x64.exe)\n",
            encoding="utf-8",
        )
        release_errors = check_windows_release_references(
            metadata, [release_reference]
        )
        if len(release_errors) != 1 or not all(
            reference in release_errors[0]
            for reference in (
                "download label 1.2.3",
                "download tag 1.2.3",
                "installer filename 1.2.4",
            )
        ):
            raise SyncError(
                "self-test: stale or mismatched Windows release references were not flagged"
            )
        release_reference.write_text(
            "[Download Windows 9.8.7]"
            "(https://github.com/rcourtman/presspeech/releases/download/"
            "windows-v9.8.7/Presspeech-Setup-9.8.7-x64.exe)\n",
            encoding="utf-8",
        )
        if check_windows_release_references(metadata, [release_reference]):
            raise SyncError("self-test: current Windows release references were rejected")

        orientation = Path(tmp) / "preview.svg"
        orientation.write_text("Mac and Windows; macOS release\n", encoding="utf-8")
        if not check_platform_orientation(
            {orientation: ("Mac and Windows", "macOS release", "Windows prerelease")}
        ):
            raise SyncError("self-test: ambiguous platform status was not flagged")
        orientation.write_text(
            "Mac and Windows; macOS release; Windows prerelease\n", encoding="utf-8"
        )
        if check_platform_orientation(
            {orientation: ("Mac and Windows", "macOS release", "Windows prerelease")}
        ):
            raise SyncError("self-test: complete platform status was rejected")

        unsigned_guidance = Path(tmp) / "windows-install.md"
        unsigned_guidance.write_text(
            "Verify SHA-256, then choose More info → Run anyway.\n",
            encoding="utf-8",
        )
        required_guidance = {
            unsigned_guidance: (
                "Smart App Control",
                "managed policy",
                "do not try to circumvent",
            )
        }
        if not check_windows_unsigned_guidance(required_guidance):
            raise SyncError("self-test: incomplete unsigned Windows guidance was not flagged")
        unsigned_guidance.write_text(
            "Smart App Control or managed policy may block the installer; "
            "do not try to circumvent that policy.\n",
            encoding="utf-8",
        )
        if check_windows_unsigned_guidance(required_guidance):
            raise SyncError("self-test: complete unsigned Windows guidance was rejected")

        verified_download = Path(tmp) / "windows-download.html"
        verified_download.write_text(
            "<strong>Check your language before downloading.</strong>\n"
            "<strong>This prerelease is not code-signed.</strong>\n"
            '<a class="button" href="#download-verify-run">'
            "Review requirements and download</a>\n"
            '<section id="download-verify-run">\n'
            '<a href="https://github.com/rcourtman/presspeech/releases/download/'
            'windows-v1.2.3/Presspeech-Setup-1.2.3-x64.exe">Installer</a>\n',
            encoding="utf-8",
        )
        if check_windows_verified_download_flow(verified_download):
            raise SyncError("self-test: verified Windows download flow was rejected")
        safe_download = verified_download.read_text(encoding="utf-8")
        early_download = safe_download.replace(
            '<section id="download-verify-run">',
            '<a data-test="early" class="button secondary" '
            'href="https://github.com/rcourtman/presspeech/releases/download/'
            'windows-v1.2.3/Presspeech-Setup-1.2.3-x64.exe">Early</a>\n'
            '<section id="download-verify-run">',
        )
        verified_download.write_text(early_download, encoding="utf-8")
        flow_errors = check_windows_verified_download_flow(verified_download)
        if not any("must follow #download-verify-run" in error for error in flow_errors):
            raise SyncError("self-test: reordered early installer link was accepted")

        unsafe_download = safe_download.replace(
            'href="#download-verify-run"',
            'href="https://github.com/rcourtman/presspeech/releases/download/'
            'windows-v1.2.3/Presspeech-Setup-1.2.3-x64.exe"',
            1,
        )
        verified_download.write_text(unsafe_download, encoding="utf-8")
        flow_errors = check_windows_verified_download_flow(verified_download)
        if not any("must lead to #download-verify-run" in error for error in flow_errors):
            raise SyncError("self-test: direct primary Windows download was accepted")

        language_guidance = Path(tmp) / "windows-language.html"
        required_language_guidance = {
            language_guidance: (
                "language and hardware split",
                "multilingual Parakeet",
                "English-only Whisper base.en",
            )
        }
        language_guidance.write_text(
            "Choose a local speech model for this computer.\n", encoding="utf-8"
        )
        if not check_windows_language_guidance(required_language_guidance):
            raise SyncError("self-test: missing Windows language split was not flagged")
        language_guidance.write_text(
            "The language and hardware split uses multilingual Parakeet with "
            "CUDA or English-only Whisper base.en on CPU.\n",
            encoding="utf-8",
        )
        if check_windows_language_guidance(required_language_guidance):
            raise SyncError("self-test: complete Windows language split was rejected")

        clipboard_guidance = Path(tmp) / "privacy.html"
        required_clipboard_guidance = {
            clipboard_guidance: (
                "macOS Clipboard History",
                "macOS 26",
                "macOS Universal Clipboard",
                "Published Windows 0.1.12",
                "ExcludeClipboardContentFromMonitorProcessing",
                "third-party clipboard",
            )
        }
        clipboard_guidance.write_text(
            "Presspeech itself does not sync transcripts.\n", encoding="utf-8"
        )
        if not check_clipboard_service_guidance(required_clipboard_guidance):
            raise SyncError("self-test: missing clipboard-service boundary was not flagged")
        clipboard_guidance.write_text(
            "macOS Clipboard History, macOS Universal Clipboard, and "
            "Windows clipboard history are outside Presspeech.\n",
            encoding="utf-8",
        )
        if not check_clipboard_service_guidance(required_clipboard_guidance):
            raise SyncError("self-test: unversioned macOS Clipboard History was accepted")
        clipboard_guidance.write_text(
            "macOS Clipboard History on macOS 26, "
            "macOS Universal Clipboard, and "
            "Windows clipboard history are outside Presspeech.\n",
            encoding="utf-8",
        )
        if not check_clipboard_service_guidance(required_clipboard_guidance):
            raise SyncError("self-test: stale Windows clipboard guidance was accepted")
        clipboard_guidance.write_text(
            "macOS Clipboard History on macOS 26 and macOS Universal Clipboard "
            "remain outside Presspeech. Published Windows 0.1.12 can enter "
            "history; 0.1.13 uses ExcludeClipboardContentFromMonitorProcessing, "
            "but a third-party clipboard reader remains outside that control.\n",
            encoding="utf-8",
        )
        if check_clipboard_service_guidance(required_clipboard_guidance):
            raise SyncError("self-test: complete clipboard-service boundary was rejected")

        token_guidance = Path(tmp) / "model-download-privacy.html"
        required_token_guidance = {
            token_guidance: (
                "Published Windows 0.1.12",
                "HF_TOKEN",
                "local Hugging Face cache",
                "HF_ENDPOINT",
                "HUGGINGFACE_CO_STAGING",
                "HF_HUB_USER_AGENT_ORIGIN",
                "configured endpoint",
                "do not require an account token",
                "Upcoming Windows 0.1.13",
                "implicit authentication",
                "Already used Windows 0.1.12?",
                "inherited",
                "staging setting",
                "destination you do not trust",
                "treat the token as disclosed",
                "revoke the token",
                "Hugging Face Access Tokens",
                "Do not include token values",
            )
        }
        token_guidance.write_text(
            "Windows downloads only public models.\n", encoding="utf-8"
        )
        if not check_windows_model_download_privacy_guidance(required_token_guidance):
            raise SyncError("self-test: missing version-scoped account-token disclosure was accepted")
        token_guidance.write_text(
            "Published Windows 0.1.12 may use HF_TOKEN or the local Hugging Face cache. "
            "It honors HF_ENDPOINT and HUGGINGFACE_CO_STAGING and may add "
            "HF_HUB_USER_AGENT_ORIGIN; a token may accompany a request to the configured endpoint. "
            "Those public models do not require an account token. Upcoming Windows 0.1.13 "
            "clears these settings and disables implicit authentication. Already used Windows "
            "0.1.12? If a model download ran with a token available and an inherited HF_ENDPOINT "
            "or staging setting may have sent it to a destination you do not trust, treat the token "
            "as disclosed to that destination. Revoke the token and create a replacement at Hugging Face Access Tokens. Do not include "
            "token values in logs or support requests.\n",
            encoding="utf-8",
        )
        if check_windows_model_download_privacy_guidance(required_token_guidance):
            raise SyncError("self-test: complete version-scoped account-token disclosure was rejected")

        scope_guidance = Path(tmp) / "windows-download-privacy-scopes.md"
        scope_guidance.write_text(
            "Published Windows 0.1.12 leaves implicit authentication enabled. "
            "Upcoming Windows 0.1.13 candidate disables implicit authentication.\n",
            encoding="utf-8",
        )
        if check_windows_model_download_privacy_scopes((scope_guidance,)):
            raise SyncError("self-test: correctly scoped Windows privacy claims were rejected")
        scope_guidance.write_text(
            "Published Windows 0.1.12 leaves implicit authentication enabled. "
            "Upcoming Windows 0.1.13 disables Hub telemetry. It also leaves implicit "
            "authentication enabled. The 0.1.13 candidate disables implicit "
            "authentication.\n",
            encoding="utf-8",
        )
        if not check_windows_model_download_privacy_scopes((scope_guidance,)):
            raise SyncError("self-test: ambiguous Windows authentication scope was accepted")

        agent_guidance = Path(tmp) / "agent-disclosure.md"
        required_agent_guidance = {
            agent_guidance: ("agent-harnesses", "agent-related environment markers"),
        }
        agent_guidance.write_text("Windows 0.1.12 uses Hub 1.29.0.\n", encoding="utf-8")
        if not check_windows_model_download_privacy_guidance(required_agent_guidance):
            raise SyncError("self-test: missing agent disclosure was not flagged")
        agent_guidance.write_text(
            "Windows 0.1.12 may fetch agent-harnesses and add an agent label "
            "from agent-related environment markers.\n", encoding="utf-8"
        )
        if check_windows_model_download_privacy_guidance(required_agent_guidance):
            raise SyncError("self-test: complete agent disclosure was rejected")

        summary_guidance = Path(tmp) / "windows-privacy-summary.html"
        required_summary_guidance = {
            summary_guidance: (
                "Before installing or launching Windows 0.1.12",
                "usage telemetry",
                "already-configured or locally saved Hugging Face token",
                "Custom download routing can change where the model request",
                "if a Hugging Face token or custom download route is configured on this PC",
                "wait until Windows 0.1.13 is published",
                "public models need no account token",
                "Windows privacy decision and technical details",
                "Already used Windows 0.1.12?",
                "inherited",
                "staging setting",
                "destination you do not trust",
                "treat the token as disclosed",
                "revoke the token",
                "Hugging Face Access Tokens",
                "Do not include token values",
            )
        }
        summary_guidance.write_text(
            "Windows downloads public models.\n", encoding="utf-8"
        )
        if not check_windows_model_download_privacy_summary(required_summary_guidance):
            raise SyncError("self-test: missing Windows privacy decision was not flagged")
        summary_guidance.write_text(
            "Before installing or launching Windows 0.1.12, usage telemetry and an already-configured "
            "or locally saved Hugging Face token may be sent; custom download routing "
            "can change where the model request goes. If a Hugging Face token or "
            "custom download route is configured on this PC, wait until Windows 0.1.13 is published. "
            "The public models need no account token; see the Windows privacy decision "
            "and technical details. Already used Windows 0.1.12? If a model download ran with "
            "a token available and an inherited HF_ENDPOINT or staging setting may have sent it to "
            "a destination you do not trust, treat the token as disclosed to that destination. "
            "Revoke the token and create a replacement at "
            "Hugging Face Access Tokens. Do not include token values in logs or support requests.\n",
            encoding="utf-8",
        )
        if check_windows_model_download_privacy_summary(required_summary_guidance):
            raise SyncError("self-test: complete Windows privacy decision was rejected")

        mac_summary = Path(tmp) / "macos-privacy-summary.html"
        required_mac_summary = {
            mac_summary: (
                'id="model-download-privacy"',
                "Before installing or launching macOS 0.3.8",
                "Hugging Face token inherited by Presspeech",
                "public model needs no account token",
                "wait until macOS 0.3.9 is published",
                "Dictation audio and transcripts are not sent",
                "version-specific network inventory",
            )
        }
        mac_summary.write_text(
            "Presspeech downloads a public model.\n", encoding="utf-8"
        )
        if not check_macos_model_download_privacy_summary(
            required_mac_summary, install_page=mac_summary
        ):
            raise SyncError("self-test: missing macOS privacy decision was not flagged")
        mac_warning = (
            '<div id="model-download-privacy"><p>Before installing or launching '
            "macOS 0.3.8, its model-download requests may include a Hugging Face "
            "token inherited by Presspeech. The public model needs no account "
            "token. If one may be present, wait until macOS 0.3.9 is published. "
            "Dictation audio and transcripts are not sent. See the version-specific "
            "network inventory.</p></div>\n"
        )
        mac_button = (
            '<a class="button" href="https://github.com/rcourtman/presspeech/'
            'releases/download/v0.3.8/Presspeech.zip">Download</a>\n'
        )
        mac_summary.write_text(mac_warning + mac_button, encoding="utf-8")
        if check_macos_model_download_privacy_summary(
            required_mac_summary, install_page=mac_summary
        ):
            raise SyncError("self-test: complete macOS privacy decision was rejected")
        mac_summary.write_text(mac_button + mac_warning, encoding="utf-8")
        mac_order_errors = check_macos_model_download_privacy_summary(
            required_mac_summary, install_page=mac_summary
        )
        if not any("must precede the direct download action" in error for error in mac_order_errors):
            raise SyncError("self-test: early macOS download action was accepted")

        faq_install = Path(tmp) / "faq.html"
        faq_install.write_text(
            '<article><h3>How do I install it?</h3>'
            '<p id="faq-macos-install-privacy">macOS warning</p>'
            '<p id="faq-windows-install-privacy">Windows warning</p>'
            '<a href="https://github.com/rcourtman/presspeech/releases/latest/'
            'download/Presspeech.zip">Download</a>'
            '<code>brew install --cask rcourtman/presspeech/presspeech</code>'
            '<a href="windows.html#download-verify-run">Windows guide</a>'
            '</article>',
            encoding="utf-8",
        )
        if check_faq_install_privacy_order(faq_install):
            raise SyncError("self-test: ordered FAQ privacy warnings were rejected")
        faq_install.write_text(
            '<article><h3>How do I install it?</h3>'
            '<a href="https://github.com/rcourtman/presspeech/releases/latest/'
            'download/Presspeech.zip">Download</a>'
            '<code>brew install --cask rcourtman/presspeech/presspeech</code>'
            '<a href="windows.html#download-verify-run">Windows guide</a>'
            '<p id="faq-macos-install-privacy">macOS warning</p>'
            '<p id="faq-windows-install-privacy">Windows warning</p>'
            '</article>',
            encoding="utf-8",
        )
        faq_order_errors = check_faq_install_privacy_order(faq_install)
        if len(faq_order_errors) != 2:
            raise SyncError("self-test: install action before FAQ privacy warning was accepted")

        delivery_guidance = Path(tmp) / "getting-started.html"
        required_delivery_guidance = {
            delivery_guidance: ("cannot verify the destination", "clipboard recovery"),
        }
        delivery_guidance.write_text(
            "Presspeech normally pastes at the cursor.\n", encoding="utf-8"
        )
        if not check_delivery_boundary_guidance(required_delivery_guidance):
            raise SyncError("self-test: missing delivery boundary was not flagged")
        delivery_guidance.write_text(
            "If it cannot verify the destination, use clipboard recovery.\n",
            encoding="utf-8",
        )
        if check_delivery_boundary_guidance(required_delivery_guidance):
            raise SyncError("self-test: complete delivery boundary was rejected")

        compatibility_guidance = Path(tmp) / "compatibility.md"
        required_compatibility_guidance = {
            compatibility_guidance: (
                "field type",
                "five steady-focus attempts",
                "filtered reports",
            ),
        }
        compatibility_guidance.write_text(
            "Please test automatic paste.\n", encoding="utf-8"
        )
        if not check_compatibility_evidence_guidance(
            required_compatibility_guidance
        ):
            raise SyncError(
                "self-test: incomplete compatibility-evidence guidance was not flagged"
            )
        compatibility_guidance.write_text(
            "Choose a field type, run five steady-focus attempts, then view "
            "the filtered reports.\n",
            encoding="utf-8",
        )
        if check_compatibility_evidence_guidance(required_compatibility_guidance):
            raise SyncError(
                "self-test: complete compatibility-evidence guidance was rejected"
            )

        worksheet_page = Path(tmp) / "app-compatibility.html"
        worksheet_script = Path(tmp) / "compatibility-worksheet.js"
        worksheet_inputs = "".join(
            f'<input type="radio" name="steady-{index}" value="{outcome}">'
            for index in range(1, 6)
            for outcome in ("pasted", "recovered", "unsafe")
        ) + "".join(
            f'<input type="radio" name="focus-{index}" value="{outcome}">'
            for index in range(1, 4)
            for outcome in ("copied", "inserted", "other")
        )
        worksheet_page.write_text(
            '<script src="compatibility-worksheet.js" defer></script>'
            '<form id="compatibility-worksheet">'
            + worksheet_inputs
            + '<textarea id="worksheet-summary" readonly></textarea>'
            '<button type="button">Copy</button>'
            '<button id="save-worksheet-summary" type="button" disabled>'
            'Download report draft</button><button type="reset">Reset</button>'
            '<div id="worksheet-report-actions" hidden>'
            '<a href="https://github.com/example/issues?q=matching">Browse</a>'
            '<a href="https://github.com/example/issues/new?template=compatibility_report.yml">New</a>'
            '</div>'
            '</form>',
            encoding="utf-8",
        )
        worksheet_valid_script = (
            'document.getElementById("compatibility-worksheet");\n'
            'reportActions.hidden = !result.complete;\n'
            'save.disabled = !result.complete;\n'
            'save.addEventListener("click", saveSummary);\n'
            'function formatReportDraft(summaryText) { return ['
            '"Platform (macOS or Windows):", "Target app and public version:", '
            '"Generic field type:", summaryText].join("\\n"); }\n'
            'const file = new Blob([`${formatReportDraft(summary.value)}\\n`]);\n'
            'link.download = "presspeech-compatibility-report-draft.txt";\n'
            'URL.createObjectURL(file);\n'
            '`Overall result: ${result.overall}`\n'
            + "\n".join(COMPATIBILITY_OVERALL_RESULTS)
        )
        worksheet_script.write_text(worksheet_valid_script, encoding="utf-8")
        if check_compatibility_worksheet_contract(worksheet_page, worksheet_script):
            raise SyncError("self-test: local compatibility worksheet was rejected")
        valid_worksheet_page = worksheet_page.read_text(encoding="utf-8")
        valid_worksheet_script = worksheet_script.read_text(encoding="utf-8")
        worksheet_page.write_text(
            re.sub(
                r'<div id="worksheet-report-actions" hidden>.*?</div>',
                "",
                valid_worksheet_page,
                count=1,
                flags=re.S,
            ),
            encoding="utf-8",
        )
        if not any(
            "must reveal report actions" in error
            for error in check_compatibility_worksheet_contract(
                worksheet_page, worksheet_script
            )
        ):
            raise SyncError("self-test: missing worksheet report handoff was accepted")
        worksheet_page.write_text(valid_worksheet_page, encoding="utf-8")
        worksheet_script.write_text(
            valid_worksheet_script.replace(
                "reportActions.hidden = !result.complete;\n", "", 1
            ),
            encoding="utf-8",
        )
        if not any(
            "must remain hidden" in error
            for error in check_compatibility_worksheet_contract(
                worksheet_page, worksheet_script
            )
        ):
            raise SyncError("self-test: always-visible worksheet report handoff was accepted")
        worksheet_script.write_text(
            valid_worksheet_script.replace("save.disabled = !result.complete;\n", "", 1),
            encoding="utf-8",
        )
        if not any(
            "report download must remain disabled" in error
            for error in check_compatibility_worksheet_contract(
                worksheet_page, worksheet_script
            )
        ):
            raise SyncError("self-test: incomplete worksheet download was accepted")
        worksheet_script.write_text(
            'localStorage.setItem("result", "unsafe");\n', encoding="utf-8"
        )
        if not check_compatibility_worksheet_contract(worksheet_page, worksheet_script):
            raise SyncError("self-test: persistent compatibility worksheet was accepted")
        worksheet_script.write_text(
            "// local only\n" + worksheet_valid_script,
            encoding="utf-8",
        )
        worksheet_page.write_text(
            worksheet_page.read_text(encoding="utf-8").replace(
                '<textarea id="worksheet-summary" readonly></textarea>',
                '<textarea id="worksheet-summary" readonly></textarea>'
                '<textarea name="private-notes"></textarea>',
                1,
            ),
            encoding="utf-8",
        )
        if not check_compatibility_worksheet_contract(worksheet_page, worksheet_script):
            raise SyncError("self-test: extra free-text worksheet field was accepted")
        worksheet_page.write_text(
            worksheet_page.read_text(encoding="utf-8").replace(
                '<textarea name="private-notes"></textarea>', "", 1
            ),
            encoding="utf-8",
        )
        worksheet_page.write_text(
            worksheet_page.read_text(encoding="utf-8").replace(
                '<input type="radio" name="steady-1" value="pasted">',
                '<input type="text" name="transcript">',
                1,
            ),
            encoding="utf-8",
        )
        if not check_compatibility_worksheet_contract(worksheet_page, worksheet_script):
            raise SyncError("self-test: free-text compatibility worksheet was accepted")

        command_guidance = Path(tmp) / "command-safety.html"
        required_command_guidance = {
            command_guidance: ("command shell", "Append newline", "review"),
        }
        command_guidance.write_text(
            "Append newline changes the suffix.\n", encoding="utf-8"
        )
        if not check_command_shell_guidance(required_command_guidance):
            raise SyncError("self-test: incomplete command-shell guidance was not flagged")
        command_guidance.write_text(
            "A command shell can execute Append newline; review the text first.\n",
            encoding="utf-8",
        )
        if check_command_shell_guidance(required_command_guidance):
            raise SyncError("self-test: complete command-shell guidance was rejected")

        phase_copy = Path(tmp) / "release-phase.md"
        phase_copy.write_text(
            "Upcoming macOS **8.7.6** is the 8.7.6 candidate.\n",
            encoding="utf-8",
        )
        phase_errors = check_mac_release_phase_copy(metadata, [phase_copy])
        if len(phase_errors) != 1 or "called upcoming" not in phase_errors[0]:
            raise SyncError("self-test: phase-bound current Mac copy was not rejected")
        phase_copy.write_text(
            "Published macOS 8.7.5 remains available.\n", encoding="utf-8"
        )
        if not check_mac_release_phase_copy(metadata, [phase_copy]):
            raise SyncError("self-test: phase-bound old Mac copy was not rejected")
        phase_copy.write_text(
            "macOS 8.7.6 and later contain this behavior; legacy 8.7.5 differs.\n",
            encoding="utf-8",
        )
        if check_mac_release_phase_copy(metadata, [phase_copy]):
            raise SyncError("self-test: release-stable Mac copy was rejected")

        model_download_guidance = Path(tmp) / "mac-model-download-guidance.md"
        required_model_download_guidance = {
            model_download_guidance: (
                "0.3.8", "0.3.9", "500", "clean install", "Download Model", "defer"
            )
        }
        model_download_guidance.write_text(
            "macOS 0.3.8 downloads 500 MB on launch. 0.3.9 asks a clean install "
            "to choose Download Model or defer in Setup.\n",
            encoding="utf-8",
        )
        if check_mac_model_download_guidance(required_model_download_guidance):
            raise SyncError("self-test: clear versioned Mac download guidance was rejected")
        model_download_guidance.write_text(
            "The Mac download behavior depends on the build.\n", encoding="utf-8"
        )
        if not check_mac_model_download_guidance(required_model_download_guidance):
            raise SyncError("self-test: vague Mac model-download guidance was not flagged")

        phase_copy.write_text(
            "Upcoming Windows **9.8.7** adds this behavior.\n",
            encoding="utf-8",
        )
        phase_errors = check_windows_release_phase_copy(metadata, [phase_copy])
        if len(phase_errors) != 1 or "called upcoming" not in phase_errors[0]:
            raise SyncError("self-test: phase-bound current Windows copy was not rejected")
        phase_copy.write_text(
            "Windows 9.8.7 candidate adds this behavior.\n", encoding="utf-8"
        )
        if not check_windows_release_phase_copy(metadata, [phase_copy]):
            raise SyncError("self-test: Windows candidate copy was not rejected")
        phase_copy.write_text(
            "Published Windows release remains 9.8.6.\n", encoding="utf-8"
        )
        if not check_windows_release_phase_copy(metadata, [phase_copy]):
            raise SyncError("self-test: phase-bound old Windows copy was not rejected")
        phase_copy.write_text(
            "Windows 9.8.7 and later contain this behavior; legacy 9.8.6 differs.\n",
            encoding="utf-8",
        )
        if check_windows_release_phase_copy(metadata, [phase_copy]):
            raise SyncError("self-test: release-stable Windows copy was rejected")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if docs are not already synced")
    parser.add_argument("--release-zip", help="release zip whose byte size should be written to docs")
    parser.add_argument("--date", help="override last_updated date as YYYY-MM-DD")
    parser.add_argument("--self-test", action="store_true", help="run offline sync self-tests")
    args = parser.parse_args()

    try:
        if args.self_test:
            run_self_test()
            print("sync-docs self-test passed")
            return 0

        metadata = build_metadata(args)
        expected = expected_files(metadata)
        errors: list[str] = []
        if args.check:
            errors.extend(stale_copy_errors(public_release_paths() + EXTRA_STALE_SCAN))
            errors.extend(check_mac_release_phase_copy(metadata))
            errors.extend(check_mac_model_download_guidance())
            errors.extend(check_windows_release_phase_copy(metadata))
            errors.extend(check_windows_release_references(metadata))
            errors.extend(check_icon_stats(metadata))
            errors.extend(check_platform_orientation())
            errors.extend(check_windows_unsigned_guidance())
            errors.extend(check_windows_verified_download_flow())
            errors.extend(check_windows_language_guidance())
            errors.extend(check_clipboard_service_guidance())
            errors.extend(check_windows_model_download_privacy_guidance())
            errors.extend(check_windows_model_download_privacy_scopes())
            errors.extend(check_windows_model_download_privacy_guidance(WINDOWS_AGENT_DISCLOSURE))
            errors.extend(check_macos_model_download_privacy_summary())
            errors.extend(check_windows_model_download_privacy_summary())
            errors.extend(check_faq_install_privacy_order())
            errors.extend(check_delivery_boundary_guidance())
            errors.extend(check_compatibility_evidence_guidance())
            errors.extend(check_compatibility_worksheet_contract())
            errors.extend(check_command_shell_guidance())
            errors.extend(check_repository_install_guidance())
            errors.extend(check_compare_freshness())
            for path, want in expected.items():
                have = read_text(path) if path.exists() else ""
                if have != want:
                    errors.append(f"{path.relative_to(ROOT)}: not synced")
                    diff = diff_text(path, have, want)
                    if diff:
                        sys.stderr.write(diff)
            errors.extend(check_install_prompt_sync(metadata))
            if errors:
                for error in errors:
                    print(error, file=sys.stderr)
                return 1
            print("docs are synced")
            return 0

        previous_size = str(load_metadata().get("release_zip_size", ""))
        for path, text in expected.items():
            if not path.exists() or read_text(path) != text:
                write_text(path, text)
                print(f"updated {path.relative_to(ROOT)}")
        sync_icon_stats(previous_size, str(metadata["release_zip_size"]))

        errors.extend(stale_copy_errors(public_release_paths() + EXTRA_STALE_SCAN))
        errors.extend(check_mac_release_phase_copy(metadata))
        errors.extend(check_mac_model_download_guidance())
        errors.extend(check_windows_release_phase_copy(metadata))
        errors.extend(check_windows_release_references(metadata))
        errors.extend(check_icon_stats(metadata))
        errors.extend(check_platform_orientation())
        errors.extend(check_windows_unsigned_guidance())
        errors.extend(check_windows_verified_download_flow())
        errors.extend(check_windows_language_guidance())
        errors.extend(check_clipboard_service_guidance())
        errors.extend(check_windows_model_download_privacy_guidance())
        errors.extend(check_windows_model_download_privacy_scopes())
        errors.extend(check_windows_model_download_privacy_guidance(WINDOWS_AGENT_DISCLOSURE))
        errors.extend(check_macos_model_download_privacy_summary())
        errors.extend(check_windows_model_download_privacy_summary())
        errors.extend(check_faq_install_privacy_order())
        errors.extend(check_delivery_boundary_guidance())
        errors.extend(check_compatibility_evidence_guidance())
        errors.extend(check_compatibility_worksheet_contract())
        errors.extend(check_command_shell_guidance())
        errors.extend(check_repository_install_guidance())
        errors.extend(check_compare_freshness())
        errors.extend(check_install_prompt_sync(metadata))
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        return 0
    except SyncError as exc:
        print(f"sync-docs: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
