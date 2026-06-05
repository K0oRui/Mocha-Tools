#!/usr/bin/env bash
# =============================================================================
# setup_and_build.sh - Mocha Tools Android port: Ubuntu venv setup and APK build
#
# Usage:
#   bash setup_and_build.sh                 # system deps + venv + desktop smoke test
#   bash setup_and_build.sh --android-tools # also install Android SDK API 34 / NDK r26b
#   bash setup_and_build.sh --wheels        # download commercial PySide6 Android wheels with qtpip
#   bash setup_and_build.sh --oss-wheels    # build LGPL/GPL Android wheels from source
#   bash setup_and_build.sh --build         # update spec and build APK
#   bash setup_and_build.sh --all           # android tools + OSS wheels + build
#
# Run from this pyside6attempt project directory on Ubuntu.
# =============================================================================

set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"

# For OSS wheel builds, PySide6 cross-compile strictly requires Python 3.11.
# Auto-upgrade PYTHON_BIN to python3.11 if the caller didn't override it and
# we are about to do an OSS wheel / build run.  We need to peek at the flags
# here, before the getopt loop, so the venv is created with the right binary.
_needs_311=false
for _a in "$@"; do
    case "$_a" in --oss-wheels|--build|--all) _needs_311=true ;; esac
done
if [ "$_needs_311" = true ] && [ "${PYTHON_BIN}" = "python3" ]; then
    if command -v python3.11 >/dev/null 2>&1; then
        PYTHON_BIN=python3.11
        echo "INFO: Auto-selected python3.11 for OSS wheel build."
    else
        echo "INFO: python3.11 not found in default repos — installing via deadsnakes PPA..."
        sudo apt install -y software-properties-common
        sudo add-apt-repository -y ppa:deadsnakes/ppa
        sudo apt update -qq
        sudo apt install -y python3.11 python3.11-venv python3.11-dev python3.11-distutils || \
        sudo apt install -y python3.11 python3.11-venv python3.11-dev
        if ! command -v python3.11 >/dev/null 2>&1; then
            echo "ERROR: python3.11 still not found after deadsnakes install."
            echo "Check: sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt update && sudo apt install python3.11"
            exit 1
        fi
        PYTHON_BIN=python3.11
        echo "INFO: python3.11 installed successfully via deadsnakes PPA."
    fi
fi
unset _needs_311 _a

# Use a versioned venv dir so a 3.14 env is never accidentally reused for a
# 3.11 build.  Derive it from the actual binary we resolved above.
_py_ver="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}{sys.version_info.minor}")'  2>/dev/null || echo "3")"
VENV_DIR="${VENV_DIR:-$HOME/mochatools-android-env${_py_ver}}"
unset _py_ver
WHEEL_DIR="${WHEEL_DIR:-$HOME/mochatools-android-wheels}"
ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-$HOME/Android/Sdk}"
ANDROID_NDK_VERSION="${ANDROID_NDK_VERSION:-26.1.10909125}" # r26b
ANDROID_NDK_ROOT="${ANDROID_NDK_ROOT:-$ANDROID_SDK_ROOT/ndk/$ANDROID_NDK_VERSION}"
ANDROID_API="${ANDROID_API:-34}"
BUILD_TOOLS_VERSION="${BUILD_TOOLS_VERSION:-34.0.0}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SDKMANAGER="$ANDROID_SDK_ROOT/cmdline-tools/latest/bin/sdkmanager"
QTPIP_COMPAT_LIB_DIR="$VENV_DIR/qtpip-compat-libs"
PYSIDE_SETUP_DIR="${PYSIDE_SETUP_DIR:-$HOME/pyside-setup}"
QT_INSTALL_PATH="${QT_INSTALL_PATH:-}"

DO_ANDROID_TOOLS=false
DO_WHEELS=false
DO_OSS_WHEELS=false
DO_BUILD=false

for arg in "$@"; do
    case "$arg" in
        --android-tools) DO_ANDROID_TOOLS=true ;;
        --wheels) DO_WHEELS=true ;;
        --oss-wheels) DO_OSS_WHEELS=true ;;
        --build) DO_BUILD=true; DO_OSS_WHEELS=true ;;
        --all) DO_ANDROID_TOOLS=true; DO_OSS_WHEELS=true; DO_BUILD=true ;;
        -h|--help)
            sed -n '1,22p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg"
            exit 2
            ;;
    esac
done

echo "=============================================="
echo " Mocha Tools Android - Ubuntu Setup & Build"
echo "=============================================="
echo "Project: $PROJECT_DIR"
echo "Python:  $PYTHON_BIN"
echo "Venv:    $VENV_DIR"
echo "SDK:     $ANDROID_SDK_ROOT"
echo "NDK:     $ANDROID_NDK_ROOT"
echo ""

echo "[1/7] Installing system packages..."
sudo apt update -qq
sudo apt install -y \
    python3-pip python3-venv \
    python3.11 python3.11-venv python3.11-dev libpython3.11-dev \
    openjdk-17-jdk \
    libgl1-mesa-dev libglib2.0-dev \
    libxkbcommon-dev libxkbcommon-x11-dev \
    libdouble-conversion-dev libdouble-conversion3 \
    git wget curl unzip zip \
    build-essential cmake ninja-build patchelf clang \
    libssl-dev libffi-dev python3-dev \
    libsqlite3-dev zlib1g-dev \
    autoconf automake libtool pkg-config \
    lld libltdl-dev libxml2-dev libxslt1-dev \
    android-tools-adb

# Install the highest available libstdc++-dev so clang can link against it.
# clang on Linux defaults to GCC's libstdc++ as the C++ runtime; without the
# -dev package the linker cannot find -lstdc++ and CMake compiler detection fails.
install_libstdcxx_dev() {
    # Try versions from newest to oldest — stop at the first one that installs.
    for _v in 14 13 12 11 10; do
        if apt-cache show "libstdc++-${_v}-dev" &>/dev/null 2>&1; then
            echo "    Installing libstdc++-${_v}-dev..."
            sudo apt install -y "libstdc++-${_v}-dev" && return
        fi
    done
    echo "WARNING: Could not install any libstdc++-dev variant. The build may fail with '-lstdc++ not found'."
    echo "Try manually: sudo apt install libstdc++-12-dev  (or whichever version apt offers)"
}
install_libstdcxx_dev

