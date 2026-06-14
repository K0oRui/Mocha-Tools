#!/usr/bin/env python3
"""
builditems/stamp_version.py
Called by build.yml before PyInstaller runs.
Rewrites the APP_VERSION line in mochatools_app/constants.py,
patches !define APP_VERSION in installer.nsi,
patches APP_VERSION in installer.sh, and generates
builditems/windows/version.txt for PyInstaller's --version-file.

Usage:
    python builditems/stamp_version.py v4.0.0
"""
import re
import sys
from pathlib import Path

def make_tuple(version: str) -> str:
    """Convert '4.1.3' -> '4, 1, 3, 0'"""
    parts = (version.lstrip("v").split(".") + ["0", "0", "0", "0"])[:4]
    return ", ".join(p.zfill(1) for p in parts)

def main():
    if len(sys.argv) != 2:
        print("Usage: stamp_version.py <version>", file=sys.stderr)
        sys.exit(1)

    version = sys.argv[1].strip().lstrip("v")
    if not version:
        print("Version string is empty", file=sys.stderr)
        sys.exit(1)

    root = Path(__file__).parent.parent

    # ── 1. constants.py ───────────────────────────────────────────────────────
    constants = root / "mochatools_app" / "constants.py"
    if not constants.exists():
        print(f"Not found: {constants}", file=sys.stderr)
        sys.exit(1)

    text = constants.read_text(encoding="utf-8")
    new_text, count = re.subn(
        r'^APP_VERSION\s*=\s*"[^"]*"',
        f'APP_VERSION = "{version}"',
        text,
        flags=re.MULTILINE,
    )
    if count == 0:
        print("ERROR: APP_VERSION line not found in constants.py", file=sys.stderr)
        sys.exit(1)
    constants.write_text(new_text, encoding="utf-8")
    print(f'Stamped APP_VERSION = "{version}" into {constants}')

    # ── 2. installer.nsi ─────────────────────────────────────────────────────
    nsi = root / "installer.nsi"
    if nsi.exists():
        nsi_text = nsi.read_text(encoding="utf-8")
        nsi_new, n = re.subn(
            r'(!define APP_VERSION\s+")([\d.]+)(")',
            rf'\g<1>{version}\3',
            nsi_text,
        )
        if n:
            nsi.write_text(nsi_new, encoding="utf-8")
            print(f'Patched APP_VERSION = "{version}" into {nsi}')
        else:
            print("WARNING: APP_VERSION not found in installer.nsi", file=sys.stderr)

    # ── 3. installer.sh ──────────────────────────────────────────────────────
    installer_sh = root / "installer.sh"
    if installer_sh.exists():
        sh_text = installer_sh.read_text(encoding="utf-8")
        sh_new, n = re.subn(
            r'^(APP_VERSION=")v?[\d.]+(")',
            rf'\g<1>v{version}\2',
            sh_text,
            flags=re.MULTILINE,
        )
        if n:
            installer_sh.write_text(sh_new, encoding="utf-8")
            print(f'Patched APP_VERSION = "v{version}" into {installer_sh}')
        else:
            print("WARNING: APP_VERSION not found in installer.sh", file=sys.stderr)

    # ── 4. builditems/windows/version.txt (generated for PyInstaller) ────────
    ver_tuple = make_tuple(version)
    ver_dir = root / "builditems" / "windows"
    ver_dir.mkdir(parents=True, exist_ok=True)
    ver_file = ver_dir / "version.txt"
    ver_file.write_text(f"""\
# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({ver_tuple}),
    prodvers=({ver_tuple}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          u'040904B0',
          [
            StringStruct(u'CompanyName',      u'nxllxvxxd2'),
            StringStruct(u'FileDescription',  u'Mocha Tools'),
            StringStruct(u'FileVersion',      u'{version}'),
            StringStruct(u'InternalName',     u'MochaTools'),
            StringStruct(u'LegalCopyright',   u'\\xa9 nxllxvxxd2'),
            StringStruct(u'OriginalFilename', u'Mocha Tools.exe'),
            StringStruct(u'ProductName',      u'Mocha Tools'),
            StringStruct(u'ProductVersion',   u'{version}'),
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct(u'Translation', [0x0409, 1200])])
  ]
)
""", encoding="utf-8")
    print(f"Generated {ver_file}")

if __name__ == "__main__":
    main()