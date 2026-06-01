# mochatools_app/dialogs_android.py
import requests
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)


class FolderBrowserDialog(QDialog):
    """Identical logic to the desktop version; only imports changed."""

    def __init__(self, api_key, base_url, current_path="/", parent=None):
        super().__init__(parent)
        self.api_key  = api_key
        self.base_url = base_url.rstrip("/")
        self.current  = current_path or "/"
        self.selected = self.current
        self.setWindowTitle("Browse remote folders")
        self.setMinimumSize(340, 480)   # taller for touch scrolling
        if parent:
            self.setStyleSheet(parent.styleSheet())

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        self.path_label = QLabel()
        self.path_label.setObjectName("section_header")
        lay.addWidget(self.path_label)

        self.list = QListWidget()
        self.list.setStyleSheet("""
            QListWidget { background:#141210; border:1px solid #2e2b27;
                          color:#f0ece6; font-size:14px; }
            QListWidget::item { padding:10px 10px; }
            QListWidget::item:selected { background:#c8a96e33; }
            QListWidget::item:hover { background:#1e1c19; }
        """)
        self.list.itemDoubleClicked.connect(self._on_double_click)
        lay.addWidget(self.list)

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color:#9c9484; font-size:11px; background:transparent;")
        lay.addWidget(self.status_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedSize(140, 48)
        cancel_btn.setStyleSheet(
            "min-height:0; padding:0 16px; font-size:14px; font-weight:600;"
            "background:#1e1c19; color:#f0ece6; border:1px solid #3d3a35; border-radius:7px;")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        ok_btn = QPushButton("Select this folder")
        ok_btn.setObjectName("upload_btn")
        ok_btn.setFixedSize(180, 48)
        ok_btn.setStyleSheet(
            "min-height:0; padding:0 16px; font-size:14px; font-weight:700;"
            "background:#c8a96e; color:#111010; border:none; border-radius:7px;")
        ok_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(ok_btn)
        lay.addLayout(btn_row)

        self._navigate(self.current)

    def _navigate(self, path):
        self.current  = path
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
            return

        if path and path != "/":
            parent = "/" + "/".join(path.strip("/").split("/")[:-1])
            parent = parent if parent != "/" else "/"
            item = QListWidgetItem("↑  .. (go up)")
            item.setData(Qt.ItemDataRole.UserRole, ("dir", parent))
            item.setForeground(QColor("#9c9484"))
            self.list.addItem(item)

        folders = []
        folder_entries = data.get("folders") if isinstance(data, dict) else []
        if not folder_entries and isinstance(data, list):
            folder_entries = data
        for entry in (folder_entries or []):
            if isinstance(entry, str):
                name = entry.rstrip("/").split("/")[-1]
                fullpath = (path.rstrip("/") + "/" + name) if path != "/" else "/" + name
            elif isinstance(entry, dict):
                name = (entry.get("name") or entry.get("original_name") or
                        entry.get("originalName") or "")
                fullpath = entry.get("path") or (path.rstrip("/") + "/" + name)
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
        self.selected = sel.data(Qt.ItemDataRole.UserRole)[1] if sel else self.current
        self.accept()


class ShareLinkDialog(QDialog):
    """Identical logic to desktop version; only imports changed."""

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self.url = url
        self.setWindowTitle("Share Link Created")
        self.setModal(True)
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        header = QLabel("✓  Share link ready")
        header.setStyleSheet("color:#4ade80; font-size:14px; font-weight:700; background:transparent;")
        layout.addWidget(header)

        self.url_edit = QLineEdit(url)
        self.url_edit.setReadOnly(True)
        self.url_edit.setStyleSheet(
            "background:#08090b; border:1px solid #35101a; border-radius:0px;"
            "padding:8px 10px; color:#c8a96e; font-size:12px;")
        layout.addWidget(self.url_edit)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.copy_btn = QPushButton("⧉  Copy URL")
        self.copy_btn.setObjectName("upload_btn")
        self.copy_btn.setMinimumHeight(48)
        self.copy_btn.clicked.connect(self._copy)
        btn_row.addWidget(self.copy_btn)
        open_btn = QPushButton("↗  Open")
        open_btn.setObjectName("browse_btn")
        open_btn.setMinimumHeight(48)
        open_btn.clicked.connect(lambda: __import__("webbrowser").open(url))
        btn_row.addWidget(open_btn)
        close_btn = QPushButton("Close")
        close_btn.setObjectName("browse_btn")
        close_btn.setMinimumHeight(48)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _copy(self):
        QApplication.clipboard().setText(self.url)
        self.copy_btn.setText("✓  Copied!")
        QTimer.singleShot(2000, lambda: self.copy_btn.setText("⧉  Copy URL"))
