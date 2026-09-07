"""payload.py — extract the embedded app payload from the installer exe."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_MARKER = b"MOCHATOOLS_PAYLOAD_V1\n"
_TRAILER = b"MOCHATOOLS_END"


def _self_exe() -> Path:
    """Return the path of the running installer exe (works under Nuitka onefile)."""
    parent = os.environ.get("NUITKA_ONEFILE_PARENT")
    if parent:
        return Path(parent)
    return Path(sys.argv[0]).resolve()


class PayloadError(RuntimeError):
    """Raised when the installer payload is missing or malformed."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"Installer payload not found ({reason})")


def extract_payload(dest_dir: Path) -> None:
    """Extract the embedded app files into dest_dir."""
    data = _self_exe().read_bytes()
    if not data.endswith(_TRAILER):
        raise PayloadError("trailer missing")  # noqa: TRY003
    body = data[: -len(_TRAILER)]
    idx = body.rfind(_MARKER)
    if idx < 0:
        raise PayloadError("marker missing")  # noqa: TRY003
    manifest_json = body[idx + len(_MARKER) :].split(b"\n", 1)[0]
    manifest = json.loads(manifest_json)
    payload_start = idx + len(_MARKER) + len(manifest_json) + 1
    dest_dir.mkdir(parents=True, exist_ok=True)
    for entry in manifest["files"]:
        start = payload_start + entry["offset"]
        end = start + entry["size"]
        (dest_dir / entry["name"]).write_bytes(data[start:end])
