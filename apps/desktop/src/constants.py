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
APP_VERSION = "8.1.0"
APP_CHANGES: list[dict[str, str]] = [
    {
        "subject": "fix(build): filter changelog to desktop app changes",
        "description": "",
    },
    {
        "subject": "fix(build): stamp changelog entries in ruff-canonical format",
        "description": "The release pipeline wrote APP_CHANGES with repr() (single-quoted, one-line dicts), so the committed constants.py failed ruff format --check. Emit multi-line double-quoted entries matching ruff's formatter instead.",
    },
    {
        "subject": "fix(upload): mark upload inactive via _set_uploading on finish/error",
        "description": "",
    },
    {
        "subject": "feat(desktop): enforce single instance and focus existing window",
        "description": "The first instance listens on a named QLocalServer. Any later launch connects, asks the running instance to focus its window, and exits before creating a window. Stale servers are cleared and simultaneous-start races fall back to notifying the winner.",
    },
    {
        "subject": "ci: make changelog base ref configurable and skip redundant bumps",
        "description": "- changelog base ref as a manual workflow input\n- default changelog to the last version-bump commit\n- skip the version bump when the version is already bumped",
    },
]

UPDATE_CHECK_URL = "https://api.github.com/repos/nxllvxxd/Mocha-Tools/releases/latest"