# shiboken6's cmake parser requires libclang. It only supports up to clang-18;
# Ubuntu 26.04 ships clang-21 which breaks the cmake configure step.
# Install clang-18 + libclang-18-dev from the official LLVM apt repo now,
# so it is ready when build_oss_android_wheels() runs.
install_clang18_if_needed() {
    if [ -d /usr/lib/llvm-18 ] && dpkg -l libclang-18-dev &>/dev/null; then
        echo "    clang-18 + libclang-18-dev already installed."
        return
    fi
    echo "    Installing clang-18 + libclang-18-dev from LLVM apt repo..."
    sudo apt install -y lsb-release software-properties-common gnupg ca-certificates

    local codename
    codename="$(lsb_release -cs 2>/dev/null || echo jammy)"
    case "$codename" in
        # Known codenames with a dedicated LLVM-18 channel:
        focal|jammy|mantic|noble) : ;;
        # Everything else falls back to jammy (22.04) which is known-good
        # for our Ubuntu 22.04 target environment.
        *)
            echo "    Ubuntu codename '$codename' has no LLVM-18 apt channel — using jammy packages."
            codename="jammy"
            ;;
    esac

    local existing_list="/etc/apt/sources.list.d/llvm-18.list"
    if [ -f "$existing_list" ] && ! grep -q "llvm-toolchain-${codename}-18" "$existing_list"; then
        echo "    Removing stale $existing_list (wrong codename) and replacing with ${codename}..."
        sudo rm -f "$existing_list"
    fi

    if [ ! -f "$existing_list" ]; then
        # Ubuntu 22.04 uses /etc/apt/trusted.gpg.d/ with .asc or .gpg files —
        # wget -qO- piped to tee works for both. Use .gpg extension for binary key.
        wget -qO- https://apt.llvm.org/llvm-snapshot.gpg.key \
            | sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/llvm-18.gpg
        echo "deb http://apt.llvm.org/${codename}/ llvm-toolchain-${codename}-18 main" \
            | sudo tee "$existing_list"
        sudo apt update -qq
    fi

    sudo apt install -y clang-18 libclang-18-dev llvm-18-dev libc++-18-dev libc++abi-18-dev || {
        echo "ERROR: Could not install clang-18 from LLVM apt repo (codename: $codename)."
        echo "Try manually:"
        echo "  wget -qO- https://apt.llvm.org/llvm-snapshot.gpg.key | sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/llvm-18.gpg"
        echo "  echo 'deb http://apt.llvm.org/jammy/ llvm-toolchain-jammy-18 main' | sudo tee /etc/apt/sources.list.d/llvm-18.list"
        echo "  sudo apt update && sudo apt install clang-18 libclang-18-dev llvm-18-dev"
        exit 1
    }
    echo "    clang-18 installed at /usr/lib/llvm-18"
}
install_clang18_if_needed

export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-17-openjdk-amd64}"
export PATH="$ANDROID_SDK_ROOT/platform-tools:$ANDROID_SDK_ROOT/cmdline-tools/latest/bin:$PATH"

echo "    Java: $(java -version 2>&1 | head -1)"

echo ""
echo "[2/7] Setting up Python virtual environment..."

# If this run needs OSS wheels, verify we have 3.11 before touching anything.
if [ "$DO_OSS_WHEELS" = true ] || [ "$DO_BUILD" = true ]; then
    _actual_ver="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    if [ "$_actual_ver" != "3.11" ]; then
        echo "ERROR: OSS wheel build requires Python 3.11, but PYTHON_BIN ($PYTHON_BIN) is $_actual_ver."
        echo "Fix: sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt update"
        echo "     sudo apt install python3.11 python3.11-venv python3.11-dev"
        echo "Then rerun — the script will install and select python3.11 automatically."
        exit 1
    fi
    unset _actual_ver
fi

# Only create the venv if it doesn't exist yet.
# If it exists but was built with the wrong Python, warn and recreate it.
if [ -d "$VENV_DIR" ]; then
    _venv_ver="$("$VENV_DIR/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "unknown")"
    _want_ver="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    if [ "$_venv_ver" != "$_want_ver" ]; then
        echo "    WARNING: existing venv at $VENV_DIR is Python $_venv_ver, need $_want_ver. Recreating..."
        rm -rf "$VENV_DIR"
    fi
    unset _venv_ver _want_ver
fi

if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip setuptools wheel --quiet

echo ""
echo "[3/7] Installing Python packages into venv..."
python -m pip install --quiet \
    "PySide6>=6.7" \
    requests \
    certifi \
    urllib3 \
    charset-normalizer \
    idna \
    packaging \
    buildozer \
    cython \
    qtpip

echo "    Python:  $(command -v python)"
echo "    PySide6: $(python -c 'import PySide6; print(PySide6.__version__)')"
echo "    Deploy:  $(command -v pyside6-android-deploy || true)"

