"""linux_rpm.py — build the .rpm installer via fpm."""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from .linux_common import stage_package


def build(version: str, app_root: Path, dist: Path, binary: Path, arch: str) -> Path:
    """Package the staged layout into a .rpm."""
    staging = stage_package(app_root, dist, binary)
    target = dist / f"MochaTools-{version}-{arch}.rpm"
    subprocess.run(
        [
            "fpm",
            "-s",
            "dir",
            "-t",
            "rpm",
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
