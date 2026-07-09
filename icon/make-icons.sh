#!/usr/bin/env bash
# Regenerate the .icns and menu bar PNGs from the canonical SVG sources.
# Run this whenever icon/presspeech.svg or icon/presspeech-menubar.svg changes.
set -euo pipefail

cd "$(dirname "$0")"

command -v rsvg-convert >/dev/null || { echo "rsvg-convert missing — brew install librsvg"; exit 1; }
command -v iconutil     >/dev/null || { echo "iconutil missing (should ship with Xcode CLT)"; exit 1; }

# --- App icon (.icns) -------------------------------------------------------
ICONSET="presspeech.iconset"
rm -rf "$ICONSET"
mkdir  "$ICONSET"

declare -a SIZES=(16 32 128 256 512)
for s in "${SIZES[@]}"; do
    rsvg-convert -w  "$s"        -h  "$s"        presspeech.svg > "$ICONSET/icon_${s}x${s}.png"
    rsvg-convert -w  "$((s*2))"  -h  "$((s*2))"  presspeech.svg > "$ICONSET/icon_${s}x${s}@2x.png"
done
# 1024x1024 is only needed at @1x (icon_512x512@2x covers retina at the largest size)

iconutil --convert icns "$ICONSET" --output Presspeech.icns
echo "  built Presspeech.icns"

# --- Menu bar template ------------------------------------------------------
# @2x must be quoted so the shell doesn't try to glob the [email pattern.
rsvg-convert -w 22 -h 22 presspeech-menubar.svg --output 'presspeech-menubar.png'
rsvg-convert -w 44 -h 44 presspeech-menubar.svg --output 'presspeech-menubar@2x.png'
echo "  built presspeech-menubar.png + presspeech-menubar@2x.png"

# Keep the Swift package's out-of-target resource copies byte-for-byte
# aligned with the canonical icon sources. They stay outside the target
# so SwiftPM does not create a resource bundle that breaks codesigning.
cp presspeech-menubar.png ../swift/Resources/presspeech-menubar.png
cp presspeech-menubar@2x.png ../swift/Resources/presspeech-menubar@2x.png
echo "  synced menu-bar PNGs into swift/Resources"

# --- GitHub Social Preview --------------------------------------------------
# 1280x640 PNG used as the repo's social-share card. Upload via
# Settings → General → Social preview on github.com.
rsvg-convert -w 1280 -h 640 social-preview.svg --output social-preview.png
echo "  built social-preview.png"

# Clean up the iconset; we only need the .icns
rm -rf "$ICONSET"
echo "Done."