install_android_tools() {
    echo ""
    echo "[Android tools] Installing SDK API $ANDROID_API, build-tools $BUILD_TOOLS_VERSION, NDK r26b..."
    mkdir -p "$ANDROID_SDK_ROOT/cmdline-tools"

    if [ ! -x "$SDKMANAGER" ]; then
        echo "    Android command-line tools not found. Downloading them now..."
        tmp_dir="$(mktemp -d)"
        wget -q -O "$tmp_dir/commandlinetools.zip" \
            "https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"
        unzip -q "$tmp_dir/commandlinetools.zip" -d "$tmp_dir"
        rm -rf "$ANDROID_SDK_ROOT/cmdline-tools/latest"
        mkdir -p "$ANDROID_SDK_ROOT/cmdline-tools/latest"
        mv "$tmp_dir/cmdline-tools"/* "$ANDROID_SDK_ROOT/cmdline-tools/latest/"
        rm -rf "$tmp_dir"
    fi

    if [ ! -x "$SDKMANAGER" ]; then
        echo "ERROR: sdkmanager was not installed at $SDKMANAGER"
        echo "Install Android command-line tools with Qt Maintenance Tool or Android Studio, then rerun."
        exit 1
    fi

    echo "    sdkmanager: $SDKMANAGER"
    echo "    Accepting Android SDK licenses..."
    if ! timeout 120 bash -c 'yes | "$1" --sdk_root="$2" --licenses' _ "$SDKMANAGER" "$ANDROID_SDK_ROOT"; then
        echo "WARNING: sdkmanager --licenses did not exit cleanly after 120 seconds."
        echo "Continuing because accepted licenses may already be recorded."
    fi

    echo "    Currently installed SDK packages:"
    "$SDKMANAGER" --sdk_root="$ANDROID_SDK_ROOT" --list_installed | sed -n '1,80p' || true

    echo "    Installing platform-tools, API $ANDROID_API, build-tools $BUILD_TOOLS_VERSION, NDK $ANDROID_NDK_VERSION..."
    if ! bash -c 'yes | "$1" --sdk_root="$2" "${@:3}"' _ "$SDKMANAGER" "$ANDROID_SDK_ROOT" \
        "platform-tools" \
        "platforms;android-$ANDROID_API" \
        "build-tools;$BUILD_TOOLS_VERSION" \
        "ndk;$ANDROID_NDK_VERSION"; then
        echo ""
        echo "WARNING: Exact install request failed."
        echo "Trying again without build-tools $BUILD_TOOLS_VERSION so sdkmanager can keep any installed build-tools."
        bash -c 'yes | "$1" --sdk_root="$2" "${@:3}"' _ "$SDKMANAGER" "$ANDROID_SDK_ROOT" \
            "platform-tools" \
            "platforms;android-$ANDROID_API" \
            "ndk;$ANDROID_NDK_VERSION"
    fi

    echo "    Installed Android SDK packages:"
    "$SDKMANAGER" --sdk_root="$ANDROID_SDK_ROOT" --list_installed | sed -n '1,80p'

    if [ ! -d "$ANDROID_NDK_ROOT" ]; then
        detected_ndk="$(find "$ANDROID_SDK_ROOT/ndk" -maxdepth 1 -type d -name "26.*" 2>/dev/null | sort -V | tail -1)"
        if [ -n "$detected_ndk" ]; then
            ANDROID_NDK_ROOT="$detected_ndk"
            echo "    Detected NDK: $ANDROID_NDK_ROOT"
        fi
    fi
}

download_android_wheels() {
    echo ""
    echo "[Android wheels] Downloading commercial PySide6/shiboken6 wheels for aarch64..."
    if ! ldconfig -p 2>/dev/null | grep -q 'libdouble-conversion.so.3'; then
        echo "ERROR: qtpip needs libdouble-conversion.so.3."
        echo "Install it with: sudo apt install libdouble-conversion3"
        exit 1
    fi
    prepare_qtpip_compat_libs
    mkdir -p "$WHEEL_DIR"
    if ! (cd "$WHEEL_DIR" && LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}" qtpip download PySide6 --android --arch aarch64); then
        echo "ERROR: qtpip failed to download Android wheels."
        echo "Try: sudo apt install libdouble-conversion3 libxml2-dev"
        echo "If qtpip still asks for libxml2.so.2, use Ubuntu 24.04 LTS for this build."
        echo "If qtpip says 'Commercial License NOT found', run: bash setup_and_build.sh --oss-wheels"
        echo "Then rerun: bash setup_and_build.sh --wheels"
        exit 1
    fi
    if [ -z "$(find_android_wheel 'PySide6*android*aarch64*.whl')" ] \
        && [ -z "$(find_android_wheel 'PySide6*android*arm64*.whl')" ]; then
        echo "ERROR: qtpip finished but no PySide6 Android wheel was downloaded to $WHEEL_DIR"
        echo "If qtpip printed 'Commercial License NOT found', run: bash setup_and_build.sh --oss-wheels"
        exit 1
    fi
    if [ -z "$(find_android_wheel 'shiboken6*android*aarch64*.whl')" ] \
        && [ -z "$(find_android_wheel 'shiboken6*android*arm64*.whl')" ]; then
        echo "ERROR: qtpip finished but no shiboken6 Android wheel was downloaded to $WHEEL_DIR"
        echo "If qtpip printed 'Commercial License NOT found', run: bash setup_and_build.sh --oss-wheels"
        exit 1
    fi
}

detect_qt_install_path() {
    if [ -n "$QT_INSTALL_PATH" ]; then
        echo "$QT_INSTALL_PATH"
        return
    fi

    local pyside_version
    pyside_version="$(python -c 'import PySide6; print(PySide6.__version__)')"

    for candidate in \
        "$HOME/Qt/$pyside_version" \
        "$HOME/Qt/${pyside_version%.*}" \
        "$HOME/Qt/6.11.1" \
        "$HOME/Qt/6.11.0" \
        "$HOME/Qt/6.10.0" \
        "$HOME/Qt/6.9.0" \
        "$HOME/Qt/6.8.0" \
        "$HOME/Qt/6.7.0"; do
        if [ -d "$candidate/android_arm64_v8a" ] || [ -d "$candidate/gcc_64" ]; then
            echo "$candidate"
            return
        fi
    done

    if [ -d "$HOME/Qt" ]; then
        local android_kit
        android_kit="$(find "$HOME/Qt" -maxdepth 3 -type d -name android_arm64_v8a 2>/dev/null | sort -V | tail -1)"
        if [ -n "$android_kit" ]; then
            dirname "$android_kit"
            return
        fi
    fi

    echo ""
}

print_qt_install_help() {
    echo "Qt folders found under \$HOME/Qt:"
    if [ -d "$HOME/Qt" ]; then
        find "$HOME/Qt" -maxdepth 3 -type d \( -name android_arm64_v8a -o -name gcc_64 -o -name '6.*' \) 2>/dev/null | sort -V | sed 's/^/  /'
    else
        echo "  none; $HOME/Qt does not exist"
    fi
    echo ""
    echo "Install Qt for Android arm64-v8a with Qt Maintenance Tool."
    echo "Then set QT_INSTALL_PATH to the version folder that contains android_arm64_v8a, for example:"
    echo "  export QT_INSTALL_PATH=\$HOME/Qt/6.11.1"
}

install_qt_for_android() {
    # Install Qt for Android using aqtinstall — a pip tool that pulls directly
    # from Qt's archive mirrors with no online-installer framework overhead.
    # Avoids the hang caused by the Qt online installer fetching a live catalogue.
    #
    # Key lessons from prior failures:
    #   - Use --autodesktop when installing the android kit: it installs the
    #     matching linux_gcc_64 host toolchain in the same aqt call, which avoids
    #     the separate desktop XML parse that triggers the "qt_base not found" error.
    #   - Use arch name "linux_gcc_64" (not the alias "gcc_64") for direct desktop
    #     installs — more reliable across Qt versions.
    #   - Do not pass --archives for the android kit: module names vary by patch
    #     release and cause XML parse failures.
    local qt_version="${1:-6.8.0}"
    local qt_install_root="$HOME/Qt"

    echo "    Installing aqtinstall (Qt archive tool)..."
    pip install --quiet --upgrade aqtinstall

    # --- Android arm64-v8a kit + host gcc_64 in one shot ---
    # --autodesktop automatically installs the linux_gcc_64 host tools alongside
    # the android kit, no separate desktop XML fetch required.
    if [ -d "$qt_install_root/$qt_version/android_arm64_v8a" ] && [ -d "$qt_install_root/$qt_version/gcc_64" ]; then
        echo "    Both android_arm64_v8a and gcc_64 already present — skipping Qt install."
    elif [ -d "$qt_install_root/$qt_version/android_arm64_v8a" ] && [ ! -d "$qt_install_root/$qt_version/gcc_64" ]; then
        # Partial install: android kit present, host tools missing.
        # Install desktop directly using the full arch name linux_gcc_64.
        echo "    android_arm64_v8a present but gcc_64 missing — installing host tools..."
        python -m aqt install-qt linux desktop "$qt_version" linux_gcc_64 \
            --outputdir "$qt_install_root"
    else
        echo "    Installing Qt $qt_version android_arm64_v8a + gcc_64 host tools to $qt_install_root ..."
        echo "    This downloads ~1 GB and takes a few minutes."
        # --autodesktop installs gcc_64 alongside the android kit in one XML fetch.
        python -m aqt install-qt linux android "$qt_version" android_arm64_v8a \
            --outputdir "$qt_install_root" \
            --autodesktop
    fi

    if [ ! -d "$qt_install_root/$qt_version/android_arm64_v8a" ]; then
        echo "ERROR: Qt $qt_version android_arm64_v8a not found after install."
        echo "Try manually: python -m aqt install-qt linux android $qt_version android_arm64_v8a --outputdir ~/Qt --autodesktop"
        exit 1
    fi
    if [ ! -d "$qt_install_root/$qt_version/gcc_64" ]; then
        echo "ERROR: Qt $qt_version gcc_64 host tools not found after install."
        echo "Try manually: python -m aqt install-qt linux desktop $qt_version linux_gcc_64 --outputdir ~/Qt"
        exit 1
    fi

    echo "    Qt $qt_version installed successfully at $qt_install_root/$qt_version"
    QT_INSTALL_PATH="$qt_install_root/$qt_version"
}

build_oss_android_wheels() {
    echo ""
    echo "[Android wheels] Building LGPL/GPL PySide6/shiboken6 Android wheels from source..."

    local pyside_version
    local python_version
    local qt_path
    python_version="$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    if [ "$python_version" != "3.11" ]; then
        echo "ERROR: PySide Android cross-compile expects Python 3.11, but this venv is Python $python_version."
        echo "Create/use a 3.11 venv, for example:"
        echo "  export VENV_DIR=\$HOME/mochatools-android-env311"
        echo "  export PYTHON_BIN=python3.11"
        echo "  bash setup_and_build.sh --oss-wheels"
        exit 1
    fi

    pyside_version="$(python -c 'import PySide6; print(PySide6.__version__)')"
    qt_path="$(detect_qt_install_path)"

    # Resolve which Qt version to auto-install (used if either kit is missing).
    _pick_auto_qt_ver() {
        local pyside_ver="$1"
        local result="6.8.0"
        pip install --quiet --upgrade aqtinstall
        for try_ver in "$pyside_ver" "${pyside_ver%.*}.0" "6.9.0" "6.8.0"; do
            if python -m aqt list-qt linux android 2>/dev/null | grep -qF "$try_ver"; then
                result="$try_ver"
                echo "    Selecting Qt $result from aqt archive." >&2
                break
            fi
        done
        echo "$result"
    }

    # Qt not found at all — full install.
    if [ -z "$qt_path" ]; then
        echo "    Qt for Android not found — installing automatically via aqtinstall..."
        local auto_qt_ver
        auto_qt_ver="$(_pick_auto_qt_ver "$pyside_version")"
        install_qt_for_android "$auto_qt_ver"
        qt_path="$(detect_qt_install_path)"
    fi

    if [ -z "$qt_path" ]; then
        echo "ERROR: Could not determine Qt install path even after automatic install."
        print_qt_install_help
        exit 1
    fi

    # Guard: qt_path must be the version root, not a kit subdirectory.
    # If QT_INSTALL_PATH was set to e.g. ~/Qt/6.8.0/gcc_64, fail loudly.
    if [[ "$qt_path" == */gcc_64 ]] || [[ "$qt_path" == */android_arm64_v8a ]]; then
        echo "ERROR: QT_INSTALL_PATH '$qt_path' points to a kit subdirectory."
        echo "Set it to the version root, e.g.: export QT_INSTALL_PATH=\$HOME/Qt/6.8.0"
        exit 1
    fi

    # android_arm64_v8a present but gcc_64 missing (partial previous install).
    # This is the exact state after a failed gcc_64 install — re-run just that piece.
    if [ -d "$qt_path/android_arm64_v8a" ] && [ ! -d "$qt_path/gcc_64" ]; then
        echo "    android_arm64_v8a found but gcc_64 host tools missing — installing now..."
        local qt_ver_only
        qt_ver_only="$(basename "$qt_path")"
        pip install --quiet --upgrade aqtinstall
        # Use the full arch name linux_gcc_64 — the alias gcc_64 causes XML parse
        # errors on some Qt versions with certain aqt mirror combinations.
        python -m aqt install-qt linux desktop "$qt_ver_only" linux_gcc_64             --outputdir "$HOME/Qt"
    fi

    if [ ! -d "$qt_path/android_arm64_v8a" ]; then
        echo "ERROR: $qt_path exists but does not contain android_arm64_v8a."
        print_qt_install_help
        exit 1
    fi

    if [ ! -d "$qt_path/gcc_64" ]; then
        echo "ERROR: $qt_path exists but does not contain gcc_64 host tools."
        echo "Try: pip install aqtinstall && python -m aqt install-qt linux desktop <ver> linux_gcc_64 --outputdir ~/Qt"
        exit 1
    fi

    echo "    PySide version: $pyside_version"
    echo "    Qt install:     $qt_path"
    echo "    Source repo:    $PYSIDE_SETUP_DIR"
    mkdir -p "$WHEEL_DIR"

    if [ ! -d "$PYSIDE_SETUP_DIR/.git" ]; then
        git clone https://code.qt.io/pyside/pyside-setup "$PYSIDE_SETUP_DIR"
    fi

    (
        cd "$PYSIDE_SETUP_DIR"
        git fetch --tags origin

        # Prefer the highest patch tag in the same minor series.
        # v6.8.0 has two known bugs fixed in v6.8.3:
        #   BUG A: QLatin1String operator+ deleted in Qt 6.8 headers
        #   BUG B: main.py passes gcc_64 as --qt-target-path instead of android_arm64_v8a
        local minor_prefix="${pyside_version%.*}"
        local best_tag
        best_tag="$(git tag --list "v${minor_prefix}.*" | sort -V | tail -1)"
        if [ -z "$best_tag" ]; then
            best_tag="v$pyside_version"
            git rev-parse --verify --quiet "$best_tag" >/dev/null 2>&1 || best_tag="$pyside_version"
            git rev-parse --verify --quiet "$best_tag" >/dev/null 2>&1 || best_tag="$minor_prefix"
        fi
        echo "    Checking out: $best_tag  (pip-installed PySide6: $pyside_version)"
        git checkout "$best_tag"

        python -m pip install -r requirements.txt
        python -m pip install -r tools/cross_compile_android/requirements.txt

        # ---------------------------------------------------------------
        # Source patch A: "literal"_L1 + QStringView fails on Qt 6.8
        # ---------------------------------------------------------------
        # The _L1 UDL suffix produces QLatin1StringView. Qt 6.8 deleted
        # operator+(QLatin1StringView, QStringView), so these lines in
        # ApiExtractor fail to compile with clang-18 on Ubuntu 22.04:
        #
        #   messages.cpp:      result += " ("_L1 + why + u')';
        #   typesystemparser.cpp (x3): "..."_L1\n + tagFromElement(...)
        #
        # Fix: wrap the _L1 literal in QString(QLatin1String(...)) so the
        # concatenation becomes QString + QStringView, which is defined.
        # Patch both source dirs since cmake may copy one to the other.
        echo "    Patch A: fix QLatin1StringView (_L1) + QStringView operator+ errors..."
        for _src_dir in \
            sources/shiboken6/ApiExtractor \
            sources/shiboken6_generator/ApiExtractor; do
            [ -d "$_src_dir" ] || continue

            # Fix 1 — messages.cpp: " ("_L1 + why  (why is QStringView)
            if [ -f "$_src_dir/messages.cpp" ]; then
                sed -i \
                    's/" ("_L1 + why/QString(QLatin1String(" (")) + why/g' \
                    "$_src_dir/messages.cpp"
                echo "      patched $_src_dir/messages.cpp"
            fi

            # Fix 2 — typesystemparser.cpp: three occurrences of
            #   "..."_L1\n                  + tagFromElement(topElement)
            # tagFromElement() returns QStringView; python3 handles the
            # multi-line match cleanly without needing GNU sed -z.
            if [ -f "$_src_dir/typesystemparser.cpp" ]; then
                python3 - "$_src_dir/typesystemparser.cpp" <<'PYEOF'
import re, sys
path = sys.argv[1]
with open(path) as f:
    src = f.read()
original = src
src = re.sub(
    r'"([^"]+)"_L1(\s*\n\s*\+\s*tagFromElement)',
    r'QString(QLatin1String("\1"))\2',
    src
)
if src != original:
    with open(path, 'w') as f:
        f.write(src)
    print(f"      patched {path}")
else:
    print(f"      (no _L1+tagFromElement patterns found in {path})")
PYEOF
            fi

            # Fix 3 — belt-and-suspenders: any remaining bare QLatin1StringView(
            # constructor calls (older shiboken versions use the explicit form).
            find "$_src_dir" -type f \( -name "*.cpp" -o -name "*.h" \) \
                -exec grep -ql 'QLatin1StringView(' {} \; | \
            while IFS= read -r _f; do
                sed -i 's/QLatin1StringView(/QString::fromLatin1(/g' "$_f"
                echo "      patched (QLatin1StringView) $_f"
            done
        done
        echo "    Patch A applied."

        # Resolve venv Python path and prefix early — needed by both Patch B
        # (source patch) and the toolchain file / invocation further below.
        local venv_python
        venv_python="$(command -v python)"        # already in venv due to 'source activate'
        local python_prefix
        python_prefix="$(python -c 'import sys; print(sys.prefix)')"

        # ---------------------------------------------------------------
        # Resolve Python_LIBRARY + Python_INCLUDE_DIR for CMake.
        # ---------------------------------------------------------------
        # CMake 3.22 FindPython with COMPONENTS Development bypasses ALL
        # Python_EXECUTABLE / Python_ROOT_DIR hints and re-searches from
        # scratch.  The only reliable bypass (Support.cmake:2344-2352) is
        # to pre-set Python_LIBRARY and Python_INCLUDE_DIR as absolute paths
        # before find_package ever runs — then the search is skipped entirely.
        #
        # The old "Patch B" approach (regex-inject into ShibokenHelpers.cmake)
        # was fragile: the else()/find_package(Python pattern varies across
        # PySide versions, silently fails to match, and the variable injection
        # is skipped.  We now pass every path via PYSIDE_SETUP_EXTRA_CMAKE_ARGS
        # instead — cross_compile_android/main.py forwards those -D flags to
        # every cmake invocation it spawns, including sub-build directories
        # where env-var inheritance is unreliable.
        echo "    Resolving Python_LIBRARY and Python_INCLUDE_DIR..."

        # Probe well-known paths directly — no subprocess, no sudo, no hang.
        local py_include_dir=""
        for _inc in \
            /usr/include/python3.11 \
            /usr/local/include/python3.11; do
            if [ -f "$_inc/Python.h" ]; then
                py_include_dir="$_inc"
                break
            fi
        done
        if [ -z "$py_include_dir" ]; then
            echo "ERROR: Python.h not found. Run: sudo apt install python3.11-dev libpython3.11-dev"
            exit 1
        fi

        local py_library=""
        for _lib in \
            /usr/lib/x86_64-linux-gnu/libpython3.11.so \
            /usr/lib/x86_64-linux-gnu/libpython3.11.so.1.0 \
            /usr/lib/x86_64-linux-gnu/libpython3.11m.so \
            /usr/lib/x86_64-linux-gnu/libpython3.11.a \
            /usr/lib/aarch64-linux-gnu/libpython3.11.so \
            /usr/lib/aarch64-linux-gnu/libpython3.11.so.1.0 \
            /usr/lib/aarch64-linux-gnu/libpython3.11.a \
            /usr/lib/libpython3.11.so \
            /usr/lib/libpython3.11.a \
            /usr/local/lib/libpython3.11.so \
            /usr/local/lib/libpython3.11.a; do
            if [ -f "$_lib" ]; then
                py_library="$_lib"
                break
            fi
        done
        if [ -z "$py_library" ]; then
            echo "ERROR: libpython3.11 not found. Run: sudo apt install python3.11-dev libpython3.11-dev"
            exit 1
        fi

        echo "    Python_INCLUDE_DIR = $py_include_dir"
        echo "    Python_LIBRARY     = $py_library"

        # --- Patch ShibokenHelpers.cmake (belt-and-suspenders) ---
        # Inject CACHE FORCE set() calls before EVERY find_package(Python call
        # so the bypass fires even if PYSIDE_SETUP_EXTRA_CMAKE_ARGS is not
        # forwarded by an older cross_compile_android/main.py.
        # Unlike the old regex that targeted a specific else() branch, this
        # pattern matches every find_package(Python occurrence regardless of
        # surrounding context or indentation style.
        local helpers_cmake="sources/shiboken6/cmake/ShibokenHelpers.cmake"
        if [ -f "$helpers_cmake" ] && ! grep -q 'PYSIDE_SETUP_SH_PYTHON_PATCH' "$helpers_cmake"; then
            python3 - "$helpers_cmake" "$py_library" "$py_include_dir" "$venv_python" <<'PATCH_B_EOF'
import sys, re
cmake_path, py_library, py_include_dir, venv_python = sys.argv[1:]
with open(cmake_path) as f:
    src = f.read()

inject = "\n".join([
    "    # PYSIDE_SETUP_SH_PYTHON_PATCH: pre-set paths so FindPython skips its search.",
    '    set(Python_EXECUTABLE "' + venv_python + '" CACHE FILEPATH "" FORCE)',
    '    set(Python3_EXECUTABLE "' + venv_python + '" CACHE FILEPATH "" FORCE)',
    '    set(Python_LIBRARY "' + py_library + '" CACHE FILEPATH "" FORCE)',
    '    set(Python3_LIBRARY "' + py_library + '" CACHE FILEPATH "" FORCE)',
    '    set(Python_INCLUDE_DIR "' + py_include_dir + '" CACHE PATH "" FORCE)',
    '    set(Python3_INCLUDE_DIR "' + py_include_dir + '" CACHE PATH "" FORCE)',
    '    set(Python_FIND_VIRTUALENV FIRST CACHE STRING "" FORCE)',
    '    set(Python3_FIND_VIRTUALENV FIRST CACHE STRING "" FORCE)',
    "",
])

# Match every find_package( call that mentions Python, regardless of indentation
# or surrounding branch. Version-agnostic and never silently misses.
pattern = re.compile(
    r'([ \t]*find_package\s*\(\s*Python)',
    re.IGNORECASE,
)
patched, count = pattern.subn(inject + r'\1', src)

if count == 0:
    print("WARNING: no find_package(Python...) call found in " + cmake_path)
    print("Dumping find_package lines for diagnosis:")
    for i, line in enumerate(src.splitlines(), 1):
        if 'find_package' in line.lower():
            print(f"  {i}: {repr(line)}")
else:
    with open(cmake_path, 'w') as f:
        f.write(patched)
    print(f"patched {cmake_path} ({count} injection site(s))")
PATCH_B_EOF
        elif grep -q 'PYSIDE_SETUP_SH_PYTHON_PATCH' "$helpers_cmake" 2>/dev/null; then
            echo "      Patch B already applied."
        else
            echo "      WARNING: $helpers_cmake not found — Patch B skipped."
        fi
        echo "    Patch B done."

        # Note: --qt-target-path=gcc_64 for the shiboken6-generator build is intentional.
        # setup_runner.py deliberately uses the host Qt (gcc_64) to build the generator
        # since it runs on the host, not the Android target.

        # Point shiboken6's cmake to clang-18 explicitly.
        # The system default (clang-21 on Ubuntu 26.04) triggers an ABI guard
        # error in ClangTargets.cmake that aborts the configure step.
        local llvm_dir="/usr/lib/llvm-18"
        if [ ! -d "$llvm_dir" ]; then
            echo "ERROR: $llvm_dir not found. Run: bash setup_and_build.sh (without flags) to install clang-18 first."
            exit 1
        fi
        echo "    Using LLVM_INSTALL_DIR=$llvm_dir for shiboken6 cmake."

        # Wipe stale CMake cache so it cannot lock in wrong libclang or Python paths.
        echo "    Clearing stale CMake caches and build artifacts..."
        rm -rf build
        find . -name "CMakeCache.txt" -delete
        find . -name "CMakeFiles" -type d -exec rm -rf {} + 2>/dev/null || true

        # Prepend clang-18 bin so CMake compiler detection finds clang-18, not clang-21.
        export PATH="$llvm_dir/bin:$PATH"

        # Write a CMake toolchain file that forces all LLVM/Clang and Python paths.
        # NOTE: heredoc uses double-quote delimiter so shell vars are expanded here,
        # but CMake ${...} variables are escaped with a backslash.
        local toolchain_file="$PWD/llvm18_toolchain.cmake"
        cat > "$toolchain_file" <<TOOLCHAIN_EOF
# Forces clang-18 for shiboken6 ApiExtractor — prevents clang-21 on Ubuntu 26.04.
set(LLVM_INSTALL_DIR "/usr/lib/llvm-18" CACHE PATH "" FORCE)
set(LLVM_DIR "/usr/lib/llvm-18/lib/cmake/llvm" CACHE PATH "" FORCE)
set(Clang_DIR "/usr/lib/llvm-18/lib/cmake/clang" CACHE PATH "" FORCE)
set(CMAKE_C_COMPILER "/usr/lib/llvm-18/bin/clang" CACHE FILEPATH "" FORCE)
set(CMAKE_CXX_COMPILER "/usr/lib/llvm-18/bin/clang++" CACHE FILEPATH "" FORCE)

# Pre-set Python paths so FindPython skips its Development search entirely.
# CMake 3.22 Support.cmake:2344-2352: when Python_LIBRARY and Python_INCLUDE_DIR
# are absolute paths in the cache, find_library/find_path are not run.
set(Python_EXECUTABLE "$venv_python" CACHE FILEPATH "" FORCE)
set(Python3_EXECUTABLE "$venv_python" CACHE FILEPATH "" FORCE)
set(Python_ROOT_DIR "$python_prefix" CACHE PATH "" FORCE)
set(Python3_ROOT_DIR "$python_prefix" CACHE PATH "" FORCE)
set(Python_FIND_VIRTUALENV FIRST CACHE STRING "" FORCE)
set(Python3_FIND_VIRTUALENV FIRST CACHE STRING "" FORCE)
set(Python_LIBRARY "$py_library" CACHE FILEPATH "" FORCE)
set(Python3_LIBRARY "$py_library" CACHE FILEPATH "" FORCE)
set(Python_INCLUDE_DIR "$py_include_dir" CACHE PATH "" FORCE)
set(Python3_INCLUDE_DIR "$py_include_dir" CACHE PATH "" FORCE)

# clang on Linux defaults to linking against GCC's libstdc++.
# Fall back to libc++ if libstdc++-dev is absent (e.g. minimal container).
if(EXISTS "/usr/lib/llvm-18/lib/libc++.a" OR EXISTS "/usr/lib/x86_64-linux-gnu/libc++.so")
    set(CMAKE_CXX_FLAGS "\${CMAKE_CXX_FLAGS} -stdlib=libc++" CACHE STRING "" FORCE)
    set(CMAKE_EXE_LINKER_FLAGS "\${CMAKE_EXE_LINKER_FLAGS} -stdlib=libc++ -lc++abi" CACHE STRING "" FORCE)
    set(CMAKE_SHARED_LINKER_FLAGS "\${CMAKE_SHARED_LINKER_FLAGS} -stdlib=libc++ -lc++abi" CACHE STRING "" FORCE)
endif()
TOOLCHAIN_EOF
        echo "    CMake toolchain file: $toolchain_file"

        # PYSIDE_SETUP_EXTRA_CMAKE_ARGS is read by cross_compile_android/main.py
        # and appended to every cmake -D invocation it spawns.  This is the most
        # reliable channel: it survives into sub-build directories where env-var
        # inheritance and toolchain files sometimes do not propagate.
        local extra_cmake_args
        extra_cmake_args="\
-DPython_EXECUTABLE=${venv_python} \
-DPython3_EXECUTABLE=${venv_python} \
-DPython_LIBRARY=${py_library} \
-DPython3_LIBRARY=${py_library} \
-DPython_INCLUDE_DIR=${py_include_dir} \
-DPython3_INCLUDE_DIR=${py_include_dir} \
-DPython_ROOT_DIR=${python_prefix} \
-DPython3_ROOT_DIR=${python_prefix} \
-DPython_FIND_VIRTUALENV=FIRST \
-DPython3_FIND_VIRTUALENV=FIRST \
-DLLVM_INSTALL_DIR=${llvm_dir} \
-DClang_DIR=${llvm_dir}/lib/cmake/clang \
-DLLVM_DIR=${llvm_dir}/lib/cmake/llvm"

        PYSIDE_SETUP_EXTRA_CMAKE_ARGS="$extra_cmake_args" \
        LLVM_INSTALL_DIR="$llvm_dir" \
        CC="$llvm_dir/bin/clang" \
        CXX="$llvm_dir/bin/clang++" \
        CMAKE_PREFIX_PATH="$llvm_dir" \
        CMAKE_TOOLCHAIN_FILE="$toolchain_file" \
        Python_EXECUTABLE="$venv_python" \
        Python3_EXECUTABLE="$venv_python" \
        Python_ROOT_DIR="$python_prefix" \
        Python3_ROOT_DIR="$python_prefix" \
        Python_LIBRARY="$py_library" \
        Python3_LIBRARY="$py_library" \
        Python_INCLUDE_DIR="$py_include_dir" \
        Python3_INCLUDE_DIR="$py_include_dir" \
        python tools/cross_compile_android/main.py \
            --plat-name=aarch64 \
            --qt-install-path="$qt_path" \
            --auto-accept-license \
            --skip-update
    )

    find "$PYSIDE_SETUP_DIR" -type f \( -iname 'PySide6*android*aarch64*.whl' -o -iname 'PySide6*android*arm64*.whl' -o -iname 'shiboken6*android*aarch64*.whl' -o -iname 'shiboken6*android*arm64*.whl' \) \
        -exec cp -f {} "$WHEEL_DIR/" \;

    if [ -z "$(find_android_wheel 'PySide6*android*aarch64*.whl')" ] \
        && [ -z "$(find_android_wheel 'PySide6*android*arm64*.whl')" ]; then
        echo "ERROR: OSS wheel build finished but no PySide6 Android wheel was found."
        echo "Search manually with: find $PYSIDE_SETUP_DIR -name '*android*.whl'"
        exit 1
    fi
    if [ -z "$(find_android_wheel 'shiboken6*android*aarch64*.whl')" ] \
        && [ -z "$(find_android_wheel 'shiboken6*android*arm64*.whl')" ]; then
        echo "ERROR: OSS wheel build finished but no shiboken6 Android wheel was found."
        echo "Search manually with: find $PYSIDE_SETUP_DIR -name '*android*.whl'"
        exit 1
    fi
}

