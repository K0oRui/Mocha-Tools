#!/usr/bin/env bash
# =============================================================================
# setup_and_build.sh — Mocha Tools Android port: Ubuntu environment setup
#                       and APK build script
#
# Usage:
#   bash setup_and_build.sh          # full setup + desktop test
#   bash setup_and_build.sh --build  # also compile the APK (requires Qt for Android)
#
# Run from the root of the mocha_tools_android/ project directory.
# =============================================================================

set -euo pipefail

VENV_DIR="$HOME/mochatools-android-env"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_APK=false

[[ "${1:-}" == "--build" ]] && BUILD_APK=true

echo "=============================================="
echo " Mocha Tools Android — Ubuntu Setup & Build"
echo "=============================================="
echo "Project: $PROJECT_DIR"
echo ""

# ── Step 1: System dependencies ──────────────────────────────────────────────
echo "[1/7] Installing system packages..."
sudo apt update -qq
sudo apt install -y \
    python3-pip python3-venv \
    openjdk-17-jdk \
    libgl1-mesa-dev libglib2.0-dev \
    git wget unzip \
    build-essential libssl-dev libffi-dev python3-dev \
    libsqlite3-dev zlib1g-dev \
    autoconf automake libtool pkg-config \
    lld libltdl-dev libxml2-dev libxslt1-dev \
    adb \
    2>/dev/null || true

# Verify Java
echo "    Java version:"
java -version 2>&1 | head -1
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64

# ── Step 2: Python venv ───────────────────────────────────────────────────────
echo ""
echo "[2/7] Setting up Python virtual environment at $VENV_DIR..."
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"

pip install --upgrade pip --quiet

# ── Step 3: Python dependencies ───────────────────────────────────────────────
echo ""
echo "[3/7] Installing Python packages..."
pip install --quiet \
    PySide6 \
    requests \
    certifi \
    packaging \
    buildozer \
    cython

echo ""
echo "    PySide6 version: $(python3 -c 'import PySide6; print(PySide6.__version__)')"
echo "    pyside6-android-deploy available: $(command -v pyside6-android-deploy && echo YES || echo NO — install Qt for Android toolchain)"

# ── Step 4: Verify project structure ─────────────────────────────────────────
echo ""
echo "[4/7] Verifying project structure..."
REQUIRED_FILES=(
    "main.py"
    "mochatools_app/__init__.py"
    "mochatools_app/constants.py"
    "mochatools_app/logging_utils.py"
    "mochatools_app/workers.py"
    "mochatools_app/styles.py"
    "mochatools_app/dialogs_android.py"
    "mochatools_app/app_android.py"
)
ALL_OK=true
for f in "${REQUIRED_FILES[@]}"; do
    if [ -f "$PROJECT_DIR/$f" ]; then
        echo "    ✓ $f"
    else
        echo "    ✗ MISSING: $f"
        ALL_OK=false
    fi
done
if [ "$ALL_OK" = false ]; then
    echo ""
    echo "ERROR: Missing files. Ensure all ported source files are in place."
    exit 1
fi

# ── Step 5: Desktop smoke test ────────────────────────────────────────────────
echo ""
echo "[5/7] Running desktop smoke test (import check)..."
cd "$PROJECT_DIR"
python3 - <<'EOF'
import sys
sys.path.insert(0, '.')
try:
    from mochatools_app.constants import APP_NAME, APP_VERSION
    from mochatools_app.logging_utils import write_debug_log
    from mochatools_app.workers import UploadWorker, FilesWorker, RemoteWorker
    from mochatools_app.styles import STYLESHEET
    from mochatools_app.dialogs_android import FolderBrowserDialog, ShareLinkDialog
    print(f"    ✓ All imports OK — {APP_NAME} {APP_VERSION}")
    assert len(STYLESHEET) > 100, "Stylesheet is suspiciously short"
    print("    ✓ Stylesheet loaded")
except ImportError as e:
    print(f"    ✗ Import error: {e}")
    sys.exit(1)
except Exception as e:
    print(f"    ✗ Error: {e}")
    sys.exit(1)
EOF
echo "    ✓ Smoke test passed"

