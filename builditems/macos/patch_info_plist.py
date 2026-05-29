"""
patch_info_plist.py -- stamps the current version into builditems/macos/Info.plist

Called by build.yml before PyInstaller runs on macOS, and before the DMG is built:
    python3 builditems/macos/patch_info_plist.py <bare_version>
    e.g.    python3 builditems/macos/patch_info_plist.py 3.0.0
"""
import re
import sys
from pathlib import Path

def patch(bare: str):
    plist = Path("builditems/macos/Info.plist")
    if not plist.exists():
        print(f"ERROR: {plist} not found", file=sys.stderr)
        sys.exit(1)

    content = plist.read_text(encoding="utf-8")

    # Stamp CFBundleShortVersionString (user-visible version, e.g. "3.0.0")
    content = re.sub(
        r'(<key>CFBundleShortVersionString</key>\s*<string>)[^<]*(</string>)',
        rf'\g<1>{bare}\g<2>',
        content,
    )

    # Stamp CFBundleVersion (build version, same value)
    content = re.sub(
        r'(<key>CFBundleVersion</key>\s*<string>)[^<]*(</string>)',
        rf'\g<1>{bare}\g<2>',
        content,
    )

    plist.write_text(content, encoding="utf-8")
    print(f"  Info.plist: CFBundleShortVersionString = {bare}")
    print(f"  Info.plist: CFBundleVersion            = {bare}")
    print("Done.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: patch_info_plist.py <bare_version>  e.g. 3.0.0")
        sys.exit(1)
    patch(sys.argv[1].lstrip("v"))