find_android_wheel() {
    local pattern="$1"
    find "$WHEEL_DIR" -maxdepth 1 -type f -iname "$pattern" | sort -V | tail -1
}

prepare_qtpip_compat_libs() {
    local libxml2_so_16

    if ldconfig -p 2>/dev/null | grep -q 'libxml2.so.2'; then
        return
    fi

    libxml2_so_16="$(ldconfig -p 2>/dev/null | awk '/libxml2\.so\.16 / {print $NF; exit}')"
    if [ -n "$libxml2_so_16" ] && [ -f "$libxml2_so_16" ]; then
        mkdir -p "$QTPIP_COMPAT_LIB_DIR"
        ln -sfn "$libxml2_so_16" "$QTPIP_COMPAT_LIB_DIR/libxml2.so.2"
        export LD_LIBRARY_PATH="$QTPIP_COMPAT_LIB_DIR:${LD_LIBRARY_PATH:-}"
        echo "    Using local qtpip compat libxml2.so.2 -> $libxml2_so_16"
        return
    fi

    echo "ERROR: qtpip needs libxml2.so.2."
    echo "Your system does not expose libxml2.so.2 or libxml2.so.16 via ldconfig."
    echo "Run: ldconfig -p | grep libxml2"
    echo "Best fix: build from Ubuntu 24.04 LTS, which still provides libxml2.so.2."
    exit 1
}

