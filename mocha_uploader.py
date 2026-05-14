"""
Mocha File Uploader
A cross-platform PyQt6 application for uploading files to mocha
Written by nxllxvxxd

To compile:
    pyinstaller --onefile --windowed --noconsole mocha_uploader.py

Android:
    Use Buildozer with Kivy — PyQt6 is not supported on Android natively.
    See README comments at the bottom of this file.
"""

import sys
import os
import json
import math
import time
import threading
import requests

from PyQt6.QtWidgets import (
    QDialog, QListWidget, QListWidgetItem, QDialogButtonBox,
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QCheckBox, QProgressBar,
    QFileDialog, QFrame, QSpinBox, QComboBox, QScrollArea,
    QSizePolicy, QMessageBox, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QHeaderView, QMenu, QAbstractItemView, QInputDialog, QToolBar,
    QSplitter
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QMimeData, QUrl,
    QSettings, QSize
)
from PyQt6.QtGui import (
    QFont, QColor, QPalette, QDragEnterEvent, QDropEvent,
    QFontDatabase, QPainter, QBrush, QLinearGradient, QIcon
)

# ── Constants ────────────────────────────────────────────────────────────────
CHUNK_THRESHOLD = 20 * 1024 * 1024   # 20 MB  → use multipart above this (Cloudflare rejects larger direct POSTs)
CHUNK_SIZE      = 20 * 1024 * 1024   # 20 MB chunks
APP_NAME        = "MochaUploader"
ORG_NAME        = "Mocha"
HARDCODED_BASE_URL = "https://mocha.my"

# ── Stylesheet ───────────────────────────────────────────────────────────────
STYLESHEET = """
QMainWindow, QWidget#root {
    background-color: #111214;
}

QWidget {
    background-color: transparent;
    color: #e0e0e0;
    font-family: "Segoe UI", "SF Pro Display", "Helvetica Neue", sans-serif;
    font-size: 13px;
}

/* Section cards */
QFrame#card {
    background-color: #1a1c1f;
    border: 1px solid #2a2d32;
    border-radius: 8px;
    padding: 4px;
}

/* Section header labels */
QLabel#section_header {
    color: #8a8f98;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    padding: 0px;
    background: transparent;
}

/* Regular labels */
QLabel#field_label {
    color: #9ca3af;
    font-size: 12px;
    min-width: 90px;
    background: transparent;
}

QLabel#status_label {
    color: #e0e0e0;
    font-size: 12px;
    background: transparent;
}

/* Text inputs */
QLineEdit {
    background-color: #0e1012;
    border: 1px solid #2a2d32;
    border-radius: 6px;
    padding: 7px 10px;
    color: #e0e0e0;
    font-size: 13px;
    selection-background-color: #c8975a;
}
QLineEdit:focus {
    border: 1px solid #c8975a;
    background-color: #111315;
}
QLineEdit::placeholder {
    color: #404449;
}

/* Spin box */
QSpinBox {
    background-color: #0e1012;
    border: 1px solid #2a2d32;
    border-radius: 6px;
    padding: 6px 8px;
    color: #e0e0e0;
    font-size: 13px;
}
QSpinBox:focus { border-color: #c8975a; }
QSpinBox::up-button, QSpinBox::down-button {
    background: #1e2024;
    border: none;
    width: 18px;
}
QSpinBox::up-arrow  { border: 4px solid transparent; border-bottom: 5px solid #8a8f98; width:0; height:0; }
QSpinBox::down-arrow{ border: 4px solid transparent; border-top:    5px solid #8a8f98; width:0; height:0; }

/* Combo box */
QComboBox {
    background-color: #0e1012;
    border: 1px solid #2a2d32;
    border-radius: 6px;
    padding: 6px 10px;
    color: #e0e0e0;
    font-size: 13px;
}
QComboBox:focus { border-color: #c8975a; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox::down-arrow { border: 4px solid transparent; border-top: 5px solid #8a8f98; width:0; height:0; }
QComboBox QAbstractItemView {
    background-color: #1a1c1f;
    border: 1px solid #2a2d32;
    selection-background-color: #c8975a33;
    selection-color: #e0e0e0;
    outline: none;
}

/* Primary upload button */
QPushButton#upload_btn {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #b8823e, stop:1 #c8975a);
    color: #111214;
    border: none;
    border-radius: 7px;
    padding: 10px 24px;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.5px;
    min-height: 38px;
}
QPushButton#upload_btn:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #c8975a, stop:1 #d9a96c);
}
QPushButton#upload_btn:pressed { background: #a06e2e; }
QPushButton#upload_btn:disabled {
    background: #2a2d32;
    color: #505560;
}

/* Secondary/ghost buttons */
QPushButton#browse_btn {
    background-color: #1e2024;
    color: #c8975a;
    border: 1px solid #c8975a44;
    border-radius: 6px;
    padding: 6px 14px;
    font-size: 12px;
}
QPushButton#browse_btn:hover { background-color: #262930; border-color: #c8975a88; }

/* Checkboxes */
QCheckBox {
    color: #9ca3af;
    font-size: 12px;
    spacing: 6px;
    background: transparent;
}
QCheckBox::indicator {
    width: 15px;
    height: 15px;
    border: 1px solid #2a2d32;
    border-radius: 4px;
    background: #0e1012;
}
QCheckBox::indicator:checked {
    background: #c8975a;
    border-color: #c8975a;
    image: none;
}
QCheckBox::indicator:hover { border-color: #c8975a; }

/* Progress bar */
QProgressBar {
    background-color: #0e1012;
    border: 1px solid #2a2d32;
    border-radius: 4px;
    height: 6px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #b8823e, stop:1 #c8975a);
    border-radius: 4px;
}

/* Log console */
QLabel#log_console {
    background-color: #0e1012;
    border: 1px solid #1e2125;
    border-radius: 6px;
    color: #c8975a;
    font-family: "Consolas", "Fira Code", "Courier New", monospace;
    font-size: 11px;
    padding: 8px 10px;
    min-height: 46px;
}

/* Status badge */
QLabel#status_badge {
    background-color: #1e2024;
    border: 1px solid #2a2d32;
    border-radius: 10px;
    color: #9ca3af;
    font-size: 11px;
    font-weight: 600;
    padding: 2px 10px;
}

/* Drop zone */
QFrame#drop_zone {
    background-color: #0e1012;
    border: 2px dashed #2a2d32;
    border-radius: 10px;
    min-height: 110px;
}
QFrame#drop_zone[drag_active="true"] {
    border-color: #c8975a;
    background-color: #c8975a0d;
}

QLabel#drop_label {
    color: #505560;
    font-size: 13px;
    background: transparent;
}
QLabel#drop_label_bold {
    color: #c8975a;
    font-size: 13px;
    font-weight: 700;
    background: transparent;
}

/* Divider */
QFrame#divider {
    background-color: #2a2d32;
    max-height: 1px;
    border: none;
}

/* Scrollbar */
QScrollBar:vertical {
    background: transparent;
    width: 6px;
}
QScrollBar::handle:vertical {
    background: #2a2d32;
    border-radius: 3px;
    min-height: 20px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

/* Tab widget */
QTabWidget::pane {
    border: none;
    background: transparent;
}
QTabBar::tab {
    background: #1a1c1f;
    color: #9ca3af;
    border: 1px solid #2a2d32;
    border-bottom: none;
    border-radius: 6px 6px 0 0;
    padding: 7px 20px;
    font-size: 12px;
    font-weight: 600;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background: #111214;
    color: #c8975a;
    border-bottom: 2px solid #c8975a;
}
QTabBar::tab:hover:!selected { background: #22252a; color: #e0e0e0; }

/* File browser tree */
QTreeWidget {
    background: #0e1012;
    border: 1px solid #2a2d32;
    border-radius: 6px;
    color: #e0e0e0;
    font-size: 12px;
    outline: none;
    show-decoration-selected: 1;
}
QTreeWidget::item {
    padding: 5px 4px;
    border-bottom: 1px solid #1a1c1f;
}
QTreeWidget::item:selected {
    background: #c8975a22;
    color: #e0e0e0;
}
QTreeWidget::item:hover:!selected { background: #1a1c1f; }
QHeaderView::section {
    background: #1a1c1f;
    color: #8a8f98;
    border: none;
    border-right: 1px solid #2a2d32;
    border-bottom: 1px solid #2a2d32;
    padding: 5px 8px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.5px;
}

/* Toolbar */
QToolBar {
    background: transparent;
    border: none;
    spacing: 4px;
    padding: 0px;
}
QPushButton#tb_btn {
    background: #1e2024;
    color: #c8975a;
    border: 1px solid #2a2d32;
    border-radius: 5px;
    padding: 5px 12px;
    font-size: 11px;
    font-weight: 600;
}
QPushButton#tb_btn:hover { background: #262930; border-color: #c8975a55; }
QPushButton#tb_btn:pressed { background: #111214; }
QPushButton#tb_btn:disabled { color: #404449; border-color: #1e2024; }

QPushButton#tb_btn_danger {
    background: #1e2024;
    color: #f87171;
    border: 1px solid #2a2d32;
    border-radius: 5px;
    padding: 5px 12px;
    font-size: 11px;
    font-weight: 600;
}
QPushButton#tb_btn_danger:hover { background: #2a1a1a; border-color: #f8717155; }
QPushButton#tb_btn_danger:disabled { color: #404449; border-color: #1e2024; }
"""

