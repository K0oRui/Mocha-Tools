#!/usr/bin/env python3
"""build.py — unified build orchestrator for Mocha Tools.

Reads the version + changes from apps/desktop/VERSION (YAML), stamps them into
the app and installer manifests, compiles with Nuitka, and packages the
portable and/or installer artifacts for the target platform.

Usage:
    python build.py --platform windows --variant portable --version 7.1.0
    python build.py --platform linux   --variant all       --version 7.1.0
    python build.py --platform macos   --variant installer --version 7.1.0
    python build.py --stamp-only
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import yaml

APP_ROOT = Path(__file__).resolve().parent.parent
DIST = APP_ROOT / "dist"
SRC = APP_ROOT / "src"
VERSION_FILE = APP_ROOT / "VERSION"

COMPANY_NAME = "nxllxvxxd2"
PRODUCT_NAME = "Mocha Tools"

NUITKA_COMMON = [
    "--assume-yes-for-downloads",
    "--enable-plugin=pyside6",
    "--include-package=keyring",
    "--include-package-data=keyring",
    "--noinclude-pytest-mode=nofollow",
]

NUITKA_STALE_DIRS = (
    "mochatools.build",
    "mochatools.dist",
    "mochatools.onefile-build",
)


def read_version_file() -> tuple[str, list[dict[str, str]]]:
    """Read version + changes from the VERSION YAML file."""
    if not VERSION_FILE.exists():
        return "0.0.0", []
    with VERSION_FILE.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    version = str(data.get("version", "0.0.0"))
    raw_changes = data.get("changes", []) or []
    changes: list[dict[str, str]] = [
        {
            "subject": str(c.get("subject", "")),
            "description": str(c.get("description", "")),
        }
        for c in raw_changes
        if isinstance(c, dict)
    ]
    return version, changes


def _py_string(value: str) -> str:
    """Quote a string the way ruff's formatter does.

    Double quotes by default; single quotes only when the string contains
    more double quotes than single quotes.
    """
    quote = "'" if value.count('"') > value.count("'") else '"'
    escaped = (
        value.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
        .replace(quote, "\\" + quote)
    )
    return f"{quote}{escaped}{quote}"


def _format_change(change: dict[str, str]) -> str:
    """Render one changelog entry in ruff's canonical style."""
    return (
        "    {\n"
        f'        "subject": {_py_string(change["subject"])},\n'
        f'        "description": {_py_string(change["description"])},\n'
        "    },"
    )


def _stamp_constants(version: str, changes: list[dict[str, str]]) -> None:
    """Rewrite APP_VERSION and APP_CHANGES in src/constants.py."""
    constants = SRC / "constants.py"
    text = constants.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("APP_VERSION"):
            out.append(f'APP_VERSION = "{version}"')
            i += 1
            continue
        if line.startswith("APP_CHANGES"):
            if changes:
                out.append("APP_CHANGES: list[dict[str, str]] = [")
                out.extend(_format_change(change) for change in changes)
                out.append("]")
            else:
                out.append("APP_CHANGES: list[dict[str, str]] = []")
            depth = line.count("[") - line.count("]")
            i += 1
            while i < len(lines) and depth > 0:
                depth += lines[i].count("[") - lines[i].count("]")
                i += 1
            continue
        out.append(line)
        i += 1
    constants.write_text("\n".join(out) + "\n", encoding="utf-8")


def _stamp_installer_sh(version: str) -> None:
    installer_sh = APP_ROOT / "installer.sh"
    if not installer_sh.exists():
        return
    text = installer_sh.read_text(encoding="utf-8")
    text = re.sub(
        r'^(APP_VERSION=")v?[\d.]+(")',
        rf"\g<1>v{version}\2",
        text,
        flags=re.MULTILINE,
    )
    installer_sh.write_text(text, encoding="utf-8")


def stamp_version(version: str, changes: list[dict[str, str]]) -> None:
    """Stamp version + changes into every manifest that needs them."""
    _stamp_constants(version, changes)
    _stamp_installer_sh(version)


def _clean_stale() -> None:
    for name in NUITKA_STALE_DIRS:
        path = DIST / name
        if path.exists():
            shutil.rmtree(path)


def _run_nuitka(jobs: int, extra: list[str]) -> Path:
    """Run Nuitka and return the produced binary/bundle path."""
    _clean_stale()
    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        f"--jobs={jobs}",
        *NUITKA_COMMON,
        *extra,
        f"--output-dir={DIST}",
        str(APP_ROOT / "mochatools.py"),
    ]
    subprocess.run(cmd, check=True)
    return DIST


def compile_windows(version: str, jobs: int) -> Path:
    """Compile the Windows onefile executable."""
    icon = APP_ROOT / "build" / "windows" / "icon.ico"
    _run_nuitka(
        jobs,
        [
            "--onefile",
            "--windows-console-mode=disable",
            f"--windows-icon-from-ico={icon}",
            f"--include-data-files={icon}=icon.ico",
            f"--company-name={COMPANY_NAME}",
            f"--product-name={PRODUCT_NAME}",
            f"--file-version={version}",
            f"--product-version={version}",
            "--output-filename=Mocha Tools.exe",
        ],
    )
    return DIST / "Mocha Tools.exe"