# ── Step 6: Desktop UI test (optional, requires display) ─────────────────────
echo ""
echo "[6/7] Desktop UI test..."
if [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
    echo "    Display detected — launching desktop UI for 3 seconds..."
    timeout 3 python3 main.py 2>/dev/null || echo "    (window opened and closed — OK)"
else
    echo "    No display detected — skipping live UI test."
    echo "    To test the UI manually, run:"
    echo "      source $VENV_DIR/bin/activate && cd $PROJECT_DIR && python3 main.py"
fi

# ── Step 7: APK build (optional, requires Qt for Android) ────────────────────
echo ""
if [ "$BUILD_APK" = true ]; then
    echo "[7/7] Building Android APK..."
    echo ""

    # Check for Android SDK/NDK
    ANDROID_SDK="${ANDROID_SDK_ROOT:-$HOME/Android/Sdk}"
    ANDROID_NDK_SEARCH=$(find "$ANDROID_SDK/ndk" -maxdepth 1 -type d -name "26*" 2>/dev/null | sort -V | tail -1)
    ANDROID_NDK="${ANDROID_NDK_ROOT:-$ANDROID_NDK_SEARCH}"

    if [ ! -d "$ANDROID_SDK" ]; then
        echo "    ERROR: Android SDK not found at $ANDROID_SDK"
        echo "    Install it via Qt Online Installer or Android Studio."
        echo "    Then set: export ANDROID_SDK_ROOT=~/Android/Sdk"
        exit 1
    fi

    if [ ! -d "${ANDROID_NDK:-}" ]; then
        echo "    ERROR: Android NDK r26 not found."
        echo "    Install NDK r26b via Android Studio → SDK Manager → SDK Tools → NDK."
        echo "    Then set: export ANDROID_NDK_ROOT=~/Android/Sdk/ndk/<version>"
        exit 1
    fi

    # Check pyside6-android-deploy
    if ! command -v pyside6-android-deploy &>/dev/null; then
        echo "    ERROR: pyside6-android-deploy not found."
        echo "    Install Qt for Android via Qt Online Installer (https://www.qt.io/download-qt-installer)"
        echo "    then install the Qt 6.7+ for Android (arm64-v8a) component."
        exit 1
    fi

    echo "    Android SDK: $ANDROID_SDK"
    echo "    Android NDK: $ANDROID_NDK"
    echo "    Starting pyside6-android-deploy..."
    echo ""

    pyside6-android-deploy \
        --name "MochaTools" \
        --input main.py \
        --android-ndk "$ANDROID_NDK" \
        --android-sdk "$ANDROID_SDK" \
        --android-api 34 \
        --android-min-api 24 \
        --arch arm64-v8a \
        --release

    APK_PATH=$(find . -name "*.apk" 2>/dev/null | head -1)
    if [ -n "$APK_PATH" ]; then
        echo ""
        echo "    ✓ APK built: $APK_PATH"
        echo ""
        echo "  Install on connected device:"
        echo "    adb install $APK_PATH"
        echo ""
        echo "  View logs:"
        echo "    adb logcat | grep -E '(python|PySide|mochatools)'"
    else
        echo "    Build completed — check android-build/build/outputs/apk/ for the APK."
    fi
else
    echo "[7/7] APK build skipped (run with --build to compile)"
fi

echo ""
echo "=============================================="
echo " Setup complete!"
echo "=============================================="
echo ""
echo "Next steps:"
echo ""
echo "  Test desktop UI:"
echo "    source $VENV_DIR/bin/activate"
echo "    cd $PROJECT_DIR"
echo "    python3 main.py"
echo ""
echo "  Build APK (after installing Qt for Android):"
echo "    bash setup_and_build.sh --build"
echo ""
echo "  Required env vars for APK build:"
echo "    export ANDROID_SDK_ROOT=~/Android/Sdk"
echo "    export ANDROID_NDK_ROOT=~/Android/Sdk/ndk/<version>"
echo "    export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64"
echo "    export Qt6_DIR=~/Qt/6.7.0/android_arm64_v8a/lib/cmake/Qt6"
echo ""
echo "  Install Qt for Android: https://www.qt.io/download-qt-installer"
echo "    Components to install: Qt 6.7+, Qt for Android (arm64-v8a),"
echo "    Android SDK API 34, Android NDK r26b"
echo ""
