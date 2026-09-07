"""windows_custom.py — build the fully custom PySide6 installer.

Compiles the PySide6 installer app (apps/desktop/installer) with Nuitka into
a onefile exe, then appends the app binary + icon as a payload so the
installer is fully self-contained.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_MARKER = b"MOCHATOOLS_PAYLOAD_V1\n"
_TRAILER = b"MOCHATOOLS_END"

_NUITKA_COMMON = [
    "--assume-yes-for-downloads",
    "--disable-cache=dll-dependencies",
    "--enable-plugin=pyside6",
    "--noinclude-pytest-mode=nofollow",
]


def _generate_meta(app_root: Path, version: str) -> None:
    """Regenerate installer/_meta.py with the version + LICENSE text."""
    license_file = app_root.parent.parent / "LICENSE"
    text = license_file.read_text(encoding="utf-8")
    installer_dir = app_root / "installer"
    (installer_dir / "_meta.py").write_text(
        f'VERSION = "{version}"\nLICENSE_TEXT = {text!r}\n',
        encoding="utf-8",
    )


def _append_payload(setup_exe: Path, files: list[Path]) -> None:
    manifest: list[dict[str, object]] = []
    offset = 0
    blobs: list[bytes] = []
    for f in files:
        data = f.read_bytes()
        manifest.append({"name": f.name, "offset": offset, "size": len(data)})
        offset += len(data)
        blobs.append(data)
    manifest_json = json.dumps({"files": manifest}).encode("utf-8")
    with setup_exe.open("ab") as fh:
        fh.write(_MARKER)
        fh.write(manifest_json)
        fh.write(b"\n")
        for blob in blobs:
            fh.write(blob)
        fh.write(_TRAILER)


def build(version: str, app_root: Path, dist: Path) -> Path:
    """Compile the custom installer and return the produced exe in dist."""
    app_exe = dist / "Mocha Tools.exe"
    if not app_exe.exists():
        msg = "Mocha Tools.exe not found in dist — build the app first"
        raise FileNotFoundError(msg)

    _generate_meta(app_root, version)

    icon = app_root / "build" / "windows" / "icon.ico"
    setup_exe = dist / f"MochaTools-Setup-{version}.exe"
    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        *_NUITKA_COMMON,
        "--onefile",
        "--windows-console-mode=disable",
        "--windows-uac-admin",
        f"--windows-icon-from-ico={icon}",
        "--company-name=nxllxvxxd2",
        "--product-name=Mocha Tools",
        f"--file-version={version}",
        f"--product-version={version}",
        f"--output-filename={setup_exe.name}",
        f"--output-dir={dist}",
        str(app_root / "installer" / "main.py"),
    ]
    subprocess.run(cmd, check=True)

    _append_payload(setup_exe, [app_exe, icon])
    return setup_exe
