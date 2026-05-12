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
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QCheckBox, QProgressBar,
    QFileDialog, QFrame, QSpinBox, QComboBox, QScrollArea,
    QSizePolicy, QMessageBox
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
CHUNK_THRESHOLD = 50 * 1024 * 1024   # 50 MB  → use multipart above this
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
"""

# ── Upload Worker ────────────────────────────────────────────────────────────
class UploadWorker(QThread):
    progress    = pyqtSignal(int)          # 0-100
    speed       = pyqtSignal(float)        # bytes/sec
    status      = pyqtSignal(str)          # log message
    finished    = pyqtSignal(dict)         # result dict
    error       = pyqtSignal(str)

    def __init__(self, api_key, base_url, upload_path, file_path,
                 create_share, share_expiry, share_max_downloads):
        super().__init__()
        self.api_key             = api_key
        self.base_url            = base_url.rstrip("/")
        # FIX: normalise upload_path so it always starts with "/" and never
        # has a trailing slash.  dest is then built as "<upload_path>/<name>",
        # which avoids the double-slash that caused 400s on both endpoints.
        self.upload_path         = "/" + upload_path.strip("/")
        self.file_path           = file_path
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

    def _dest_path(self, file_name):
        """Return a clean absolute destination path with no double slashes."""
        base = self.upload_path.rstrip("/")   # e.g. "" for root, "/folder"
        return f"{base}/{file_name}"          # always exactly one slash

    def run(self):
        try:
            file_size = os.path.getsize(self.file_path)
            self.status.emit(f"File size: {self._fmt_size(file_size)}")

            if file_size <= CHUNK_THRESHOLD:
                file_id = self._simple_upload(file_size)
            else:
                file_id = self._multipart_upload(file_size)

            if self._cancel or file_id is None:
                return

            result = {"file_id": file_id, "share_url": None}

            if self.create_share:
                self.status.emit("Creating share link…")
                share_url = self._create_share(file_id)
                result["share_url"] = share_url
                self.status.emit(f"Share: {share_url}")

            self.finished.emit(result)

        except Exception as e:
            self.error.emit(str(e))

    # ── simple upload (≤ 50 MB) ──────────────────────────────────────────────
    def _simple_upload(self, file_size):
        self.status.emit("Starting direct upload…")
        file_name = os.path.basename(self.file_path)
        dest      = self._dest_path(file_name)
        url       = f"{self.base_url}/api/files"

        start    = time.time()
        uploaded = 0

        # Read file in chunks so we can report live progress
        chunks = []
        with open(self.file_path, "rb") as f:
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

        # Debug: log request details (excluding sensitive data)
        self.status.emit(f"[DEBUG] Upload URL: {url}")
        self.status.emit(f"[DEBUG] Dest path: {dest}")
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
                    "path": (None, dest),
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
        return file_id

    # ── multipart upload (> 50 MB) ───────────────────────────────────────────
    def _multipart_upload(self, file_size):
        file_name   = os.path.basename(self.file_path)
        dest        = self._dest_path(file_name)
        total_parts = math.ceil(file_size / CHUNK_SIZE)
        self.status.emit(f"Multipart upload: {total_parts} parts…")

        # Debug: log request details (excluding sensitive data)
        url = f"{self.base_url}/api/files/multipart/init"
        payload = {
            "name": file_name,
            "originalName": file_name,
            "path": dest,
            "size": file_size,
        }
        debug_headers = {**self._headers(), "Content-Type": "application/json"}
        debug_headers["Authorization"] = "(hidden)"
        self.status.emit(f"[DEBUG] Multipart init URL: {url}")
        self.status.emit(f"[DEBUG] Payload: {payload}")
        self.status.emit(f"[DEBUG] Headers: {debug_headers}")
        try:
            init_resp = requests.post(
                url,
                headers={**self._headers(), "Content-Type": "application/json"},
                json=payload,
                timeout=30,
            )
            init_resp.raise_for_status()
        except requests.HTTPError as e:
            self.status.emit(f"[DEBUG] HTTPError: {e}")
            self.status.emit(f"[DEBUG] Response status: {getattr(e.response, 'status_code', None)}")
            self.status.emit(f"[DEBUG] Response content: {getattr(e.response, 'text', None)}")
            raise
        except Exception as e:
            self.status.emit(f"[DEBUG] Exception: {e}")
            raise
        init_data  = init_resp.json()
        self.status.emit(f"[DEBUG] Init response: {init_data}")
        upload_id  = init_data.get("uploadId")
        server_fid = init_data.get("fileId") or init_data.get("id") or (init_data.get("file") or {}).get("id")
        self.status.emit(f"Session: {upload_id}")

        parts    = []
        uploaded = 0
        start    = time.time()

        with open(self.file_path, "rb") as f:
            for part_num in range(1, total_parts + 1):
                if self._cancel:
                    self._abort(upload_id, server_fid)
                    return None

                chunk = f.read(CHUNK_SIZE)
                self.status.emit(f"Uploading part {part_num}/{total_parts}…")

                part_url = f"{self.base_url}/api/files/multipart/part"
                part_params = {
                    "uploadId":   upload_id,
                    "partNumber": part_num,
                }
                if server_fid:
                    part_params["fileId"] = server_fid
                part_headers = self._headers()
                self.status.emit(f"[DEBUG] Part upload URL: {part_url}")
                self.status.emit(f"[DEBUG] Params: {part_params}")
                self.status.emit(f"[DEBUG] Headers: {{'Authorization': '(hidden)'}}")
                self.status.emit(f"[DEBUG] Chunk size: {len(chunk)} bytes")
                try:
                    part_resp = requests.put(
                        part_url,
                        headers=part_headers,
                        params=part_params,
                        data=chunk,
                        timeout=120,
                    )
                    part_resp.raise_for_status()
                except requests.HTTPError as e:
                    self.status.emit(f"[DEBUG] HTTPError: {e}")
                    self.status.emit(f"[DEBUG] Response status: {getattr(e.response, 'status_code', None)}")
                    self.status.emit(f"[DEBUG] Response content: {getattr(e.response, 'text', None)}")
                    raise
                except Exception as e:
                    self.status.emit(f"[DEBUG] Exception: {e}")
                    raise
                etag = part_resp.headers.get("ETag", "")
                parts.append({"partNumber": part_num, "etag": etag})

                uploaded += len(chunk)
                elapsed   = max(time.time() - start, 0.001)
                self.progress.emit(int(uploaded / file_size * 100))
                self.speed.emit(uploaded / elapsed)

        # 3. Complete
        comp_resp = requests.post(
            f"{self.base_url}/api/files/multipart/complete",
            headers={**self._headers(), "Content-Type": "application/json"},
            json={"uploadId": upload_id, "fileId": server_fid, "parts": parts},
            timeout=60,
        )
        comp_resp.raise_for_status()
        j       = comp_resp.json()
        file_id = j.get("fileId") or server_fid
        self.status.emit(f"Multipart complete. File ID: {file_id}")
        self.progress.emit(100)
        return file_id

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
    file_dropped = pyqtSignal(str)

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
        rest = QLabel("or drag & drop a file here")
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
        path, _ = QFileDialog.getOpenFileName(self, "Select file")
        if path:
            self._set_file(path)

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
        if urls:
            path = urls[0].toLocalFile()
            if os.path.isfile(path):
                self._set_file(path)

    def _set_file(self, path):
        name  = os.path.basename(path)
        size  = os.path.getsize(path)
        label = f"{name}  ({UploadWorker._fmt_size(size)})"
        self.file_label.setText(label)
        self.file_dropped.emit(path)


# ── Main Window ──────────────────────────────────────────────────────────────
class MochaUploader(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Mocha Uploader")
        self.setMinimumWidth(520)
        self.setMaximumWidth(640)
        self.selected_file = None
        self.worker        = None
        self.settings      = QSettings(ORG_NAME, APP_NAME)
        self._build_ui()
        self._load_settings()

    # ── UI construction ──────────────────────────────────────────────────────
    def _build_ui(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        main  = QVBoxLayout(inner)
        main.setContentsMargins(16, 16, 16, 20)
        main.setSpacing(12)

        scroll.setWidget(inner)

        root_lay = QVBoxLayout(root)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.addWidget(scroll)

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
        self.upload_path_edit.setText("/")
        path_row.addWidget(path_lbl)
        path_row.addWidget(self.upload_path_edit, 1)
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
        self.drop_zone.file_dropped.connect(self._on_file_selected)
        file_lay.addWidget(self.drop_zone)
        main.addWidget(file_card)

        # ── UPLOAD STATUS ─────────────────────────────────────────────────────
        main.addWidget(self._make_section_header("Upload Status"))
        status_card = self._make_card()
        status_lay  = QVBoxLayout(status_card)
        status_lay.setSpacing(8)

        # Badge + speed row
        top_row = QHBoxLayout()
        self.status_badge = QLabel("● Idle")
        self.status_badge.setObjectName("status_badge")
        self.speed_label  = QLabel("")
        self.speed_label.setObjectName("status_label")
        self.speed_label.setStyleSheet("color: #9ca3af; font-size: 11px; background:transparent;")
        top_row.addWidget(self.status_badge)
        top_row.addStretch()
        top_row.addWidget(self.speed_label)
        status_lay.addLayout(top_row)

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

    def _toggle_share_options(self, checked):
        self.share_opts_widget.setVisible(checked)

    def _on_file_selected(self, path):
        self.selected_file = path
        self._log(f"Selected: {os.path.basename(path)}")
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
        base_url    = HARDCODED_BASE_URL  # always use hardcoded
        upload_path = self.upload_path_edit.text().strip() or "/"

        if not api_key:
            self._log("⚠ Please enter an API key.")
            return
        # Remove base_url check
        if not self.selected_file:
            self._log("⚠ Please select a file.")
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

        self.worker = UploadWorker(
            api_key, base_url, upload_path, self.selected_file,
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

    def closeEvent(self, event):
        self._save_settings()
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
