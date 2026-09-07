"""install.py — install/uninstall logic for the Mocha Tools installer."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import winreg
from collections.abc import Callable
from pathlib import Path

from payload import extract_payload

APP_EXE = "Mocha Tools.exe"
ICON = "icon.ico"
UNINSTALLER = "uninstall.bat"
REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\MochaTools"

ProgressCb = Callable[[int, str], None]


def default_install_dir() -> str:
    pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    return str(Path(pf) / "Mocha Tools")


def install(
    install_dir: str,
    desktop_shortcut: bool,
    start_menu_shortcut: bool,
    version: str,
    progress_cb: ProgressCb | None = None,
) -> None:
    """Install Mocha Tools to install_dir and register it with Windows."""

    def report(pct: int, msg: str) -> None:
        if progress_cb:
            progress_cb(pct, msg)

    report(2, "Preparing...")
    payload_dir = Path(tempfile.mkdtemp(prefix="mocha_setup_"))
    try:
        extract_payload(payload_dir)
        report(15, "Extracted installer payload")

        _kill_running_app()
        report(20, "Stopped running instances")

        target = Path(install_dir)
        target.mkdir(parents=True, exist_ok=True)
        report(30, f"Created {target}")

        shutil.copy2(payload_dir / APP_EXE, target / APP_EXE)
        report(50, "Copied Mocha Tools.exe")
        shutil.copy2(payload_dir / ICON, target / ICON)
        report(60, "Copied icon")

        _write_uninstaller(target)
        report(70, "Wrote uninstaller")

        _write_registry(target, version)
        report(80, "Registered with Windows")

        if start_menu_shortcut:
            _create_shortcut(
                _shell_folder("Programs"),
                "Mocha Tools.lnk",
                target / APP_EXE,
                target / ICON,
            )
        if desktop_shortcut:
            _create_shortcut(
                _shell_folder("Desktop"),
                "Mocha Tools.lnk",
                target / APP_EXE,
                target / ICON,
            )
        report(95, "Created shortcuts")

        report(100, "Done")
    finally:
        shutil.rmtree(payload_dir, ignore_errors=True)


def _kill_running_app() -> None:
    subprocess.run(["taskkill", "/f", "/im", APP_EXE], capture_output=True)


def _write_uninstaller(target: Path) -> None:
    lines = [
        "@echo off",
        "setlocal",
        f'taskkill /f /im "{APP_EXE}" >nul 2>&1',
        "timeout /t 1 /nobreak >nul",
        f'reg delete "HKLM\\{REG_KEY}" /f >nul 2>&1',
        'for /f "delims=" %%D in (\'powershell -NoProfile -Command '
        '"[Environment]::GetFolderPath(\'Desktop\')"\') do set "DESKTOP=%%D"',
        'del /q "%DESKTOP%\\Mocha Tools.lnk" >nul 2>&1',
        'del /q "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Mocha Tools.lnk" >nul 2>&1',
        f'del /q "%~dp0{APP_EXE}" "%~dp0{ICON}" "%~dp0{UNINSTALLER}" >nul 2>&1',
        'set "INSTDIR=%~dp0"',
        '> "%TEMP%\\mocha_cleanup.bat" echo @echo off',
        '>> "%TEMP%\\mocha_cleanup.bat" echo timeout /t 1 /nobreak ^>nul',
        '>> "%TEMP%\\mocha_cleanup.bat" echo rmdir /s /q "%INSTDIR%"',
        '>> "%TEMP%\\mocha_cleanup.bat" echo del /q "%~f0"',
        'start "" /b "%TEMP%\\mocha_cleanup.bat"',
        "endlocal",
    ]
    (target / UNINSTALLER).write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")


def _write_registry(target: Path, version: str) -> None:
    with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, REG_KEY) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "Mocha Tools")
        winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, version)
        winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, "nxllxvxxd2")
        winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(target))
        winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, str(target / APP_EXE))
        winreg.SetValueEx(
            key,
            "UninstallString",
            0,
            winreg.REG_SZ,
            f'"{target / UNINSTALLER}"',
        )
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)


def _shell_folder(name: str) -> Path:
    ps = f"[Environment]::GetFolderPath('{name}')"
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True,
        text=True,
    )
    path = out.stdout.strip()
    return Path(path) if path else Path()


def _create_shortcut(folder: Path, name: str, target: Path, icon: Path) -> None:
    if not folder:
        return
    folder.mkdir(parents=True, exist_ok=True)
    lnk = folder / name

    def q(s: str | Path) -> str:
        return str(s).replace("'", "''")

    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{q(lnk)}'); "
        f"$s.TargetPath = '{q(target)}'; "
        f"$s.WorkingDirectory = '{q(target.parent)}'; "
        f"$s.IconLocation = '{q(icon)},0'; "
        "$s.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        check=True,
    )
