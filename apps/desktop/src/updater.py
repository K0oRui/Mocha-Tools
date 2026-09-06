"""updater.py — Auto-update support for Mocha Tools.

Flow:
  1. UpdateCheckWorker runs on startup (background thread).
     It hits the GitHub Releases API and compares the latest tag to APP_VERSION.
     If a newer version exists it emits update_available(tag, url, release_notes).

  2. When the user clicks "Update Now", UpdateDownloadWorker downloads
     the correct asset for the running platform.

     - Windows: downloads the NSIS installer exe and emits
       ready_to_restart(path) with a batch script that launches it
       after this process exits.
     - Linux: downloads the linux tarball and emits
       ready_to_restart(path) with a shell script that extracts the
       tarball and runs installer.sh in a terminal window after this
       process exits, so the user can respond to any sudo/password
       prompts interactively.
     - macOS: replaces the .app bundle in-place and emits done().

Asset naming convention (must match build.yml):
  Windows : MochaTools-Setup-<version>.exe      e.g. MochaTools-Setup-3.0.1.exe
  Linux   : MochaTools-<version>-linux.tar.gz   e.g. MochaTools-3.0.1-linux.tar.gz
  macOS   : macos-<arch>-<version>.zip          e.g. macos-arm64-3.0.1.zip
              arch is one of: x86_64 | arm64 | universal
"""

from __future__ import annotations

import contextlib
import ctypes
import pathlib
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

import requests
from packaging.version import Version
from PySide6.QtCore import QObject, QThread, Signal

from .constants import APP_VERSION, UPDATE_CHECK_URL
from .logging_utils import write_debug_log

# ── helpers ──────────────────────────────────────────────────────────────────


def _is_compiled() -> bool:
    """Return True when running as a compiled/bundled app.

    PyInstaller sets ``sys.frozen``; Nuitka provides the ``__compiled__``
    builtin. Checking both keeps the updater working regardless of which
    packager produced the running binary.
    """
    if getattr(sys, "frozen", False):
        return True
    try:
        return bool(__compiled__)  # type: ignore[name-defined]  # Nuitka builtin
    except NameError:
        return False


def _current_exe() -> str:
    """Return the path to the running executable (or .app bundle on macOS)."""
    if _is_compiled():
        exe = sys.executable
        if platform.system() == "Darwin":
            contents = pathlib.Path(exe).parent.parent
            bundle = str(contents.parent)
            if bundle.endswith(".app"):
                return bundle
        return exe
    return ""


def _current_exe_override() -> str:
    """Returns a fake frozen install path for --test-update when running from source.
    Creates a dummy onefile layout under the system temp dir so the batch script
    has a real file to back up and replace.

    Layout created:
      <tmp>/mochatools_test_install/
          Mocha Tools.exe     ← placeholder — will be overwritten by the update
    """
    dummy_dir = str(pathlib.Path(tempfile.gettempdir()) / "mochatools_test_install")
    dummy_exe = str(pathlib.Path(dummy_dir) / "Mocha Tools.exe")
    pathlib.Path(dummy_dir).mkdir(exist_ok=True, parents=True)
    # Only create if it doesn't exist — don't overwrite if a previous test run
    # already placed a real binary here (e.g. from a successful update test).
    if not pathlib.Path(dummy_exe).exists():
        pathlib.Path(dummy_exe).write_text("placeholder - old version")
    return dummy_exe


def _asset_prefix() -> str:
    """Return the macOS arch-specific filename prefix used in GitHub release
    assets, e.g. "macos-arm64-3.0.1.zip". Windows and Linux assets use their
    own naming schemes handled directly in _asset_name().
    """
    machine = platform.machine().lower()
    if machine == "arm64":
        return "macos-arm64"
    if machine == "x86_64":
        return "macos-x86_64"
    return "macos-universal"