update_deploy_spec() {
    echo ""
    echo "[Spec] Updating pysidedeploy.spec with this venv/toolchain..."
    local pyside_wheel
    local shiboken_wheel
    pyside_wheel="$(find_android_wheel 'PySide6*android*aarch64*.whl')"
    shiboken_wheel="$(find_android_wheel 'shiboken6*android*aarch64*.whl')"
    if [ -z "$pyside_wheel" ]; then
        pyside_wheel="$(find_android_wheel 'PySide6*android*arm64*.whl')"
    fi
    if [ -z "$shiboken_wheel" ]; then
        shiboken_wheel="$(find_android_wheel 'shiboken6*android*arm64*.whl')"
    fi

    if [ -z "$pyside_wheel" ] || [ -z "$shiboken_wheel" ]; then
        echo "ERROR: Android wheels were not found in $WHEEL_DIR"
        echo "Run: bash setup_and_build.sh --wheels"
        exit 1
    fi

    python - "$PROJECT_DIR/pysidedeploy.spec" "$VENV_DIR/bin/python3" \
        "$pyside_wheel" "$shiboken_wheel" "$ANDROID_NDK_ROOT" "$ANDROID_SDK_ROOT" <<'PY'
import configparser
import pathlib
import sys

spec, python_path, pyside_wheel, shiboken_wheel, ndk_path, sdk_path = sys.argv[1:]
path = pathlib.Path(spec)
cfg = configparser.ConfigParser()
cfg.optionxform = str
cfg.read(path)

for section in ("app", "python", "qt", "android", "buildozer"):
    if section not in cfg:
        cfg[section] = {}

cfg["app"].update({
    "title": "MochaTools",
    "project_dir": ".",
    "input_file": "main.py",
    "project_file": "",
    "exec_directory": "",
})
cfg["python"].update({
    "python_path": python_path,
    "android_packages": "buildozer,cpython",
    "packages": "requests, certifi, urllib3, charset-normalizer, idna, packaging",
    "protected_packages": "requests, certifi, urllib3, charset-normalizer, idna, packaging",
})
cfg["qt"].update({
    "modules": "Core, Gui, Widgets",
    "plugins": "",
})
cfg["android"].update({
    "wheel_pyside": pyside_wheel,
    "wheel_shiboken": shiboken_wheel,
    "plugins": "platforms, imageformats",
})
cfg["buildozer"].update({
    "mode": "debug",
    "recipe_dir": "",
    "jars_dir": "",
    "ndk_path": ndk_path,
    "sdk_path": sdk_path,
    "local_libs": "",
    "arch": "aarch64",
})

with path.open("w", encoding="utf-8") as f:
    cfg.write(f, space_around_delimiters=True)
PY
}

