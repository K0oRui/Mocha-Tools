#!/usr/bin/env python3
"""
builditems/stamp_version.py
Called by build.yml before PyInstaller runs.
Rewrites the APP_VERSION line in mochatools_app/constants.py.

Usage:
    python builditems/stamp_version.py v4.0.0
"""
import re
import sys
from pathlib import Path

def main():
    if len(sys.argv) != 2:
        print("Usage: stamp_version.py <version>", file=sys.stderr)
        sys.exit(1)

    version = sys.argv[1].strip()
    if not version:
        print("Version string is empty", file=sys.stderr)
        sys.exit(1)

    constants = Path(__file__).parent.parent / "mochatools_app" / "constants.py"
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
    print(f"Stamped APP_VERSION = \"{version}\" into {constants}")

if __name__ == "__main__":
    main()