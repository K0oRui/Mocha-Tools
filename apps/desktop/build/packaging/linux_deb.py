"""linux_deb.py — build the .deb installer via fpm."""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from .linux_common import stage_package


def build(version: str, app_root: Path, dist: Path, binary: Path) -> Path:
    """Package the staged layout into a .deb."""
    staging = stage_package(app_root, dist, binary)
    target = dist / f"MochaTools-{version}-amd64.deb"
    subprocess.run(
        [
            "fpm",
            "-s",
            "dir",
            "-t",
            "deb",
            "-n",
            "mochatools",
            "-v",
            version,
            "--package",
            str(target),
            "-C",
            str(staging),
            ".",
        ],
        check=True,
    )
    shutil.rmtree(staging)
    return target
