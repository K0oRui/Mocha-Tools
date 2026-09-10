CHUNK_SIZE = 50 * 1024 * 1024
PART_UPLOAD_RETRIES = 10
PART_UPLOAD_TIMEOUT = 7200
S3_DEFAULT_CONCURRENCY = 24
S3_MAX_CONCURRENCY = 24
RELAY_DEFAULT_CONCURRENCY = 1
RELAY_MAX_CONCURRENCY = 1

DEFAULT_CHUNK_SIZE_MB = 50
DEFAULT_MAX_CHUNKS = 20
APP_NAME = "MochaTools"
ORG_NAME = "Mocha"
HARDCODED_BASE_URL = "https://api.mocha.my"
SHARE_BASE_URL = "https://mocha.my"

# Stamped at build time by build.py — do not edit manually.
APP_VERSION = "8.2.0"
APP_CHANGES: list[dict[str, str]] = [
    {
        "subject": "feat(build): add PowerShell build launcher with dependency checks",
        "description": "- Add build.ps1 as a cross-platform alternative to build.bat\n- Remove build.bat (superseded by build.ps1)\n- Check for platform-specific build dependencies before compiling\n- Default target OS prompt to the current platform\n- Remove invalid --linux-app-icon flag from onefile builds\n- Add --force to fpm so rebuilds overwrite existing artifacts\n- Update release workflow version example",
    },
    {
        "subject": "fix(desktop): keep corners rounded on Linux when not at screen edge",
        "description": "",
    },
    {
        "subject": "feat(build): rework Linux packaging for glibc-pinned multi-arch builds",
        "description": "Pin x86_64 builds to manylinux_2_34 and aarch64 to ubuntu-24.04-arm so each artifact targets the correct glibc floor. Add AppRun to AppImage packaging and set the desktop file name so the taskbar icon resolves on Wayland.",
    },
    {
        "subject": "fix(macos): make app build and run on macOS",
        "description": "- guard window_chrome ctypes/wintypes behind sys.platform so the app\n  no longer crashes at import on macOS\n- pass bundle identifier, min-version, and category to Nuitka instead\n  of the unused Info.plist\n- add /Applications shortcut to the DMG\n- document Gatekeeper workaround for DMG and pkg",
    },
]

UPDATE_CHECK_URL = "https://api.github.com/repos/nxllvxxd/Mocha-Tools/releases/latest"
