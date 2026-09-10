"""linux_tarball.py — build the portable Linux tarball.

Layout (matches installer.sh's expectations):
    Mocha-Tools-linux
    installer.sh
    .mochatools-portable     ← portable marker (read by updater.py)
    build/debian_ubuntu/icon.png
"""

from __future__ import annotations

import shutil
import tarfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def build(version: str, app_root: Path, dist: Path, binary: Path, arch: str) -> Path:
    """Bundle the binary + installer.sh + icon into a gzipped tarball."""
    staging = dist / "linux-installer"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    shutil.copy(binary, staging / "Mocha-Tools-linux")
    shutil.copy(app_root / "installer.sh", staging / "installer.sh")
    (staging / ".mochatools-portable").write_text("portable\n", encoding="utf-8")

    icon_dir = staging / "build" / "debian_ubuntu"
    icon_dir.mkdir(parents=True)
    shutil.copy(
        app_root / "build" / "debian_ubuntu" / "icon.png",
        icon_dir / "icon.png",
    )

    target = dist / f"MochaTools-{version}-linux-{arch}.tar.gz"
    with tarfile.open(target, "w:gz") as tar:
        tar.add(staging, arcname=".")
    shutil.rmtree(staging)
    return target
