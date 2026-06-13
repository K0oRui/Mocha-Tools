"""
updater.py — Auto-update support for Mocha Tools

Flow:
  1. UpdateCheckWorker runs on startup (background thread).
     It hits the GitHub Releases API and compares the latest tag to APP_VERSION.
     If a newer version exists it emits update_available(tag, url, release_notes).

  2. When the user clicks "Update Now", UpdateDownloadWorker downloads
     the correct asset for the running platform, replaces the current
     executable (or .app on macOS), and emits done() so the UI can
     prompt a restart.

Asset naming convention (must match build.yml):
  Windows : MochaTools-Setup-<version>.exe   e.g. MochaTools-Setup-3.0.1.exe
  Ubuntu  : debian_ubuntu-<version>.zip      e.g. debian_ubuntu-3.0.1.zip
  macOS   : macos-<arch>-<version>.zip       e.g. macos-arm64-3.0.1.zip
              arch is one of: x86_64 | arm64 | universal
"""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from packaging.version import Version

import requests
from PyQt6.QtCore import QThread, pyqtSignal

from .constants import APP_VERSION, UPDATE_CHECK_URL


# ── helpers ──────────────────────────────────────────────────────────────────

def _current_exe() -> str:
    """Return the path to the running executable (or .app bundle on macOS)."""
    if getattr(sys, "frozen", False):
        exe = sys.executable
        if platform.system() == "Darwin":
            contents = os.path.dirname(os.path.dirname(exe))
            bundle   = os.path.dirname(contents)
            if bundle.endswith(".app"):
                return bundle
        return exe
    return ""


def _current_exe_override() -> str:
    """
    Returns a fake frozen install path for --test-update when running from source.
    Creates a dummy onefile layout under the system temp dir so the batch script
    has a real file to back up and replace.

    Layout created:
      <tmp>/mochatools_test_install/
          Mocha Tools.exe     ← placeholder — will be overwritten by the update
    """
    dummy_dir = os.path.join(tempfile.gettempdir(), "mochatools_test_install")
    dummy_exe = os.path.join(dummy_dir, "Mocha Tools.exe")
    os.makedirs(dummy_dir, exist_ok=True)
    # Only create if it doesn't exist — don't overwrite if a previous test run
    # already placed a real binary here (e.g. from a successful update test).
    if not os.path.exists(dummy_exe):
        with open(dummy_exe, "w") as fh:
            fh.write("placeholder - old version")
    return dummy_exe


def _asset_prefix() -> str:
    """
    Return the platform-specific filename prefix used in GitHub release assets.
    The full asset name is  <prefix>-<tag>.zip  e.g. windows-v3.0.1.zip
    """
    system = platform.system()
    if system == "Windows":
        return "windows"
    if system == "Darwin":
        machine = platform.machine().lower()
        if machine == "arm64":
            return "macos-arm64"
        if machine == "x86_64":
            return "macos-x86_64"
        return "macos-universal"
    return "debian_ubuntu"


def _asset_name(tag: str) -> str:
    """Build the full expected asset filename for this platform and release tag."""
    # Guard: if tag is not a string (e.g. accidentally passed a QObject), coerce it
    tag = str(tag).strip() if tag else ""
    if not tag:
        raise ValueError("_asset_name() called with an empty tag")

    if platform.system() == "Windows":
        # Windows installer asset is named with the version number WITHOUT
        # the leading 'v' (matches build.yml's ${{ env.VERSION }}), e.g.
        # tag "v3.0.1" -> "MochaTools-Setup-3.0.1.exe"
        version = tag.lstrip("v")
        return f"MochaTools-Setup-{version}.exe"

    return f"{_asset_prefix()}-{tag}.zip"


def _is_newer(latest: str, current: str) -> bool:
    try:
        return Version(latest.lstrip("v")) > Version(current.lstrip("v"))
    except Exception:
        return latest != current


def _ensure_admin_windows() -> bool:
    """
    On Windows, re-launch the current process with UAC elevation if we are not
    already running as administrator.  Returns True if already elevated (caller
    should proceed), False if we requested elevation and the caller should exit
    (the elevated copy will take over).
    """
    try:
        is_admin = ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        is_admin = False

    if is_admin:
        return True  # already elevated — proceed normally

    # Re-launch with 'runas' verb (triggers UAC prompt)
    params = " ".join(f'"{a}"' for a in sys.argv)
    try:
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, None, 1
        )
    except Exception:
        pass
    return False  # caller should sys.exit() or abort


# ── Update check ─────────────────────────────────────────────────────────────

