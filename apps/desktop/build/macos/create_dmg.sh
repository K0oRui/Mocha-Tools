#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# create_dmg.sh — Build a drag-to-install macOS DMG for Mocha Tools
#
# Usage (called by build.yml after the universal .app is assembled):
#   bash builditems/macos/create_dmg.sh <path/to/Mocha Tools.app> <version>
#
# Produces:
#   MochaTools-<version>-macOS.dmg   in the current working directory
#
# Requires: hdiutil (built-in macOS), osascript (built-in macOS)
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

APP_BUNDLE="${1:?Usage: $0 <path/to/Mocha Tools.app> <version>}"
VERSION="${2:?Usage: $0 <path/to/Mocha Tools.app> <version>}"
APP_NAME="Mocha Tools"
DMG_NAME="MochaTools-${VERSION}-macOS.dmg"
VOL_NAME="${APP_NAME} ${VERSION}"
STAGING="dmg_staging"
TMP_DMG="mocha_tmp.dmg"

echo "→ Building DMG for ${APP_NAME} ${VERSION}"
echo "  App bundle : ${APP_BUNDLE}"
echo "  Output     : ${DMG_NAME}"

# Show available disk space before we start
echo "  Disk space before DMG build:"
df -h /

# ── 1. Prepare staging directory ─────────────────────────────────────────────
rm -rf "$STAGING"
mkdir -p "$STAGING"

# Move instead of copy — avoids doubling the .app footprint on a tight runner
mv "$APP_BUNDLE" "$STAGING/${APP_NAME}.app"

# Symlink to /Applications so users can drag-and-drop
ln -s /Applications "$STAGING/Applications"

# ── 2. Create a read/write DMG from the staging folder ───────────────────────
# Note: -scratchdir is not supported by hdiutil create, only hdiutil convert
hdiutil create \
    -srcfolder  "$STAGING" \
    -volname    "$VOL_NAME" \
    -fs         HFS+ \
    -fsargs     "-c c=16,a=16,b=16" \
    -format     UDRW \
    -size       400m \
    "$TMP_DMG"

# ── 3. Mount the RW DMG ───────────────────────────────────────────────────────
MOUNT_DIR="/Volumes/${VOL_NAME}"

# Unmount if already mounted from a previous failed run
if [[ -d "$MOUNT_DIR" ]]; then
    hdiutil detach "$MOUNT_DIR" -force 2>/dev/null || true
fi

hdiutil attach "$TMP_DMG" -readwrite -noverify -noautoopen

# Give the Finder a moment to register the volume
sleep 2

# ── 4. Customise the DMG window with AppleScript ─────────────────────────────
osascript << APPLESCRIPT
tell application "Finder"
    tell disk "${VOL_NAME}"
        open
        set current view of container window to icon view
        set toolbar visible of container window to false
        set statusbar visible of container window to false
        set the bounds of container window to {400, 100, 900, 420}
        set theViewOptions to icon view options of container window
        set arrangement of theViewOptions to not arranged
        set icon size of theViewOptions to 96
        set position of item "${APP_NAME}.app" of container window to {130, 150}
        set position of item "Applications" of container window to {370, 150}
        close
        open
        update without registering applications
        delay 2
    end tell
end tell
APPLESCRIPT

# ── 5. Unmount, convert to compressed read-only DMG ──────────────────────────
sync
hdiutil detach "$MOUNT_DIR" -force

# -scratchdir IS supported by hdiutil convert, redirecting temp files to /tmp
hdiutil convert "$TMP_DMG" \
    -format     UDZO \
    -imagekey   zlib-level=9 \
    -o "$DMG_NAME"

# ── 6. Clean up ───────────────────────────────────────────────────────────────
rm -rf "$STAGING" "$TMP_DMG"

echo ""
echo "✓ DMG created: ${DMG_NAME}  ($(du -sh "$DMG_NAME" | cut -f1))"