def _asset_name(tag: str) -> str:
    """Build the full expected asset filename for this platform and release tag."""
    # Guard: if tag is not a string (e.g. accidentally passed a QObject), coerce it
    tag = str(tag).strip() if tag else ""
    if not tag:
        msg = "_asset_name() called with an empty tag"
        raise ValueError(msg)

    if platform.system() == "Windows":
        # Windows installer asset filename matches build.yml's ${{ env.VERSION }},
        # e.g. tag "3.0.1" -> "MochaTools-Setup-3.0.1.exe"
        return f"MochaTools-Setup-{tag}.exe"

    if platform.system() != "Darwin":
        # Linux asset is the standalone tarball, e.g. "MochaTools-3.0.1-linux.tar.gz"
        return f"MochaTools-{tag}-linux.tar.gz"

    return f"{_asset_prefix()}-{tag}.zip"


def _is_newer(latest: str, current: str) -> bool:
    try:
        return Version(latest.lstrip("v")) > Version(current.lstrip("v"))
    except (AttributeError, TypeError, RuntimeError, ValueError) as e:
        write_debug_log(f"[Silenced] _is_newer: {e}")
        return latest != current


def _ensure_admin_windows() -> bool:
    """On Windows, re-launch the current process with UAC elevation if we are not
    already running as administrator.  Returns True if already elevated (caller
    should proceed), False if we requested elevation and the caller should exit
    (the elevated copy will take over).
    """
    if sys.platform == "win32":
        try:
            is_admin = ctypes.windll.shell32.IsUserAnAdmin()
        except OSError as e:
            write_debug_log(f"Admin check failed: {e}")
            is_admin = False
    else:
        is_admin = False

    if is_admin:
        return True  # already elevated — proceed normally

    # Re-launch with 'runas' verb (triggers UAC prompt)
    params = " ".join(f'"{a}"' for a in sys.argv)
    with contextlib.suppress(Exception):
        ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            sys.executable,
            params,
            None,
            1,
        )
    return False  # caller should sys.exit() or abort


# ── Update check ─────────────────────────────────────────────────────────────


