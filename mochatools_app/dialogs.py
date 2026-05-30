import requests

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)


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
            QListWidget { background:#141210; border:1px solid #2e2b27;
                          border-radius:0px; color:#f0ece6; font-size:13px; }
            QListWidget::item { padding:6px 10px; }
            QListWidget::item:selected { background:#c8a96e33; color:#f0ece6; }
            QListWidget::item:hover { background:#1e1c19; }
        """)
        self.list.itemDoubleClicked.connect(self._on_double_click)
        lay.addWidget(self.list)

        # Status
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color:#9c9484; font-size:11px; background:transparent;")
        lay.addWidget(self.status_lbl)

        # Buttons — manual layout so both buttons get identical sizing
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedHeight(38)
        cancel_btn.setMinimumWidth(100)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        ok_btn = QPushButton("Select this folder")
        ok_btn.setObjectName("upload_btn")
        ok_btn.setFixedHeight(38)
        ok_btn.setMinimumWidth(150)
        ok_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(ok_btn)

        lay.addLayout(btn_row)

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
            if hasattr(e, "response") and e.response is not None:
                self.status_lbl.setText(
                    f"Error {e.response.status_code}: {e.response.text[:200]}"
                )
            return

        # Add ".." entry unless we're at root
        if path and path != "/":
            parent = "/" + "/".join(path.strip("/").split("/")[:-1])
            parent = parent if parent != "/" else "/"
            item = QListWidgetItem("↑  .. (go up)")
            item.setData(Qt.ItemDataRole.UserRole, ("dir", parent))
            item.setForeground(QColor("#9c9484"))
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
                name = entry.rstrip("/").split("/")[-1]
                # API returns bare names, not full paths; build the full path ourselves.
                if entry.startswith("/"):
                    fullpath = entry  # already absolute
                else:
                    fullpath = (path.rstrip("/") + "/" + name) if path != "/" else ("/" + name)
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
        self.status_lbl.setText(f"{count} folder{'s' if count != 1 else ''}")

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

# ── Share Link Dialog ─────────────────────────────────────────────────────────
class ShareLinkDialog(QDialog):
    """Modal dialog that displays a freshly created share URL with a Copy button."""

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self.url = url
        self.setWindowTitle("Share Link Created")
        self.setModal(True)
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        # Header
        header = QLabel("✓  Share link ready")
        header.setStyleSheet("color:#4ade80; font-size:14px; font-weight:700; background:transparent;")
        layout.addWidget(header)

        # URL box (read-only, selectable)
        self.url_edit = QLineEdit(url)
        self.url_edit.setReadOnly(True)
        self.url_edit.setStyleSheet(
            "background:#08090b; border:1px solid #35101a; border-radius:0px;"
            "padding:8px 10px; color:#c8a96e; font-family:'Consolas','Fira Code','Courier New',monospace;"
            "font-size:12px;"
        )
        layout.addWidget(self.url_edit)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.copy_btn = QPushButton("⧉  Copy URL")
        self.copy_btn.setObjectName("upload_btn")
        self.copy_btn.setFixedHeight(36)
        self.copy_btn.clicked.connect(self._copy)
        btn_row.addWidget(self.copy_btn)

        open_btn = QPushButton("↗  Open in browser")
        open_btn.setObjectName("browse_btn")
        open_btn.setFixedHeight(36)
        open_btn.clicked.connect(lambda: __import__("webbrowser").open(url))
        btn_row.addWidget(open_btn)

        close_btn = QPushButton("Close")
        close_btn.setObjectName("browse_btn")
        close_btn.setFixedHeight(36)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    def _copy(self):
        QApplication.clipboard().setText(self.url)
        self.copy_btn.setText("✓  Copied!")
        QTimer.singleShot(2000, lambda: self.copy_btn.setText("⧉  Copy URL"))