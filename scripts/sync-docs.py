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

# People and install assistants can arrive at these anchors without seeing the
# top-of-page model-download warning. Review the local reminder for each newly
# published release instead of silently carrying the old first-launch decision
# into a new version.
ANCHORED_INSTALL_PREFLIGHTS = (
    {
        "path": DOCS / "install.html",
        "anchor": "direct-download",
        "platform": "macos",
        "version_key": "version",
        "reviewed_version": "0.3.8",
        "required": (
            "Before opening macOS 0.3.8",
            "missing-model download starts on launch",
            "Hugging Face token inherited by Presspeech",
            "leave the downloaded app unopened",
            "wait until macOS 0.3.9 is published",
            "full release-specific warning",
        ),
    },
    {
        "path": DOCS / "windows.html",
        "anchor": "download-verify-run",
        "platform": "windows",
        "version_key": "windows_version",
        "reviewed_version": "0.1.12",
        "required": (
            "Before launching Windows 0.1.12",
            "missing-model download starts on launch",
            "usage telemetry or an available token",
            "custom routing can change where a token goes",
            "TLS-inspecting HTTPS proxy",
            "wait until Windows 0.1.13 is published",
            "Launch Presspeech",
            "unchecked on the final installer screen",
            "full release-specific warning",
        ),
    },
)

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
        "marks every dictation clipboard item",
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
        "Before opening macOS 0.3.8",
        "Hugging Face token inherited by Presspeech",
        "public model needs no token",
        "wait until 0.3.9 is published",
        "dictation audio and transcripts are not sent",
        "full macOS warning",
        "Already used macOS 0.3.8?",
        "macos-0-3-8-after-use",
    ),
    DOCS / "getting-started.html": (
        "Downloading is not launching",
        "macOS 0.3.8 — wait if a token may be inherited",
        "A model request can include the inherited token",
        "public models need no account token",
        "Wait for published 0.3.9",
        "model requests do not include dictation audio or transcripts",
        "version-specific network inventory",
        "install.html#model-download-privacy",
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
        "install.html#model-download-privacy",
        "macOS install guide",
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
        "Before installing or launching macOS 0.3.8",
        "model-download requests may include a Hugging Face token inherited by Presspeech",
        "install.html#model-download-privacy",
        "macOS 0.3.8 may attach an inherited Hugging Face token",
        "public model needs no account token",
        "wait until macOS 0.3.9 is published",
        "dictation audio and transcripts are not sent",
        "leave a working model cache in place",
        "macos-0-3-8-after-use",
    ),
    DOCS / "llms-full.txt": (
        "Before installing or launching macOS 0.3.8",
        "model-download requests may include a Hugging Face token inherited by Presspeech",
        "install.html#model-download-privacy",
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

# The pinned FluidAudio client used by both macOS versions routes HTTPS model
# requests through an inherited lowercase https_proxy. The published 0.3.8
# build may also attach an inherited account token; 0.3.9 removes that token
# but does not remove the proxy setting. Keep this distinction visible at every
# first-launch decision surface and in the technical inventory.
MAC_MODEL_DOWNLOAD_PROXY_GUIDANCE = {
    ROOT / "README.md": (
        "lowercase", "https_proxy", "TLS-inspecting", "0.3.8 token", "still honors proxy settings"
    ),
    ROOT / "SECURITY.md": (
        "macOS 0.3.8", "https_proxy", "http_proxy", "treat that token as disclosed",
        "still honors proxy settings",
    ),
    DOCS / "index.html": (
        "lowercase", "https_proxy", "TLS-inspecting", "trusted by macOS", "proxy's trust is unclear"
    ),
    DOCS / "getting-started.html": (
        "lowercase", "https_proxy", "TLS-inspecting", "trusted by macOS", "trust is unclear"
    ),
    DOCS / "faq.html": (
        "lowercase", "https_proxy", "TLS-inspecting", "0.3.8 token", "tunnelling proxy"
    ),
    DOCS / "install.html": (
        "lowercase", "https_proxy", "TLS-inspecting", "0.3.8 token", "still honors proxy settings"
    ),
    DOCS / "install" / "agents.md": (
        "lowercase", "https_proxy", "TLS-inspecting", "0.3.8 token", "still honors proxy settings"
    ),
    DOCS / "privacy.html": (
        "lowercase", "https_proxy", "http_proxy", "TLS-inspecting", "treat the token as disclosed"
    ),
    DOCS / "privacy" / "network-calls.json": (
        "lowercase", "https_proxy", "http_proxy", "TLS-inspecting", "0.3.9 still honors proxy settings"
    ),
    DOCS / "llms.txt": (
        "lowercase", "https_proxy", "TLS-inspecting", "0.3.8 token", "still honors proxy settings"
    ),
    DOCS / "llms-full.txt": (
        "lowercase", "https_proxy", "http_proxy", "TLS-inspecting", "0.3.8 token"
    ),
}

WINDOWS_MODEL_DOWNLOAD_PRIVACY_SUMMARY = {
    ROOT / "windows" / "README.md": (
        "published Windows 0.1.12 build",
        "usage telemetry enabled",
        "avoid this possible usage telemetry",
        "wait for 0.1.13",
        "keep the app unopened",
    ),
    ROOT / "README.md": (
        "Before installing or launching Windows 0.1.12",
        "usage telemetry",
        "already-configured or locally saved Hugging Face token",
        "Custom download routing can change where the model request",
        "avoid this possible usage telemetry",
        "concerned that a Hugging Face token or custom download route may be configured on this PC",
        "TLS-inspecting HTTPS proxy trusted by the client can read any 0.1.12 token",
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
        "Before opening Windows 0.1.12",
        "usage telemetry",
        "already-configured or locally saved token",
        "Custom routing can change the request destination",
        "want to avoid possible telemetry",
        "a token or custom route may be configured",
        "TLS-inspecting HTTPS proxy trusted by the client can read a token",
        "wait until 0.1.13 is published",
        "public models need no token",
        "full Windows warning",
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
        "Windows 0.1.12 — wait if privacy risks are unclear",
        "usage telemetry",
        "an available token or custom download route may be configured",
        "A custom route can change where the request and token go",
        "want to avoid possible Hugging Face usage telemetry",
        "TLS-inspecting proxy trusted by the client can read the token",
        "Wait for published 0.1.13",
        "public models need no account token",
        "windows.html#model-download-privacy",
        "Already used Windows 0.1.12?",
        "windows.html#windows-0-1-12-after-use",
        "Do not include token values",
    ),
    DOCS / "faq.html": (
        "Before installing or launching Windows 0.1.12",
        "usage telemetry",
        "include an available token",
        "custom routing can change where the token goes",
        "avoid this possible usage telemetry",
        "TLS-inspecting HTTPS proxy trusted by the client can read any 0.1.12 token",
        "wait until Windows 0.1.13 is published",
        "dictation audio and transcripts are not sent",
        'id="faq-windows-install-privacy"',
    ),
    DOCS / "windows.html": (
        "Privacy decision for published Windows 0.1.12",
        "may send default usage telemetry",
        "avoid this possible usage telemetry",
        "wait for Windows 0.1.13",
        "leave <strong>Launch Presspeech</strong> unchecked",
    ),
    DOCS / "privacy.html": (
        "Windows 0.1.12 package vs. launch",
        "avoid this possible usage telemetry",
        "wait for 0.1.13 before launching",
        "Launch Presspeech",
    ),
    DOCS / "install" / "agents.md": (
        "Before installing or launching published Windows 0.1.12",
        "Hugging Face usage telemetry",
        "already-configured or locally saved Hugging Face token",
        "custom download routing can change where a request",
        "public models need no account token",
        "avoid this possible usage telemetry",
        "concerned that a Hugging Face token or custom download route may be configured",
        "TLS-inspecting HTTPS proxy trusted by the client can read any 0.1.12 token",
        "wait until Windows 0.1.13 is published",
        "Do not inspect or display token values",
        "launch 0.1.12 without the user's informed choice",
        "Dictation audio and transcripts are not sent in model downloads",
        "privacy.html#network-calls",
    ),
}

# Standard HTTPX proxy/CA settings remain active in both Windows versions.
WINDOWS_MODEL_DOWNLOAD_PROXY_GUIDANCE = {
    path: (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "SSL_CERT_FILE", "SSL_CERT_DIR", "trust_env=True",
        "TLS-inspecting HTTPS proxy", "published Windows 0.1.12",
        "upcoming Windows 0.1.13",
    )
    for path in (
        ROOT / "SECURITY.md",
        ROOT / "README.md",
        ROOT / "windows" / "README.md",
        DOCS / "privacy.html",
        DOCS / "privacy" / "network-calls.json",
        DOCS / "windows.html",
        DOCS / "llms.txt",
        DOCS / "llms-full.txt",
    )
}

# Model-download integrity is now enforced only by the Windows 0.1.13
# candidate. Keep the published/candidate split on each public trust surface.
WINDOWS_MODEL_DOWNLOAD_INTEGRITY_GUIDANCE = {
    path: (
        "Published Windows 0.1.12 does not independently verify",
        "SHA-256",
        "verifies every allowed",
    )
    for path in (
        ROOT / "SECURITY.md",
        ROOT / "README.md",
        ROOT / "windows" / "README.md",
        DOCS / "privacy.html",
        DOCS / "privacy" / "network-calls.json",
        DOCS / "windows.html",
        DOCS / "llms.txt",
        DOCS / "llms-full.txt",
    )
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
    DOCS / "getting-started.html": ("If Presspeech did not paste", "Windows 0.1.12", "only if it still holds the complete transcript"),
    DOCS / "install.html": ("cannot verify that destination", "clipboard"),
    DOCS / "windows.html": ("cannot verify that destination", "clipboard"),
    DOCS / "faq.html": ("cannot verify that destination", "clipboard"),
    DOCS / "llms.txt": ("cannot verify the same destination", "clipboard"),
    DOCS / "llms-full.txt": (
        "verify the original destination", "clipboard",
        "does not verify that the same field or browser tab",
    ),
    ROOT / "marketing" / "SHARING.md": ("cannot be verified", "clipboard"),
}

# The published Windows build copies after a failed target check; the next
# candidate intentionally does not replace a known-invalid target's previous
# clipboard item. Generic "already copied" advice could paste unrelated text.
WINDOWS_DELIVERY_RECOVERY_GUIDANCE = {
    ROOT / "README.md": (
        "published Windows 0.1.12", "copied at completion",
        "only if the clipboard still holds it", "Upcoming Windows 0.1.13",
        "Delivery Recovery",
    ),
    DOCS / "windows.html": ("Published 0.1.12", "Upcoming 0.1.13", "Delivery Recovery"),
    DOCS / "faq.html": ("published Windows 0.1.12", "Upcoming Windows 0.1.13", "Delivery Recovery"),
    DOCS / "getting-started.html": ("Windows 0.1.12", "Transcript copied, not pasted", "only if it still holds the complete transcript"),
    DOCS / "privacy.html": ("Published Windows 0.1.12", "Upcoming Windows 0.1.13", "Delivery Recovery"),
    DOCS / "llms.txt": ("published Windows 0.1.12", "upcoming Windows 0.1.13", "Delivery Recovery"),
    DOCS / "llms-full.txt": ("published Windows 0.1.12", "upcoming Windows 0.1.13", "Delivery Recovery"),
    DOCS / "app-compatibility.html": (
        "published Windows 0.1.12", "upcoming Windows 0.1.13",
        "Delivery Recovery", "do <em>not</em> treat the current clipboard as the transcript",
    ),
    ROOT / ".github" / "ISSUE_TEMPLATE" / "compatibility_report.yml": (
        "Windows 0.1.13", "Delivery Recovery", "notice alone does not mean",
    ),
}
WINDOWS_DELIVERY_UNSCOPED_CLAIMS = (
    "windows leaves the transcript on the clipboard",
    "if it cannot verify that destination, it keeps the result on the clipboard",
    "if presspeech cannot verify the same destination, it copies the transcript",
    "otherwise it leaves the transcript on the clipboard for manual paste",
    "if delivery succeeded or presspeech showed its copied/manual-paste notice, paste",
)

# A copied notice describes clipboard state at completion, not at a later
# manual paste. Keep this warning in the actionable section on each first-use
# and recovery surface; a matching phrase elsewhere on the page is not enough.
COPY_NOTICE_FRESHNESS_GUIDANCE = {
    DOCS / "getting-started.html": (
        '<section id="first-app">', '</section>',
        ("copied at completion", "later copy can replace", "only if it still holds the complete transcript", "Copy Last Transcript"),
    ),
    DOCS / "troubleshooting.html": (
        'id="windows-paste"', '</article>',
        ("copied at completion", "later copy can replace", "only if the clipboard still holds the complete transcript", "do not paste the newer item"),
    ),
    DOCS / "troubleshooting.md": (
        "### Try Dictation Works But Text Is Not Inserted", "\n### Playback",
        ("copied at completion", "later copy can", "only if the clipboard still holds the complete transcript", "do not paste the newer item"),
    ),
    DOCS / "llms.txt": (
        "- Delivery fallback:", "\n- Command-shell safety:",
        ("copied at completion", "later clipboard changes", "only if the clipboard still holds the complete transcript", "never paste a newer clipboard item"),
    ),
    DOCS / "llms-full.txt": (
        "## Usage", "Do not describe direct",
        ("copied at completion", "later clipboard changes", "only if the clipboard still holds the complete transcript", "never paste a newer clipboard item"),
    ),
}

# Keep the large macOS model transfer's consent behavior explicit by release.
# Discovery pages describe the linked 0.3.8 first run and point to the full
# warning; reference/install pages also explain 0.3.9's clean-install prompt.
# Vague "depending on the build" copy hides the behavior that matters most.
MAC_MODEL_DOWNLOAD_GUIDANCE = {
    ROOT / "README.md": ("0.3.8", "0.3.9", "500", "clean install", "Download Model", "Set Up Later", "defer"),
    DOCS / "index.html": ("0.3.8", "0.3.9", "500", "on launch", "full macOS warning"),
    DOCS / "getting-started.html": (
        "0.3.8", "0.3.9", "500", "on launch", "full macOS warning"
    ),
    DOCS / "install.html": ("0.3.8", "0.3.9", "500", "clean install", "Download Model", "Set Up Later", "defer"),
    DOCS / "faq.html": ("0.3.8", "0.3.9", "500", "clean install", "Download Model", "Set Up Later", "defer"),
    DOCS / "privacy.html": (
        "0.3.8", "0.3.9", "500", "clean install", "Download Model", "Set Up Later", "defer"
    ),
    DOCS / "privacy" / "network-calls.json": (
        "0.3.8", "0.3.9", "500", "clean install", "Download Model", "Set Up Later", "defer"
    ),
    DOCS / "llms-full.txt": (
        "0.3.8", "0.3.9", "500", "clean install", "Download Model", "Set Up Later", "defer"
    ),
    DOCS / "install" / "agents.md": (
        "0.3.8", "0.3.9", "500", "clean install", "Download Model", "Set Up Later", "defer"
    ),
}

# These are action surfaces, not just release-reference pages. A reader or
# assistant must not treat the candidate's download prompt as present in the
# published Windows build, or run the published Mac app as part of an install
# command before deciding whether to make its model request.
FIRST_RUN_ACTION_COPY = {
    ROOT / "README.md": (
        "If you decide to launch published 0.3.8 after reviewing the model-download",
        "Only after the user makes an informed choice to launch 0.3.8:",
        "Choose **Press to toggle** in Settings, not Setup.",
        "**Finish Setup**, **Set Up Later**, or",
    ),
    ROOT / "windows" / "README.md": (
        "Published 0.1.12 starts a missing selected-model download automatically",
        "Only upcoming 0.1.13 asks you to confirm",
        "Start with Windows is on for a new profile.",
        "Choose **Press to toggle** in Settings",
        "**Finish Setup**, **Set Up Later**, or closing Setup",
    ),
    DOCS / "getting-started.html": (
        "In 0.1.12, <strong>Set Up Later</strong> does not stop a missing-model download",
        "0.1.12 selects <strong>Start Presspeech with Windows</strong> by default",
        "Its Setup has no dictation-style selector",
        "<strong>Set Up Later</strong>, or closing Setup if you do not want the app at sign-in",
    ),
    DOCS / "install.html": (
        "If you decide to launch published 0.3.8 after reviewing the",
        "<strong>Decide whether to launch 0.3.8</strong>",
        "If you chose to wait after reading the <a href=\"#model-download-privacy\">privacy warning</a>, leave the app unopened",
    ),
    DOCS / "windows.html": (
        "Published 0.1.12 starts the selected download on first launch; it has no pre-download deferral. Only upcoming 0.1.13",
        "<strong>Decide whether to launch 0.1.12</strong>",
        "leave the installer’s final <strong>Launch Presspeech</strong> option unchecked and do not open the app",
        "a missing model starts downloading without another prompt",
        "<strong>Published 0.1.12:</strong> Setup shows",
        "0.1.12 does not offer dictation style in Setup",
        "open <strong>Settings</strong>, select <strong>Press to toggle</strong> under <strong>Trigger</strong>",
        "<strong>Upcoming 0.1.13 (not yet published):</strong> Setup also offers",
        "<strong>Set Up Later</strong> defers completing setup, not that download.",
        "A new profile starts with <strong>Start Presspeech with Windows</strong> off",
        "<strong>Set Up Later</strong>, or closing Setup if you do not want the app at sign-in",
    ),
    DOCS / "llms-full.txt": (
        "Only after the user decides to launch published 0.3.8 despite the model-download warning above:",
        "Published Windows 0.1.12 starts a missing selected-model download on first launch without asking first",
        "Upcoming 0.1.13 checks the default-model cache locally and asks before downloading missing first-run files",
    ),
}
FIRST_RUN_ACTION_FORBIDDEN = {
    ROOT / "README.md": (
        re.compile(r"(?m)^brew install --cask rcourtman/presspeech/presspeech\nopen /Applications/Presspeech\.app$"),
    ),
    DOCS / "install.html": (
        re.compile(r"<pre><code>brew install --cask rcourtman/presspeech/presspeech\s+open /Applications/Presspeech\.app"),
    ),
    DOCS / "windows.html": (
        re.compile(r"Choose <strong>Set Up Later</strong> to defer; the Parakeet path also offers the smaller CPU model"),
        re.compile(
            r"first-run window shows.{0,220}<strong>Press to toggle</strong> style",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    DOCS / "llms-full.txt": (
        re.compile(r"On first run it checks whether the default model is cached and asks before downloading missing files"),
        re.compile(r"(?m)^brew install --cask rcourtman/presspeech/presspeech\nopen /Applications/Presspeech\.app$"),
    ),
}

# The landing and first-dictation pages deliberately teach only the current
# published controls. Unlike their version headings, their hand-written steps
# are not rewritten by sync_index/sync_getting_started. Require a human review
# when release metadata advances so the old steps cannot be published beneath
# a new version heading.
ONBOARDING_RELEASE_COPY_REVIEWED = {
    "version": "0.3.8",
    "windows_version": "0.1.12",
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
        "five steady-focus and three focus-change slots",
        "live coverage links separate native, browser, and Electron/Chromium",
        "Browse existing compatibility",
    ),
    ROOT / "SUPPORT.md": (
        "Browse existing target-app compatibility reports",
        "live coverage",
        "generic field type",
        "keyboard layout/input source",
    ),
    ROOT / "CONTRIBUTING.md": (
        "per platform/app/version/field type",
        "Browse existing compatibility",
    ),
    ROOT / ".github" / "ISSUE_TEMPLATE" / "compatibility_report.yml": (
        "eight outcome counts below",
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
        "keyboard layout/input source only when it differs from your usual layout",
        "Manual-paste recovery occurred during steady focus",
        "including its eight counts and Overall result",
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
        "worksheet's eight counts and Overall result",
        "Download report draft",
        "blank prompts for public versions and generic target context",
        "keyboard layout/input source only if it differs from your",
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
        "worksheet's eight counts and Overall result",
        "Download report draft",
        "blank prompts for public version and generic target context",
        "keyboard layout/input source only if it differs from your",
        "Check issue-creation status",
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
# alias for macOS and the gated, version-pinned Pages guide for Windows. The
# README's top install badges should reach the warning-first macOS guide, not
# a release or cask page that may be opened before its privacy decision.
README_BADGE_PREFLIGHT = (
    'href="https://rcourtman.github.io/presspeech/install.html#model-download-privacy"><img src="https://img.shields.io/github/v/release/rcourtman/presspeech',
    'href="https://rcourtman.github.io/presspeech/install.html"><img src="https://img.shields.io/badge/Homebrew-Cask',
)
REPOSITORY_INSTALL_GUIDANCE = {
    ROOT / "llms.txt": (
        "Before installing or launching macOS 0.3.8",
        "wait until 0.3.9 is published",
        "unsigned Windows 0.1.12 prerelease",
        "Verify the installer SHA-256",
        "do not bypass Smart App Control or managed policy",
    ),
    ROOT / "README.md": (
        "The `main` branch can contain",
        "an unreleased candidate",
        *README_BADGE_PREFLIGHT,
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

Before installing or launching macOS 0.3.8, disclose that a Hugging Face token inherited by Presspeech may be included in model-download requests; the public model needs no account token. If a token may be present in the environment that launches Presspeech—or the user is unsure—offer to wait until macOS 0.3.9 is published. Explain that an inherited lowercase `https_proxy` can route the model request through a proxy: a TLS-inspecting proxy trusted by macOS can read a 0.3.8 token, while a tunnelling proxy cannot. If the trust of a TLS-inspecting proxy is unclear, do not launch 0.3.8 while it is in use. Upcoming 0.3.9 removes account-token authentication but still honors proxy settings. Downloading the ZIP or installing the app without opening it does not make the model request; the first download of a missing model starts when Presspeech launches. Do not inspect or display token values, change credential settings, or launch 0.3.8 without the user's informed choice. If the user chooses to wait, skip the `open` command below and leave the app unopened. Model downloads do not include dictation audio or transcripts. See https://rcourtman.github.io/presspeech/privacy.html#network-calls.

Before downloading or installing, run these read-only compatibility checks:
  uname -m
  sw_vers -productVersion

Stop if the Mac is not Apple Silicon (arm64) or macOS is older than 14. Do not download, install, or launch Presspeech on an unsupported Mac.

Presspeech has two notarised install paths: a direct release zip and a Homebrew Cask. After the compatibility checks pass, check whether Homebrew is already available:
  command -v brew

If Homebrew is available and the user chooses it, install with:
  brew install --cask rcourtman/presspeech/presspeech

Otherwise offer the direct notarised zip using the current version-pinned download and verification steps at https://rcourtman.github.io/presspeech/install.html#direct-download. Do not install Homebrew just for Presspeech. After verification, unzip and move Presspeech.app to Applications, but do not follow the guide's launch instruction yet. Do not run the Homebrew command when Homebrew is unavailable.

Only after the user makes an informed choice to launch 0.3.8:
  open /Applications/Presspeech.app

After launch, explain that macOS 0.3.8 starts its first local speech-model download (~500-600 MB) on launch. In 0.3.9, a clean install must choose Download Model in Setup; choose Set Up Later to defer. Existing installs and cached models continue loading automatically. Before asking the user to enable Input Monitoring, explain that macOS's grant can expose typed keys; Presspeech requests keyboard events only to detect the configured hotkey and Escape to cancel an active recording, passes other keys through without saving, logging, or sending their values, and does not inspect mouse or trackpad events. Offer Apple's guide at https://support.apple.com/guide/mac-help/mchl4cedafb6/mac. Use Setup Checklist to finish the model, permissions, and hotkey readiness. The default dictation key is Right Option. Focus on setup and the first in-app test; explain that the scratchpad can still use the system clipboard and use only harmless words. Do not ask the user to star, review, or otherwise endorse the project."""

WINDOWS_INSTALL_PROMPT = r"""Install Presspeech from https://github.com/rcourtman/presspeech on this Windows PC.

Before installing or launching published Windows 0.1.12, explain that its model downloads may send Hugging Face usage telemetry and an already-configured or locally saved Hugging Face token; custom download routing can change where a request—and any token it carries—goes. The bundled HTTP client also honors configured HTTPS proxies; a TLS-inspecting HTTPS proxy trusted by the client can read any 0.1.12 token it receives. Upcoming 0.1.13 removes account-token authentication but still honors proxy and CA settings. If the user cannot confirm that a TLS-inspection proxy is trusted, don't launch while it is in use. These public models need no account token. Offer to wait until Windows 0.1.13 is published if the user prefers to avoid this possible usage telemetry, is concerned that a Hugging Face token or custom download route may be configured on this PC, or is unsure. Downloading the installer and checksum from GitHub does not make a model request; 0.1.12 starts its selected model download when Presspeech launches. If the user chooses to wait but still wants to install, tell them to uncheck the installer's final "Launch Presspeech" option; do not start the app. Do not inspect or display token values, change credential settings, or launch 0.1.12 without the user's informed choice. Dictation audio and transcripts are not sent in model downloads. See https://rcourtman.github.io/presspeech/privacy.html#network-calls.

Use only the published Windows prerelease selected by Presspeech's deployed metadata and version-pinned install guide:
  https://rcourtman.github.io/presspeech/windows.html#download-verify-run

The source branch can contain a newer unreleased candidate, so do not infer a download version from windows/config.py, release notes, or other files on main. The deployed metadata stays on a version whose installer and checksum are both public. The installer is not code-signed. Explain that before downloading; SHA-256 verification confirms that the file matches the asset published in this repository, but it is not a publisher signature.

Run these read-only checks in PowerShell:
  [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
  (Get-CimInstance Win32_OperatingSystem).Caption

Stop if the architecture is not X64. Windows 11 is recommended. If this is Windows 10, explain that general support has ended and continue only if the user confirms the PC has Extended Security Updates or an edition that remains supported.

Before downloading, explain the language and hardware split: a fresh system with usable NVIDIA CUDA selects multilingual Parakeet (~2.5 GB), while a fresh system without usable CUDA selects English-only Whisper base.en on CPU (~141 MiB). Published Windows 0.1.12 starts the selected model download on first launch. Upcoming 0.1.13 asks before downloading missing first-run default model files on either path; Setup offers deferral on both and the smaller CPU model on the Parakeet path. Other local models remain selectable in Settings, but the multilingual alternatives are intended for a supported NVIDIA GPU. If the user needs a language other than English and does not have usable NVIDIA CUDA, show them https://rcourtman.github.io/presspeech/windows.html#language-support and ask whether they still want to continue.

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

After the user completes the installer, launch Presspeech from the Start Menu only if they chose not to wait and explicitly confirmed launching 0.1.12. If they chose to wait, leave the app unopened and make sure the installer's final "Launch Presspeech" option was unchecked. Explain that first launch may download a local model (about 141 MiB on a fresh CPU-only PC or about 2.5 GB with usable NVIDIA CUDA; an incomplete cache may need less). With 0.1.13, Setup asks before downloading either missing first-run default model and offers Set Up Later; the Parakeet path also offers the smaller CPU model. With published 0.1.12, the model download starts automatically on first launch, so make sure the user understands the size before launching. Published 0.1.12 also checks the microphone automatically; upcoming 0.1.13 leaves it closed until the user chooses Check Microphone. Let them decide whether to run that test in versions that offer the button. Before choosing Finish Setup, Set Up Later, or closing Setup, explain that a new 0.1.12 profile selects Start with Windows by default and ask whether to turn it off; upcoming 0.1.13 defaults it off. Published 0.1.12 offers Press to toggle in Settings, not Setup; upcoming 0.1.13 offers it in Setup. If the user chooses Set Up Later, leave setup incomplete; in 0.1.12 this does not defer an already-started model download. Otherwise, finish Setup before testing the configured hotkey. Right Alt is the default; choose F8 or another available key if Right Alt acts as AltGr. Use Try Dictation for the first in-app test; explain that the scratchpad can still use the system clipboard and use only harmless words. Focus on setup and the first test; do not ask the user to star, review, or otherwise endorse the project."""


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


def remove_structured_download_urls(text: str) -> str:
    """Keep discovery metadata on the install guides, not bare release assets.

    Older snapshots advertised a direct unsigned Windows installer and called
    the macOS release landing page a binary downloadUrl. Neither route carries
    the current first-launch decision and Windows checksum instructions.
    """
    return re.sub(r'(?m)^[ \t]*"downloadUrl": "[^"]+",?\n', "", text)


def sync_index(path: Path, metadata: dict[str, object]) -> str:
    text = remove_structured_download_urls(read_text(path))
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
        "<p>Homebrew is the easiest path if you already use it or want command-line updates. On first launch, macOS shows its standard downloaded-app confirmation; choose <strong>Open</strong> after checking that it says Apple found no malicious software. The Presspeech icon then appears in the menu bar. The macOS 0.3.8 release starts its first ~500–600 MB model download on launch. In 0.3.9, a clean install must choose <strong>Download Model</strong> in Setup; choose <strong>Set Up Later</strong> to defer. Existing installs and cached models load automatically. If setup is not complete, Presspeech opens Setup Checklist; you can reopen it from the menu at any time.</p>",
        path=path,
    )
    text = replace_regex(
        text,
        r'<div class="fact"><strong>(?:Model download|First model download)</strong><span>.*?</span></div>',
        '<div class="fact"><strong>First model download</strong><span>Internet is required for the local model, about 500–600 MB. macOS 0.3.8 starts the first download on launch. In 0.3.9, a clean install must choose Download Model in Setup; choose Set Up Later to defer. Existing installs and cached models load automatically.</span></div>',
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
    text = remove_structured_download_urls(read_text(path))
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
        "<p><strong>macOS:</strong> Microphone, Accessibility (shown as Device Control and Data Access on macOS 27 and later), and Input Monitoring. Setup Checklist tracks each grant. <strong>Windows:</strong> Turn on Microphone access and Let desktop apps access your microphone. Some Windows 11 Experimental builds also provide per-app microphone controls for desktop apps; if shown, allow Presspeech there too. Windows may ask for microphone permission on first access; approve it only if you want Presspeech to use the microphone.</p>",
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

LLMS_SHORT_ANSWER = (
    "Presspeech is local push-to-talk dictation for Apple Silicon Macs and x64 Windows PCs. "
    "It transcribes on-device and pastes only when it can verify the original destination. "
    "If delivery is uncertain, macOS and published Windows 0.1.12 copy the transcript "
    "for manual paste; upcoming Windows 0.1.13 instead requires an explicit Delivery "
    "Recovery Copy or Discard and may leave the previous clipboard item unchanged. "
    "No account or cloud transcription is required. Before opening a published build "
    "with a missing model, read its launch decision: macOS 0.3.8 may include an "
    "inherited Hugging Face token in the model request. If a token may be present or "
    "you are unsure, wait for macOS 0.3.9. Windows 0.1.12 may send Hugging Face "
    "usage telemetry and an available token; custom routing can change the request "
    "destination. If you want to avoid possible telemetry, cannot rule out a token "
    "or custom route, or are unsure, wait for Windows 0.1.13. Neither newer build "
    "is published yet. A TLS-inspecting HTTPS proxy trusted by the client can read "
    "any token sent through it; do not launch through one whose trust is unclear. "
    "Downloading the app alone does not start a model request, but opening it with a "
    "missing model does. If installing Windows 0.1.12 while waiting, leave the final "
    "Launch Presspeech option unchecked. Review the macOS "
    "https://rcourtman.github.io/presspeech/install.html#model-download-privacy "
    "or Windows https://rcourtman.github.io/presspeech/windows.html#model-download-privacy "
    "warning before choosing to launch. Dictation audio and transcripts are not sent "
    "in model downloads."
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
    download_privacy_notice = (
        "- Before installing or launching macOS 0.3.8, model-download requests may include "
        "a Hugging Face token inherited by Presspeech. The public model needs no account "
        "token; if one may be present in the environment that launches Presspeech—or you "
        "are unsure—wait until macOS 0.3.9 is published. The pinned FluidAudio client "
        "honors inherited lowercase `https_proxy`; an untrusted TLS-inspecting proxy could "
        "read a 0.3.8 token, while a tunnelling proxy cannot. If the proxy's trust is unclear, "
        "do not launch 0.3.8 while it is in use. Upcoming 0.3.9 removes account-token "
        "authentication but still honors proxy settings. See "
        "https://rcourtman.github.io/presspeech/install.html#model-download-privacy.\n"
    )
    if download_privacy_notice not in text:
        marker = "- macOS latest published download:"
        if marker not in text:
            marker = "- Homebrew install:"
        text = replace_literal(text, marker, download_privacy_notice + marker, path=path)
    privacy_line = (
        "- Privacy: no cloud transcription or Presspeech-authored analytics, and no transcript persistence; "
        "macOS 0.3.8 may attach an inherited Hugging Face token to model-download requests, "
        "although the public model needs no account token. If a token may be present in the environment "
        "that launches Presspeech—or you are unsure—wait until macOS 0.3.9 is published; dictation audio "
        "and transcripts are not sent in those requests. If macOS 0.3.8 already downloaded a model "
        "with a token available and an untrusted TLS-inspecting proxy could read the request, "
        "treat the token as disclosed to that proxy and revoke it at Hugging Face Access Tokens. "
        "Leave a working "
        "model cache in place and wait for 0.3.9 before a planned re-download; see "
        "https://rcourtman.github.io/presspeech/privacy.html#macos-0-3-8-after-use. "
        "During Windows 0.1.12 model downloads, bundled libraries may send default usage telemetry to "
        "Hugging Face. If you prefer to avoid this possible usage telemetry, are concerned that a token "
        "or custom route may be configured, or are unsure, wait until Windows 0.1.13 is published. "
        "Model-request metadata includes a random per-process session ID, and pinned Hub "
        "1.29.0 may request /api/agent-harnesses and add an agent/<id> label based on inherited "
        "agent-related environment markers. An available "
        "Hugging Face token may accompany a model request. Inherited HF_ENDPOINT and "
        "HUGGINGFACE_CO_STAGING settings can change its destination, and HF_HUB_USER_AGENT_ORIGIN "
        "is included in request metadata if set; the token may accompany a request to that configured "
        "endpoint. These public models do not need an account token. Upcoming Windows 0.1.13 fixes "
        "these inherited settings but is not yet published. Both published Windows 0.1.12 and upcoming Windows 0.1.13 use HTTPX 0.28.1 with the Hub client's default trust_env=True; it honors HTTP_PROXY, HTTPS_PROXY, ALL_PROXY, NO_PROXY, SSL_CERT_FILE, and SSL_CERT_DIR, and 0.1.13 does not clear those proxy/CA settings. A TLS-inspecting HTTPS proxy whose CA is trusted by the client can read a model request and any 0.1.12 token it carries; a proxy that only tunnels HTTPS sees connection metadata, not request contents. Published Windows 0.1.12 does not independently verify model-file contents against SHA-256. Upcoming Windows 0.1.13 verifies every allowed required and present optional inference file before model loading: it checks each against its pinned SHA-256 manifest and reuses a local verification record only while file identity and metadata remain unchanged. Files without a matching record are rehashed, and mismatches fail closed; a proxy can still observe request metadata or block a download. Dictation audio "
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
    if text.count("Best short answer:\n") != 1:
        raise SyncError(f"{path}: expected one Best short answer section")
    text = re.sub(
        r"(?ms)^Best short answer:\n.*?(?=^If you are an agent installing Presspeech|\Z)",
        "Best short answer:\n" + LLMS_SHORT_ANSWER + "\n\n",
        text,
        count=1,
    )
    return text


def sync_llms_full(path: Path, metadata: dict[str, object]) -> str:
    del metadata
    text = read_text(path)
    macos_install_notice = (
        "Before installing or launching macOS 0.3.8, its model-download requests may "
        "include a Hugging Face token inherited by Presspeech. The public model needs "
        "no account token. If one may be present in the environment that launches "
        "Presspeech—or you are unsure—wait until macOS 0.3.9 is published. "
        "The pinned FluidAudio client also honors inherited lowercase `https_proxy`; "
        "an untrusted TLS-inspecting proxy could read a 0.3.8 token, while a tunnelling "
        "proxy cannot. If the proxy's trust is unclear, do not launch 0.3.8 while it is "
        "in use. Upcoming 0.3.9 removes account-token authentication but still honors "
        "proxy settings. Review the "
        "[current privacy decision](https://rcourtman.github.io/presspeech/"
        "install.html#model-download-privacy) first.\n\n"
    )
    macos_install_heading = "### macOS\n\n"
    if macos_install_heading in text and macos_install_notice not in text:
        text = replace_literal(
            text, macos_install_heading,
            macos_install_heading + macos_install_notice, path=path,
        )
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
    privacy_paragraph = text.partition("## Privacy")[2].lstrip().partition("\n\n")[0]
    if "HTTP_PROXY" not in privacy_paragraph:
        proxy_summary = (
            " Both published Windows 0.1.12 and upcoming Windows 0.1.13 use HTTPX 0.28.1 with the Hub client's default trust_env=True; it honors HTTP_PROXY, HTTPS_PROXY, ALL_PROXY, NO_PROXY, SSL_CERT_FILE, and SSL_CERT_DIR, and 0.1.13 does not clear those proxy/CA settings. A TLS-inspecting HTTPS proxy whose CA is trusted by the client can read a model request and any 0.1.12 token it carries; a proxy that only tunnels HTTPS sees connection metadata, not request contents."
        )
        marker = "Upcoming Windows 0.1.13 fixes these inherited settings but is not yet published."
        if marker in privacy_paragraph:
            text = text.replace(marker, marker + proxy_summary, 1)
    privacy_paragraph = text.partition("## Privacy")[2].lstrip().partition("\n\n")[0]
    legacy_integrity_summary = (
        "Upcoming Windows 0.1.13 verifies every allowed required and present optional inference file "
        "against its pinned SHA-256 manifest before model loading; modified model bytes fail closed, "
        "although a proxy can still observe request metadata or block a download."
    )
    integrity_detail = (
        "Upcoming Windows 0.1.13 verifies every allowed required and present optional inference file "
        "before model loading by checking each against its pinned SHA-256 manifest; it reuses a local "
        "verification record only while file identity and metadata remain unchanged. Files without a "
        "matching record are rehashed, and mismatches fail closed, although a proxy can still observe "
        "request metadata or block a download."
    )
    if legacy_integrity_summary in privacy_paragraph:
        text = text.replace(legacy_integrity_summary, integrity_detail, 1)
    elif "does not independently verify model-file contents against SHA-256" not in privacy_paragraph:
        integrity_summary = (
            " Published Windows 0.1.12 does not independently verify model-file contents against SHA-256. "
            + integrity_detail
        )
        marker = "Upcoming Windows 0.1.13 fixes these inherited settings but is not yet published."
        if marker in privacy_paragraph:
            text = text.replace(marker, marker + integrity_summary, 1)
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
            "If you prefer to avoid this possible usage telemetry, are concerned that a token or custom "
            "route may be configured, or are unsure, wait for Windows 0.1.13 before launching 0.1.12. "
            "Dictation audio and transcripts are not sent in model downloads; exact telemetry "
            "fields are not independently itemised. An available HF_TOKEN, HUGGING_FACE_HUB_TOKEN, or token in the local Hugging Face cache may accompany a model request. The 0.1.12 loader honors inherited HF_ENDPOINT and HUGGINGFACE_CO_STAGING settings, which can change its destination; if HF_HUB_USER_AGENT_ORIGIN is set, its value is included in request metadata. These models do not require an account token. Upcoming Windows 0.1.13 fixes these inherited settings but is not yet published. Audio is captured while the hotkey is "
            "active, transcribed locally, then discarded.",
            path=path,
        )
    download_sentence = (
        "The macOS 0.3.8 release starts its first speech-model download (about 500-600 MB) "
        "on launch. In 0.3.9, a clean install must choose Download Model in Setup; "
        "choosing Set Up Later defers it. Existing installations and cached models load automatically. "
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
        "Use Setup Checklist from the Presspeech menu-bar item or Dock menu to finish "
        "the speech model, Microphone, Accessibility (Device Control and Data Access "
        "on macOS 27+), Input Monitoring, and hotkey readiness checks. Settings → "
        "Behavior → Show Presspeech in Menu Bar hides the status item and enables "
        "Dock access if needed; the Dock menu can restore it.\n"
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


def check_anchored_install_preflights(
    metadata: dict[str, object],
    surfaces: tuple[dict[str, object], ...] = ANCHORED_INSTALL_PREFLIGHTS,
) -> list[str]:
    """Keep release-specific privacy decisions visible at deep-linked install steps."""
    errors: list[str] = []
    for surface in surfaces:
        path = Path(surface["path"])
        display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
        platform = str(surface["platform"])
        version = metadata.get(str(surface["version_key"]))
        if version != surface["reviewed_version"]:
            errors.append(
                f"{display}: review the {platform} anchored install preflight "
                f"for release {version} before publishing"
            )
            continue
        if not path.exists():
            errors.append(f"{display}: missing {platform} install guide")
            continue
        contents = read_text(path)
        anchor = f'<section id="{surface["anchor"]}">'
        start = contents.find(anchor)
        end = contents.find("</section>", start + len(anchor)) if start >= 0 else -1
        if start < 0 or end < 0:
            errors.append(f"{display}: missing #{surface['anchor']} install section")
            continue
        section = contents[start:end]
        note = re.search(
            rf'<div class="note warn" data-install-preflight="{platform}">(.*?)</div>',
            section,
            flags=re.S,
        )
        if note is None:
            errors.append(
                f"{display}: #{surface['anchor']} needs a local model-download "
                "privacy reminder before the install steps"
            )
            continue
        visible = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", note.group(1))).split())
        missing = [phrase for phrase in surface["required"] if phrase not in visible]
        if 'href="#model-download-privacy"' not in note.group(1):
            missing.append("link to #model-download-privacy")
        if missing:
            errors.append(
                f"{display}: #{surface['anchor']} has incomplete release-specific "
                "preflight — missing " + ", ".join(repr(phrase) for phrase in missing)
            )
        download_name = (
            r"Presspeech\.zip" if platform == "macos"
            else r'Presspeech-Setup-[^"/]+-x64\.exe'
        )
        download = re.search(
            rf'<a href="https://github\.com/rcourtman/presspeech/releases/download/[^"/]+/{download_name}">',
            contents[start:],
        )
        if download is None or note.start() > download.start():
            errors.append(
                f"{display}: #{surface['anchor']} privacy reminder must "
                "precede the direct download link"
            )
        if platform == "macos":
            if (
                "If you chose to wait" not in section
                or "leave it unopened" not in section
                or "Otherwise, open it" not in section
            ):
                errors.append(
                    f"{display}: direct-download launch step must keep the "
                    "wait-without-opening option"
                )
            homebrew = contents.find("<h2>Homebrew install and launch</h2>")
            homebrew_note = contents.find(
                "<strong>Before the <code>open</code> command:</strong>", homebrew
            )
            homebrew_code = contents.find(
                "<pre><code>brew install --cask rcourtman/presspeech/presspeech",
                homebrew,
            )
            if not (0 <= homebrew < homebrew_note < homebrew_code) or not all(
                phrase in contents[homebrew_note:homebrew_code]
                for phrase in (
                    "macOS 0.3.8",
                    "skip <code>open</code>",
                    'href="#model-download-privacy"',
                )
            ):
                errors.append(
                    f"{display}: Homebrew open command needs the release-specific "
                    "wait-without-opening reminder first"
                )
        else:
            launch_step = re.search(
                r"<li>\s*<strong>Decide whether to launch 0\.1\.12</strong>\s*<p>(.*?)</p>",
                contents[end:],
                flags=re.S,
            )
            if launch_step is None or not all(
                phrase in launch_step.group(1)
                for phrase in (
                    "If you chose to wait",
                    "leave the installer’s final <strong>Launch Presspeech</strong> option unchecked",
                    "do not open the app",
                    "If you choose to use published 0.1.12 now",
                )
            ):
                errors.append(
                    f"{display}: Windows launch step must keep the "
                    "wait-without-launching option"
                )
    return errors


def check_macos_upgrade_preflight(
    metadata: dict[str, object],
    page: Path = DOCS / "install.html",
    reviewed_version: str = "0.3.8",
) -> list[str]:
    """An upgrade can fetch a missing/invalid model, so deep links need a stop choice."""
    display = page.relative_to(ROOT) if page.is_relative_to(ROOT) else page.name
    if metadata.get("version") != reviewed_version:
        return [f"{display}: review the macOS upgrade launch decision for release {metadata.get('version')}"]
    if not page.exists():
        return [f"{display}: missing macOS upgrade guide"]
    contents = read_text(page)
    match = re.search(r'<section id="upgrade">(.*?)</section>', contents, flags=re.S)
    if match is None:
        return [f"{display}: missing #upgrade section"]
    section = match.group(1)
    note = re.search(
        r'<div class="note warn" data-install-preflight="macos-upgrade">(.*?)</div>',
        section,
        flags=re.S,
    )
    if note is None or note.start() > section.find('<div class="grid two">'):
        return [f"{display}: #upgrade needs a local privacy decision before upgrade launch steps"]
    visible = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", note.group(1))).split())
    required = (
        "macOS 0.3.8", "cached model loads without a download",
        "missing or fails integrity checks", "model request at launch",
        "inherited Hugging Face token", "or you are unsure",
        "leave the upgraded app unopened", "wait until macOS 0.3.9 is published",
        "TLS-inspecting proxy",
    )
    errors = [
        f"{display}: #upgrade privacy decision is missing {phrase!r}"
        for phrase in required if phrase not in visible
    ]
    if 'href="#model-download-privacy"' not in note.group(1):
        errors.append(f"{display}: #upgrade must link to the full launch decision")
    cards = re.findall(r'<article class="card">(.*?)</article>', section, flags=re.S)
    if len(cards) != 2 or any(
        "If you decide to launch after reading the warning above" not in card
        for card in cards
    ):
        errors.append(f"{display}: #upgrade must make both launch steps conditional")
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


MODEL_DOWNLOAD_FIRST_RUN_CONTROL_EXPECTATIONS = {
    "macos_first_launch_model_download": {
        "version_key": "version",
        "published": [
            {
                "model": "Parakeet TDT v3 CoreML",
                "trigger": "automatic_on_launch",
                "can_defer": False,
            }
        ],
        "upcoming": [
            {
                "model": "Parakeet TDT v3 CoreML",
                "trigger": "explicit_setup_choice_before_download",
                "can_defer": True,
            }
        ],
    },
    "windows_first_launch_model_download": {
        "version_key": "windows_version",
        "published": [
            {
                "model": "Selected local model",
                "trigger": "automatic_on_first_launch",
                "can_defer": False,
            }
        ],
        "upcoming": [
            {
                "model": "Multilingual Parakeet TDT v3",
                "trigger": "explicit_setup_choice_before_download",
                "can_defer": True,
            },
            {
                "model": "CPU-default Whisper base.en",
                "trigger": "explicit_setup_choice_before_download",
                "can_defer": True,
            },
        ],
    },
}

# Revisit this matrix whenever the current or candidate release changes. The
# CI gate deliberately requires a policy review instead of inferring consent
# behavior from version numbers or generic call-level flags.
MODEL_DOWNLOAD_SCHEMA_DESCRIPTION = (
    "first_run_controls_by_release lists the missing-model action for each release. "
    "can_defer applies only to that model's first-run path, not to later integrity "
    "retries, cache resets, or existing-install startup"
)

MODEL_DOWNLOAD_FIRST_RUN_GUIDANCE = (
    "macOS 0.3.8 and Windows 0.1.12 start a missing-model download automatically",
    "a clean install in upcoming macOS 0.3.9 asks you to choose Download Model or Set Up Later to defer",
    "upcoming Windows 0.1.13 asks before fetching missing files for either first-run default",
    "cached models load without a prompt",
    "Existing macOS installs keep automatic startup",
    "The machine-readable inventory lists these controls per release and model",
    "Schema 1 lists model-download choices by release and missing model",
    "Its can_defer flag covers only that model's first-run path",
)


def check_model_download_first_run_controls(
    metadata: dict[str, object],
    inventory_path: Path = DOCS / "privacy" / "network-calls.json",
    privacy_page_path: Path = DOCS / "privacy.html",
) -> list[str]:
    """Keep model-download consent metadata release- and model-specific."""
    errors: list[str] = []
    inventory_display = (
        inventory_path.relative_to(ROOT)
        if inventory_path.is_relative_to(ROOT)
        else inventory_path.name
    )
    try:
        inventory = json.loads(read_text(inventory_path))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{inventory_display}: cannot read model-download controls: {exc}"]
    if (
        not isinstance(inventory, dict)
        or type(inventory.get("schema_version")) is not int
        or inventory.get("schema_version") != 1
    ):
        errors.append(f"{inventory_display}: expected network inventory schema_version 1")
        return errors
    if inventory.get("schema_description") != MODEL_DOWNLOAD_SCHEMA_DESCRIPTION:
        errors.append(f"{inventory_display}: missing model-control schema semantics")

    calls = inventory.get("network_calls")
    if not isinstance(calls, list):
        return [f"{inventory_display}: network_calls must be a list"]

    for call_name, expectation in MODEL_DOWNLOAD_FIRST_RUN_CONTROL_EXPECTATIONS.items():
        matches = [
            call for call in calls
            if isinstance(call, dict) and call.get("name") == call_name
        ]
        if len(matches) != 1:
            errors.append(
                f"{inventory_display}: expected one {call_name} entry, found {len(matches)}"
            )
            continue
        call = matches[0]
        if "user_triggered" in call or "can_disable" in call:
            errors.append(
                f"{inventory_display}: {call_name} must use per-release controls, not blanket booleans"
            )
        controls = call.get("first_run_controls_by_release")
        if not isinstance(controls, list) or len(controls) != 2:
            errors.append(
                f"{inventory_display}: {call_name} must list published and upcoming controls"
            )
            continue

        expected_current = metadata.get(str(expectation["version_key"]))
        if not isinstance(expected_current, str) or not re.fullmatch(
            r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", expected_current
        ):
            errors.append(
                f"{inventory_display}: missing canonical current version for {call_name}"
            )
            continue
        try:
            current_parts = tuple(int(part) for part in expected_current.split("."))
        except ValueError:
            errors.append(
                f"{inventory_display}: invalid current version for {call_name}"
            )
            continue

        by_status: dict[str, dict[str, object]] = {}
        for control in controls:
            if not isinstance(control, dict):
                continue
            status = control.get("status")
            if not isinstance(status, str):
                continue
            if status in by_status:
                errors.append(
                    f"{inventory_display}: duplicate {status} control for {call_name}"
                )
            elif status in ("published", "upcoming"):
                by_status[str(status)] = control
        if set(by_status) != {"published", "upcoming"}:
            errors.append(
                f"{inventory_display}: {call_name} needs one published and one upcoming release"
            )
            continue

        published = by_status["published"]
        upcoming = by_status["upcoming"]
        if published.get("version") != expected_current:
            errors.append(
                f"{inventory_display}: {call_name} published controls must match "
                f"current version {expected_current}"
            )
        upcoming_version = upcoming.get("version")
        if not isinstance(upcoming_version, str) or not re.fullmatch(
            r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", upcoming_version
        ):
            errors.append(
                f"{inventory_display}: {call_name} upcoming version is not canonical"
            )
        elif tuple(int(part) for part in upcoming_version.split(".")) <= current_parts:
            errors.append(
                f"{inventory_display}: {call_name} upcoming controls must be newer than "
                f"{expected_current}"
            )

        for status in ("published", "upcoming"):
            actual = by_status[status].get("missing_model_controls")
            expected = expectation[status]
            if actual != expected:
                errors.append(
                    f"{inventory_display}: {call_name} {status} first-run model controls "
                    "do not match the documented release behavior"
                )

    privacy_display = (
        privacy_page_path.relative_to(ROOT)
        if privacy_page_path.is_relative_to(ROOT)
        else privacy_page_path.name
    )
    try:
        page = read_text(privacy_page_path)
    except OSError as exc:
        return errors + [f"{privacy_display}: cannot read model-download controls: {exc}"]
    visible_copy = " ".join(
        html.unescape(re.sub(r"<[^>]*>", " ", page)).split()
    ).casefold()
    missing_guidance = [
        phrase for phrase in MODEL_DOWNLOAD_FIRST_RUN_GUIDANCE
        if phrase.casefold() not in visible_copy
    ]
    if missing_guidance:
        errors.append(
            f"{privacy_display}: missing release-specific model-download control guidance — "
            + ", ".join(repr(phrase) for phrase in missing_guidance)
        )
    return errors


def check_user_triggered_support_guide() -> list[str]:
    """Keep the disclosed feedback destination aligned with both app menus."""
    errors: list[str] = []
    destination = "github.com/rcourtman/presspeech/blob/main/SUPPORT.md"
    inventory_path = DOCS / "privacy" / "network-calls.json"
    inventory = json.loads(read_text(inventory_path))
    calls = inventory.get("network_calls", [])
    matches = [call for call in calls if call.get("name") == "user_triggered_support_guide"]
    if len(matches) != 1 or matches[0].get("destination") != destination:
        errors.append("docs/privacy/network-calls.json: support-guide destination is missing or mismatched")
    swift = read_text(ROOT / "swift" / "Sources" / "Presspeech" / "main.swift")
    windows = read_text(ROOT / "windows" / "app.py")
    if (
        f"https://{destination}" not in swift
        or swift.count("NSWorkspace.shared.open(GITHUB_SUPPORT_GUIDE_PAGE)") < 2
    ):
        errors.append("macOS feedback actions no longer match the disclosed support guide")
    if (
        f"https://{destination}" not in windows
        or windows.count("return self._open_support_page(SUPPORT_GUIDE_URL)") < 2
    ):
        errors.append("Windows feedback actions no longer match the disclosed support guide")
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


def check_first_run_action_copy(
    required_surfaces: dict[Path, tuple[str, ...]] = FIRST_RUN_ACTION_COPY,
    forbidden_surfaces: dict[Path, tuple[re.Pattern[str], ...]] = FIRST_RUN_ACTION_FORBIDDEN,
) -> list[str]:
    errors: list[str] = []
    for path in sorted(required_surfaces.keys() | forbidden_surfaces.keys()):
        display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
        try:
            contents = read_text(path)
        except OSError as exc:
            errors.append(f"{display}: cannot read first-run guidance: {exc}")
            continue
        for phrase in required_surfaces.get(path, ()):
            if phrase not in contents:
                errors.append(f"{display}: missing first-run action boundary {phrase!r}")
        for pattern in forbidden_surfaces.get(path, ()):
            if pattern.search(contents):
                errors.append(f"{display}: first-run action misstates the published build or combines install and launch")
    return errors


def check_onboarding_release_scope(
    metadata: dict[str, object],
    reviewed: dict[str, str] = ONBOARDING_RELEASE_COPY_REVIEWED,
) -> list[str]:
    errors: list[str] = []
    for key, version in reviewed.items():
        if metadata.get(key) != version:
            errors.append(
                "docs/index.html and docs/getting-started.html: first-run steps "
                f"reviewed for {key} {version}, not {metadata.get(key)!r}; "
                "review both pages and update ONBOARDING_RELEASE_COPY_REVIEWED"
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


def check_readme_windows_install_decision_order(
    path: Path = ROOT / "README.md",
) -> list[str]:
    """Keep Windows privacy and unsigned-build decisions before README install steps."""
    display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
    if not path.exists():
        return [f"{display}: missing Windows install section"]

    contents = read_text(path)
    heading = "## Install on Windows"
    start = contents.find(heading)
    if start < 0:
        return [f"{display}: missing Windows install section"]
    end = contents.find("\n## ", start + len(heading))
    section = contents[start:end] if end >= 0 else contents[start:]

    warnings = (
        ("model-download privacy", "**Before installing or launching Windows 0.1.12:**"),
        ("unsigned-installer policy", "The installer is currently unsigned"),
    )
    actions = (
        "Download the self-contained installer",
        "- After verification, run the installer.",
        "- If a shell-capable assistant is doing the installation",
    )
    warning_positions: list[tuple[str, int]] = []
    errors: list[str] = []
    for label, marker in warnings:
        position = section.find(marker)
        if position < 0:
            errors.append(f"{display}: missing Windows {label} warning")
        else:
            warning_positions.append((label, position))

    for action in actions:
        action_position = section.find(action)
        if action_position < 0:
            errors.append(f"{display}: missing Windows install step {action!r}")
            continue
        for label, warning_position in warning_positions:
            if warning_position > action_position:
                errors.append(
                    f"{display}: Windows {label} warning must precede "
                    "installer download, run, and assistant instructions"
                )
                break
    launch_start = section.find("- After verification, run the installer.")
    if launch_start >= 0:
        launch_end = section.find("\n- ", launch_start + 1)
        launch_step = " ".join(
            (section[launch_start:launch_end] if launch_end >= 0 else section[launch_start:]).split()
        )
        decision_markers = (
            "If you choose to wait for 0.1.13",
            "clear **Launch Presspeech**",
            "leave the app unopened",
            "If you choose to launch 0.1.12 after reviewing the privacy decision above",
            "a missing selected-model download begins without another prompt",
        )
        positions = [launch_step.find(marker) for marker in decision_markers]
        if any(position < 0 for position in positions) or positions != sorted(positions):
            errors.append(
                f"{display}: Windows installer step must keep the wait/unopened choice "
                "before the explicit launch and automatic model-download instructions"
            )
    return errors


def check_faq_install_privacy_order(path: Path = DOCS / "faq.html") -> list[str]:
    """Keep the FAQ's release warnings ahead of safe guide routes."""
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
                'href="install.html#model-download-privacy"',
                'href="install.html"',
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
    if (
        "releases/latest/download/Presspeech.zip" in answer
        or "brew install --cask" in answer
    ):
        errors.append(
            f"{display}: FAQ must not bypass the macOS privacy decision "
            "with a direct download"
        )
    return errors


def check_homepage_launch_decision(path: Path = DOCS / "index.html") -> list[str]:
    """Keep the first-launch choice scannable and ahead of homepage actions."""
    display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
    if not path.exists():
        return [f"{display}: missing homepage launch decision"]

    contents = read_text(path)
    start = contents.find('<div class="note warn launch-decision" role="region"')
    end = contents.find("</div>", start) if start >= 0 else -1
    actions = contents.find('<div class="actions">')
    if start < 0 or end < 0 or actions < 0 or end > actions:
        return [f"{display}: first-launch decision must precede homepage actions"]

    panel = contents[start:end]
    required = (
        'aria-labelledby="launch-decision-title"',
        '<h2 id="launch-decision-title">Before opening a current build</h2>',
        '<strong>Unsure? Leave it unopened.</strong>',
        "Downloading the ZIP or installer does not start a speech-model request",
        "first launch with a missing model does",
        "On Windows, clear the installer’s final <strong>Launch Presspeech</strong> option if you choose to wait",
        "macOS 0.3.8 — inherited token",
        "wait until macOS 0.3.9 is published",
        'href="install.html#model-download-privacy"',
        "Windows 0.1.12 — telemetry or token",
        "wait until Windows 0.1.13 is published",
        'href="windows.html#model-download-privacy"',
    )
    missing = [phrase for phrase in required if phrase not in panel]
    if missing:
        return [
            f"{display}: incomplete homepage first-launch decision — missing "
            + ", ".join(repr(phrase) for phrase in missing)
        ]
    return []


def check_getting_started_preflight_order(
    path: Path = DOCS / "getting-started.html",
) -> list[str]:
    """Keep install/setup shortcuts behind the current model-download decision."""
    display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
    if not path.exists():
        return [f"{display}: missing getting-started model-download preflight"]

    contents = read_text(path)
    preflight_start = contents.find('id="model-download-preflight"')
    if preflight_start < 0:
        return [f"{display}: missing model-download preflight before onboarding actions"]
    preflight_end = contents.find("</div>", preflight_start)
    preflight = (
        contents[preflight_start:]
        if preflight_end < 0
        else contents[preflight_start:preflight_end]
    )
    required = (
        "Downloading is not launching",
        "macOS 0.3.8 and Windows 0.1.12 start a missing-model request when Presspeech opens, not when you download the ZIP or installer",
        "You can download a build and leave it unopened",
        "Unsure about a token, telemetry, or proxy? Keep it closed",
        "Wait for published 0.3.9",
        "a Hugging Face token may be inherited by Presspeech",
        "Do not launch 0.3.8 while using a TLS-inspecting proxy whose trust is unclear",
        "A model request can include the inherited token",
        "lowercase <code>https_proxy</code>",
        "TLS-inspecting proxy trusted by macOS can read that token",
        "Wait for published 0.1.13",
        "want to avoid possible Hugging Face usage telemetry",
        "an available token or custom download route may be configured",
        "A custom route can change where the request and token go",
        "TLS-inspecting proxy trusted by the client can read the token",
        "If such a proxy's trust is unclear, do not launch while it is in use",
        "upcoming build disables bundled-library telemetry and account-token authentication but still honors proxy and CA settings",
        "leave <strong>Launch Presspeech</strong> unchecked",
        "public models need no account token",
        "model requests do not include dictation audio or transcripts",
        "Do not inspect or share token values",
        "not published yet",
        'href="https://github.com/rcourtman/presspeech/releases"',
        'href="install.html#model-download-privacy"',
        'href="windows.html#model-download-privacy"',
        'href="#macos-launch-decision"',
        'href="#windows-launch-decision"',
        '<li id="macos-launch-decision">',
        '<li id="windows-launch-decision">',
        'role="region" aria-labelledby="launch-decision-heading"',
        '<h2 id="launch-decision-heading">',
        '<h3>macOS 0.3.8 — wait if a token may be inherited</h3>',
        '<h3>Windows 0.1.12 — wait if privacy risks are unclear</h3>',
    )
    missing = [phrase for phrase in required if phrase not in preflight]
    if missing:
        return [
            f"{display}: incomplete model-download preflight — missing "
            + ", ".join(repr(phrase) for phrase in missing)
        ]

    for platform in ("macos", "windows"):
        link = preflight.find(f'href="#{platform}-launch-decision"')
        decision = preflight.find(f'<li id="{platform}-launch-decision">')
        if link > decision:
            return [
                f"{display}: {platform} launch-decision shortcut must precede "
                "its warning"
            ]

    actions = contents.find('<div class="actions">')
    if actions < 0 or preflight_end < 0 or preflight_end > actions:
        return [
            f"{display}: model-download preflight must precede the first onboarding actions"
        ]

    setup_actions = [
        match.start() for match in re.finditer(r'href="#finish-setup"', contents)
    ]
    if not setup_actions or any(position < preflight_end for position in setup_actions):
        return [
            f"{display}: setup shortcuts must follow the model-download decision"
        ]
    return []


def check_getting_started_entry_links(docs: Path = DOCS) -> list[str]:
    """Other pages must not send a first-time reader past the launch decision."""
    errors: list[str] = []
    for path in sorted(docs.rglob("*.html")):
        if path == docs / "getting-started.html":
            continue
        for match in re.finditer(r"""href\s*=\s*(["'])([^"']+)\1""", read_text(path)):
            href = match.group(2)
            if re.search(r"(?:^|/)getting-started\.html#", href) and not href.endswith(
                "#model-download-preflight"
            ):
                errors.append(
                    f"{path.relative_to(docs)}: onboarding link bypasses the "
                    f"first-launch decision: {href}"
                )
    return errors


def check_getting_started_scratchpad_privacy_order(
    path: Path = DOCS / "getting-started.html",
) -> list[str]:
    """Disclose clipboard exposure before the first in-app dictation step."""
    display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
    if not path.exists():
        return [f"{display}: missing scratchpad clipboard guidance"]

    contents = read_text(path)
    section_start = contents.find('<section id="private-test">')
    section_end = contents.find("</section>", section_start)
    if section_start < 0 or section_end < 0:
        return [f"{display}: missing first-dictation practice section"]
    section = contents[section_start:section_end]
    warning_start = section.find('id="scratchpad-clipboard-boundary"')
    first_step = section.find('<ol class="steps">')
    if warning_start < 0 or first_step < 0 or warning_start > first_step:
        return [f"{display}: scratchpad clipboard warning must precede practice steps"]

    warning_end = section.find("</div>", warning_start)
    warning = section[warning_start:warning_end] if warning_end >= 0 else ""
    required = (
        "not a guarantee that the system clipboard is untouched",
        "macOS 0.3.8",
        "Windows 0.1.12",
        "macOS Universal Clipboard",
        "Windows Clipboard History",
        "non-sensitive test words",
        'href="privacy.html#operating-system-clipboard-services"',
    )
    missing = [phrase for phrase in required if phrase not in warning]
    if missing:
        return [
            f"{display}: incomplete scratchpad clipboard warning — missing "
            + ", ".join(repr(phrase) for phrase in missing)
        ]
    return []


SCRATCHPAD_PRIVACY_OVERCLAIM = re.compile(
    r"\b(?:private\s+(?:scratchpad|test|first\s+dictation)"
    r"|first\s+private\s+dictation|test\s+privately"
    r"|private,\s*click-driven\s+test"
    r"|transcript\s+stays\s+in\s+that\s+private\s+window)\b",
    re.I,
)


def check_scratchpad_privacy_claims(paths: list[Path] | None = None) -> list[str]:
    """Do not imply the in-app test bypasses the shared system clipboard."""
    if paths is None:
        paths = [ROOT / "README.md", ROOT / "windows" / "README.md"] + [
            path for path in DOCS.rglob("*")
            if path.is_file() and path.suffix in {".html", ".md", ".txt"}
        ]
    errors: list[str] = []
    for path in paths:
        content = html.unescape(re.sub(r"<[^>]+>", " ", read_text(path)))
        if SCRATCHPAD_PRIVACY_OVERCLAIM.search(content):
            display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
            errors.append(
                f"{display}: in-app scratchpad is not clipboard-private; "
                "use 'in-app test' and harmless practice words"
            )
    return errors


def check_model_recovery_privacy_order(
    html_path: Path = DOCS / "troubleshooting.html",
    markdown_path: Path = DOCS / "troubleshooting.md",
) -> list[str]:
    """Keep repeat-download decisions ahead of troubleshooting actions and deep links."""
    errors: list[str] = []
    sections = (
        (
            html_path,
            (
                ('id="start-here"', "</section>", "Reopen Presspeech's controls", (
                    "Check before model recovery", "reopening a published build with a missing model",
                    "If you choose to wait with a missing or damaged model, do not reopen",
                    "Model requests do not include dictation audio or transcripts",
                )),
                ('id="macos-model"', "</article>", "check the connection before retrying", (
                    "Before reopening, retrying, or resetting macOS 0.3.8",
                    "another download may include a Hugging Face token inherited by Presspeech",
                    "do not start another download", "macOS 0.3.9 is published and installed",
                    "privacy.html#macos-0-3-8-after-use",
                    "If you choose to make another model request after reading the warning above",
                )),
                ('id="windows-model"', "</article>", "Retry Speech Model", (
                    "Before reopening, retrying, or selecting an uncached model in Windows 0.1.12",
                    "usage telemetry", "already-configured or saved token",
                    "custom routing can change its destination", "TLS-inspecting HTTPS proxy",
                    "do not start another download", "Windows 0.1.13 is published and installed",
                    "windows.html#model-download-privacy",
                    "If you choose to make another model request after reading the warning above",
                )),
            ),
        ),
        (
            markdown_path,
            (
                ("## Start Here", "\n## ", "Reopen Presspeech's controls", (
                    "Check before model recovery", "Reopening a published build with a missing model",
                    "If you choose to wait with a missing or damaged model, do not reopen",
                    "Model requests do not include dictation audio or transcripts",
                )),
                ("### Speech Model Fails To Load", "\n### ", "check the connection before retrying", (
                    "Before reopening, retrying, or resetting macOS 0.3.8",
                    "Another download may include a Hugging Face token inherited by Presspeech",
                    "do not start another download", "macOS 0.3.9 is published and installed",
                    "privacy.html#macos-0-3-8-after-use",
                    "If you choose to make another model request after reading the warning above",
                )),
                ("### Speech Model Is Preparing Or Failed", "\n### ", "Retry Speech Model", (
                    "Before reopening, retrying, or selecting an uncached model in Windows 0.1.12",
                    "usage telemetry", "already-configured or saved token",
                    "custom routing can change its destination", "TLS-inspecting HTTPS proxy",
                    "do not start another download", "Windows 0.1.13 is published and installed",
                    "windows.html#model-download-privacy",
                    "If you choose to make another model request after reading the warning above",
                )),
            ),
        ),
    )
    for path, checks in sections:
        display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
        if not path.exists():
            errors.append(f"{display}: missing model recovery guidance")
            continue
        raw = read_text(path)
        for start_marker, end_marker, action, required in checks:
            start = raw.find(start_marker)
            end = raw.find(end_marker, start + len(start_marker)) if start >= 0 else -1
            if start < 0 or end < 0:
                errors.append(f"{display}: missing model recovery section {start_marker!r}")
                continue
            section = " ".join(raw[start:end].split()).casefold()
            action_position = section.find(action.casefold())
            if action_position < 0:
                errors.append(f"{display}: missing model recovery action {action!r}")
                continue
            missing_or_late = [
                phrase for phrase in required
                if section.find(phrase.casefold()) < 0
                or section.find(phrase.casefold()) > action_position
            ]
            if missing_or_late:
                errors.append(
                    f"{display}: model recovery privacy decision must precede "
                    f"{action!r} in {start_marker!r}; missing or late "
                    + ", ".join(repr(phrase) for phrase in missing_or_late)
                )
    return errors


def check_windows_agent_install_privacy_order(
    path: Path = DOCS / "install" / "agents.md",
) -> list[str]:
    """Keep the Windows privacy decision ahead of assistant install actions."""
    display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
    if not path.exists():
        return [f"{display}: missing Windows assistant install guidance"]

    contents = read_text(path)
    section_start = contents.find("## Windows")
    if section_start < 0:
        return [f"{display}: missing Windows assistant install prompt"]
    section_end = contents.find("\n## ", section_start + len("## Windows"))
    section = contents[section_start:] if section_end < 0 else contents[section_start:section_end]
    warning = section.find("Before installing or launching published Windows 0.1.12")
    actions = (
        "Use only the published Windows prerelease",
        "Download the installer and its checksum",
        "Start-Process -FilePath $installer",
    )
    missing_actions = [action for action in actions if section.find(action) < 0]
    if warning < 0:
        return [f"{display}: missing Windows assistant model-download privacy warning"]
    if missing_actions:
        return [f"{display}: incomplete Windows assistant install prompt"]
    if any(warning > section.find(action) for action in actions):
        return [
            f"{display}: Windows model-download privacy warning must precede every assistant install action"
        ]
    return []


def check_agent_brief_preflight_order(
    root_brief: Path = ROOT / "llms.txt",
    full_brief: Path = DOCS / "llms-full.txt",
) -> list[str]:
    """Keep first-launch decisions ahead of agent-facing install routes."""
    checks = (
        (
            root_brief,
            (
                ("Before installing or launching macOS 0.3.8",
                 "brew install --cask"),
                ("If you are installing the unsigned Windows 0.1.12 prerelease",
                 "Current Windows language and hardware requirements"),
            ),
        ),
        (
            full_brief,
            (
                ("Before installing or launching macOS 0.3.8",
                 "brew install --cask"),
                ("Before installing or launching published Windows 0.1.12",
                 "The current Windows installer, requirements"),
            ),
        ),
    )
    errors: list[str] = []
    for path, pairs in checks:
        display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
        if not path.exists():
            errors.append(f"{display}: missing agent-facing install brief")
            continue
        contents = read_text(path)
        for warning, action in pairs:
            warning_at, action_at = contents.find(warning), contents.find(action)
            if warning_at < 0 or action_at < 0 or warning_at > action_at:
                errors.append(
                    f"{display}: {warning!r} must precede {action!r}"
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
        elif path.name in ("llms.txt", "llms-full.txt"):
            raw = read_text(path)
            install = (
                raw.partition("### macOS")[2].partition("### Windows")[0]
                if path.name == "llms-full.txt" else raw
            )
            warning = install.find("Before installing or launching macOS 0.3.8")
            actions = (
                ("brew install --cask", "Direct download:")
                if path.name == "llms-full.txt"
                else ("- macOS latest published download:", "- Homebrew install:")
            )
            if warning < 0 or any(
                install.find(action) < 0 or warning > install.find(action)
                for action in actions
            ):
                errors.append(
                    f"{display}: the macOS privacy decision must precede "
                    "download and install instructions"
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


def check_copy_notice_freshness_guidance(
    surfaces: dict[Path, tuple[str, str, tuple[str, ...]]] = COPY_NOTICE_FRESHNESS_GUIDANCE,
) -> list[str]:
    """Require point-in-time copy notices and conditional paste in recovery steps."""
    errors: list[str] = []
    for path, (start_marker, end_marker, required) in surfaces.items():
        display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
        if not path.exists():
            errors.append(f"{display}: missing copy-notice recovery guidance")
            continue
        contents = read_text(path)
        start = contents.find(start_marker)
        end = contents.find(end_marker, start + len(start_marker)) if start >= 0 else -1
        if start < 0 or end < 0:
            errors.append(f"{display}: missing copy-notice recovery section")
            continue
        section = " ".join(contents[start:end].split()).casefold()
        missing = [phrase for phrase in required if phrase.casefold() not in section]
        if missing:
            errors.append(
                f"{display}: copied-notice recovery must check clipboard freshness — "
                f"missing {', '.join(repr(phrase) for phrase in missing)}"
            )
    return errors


def check_windows_delivery_recovery_guidance(
    surfaces: dict[Path, tuple[str, ...]] = WINDOWS_DELIVERY_RECOVERY_GUIDANCE,
) -> list[str]:
    """Keep clipboard recovery instructions scoped to their Windows release."""
    errors: list[str] = []
    for path, required in surfaces.items():
        display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
        if not path.exists():
            errors.append(f"{display}: missing Windows delivery recovery guidance")
            continue
        contents = " ".join(read_text(path).split()).casefold()
        missing = [phrase for phrase in required if phrase.casefold() not in contents]
        if missing:
            errors.append(
                f"{display}: incomplete release-scoped Windows delivery recovery — "
                f"missing {', '.join(repr(phrase) for phrase in missing)}"
            )
        for claim in WINDOWS_DELIVERY_UNSCOPED_CLAIMS:
            if claim in contents:
                errors.append(
                    f"{display}: unscoped clipboard-copy advice can paste the previous item"
                )
                break
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
        if path.name == "compatibility_report.yml" and re.search(
            r"\b(?:six|seven) outcome counts\b", contents, flags=re.I
        ):
            errors.append(
                f"{display}: obsolete compatibility outcome count in issue form"
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
    noscript = re.search(r"<noscript\b[^>]*>(.*?)</noscript>", page, flags=re.I | re.S)
    if (
        noscript is None
        or "four steady-focus categories across five slots" not in noscript.group(1)
        or "four focus-change categories across three slots" not in noscript.group(1)
    ):
        errors.append(
            "docs/app-compatibility.html: no-JavaScript guidance must count "
            "four categories in each group, including unrun slots"
        )

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
                for outcome in ("pasted", "recovered", "unsafe", "notrun")
            ),
            *(
                (f"focus-{index}", outcome)
                for index in range(1, 4)
                for outcome in ("copied", "inserted", "failed", "notrun")
            ),
        ]
        if (
            len(inputs) != len(expected_options)
            or sorted(options) != sorted(expected_options)
        ):
            errors.append(
                "docs/app-compatibility.html: worksheet must expose four steady-focus "
                "and four focus-change categories for each attempt"
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
            status_link = re.search(
                r'href="https://github\.com/rcourtman/presspeech/issues"', actions
            )
            status_position = status_link.start() if status_link else -1
            if (
                browse_position < 0
                or status_position < 0
                or browse_position > status_position
            ):
                errors.append(
                    "docs/app-compatibility.html: worksheet handoff must check "
                    "matching reports before issue-creation status"
                )
            if (
                "Check issue-creation status" not in actions
                or "issues/new?" in actions
            ):
                errors.append(
                    "docs/app-compatibility.html: worksheet handoff must use the "
                    "issue list instead of a restricted new-report form"
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
    if (
        "reportActions.hidden = !result.reportable" not in script
        or "reportable: remaining === 0 && !sequenceViolation" not in script
    ):
        errors.append(
            "docs/compatibility-worksheet.js: report actions must remain hidden "
            "until all outcomes are complete and comparable"
        )
    if "save.disabled = !result.complete" not in script:
        errors.append(
            "docs/compatibility-worksheet.js: report download must remain disabled "
            "until all outcomes are complete"
        )
    if (
        'save.addEventListener("click", saveSummary)' not in script
        or "new Blob([`${formatReportDraft(summary.value, latestResult.reportable)}\\n`]" not in script
        or '"presspeech-compatibility-report-draft.txt"' not in script
        or '"presspeech-compatibility-noncomparable-draft.txt"' not in script
        or "URL.createObjectURL(file)" not in script
        or "function formatReportDraft(summaryText, reportable" not in script
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
                rf"\bupcoming(?:{separator}Windows)?{separator}v?{re.escape(version)}\b",
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


def check_cross_platform_compare_privacy(
    path: Path = COMPARE_DIR / "handy.html",
) -> list[str]:
    """Keep the Handy comparison scoped to the published Windows privacy behavior."""
    display = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
    if not path.exists():
        return [f"{display}: missing cross-platform Presspeech privacy comparison"]

    contents = read_text(path)
    marker = '<th scope="row">Privacy posture</th>'
    start = contents.find(marker)
    end = contents.find("</tr>", start + len(marker)) if start >= 0 else -1
    if start < 0 or end < 0:
        return [f"{display}: missing Presspeech privacy comparison row"]
    row = contents[start:end]
    cell_start = row.find("<td>")
    cell_end = row.find("</td>", cell_start + 4) if cell_start >= 0 else -1
    if cell_start < 0 or cell_end < 0:
        return [f"{display}: missing Presspeech privacy comparison cell"]
    cell = row[cell_start + 4:cell_end]

    required = (
        "Presspeech-authored analytics",
        "Windows 0.1.12",
        "default usage telemetry",
        "model downloads",
        "../privacy.html#network-calls",
    )
    missing = [phrase for phrase in required if phrase.casefold() not in cell.casefold()]
    if missing:
        return [
            f"{display}: cross-platform privacy comparison must qualify Windows model-download telemetry; missing "
            + ", ".join(repr(phrase) for phrase in missing)
        ]
    if re.search(
        r"\bno cloud transcription(?:\s+or(?:\s+[\w-]+){1,5})?,\s*account,\s*telemetry\b",
        cell,
        re.IGNORECASE,
    ):
        return [
            f"{display}: unqualified no-telemetry claim conflicts with published Windows dependencies"
        ]
    return []


def check_macos_agent_install_order(
    prompt: str = MAC_INSTALL_PROMPT, readme: str | None = None,
) -> list[str]:
    """Require a compatibility stop before either install path or launch."""
    if readme is None:
        readme = read_text(ROOT / "README.md")
    if "### Assistant Install Prompt" in readme:
        readme = readme.split("### Assistant Install Prompt", 1)[1].split(
            "</details>", 1
        )[0]
    sequences = (
        ("macOS assistant prompt", prompt, (
            "Before installing or launching macOS 0.3.8", "uname -m",
            "sw_vers -productVersion", "Stop if", "command -v brew",
            "brew install --cask", "current version-pinned download",
            "Only after the user makes an informed choice to launch 0.3.8",
            "open /Applications/Presspeech.app",
        )),
        ("README assistant prompt", readme, (
            "Before downloading or installing, run these read-only compatibility checks",
            "uname -m", "sw_vers -productVersion", "Stop if this is not",
            "command -v brew", "brew install --cask",
            "current version-pinned notarised ZIP",
            "Only after the user makes an informed choice to launch 0.3.8",
            "open /Applications/Presspeech.app",
        )),
    )
    errors = []
    for display, content, markers in sequences:
        positions = [content.find(marker) for marker in markers]
        if any(position < 0 for position in positions) or positions != sorted(positions):
            errors.append(f"{display}: compatibility and privacy decisions must precede install and launch")
    return errors


def check_install_prompt_sync(metadata: dict[str, object]) -> list[str]:
    errors: list[str] = []
    errors.extend(check_macos_agent_install_order())
    required_mac_launch_choice = (
        "installing the app without opening it does not make the model request",
        "If the user chooses to wait, skip the `open` command below",
        "Only after the user makes an informed choice to launch 0.3.8",
    )
    missing_mac_launch_choice = [
        phrase for phrase in required_mac_launch_choice
        if phrase not in MAC_INSTALL_PROMPT
    ]
    if missing_mac_launch_choice:
        errors.append(
            "macOS assistant prompt: keep package installation distinct from the launch-triggered model request; missing "
            + ", ".join(repr(phrase) for phrase in missing_mac_launch_choice)
        )

    required_windows_launch_choice = (
        "Downloading the installer and checksum from GitHub does not make a model request",
        'uncheck the installer\'s final "Launch Presspeech" option',
        'make sure the installer\'s final "Launch Presspeech" option was unchecked',
    )
    missing_windows_launch_choice = [
        phrase for phrase in required_windows_launch_choice
        if phrase not in WINDOWS_INSTALL_PROMPT
    ]
    if missing_windows_launch_choice:
        errors.append(
            "Windows assistant prompt: keep package installation distinct from the launch-triggered model request; missing "
            + ", ".join(repr(phrase) for phrase in missing_windows_launch_choice)
        )

    required_windows_setup_choice = (
        "Before choosing Finish Setup, Set Up Later, or closing Setup",
        "new 0.1.12 profile selects Start with Windows by default and ask whether to turn it off",
        "Published 0.1.12 offers Press to toggle in Settings, not Setup",
        "upcoming 0.1.13 offers it in Setup",
        "If the user chooses Set Up Later, leave setup incomplete",
    )
    missing_windows_setup_choice = [
        phrase for phrase in required_windows_setup_choice
        if phrase not in WINDOWS_INSTALL_PROMPT
    ]
    if missing_windows_setup_choice:
        errors.append(
            "Windows assistant prompt: keep setup choices tied to the published build; missing "
            + ", ".join(repr(phrase) for phrase in missing_windows_setup_choice)
        )

    required_windows_proxy_notice = (
        "TLS-inspecting HTTPS proxy trusted by the client can read any 0.1.12 token",
        "still honors proxy and CA settings",
        "cannot confirm that a TLS-inspection proxy is trusted",
    )
    missing_windows_proxy_notice = [
        phrase for phrase in required_windows_proxy_notice
        if phrase not in WINDOWS_INSTALL_PROMPT
    ]
    if missing_windows_proxy_notice:
        errors.append(
            "Windows assistant prompt: explain inherited proxy/TLS-inspection risk; missing "
            + ", ".join(repr(phrase) for phrase in missing_windows_proxy_notice)
        )

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
    if check_macos_agent_install_order():
        raise SyncError("self-test: safe macOS assistant install order was rejected")
    early_install = MAC_INSTALL_PROMPT.replace(
        "Before installing or launching macOS 0.3.8",
        "brew install --cask rcourtman/presspeech/presspeech\n"
        "Before installing or launching macOS 0.3.8", 1,
    )
    if not check_macos_agent_install_order(prompt=early_install):
        raise SyncError("self-test: install before compatibility stop was accepted")
    missing_check = MAC_INSTALL_PROMPT.replace("  sw_vers -productVersion", "")
    if not check_macos_agent_install_order(prompt=missing_check):
        raise SyncError("self-test: missing macOS compatibility check was accepted")
    metadata: dict[str, object] = {
        "last_updated": "2026-01-02",
        "version": "8.7.6",
        "windows_version": "9.8.7",
        "release_zip_sha256": "a" * 64,
        "release_zip_size": "7.6 MB",
    }
    with tempfile.TemporaryDirectory() as tmp:
        homepage = Path(tmp) / "homepage.html"
        safe_homepage = read_text(DOCS / "index.html")
        homepage.write_text(safe_homepage, encoding="utf-8")
        if check_homepage_launch_decision(homepage):
            raise SyncError("self-test: safe homepage launch decision was rejected")
        homepage.write_text(
            safe_homepage.replace("Unsure? Leave it unopened.", "", 1),
            encoding="utf-8",
        )
        if not check_homepage_launch_decision(homepage):
            raise SyncError("self-test: missing homepage stop action was accepted")
        homepage.write_text(
            safe_homepage.replace(
                '<div class="note warn launch-decision"',
                '<div class="actions"></div><div class="note warn launch-decision"',
                1,
            ),
            encoding="utf-8",
        )
        if not check_homepage_launch_decision(homepage):
            raise SyncError("self-test: homepage action before launch decision was accepted")
        homepage.write_text(
            safe_homepage.replace('href="windows.html#model-download-privacy"', 'href="windows.html"', 1),
            encoding="utf-8",
        )
        if not check_homepage_launch_decision(homepage):
            raise SyncError("self-test: missing Windows privacy link was accepted")

        preflight_metadata = {"version": "0.3.8", "windows_version": "0.1.12"}
        preflight_surfaces = []
        for surface in ANCHORED_INSTALL_PREFLIGHTS:
            fixture = dict(surface)
            fixture["path"] = Path(tmp) / f"{surface['platform']}-install.html"
            note = (
                f'<div class="note warn" data-install-preflight="{surface["platform"]}">'
                + " ".join(surface["required"])
                + '<a href="#model-download-privacy">full release-specific warning</a></div>'
            )
            if surface["platform"] == "macos":
                download = '<a href="https://github.com/rcourtman/presspeech/releases/download/v0.3.8/Presspeech.zip">Download</a>'
                action = (
                    "If you chose to wait, leave it unopened. "
                    "Otherwise, open it"
                )
                suffix = (
                    "<h2>Homebrew install and launch</h2>"
                    "<p><strong>Before the <code>open</code> command:</strong> "
                    "macOS 0.3.8; skip <code>open</code>; "
                    '<a href="#model-download-privacy">Read warning</a></p>'
                    "<pre><code>brew install --cask rcourtman/presspeech/presspeech"
                    "</code></pre>"
                )
            else:
                download = '<a href="https://github.com/rcourtman/presspeech/releases/download/windows-v0.1.12/Presspeech-Setup-0.1.12-x64.exe">Download</a>'
                action = ""
                suffix = (
                    "<li><strong>Decide whether to launch 0.1.12</strong><p>"
                    "If you chose to wait after reading the privacy decision, "
                    "leave the installer’s final <strong>Launch Presspeech</strong> option unchecked "
                    "and do not open the app. If you choose to use published 0.1.12 now, "
                    "open it from the Start Menu.</p></li>"
                )
            Path(fixture["path"]).write_text(
                f'<section id="{surface["anchor"]}">{note}{download}{action}</section>{suffix}',
                encoding="utf-8",
            )
            preflight_surfaces.append(fixture)
        preflight_surfaces = tuple(preflight_surfaces)
        if check_anchored_install_preflights(preflight_metadata, preflight_surfaces):
            raise SyncError("self-test: valid anchored install preflights were rejected")
        stale_preflight_metadata = dict(preflight_metadata, version="0.3.9")
        if not any(
            "review the macos anchored install preflight" in error
            for error in check_anchored_install_preflights(
                stale_preflight_metadata, preflight_surfaces
            )
        ):
            raise SyncError("self-test: stale release-specific preflight was accepted")
        mac_fixture = Path(preflight_surfaces[0]["path"])
        valid_mac_fixture = mac_fixture.read_text(encoding="utf-8")
        mac_note = re.search(
            r'<div class="note warn" data-install-preflight="macos">.*?</div>',
            valid_mac_fixture,
        )
        if mac_note is None:
            raise SyncError("self-test: missing macOS preflight fixture")
        mac_fixture.write_text(
            valid_mac_fixture.replace(mac_note.group(0), "").replace(
                "</section>", mac_note.group(0) + "</section>"
            ),
            encoding="utf-8",
        )
        if not any(
            "privacy reminder must precede the direct download link" in error
            for error in check_anchored_install_preflights(
                preflight_metadata, preflight_surfaces
            )
        ):
            raise SyncError("self-test: late anchored privacy reminder was accepted")
        mac_fixture.write_text(
            valid_mac_fixture.replace(mac_note.group(0), ""), encoding="utf-8"
        )
        if not any(
            "needs a local model-download privacy reminder" in error
            for error in check_anchored_install_preflights(
                preflight_metadata, preflight_surfaces
            )
        ):
            raise SyncError("self-test: missing anchored privacy reminder was accepted")
        mac_fixture.write_text(
            valid_mac_fixture.replace("skip <code>open</code>", "open immediately"),
            encoding="utf-8",
        )
        if not any(
            "Homebrew open command needs the release-specific" in error
            for error in check_anchored_install_preflights(
                preflight_metadata, preflight_surfaces
            )
        ):
            raise SyncError("self-test: unconditional Homebrew open was accepted")
        windows_fixture = Path(preflight_surfaces[1]["path"])
        valid_windows_fixture = windows_fixture.read_text(encoding="utf-8")
        windows_note = re.search(
            r'<div class="note warn" data-install-preflight="windows">.*?</div>',
            valid_windows_fixture,
        )
        if windows_note is None:
            raise SyncError("self-test: missing Windows preflight fixture")
        windows_fixture.write_text(
            valid_windows_fixture.replace(windows_note.group(0), "").replace(
                "</section>", windows_note.group(0) + "</section>"
            ),
            encoding="utf-8",
        )
        if not any(
            "privacy reminder must precede the direct download link" in error
            for error in check_anchored_install_preflights(
                preflight_metadata, preflight_surfaces
            )
        ):
            raise SyncError("self-test: late Windows privacy reminder was accepted")
        windows_fixture.write_text(
            valid_windows_fixture.replace(
                "If you chose to wait after reading",
                "Open it without a privacy decision after reading",
            ),
            encoding="utf-8",
        )
        if not any(
            "Windows launch step must keep the wait-without-launching option" in error
            for error in check_anchored_install_preflights(
                preflight_metadata, preflight_surfaces
            )
        ):
            raise SyncError("self-test: unconditional Windows launch was accepted")

        upgrade_page = Path(tmp) / "upgrade.html"
        safe_upgrade = read_text(DOCS / "install.html")
        upgrade_page.write_text(safe_upgrade, encoding="utf-8")
        if check_macos_upgrade_preflight(preflight_metadata, upgrade_page):
            raise SyncError("self-test: safe macOS upgrade launch decision was rejected")
        if not check_macos_upgrade_preflight(stale_preflight_metadata, upgrade_page):
            raise SyncError("self-test: stale macOS upgrade launch decision was accepted")
        upgrade_note = re.search(
            r'<div class="note warn" data-install-preflight="macos-upgrade">.*?</div>',
            safe_upgrade,
            flags=re.S,
        )
        if upgrade_note is None:
            raise SyncError("self-test: missing macOS upgrade preflight fixture")
        upgrade_page.write_text(safe_upgrade.replace(upgrade_note.group(0), "", 1), encoding="utf-8")
        if not check_macos_upgrade_preflight(preflight_metadata, upgrade_page):
            raise SyncError("self-test: missing macOS upgrade warning was accepted")
        upgrade_page.write_text(
            safe_upgrade.replace(
                upgrade_note.group(0), "", 1
            ).replace(
                '<div class="grid two">',
                '<div class="grid two">' + upgrade_note.group(0),
                1,
            ),
            encoding="utf-8",
        )
        if not check_macos_upgrade_preflight(preflight_metadata, upgrade_page):
            raise SyncError("self-test: late macOS upgrade warning was accepted")
        upgrade_page.write_text(
            safe_upgrade.replace(
                "If you decide to launch after reading the warning above, open it once.",
                "Open it once.",
                1,
            ),
            encoding="utf-8",
        )
        if not check_macos_upgrade_preflight(preflight_metadata, upgrade_page):
            raise SyncError("self-test: unconditional macOS upgrade launch was accepted")

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

        inventory_path = Path(tmp) / "network-calls.json"
        privacy_page_path = Path(tmp) / "privacy.html"
        fixture_calls = []
        for call_name, expectation in MODEL_DOWNLOAD_FIRST_RUN_CONTROL_EXPECTATIONS.items():
            current_version = str(metadata[str(expectation["version_key"])])
            major, minor, patch = (int(part) for part in current_version.split("."))
            next_version = f"{major}.{minor}.{patch + 1}"
            fixture_calls.append({
                "name": call_name,
                "first_run_controls_by_release": [
                    {
                        "version": current_version,
                        "status": "published",
                        "missing_model_controls": expectation["published"],
                    },
                    {
                        "version": next_version,
                        "status": "upcoming",
                        "missing_model_controls": expectation["upcoming"],
                    },
                ],
            })
        inventory_path.write_text(
            json.dumps({
                "schema_version": 1,
                "schema_description": MODEL_DOWNLOAD_SCHEMA_DESCRIPTION,
                "network_calls": fixture_calls,
            }),
            encoding="utf-8",
        )
        privacy_page_path.write_text(
            " ".join(MODEL_DOWNLOAD_FIRST_RUN_GUIDANCE), encoding="utf-8"
        )
        if check_model_download_first_run_controls(
            metadata, inventory_path, privacy_page_path
        ):
            raise SyncError("self-test: valid per-release model controls were rejected")

        incorrect_controls = json.loads(inventory_path.read_text(encoding="utf-8"))
        incorrect_controls["network_calls"][0]["first_run_controls_by_release"][1][
            "missing_model_controls"
        ][0]["can_defer"] = False
        inventory_path.write_text(json.dumps(incorrect_controls), encoding="utf-8")
        if not any(
            "do not match the documented release behavior" in error
            for error in check_model_download_first_run_controls(
                metadata, inventory_path, privacy_page_path
            )
        ):
            raise SyncError("self-test: contradictory defer control was accepted")

        incorrect_controls["network_calls"][0]["first_run_controls_by_release"][1][
            "missing_model_controls"
        ][0]["can_defer"] = True
        incorrect_controls["network_calls"][0]["first_run_controls_by_release"][0][
            "version"
        ] = "0.0.0"
        inventory_path.write_text(json.dumps(incorrect_controls), encoding="utf-8")
        if not any(
            "published controls must match" in error
            for error in check_model_download_first_run_controls(
                metadata, inventory_path, privacy_page_path
            )
        ):
            raise SyncError("self-test: stale published model-control version was accepted")

        inventory_path.write_text(
            json.dumps({
                "schema_version": 1,
                "schema_description": MODEL_DOWNLOAD_SCHEMA_DESCRIPTION,
                "network_calls": fixture_calls,
            }),
            encoding="utf-8",
        )
        privacy_page_path.write_text("Model downloads happen automatically.", encoding="utf-8")
        if not any(
            "missing release-specific model-download control guidance" in error
            for error in check_model_download_first_run_controls(
                metadata, inventory_path, privacy_page_path
            )
        ):
            raise SyncError("self-test: missing visible model-control guidance was accepted")

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
            "- macOS latest published download: https://github.com/rcourtman/presspeech/releases/latest/download/Presspeech.zip\n"
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
            or "Before installing or launching macOS 0.3.8" not in synced_llms
            or "macOS 0.3.8 may attach an inherited Hugging Face token" not in synced_llms
            or "wait until macOS 0.3.9 is published" not in synced_llms
            or "Leave a working model cache in place" not in synced_llms
            or "https_proxy" not in synced_llms
            or "TLS-inspecting" not in synced_llms
            or "macos-0-3-8-after-use" not in synced_llms
            or synced_llms.find("Before installing or launching macOS 0.3.8")
            > synced_llms.find("- macOS latest published download:")
            or synced_llms.find("Before installing or launching macOS 0.3.8")
            > synced_llms.find("- Homebrew install:")
        ):
            raise SyncError("self-test: inaccurate llms privacy claim was not corrected")
        short_answer = synced_llms.partition("Best short answer:\n")[2]
        if not all(
            phrase in short_answer
            for phrase in (
                "macOS 0.3.8 may include an inherited Hugging Face token",
                "wait for macOS 0.3.9",
                "Windows 0.1.12 may send Hugging Face usage telemetry",
                "wait for Windows 0.1.13",
                "Delivery Recovery Copy or Discard",
                "may leave the previous clipboard item unchanged",
                "Downloading the app alone does not start a model request",
                "leave the final Launch Presspeech option unchecked",
                "install.html#model-download-privacy",
                "windows.html#model-download-privacy",
            )
        ):
            raise SyncError("self-test: short agent answer omitted a launch or delivery decision")
        legacy_llms.write_text(synced_llms, encoding="utf-8")
        if sync_llms(legacy_llms, metadata) != synced_llms:
            raise SyncError("self-test: llms privacy correction is not idempotent")

        index_page = Path(tmp) / "index.html"
        index_page.write_text(
            '"@id": "https://rcourtman.github.io/presspeech/#webpage"\n'
            '"dateModified": "2025-12-29"\n'
            '"@id": "https://rcourtman.github.io/presspeech/#software"\n'
            '"softwareVersion": "1.2.3"\n'
            '"downloadUrl": "https://github.com/rcourtman/presspeech/releases/latest"\n'
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
            '"releaseNotes": "https://github.com/rcourtman/presspeech/releases/tag/windows-v9.8.7"',
            "<strong>7.6 MB</strong>",
            "<strong>macOS 8.7.6:</strong>",
            "<strong>Windows 9.8.7:</strong>",
            '"dateModified": "2026-01-02"',
            'datetime="2026-01-02">2 January 2026</time>',
        ):
            if expected not in synced_index:
                raise SyncError(f"self-test: homepage metadata did not sync {expected!r}")
        if '"downloadUrl"' in synced_index:
            raise SyncError("self-test: release sync retained an unguarded structured download")

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
            or "skip the `open` command below" not in MAC_INSTALL_PROMPT
            or 'uncheck the installer\'s final "Launch Presspeech" option' not in WINDOWS_INSTALL_PROMPT
            or 'leave the app unopened' not in WINDOWS_INSTALL_PROMPT
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

        badge_required = README_BADGE_PREFLIGHT
        badge_entrypoint = Path(tmp) / "badge-entrypoint.md"
        badge_entrypoint.write_text(" ".join(badge_required), encoding="utf-8")
        if check_repository_install_guidance({badge_entrypoint: badge_required}, {}):
            raise SyncError("self-test: warning-first README badges were rejected")
        for index in range(len(badge_required)):
            unsafe_badges = list(badge_required)
            unsafe_badges[index] = unsafe_badges[index].replace(
                'href="https://rcourtman.github.io/presspeech/install.html#model-download-privacy"'
                if index == 0 else 'href="https://rcourtman.github.io/presspeech/install.html"',
                'href="https://github.com/rcourtman/presspeech/releases/latest"',
            )
            badge_entrypoint.write_text(" ".join(unsafe_badges), encoding="utf-8")
            if not check_repository_install_guidance({badge_entrypoint: badge_required}, {}):
                raise SyncError("self-test: README badge bypassed the install warning")

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
            "## Install\n\n### macOS\n\n"
            "brew install --cask rcourtman/presspeech/presspeech\n\n"
            "Direct download:\nhttps://github.com/rcourtman/presspeech/releases/latest/download/Presspeech.zip\n\n"
            "### Windows\n\nWindows guide.\n\n"
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
                or "Before installing or launching macOS 0.3.8" not in synced_llms_full
                or "The macOS 0.3.8 release starts its first speech-model download" not in synced_llms_full
                or "In 0.3.9, a clean install must choose Download Model" not in synced_llms_full
                or synced_llms_full.find("Before installing or launching macOS 0.3.8")
                > synced_llms_full.find("brew install --cask")
                or synced_llms_full.find("Before installing or launching macOS 0.3.8")
                > synced_llms_full.find("Direct download:")):
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

        compare_privacy = Path(tmp) / "handy.html"
        compare_privacy.write_text(
            '<tr><th scope="row">Privacy posture</th>'
            '<td>No cloud transcription, Presspeech-authored analytics, account, or crash reporter. '
            'Windows 0.1.12 leaves default usage telemetry enabled during model downloads. '
            '<a href="../privacy.html#network-calls">Network-call inventory</a></td>'
            '<td>Competitor content</td></tr>',
            encoding="utf-8",
        )
        if check_cross_platform_compare_privacy(compare_privacy):
            raise SyncError("self-test: correctly scoped comparison privacy claim was rejected")
        compare_privacy.write_text(
            '<tr><th scope="row">Privacy posture</th>'
            '<td>No cloud transcription, account, telemetry, or crash reporter. '
            'No Presspeech-authored analytics. '
            'Windows 0.1.12 leaves default usage telemetry enabled during model downloads. '
            '<a href="../privacy.html#network-calls">Network-call inventory</a></td>'
            '<td>Competitor content</td></tr>',
            encoding="utf-8",
        )
        if not any(
            "unqualified no-telemetry claim" in error
            for error in check_cross_platform_compare_privacy(compare_privacy)
        ):
            raise SyncError("self-test: unqualified cross-platform telemetry claim was accepted")

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

        proxy_guidance = Path(tmp) / "windows-proxy-disclosure.md"
        required_proxy_guidance = {
            proxy_guidance: (
                "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                "SSL_CERT_FILE", "SSL_CERT_DIR", "trust_env=True",
                "TLS-inspecting HTTPS proxy", "published Windows 0.1.12",
                "upcoming Windows 0.1.13",
            )
        }
        proxy_guidance.write_text(
            "Windows downloads use HTTPS.", encoding="utf-8"
        )
        if not check_windows_model_download_privacy_guidance(required_proxy_guidance):
            raise SyncError("self-test: missing HTTPX proxy/CA disclosure was accepted")
        proxy_guidance.write_text(
            "The published Windows 0.1.12 client uses trust_env=True and honors "
            "HTTP_PROXY, HTTPS_PROXY, ALL_PROXY, NO_PROXY, SSL_CERT_FILE, and "
            "SSL_CERT_DIR. A TLS-inspecting HTTPS proxy whose CA is trusted can "
            "see its token. Upcoming Windows 0.1.13 removes that token but still "
            "honors proxy and CA settings.",
            encoding="utf-8",
        )
        if check_windows_model_download_privacy_guidance(required_proxy_guidance):
            raise SyncError("self-test: complete HTTPX proxy/CA disclosure was rejected")

        summary_guidance = Path(tmp) / "windows-privacy-summary.html"
        required_summary_guidance = {
            summary_guidance: (
                "Before installing or launching Windows 0.1.12",
                "usage telemetry",
                "already-configured or locally saved Hugging Face token",
                "Custom download routing can change where the model request",
                "avoid this possible usage telemetry",
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
            "can change where the model request goes. If you prefer to avoid this possible usage telemetry, "
            "or if a Hugging Face token or custom download route is configured on this PC, wait until Windows 0.1.13 is published. "
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
        summary_guidance.write_text(
            "Before installing or launching Windows 0.1.12, usage telemetry and an already-configured "
            "or locally saved Hugging Face token may be sent; custom download routing can change where "
            "the model request goes. If a token is configured, wait until Windows 0.1.13 is published. "
            "The public models need no account token. Windows privacy decision and technical details. "
            "Already used Windows 0.1.12? If a model download ran with a token available and an inherited "
            "HF_ENDPOINT or staging setting may have sent it to a destination you do not trust, treat the "
            "token as disclosed to that destination. Revoke the token. Hugging Face Access Tokens. "
            "Do not include token values in logs or support requests.\n",
            encoding="utf-8",
        )
        if not check_windows_model_download_privacy_summary(required_summary_guidance):
            raise SyncError("self-test: telemetry-only privacy concern was not covered")

        readme_install = Path(tmp) / "README.md"
        readme_install.write_text(
            "## Install on Windows\n"
            "**Before installing or launching Windows 0.1.12:** privacy decision.\n"
            "The installer is currently unsigned; stop if managed policy blocks it.\n"
            "Download the self-contained installer.\n"
            "- After verification, run the installer. If you choose to wait for 0.1.13, "
            "clear **Launch Presspeech** and leave the app unopened. "
            "If you choose to launch 0.1.12 after reviewing the privacy decision above, "
            "a missing selected-model download begins without another prompt.\n"
            "- If a shell-capable assistant is doing the installation, use the guarded prompt.\n"
            "\n## Install on macOS\n",
            encoding="utf-8",
        )
        if check_readme_windows_install_decision_order(readme_install):
            raise SyncError("self-test: ordered README Windows warnings were rejected")
        readme_install.write_text(
            "## Install on Windows\n"
            "**Before installing or launching Windows 0.1.12:** privacy decision.\n"
            "The installer is currently unsigned; stop if managed policy blocks it.\n"
            "Download the self-contained installer.\n"
            "- After verification, run the installer. If you choose to launch 0.1.12 "
            "after reviewing the privacy decision above, start it now. "
            "If you choose to wait for 0.1.13, clear **Launch Presspeech** and "
            "leave the app unopened; a missing selected-model download begins "
            "without another prompt.\n"
            "- If a shell-capable assistant is doing the installation, use the guarded prompt.\n"
            "\n## Install on macOS\n",
            encoding="utf-8",
        )
        if not check_readme_windows_install_decision_order(readme_install):
            raise SyncError("self-test: README launch-before-wait instruction was accepted")
        readme_install.write_text(
            "## Install on Windows\n"
            "Download the self-contained installer.\n"
            "- After verification, run the installer. If you choose to wait for 0.1.13, "
            "clear **Launch Presspeech** and leave the app unopened. "
            "If you choose to launch 0.1.12 after reviewing the privacy decision above, "
            "a missing selected-model download begins without another prompt.\n"
            "- If a shell-capable assistant is doing the installation, use the guarded prompt.\n"
            "**Before installing or launching Windows 0.1.12:** privacy decision.\n"
            "The installer is currently unsigned; stop if managed policy blocks it.\n"
            "\n## Install on macOS\n",
            encoding="utf-8",
        )
        if not check_readme_windows_install_decision_order(readme_install):
            raise SyncError(
                "self-test: README Windows install instructions before the warnings were accepted"
            )

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
            '<a href="install.html#model-download-privacy">Privacy decision</a>'
            '<a href="install.html">macOS guide</a>'
            '<a href="windows.html#download-verify-run">Windows guide</a>'
            '</article>',
            encoding="utf-8",
        )
        if check_faq_install_privacy_order(faq_install):
            raise SyncError("self-test: ordered FAQ privacy warnings were rejected")
        faq_install.write_text(
            '<article><h3>How do I install it?</h3>'
            '<a href="install.html#model-download-privacy">Privacy decision</a>'
            '<a href="install.html">macOS guide</a>'
            '<a href="windows.html#download-verify-run">Windows guide</a>'
            '<p id="faq-macos-install-privacy">macOS warning</p>'
            '<p id="faq-windows-install-privacy">Windows warning</p>'
            '</article>',
            encoding="utf-8",
        )
        faq_order_errors = check_faq_install_privacy_order(faq_install)
        if len(faq_order_errors) != 2:
            raise SyncError("self-test: install action before FAQ privacy warning was accepted")
        faq_install.write_text(
            '<article><h3>How do I install it?</h3>'
            '<p id="faq-macos-install-privacy">macOS warning</p>'
            '<p id="faq-windows-install-privacy">Windows warning</p>'
            '<a href="install.html#model-download-privacy">Privacy decision</a>'
            '<a href="install.html">macOS guide</a>'
            '<a href="windows.html#download-verify-run">Windows guide</a>'
            '<a href="https://github.com/rcourtman/presspeech/releases/latest/'
            'download/Presspeech.zip">Download</a>'
            '</article>',
            encoding="utf-8",
        )
        if not check_faq_install_privacy_order(faq_install):
            raise SyncError("self-test: FAQ direct download shortcut was accepted")

        getting_started = Path(tmp) / "getting-started.html"
        safe_getting_started = (
            '<div id="model-download-preflight" role="region" aria-labelledby="launch-decision-heading">'
            '<h2 id="launch-decision-heading">Decide before opening a current build</h2>'
            '<p><strong>Downloading is not launching:</strong> macOS 0.3.8 and Windows 0.1.12 '
            'start a missing-model request when Presspeech opens, not when you download the ZIP or installer. '
            'You can download a build and leave it unopened. '
            '<strong>Unsure about a token, telemetry, or proxy? Keep it closed.</strong> '
            '<a href="#macos-launch-decision">macOS decision</a> '
            '<a href="#windows-launch-decision">Windows decision</a></p>'
            '<ul><li id="macos-launch-decision"><h3>macOS 0.3.8 — wait if a token may be inherited</h3> '
            '<strong>Wait for published 0.3.9</strong> if a Hugging Face token may be inherited by Presspeech. '
            'Do not launch 0.3.8 while using a TLS-inspecting proxy whose trust is unclear. '
            'A model request can include the inherited token; the bundled client honors '
            'lowercase <code>https_proxy</code>, and a TLS-inspecting proxy trusted by macOS can read that token. '
            '<a href="install.html#model-download-privacy">full macOS warning</a></li>'
            '<li id="windows-launch-decision"><h3>Windows 0.1.12 — wait if privacy risks are unclear</h3> '
            '<strong>Wait for published 0.1.13</strong> if you want to avoid possible Hugging Face usage telemetry, '
            'an available token or custom download route may be configured. A custom route can change where '
            'the request and token go; a TLS-inspecting proxy trusted by the client can read the token. '
            "If such a proxy's trust is unclear, do not launch while it is in use. "
            'If you install but wait, leave <strong>Launch Presspeech</strong> unchecked. '
            'The upcoming build disables bundled-library telemetry and account-token authentication '
            'but still honors proxy and CA settings. '
            '<a href="windows.html#model-download-privacy">full Windows warning</a></li></ul>'
            '<p>The public models need no account token, and model requests do not include dictation audio '
            'or transcripts. Do not inspect or share token values. The later builds are '
            '<strong>not published yet</strong>; check '
            '<a href="https://github.com/rcourtman/presspeech/releases">GitHub Releases</a>. '
            'See the <a href="privacy.html#network-calls">version-specific network inventory</a>.</p></div>'
            '<div class="actions"><a href="install.html">Install</a>'
            '<a href="#quick-path">Already installed?</a></div>'
            '<section id="quick-path"><a href="#model-download-preflight">Check warning</a></section>'
            '<a href="#finish-setup">Continue to setup</a>'
        )
        getting_started.write_text(safe_getting_started, encoding="utf-8")
        if check_getting_started_preflight_order(getting_started):
            raise SyncError("self-test: safe getting-started preflight was rejected")
        getting_started.write_text(
            safe_getting_started.replace("Do not inspect or share token values.", ""),
            encoding="utf-8",
        )
        if not check_getting_started_preflight_order(getting_started):
            raise SyncError("self-test: missing token-handling guidance was accepted")
        getting_started.write_text(
            safe_getting_started.replace('href="#windows-launch-decision"', 'href="#quick-path"'),
            encoding="utf-8",
        )
        if not check_getting_started_preflight_order(getting_started):
            raise SyncError("self-test: onboarding platform shortcut bypassed its warning")
        for warning in (
            "Wait for published 0.3.9",
            "Wait for published 0.1.13",
            "If such a proxy's trust is unclear, do not launch while it is in use",
        ):
            getting_started.write_text(
                safe_getting_started.replace(warning, ""),
                encoding="utf-8",
            )
            if not check_getting_started_preflight_order(getting_started):
                raise SyncError(f"self-test: missing first-launch warning was accepted: {warning}")
        getting_started.write_text(
            safe_getting_started.replace(
                '<div id="model-download-preflight" role="region" aria-labelledby="launch-decision-heading">',
                '<a href="#finish-setup">Skip to setup</a><div id="model-download-preflight" role="region" aria-labelledby="launch-decision-heading">',
            ),
            encoding="utf-8",
        )
        if not check_getting_started_preflight_order(getting_started):
            raise SyncError("self-test: setup shortcut before first-launch decision was accepted")
        getting_started.write_text(
            safe_getting_started.replace('id="model-download-preflight"', 'id="removed"'),
            encoding="utf-8",
        )
        if not check_getting_started_preflight_order(getting_started):
            raise SyncError("self-test: missing getting-started preflight was accepted")

        entry_docs = Path(tmp) / "entry-docs"
        entry_docs.mkdir()
        entry_page = entry_docs / "install.html"
        entry_page.write_text(
            '<a href="getting-started.html">First dictation</a>'
            '<a href="getting-started.html#model-download-preflight">Launch decision</a>',
            encoding="utf-8",
        )
        if check_getting_started_entry_links(entry_docs):
            raise SyncError("self-test: safe onboarding entry links were rejected")
        entry_page.write_text(
            '<a href="getting-started.html#private-test">Try Dictation</a>',
            encoding="utf-8",
        )
        if not check_getting_started_entry_links(entry_docs):
            raise SyncError("self-test: first-launch decision bypass was accepted")

        scratchpad_guidance = Path(tmp) / "scratchpad.html"
        safe_scratchpad = (
            '<section id="private-test"><p>Practice</p>'
            '<div id="scratchpad-clipboard-boundary">'
            'not a guarantee that the system clipboard is untouched; '
            'macOS 0.3.8; Windows 0.1.12; macOS Universal Clipboard; '
            'Windows Clipboard History; use non-sensitive test words. '
            '<a href="privacy.html#operating-system-clipboard-services">Privacy</a>'
            '</div><ol class="steps"><li>Open Try Dictation</li></ol></section>'
        )
        scratchpad_guidance.write_text(safe_scratchpad, encoding="utf-8")
        if check_getting_started_scratchpad_privacy_order(scratchpad_guidance):
            raise SyncError("self-test: ordered scratchpad warning was rejected")
        scratchpad_guidance.write_text(
            safe_scratchpad.replace(
                '<p>Practice</p>', '<ol class="steps"><li>Open Try Dictation</li></ol><p>Practice</p>'
            ),
            encoding="utf-8",
        )
        if not check_getting_started_scratchpad_privacy_order(scratchpad_guidance):
            raise SyncError("self-test: scratchpad warning after practice was accepted")
        scratchpad_guidance.write_text(
            safe_scratchpad.replace("Windows Clipboard History", "clipboard history"),
            encoding="utf-8",
        )
        if not check_getting_started_scratchpad_privacy_order(scratchpad_guidance):
            raise SyncError("self-test: incomplete scratchpad warning was accepted")

        scratchpad_claim = Path(tmp) / "scratchpad-claim.txt"
        scratchpad_claim.write_text(
            "Use an in-app scratchpad with harmless words; it can use the clipboard.",
            encoding="utf-8",
        )
        if check_scratchpad_privacy_claims([scratchpad_claim]):
            raise SyncError("self-test: accurate scratchpad copy was rejected")
        for overclaim in (
            "private scratchpad", "private test", "first private dictation",
            "private first dictation", "test privately",
            "private, click-driven test", "transcript stays in that private window",
        ):
            scratchpad_claim.write_text(overclaim, encoding="utf-8")
            if not check_scratchpad_privacy_claims([scratchpad_claim]):
                raise SyncError(f"self-test: {overclaim!r} scratchpad claim was accepted")

        recovery_html = Path(tmp) / "troubleshooting.html"
        recovery_markdown = Path(tmp) / "troubleshooting.md"
        safe_recovery_html = read_text(DOCS / "troubleshooting.html")
        safe_recovery_markdown = read_text(DOCS / "troubleshooting.md")
        recovery_html.write_text(safe_recovery_html, encoding="utf-8")
        recovery_markdown.write_text(safe_recovery_markdown, encoding="utf-8")
        if check_model_recovery_privacy_order(recovery_html, recovery_markdown):
            raise SyncError("self-test: safe model-recovery decisions were rejected")
        for path, safe, phrase in (
            (recovery_html, safe_recovery_html, "Check before model recovery"),
            (recovery_html, safe_recovery_html, "If you choose to wait with a missing or damaged model"),
            (recovery_html, safe_recovery_html, "another download may include a Hugging Face token"),
            (recovery_html, safe_recovery_html, "custom routing can change its destination"),
            (recovery_markdown, safe_recovery_markdown, "Check before model recovery"),
            (recovery_markdown, safe_recovery_markdown, "If you choose to wait with a missing or damaged model"),
            (recovery_markdown, safe_recovery_markdown, "Another download may include a Hugging Face token"),
            (recovery_markdown, safe_recovery_markdown, "custom routing can change its destination"),
        ):
            path.write_text(safe.replace(phrase, "", 1), encoding="utf-8")
            if not check_model_recovery_privacy_order(recovery_html, recovery_markdown):
                raise SyncError(f"self-test: missing model-recovery warning was accepted: {phrase}")
            path.write_text(safe, encoding="utf-8")
        recovery_html.write_text(
            safe_recovery_html.replace(
                "do not start another download; when you can choose the timing,",
                "when you can choose the timing,",
                1,
            ).replace(
                "check the connection before retrying.",
                "check the connection before retrying. do not start another download;",
                1,
            ),
            encoding="utf-8",
        )
        if not check_model_recovery_privacy_order(recovery_html, recovery_markdown):
            raise SyncError("self-test: model-recovery action before its warning was accepted")
        unconditional_mac_retry = safe_recovery_html.replace(
            "If you choose to make another model request after reading the warning above, "
            "check the connection before retrying.",
            "Check the connection before retrying. If you choose to make another model "
            "request after reading the warning above, continue.",
            1,
        )
        if unconditional_mac_retry == safe_recovery_html:
            raise SyncError("self-test: missing Mac retry mutation target")
        recovery_html.write_text(unconditional_mac_retry, encoding="utf-8")
        if not check_model_recovery_privacy_order(recovery_html, recovery_markdown):
            raise SyncError("self-test: unconditional Mac model retry was accepted")
        unconditional_windows_retry = safe_recovery_html.replace(
            "If you choose to make another model request after reading the warning above, "
            "use <strong>Retry Speech Model</strong>",
            "Use <strong>Retry Speech Model</strong> first. If you choose to make another "
            "model request after reading the warning above, continue",
            1,
        )
        if unconditional_windows_retry == safe_recovery_html:
            raise SyncError("self-test: missing Windows retry mutation target")
        recovery_html.write_text(unconditional_windows_retry, encoding="utf-8")
        if not check_model_recovery_privacy_order(recovery_html, recovery_markdown):
            raise SyncError("self-test: unconditional Windows model retry was accepted")

        windows_agent_prompt = Path(tmp) / "agents.md"
        agent_warning = (
            "Before installing or launching published Windows 0.1.12, disclose "
            "the model-download privacy behavior."
        )
        agent_actions = (
            "Install Presspeech from https://github.com/rcourtman/presspeech",
            "Use only the published Windows prerelease",
            "Download the installer and its checksum",
            "Start-Process -FilePath $installer",
        )
        windows_agent_prompt.write_text(
            "## Windows\n```text\n" + agent_actions[0] + "\n" + agent_warning + "\n"
            + "\n".join(agent_actions[1:]) + "\n```\n",
            encoding="utf-8",
        )
        if check_windows_agent_install_privacy_order(windows_agent_prompt):
            raise SyncError("self-test: ordered Windows assistant privacy warning was rejected")
        windows_agent_prompt.write_text(
            "## Windows\n```text\n" + agent_actions[0] + "\n" + agent_actions[1] + "\n"
            + agent_warning + "\n" + "\n".join(agent_actions[2:]) + "\n```\n",
            encoding="utf-8",
        )
        agent_order_errors = check_windows_agent_install_privacy_order(windows_agent_prompt)
        if not any("must precede every assistant install action" in error
                   for error in agent_order_errors):
            raise SyncError("self-test: Windows assistant install before privacy warning was accepted")

        short_brief = Path(tmp) / "short-brief.txt"
        full_brief = Path(tmp) / "full-brief.txt"
        short_contents = read_text(ROOT / "llms.txt")
        full_contents = read_text(DOCS / "llms-full.txt")
        short_brief.write_text(short_contents, encoding="utf-8")
        full_brief.write_text(full_contents, encoding="utf-8")
        if check_agent_brief_preflight_order(short_brief, full_brief):
            raise SyncError("self-test: ordered agent briefs were rejected")
        short_brief.write_text(
            short_contents.replace(
                "Before installing or launching macOS 0.3.8",
                "After installation, macOS 0.3.8",
                1,
            ), encoding="utf-8",
        )
        if not check_agent_brief_preflight_order(short_brief, full_brief):
            raise SyncError("self-test: missing short-brief preflight was accepted")
        short_brief.write_text(short_contents, encoding="utf-8")
        full_brief.write_text(
            full_contents.replace(
                "Before installing or launching published Windows 0.1.12",
                "After installing published Windows 0.1.12",
                1,
            ), encoding="utf-8",
        )
        if not check_agent_brief_preflight_order(short_brief, full_brief):
            raise SyncError("self-test: missing full-brief Windows preflight was accepted")

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

        copy_notice = Path(tmp) / "copy-notice.html"
        required_copy_notice = {
            copy_notice: (
                '<section id="recovery">', '</section>',
                ("copied at completion", "later copy can replace", "only if the clipboard still holds"),
            ),
        }
        copy_notice.write_text(
            '<section id="recovery">Copied — press Control-V to paste.</section>'
            '<section>It was copied at completion; a later copy can replace it. '
            'Paste only if the clipboard still holds the transcript.</section>',
            encoding="utf-8",
        )
        if not check_copy_notice_freshness_guidance(required_copy_notice):
            raise SyncError("self-test: guidance outside the recovery section was accepted")
        copy_notice.write_text(
            '<section id="recovery">It was copied at completion; a later copy can replace it. '
            'Paste only if the clipboard still holds the transcript.</section>',
            encoding="utf-8",
        )
        if check_copy_notice_freshness_guidance(required_copy_notice):
            raise SyncError("self-test: conditional copy recovery was rejected")

        recovery_page = Path(tmp) / "recovery.html"
        scoped_recovery = {
            recovery_page: (
                "Published Windows 0.1.12",
                "Upcoming Windows 0.1.13",
                "Delivery Recovery",
            ),
        }
        recovery_page.write_text(
            "Published Windows 0.1.12 copies for manual paste. "
            "Upcoming Windows 0.1.13 uses Delivery Recovery Copy or Discard.\n",
            encoding="utf-8",
        )
        if check_windows_delivery_recovery_guidance(scoped_recovery):
            raise SyncError("self-test: release-scoped Windows recovery was rejected")
        recovery_page.write_text(
            "If it cannot verify that destination, it keeps the result on the clipboard. "
            + recovery_page.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        if not any(
            "unscoped clipboard-copy advice" in error
            for error in check_windows_delivery_recovery_guidance(scoped_recovery)
        ):
            raise SyncError("self-test: unsafe Windows clipboard advice was accepted")

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

        compatibility_form = Path(tmp) / "compatibility_report.yml"
        required_form_count = {compatibility_form: ("eight outcome counts below",)}
        compatibility_form.write_text(
            "The worksheet prepares the eight outcome counts below.\n",
            encoding="utf-8",
        )
        if check_compatibility_evidence_guidance(required_form_count):
            raise SyncError("self-test: current compatibility outcome count was rejected")
        compatibility_form.write_text(
            "The worksheet prepares the eight outcome counts below. "
            "An old note says seven outcome counts.\n",
            encoding="utf-8",
        )
        if not any(
            "obsolete compatibility outcome count" in error
            for error in check_compatibility_evidence_guidance(required_form_count)
        ):
            raise SyncError("self-test: stale compatibility outcome count was accepted")

        worksheet_page = Path(tmp) / "app-compatibility.html"
        worksheet_script = Path(tmp) / "compatibility-worksheet.js"
        worksheet_inputs = "".join(
            f'<input type="radio" name="steady-{index}" value="{outcome}">'
            for index in range(1, 6)
            for outcome in ("pasted", "recovered", "unsafe", "notrun")
        ) + "".join(
            f'<input type="radio" name="focus-{index}" value="{outcome}">'
            for index in range(1, 4)
            for outcome in ("copied", "inserted", "failed", "notrun")
        )
        worksheet_page.write_text(
            '<script src="compatibility-worksheet.js" defer></script>'
            '<noscript>four steady-focus categories across five slots; '
            'four focus-change categories across three slots</noscript>'
            '<form id="compatibility-worksheet">'
            + worksheet_inputs
            + '<textarea id="worksheet-summary" readonly></textarea>'
            '<button type="button">Copy</button>'
            '<button id="save-worksheet-summary" type="button" disabled>'
            'Download report draft</button><button type="reset">Reset</button>'
            '<div id="worksheet-report-actions" hidden>'
            '<a href="https://github.com/example/issues?q=matching">Check matching reports</a>'
            '<a href="https://github.com/rcourtman/presspeech/issues">Check issue-creation status</a>'
            '</div>'
            '</form>',
            encoding="utf-8",
        )
        worksheet_valid_script = (
            'document.getElementById("compatibility-worksheet");\n'
            'reportActions.hidden = !result.reportable;\n'
            'reportable: remaining === 0 && !sequenceViolation;\n'
            'save.disabled = !result.complete;\n'
            'save.addEventListener("click", saveSummary);\n'
            'function formatReportDraft(summaryText, reportable = true) { return ['
            '"Platform (macOS or Windows):", "Target app and public version:", '
            '"Generic field type:", summaryText].join("\\n"); }\n'
            'const file = new Blob([`${formatReportDraft(summary.value, latestResult.reportable)}\\n`]);\n'
            'link.download = "presspeech-compatibility-report-draft.txt";\n'
            'link.download = "presspeech-compatibility-noncomparable-draft.txt";\n'
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
            valid_worksheet_page.replace(
                "four steady-focus categories across five slots",
                "three steady-focus categories across five slots",
                1,
            ),
            encoding="utf-8",
        )
        if not any(
            "no-JavaScript guidance" in error
            for error in check_compatibility_worksheet_contract(
                worksheet_page, worksheet_script
            )
        ):
            raise SyncError("self-test: stale no-JavaScript category count was accepted")
        worksheet_page.write_text(valid_worksheet_page, encoding="utf-8")
        worksheet_page.write_text(
            valid_worksheet_page.replace(
                '<a href="https://github.com/rcourtman/presspeech/issues">Check issue-creation status</a>',
                '<a href="https://github.com/rcourtman/presspeech/issues/new?template=compatibility_report.yml">Check new-report availability</a>',
                1,
            ),
            encoding="utf-8",
        )
        if not any(
            "issue list instead of a restricted new-report form" in error
            for error in check_compatibility_worksheet_contract(
                worksheet_page, worksheet_script
            )
        ):
            raise SyncError("self-test: blocked form handoff was accepted")
        worksheet_page.write_text(valid_worksheet_page, encoding="utf-8")
        worksheet_page.write_text(
            valid_worksheet_page.replace(
                '<input type="radio" name="focus-1" value="failed">',
                '<input type="radio" name="focus-1" value="other">',
                1,
            ),
            encoding="utf-8",
        )
        if not any(
            "four focus-change categories" in error
            for error in check_compatibility_worksheet_contract(
                worksheet_page, worksheet_script
            )
        ):
            raise SyncError("self-test: ambiguous focus-change category was accepted")
        worksheet_page.write_text(valid_worksheet_page, encoding="utf-8")
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
                "reportActions.hidden = !result.reportable;\n", "", 1
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
        proxy_guidance = Path(tmp) / "mac-proxy-guidance.md"
        proxy_required = {
            proxy_guidance: ("https_proxy", "TLS-inspecting", "0.3.8 token", "0.3.9"),
        }
        proxy_guidance.write_text("The model uses a proxy.\n", encoding="utf-8")
        if not check_mac_model_download_guidance(proxy_required):
            raise SyncError("self-test: missing macOS proxy disclosure was accepted")
        proxy_guidance.write_text(
            "https_proxy: a TLS-inspecting proxy could read a 0.3.8 token; 0.3.9 removes it.\n",
            encoding="utf-8",
        )
        if check_mac_model_download_guidance(proxy_required):
            raise SyncError("self-test: complete macOS proxy disclosure was rejected")
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

        action_copy = Path(tmp) / "first-run-copy.txt"
        required_action_copy = {action_copy: ("Published 0.1.12 starts without asking",)}
        forbidden_action_copy = {
            action_copy: (
                re.compile(r"brew install --cask.*\nopen /Applications/Presspeech\.app"),
                FIRST_RUN_ACTION_FORBIDDEN[DOCS / "windows.html"][1],
            )
        }
        action_copy.write_text("Published 0.1.12 starts without asking\n", encoding="utf-8")
        if check_first_run_action_copy(required_action_copy, forbidden_action_copy):
            raise SyncError("self-test: versioned first-run action was rejected")
        if check_onboarding_release_scope({"version": "0.3.8", "windows_version": "0.1.12"}):
            raise SyncError("self-test: reviewed onboarding release scope was rejected")
        if len(check_onboarding_release_scope({"version": "0.3.9", "windows_version": "0.1.13"})) != 2:
            raise SyncError("self-test: onboarding steps were not gated on a new release")
        action_copy.write_text("Upcoming 0.1.13 asks before downloading\n", encoding="utf-8")
        if not check_first_run_action_copy(required_action_copy, forbidden_action_copy):
            raise SyncError("self-test: missing published first-run action was accepted")
        action_copy.write_text(
            "Published 0.1.12 starts without asking\n"
            "brew install --cask rcourtman/presspeech/presspeech\n"
            "open /Applications/Presspeech.app\n",
            encoding="utf-8",
        )
        if not check_first_run_action_copy(required_action_copy, forbidden_action_copy):
            raise SyncError("self-test: combined install-and-launch command was accepted")
        action_copy.write_text(
            "Published 0.1.12 starts without asking. The first-run window shows "
            "a <strong>Press to toggle</strong> style.\n", encoding="utf-8"
        )
        if not check_first_run_action_copy(required_action_copy, forbidden_action_copy):
            raise SyncError("self-test: unreleased Setup trigger was attributed to 0.1.12")

        phase_copy.write_text(
            "Upcoming Windows **9.8.7** adds this behavior.\n",
            encoding="utf-8",
        )
        phase_errors = check_windows_release_phase_copy(metadata, [phase_copy])
        if len(phase_errors) != 1 or "called upcoming" not in phase_errors[0]:
            raise SyncError("self-test: phase-bound current Windows copy was not rejected")
        for upcoming in (
            "Upcoming 9.8.7 adds this behavior.\n",
            "upcoming <strong>9.8.7</strong> adds this behavior.\n",
            "Upcoming Windows v9.8.7 adds this behavior.\n",
        ):
            phase_copy.write_text(upcoming, encoding="utf-8")
            if not check_windows_release_phase_copy(metadata, [phase_copy]):
                raise SyncError("self-test: bare or marked-up upcoming Windows version was accepted")
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
            errors.extend(check_first_run_action_copy())
            errors.extend(check_onboarding_release_scope(metadata))
            errors.extend(check_windows_release_phase_copy(metadata))
            errors.extend(check_windows_release_references(metadata))
            errors.extend(check_icon_stats(metadata))
            errors.extend(check_platform_orientation())
            errors.extend(check_windows_unsigned_guidance())
            errors.extend(check_windows_verified_download_flow())
            errors.extend(check_anchored_install_preflights(metadata))
            errors.extend(check_macos_upgrade_preflight(metadata))
            errors.extend(check_windows_language_guidance())
            errors.extend(check_clipboard_service_guidance())
            errors.extend(check_windows_model_download_privacy_guidance())
            errors.extend(check_windows_model_download_privacy_scopes())
            errors.extend(check_model_download_first_run_controls(metadata))
            errors.extend(check_user_triggered_support_guide())
            errors.extend(check_windows_model_download_privacy_guidance(WINDOWS_AGENT_DISCLOSURE))
            errors.extend(check_windows_model_download_privacy_guidance(WINDOWS_MODEL_DOWNLOAD_PROXY_GUIDANCE))
            errors.extend(check_windows_model_download_privacy_guidance(WINDOWS_MODEL_DOWNLOAD_INTEGRITY_GUIDANCE))
            errors.extend(check_macos_model_download_privacy_summary())
            errors.extend(check_mac_model_download_guidance(MAC_MODEL_DOWNLOAD_PROXY_GUIDANCE))
            errors.extend(check_windows_model_download_privacy_summary())
            errors.extend(check_readme_windows_install_decision_order())
            errors.extend(check_faq_install_privacy_order())
            errors.extend(check_homepage_launch_decision())
            errors.extend(check_getting_started_preflight_order())
            errors.extend(check_getting_started_entry_links())
            errors.extend(check_model_recovery_privacy_order())
            errors.extend(check_getting_started_scratchpad_privacy_order())
            errors.extend(check_scratchpad_privacy_claims())
            errors.extend(check_windows_agent_install_privacy_order())
            errors.extend(check_agent_brief_preflight_order())
            errors.extend(check_delivery_boundary_guidance())
            errors.extend(check_copy_notice_freshness_guidance())
            errors.extend(check_windows_delivery_recovery_guidance())
            errors.extend(check_compatibility_evidence_guidance())
            errors.extend(check_compatibility_worksheet_contract())
            errors.extend(check_command_shell_guidance())
            errors.extend(check_repository_install_guidance())
            errors.extend(check_compare_freshness())
            errors.extend(check_cross_platform_compare_privacy())
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
        errors.extend(check_first_run_action_copy())
        errors.extend(check_onboarding_release_scope(metadata))
        errors.extend(check_windows_release_phase_copy(metadata))
        errors.extend(check_windows_release_references(metadata))
        errors.extend(check_icon_stats(metadata))
        errors.extend(check_platform_orientation())
        errors.extend(check_windows_unsigned_guidance())
        errors.extend(check_windows_verified_download_flow())
        errors.extend(check_anchored_install_preflights(metadata))
        errors.extend(check_macos_upgrade_preflight(metadata))
        errors.extend(check_windows_language_guidance())
        errors.extend(check_clipboard_service_guidance())
        errors.extend(check_windows_model_download_privacy_guidance())
        errors.extend(check_windows_model_download_privacy_scopes())
        errors.extend(check_model_download_first_run_controls(metadata))
        errors.extend(check_user_triggered_support_guide())
        errors.extend(check_windows_model_download_privacy_guidance(WINDOWS_AGENT_DISCLOSURE))
        errors.extend(check_windows_model_download_privacy_guidance(WINDOWS_MODEL_DOWNLOAD_PROXY_GUIDANCE))
        errors.extend(check_windows_model_download_privacy_guidance(WINDOWS_MODEL_DOWNLOAD_INTEGRITY_GUIDANCE))
        errors.extend(check_macos_model_download_privacy_summary())
        errors.extend(check_mac_model_download_guidance(MAC_MODEL_DOWNLOAD_PROXY_GUIDANCE))
        errors.extend(check_windows_model_download_privacy_summary())
        errors.extend(check_readme_windows_install_decision_order())
        errors.extend(check_faq_install_privacy_order())
        errors.extend(check_homepage_launch_decision())
        errors.extend(check_getting_started_preflight_order())
        errors.extend(check_getting_started_entry_links())
        errors.extend(check_model_recovery_privacy_order())
        errors.extend(check_getting_started_scratchpad_privacy_order())
        errors.extend(check_scratchpad_privacy_claims())
        errors.extend(check_windows_agent_install_privacy_order())
        errors.extend(check_agent_brief_preflight_order())
        errors.extend(check_delivery_boundary_guidance())
        errors.extend(check_copy_notice_freshness_guidance())
        errors.extend(check_windows_delivery_recovery_guidance())
        errors.extend(check_compatibility_evidence_guidance())
        errors.extend(check_compatibility_worksheet_contract())
        errors.extend(check_command_shell_guidance())
        errors.extend(check_repository_install_guidance())
        errors.extend(check_compare_freshness())
        errors.extend(check_cross_platform_compare_privacy())
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