class UpdateCheckWorker(QThread):
    """Checks GitHub Releases API; emits update_available if a newer tag exists."""

    update_available = Signal(str, str, str)  # (tag, download_url, release_notes)
    up_to_date = Signal()
    error = Signal(str)

    def run(self) -> None:
        try:
            resp = requests.get(
                UPDATE_CHECK_URL,
                headers={"Accept": "application/vnd.github+json"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.Timeout:
            self.error.emit(
                "Connection to GitHub timed out. Check your network and try again.",
            )
            return
        except requests.exceptions.ConnectionError:
            self.error.emit("Could not reach GitHub. Check your internet connection.")
            return
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            self.error.emit(f"GitHub returned an error (HTTP {code}). Try again later.")
            return
        except Exception as e:  # noqa: BLE001
            self.error.emit(f"Update check failed: {e}")
            return

        latest_tag = data.get("tag_name", "")
        release_body = data.get("body", "")
        assets = data.get("assets", [])

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
            (
                a.get("browser_download_url", "")
                for a in assets
                if a.get("name") == want
            ),
            "",
        )
        self.update_available.emit(latest_tag, url, release_body)


# ── Download & install ───────────────────────────────────────────────────────


def launch_update_batch(bat_path: str, _test_mode: bool = False) -> None:
    """Launch a previously-prepared update script (see
    UpdateDownloadWorker.ready_to_restart).

    - Windows : runs the .bat via cmd.exe with DETACHED_PROCESS so it survives
                this process exiting.
    - Linux   : delegates to launch_update_terminal() to open the .sh in a
                terminal emulator so the user can see progress and answer sudo
                prompts.  Falls back to a bare background Popen if no terminal
                is found, and raises RuntimeError if that also fails.
    - macOS   : update is applied in-place by _install_macos; this function is
                not normally called on that platform.
    """
    if not bat_path or not pathlib.Path(bat_path).exists():
        return

    system = platform.system()

    if system == "Windows":
        # DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP ensures the batch is NOT
        # a child of this process.  Without this, `taskkill /F /T` in the batch
        # kills the batch script itself before it can copy the new exe and
        # relaunch.
        DETACHED_PROCESS = 0x00000008
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

    else:
        # Linux (and macOS fallback): the script is a .sh — open it in a
        # terminal so the user can interact with installer.sh (sudo prompts, etc.)
        launched = launch_update_terminal(bat_path)
        if not launched:
            # No graphical terminal found — run headlessly as a last resort.
            # The user won't see output, but the update will still proceed.
            pathlib.Path(bat_path).chmod(
                pathlib.Path(bat_path).stat().st_mode
                | stat.S_IEXEC
                | stat.S_IXGRP
                | stat.S_IXOTH
            )
            subprocess.Popen(
                ["bash", bat_path],
                close_fds=True,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def launch_update_terminal(script_path: str) -> bool:
    """Launch a previously-prepared update shell script (see
    UpdateDownloadWorker._install_linux) inside a terminal emulator so the
    user can see output and respond to any sudo/password prompts.

    Returns True if a terminal was launched, False if none could be found
    (caller should show an error pointing the user at the script path).
    """
    if not script_path or not pathlib.Path(script_path).exists():
        return False

    pathlib.Path(script_path).chmod(
        pathlib.Path(script_path).stat().st_mode
        | stat.S_IEXEC
        | stat.S_IXGRP
        | stat.S_IXOTH
    )

    # Try common terminal emulators in order of preference. Each needs a
    # slightly different "run this command and keep the window open" syntax.
    candidates = [
        ("x-terminal-emulator", ["-e", "bash", script_path]),
        ("gnome-terminal", ["--", "bash", script_path]),
        ("konsole", ["-e", "bash", script_path]),
        ("xfce4-terminal", ["-e", f"bash {script_path}"]),
        ("mate-terminal", ["-e", f"bash {script_path}"]),
        ("tilix", ["-e", f"bash {script_path}"]),
        ("alacritty", ["-e", "bash", script_path]),
        ("kitty", ["bash", script_path]),
        ("xterm", ["-hold", "-e", "bash", script_path]),
    ]

    for terminal, args in candidates:
        path = shutil.which(terminal)
        if not path:
            continue
        try:
            subprocess.Popen(
                [path, *args],
                close_fds=True,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (AttributeError, TypeError, RuntimeError, OSError) as e:
            write_debug_log(f"[Silenced] launch_update_terminal: {e}")
            continue
        else:
            return True

    return False


class UpdateDownloadWorker(QThread):
    """Downloads the update asset and replaces the running binary."""

    progress = Signal(int)  # 0-100
    status = Signal(str)  # human-readable status text
    done = Signal()  # update installed; caller should prompt restart
    ready_to_restart = Signal(str)  # path to a script ready to launch on restart
    # (Windows: .bat that launches the installer exe;
    #  Linux: .sh launched in a terminal that runs installer.sh)
    error = Signal(str)

    def __init__(
        self,
        download_url: str,
        tag: str = "",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.download_url = str(download_url).strip()
        # Sanitise tag — must be a plain version string, never an object repr
        raw_tag = str(tag).strip() if tag else ""
        # If it looks like an object repr, discard it
        if "<" in raw_tag or "object at" in raw_tag:
            raw_tag = ""
        self.tag = raw_tag

    def run(self) -> None:
        try:
            self._download_and_install()
        except Exception as e:  # noqa: BLE001
            self.error.emit(str(e))

    def _download_and_install(self) -> None:
        system = platform.system()
        test_mode = "--test-update" in sys.argv and not _is_compiled()
        target = _current_exe_override() if test_mode else _current_exe()
        if not target:
            self.error.emit(
                "Cannot auto-update when running from source. "
                "Pull the latest code manually.",
            )
            return
        if test_mode:
            self.status.emit(
                f"[TEST] Fake install dir: {pathlib.Path(target).parent!s}"
            )

        # On Windows, ensure we have write permission (UAC elevation if needed).
        # Probe the install directory rather than the target exe itself — the
        # exe may be locked by the OS even though the directory is writable.
        if system == "Windows":
            try:
                probe = str(pathlib.Path(target).parent / ".mocha_write_test")
                pathlib.Path(probe).write_text("ok")
                pathlib.Path(probe).unlink()
            except PermissionError:
                # We don't have write access — request elevation and bail
                elevated = _ensure_admin_windows()
                if not elevated:
                    self.error.emit(
                        "Administrator privileges are required to install the update.\n"
                        "The app will re-launch with elevated permissions.",
                    )
                    return
                # If we somehow are elevated but still can't write, report it
                self.error.emit(
                    "Cannot write to the installation directory even as administrator.\n"
                    "Try running the updater manually.",
                )
                return

        # Build asset filename
        try:
            if self.tag:
                asset_name = _asset_name(self.tag)
            elif platform.system() == "Windows":
                asset_name = "MochaTools-Setup-update.exe"
            elif platform.system() == "Darwin":
                asset_name = f"{_asset_prefix()}-update.zip"
            else:
                asset_name = "MochaTools-update-linux.tar.gz"
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
            self.error.emit(
                "Could not reach the download server. Check your internet connection.",
            )
            return
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            self.error.emit(f"Download failed: server returned HTTP {code}.")
            return
        except Exception as e:  # noqa: BLE001
            self.error.emit(f"Download failed: {e}")
            return

        total = int(resp.headers.get("content-length", 0))
        fetched = 0
        tmp_dir = tempfile.mkdtemp(prefix="mochatools_update_")
        tmp_asset = str(pathlib.Path(tmp_dir) / asset_name)

        try:
            with pathlib.Path(tmp_asset).open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        fh.write(chunk)
                        fetched += len(chunk)
                        if total:
                            self.progress.emit(int(fetched / total * 90))
        except Exception as e:  # noqa: BLE001
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
        if system == "Darwin":
            self._install_macos(tmp_asset, target, tmp_dir)
            self.progress.emit(100)
            self.status.emit("Update installed. Restart to apply.")
            self.done.emit()
            return
        script = self._install_linux(tmp_asset, target, tmp_dir)
        self.progress.emit(100)
        self.status.emit("Update ready. Quit to continue in terminal.")
        self.ready_to_restart.emit(script)
        return

    # ── Platform installers ──────────────────────────────────────────────────

    def _install_windows(self, installer_path: str, target: str, tmp_dir: str) -> str:
        """The downloaded asset IS the NSIS setup installer (MochaTools-Setup-x.x.x.exe)
        — no zip, no extraction needed. We just need to wait for our process to
        exit, then launch the installer.
        """
        if not pathlib.Path(installer_path).exists():
            msg = f"Downloaded installer not found: {installer_path}"
            raise RuntimeError(msg)

        bat = str(pathlib.Path(tmp_dir) / "update.bat")
        test_mode = "--test-update" in sys.argv and not _is_compiled()
        log = str(pathlib.Path(target).parent / "update.log")

        lines = [
            "@echo off",
            f'set "LOG={log}"',
            "setlocal",
            'call :log "=== Mocha Tools updater started ==="',
            "",
            'call :log "App already exited, proceeding..."',
            "",
            'call :log "Waiting 3s for file locks to clear..."',
            "timeout /t 3 /nobreak >NUL",
            "",
            f'call :log "Launching installer: {installer_path}"',
            *(
                []
                if test_mode
                else [
                    f'start "" "{installer_path}"',
                ]
            ),
            "",
            'call :log "Done."',
            "goto end",
            "",
            ":fail",
            'call :log "FAILED - see %LOG%"',
            "goto end",
            "",
            ":log",
            r"echo %~1",
            r'echo %~1 >>"%LOG%"',
            "exit /b",
            "",
            ":end",
            "endlocal",
        ]

        script = "\r\n".join(lines) + "\r\n"
        pathlib.Path(bat).write_text(script, newline="", encoding="utf-8")

        # Don't launch yet — the batch script will be spawned when the user
        # clicks "Restart".
        return bat

    def _install_macos(self, zip_path: str, target: str, tmp_dir: str) -> None:
        extract_dir = str(pathlib.Path(tmp_dir) / "extracted")
        pathlib.Path(extract_dir).mkdir(exist_ok=True, parents=True)

        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        new_app = next(
            (
                str(e)
                for e in pathlib.Path(extract_dir).iterdir()
                if e.name.endswith(".app")
            ),
            None,
        )
        if not new_app:
            msg = "No .app bundle found inside the downloaded zip."
            raise RuntimeError(msg)

        backup = target + ".bak"
        if pathlib.Path(backup).exists():
            shutil.rmtree(backup)
        shutil.move(target, backup)
        shutil.move(new_app, target)

        subprocess.run(
            ["xattr", "-dr", "com.apple.quarantine", target],
            check=False,
        )

    def _install_linux(self, tarball_path: str, _target: str, tmp_dir: str) -> str:
        """Write a small shell script that, once this app has quit, extracts the
        downloaded tarball and runs its installer.sh. The script is launched
        in a terminal window (see launch_update_terminal) so the user can
        watch progress and respond to any sudo password prompts.

        tarball layout (see build.yml):
          Mocha-Tools-linux        ← new binary
          installer.sh
          builditems/debian_ubuntu/icon.png (optional)

        Returns the path to the generated shell script.
        """
        if not pathlib.Path(tarball_path).exists():
            msg = f"Downloaded update not found: {tarball_path}"
            raise RuntimeError(msg)

        extract_dir = str(pathlib.Path(tmp_dir) / "extracted")
        test_mode = "--test-update" in sys.argv and not _is_compiled()
        log = str(pathlib.Path(tempfile.gettempdir()) / "mochatools_update.log")

        script_path = str(pathlib.Path(tmp_dir) / "update.sh")

        lines = [
            "#!/bin/bash",
            f'LOG="{log}"',
            f'TMP_DIR="{tmp_dir}"',
            'echo "=== Mocha Tools updater started ===" | tee -a "$LOG"',
            "",
            # Kill any running mochatools process
            'echo "Checking for running mochatools process..." | tee -a "$LOG"',
            'if pgrep -x "mochatools" > /dev/null 2>&1; then',
            '  echo "Stopping running mochatools..." | tee -a "$LOG"',
            '  pkill -x "mochatools" 2>/dev/null || true',
            "  sleep 2",
            "  # Force kill if still running",
            '  if pgrep -x "mochatools" > /dev/null 2>&1; then',
            '    pkill -9 -x "mochatools" 2>/dev/null || true',
            "    sleep 1",
            "  fi",
            '  echo "Process stopped." | tee -a "$LOG"',
            "else",
            '  echo "No running instance found." | tee -a "$LOG"',
            "fi",
            "",
            f'mkdir -p "{extract_dir}"',
            'echo "Extracting update..." | tee -a "$LOG"',
            f'tar -xzf "{tarball_path}" -C "{extract_dir}"',
            "",
            'if [ ! -f "'
            + str(pathlib.Path(extract_dir) / "installer.sh")
            + '" ]; then',
            '  echo "ERROR: installer.sh not found in update archive." | tee -a "$LOG"',
            '  read -n 1 -s -r -p "Press any key to close..."',
            "  exit 1",
            "fi",
            "",
            f'cd "{extract_dir}"',
            'chmod +x installer.sh "Mocha-Tools-linux" 2>/dev/null',
            "",
            # installer.sh handles its own sudo escalation via exec sudo
            'echo "Running installer..." | tee -a "$LOG"',
            "echo",
            *(
                []
                if test_mode
                else [
                    "./installer.sh",
                ]
            ),
            "",
            # Cleanup temp directory
            'echo "Cleaning up..." | tee -a "$LOG"',
            'rm -rf "$TMP_DIR"',
            "",
            "echo",
            'echo "Update complete. You can close this window and relaunch Mocha Tools."',
            'read -n 1 -s -r -p "Press any key to close..."',
        ]

        script = "\n".join(lines) + "\n"
        pathlib.Path(script_path).write_text(script, encoding="utf-8")
        pathlib.Path(script_path).chmod(
            pathlib.Path(script_path).stat().st_mode
            | stat.S_IEXEC
            | stat.S_IXGRP
            | stat.S_IXOTH
        )

        return script_path
