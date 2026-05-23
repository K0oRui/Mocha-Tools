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