echo ""
echo "[4/7] Verifying project structure..."
cd "$PROJECT_DIR"
required_files=(
    "main.py"
    "mochatools_app/__init__.py"
    "mochatools_app/constants.py"
    "mochatools_app/logging_utils.py"
    "mochatools_app/workers.py"
    "mochatools_app/styles.py"
    "mochatools_app/dialogs_android.py"
    "mochatools_app/app_android.py"
    "pysidedeploy.spec"
)
for f in "${required_files[@]}"; do
    if [ ! -f "$f" ]; then
        echo "ERROR: missing $f"
        exit 1
    fi
    echo "    OK $f"
done

echo ""
echo "[5/7] Running import smoke test..."
python - <<'PY'
import sys
sys.path.insert(0, ".")
from mochatools_app.constants import APP_NAME, APP_VERSION
from mochatools_app.logging_utils import write_debug_log
from mochatools_app.workers import UploadWorker, FilesWorker, RemoteWorker
from mochatools_app.styles import STYLESHEET
from mochatools_app.dialogs_android import FolderBrowserDialog, ShareLinkDialog
assert len(STYLESHEET) > 100
print(f"    OK imports - {APP_NAME} {APP_VERSION}")
PY

if [ "$DO_ANDROID_TOOLS" = true ]; then
    install_android_tools
