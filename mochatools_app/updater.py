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
  Windows : windows-<version>.zip         e.g. windows-v3.0.1.zip
  Ubuntu  : debian_ubuntu-<version>.zip   e.g. debian_ubuntu-v3.0.1.zip
  macOS   : macos-<arch>-<version>.zip    e.g. macos-arm64-v3.0.1.zip
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

class UpdateDownloadWorker(QThread):
    """Downloads the update asset and replaces the running binary."""

    progress = pyqtSignal(int)          # 0–100
    status   = pyqtSignal(str)          # human-readable status text
    done     = pyqtSignal()             # update installed; caller should prompt restart
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
        system = platform.system()
        target = _current_exe()
        if not target:
            self.error.emit(
                "Cannot auto-update when running from source. "
                "Pull the latest code manually."
            )
            return

        # On Windows, ensure we have write permission (UAC elevation if needed)
        if system == "Windows":
            try:
                # Try a quick write-permission probe on the target's directory
                test_file = target + ".write_test"
                with open(test_file, "w") as fh:
                    fh.write("ok")
                os.remove(test_file)
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
            asset_name = _asset_name(self.tag) if self.tag else (
                f"{_asset_prefix()}-update.zip"
            )
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
            self._install_windows(tmp_asset, target, tmp_dir)
        elif system == "Darwin":
            self._install_macos(tmp_asset, target, tmp_dir)
        else:
            self._install_linux(tmp_asset, target, tmp_dir)

        self.progress.emit(100)
        self.status.emit("Update installed. Restart to apply.")
        self.done.emit()

    # ── Platform installers ──────────────────────────────────────────────────

    def _install_windows(self, zip_path: str, target: str, tmp_dir: str):
        """
        Unzip the release archive, then use a batch script to swap the binary
        after this process exits (the running .exe is locked on Windows).
        """
        extract_dir = os.path.join(tmp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)
        except zipfile.BadZipFile as e:
            raise RuntimeError(f"Downloaded file is not a valid zip archive: {e}") from e

        new_exe = next(
            (os.path.join(extract_dir, f)
             for f in os.listdir(extract_dir) if f.lower().endswith(".exe")),
            None,
        )
        if not new_exe:
            raise RuntimeError("No .exe found inside the downloaded zip.")

        bat = os.path.join(tmp_dir, "update.bat")
        pid = os.getpid()
        script = (
            "@echo off\n"
            ":wait\n"
            f'tasklist /FI "PID eq {pid}" 2>NUL | find /I "{pid}" >NUL\n'
            "if not errorlevel 1 (\n"
            "    timeout /t 1 /nobreak >NUL\n"
            "    goto wait\n"
            ")\n"
            f'copy /Y "{new_exe}" "{target}"\n'
            f'start "" "{target}"\n'
        )
        with open(bat, "w") as fh:
            fh.write(script)

        subprocess.Popen(
            ["cmd.exe", "/C", bat],
            creationflags=subprocess.CREATE_NO_WINDOW,
            close_fds=True,
        )

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
        backup  = target + ".bak"
        if os.path.exists(backup):
            os.remove(backup)
        shutil.copy2(target, backup)
        shutil.move(new_bin, target)
        os.chmod(target, os.stat(target).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)