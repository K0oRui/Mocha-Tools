"""macos_dmg.py — build the portable macOS DMG via hdiutil."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def build(version: str, dist: Path, bundle: Path, arch: str) -> Path:
    """Compress the .app bundle into a read-only DMG with an /Applications
    shortcut so users can drag the app into place."""
    target = dist / f"MochaTools-{version}-macOS-{arch}.dmg"
    staging = Path(tempfile.mkdtemp(prefix="mochatools_dmg_"))
    try:
        shutil.copytree(bundle, staging / bundle.name, symlinks=True)
        (staging / "Applications").symlink_to("/Applications")
        subprocess.run(
            [
                "hdiutil",
                "create",
                "-volname",
                "Mocha Tools",
                "-srcfolder",
                str(staging),
                "-ov",
                "-format",
                "UDZO",
                str(target),
            ],
            check=True,
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return target
