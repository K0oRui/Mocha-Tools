#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Mocha Tools — Linux Installer
#
# Usage:
#   chmod +x installer.sh
#   ./installer.sh            # install
#   ./installer.sh --uninstall
#
# What it does:
#   • Escalates to root via sudo if not already running as root
#   • Copies the binary to /usr/local/bin/mochatools
#   • Installs a .desktop file for the app launcher
#   • Installs an icon to the hicolor icon theme
#   • Writes an uninstall script to the install prefix
#
# Supports: Ubuntu, Debian, Fedora, Arch, and any distro with bash + a
# freedesktop-compliant desktop environment.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

APP_NAME="Mocha Tools"
APP_BINARY="mochatools"
APP_VERSION="v1.0.0"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Detect the bundled binary name (raw or renamed)
if   [[ -f "$SCRIPT_DIR/Mocha-Tools-linux" ]]; then
    SRC_BINARY="$SCRIPT_DIR/Mocha-Tools-linux"
elif [[ -f "$SCRIPT_DIR/mochatools" ]]; then
    SRC_BINARY="$SCRIPT_DIR/mochatools"
elif [[ -f "$SCRIPT_DIR/Mocha Tools" ]]; then
    SRC_BINARY="$SCRIPT_DIR/Mocha Tools"
else
    echo "ERROR: Could not find the Mocha Tools binary."
    echo "       Place Mocha-Tools-linux in the same directory as this script."
    exit 1
fi

# Icon — look for a bundled png
ICON_SRC=""
for candidate in \
    "$SCRIPT_DIR/builditems/debian_ubuntu/icon.png" \
    "$SCRIPT_DIR/icon.png"; do
    if [[ -f "$candidate" ]]; then
        ICON_SRC="$candidate"
        break
    fi
done

# ── Mode detection ────────────────────────────────────────────────────────────
UNINSTALL=false
for arg in "$@"; do
    [[ "$arg" == "--uninstall" ]] && UNINSTALL=true
done

# ── Privilege / prefix selection ─────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    echo "Root privileges required. Re-running with sudo..."
    exec sudo bash "$0" "$@"
fi

PREFIX="/usr/local"
DESKTOP_DIR="/usr/share/applications"
ICON_DIR="/usr/share/icons/hicolor/256x256/apps"

BIN_DIR="$PREFIX/bin"
UNINSTALL_SCRIPT="$PREFIX/lib/mochatools/uninstall.sh"

# ── Uninstall ─────────────────────────────────────────────────────────────────
if $UNINSTALL; then
    echo "Uninstalling $APP_NAME…"
    rm -f  "$BIN_DIR/$APP_BINARY"
    rm -f  "$DESKTOP_DIR/mochatools.desktop"
    rm -f  "$ICON_DIR/mochatools.png"
    rm -f  "$UNINSTALL_SCRIPT"
    rmdir --ignore-fail-on-non-empty "$PREFIX/lib/mochatools" 2>/dev/null || true
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
    gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true
    echo "✓ $APP_NAME uninstalled."
    exit 0
fi

# ── Install ───────────────────────────────────────────────────────────────────
echo "Installing $APP_NAME $APP_VERSION…"

# Binary
mkdir -p "$BIN_DIR"
cp "$SRC_BINARY" "$BIN_DIR/$APP_BINARY"
chmod 755 "$BIN_DIR/$APP_BINARY"
echo "  ✓ Binary  → $BIN_DIR/$APP_BINARY"

# Icon
if [[ -n "$ICON_SRC" ]]; then
    mkdir -p "$ICON_DIR"
    cp "$ICON_SRC" "$ICON_DIR/mochatools.png"
    echo "  ✓ Icon    → $ICON_DIR/mochatools.png"
    ICON_NAME="mochatools"
else
    ICON_NAME=""
    echo "  ⚠ No icon found — launcher will use a generic icon."
fi

# .desktop file
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_DIR/mochatools.desktop" << DESKTOP
[Desktop Entry]
Version=1.0
Type=Application
Name=$APP_NAME
GenericName=File Uploader
Comment=Upload files to Mocha
Exec=$BIN_DIR/$APP_BINARY
Icon=$ICON_NAME
Terminal=false
Categories=Network;FileTransfer;Utility;
Keywords=mocha;upload;share;file;
StartupNotify=true
DESKTOP
chmod 644 "$DESKTOP_DIR/mochatools.desktop"
echo "  ✓ Launcher → $DESKTOP_DIR/mochatools.desktop"

# Uninstall helper
mkdir -p "$(dirname "$UNINSTALL_SCRIPT")"
cat > "$UNINSTALL_SCRIPT" << UNSINSTALL
#!/usr/bin/env bash
# Auto-generated uninstall script for $APP_NAME
exec "$(readlink -f "${BASH_SOURCE[0]%/*}/../../..")/$(basename "$0")" --uninstall
UNSINSTALL
# Write a self-contained uninstall script that doesn't rely on the installer
cat > "$UNINSTALL_SCRIPT" << UNSCRIPT
#!/usr/bin/env bash
set -euo pipefail
echo "Uninstalling $APP_NAME…"
rm -f  "$BIN_DIR/$APP_BINARY"
rm -f  "$DESKTOP_DIR/mochatools.desktop"
rm -f  "$ICON_DIR/mochatools.png"
rm -f  "$UNINSTALL_SCRIPT"
rmdir --ignore-fail-on-non-empty "$(dirname "$UNINSTALL_SCRIPT")" 2>/dev/null || true
update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
gtk-update-icon-cache -f -t "$(dirname "$(dirname "$(dirname "$ICON_DIR")")")" 2>/dev/null || true
echo "✓ $APP_NAME uninstalled."
UNSCRIPT
chmod 755 "$UNINSTALL_SCRIPT"
echo "  ✓ Uninstall script → $UNINSTALL_SCRIPT"

# Refresh desktop DB / icon cache
update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true

echo ""
echo "✓ $APP_NAME $APP_VERSION installed successfully."
echo "  Run: $APP_BINARY"
echo "  Or find it in your application launcher."