# ── Upload Worker ────────────────────────────────────────────────────────────
class UploadWorker(QThread):
    progress    = pyqtSignal(int)          # 0-100
    speed       = pyqtSignal(float)        # bytes/sec
    status      = pyqtSignal(str)          # log message
    finished    = pyqtSignal(dict)         # result dict
    error       = pyqtSignal(str)

    def __init__(self, api_key, base_url, file_pairs,
                 create_share, share_expiry, share_max_downloads):
        """
        file_pairs: list of (local_abs_path, remote_dest_path) tuples.
        remote_dest_path is already the full absolute path on Mocha,
        e.g. '/Music/Album/CD1/track.flac'.
        """
        super().__init__()
        self.api_key             = api_key
        self.base_url            = base_url.rstrip("/")
        self.file_pairs          = file_pairs          # [(local, dest), ...]
        self.create_share        = create_share
        self.share_expiry        = share_expiry
        self.share_max_downloads = share_max_downloads
        self._cancel             = False

    def cancel(self):
        self._cancel = True

    def _headers(self, file_name=None):
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if file_name:
            headers["x-file-name"] = file_name
        return headers

    # ── helpers ──────────────────────────────────────────────────────────────

    def run(self):
        total_files = len(self.file_pairs)
        last_file_id = None
        last_share_url = None

        for idx, (local_path, dest_path) in enumerate(self.file_pairs, 1):
            if self._cancel:
                return

            file_name = os.path.basename(local_path)
            prefix    = f"[{idx}/{total_files}] " if total_files > 1 else ""

            try:
                file_size = os.path.getsize(local_path)
                self.status.emit(f"{prefix}{file_name}  ({self._fmt_size(file_size)})")

                if file_size <= CHUNK_THRESHOLD:
                    file_id = self._simple_upload(file_size, local_path, dest_path)
                else:
                    file_id = self._multipart_upload(file_size, local_path, dest_path)

                if self._cancel or file_id is None:
                    return

                last_file_id = file_id

                if self.create_share and idx == total_files:
                    # Only create a share for the last file (or the only file)
                    self.status.emit("Creating share link…")
                    last_share_url = self._create_share(file_id)
                    self.status.emit(f"Share: {last_share_url}")

            except Exception as e:
                self.error.emit(f"{prefix}{file_name}: {e}")
                return

        self.finished.emit({"file_id": last_file_id, "share_url": last_share_url})

    # ── simple upload (≤ 20 MB) ──────────────────────────────────────────────
    def _simple_upload(self, file_size, local_path, dest_path):
        file_name  = os.path.basename(local_path)
        dest_dir   = "/".join(dest_path.rstrip("/").split("/")[:-1]) or "/"
        url        = f"{self.base_url}/api/files"

        start    = time.time()
        uploaded = 0

        # Read file in chunks so we can report live progress
        chunks = []
        with open(local_path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                if self._cancel:
                    self.status.emit("Cancelled.")
                    return None
                chunks.append(chunk)
                uploaded += len(chunk)
                elapsed   = max(time.time() - start, 0.001)
                self.progress.emit(int(uploaded / file_size * 100))
                self.speed.emit(uploaded / elapsed)
        data = b"".join(chunks)

        self.status.emit(f"[DEBUG] Upload URL: {url}")
        self.status.emit(f"[DEBUG] Dest path: {dest_path}")
        self.status.emit(f"[DEBUG] File name: {file_name}")
        debug_headers = dict(self._headers(file_name))
        debug_headers["Authorization"] = "(hidden)"
        self.status.emit(f"[DEBUG] Headers: {debug_headers}")
        try:
            resp = requests.post(
                url,
                headers=self._headers(file_name),
                files={
                    "file": (file_name, data, "application/octet-stream"),
                    "path": (None, dest_path),
                },
                timeout=120,
            )
            resp.raise_for_status()
        except requests.HTTPError as e:
            self.status.emit(f"[DEBUG] HTTPError: {e}")
            self.status.emit(f"[DEBUG] Response status: {getattr(e.response, 'status_code', None)}")
            self.status.emit(f"[DEBUG] Response content: {getattr(e.response, 'text', None)}")
            raise
        except Exception as e:
            self.status.emit(f"[DEBUG] Exception: {e}")
            raise

        j       = resp.json()
        file_id = j.get("fileId") or j.get("id") or j.get("file", {}).get("id")
        self.status.emit(f"Upload complete. File ID: {file_id}")
        self.progress.emit(100)

        # The API honours the `path` field in the multipart form body and places
        # the file there directly — no post-upload move is needed.  Previously
        # this code always tried to move the file, which produced a 400
        # "Source and destination paths are the same" error when the API
        # had already put the file at the correct path.

        return file_id

    # ── multipart upload (> 20 MB) ───────────────────────────────────────────
    def _multipart_upload(self, file_size, local_path, dest_path):
        """
        Multipart upload per the Mocha API docs:
          1. POST /api/files/multipart/init
          2. PUT  /api/files/multipart/part  (per part, with retry)
          3. POST /api/files/multipart/complete
          POST /api/files/multipart/abort on cancel or unrecoverable error.

        Always uses the Mocha relay (PUT /api/files/multipart/part) rather than
        direct S3 presigned URLs.  Direct S3 returns NoSuchUpload on this backend
        consistently — the server-side S3 session is not created before the
        uploadId is returned.  The relay path works; it just needs small chunks
        and retry logic to handle transient connection drops.
        """
        file_name = os.path.basename(local_path)

        # ── 1. Init ──────────────────────────────────────────────────────────
        init_payload = {
            "name":         file_name,
            "originalName": file_name,
            "path":         dest_path,
            "size":         file_size,
        }
        self.status.emit(f"[DEBUG] Multipart init URL: {self.base_url}/api/files/multipart/init")
        self.status.emit(f"[DEBUG] Payload: {init_payload}")
        debug_h = {**self._headers(), "Content-Type": "application/json"}
        debug_h["Authorization"] = "(hidden)"
        self.status.emit(f"[DEBUG] Headers: {debug_h}")

        try:
            init_resp = requests.post(
                f"{self.base_url}/api/files/multipart/init",
                headers={**self._headers(file_name), "Content-Type": "application/json"},
                json=init_payload,
                timeout=30,
            )
            init_resp.raise_for_status()
        except requests.HTTPError as e:
            self.status.emit(f"[DEBUG] Init HTTPError: {e}")
            self.status.emit(f"[DEBUG] Response: {getattr(e.response, 'text', '')}")
            raise
        except Exception as e:
            self.status.emit(f"[DEBUG] Init exception: {e}")
            raise

        init      = init_resp.json()
        self.status.emit(f"[DEBUG] Init response: {init}")

        upload_id  = init["uploadId"]
        strategy   = init.get("strategy", "mocha")
        server_fid = (init.get("fileId") or init.get("id")
                      or (init.get("file") or {}).get("id"))

        # Chunk size: use CHUNK_SIZE (20 MB) for the relay path regardless of
        # what partSizeBytes says.  partSizeBytes is the S3 session's part size
        # and only matters for direct S3 uploads; the relay accepts any size.
        chunk_size  = CHUNK_SIZE
        total_parts = math.ceil(file_size / chunk_size)
        self.status.emit(
            f"Multipart upload: {total_parts} parts\u2026 "
            f"(strategy={strategy}, relayChunk={self._fmt_size(chunk_size)})"
        )
        self.status.emit(f"Session: {upload_id}")

        # ── 2. Upload parts via relay ─────────────────────────────────────────
        parts    = []
        uploaded = 0
        start    = time.time()

        with open(local_path, "rb") as f:
            for part_num in range(1, total_parts + 1):
                if self._cancel:
                    self._abort(upload_id, server_fid)
                    return None

                chunk = f.read(chunk_size)
                if not chunk:
                    break

                self.status.emit(f"Uploading part {part_num}/{total_parts}\u2026")
                self.status.emit(f"[DEBUG] Chunk size: {len(chunk)} bytes")

                etag = self._upload_part_relay(upload_id, server_fid, part_num, chunk)

                if etag is None:
                    return None

                parts.append({"partNumber": part_num, "etag": etag})
                uploaded += len(chunk)
                elapsed   = max(time.time() - start, 0.001)
                self.progress.emit(int(uploaded / file_size * 100))
                self.speed.emit(uploaded / elapsed)

        # ── 3. Complete ───────────────────────────────────────────────────────
        complete_payload = {"uploadId": upload_id, "parts": parts}
        if server_fid:
            complete_payload["fileId"] = server_fid

        self.status.emit("[DEBUG] Completing multipart upload\u2026")
        try:
            comp_resp = requests.post(
                f"{self.base_url}/api/files/multipart/complete",
                headers={**self._headers(), "Content-Type": "application/json"},
                json=complete_payload,
                timeout=60,
            )
            comp_resp.raise_for_status()
        except requests.HTTPError as e:
            self.status.emit(f"[DEBUG] Complete HTTPError: {e}")
            self.status.emit(f"[DEBUG] Response: {getattr(e.response, 'text', '')}")
            raise
        except Exception as e:
            self.status.emit(f"[DEBUG] Complete exception: {e}")
            raise

        j       = comp_resp.json()
        file_id = j.get("fileId") or j.get("id") or server_fid
        self.status.emit(f"Multipart complete. File ID: {file_id}")
        self.progress.emit(100)
        return file_id

    def _upload_part_relay(self, upload_id, server_fid, part_num, chunk,
                           max_retries=3, retry_delay=5):
        """
        Upload one part via PUT /api/files/multipart/part.
        Retries on transient network errors (connection drops, timeouts).
        Returns the ETag string on success, raises on unrecoverable error.
        """
        params = {"uploadId": upload_id, "partNumber": part_num}
        if server_fid:
            params["fileId"] = server_fid

        self.status.emit(f"[DEBUG] Part upload URL: {self.base_url}/api/files/multipart/part")
        self.status.emit(f"[DEBUG] Params: {params}")
        debug_h = dict(self._headers())
        debug_h["Authorization"] = "(hidden)"
        self.status.emit(f"[DEBUG] Headers: {debug_h}")

        last_exc = None
        for attempt in range(1, max_retries + 1):
            try:
                resp = requests.put(
                    f"{self.base_url}/api/files/multipart/part",
                    headers=self._headers(),
                    params=params,
                    data=chunk,
                    timeout=300,
                )
                resp.raise_for_status()
                return resp.headers.get("ETag", "")

            except requests.HTTPError as e:
                self.status.emit(f"[DEBUG] Part HTTPError: {e}")
                self.status.emit(f"[DEBUG] Response: {getattr(e.response, 'text', '')}")
                raise

            except Exception as e:
                last_exc = e
                self.status.emit(
                    f"[DEBUG] Part attempt {attempt}/{max_retries} failed: {e}"
                )
                if attempt < max_retries:
                    self.status.emit(f"[DEBUG] Retrying in {retry_delay}s\u2026")
                    time.sleep(retry_delay)

        raise last_exc
    def _abort(self, upload_id, file_id=None):
        try:
            payload = {"uploadId": upload_id}
            if file_id:
                payload["fileId"] = file_id
            requests.post(
                f"{self.base_url}/api/files/multipart/abort",
                headers={**self._headers(), "Content-Type": "application/json"},
                json=payload,
                timeout=15,
            )
        except Exception:
            pass
        self.status.emit("Upload aborted.")

    def _move_file(self, file_id, dest_path):
        """Move an uploaded file to dest_path via POST /api/files/move."""
        try:
            resp = requests.post(
                f"{self.base_url}/api/files/move",
                headers={**self._headers(), "Content-Type": "application/json"},
                json={"fileId": file_id, "newPath": dest_path},
                timeout=30,
            )
            resp.raise_for_status()
            j = resp.json()
            self.status.emit(f"[DEBUG] Move response: {j}")
            return j.get("fileId") or j.get("id") or file_id
        except requests.HTTPError as e:
            self.status.emit(f"[DEBUG] Move HTTPError: {e}")
            self.status.emit(f"[DEBUG] Move response: {getattr(e.response, 'text', '')[:200]}")
            # Don't raise — upload succeeded even if move fails
            return file_id
        except Exception as e:
            self.status.emit(f"[DEBUG] Move exception: {e}")
            return file_id

    def _create_share(self, file_id):
        payload = {"fileId": file_id}
        if self.share_expiry and self.share_expiry != "Never":
            payload["expiresIn"] = self.share_expiry
        if self.share_max_downloads > 0:
            payload["maxDownloads"] = self.share_max_downloads

        resp = requests.post(
            f"{self.base_url}/api/shares",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data  = resp.json()
        token = data.get("token") or data.get("share", {}).get("token", "")
        return f"{self.base_url}/s/{token}" if token else "(no share URL returned)"

    @staticmethod
    def _fmt_size(b):
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if b < 1024:
                return f"{b:.1f} {unit}"
            b /= 1024
        return f"{b:.1f} PB"


# ── Drop Zone Widget ─────────────────────────────────────────────────────────
class DropZone(QFrame):
    # Emits a list of absolute local file paths (1 file, or many from a folder)
    selection_changed = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("drop_zone")
        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(110)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(4)

        icon = QLabel("↑")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("color: #404449; font-size: 24px; background: transparent;")

        row = QHBoxLayout()
        row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.setSpacing(4)

        bold = QLabel("Click to browse")
        bold.setObjectName("drop_label_bold")
        rest = QLabel("or drag & drop a file / folder here")
        rest.setObjectName("drop_label")

        row.addWidget(bold)
        row.addWidget(rest)

        layout.addWidget(icon)
        layout.addLayout(row)

        self.file_label = QLabel("")
        self.file_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.file_label.setStyleSheet("color: #c8975a; font-size: 12px; font-weight:600; background:transparent;")
        layout.addWidget(self.file_label)

        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        self._browse()

    def _browse(self):
        """Pop a small menu so the user can choose file or folder."""
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        act_file   = menu.addAction("📄  Select file…")
        act_folder = menu.addAction("📁  Select folder…")
        chosen = menu.exec(self.mapToGlobal(self.rect().center()))
        if chosen == act_file:
            path, _ = QFileDialog.getOpenFileName(self, "Select file")
            if path:
                self._set_paths([path], path)
        elif chosen == act_folder:
            path = QFileDialog.getExistingDirectory(self, "Select folder")
            if path:
                files = self._collect_folder(path)
                if files:
                    self._set_paths(files, path)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setProperty("drag_active", "true")
            self.style().unpolish(self)
            self.style().polish(self)

    def dragLeaveEvent(self, event):
        self.setProperty("drag_active", "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def dropEvent(self, event: QDropEvent):
        self.setProperty("drag_active", "false")
        self.style().unpolish(self)
        self.style().polish(self)
        urls = event.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if os.path.isfile(path):
            self._set_paths([path], path)
        elif os.path.isdir(path):
            files = self._collect_folder(path)
            if files:
                self._set_paths(files, path)

    @staticmethod
    def _collect_folder(folder_path):
        """Recursively collect all files under folder_path, sorted."""
        result = []
        for dirpath, _dirnames, filenames in os.walk(folder_path):
            for fname in filenames:
                result.append(os.path.join(dirpath, fname))
        return sorted(result)

    def _set_paths(self, file_list, display_root):
        if not file_list:
            return
        name = os.path.basename(display_root.rstrip("/\\"))
        if len(file_list) == 1:
            size  = os.path.getsize(file_list[0])
            label = f"{os.path.basename(file_list[0])}  ({UploadWorker._fmt_size(size)})"
        else:
            total = sum(os.path.getsize(p) for p in file_list)
            label = f"{name}/  —  {len(file_list)} files  ({UploadWorker._fmt_size(total)})"
        self.file_label.setText(label)
        self.selection_changed.emit(file_list)



# ── Remote Folder Browser ─────────────────────────────────────────────────────
class FolderBrowserDialog(QDialog):
    """Fetches folders from the Mocha API and lets the user navigate & pick one."""

    def __init__(self, api_key, base_url, current_path="/", parent=None):
        super().__init__(parent)
        self.api_key    = api_key
        self.base_url   = base_url.rstrip("/")
        self.current    = current_path or "/"
        self.selected   = self.current

        self.setWindowTitle("Browse remote folders")
        self.setMinimumSize(420, 380)
        self.setStyleSheet(parent.styleSheet() if parent else "")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        # Current path label
        self.path_label = QLabel()
        self.path_label.setObjectName("section_header")
        lay.addWidget(self.path_label)

        # Folder list
        self.list = QListWidget()
        self.list.setStyleSheet("""
            QListWidget { background:#0e1012; border:1px solid #2a2d32;
                          border-radius:6px; color:#e0e0e0; font-size:13px; }
            QListWidget::item { padding:6px 10px; }
            QListWidget::item:selected { background:#c8975a33; color:#e0e0e0; }
            QListWidget::item:hover { background:#1e2024; }
        """)
        self.list.itemDoubleClicked.connect(self._on_double_click)
        lay.addWidget(self.list)

        # Status
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color:#9ca3af; font-size:11px; background:transparent;")
        lay.addWidget(self.status_lbl)

        # Buttons
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        # Style the OK button
        btns.button(QDialogButtonBox.StandardButton.Ok).setObjectName("upload_btn")
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Select this folder")
        lay.addWidget(btns)

        self._navigate(self.current)

    def _navigate(self, path):
        self.current = path
        self.selected = path
        self.path_label.setText(path or "/")
        self.status_lbl.setText("Loading…")
        self.list.clear()

        try:
            resp = requests.get(
                f"{self.base_url}/api/files",
                headers={"Authorization": f"Bearer {self.api_key}"},
                params={"path": path, "includeSubfolders": "0"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            self.status_lbl.setText(f"Error: {e}")
            # Log raw response for debugging
            if hasattr(e, "response") and e.response is not None:
                self.status_lbl.setText(
                    f"Error {e.response.status_code}: {e.response.text[:200]}"
                )
            return

        # ── DEBUG: show raw API response shape in the dialog status label ──────
        import json as _json
        if isinstance(data, dict):
            preview = f"keys={list(data.keys())}  sample={_json.dumps(data)[:300]}"
        elif isinstance(data, list):
            preview = f"list[{len(data)}]  first={_json.dumps(data[0])[:200] if data else '(empty)'}"
        else:
            preview = repr(data)[:300]
        self.status_lbl.setText(f"[DEBUG] {preview}")
        self.status_lbl.setWordWrap(True)

        # Add ".." entry unless we're at root
        if path and path != "/":
            parent = "/" + "/".join(path.strip("/").split("/")[:-1])
            parent = parent if parent != "/" else "/"
            item = QListWidgetItem("↑  .. (go up)")
            item.setData(Qt.ItemDataRole.UserRole, ("dir", parent))
            item.setForeground(QColor("#9ca3af"))
            self.list.addItem(item)

        # Collect folders — API returns {"files": [...], "folders": [...], ...}
        # "folders" contains folder entries; "files" contains only file entries.
        folders = []
        # Check dedicated "folders" key first, then fall back to scanning "files"
        # for any entries that look like folders (future-proofing).
        folder_entries = data.get("folders") if isinstance(data, dict) else []
        if not folder_entries and isinstance(data, list):
            folder_entries = data  # flat list — scan everything
        for entry in (folder_entries or []):
            # API may return folders as strings (paths) or as dicts
            if isinstance(entry, str):
                # entry is the full path e.g. "/Music/Albums"
                name     = entry.rstrip("/").split("/")[-1]
                fullpath = entry
            elif isinstance(entry, dict):
                name = (
                    entry.get("name")
                    or entry.get("original_name")
                    or entry.get("originalName")
                    or entry.get("file_name")
                    or ""
                )
                fullpath = (
                    entry.get("path")
                    or entry.get("fullPath")
                    or (path.rstrip("/") + "/" + name)
                )
            else:
                continue
            if name:
                folders.append((name, fullpath))

        folders.sort(key=lambda x: x[0].lower())
        for name, fullpath in folders:
            item = QListWidgetItem(f"📁  {name}")
            item.setData(Qt.ItemDataRole.UserRole, ("dir", fullpath))
            self.list.addItem(item)

        count = len(folders)
        suffix = f" folder{'s' if count != 1 else ''}"
        existing = self.status_lbl.text()
        if existing.startswith("[DEBUG]"):
            # Append folder count to the debug line
            self.status_lbl.setText(existing + f"  |  {count}{suffix}")
        else:
            self.status_lbl.setText(f"{count}{suffix}")

    def _on_double_click(self, item):
        kind, path = item.data(Qt.ItemDataRole.UserRole)
        if kind == "dir":
            self._navigate(path)

    def _on_accept(self):
        sel = self.list.currentItem()
        if sel:
            kind, path = sel.data(Qt.ItemDataRole.UserRole)
            self.selected = path
        else:
            self.selected = self.current
        self.accept()


# ── Files API Worker ─────────────────────────────────────────────────────────
class FilesWorker(QThread):
    """Generic background worker for Files-tab API operations."""
    done    = pyqtSignal(object)   # result payload (varies by op)
    error   = pyqtSignal(str)

    def __init__(self, op, api_key, base_url, **kwargs):
        super().__init__()
        self.op       = op          # 'list' | 'delete' | 'move' | 'share' | 'mkdir' | 'shares'
        self.api_key  = api_key
        self.base_url = base_url.rstrip("/")
        self.kwargs   = kwargs

    def _h(self):
        return {"Authorization": f"Bearer {self.api_key}",
                "Content-Type":  "application/json"}

    def run(self):
        try:
            if self.op == "list":
                self._list()
            elif self.op == "delete":
                self._delete()
            elif self.op == "move":
                self._move()
            elif self.op == "share":
                self._share()
            elif self.op == "mkdir":
                self._mkdir()
            elif self.op == "shares":
                self._list_shares()
            elif self.op == "delete_folder":
                self._delete_folder()
        except Exception as e:
            self.error.emit(str(e))

    def _list(self):
        path = self.kwargs.get("path", "/")
        resp = requests.get(
            f"{self.base_url}/api/files",
            headers={"Authorization": f"Bearer {self.api_key}"},
            params={"path": path, "includeSubfolders": "0"},
            timeout=15,
        )
        resp.raise_for_status()
        self.done.emit({"op": "list", "path": path, "data": resp.json()})

    def _delete(self):
        file_name = self.kwargs["file_name"]   # full remote path / filename
        resp = requests.delete(
            f"{self.base_url}/api/files/{requests.utils.quote(file_name, safe='')}",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        self.done.emit({"op": "delete", "file_name": file_name})

    def _delete_folder(self):
        path = self.kwargs["path"]
        resp = requests.delete(
            f"{self.base_url}/api/files/folders",
            headers=self._h(),
            json={"path": path},
            timeout=15,
        )
        resp.raise_for_status()
        self.done.emit({"op": "delete_folder", "path": path})

    def _move(self):
        file_id  = self.kwargs.get("file_id")
        new_path = self.kwargs["new_path"]
        payload  = {"newPath": new_path}
        if file_id:
            payload["fileId"] = file_id
        else:
            payload["sourcePath"] = self.kwargs.get("source_path", "")
        resp = requests.post(
            f"{self.base_url}/api/files/move",
            headers=self._h(),
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        self.done.emit({"op": "move", "new_path": new_path})

    def _share(self):
        file_id  = self.kwargs["file_id"]
        expiry   = self.kwargs.get("expiry", "")
        max_dl   = self.kwargs.get("max_downloads", 0)
        payload  = {"fileId": file_id}
        if expiry and expiry != "Never":
            payload["expiresIn"] = expiry
        if max_dl > 0:
            payload["maxDownloads"] = max_dl
        resp = requests.post(
            f"{self.base_url}/api/shares",
            headers=self._h(),
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        data  = resp.json()
        token = data.get("token") or (data.get("share") or {}).get("token", "")
        url   = f"{self.base_url}/s/{token}" if token else ""
        self.done.emit({"op": "share", "url": url, "token": token})

    def _mkdir(self):
        path = self.kwargs["path"]
        resp = requests.post(
            f"{self.base_url}/api/files/folders",
            headers=self._h(),
            json={"path": path},
            timeout=15,
        )
        resp.raise_for_status()
        self.done.emit({"op": "mkdir", "path": path})

    def _list_shares(self):
        resp = requests.get(
            f"{self.base_url}/api/shares",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        self.done.emit({"op": "shares", "data": resp.json()})


# ── Files Browser Tab ─────────────────────────────────────────────────────────
class FilesBrowserTab(QWidget):
    """
    The 'Files' tab — lists remote files and folders, allows:
      • Navigate folders (double-click or breadcrumb)
      • Create folder
      • Delete file or folder
      • Move file
      • Create / copy share link
    """

    def __init__(self, get_api_key, parent=None):
        super().__init__(parent)
        self.get_api_key  = get_api_key   # callable → current API key string
        self.base_url     = HARDCODED_BASE_URL
        self.current_path = "/"
        self._workers     = []            # keep refs alive
        self._shares_map  = {}            # fileId → share token/url

        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        # ── Breadcrumb / path bar ────────────────────────────────────────────
        path_row = QHBoxLayout()
        path_row.setSpacing(6)

        self.path_edit = QLineEdit("/")
        self.path_edit.setPlaceholderText("/path/to/folder")
        self.path_edit.returnPressed.connect(self._on_path_entered)

        go_btn = QPushButton("Go")
        go_btn.setObjectName("tb_btn")
        go_btn.setFixedWidth(40)
        go_btn.clicked.connect(self._on_path_entered)

        up_btn = QPushButton("↑")
        up_btn.setObjectName("tb_btn")
        up_btn.setFixedWidth(32)
        up_btn.setToolTip("Go up one level")
        up_btn.clicked.connect(self._go_up)

        path_row.addWidget(QLabel("Path:"))
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(go_btn)
        path_row.addWidget(up_btn)
        outer.addLayout(path_row)

        # ── Toolbar ──────────────────────────────────────────────────────────
        tb = QHBoxLayout()
        tb.setSpacing(4)

        self.refresh_btn  = self._tb("↺  Refresh",     self._refresh)
        self.mkdir_btn    = self._tb("+ New Folder",    self._create_folder)
        self.move_btn     = self._tb("↦  Move",         self._move_selected)
        self.share_btn    = self._tb("⤴  Share",        self._share_selected)
        self.delete_btn   = self._tb("✕  Delete",       self._delete_selected, danger=True)

        for btn in (self.refresh_btn, self.mkdir_btn, self.move_btn,
                    self.share_btn, self.delete_btn):
            tb.addWidget(btn)
        tb.addStretch()

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet(
            "color:#9ca3af; font-size:11px; background:transparent;")
        tb.addWidget(self.status_lbl)

        outer.addLayout(tb)

        # ── File tree ────────────────────────────────────────────────────────
        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(["Name", "Size", "Type", "Shared", "Expires"])
        self.tree.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setRootIsDecorated(False)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)

        # Column widths
        hdr = self.tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)

        outer.addWidget(self.tree, 1)

        # ── Share result bar ─────────────────────────────────────────────────
        self.share_bar = QLabel("")
        self.share_bar.setObjectName("log_console")
        self.share_bar.setWordWrap(True)
        self.share_bar.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.share_bar.setOpenExternalLinks(True)
        self.share_bar.hide()
        outer.addWidget(self.share_bar)

        self._set_action_btns_enabled(False)

    def _tb(self, label, slot, danger=False):
        btn = QPushButton(label)
        btn.setObjectName("tb_btn_danger" if danger else "tb_btn")
        btn.clicked.connect(slot)
        return btn

    # ── Navigation ────────────────────────────────────────────────────────────
    def _on_path_entered(self):
        path = self.path_edit.text().strip() or "/"
        self._navigate(path)

    def _go_up(self):
        parts = self.current_path.strip("/").split("/")
        parent = "/" + "/".join(parts[:-1]) if len(parts) > 1 else "/"
        self._navigate(parent)

    def _navigate(self, path):
        api_key = self.get_api_key()
        if not api_key:
            self._status("⚠ Enter your API key in the Upload tab first.")
            return
        self.current_path = path
        self.path_edit.setText(path)
        self._status("Loading…")
        self.tree.clear()
        self.share_bar.hide()

        # Fetch file list and shares in parallel
        self._run_worker("list", path=path)
        self._run_worker("shares")

    def _refresh(self):
        self._navigate(self.current_path)

    # ── Worker dispatch ───────────────────────────────────────────────────────
    def _run_worker(self, op, **kwargs):
        api_key = self.get_api_key()
        w = FilesWorker(op, api_key, self.base_url, **kwargs)
        w.done.connect(self._on_worker_done)
        w.error.connect(self._on_worker_error)
        w.finished.connect(lambda: self._workers.remove(w) if w in self._workers else None)
        self._workers.append(w)
        w.start()

    def _on_worker_done(self, result):
        op = result.get("op")
        if op == "list":
            self._populate(result["path"], result["data"])
        elif op == "shares":
            self._index_shares(result["data"])
            self._refresh_share_indicators()
        elif op in ("delete", "delete_folder", "move", "mkdir"):
            self._status("✓ Done")
            self._refresh()
        elif op == "share":
            url = result.get("url", "")
            self._status(f"✓ Share created")
            if url:
                self.share_bar.setText(
                    f'Share link: <a href="{url}" style="color:#c8975a;">{url}</a>')
                self.share_bar.show()
            self._refresh()

    def _on_worker_error(self, msg):
        self._status(f"✗ {msg}")
        QMessageBox.warning(self, "Error", msg)

    # ── Populate tree ─────────────────────────────────────────────────────────
    def _populate(self, path, data):
        self.tree.setSortingEnabled(False)
        self.tree.clear()

        folders = []
        files   = []

        if isinstance(data, dict):
            raw_folders = data.get("folders") or []
            raw_files   = data.get("files")   or []
        elif isinstance(data, list):
            raw_files   = data
            raw_folders = []
        else:
            raw_files = raw_folders = []

        # ── Folders ──
        for entry in raw_folders:
            if isinstance(entry, str):
                name     = entry.rstrip("/").split("/")[-1]
                fullpath = entry
                folders.append({"name": name, "path": fullpath})
            elif isinstance(entry, dict):
                name = (entry.get("name") or entry.get("originalName")
                        or entry.get("path", "").rstrip("/").split("/")[-1])
                fullpath = entry.get("path") or f"{path.rstrip('/')}/{name}"
                folders.append({"name": name, "path": fullpath, **entry})

        # ── Files ──
        for entry in raw_files:
            if isinstance(entry, dict):
                # Skip entries that look like folders in a flat list
                if entry.get("type") == "folder" or entry.get("isFolder"):
                    name     = (entry.get("name") or
                                entry.get("path", "").rstrip("/").split("/")[-1])
                    fullpath = entry.get("path") or f"{path.rstrip('/')}/{name}"
                    folders.append({"name": name, "path": fullpath, **entry})
                else:
                    files.append(entry)

        # Add ".." row
        if path and path != "/":
            up_item = QTreeWidgetItem(["↑  ..", "", "folder", "", ""])
            up_item.setData(0, Qt.ItemDataRole.UserRole,
                            {"_type": "up", "path": self._parent_path(path)})
            up_item.setForeground(0, QColor("#9ca3af"))
            self.tree.addTopLevelItem(up_item)

        # Add folder rows
        for f in sorted(folders, key=lambda x: x["name"].lower()):
            item = QTreeWidgetItem([
                f"📁  {f['name']}", "", "folder", "", ""
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, {"_type": "folder", **f})
            item.setForeground(0, QColor("#c8975a"))
            self.tree.addTopLevelItem(item)

        # Add file rows
        for f in sorted(files, key=lambda x: (
                x.get("name") or x.get("originalName") or "").lower()):
            name    = (f.get("name") or f.get("originalName")
                       or f.get("file_name") or "")
            size    = f.get("size") or f.get("fileSize") or 0
            fid     = f.get("id") or f.get("fileId") or ""
            expires = f.get("expiresAt") or f.get("expiry") or "—"
            if expires and expires != "—":
                # Trim to date only if it's an ISO timestamp
                expires = expires[:10] if len(expires) > 10 else expires

            item = QTreeWidgetItem([
                f"  {name}",
                UploadWorker._fmt_size(int(size)) if size else "—",
                "file",
                "",          # shared — filled after shares load
                expires,
            ])
            item.setData(0, Qt.ItemDataRole.UserRole,
                         {"_type": "file", "name": name, "id": fid,
                          "path": f.get("path") or f"{path.rstrip('/')}/{name}",
                          **f})
            self.tree.addTopLevelItem(item)

        self.tree.setSortingEnabled(True)
        count = len(folders) + len(files)
        self._status(f"{len(folders)} folder{'s' if len(folders)!=1 else ''}, "
                     f"{len(files)} file{'s' if len(files)!=1 else ''}")
        self._set_action_btns_enabled(False)
        # Apply any already-loaded share indicators
        self._refresh_share_indicators()

    def _index_shares(self, data):
        """Build fileId → share_url map from GET /api/shares response."""
        self._shares_map = {}
        items = data if isinstance(data, list) else data.get("shares", [])
        for s in items:
            fid   = (s.get("fileId") or
                     (s.get("file") or {}).get("id") or "")
            token = s.get("token", "")
            if fid:
                self._shares_map[fid] = {
                    "url":     f"{self.base_url}/s/{token}" if token else "",
                    "token":   token,
                    "expires": s.get("expiresAt") or s.get("expiry") or "—",
                    "active":  s.get("active", True),
                }

    def _refresh_share_indicators(self):
        """Update the Shared column for all file rows based on _shares_map."""
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            item = root.child(i)
            meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
            if meta.get("_type") != "file":
                continue
            fid = meta.get("id") or meta.get("fileId") or ""
            if fid in self._shares_map:
                share = self._shares_map[fid]
                label = "● Shared" if share.get("active", True) else "○ Inactive"
                color = "#4ade80" if share.get("active", True) else "#9ca3af"
                item.setText(3, label)
                item.setForeground(3, QColor(color))
                # Update expires column with share expiry if file has none
                if item.text(4) in ("—", ""):
                    exp = share.get("expires", "—")
                    if exp and exp != "—":
                        item.setText(4, exp[:10] if len(exp) > 10 else exp)
            else:
                item.setText(3, "")

    # ── Selection ─────────────────────────────────────────────────────────────
    def _on_selection_changed(self):
        items = self._selected_items()
        has   = len(items) > 0
        # Move/share only for single file selection; delete works for all
        single_file = (len(items) == 1 and
                       items[0].data(0, Qt.ItemDataRole.UserRole).get("_type") == "file")
        single_any  = len(items) == 1 and (
                       items[0].data(0, Qt.ItemDataRole.UserRole).get("_type")
                       in ("file", "folder"))
        self.move_btn.setEnabled(single_file)
        self.share_btn.setEnabled(single_file)
        self.delete_btn.setEnabled(has)

    def _set_action_btns_enabled(self, enabled):
        self.move_btn.setEnabled(enabled)
        self.share_btn.setEnabled(enabled)
        self.delete_btn.setEnabled(enabled)

    def _selected_items(self):
        return [i for i in self.tree.selectedItems()
                if (i.data(0, Qt.ItemDataRole.UserRole) or {}).get("_type")
                in ("file", "folder")]

    def _on_double_click(self, item, _col):
        meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
        t    = meta.get("_type")
        if t in ("folder", "up"):
            self._navigate(meta["path"])

    # ── Actions ───────────────────────────────────────────────────────────────
    def _create_folder(self):
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        path = f"{self.current_path.rstrip('/')}/{name}"
        self._status(f"Creating {path}…")
        self._run_worker("mkdir", path=path)

    def _delete_selected(self):
        items = self._selected_items()
        if not items:
            return
        names = [item.text(0).strip().lstrip("📁").lstrip() for item in items]
        msg   = (f"Delete {names[0]!r}?"
                 if len(names) == 1
                 else f"Delete {len(names)} items?")
        if QMessageBox.question(self, "Confirm Delete", msg,
                                QMessageBox.StandardButton.Yes |
                                QMessageBox.StandardButton.No
                                ) != QMessageBox.StandardButton.Yes:
            return
        for item in items:
            meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
            if meta.get("_type") == "folder":
                self._run_worker("delete_folder", path=meta.get("path", ""))
            else:
                file_name = meta.get("name") or meta.get("path", "").lstrip("/")
                self._run_worker("delete", file_name=file_name)
        self._status("Deleting…")

    def _move_selected(self):
        items = self._selected_items()
        if len(items) != 1:
            return
        meta  = items[0].data(0, Qt.ItemDataRole.UserRole) or {}
        fid   = meta.get("id") or meta.get("fileId") or ""
        src   = meta.get("path") or meta.get("name") or ""
        name  = meta.get("name") or src.rstrip("/").split("/")[-1]

        dlg = FolderBrowserDialog(self.get_api_key(), self.base_url,
                                  self.current_path, parent=self)
        dlg.setWindowTitle("Move — choose destination folder")
        if not dlg.exec():
            return
        dest_folder = dlg.selected.rstrip("/")
        dest_path   = f"{dest_folder}/{name}"
        self._status(f"Moving to {dest_path}…")
        self._run_worker("move", file_id=fid, source_path=src, new_path=dest_path)

    def _share_selected(self):
        items = self._selected_items()
        if len(items) != 1:
            return
        meta = items[0].data(0, Qt.ItemDataRole.UserRole) or {}
        fid  = meta.get("id") or meta.get("fileId") or ""
        name = meta.get("name") or ""

        # Check if already shared — offer to copy existing link
        if fid in self._shares_map:
            existing_url = self._shares_map[fid].get("url", "")
            ans = QMessageBox.question(
                self, "Already Shared",
                f"{name!r} already has a share link.\n\n{existing_url}\n\nCreate a new link anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
                QMessageBox.StandardButton.Cancel,
            )
            if ans == QMessageBox.StandardButton.No:
                self.share_bar.setText(
                    f'Share link: <a href="{existing_url}" style="color:#c8975a;">'
                    f'{existing_url}</a>')
                self.share_bar.show()
                return
            elif ans == QMessageBox.StandardButton.Cancel:
                return

        # Pick expiry
        expiry, ok = QInputDialog.getItem(
            self, "Share Expiry", "Expiration:",
            ["Never", "1h", "6h", "12h", "1d", "3d", "7d", "14d", "30d"],
            editable=False,
        )
        if not ok:
            return

        self._status(f"Creating share for {name!r}…")
        self._run_worker("share", file_id=fid, expiry=expiry)

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if meta.get("_type") not in ("file", "folder"):
            return

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background:#1a1c1f; border:1px solid #2a2d32;
                    color:#e0e0e0; font-size:12px; }
            QMenu::item { padding:6px 24px; }
            QMenu::item:selected { background:#c8975a33; }
        """)

        if meta.get("_type") == "file":
            menu.addAction("⤴  Share",  self._share_selected)
            menu.addAction("↦  Move",   self._move_selected)
        menu.addSeparator()
        menu.addAction("✕  Delete", self._delete_selected)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _status(self, msg):
        self.status_lbl.setText(msg)

    @staticmethod
    def _parent_path(path):
        parts = path.strip("/").split("/")
        return "/" + "/".join(parts[:-1]) if len(parts) > 1 else "/"


# ── Main Window ──────────────────────────────────────────────────────────────
class MochaUploader(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Mocha Uploader")
        self.setMinimumWidth(520)
        self.setMaximumWidth(640)
        self.selected_files = []   # list of local absolute paths
        self.selected_root  = ""   # common ancestor for relative path calc
        self.worker         = None
        self.settings      = QSettings(ORG_NAME, APP_NAME)
        self._build_ui()
        self._load_settings()

    # ── UI construction ──────────────────────────────────────────────────────
    def _build_ui(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)

        root_lay = QVBoxLayout(root)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # ── Tab widget ───────────────────────────────────────────────────────
        self.tabs = QTabWidget()
        root_lay.addWidget(self.tabs)

        # ── Upload tab ───────────────────────────────────────────────────────
        upload_tab = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        main  = QVBoxLayout(inner)
        main.setContentsMargins(16, 16, 16, 20)
        main.setSpacing(12)

        scroll.setWidget(inner)

        upload_tab_lay = QVBoxLayout(upload_tab)
        upload_tab_lay.setContentsMargins(0, 0, 0, 0)
        upload_tab_lay.addWidget(scroll)

        # ── Files tab ────────────────────────────────────────────────────────
        self.files_tab = FilesBrowserTab(
            get_api_key=lambda: self.api_key_edit.text().strip()
        )

        self.tabs.addTab(upload_tab, "↑  Upload")
        self.tabs.addTab(self.files_tab, "📁  Files")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # ── API CONFIGURATION ────────────────────────────────────────────────
        main.addWidget(self._make_section_header("API Configuration"))
        api_card = self._make_card()
        api_lay  = QVBoxLayout(api_card)
        api_lay.setSpacing(10)

        # API Key row
        key_row = QHBoxLayout()
        key_lbl = QLabel("API key")
        key_lbl.setObjectName("field_label")
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setPlaceholderText("mocha_your_api_key_here")
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_key_cb  = QCheckBox("Show")
        self.show_key_cb.toggled.connect(self._toggle_key_visibility)
        key_row.addWidget(key_lbl)
        key_row.addWidget(self.api_key_edit, 1)
        key_row.addWidget(self.show_key_cb)
        api_lay.addLayout(key_row)

        # Remove Base URL row (hardcoded)
        # Upload path
        path_row = QHBoxLayout()
        path_lbl = QLabel("Upload path")
        path_lbl.setObjectName("field_label")
        self.upload_path_edit = QLineEdit()
        self.upload_path_edit.setPlaceholderText("/folder/subfolder")
        self.upload_path_edit.setText("/")
        self.browse_path_btn = QPushButton("Browse")
        self.browse_path_btn.setObjectName("browse_btn")
        self.browse_path_btn.setFixedWidth(68)
        self.browse_path_btn.clicked.connect(self._browse_remote_path)
        path_row.addWidget(path_lbl)
        path_row.addWidget(self.upload_path_edit, 1)
        path_row.addWidget(self.browse_path_btn)
        api_lay.addLayout(path_row)

        # Remember key checkbox
        self.remember_cb = QCheckBox("Remember settings across sessions")
        api_lay.addWidget(self.remember_cb)

        main.addWidget(api_card)

        # ── FILE ─────────────────────────────────────────────────────────────
        main.addWidget(self._make_section_header("File"))
        file_card = self._make_card()
        file_lay  = QVBoxLayout(file_card)
        self.drop_zone = DropZone()
        self.drop_zone.selection_changed.connect(self._on_files_selected)
        file_lay.addWidget(self.drop_zone)
        main.addWidget(file_card)

        # ── UPLOAD STATUS ─────────────────────────────────────────────────────
        main.addWidget(self._make_section_header("Upload Status"))
        status_card = self._make_card()
        status_lay  = QVBoxLayout(status_card)
        status_lay.setSpacing(8)

        # Badge row
        top_row = QHBoxLayout()
        self.status_badge = QLabel("● Idle")
        self.status_badge.setObjectName("status_badge")
        top_row.addWidget(self.status_badge)
        top_row.addStretch()
        status_lay.addLayout(top_row)

        # Upload speed row
        speed_row = QHBoxLayout()
        speed_lbl = QLabel("Speed:")
        speed_lbl.setObjectName("field_label")
        self.speed_label = QLabel("")
        self.speed_label.setObjectName("status_label")
        self.speed_label.setStyleSheet("color: #9ca3af; font-size: 11px; background:transparent;")
        speed_row.addWidget(speed_lbl)
        speed_row.addWidget(self.speed_label)
        speed_row.addStretch()
        status_lay.addLayout(speed_row)

        # Progress bar + percent
        prog_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.pct_label = QLabel("0%")
        self.pct_label.setObjectName("status_label")
        self.pct_label.setFixedWidth(36)
        prog_row.addWidget(self.progress_bar, 1)
        prog_row.addWidget(self.pct_label)
        status_lay.addLayout(prog_row)

        # Log console
        self.log_label = QLabel("Ready — configure your API key and select a file.")
        self.log_label.setObjectName("log_console")
        self.log_label.setWordWrap(True)
        self.log_label.setMinimumHeight(46)
        status_lay.addWidget(self.log_label)

        # Share result
        self.share_result = QLabel("")
        self.share_result.setObjectName("log_console")
        self.share_result.setWordWrap(True)
        self.share_result.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.share_result.setOpenExternalLinks(True)
        self.share_result.hide()
        status_lay.addWidget(self.share_result)

        main.addWidget(status_card)

        # ── SHARE OPTIONS ─────────────────────────────────────────────────────
        main.addWidget(self._make_section_header("Share Options"))
        share_card = self._make_card()
        share_lay  = QVBoxLayout(share_card)
        share_lay.setSpacing(10)

        self.create_share_cb = QCheckBox("Create share link after upload")
        share_lay.addWidget(self.create_share_cb)
        self.create_share_cb.toggled.connect(self._toggle_share_options)

        self.share_opts_widget = QWidget()
        share_opts_lay = QVBoxLayout(self.share_opts_widget)
        share_opts_lay.setContentsMargins(0, 4, 0, 0)
        share_opts_lay.setSpacing(8)

        # Expiration
        exp_row = QHBoxLayout()
        exp_lbl = QLabel("Expiration")
        exp_lbl.setObjectName("field_label")
        self.expiry_combo = QComboBox()
        self.expiry_combo.addItems([
            "Never", "1h", "6h", "12h", "1d", "3d", "7d", "14d", "30d"
        ])
        exp_row.addWidget(exp_lbl)
        exp_row.addWidget(self.expiry_combo, 1)
        share_opts_lay.addLayout(exp_row)

        # Max downloads
        dl_row = QHBoxLayout()
        dl_lbl = QLabel("Max downloads")
        dl_lbl.setObjectName("field_label")
        self.max_dl_spin = QSpinBox()
        self.max_dl_spin.setRange(0, 9999)
        self.max_dl_spin.setValue(0)
        self.max_dl_spin.setSpecialValueText("Unlimited")
        self.max_dl_spin.setSuffix(" downloads")
        dl_row.addWidget(dl_lbl)
        dl_row.addWidget(self.max_dl_spin, 1)
        share_opts_lay.addLayout(dl_row)

        share_lay.addWidget(self.share_opts_widget)
        self.share_opts_widget.hide()
        main.addWidget(share_card)

        # ── UPLOAD BUTTON ─────────────────────────────────────────────────────
        self.upload_btn = QPushButton("↑  Upload file")
        self.upload_btn.setObjectName("upload_btn")
        self.upload_btn.setMinimumHeight(42)
        self.upload_btn.clicked.connect(self._start_upload)
        main.addWidget(self.upload_btn)

        self.cancel_btn = QPushButton("✕  Cancel")
        self.cancel_btn.setObjectName("browse_btn")
        self.cancel_btn.setMinimumHeight(36)
        self.cancel_btn.clicked.connect(self._cancel_upload)
        self.cancel_btn.hide()
        main.addWidget(self.cancel_btn)

    # ── Helpers ──────────────────────────────────────────────────────────────
    def _make_section_header(self, text):
        lbl = QLabel(text.upper())
        lbl.setObjectName("section_header")
        return lbl

    def _make_card(self):
        frame = QFrame()
        frame.setObjectName("card")
        return frame

    def _toggle_key_visibility(self, checked):
        mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        self.api_key_edit.setEchoMode(mode)

    def _browse_remote_path(self):
        api_key = self.api_key_edit.text().strip()
        if not api_key:
            self._log("⚠ Enter your API key first to browse remote folders.")
            return
        current = self.upload_path_edit.text().strip() or "/"
        dlg = FolderBrowserDialog(api_key, HARDCODED_BASE_URL, current, parent=self)
        if dlg.exec():
            self.upload_path_edit.setText(dlg.selected)

    def _toggle_share_options(self, checked):
        self.share_opts_widget.setVisible(checked)

    def _on_files_selected(self, file_list):
        self.selected_files = file_list
        if len(file_list) == 1:
            self.selected_root = os.path.dirname(file_list[0])
            self._log(f"Selected: {os.path.basename(file_list[0])}")
        else:
            # Common root = the dropped folder itself (parent of first file's dir)
            self.selected_root = os.path.commonpath(file_list)
            if os.path.isfile(self.selected_root):
                self.selected_root = os.path.dirname(self.selected_root)
            self._log(f"Selected folder: {len(file_list)} files")
        self.share_result.hide()

    # ── Settings ──────────────────────────────────────────────────────────────
    def _load_settings(self):
        self.api_key_edit.setText(self.settings.value("api_key", ""))
        # self.base_url_edit.setText(self.settings.value("base_url", ""))  # removed
        self.upload_path_edit.setText(self.settings.value("upload_path", "/"))
        remember = self.settings.value("remember", False, type=bool)
        self.remember_cb.setChecked(remember)

    def _save_settings(self):
        if self.remember_cb.isChecked():
            self.settings.setValue("api_key",     self.api_key_edit.text())
            # self.settings.setValue("base_url",    self.base_url_edit.text())  # removed
            self.settings.setValue("upload_path", self.upload_path_edit.text())
            self.settings.setValue("remember",    True)
        else:
            self.settings.remove("api_key")
            # self.settings.remove("base_url")  # removed
            self.settings.remove("upload_path")
            self.settings.setValue("remember", False)

    # ── Upload flow ───────────────────────────────────────────────────────────
    def _start_upload(self):
        api_key     = self.api_key_edit.text().strip()
        base_url    = HARDCODED_BASE_URL
        upload_path = self.upload_path_edit.text().strip() or "/"

        if not api_key:
            self._log("⚠ Please enter an API key.")
            return
        if not self.selected_files:
            self._log("⚠ Please select a file or folder.")
            return

        self._save_settings()
        self._set_uploading(True)
        self.share_result.hide()
        self.progress_bar.setValue(0)
        self.pct_label.setText("0%")
        self.speed_label.setText("")
        self._badge("Uploading", "#c8975a")

        expiry = self.expiry_combo.currentText() if self.create_share_cb.isChecked() else "Never"
        max_dl = self.max_dl_spin.value()        if self.create_share_cb.isChecked() else 0

        # Build list of (local_abs_path, remote_dest_path) pairs.
        # For a single file the dest is just upload_path/filename.
        # For a folder we preserve the relative sub-structure so that
        #   /local/Album/CD1/track.flac → <upload_path>/Album/CD1/track.flac
        base_remote = "/" + upload_path.strip("/")
        file_pairs  = []
        for local in self.selected_files:
            rel = os.path.relpath(local, self.selected_root)
            # relpath uses OS separator; normalise to forward slashes
            rel = rel.replace(os.sep, "/")
            # If relpath returned an absolute path (different drive on Windows),
            # fall back to just the filename
            if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
                rel = os.path.basename(local)
            dest = f"{base_remote}/{rel}" if base_remote != "/" else f"/{rel}"
            file_pairs.append((local, dest))

        self._log(f"[DEBUG] Upload path: {upload_path!r} → base_remote: {base_remote!r}")
        for local, dest in file_pairs[:3]:  # log first 3 so it's not overwhelming
            self._log(f"[DEBUG] Dest: {dest}")

        self.worker = UploadWorker(
            api_key, base_url, file_pairs,
            self.create_share_cb.isChecked(), expiry, max_dl
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.speed.connect(self._on_speed)
        self.worker.status.connect(self._log)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _cancel_upload(self):
        if self.worker:
            self.worker.cancel()
        self._set_uploading(False)
        self._badge("Cancelled", "#9ca3af")
        self._log("Upload cancelled by user.")

    def _set_uploading(self, active):
        self.upload_btn.setVisible(not active)
        self.cancel_btn.setVisible(active)
        self.upload_btn.setEnabled(not active)

    def _on_progress(self, pct):
        self.progress_bar.setValue(pct)
        self.pct_label.setText(f"{pct}%")

    def _on_speed(self, bps):
        if bps < 1024:
            txt = f"{bps:.0f} B/s"
        elif bps < 1024**2:
            txt = f"{bps/1024:.1f} KB/s"
        else:
            txt = f"{bps/1024**2:.2f} MB/s"
        self.speed_label.setText(txt)

    def _on_finished(self, result):
        self._set_uploading(False)
        self._badge("Complete", "#4ade80")
        self._log(f"✓ Done! File ID: {result['file_id']}")
        if result.get("share_url"):
            url = result["share_url"]
            self.share_result.setText(f'<a href="{url}" style="color:#c8975a;">{url}</a>')
            self.share_result.show()

    def _on_error(self, msg):
        self._set_uploading(False)
        self._badge("Error", "#f87171")
        self._log(f"✗ Error: {msg}")

    def _log(self, msg):
        self.log_label.setText(msg)
        try:
            with open("mocha_uploader.log", "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass

    def _badge(self, text, color):
        self.status_badge.setText(f"● {text}")
        self.status_badge.setStyleSheet(
            f"background-color: {color}22; border: 1px solid {color}55; "
            f"border-radius: 10px; color: {color}; font-size: 11px; "
            f"font-weight: 600; padding: 2px 10px;"
        )

    def _on_tab_changed(self, index):
        # Auto-refresh the Files tab when switched to (if API key is present)
        if index == 1 and self.api_key_edit.text().strip():
            self.files_tab._refresh()

    def closeEvent(self, event):
        self._save_settings()
        # Stop any running file-tab workers
        for w in list(self.files_tab._workers):
            w.quit()
        super().closeEvent(event)


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window,           QColor("#111214"))
    palette.setColor(QPalette.ColorRole.WindowText,       QColor("#e0e0e0"))
    palette.setColor(QPalette.ColorRole.Base,             QColor("#0e1012"))
    palette.setColor(QPalette.ColorRole.Text,             QColor("#e0e0e0"))
    palette.setColor(QPalette.ColorRole.Button,           QColor("#1a1c1f"))
    palette.setColor(QPalette.ColorRole.ButtonText,       QColor("#e0e0e0"))
    palette.setColor(QPalette.ColorRole.Highlight,        QColor("#c8975a"))
    palette.setColor(QPalette.ColorRole.HighlightedText,  QColor("#111214"))
    app.setPalette(palette)

    win = MochaUploader()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()


# ═══════════════════════════════════════════════════════════════════════════
# README / BUILD INSTRUCTIONS ANDROID
# ═══════════════════════════════════════════════════════════════════════════
# ANDROID
# -------
#   PyQt6 does NOT support Android. For Android, rewrite the UI layer using:
#     • Kivy (https://kivy.org) + Buildozer — pure Python, compiles to APK
#     • BeeWare Toga (https://beeware.org) — cross-platform including Android
#   The upload logic (UploadWorker class, requests calls) is fully portable
#   and can be reused in either framework with minimal changes.
#
# SETTINGS STORAGE
# ----------------
#   API key and settings are stored via QSettings:
#     • Windows : HKEY_CURRENT_USER\Software\Mocha\MochaUploader
#     • macOS   : ~/Library/Preferences/com.Mocha.MochaUploader.plist
#     • Linux   : ~/.config/Mocha/MochaUploader.ini
#   Only saved when "Remember settings across sessions" is checked.
#
# UPLOAD LOGIC
# ------------
#   ≤ 50 MB  → POST /api/files          (direct upload)
#   > 50 MB  → multipart: init → parts → complete
#              Each part is 20 MB. Abort is called on cancel.
#
# SHARE OPTIONS
# -------------
#   Expiration values are sent as-is to the API (e.g. "1d", "7d", "Never").
#   Max downloads = 0 means "Unlimited" (field omitted from request).
# ═══════════════════════════════════════════════════════════════════════════