class UpdateCheckWorker(QThread):
    """Checks GitHub Releases API; emits update_available if a newer tag exists."""

    update_available = pyqtSignal(str, str, str)   # (tag, download_url, release_notes)
    up_to_date       = pyqtSignal()
    error            = pyqtSignal(str)

    def run(self):
        try:
            resp = requests.get(
                UPDATE_CHECK_URL,
                headers={"Accept": "application/vnd.github+json"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.Timeout:
            self.error.emit("Connection to GitHub timed out. Check your network and try again.")
            return
        except requests.exceptions.ConnectionError:
            self.error.emit("Could not reach GitHub. Check your internet connection.")
            return
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            self.error.emit(f"GitHub returned an error (HTTP {code}). Try again later.")
            return
        except Exception as e:
            self.error.emit(f"Update check failed: {e}")
            return

        latest_tag   = data.get("tag_name", "")
        release_body = data.get("body", "")
        assets       = data.get("assets", [])

        if not _is_newer(latest_tag, APP_VERSION):
            self.up_to_date.emit()
            return

        # Validate tag before building asset name
        if not latest_tag or not isinstance(latest_tag, str):
            self.error.emit("Update check returned an invalid release tag.")
            return

        try:
            want = _asset_name(latest_tag)
        except ValueError as e:
            self.error.emit(str(e))
            return

        url = next(
            (a["browser_download_url"] for a in assets if a["name"] == want),
            "",
        )
        self.update_available.emit(latest_tag, url, release_body)


# ── Download & install ───────────────────────────────────────────────────────

def launch_update_batch(bat_path: str, test_mode: bool = False) -> None:
    """
    Launch a previously-prepared update batch script (see
    UpdateDownloadWorker.ready_to_restart). The batch script will force-kill
    this process and relaunch the updated exe.
    """
    if not bat_path or not os.path.exists(bat_path):
        return

    # DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP ensures the batch is NOT a
    # child of this process.  Without this, `taskkill /F /T` in the batch kills
    # the batch script itself before it can copy the new exe and relaunch.
    DETACHED_PROCESS         = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

    subprocess.Popen(
        ["cmd.exe", "/C", bat_path],
        creationflags=flags,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class UpdateDownloadWorker(QThread):
    """Downloads the update asset and replaces the running binary."""

    progress = pyqtSignal(int)          # 0–100
    status   = pyqtSignal(str)          # human-readable status text
    done     = pyqtSignal()             # update installed; caller should prompt restart
    ready_to_restart = pyqtSignal(str)  # (windows only) batch script path, ready to launch on restart
    error    = pyqtSignal(str)

    def __init__(self, download_url: str, tag: str = "", parent=None):
        super().__init__(parent)
        self.download_url = str(download_url).strip()
        # Sanitise tag — must be a plain version string, never an object repr
        raw_tag = str(tag).strip() if tag else ""
        # If it looks like an object repr, discard it
        if "<" in raw_tag or "object at" in raw_tag:
            raw_tag = ""
        self.tag = raw_tag

    def run(self):
        try:
            self._download_and_install()
        except Exception as e:
            self.error.emit(str(e))

    def _download_and_install(self):
        system     = platform.system()
        _test_mode = "--test-update" in sys.argv and not getattr(sys, "frozen", False)
        target     = _current_exe_override() if _test_mode else _current_exe()
        if not target:
            self.error.emit(
                "Cannot auto-update when running from source. "
                "Pull the latest code manually."
            )
            return
        if _test_mode:
            self.status.emit(f"[TEST] Fake install dir: {os.path.dirname(target)}")

        # On Windows, ensure we have write permission (UAC elevation if needed).
        # Probe the install directory rather than the target exe itself — the
        # exe may be locked by the OS even though the directory is writable.
        if system == "Windows":
            try:
                probe = os.path.join(os.path.dirname(target), ".mocha_write_test")
                with open(probe, "w") as fh:
                    fh.write("ok")
                os.remove(probe)
            except PermissionError:
                # We don't have write access — request elevation and bail
                elevated = _ensure_admin_windows()
                if not elevated:
                    self.error.emit(
                        "Administrator privileges are required to install the update.\n"
                        "The app will re-launch with elevated permissions."
                    )
                    return
                # If we somehow are elevated but still can't write, report it
                self.error.emit(
                    "Cannot write to the installation directory even as administrator.\n"
                    "Try running the updater manually."
                )
                return

        # Build asset filename
        try:
            if self.tag:
                asset_name = _asset_name(self.tag)
            elif platform.system() == "Windows":
                asset_name = "MochaTools-Setup-update.exe"
            else:
                asset_name = f"{_asset_prefix()}-update.zip"
        except ValueError as exc:
            self.error.emit(str(exc))
            return

        # ── Download ──────────────────────────────────────────────────────────
        self.status.emit("Downloading update…")
        try:
            resp = requests.get(self.download_url, stream=True, timeout=120)
            resp.raise_for_status()
        except requests.exceptions.Timeout:
            self.error.emit("Download timed out. Check your connection and try again.")
            return
        except requests.exceptions.ConnectionError:
            self.error.emit("Could not reach the download server. Check your internet connection.")
            return
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            self.error.emit(f"Download failed: server returned HTTP {code}.")
            return
        except Exception as e:
            self.error.emit(f"Download failed: {e}")
            return

        total   = int(resp.headers.get("content-length", 0))
        fetched = 0
        tmp_dir = tempfile.mkdtemp(prefix="mochatools_update_")
        tmp_asset = os.path.join(tmp_dir, asset_name)

        try:
            with open(tmp_asset, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        fh.write(chunk)
                        fetched += len(chunk)
                        if total:
                            self.progress.emit(int(fetched / total * 90))
        except Exception as e:
            self.error.emit(f"Failed writing download: {e}")
            return

        # ── Install ───────────────────────────────────────────────────────────
        self.status.emit("Installing…")
        self.progress.emit(92)

        if system == "Windows":
            bat = self._install_windows(tmp_asset, target, tmp_dir)
            self.progress.emit(100)
            self.status.emit("Update ready. Restart to apply.")
            self.ready_to_restart.emit(bat)
            return
        elif system == "Darwin":
            self._install_macos(tmp_asset, target, tmp_dir)
        else:
            self._install_linux(tmp_asset, target, tmp_dir)

        self.progress.emit(100)
        self.status.emit("Update installed. Restart to apply.")
        self.done.emit()

    # ── Platform installers ──────────────────────────────────────────────────

    def _install_windows(self, installer_path: str, target: str, tmp_dir: str):
        """
        The downloaded asset IS the NSIS setup installer (MochaTools-Setup-x.x.x.exe)
        — no zip, no extraction needed. We just need to wait for our process to
        exit, then launch the installer.
        """
        if not os.path.exists(installer_path):
            raise RuntimeError(f"Downloaded installer not found: {installer_path}")

        bat        = os.path.join(tmp_dir, "update.bat")
        _test_mode = "--test-update" in sys.argv and not getattr(sys, "frozen", False)
        log        = os.path.join(os.path.dirname(target), "update.log")

        lines = [
            "@echo off",
            f'set "LOG={log}"',
            "setlocal",
            f'call :log "=== Mocha Tools updater started ==="',
            "",
            f'call :log "App already exited, proceeding..."',
            "",
            f'call :log "Waiting 3s for file locks to clear..."',
            "timeout /t 3 /nobreak >NUL",
            "",
            f'call :log "Launching installer: {installer_path}"',
            *([] if _test_mode else [
                f'start "" "{installer_path}"',
            ]),
            "",
            f'call :log "Done."',
            "goto end",
            "",
            ":fail",
            f'call :log "FAILED - see %LOG%"',
            "goto end",
            "",
            ":log",
            r'echo %~1',
            r'echo %~1 >>"%LOG%"',
            "exit /b",
            "",
            ":end",
            "endlocal",
        ]

        script = "\r\n".join(lines) + "\r\n"
        with open(bat, "w", newline="", encoding="utf-8") as fh:
            fh.write(script)

        # Don't launch yet — the batch script will be spawned when the user
        # clicks "Restart".
        return bat

    def _install_macos(self, zip_path: str, target: str, tmp_dir: str):
        extract_dir = os.path.join(tmp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)

        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        new_app = next(
            (os.path.join(extract_dir, e)
             for e in os.listdir(extract_dir) if e.endswith(".app")),
            None,
        )
        if not new_app:
            raise RuntimeError("No .app bundle found inside the downloaded zip.")

        backup = target + ".bak"
        if os.path.exists(backup):
            shutil.rmtree(backup)
        shutil.move(target, backup)
        shutil.move(new_app, target)

        subprocess.run(
            ["xattr", "-dr", "com.apple.quarantine", target],
            check=False,
        )

    def _install_linux(self, zip_path: str, target: str, tmp_dir: str):
        extract_dir = os.path.join(tmp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)

        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        candidates = [
            os.path.join(extract_dir, f)
            for f in os.listdir(extract_dir)
            if not f.endswith((".zip", ".sh", ".txt", ".md"))
        ]
        if not candidates:
            candidates = [os.path.join(extract_dir, f) for f in os.listdir(extract_dir)]
        if not candidates:
            raise RuntimeError("No binary found inside the downloaded zip.")

        new_bin = candidates[0]
        # Make the new binary executable before moving it into place
        os.chmod(new_bin, os.stat(new_bin).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        backup = target + ".bak"
        install_dir = os.path.dirname(target)

        # Probe whether we can write to the install directory directly
        try:
            probe = os.path.join(install_dir, ".mocha_write_test")
            with open(probe, "w") as fh:
                fh.write("ok")
            os.remove(probe)
            needs_elevation = False
        except PermissionError:
            needs_elevation = True

        if needs_elevation:
            # Prefer pkexec (graphical polkit prompt); fall back to sudo
            # Build a small shell snippet: backup old, move new, chmod
            shell_cmd = (
                f'cp -f "{target}" "{backup}" && '
                f'mv -f "{new_bin}" "{target}" && '
                f'chmod 755 "{target}"'
            )
            elevated = False
            for elevator in ("pkexec", "sudo"):
                if shutil.which(elevator):
                    result = subprocess.run(
                        [elevator, "sh", "-c", shell_cmd],
                        timeout=60,
                    )
                    if result.returncode == 0:
                        elevated = True
                        break
            if not elevated:
                raise RuntimeError(
                    "Cannot write to the installation directory.\n"
                    "Neither pkexec nor sudo succeeded. "
                    "Try running the updater with elevated permissions."
                )
        else:
            if os.path.exists(backup):
                os.remove(backup)
            shutil.copy2(target, backup)
            shutil.move(new_bin, target)
            os.chmod(target, os.stat(target).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)