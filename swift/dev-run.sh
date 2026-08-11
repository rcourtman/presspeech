#!/usr/bin/env bash
# dev-run.sh — local iteration loop for the Swift Presspeech port.
#
# Rebuilds main.swift, drops the binary into a minimal .app wrapper
# at /tmp/Presspeech-dev.app, signs it with the Developer ID + hardened
# runtime + the production entitlements (so TCC carries over from
# the Cask-installed Presspeech, no manual permission re-grants), kills
# any prior dev instance, and relaunches via `open`.
#
# Usage: ./dev-run.sh
#
# When you're done testing and want the production Cask Presspeech
# back, just `open /Applications/Presspeech.app` — both binaries share
# bundle id `com.local.presspeech`, so the TCC entries are
# interchangeable.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
APP="/tmp/Presspeech-dev.app"

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

say "Building (debug)..."
( cd "$HERE" && swift build 2>&1 | grep -vE "^/.*warning:|^[0-9]+ \|" | tail -3 )

say "Wrapping in $APP..."
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$HERE/.build/debug/Presspeech" "$APP/Contents/MacOS/Presspeech"
# Single canonical Info.plist (swift/Info.plist) — same file
# ship-swift.sh uses, so dev and release builds advertise the same
# bundle id / minimum macOS / usage descriptions / icon. Don't
# overwrite this with an inline heredoc; the canonical Info.plist is
# the source of truth, full stop.
cp "$HERE/Info.plist" "$APP/Contents/Info.plist"
# Menubar PNGs into the canonical Contents/Resources/ slot. NSImage
# (named:) on Bundle.main finds them under this exact path. We avoid
# SwiftPM's auto-generated <Package>_<Target>.bundle because it lacks
# an Info.plist, which makes codesign --deep error out.
cp "$HERE/Resources/presspeech-menubar.png"    "$APP/Contents/Resources/"
cp "$HERE/Resources/presspeech-menubar@2x.png" "$APP/Contents/Resources/"
# .icns for the About dialog + dock (when "Show in Dock" is on).
if [[ -f "$REPO/icon/Presspeech.icns" ]]; then
    cp "$REPO/icon/Presspeech.icns" "$APP/Contents/Resources/Presspeech.icns"
fi

say "Signing with Developer ID + hardened runtime..."
CERT_HASH="$(security find-identity -v -p codesigning 2>/dev/null \
    | awk '/Developer ID Application:/ { print $2; exit }')"
[[ -n "$CERT_HASH" ]] || { echo "no Developer ID cert in keychain" >&2; exit 1; }
# Capture codesign output so a failure under `set -e` still explains
# itself instead of dying silently.
if ! SIGN_OUTPUT="$(codesign --force --deep --sign "$CERT_HASH" \
    --options runtime \
    --entitlements "$REPO/entitlements.plist" \
    --timestamp \
    "$APP" 2>&1)"; then
    printf '%s\n' "$SIGN_OUTPUT" >&2
    echo "codesign failed" >&2
    exit 1
fi

say "Checking signed entitlements..."
EMBEDDED_ENTITLEMENTS="$(codesign -d --entitlements - "$APP" 2>&1)"
for key in \
    "com.apple.security.device.audio-input" \
    "com.apple.security.device.microphone"
do
    if ! grep -q "$key" <<<"$EMBEDDED_ENTITLEMENTS"; then
        printf '%s\n' "$EMBEDDED_ENTITLEMENTS" >&2
        echo "missing required entitlement: $key" >&2
        exit 1
    fi
done

say "Stopping any prior dev instance..."
pkill -f "Presspeech-dev.app" 2>/dev/null || true
# Also kill any Cask instance — same bundle id would clash on TCC + hotkey.
pkill -f "/Applications/Presspeech.app" 2>/dev/null || true

# pkill is a SIGTERM the app never gets to handle, so the active-run marker it
# sets at launch is left standing and the NEXT launch shows the "Reopened After
# an Unexpected Exit" alert — whose default button is Copy Diagnostics. That
# notice is for real crashes; a deliberate stop by this script is not one, so
# clear the marker rather than crying wolf on every dev iteration.
sleep 0.5
defaults write com.local.presspeech active_run_marker -bool false 2>/dev/null || true
sleep 0.5

say "Launching..."
open "$APP"
# `open` returns before the process exists; poll instead of racing a
# fixed sleep. The script promises a relaunch, so a process that never
# appears is a hard failure.
LAUNCHED_PID=""
for _ in $(seq 1 25); do
    LAUNCHED_PID="$(pgrep -f "Presspeech-dev.app" | head -n 1 || true)"
    [[ -n "$LAUNCHED_PID" ]] && break
    sleep 0.2
done
if [[ -z "$LAUNCHED_PID" ]]; then
    echo "Presspeech-dev did not appear within ~5s of 'open'." >&2
    echo "Check ~/Library/Logs/Presspeech.log for a startup crash." >&2
    exit 1
fi
echo "  pid=$LAUNCHED_PID $APP/Contents/MacOS/Presspeech"
echo
echo "  log: tail -f ~/Library/Logs/Presspeech.log"
echo "  stop: pkill -f Presspeech-dev.app"