fi

if [ "$DO_WHEELS" = true ]; then
    download_android_wheels
    update_deploy_spec
fi

if [ "$DO_OSS_WHEELS" = true ]; then
    build_oss_android_wheels
    update_deploy_spec
fi

echo ""
echo "[6/7] Desktop UI test..."
if [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
    echo "    Display detected - launching desktop UI for 3 seconds..."
    timeout 3 python main.py >/dev/null 2>&1 || true
else
    echo "    No display detected - skipping live UI test."
fi

echo ""
if [ "$DO_BUILD" = true ]; then
    echo "[7/7] Building Android package..."
    if [ ! -d "$ANDROID_NDK_ROOT" ]; then
        detected_ndk="$(find "$ANDROID_SDK_ROOT/ndk" -maxdepth 1 -type d -name "26.*" 2>/dev/null | sort -V | tail -1)"
        if [ -n "$detected_ndk" ]; then
            ANDROID_NDK_ROOT="$detected_ndk"
        fi
    fi
    if [ ! -d "$ANDROID_SDK_ROOT" ]; then
        echo "ERROR: Android SDK not found at $ANDROID_SDK_ROOT"
        echo "Run: bash setup_and_build.sh --android-tools"
        exit 1
    fi
    if [ ! -d "$ANDROID_NDK_ROOT" ]; then
        echo "ERROR: Android NDK r26b not found at $ANDROID_NDK_ROOT"
        echo "Run: bash setup_and_build.sh --android-tools"
        exit 1
    fi
    pyside6-android-deploy --config-file "$PROJECT_DIR/pysidedeploy.spec" --force --verbose

    echo ""
    echo "Built packages:"
    find "$PROJECT_DIR" -type f \( -name "*.apk" -o -name "*.aab" \) -print
else
    echo "[7/7] APK build skipped. Run with --build after Android tools are installed."
fi

echo ""
echo "Done."
echo ""
echo "Useful exports for your shell:"
echo "  export JAVA_HOME=$JAVA_HOME"
echo "  export ANDROID_SDK_ROOT=$ANDROID_SDK_ROOT"
echo "  export ANDROID_NDK_ROOT=$ANDROID_NDK_ROOT"
echo "  export PATH=$ANDROID_SDK_ROOT/platform-tools:$ANDROID_SDK_ROOT/cmdline-tools/latest/bin:\$PATH"