#!/usr/bin/env python3
"""
build.py — Cross-platform build script for Mocha Tools

Usage:
    python build.py

Produces:
    Windows : dist/windows-<version>.zip        (contains the .exe)
    macOS   : dist/macos-<arch>-<version>.zip   (contains Mocha Tools.app)
    Linux   : dist/debian_ubuntu-<version>.zip  (contains the binary)

Why --onedir on macOS?
    PyQt6 on macOS requires Qt frameworks and plugins to sit in a fixed
    relative directory structure next to the binary.  --onefile breaks
    that by extracting everything to a random temp path at launch, which
    causes a segmentation fault before the event loop even starts.
    --onedir (packaged as a .app bundle) keeps the structure intact.
    Windows and Linux do not have this constraint so --onefile is fine.
"""

import os
import platform
import shutil
import subprocess
import sys
import zipfile

# ── Read version ──────────────────────────────────────────────────────────────
VERSION_FILE = os.path.join(os.path.dirname(__file__), "VERSION")
if not os.path.isfile(VERSION_FILE):
    print("ERROR: VERSION file not found in repo root.")
    sys.exit(1)

VERSION = open(VERSION_FILE).read().strip()
print(f"Building Mocha Tools {VERSION}")

SYSTEM  = platform.system()
MACHINE = platform.machine().lower()   # x86_64 | arm64

# ── Shared PyInstaller flags ───────────────────────────────────────────────────
BASE_CMD = [
    sys.executable, "-m", "PyInstaller",
    "--noconsole",
    "--windowed",
    "--name", "Mocha Tools",
    "--add-data", f"VERSION{';' if SYSTEM == 'Windows' else ':'}.",
    "mochatools.py",
]

# ── Platform-specific flags ───────────────────────────────────────────────────
if SYSTEM == "Darwin":
    # --onedir is REQUIRED on macOS with PyQt6 — --onefile causes a segfault
    # because Qt cannot locate its plugin/framework paths from a temp dir.
    CMD = BASE_CMD + ["--onedir"]
    ARCH_TAG = "arm64" if MACHINE == "arm64" else "x86_64"
    ASSET_NAME = f"macos-{ARCH_TAG}-{VERSION}"
elif SYSTEM == "Windows":
    CMD = BASE_CMD + ["--onefile"]
    ASSET_NAME = f"windows-{VERSION}"
else:
    # Linux
    CMD = BASE_CMD + ["--onefile"]
    ASSET_NAME = f"debian_ubuntu-{VERSION}"

# ── Clean previous build ───────────────────────────────────────────────────────
for d in ("build", "dist"):
    if os.path.exists(d):
        shutil.rmtree(d)

# ── Run PyInstaller ───────────────────────────────────────────────────────────
print(f"Running: {' '.join(CMD)}")
result = subprocess.run(CMD)
if result.returncode != 0:
    print("PyInstaller failed.")
    sys.exit(result.returncode)

# ── Package into zip ──────────────────────────────────────────────────────────
os.makedirs("dist", exist_ok=True)
zip_path = os.path.join("dist", f"{ASSET_NAME}.zip")

if SYSTEM == "Darwin":
    # dist/ contains "Mocha Tools.app" (a directory bundle)
    app_bundle = os.path.join("dist", "Mocha Tools.app")
    if not os.path.exists(app_bundle):
        print(f"ERROR: expected .app bundle at {app_bundle}")
        sys.exit(1)
    print(f"Zipping {app_bundle} → {zip_path}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(app_bundle):
            for file in files:
                full = os.path.join(root, file)
                arcname = os.path.relpath(full, os.path.dirname(app_bundle))
                zf.write(full, arcname)

elif SYSTEM == "Windows":
    exe = os.path.join("dist", "Mocha Tools.exe")
    if not os.path.exists(exe):
        print(f"ERROR: expected exe at {exe}")
        sys.exit(1)
    print(f"Zipping {exe} → {zip_path}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(exe, "Mocha Tools.exe")

else:
    binary = os.path.join("dist", "Mocha Tools")
    if not os.path.exists(binary):
        print(f"ERROR: expected binary at {binary}")
        sys.exit(1)
    print(f"Zipping {binary} → {zip_path}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(binary, "Mocha Tools")

print(f"\nDone! Asset ready: {zip_path}")