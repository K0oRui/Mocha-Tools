"""linux_appimage.py — build the portable AppImage.

Requires appimagetool on PATH (installed in CI).
"""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

DESKTOP_FILE = """\
[Desktop Entry]
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

APPRUN = """\
#!/bin/sh
SELF=$(readlink -f "$0")
HERE=${SELF%/*}
export PATH="${HERE}/usr/bin/:${HERE}/usr/sbin/:${HERE}/usr/games/:${HERE}/bin/:${HERE}/sbin/:${PATH}"
export LD_LIBRARY_PATH="${HERE}/usr/lib/:${HERE}/usr/lib/x86_64-linux-gnu/:${HERE}/usr/lib64/:${HERE}/lib/:${HERE}/lib/x86_64-linux-gnu/:${HERE}/lib64/:${LD_LIBRARY_PATH}"
exec "${HERE}/usr/bin/mochatools" "$@"
"""


def build(version: str, app_root: Path, dist: Path, binary: Path, arch: str) -> Path:
    """Assemble an AppDir and run appimagetool."""
    appdir = dist / "AppDir"
    if appdir.exists():
        shutil.rmtree(appdir)
    (appdir / "usr" / "bin").mkdir(parents=True)

    shutil.copy(binary, appdir / "usr" / "bin" / "mochatools")
    shutil.copy(
        app_root / "build" / "debian_ubuntu" / "icon.png",
        appdir / "mochatools.png",
    )
    (appdir / "mochatools.desktop").write_text(DESKTOP_FILE, encoding="utf-8")
    apprun = appdir / "AppRun"
    apprun.write_text(APPRUN, encoding="utf-8")
    apprun.chmod(0o755)

    target = dist / f"MochaTools-{version}-{arch}.AppImage"
    subprocess.run(["appimagetool", str(appdir), str(target)], check=True)
    shutil.rmtree(appdir)
    return target