def compile_linux(jobs: int) -> Path:
    """Compile the Linux onefile executable."""
    icon = APP_ROOT / "build" / "debian_ubuntu" / "icon.png"
    _run_nuitka(
        jobs,
        [
            "--onefile",
            f"--linux-app-icon={icon}",
            "--output-filename=Mocha Tools",
        ],
    )
    return DIST / "Mocha Tools"


def compile_macos(version: str, jobs: int) -> Path:
    """Compile the macOS .app bundle."""
    icon = APP_ROOT / "build" / "macos" / "icon.icns"
    _run_nuitka(
        jobs,
        [
            "--mode=app",
            f"--macos-app-icon={icon}",
            f"--macos-app-name={PRODUCT_NAME}",
            f"--macos-app-version={version}",
            "--macos-signed-app-name=com.mocha.tools",
            "--macos-app-macos-min-version=11.0",
            "--macos-app-category-type=public.app-category.utilities",
        ],
    )
    bundles = sorted(
        DIST.glob("*.app"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not bundles:
        print("ERROR: expected .app bundle not found", file=sys.stderr)
        sys.exit(1)
    return bundles[0]


def _import_packaging(module: str) -> Any:
    sys.path.insert(0, str(APP_ROOT / "build"))
    return __import__(f"packaging.{module}", fromlist=["build"])


def build_windows(variant: str, version: str, jobs: int) -> None:
    binary = compile_windows(version, jobs)
    if variant == "portable":
        target = DIST / f"MochaTools-Portable-{version}.exe"
        shutil.copy(binary, target)
        _verify(target)
    else:
        module = _import_packaging("windows_custom")
        target = module.build(version, APP_ROOT, DIST)
        _verify(target)


def build_linux(variant: str, version: str, jobs: int) -> None:
    binary = compile_linux(jobs)
    if variant == "portable":
        module = _import_packaging("linux_tarball")
        target = module.build(version, APP_ROOT, DIST, binary)
        _verify(target)
        appimage = _import_packaging("linux_appimage")
        target = appimage.build(version, APP_ROOT, DIST, binary)
        _verify(target)
    else:
        module = _import_packaging("linux_deb")
        target = module.build(version, APP_ROOT, DIST, binary)
        _verify(target)
        module = _import_packaging("linux_rpm")
        target = module.build(version, APP_ROOT, DIST, binary)
        _verify(target)


def build_macos(variant: str, version: str, jobs: int) -> None:
    bundle = compile_macos(version, jobs)
    arch = "arm64" if sys.platform == "darwin" and _is_arm64() else "x86_64"
    if variant == "portable":
        module = _import_packaging("macos_dmg")
        target = module.build(version, DIST, bundle, arch)
        _verify(target)
    else:
        module = _import_packaging("macos_pkg")
        target = module.build(version, DIST, bundle, arch)
        _verify(target)


def _is_arm64() -> bool:
    import platform

    return platform.machine().lower() in ("arm64", "aarch64")


def _verify(path: Path) -> None:
    if not path.exists():
        print(f"ERROR: expected artifact not found: {path}", file=sys.stderr)
        sys.exit(1)
    size = path.stat().st_size
    print(f"  ✓ {path.name} ({size / 1024 / 1024:.1f} MB)")


def main() -> None:
    cast("Any", sys.stdout).reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--platform",
        choices=["windows", "linux", "macos"],
        help="Target platform (required unless --stamp-only)",
    )
    parser.add_argument(
        "--variant",
        choices=["portable", "installer", "all"],
        help="Artifact variant (required unless --stamp-only)",
    )
    parser.add_argument(
        "--version",
        help="Override the version from the VERSION file",
    )
    parser.add_argument(
        "--stamp-only",
        action="store_true",
        help="Only stamp version + changes into manifests, then exit",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=4,
        help="Nuitka compile jobs (default: 4)",
    )
    args = parser.parse_args()

    version, changes = read_version_file()
    version = args.version or version

    if args.stamp_only:
        stamp_version(version, changes)
        print(f"Stamped version {version} with {len(changes)} changes")
        return

    if not args.platform or not args.variant:
        parser.error("--platform and --variant are required unless --stamp-only")

    stamp_version(version, changes)
    print(f"Building {PRODUCT_NAME} {version} for {args.platform} ({args.variant})")

    variants = ["portable", "installer"] if args.variant == "all" else [args.variant]
    for variant in variants:
        if args.platform == "windows":
            build_windows(variant, version, args.jobs)
        elif args.platform == "linux":
            build_linux(variant, version, args.jobs)
        else:
            build_macos(variant, version, args.jobs)


if __name__ == "__main__":
    main()
