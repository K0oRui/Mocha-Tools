"""
patch_version.py -- stamps the current version into installer.nsi and version.txt

Called by build.yml:
    python3 builditems/windows/patch_version.py <bare_version>
    e.g.    python3 builditems/windows/patch_version.py 1.2.3
"""
import io
import re
import sys
from pathlib import Path

# Force UTF-8 stdout so Windows cp1252 console doesn't choke on any characters
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

def patch(bare: str):
    # -- installer.nsi --------------------------------------------------------
    nsi = Path("installer.nsi")
    if nsi.exists():
        content = nsi.read_text(encoding="utf-8")
        content = re.sub(
            r'!define APP_VERSION\s+"[^"]*"',
            f'!define APP_VERSION      "{bare}"',
            content,
        )
        nsi.write_text(content, encoding="utf-8")
        print(f"  installer.nsi   : APP_VERSION = {bare}")

    # -- builditems/windows/version.txt ---------------------------------------
    vtxt = Path("builditems/windows/version.txt")
    if vtxt.exists():
        parts = bare.split(".")
        # Pad to four parts: 1.2.3 -> (1, 2, 3, 0)
        while len(parts) < 4:
            parts.append("0")
        tuple_str = f"({', '.join(parts)})"

        content = vtxt.read_text(encoding="utf-8")
        content = re.sub(r"filevers=\([^)]*\)", f"filevers={tuple_str}", content)
        content = re.sub(r"prodvers=\([^)]*\)", f"prodvers={tuple_str}", content)
        content = re.sub(
            r"(StringStruct\(u'FileVersion',\s+u')[^']*(')",
            rf"\g<1>{bare}.0\g<2>",
            content,
        )
        content = re.sub(
            r"(StringStruct\(u'ProductVersion',\s+u')[^']*(')",
            rf"\g<1>{bare}.0\g<2>",
            content,
        )
        vtxt.write_text(content, encoding="utf-8")
        print(f"  version.txt     : {tuple_str} / {bare}.0")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: patch_version.py <bare_version>  e.g. 1.2.3")
        sys.exit(1)
    patch(sys.argv[1].lstrip("v"))
    print("Done.")