"""macos_pkg.py — build the macOS .pkg installer via pkgbuild."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def build(version: str, dist: Path, bundle: Path, arch: str) -> Path:
    """Package the .app bundle into an installer .pkg."""
    target = dist / f"MochaTools-{version}-macOS-{arch}.pkg"
    subprocess.run(
        [
            "pkgbuild",
            "--component",
            str(bundle),
            "--install-location",
            "/Applications",
            "--version",
            version,
            str(target),
        ],
        check=True,
    )
    return target
