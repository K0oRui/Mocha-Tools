"""macos_dmg.py — build the portable macOS DMG via hdiutil."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def build(version: str, dist: Path, bundle: Path, arch: str) -> Path:
    """Compress the .app bundle into a read-only DMG."""
    target = dist / f"MochaTools-{version}-macOS-{arch}.dmg"
    subprocess.run(
        [
            "hdiutil",
            "create",
            "-volname",
            "Mocha Tools",
            "-srcfolder",
            str(bundle),
            "-ov",
            "-format",
            "UDZO",
            str(target),
        ],
        check=True,
    )
    return target
