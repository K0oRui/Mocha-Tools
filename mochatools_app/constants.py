CHUNK_SIZE = 50 * 1024 * 1024
PART_UPLOAD_RETRIES = 10
PART_UPLOAD_TIMEOUT = 7200
S3_DEFAULT_CONCURRENCY = 24
S3_MAX_CONCURRENCY = 24
RELAY_DEFAULT_CONCURRENCY = 1
RELAY_MAX_CONCURRENCY = 1

# Configurable multipart defaults (overridable via Settings UI)
DEFAULT_CHUNK_SIZE_MB = 50    # 1–100 MB per chunk
DEFAULT_MAX_CHUNKS    = 20    # 1–20 parallel chunks in flight at once
APP_NAME = "MochaTools"
ORG_NAME = "Mocha"
HARDCODED_BASE_URL = "https://mocha.my"

# Version — read from the VERSION file in the repo root at runtime.
# Falls back to the hardcoded string only if the file cannot be found
# (e.g. inside a frozen PyInstaller bundle that didn't bundle VERSION).
import os as _os

def _read_version() -> str:
    # Walk up from this file's directory to find VERSION in the repo root
    here = _os.path.dirname(_os.path.abspath(__file__))
    for _ in range(4):  # check up to 4 levels up
        candidate = _os.path.join(here, "VERSION")
        if _os.path.isfile(candidate):
            v = open(candidate).read().strip()
            return v if v else "v0.0.0"
        here = _os.path.dirname(here)
    return "v3.0.0"  # last-resort fallback

APP_VERSION = _read_version()
del _read_version, _os

# Auto-updater — points at the GitHub Releases API for this repo
UPDATE_CHECK_URL = "https://api.github.com/repos/nxllxvxxd2/Mocha-Tools/releases/latest"