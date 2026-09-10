"""linux_common.py — shared helpers for Linux package builders."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

SUPPORTED_ARCHS = ("x86_64", "aarch64")

DEB_ARCH_MAP = {
    "x86_64": "amd64",
    "aarch64": "arm64",
}


DESKTOP_FILE = """\
[Desktop Entry]
Version=1.0
Type=Application
Name=Mocha Tools
GenericName=File Uploader
Comment=Upload files to Mocha
Exec=mochatools
Icon=mochatools
Terminal=false
Categories=Network;FileTransfer;Utility;
Keywords=mocha;upload;share;file;
StartupNotify=true
"""


def stage_package(app_root: Path, dist: Path, binary: Path) -> Path:
    """Stage the binary, desktop file, and icon for fpm packaging."""
    staging = dist / "pkg-staging"
    if staging.exists():
        shutil.rmtree(staging)

    (staging / "usr" / "bin").mkdir(parents=True)
    shutil.copy(binary, staging / "usr" / "bin" / "mochatools")

    (staging / "usr" / "share" / "applications").mkdir(parents=True)
    (staging / "usr" / "share" / "applications" / "mochatools.desktop").write_text(
        DESKTOP_FILE,
        encoding="utf-8",
    )

    icon_dir = staging / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
    icon_dir.mkdir(parents=True)
    shutil.copy(
        app_root / "build" / "debian_ubuntu" / "icon.png",
        icon_dir / "mochatools.png",
    )
    return staging
