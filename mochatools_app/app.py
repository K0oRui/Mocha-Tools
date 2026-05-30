"""
Mocha Tools
A cross-platform PyQt6 application for uploading files to mocha
Written by nxllxvxxd && Bink-lab
To compile:
    pyinstaller --onefile --windowed --noconsole mochatools.py

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
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, unquote

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

from .constants import (
    APP_NAME,
    CHUNK_SIZE,
    DEFAULT_CHUNK_SIZE_MB,
    DEFAULT_MAX_CHUNKS,
    HARDCODED_BASE_URL,
    ORG_NAME,
    PART_UPLOAD_RETRIES,
    PART_UPLOAD_TIMEOUT,
    RELAY_DEFAULT_CONCURRENCY,
    RELAY_MAX_CONCURRENCY,
    S3_DEFAULT_CONCURRENCY,
    S3_MAX_CONCURRENCY,
)
from .logging_utils import write_debug_log

from .styles import STYLESHEET


from .workers import FilesWorker, RemoteWorker, UploadWorker


from .dialogs import FolderBrowserDialog, ShareLinkDialog
from .updater import UpdateCheckWorker, UpdateDownloadWorker
from .constants import APP_VERSION


# ── Drop Zone Widget ─────────────────────────────────────────────────────────
class DropZone(QFrame):
    # Emits (file_list, root) — root is the authoritative base for relpath so
    # commonpath guessing is never needed (fixes folder-upload path stripping).
    selection_changed = pyqtSignal(list, str)

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
        icon.setStyleSheet("color: #4a4a4a; font-size: 24px; background: transparent;")

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
        self.file_label.setStyleSheet("color: #c8a96e; font-size: 12px; font-weight:600; background:transparent;")
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
                self._set_paths([path], os.path.dirname(path), is_folder=False)
        elif chosen == act_folder:
            path = QFileDialog.getExistingDirectory(self, "Select folder")
            if path:
                files = self._collect_folder(path)
                if files:
                    self._set_paths(files, path, is_folder=True)

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
            self._set_paths([path], os.path.dirname(path), is_folder=False)
        elif os.path.isdir(path):
            files = self._collect_folder(path)
            if files:
                self._set_paths(files, path, is_folder=True)

    @staticmethod
    def _collect_folder(folder_path):
        """Recursively collect all files under folder_path, sorted."""
        result = []
        for dirpath, _dirnames, filenames in os.walk(folder_path):
            for fname in filenames:
                result.append(os.path.join(dirpath, fname))
        return sorted(result)

    def _set_paths(self, file_list, root, is_folder=False):
        if not file_list:
            return
        name = os.path.basename(root.rstrip("/\\"))
        if len(file_list) == 1 and not is_folder:
            # Single file was selected
            size  = os.path.getsize(file_list[0])
            label = f"{os.path.basename(file_list[0])}  ({UploadWorker._fmt_size(size)})"
            selected_root = root
        else:
            # Folder was selected (may contain 1 or more files)
            total = sum(os.path.getsize(p) for p in file_list)
            label = f"{name}/  —  {len(file_list)} files  ({UploadWorker._fmt_size(total)})"
            # For a folder, set selected_root to the parent so the folder name is preserved
            selected_root = os.path.dirname(root.rstrip("/\\"))
        self.file_label.setText(label)
        self.selection_changed.emit(file_list, selected_root)

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

    def __init__(self, get_api_key, get_upload_path, set_upload_path, parent=None):
        super().__init__(parent)
        self.get_api_key      = get_api_key
        self.get_upload_path  = get_upload_path
        self.set_upload_path  = set_upload_path
        self.base_url         = HARDCODED_BASE_URL
        self.current_path     = "/"
        self._workers         = []
        self._shares_map      = {}

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
            Qt.TextInteractionFlag.TextSelectableByKeyboard |
            Qt.TextInteractionFlag.LinksAccessibleByMouse |
            Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        self.share_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
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
            self._status("⚠ Enter your API key in the Settings tab first.")
            return
        self.current_path = path
        self.path_edit.setText(path)
        self._status("Loading…")
        self.tree.clear()
        self.share_bar.hide()

        write_debug_log(f"[DEBUG] _navigate: navigating to path={path!r}")

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
            self._status("✓ Share created")
            if url:
                dlg = ShareLinkDialog(url, parent=self)
                dlg.exec()
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

        write_debug_log(f"[DEBUG] _populate: path={path!r}, raw_folders={raw_folders}")

        # ── Folders ──
        for entry in raw_folders:
            if isinstance(entry, str):
                name = entry.rstrip("/").split("/")[-1]
                # API returns bare names with no parent path; build the full path ourselves.
                if entry.startswith("/"):
                    fullpath = entry  # already absolute
                else:
                    fullpath = (path.rstrip("/") + "/" + name) if path != "/" else ("/" + name)
                write_debug_log(f"[DEBUG]   String folder entry: {entry!r} -> fullpath={fullpath!r}")
                folders.append({"name": name, "path": fullpath})
            elif isinstance(entry, dict):
                entry_name = entry.get("name")
                entry_path = entry.get("path")
                name = (entry_name or entry.get("originalName")
                        or entry_path.rstrip("/").split("/")[-1] if entry_path else "")
                # ALWAYS compute fullpath based on current path if entry.path is not absolute
                if entry_path and entry_path.startswith("/"):
                    fullpath = entry_path
                else:
                    fullpath = f"{path.rstrip('/')}/{name}" if path != "/" else f"/{name}"
                write_debug_log(f"[DEBUG]   Dict folder: name={name!r}, entry.path={entry_path!r}, current_path={path!r}, computed fullpath={fullpath!r}")
                # Important: put **entry first, then override with our computed path
                folder_data = {**entry, "_type": "folder", "name": name, "path": fullpath}
                folders.append(folder_data)

        # ── Files ──
        for entry in raw_files:
            if isinstance(entry, dict):
                # Skip entries that look like folders in a flat list
                if entry.get("type") == "folder" or entry.get("isFolder"):
                    name     = (entry.get("name") or
                                entry.get("path", "").rstrip("/").split("/")[-1])
                    entry_path = entry.get("path")
                    if entry_path and entry_path.startswith("/"):
                        fullpath = entry_path
                    else:
                        fullpath = f"{path.rstrip('/')}/{name}" if path != "/" else f"/{name}"
                    # Override with our computed path
                    folder_data = {**entry, "name": name, "path": fullpath}
                    folders.append(folder_data)
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
            item.setForeground(0, QColor("#c8a96e"))
            self.tree.addTopLevelItem(item)

        # Add file rows
        for f in sorted(files, key=lambda x: (
                x.get("originalName") or x.get("original_name") or x.get("name") or x.get("file_name") or "").lower()):
            stored_name = f.get("file_name") or f.get("name") or ""
            name    = (f.get("originalName") or f.get("original_name")
                       or f.get("name") or stored_name)
            size    = f.get("size") or f.get("fileSize") or 0
            fid     = f.get("id") or f.get("fileId") or ""
            expires = f.get("expiresAt") or f.get("expiry") or "—"
            if expires and expires != "—":
                expires = expires[:10] if len(expires) > 10 else expires

            item = QTreeWidgetItem([
                f"  {name}",
                UploadWorker._fmt_size(int(size)) if size else "—",
                "file",
                "",          # shared — filled after shares load
                expires,
            ])
            item.setData(0, Qt.ItemDataRole.UserRole,
                         {**f, "_type": "file", "name": name, "id": fid,
                          "file_name": stored_name,
                          "path": f.get("path") or f"{path.rstrip('/')}/{stored_name or name}"})
            self.tree.addTopLevelItem(item)

        self.tree.setSortingEnabled(True)
        self._status(f"{len(folders)} folder{'s' if len(folders)!=1 else ''}, "
                     f"{len(files)} file{'s' if len(files)!=1 else ''}")
        self._set_action_btns_enabled(False)
        self._refresh_share_indicators()

    def _index_shares(self, data):
        """Build file reference → share_url map from GET /api/shares response."""
        self._shares_map = {}
        items = data if isinstance(data, list) else data.get("shares", [])
        for s in items:
            fid   = (s.get("fileId") or
                     (s.get("file") or {}).get("id") or "")
            file_name = s.get("fileName") or s.get("file_name") or ""
            token = s.get("token", "")
            share = {
                "url":     f"{self.base_url}/share/{token}" if token else "",
                "token":   token,
                "expires": s.get("expiresAt") or s.get("expires_at") or s.get("expiry") or "—",
                "active":  s.get("active", s.get("is_active", True)),
            }
            for key in (fid, file_name):
                if key:
                    self._shares_map[key] = share

    def _refresh_share_indicators(self):
        """Update the Shared column for all file rows based on _shares_map."""
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            item = root.child(i)
            meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
            if meta.get("_type") != "file":
                continue
            fid = meta.get("id") or meta.get("fileId") or ""
            file_name = meta.get("file_name") or meta.get("name") or ""
            share = self._shares_map.get(fid) or self._shares_map.get(file_name)
            if share:
                label = "● Shared" if share.get("active", True) else "○ Inactive"
                color = "#4ade80" if share.get("active", True) else "#9ca3af"
                item.setText(3, label)
                item.setForeground(3, QColor(color))
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
        single      = len(items) == 1
        single_file = single and items[0].data(0, Qt.ItemDataRole.UserRole).get("_type") == "file"
        single_item = single  # files or folders can be moved
        self.move_btn.setEnabled(single_item)
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
                file_name = meta.get("file_name") or meta.get("name") or meta.get("path", "").lstrip("/")
                self._run_worker("delete", file_name=file_name)
        self._status("Deleting…")

    def _move_selected(self):
        items = self._selected_items()
        if len(items) != 1:
            return
        meta      = items[0].data(0, Qt.ItemDataRole.UserRole) or {}
        is_folder = meta.get("_type") == "folder"
        fid       = meta.get("id") or meta.get("fileId") or ""
        src       = meta.get("path") or meta.get("name") or ""
        # Folder source path must have trailing slash for the API
        if is_folder and src and not src.endswith("/"):
            src = src + "/"

        dlg = FolderBrowserDialog(self.get_api_key(), self.base_url,
                                  self.current_path, parent=self)
        dlg.setWindowTitle("Move — choose destination folder")
        if not dlg.exec():
            return
        dest_folder = dlg.selected.rstrip("/") + "/"
        self._status(f"Moving to {dest_folder}…")
        self._run_worker("move", file_id=fid, source_path=src,
                         new_path=dest_folder, is_folder=is_folder)

    def _share_selected(self):
        items = self._selected_items()
        if len(items) != 1:
            return
        meta = items[0].data(0, Qt.ItemDataRole.UserRole) or {}
        fid  = meta.get("id") or meta.get("fileId") or ""
        name = meta.get("name") or ""

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
                    f'Share link: <a href="{existing_url}" style="color:#c8a96e;">'
                    f'{existing_url}</a>')
                self.share_bar.show()
                return
            elif ans == QMessageBox.StandardButton.Cancel:
                return

        expiry, ok = QInputDialog.getItem(
            self, "Share Expiry", "Expiration:",
            ["Never", "1h", "6h", "12h", "1d", "3d", "7d", "14d", "30d"],
            editable=False,
        )
        if not ok:
            return

        self._status(f"Creating share for {name!r}…")
        self._run_worker("share", file_id=fid, expiry=expiry)

    def _download_selected(self):
        items = self._selected_items()
        if len(items) != 1:
            return
        meta = items[0].data(0, Qt.ItemDataRole.UserRole) or {}
        fid  = meta.get("id") or meta.get("fileId") or ""
        if not fid:
            QMessageBox.warning(self, "Download", "Cannot determine file ID.")
            return
        # Fetch a presigned URL server-side (auth header sent here),
        # then open it in the browser — no API key needed in the browser.
        api_key = self.get_api_key()
        try:
            resp = requests.get(
                f"{self.base_url}/api/files/presigned",
                headers={"Authorization": f"Bearer {api_key}"},
                params={"fileId": fid},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            url  = data.get("url") or data.get("presignedUrl") or data.get("downloadUrl") or ""
            if not url:
                QMessageBox.warning(self, "Download", f"No download URL returned: {data}")
                return
        except Exception as e:
            QMessageBox.warning(self, "Download", f"Failed to get download URL: {e}")
            return
        import webbrowser
        webbrowser.open(url)

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if meta.get("_type") not in ("file", "folder"):
            return

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background:#1f1f1f; border:1px solid #3a3a3a; border-radius:8px;
                    color:#f0f0f0; font-size:12px; }
            QMenu::item { padding:6px 24px; }
            QMenu::item:selected { background:#c8a96e33; }
        """)

        if meta.get("_type") == "file":
            menu.addAction("⬇  Download", self._download_selected)
            menu.addAction("⤴  Share",    self._share_selected)
        menu.addAction("↦  Move", self._move_selected)
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


# ── Remote Tab ───────────────────────────────────────────────────────────────
class RemoteTab(QWidget):
    """Starts server-side remote downloads and displays transfer jobs."""

    def __init__(self, get_api_key, parent=None):
        super().__init__(parent)
        self.get_api_key = get_api_key
        self.base_url    = HARDCODED_BASE_URL
        self._workers    = []
        self._is_active  = False
        self._watched_jobs = {}
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        ingest_card = self._make_card()
        ingest_lay = QVBoxLayout(ingest_card)
        ingest_lay.setSpacing(8)

        url_row = QHBoxLayout()
        url_lbl = QLabel("URL")
        url_lbl.setObjectName("field_label")
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://example.com/big-file.zip")
        url_row.addWidget(url_lbl)
        url_row.addWidget(self.url_edit, 1)
        ingest_lay.addLayout(url_row)

        name_row = QHBoxLayout()
        name_lbl = QLabel("Filename")
        name_lbl.setObjectName("field_label")
        self.file_name_edit = QLineEdit()
        self.file_name_edit.setPlaceholderText("Leave blank to use the URL filename")
        name_row.addWidget(name_lbl)
        name_row.addWidget(self.file_name_edit, 1)
        ingest_lay.addLayout(name_row)

        dest_row = QHBoxLayout()
        dest_lbl = QLabel("Folder")
        dest_lbl.setObjectName("field_label")
        self.path_edit = QLineEdit()
        self.path_edit.setText("/")
        browse_btn = QPushButton("Browse…")
        browse_btn.setObjectName("browse_btn")
        browse_btn.clicked.connect(self._browse_dest)
        dest_row.addWidget(dest_lbl)
        dest_row.addWidget(self.path_edit, 1)
        dest_row.addWidget(browse_btn)
        ingest_lay.addLayout(dest_row)

        self.ingest_btn = QPushButton("⇣  Remote ingest")
        self.ingest_btn.setObjectName("upload_btn")
        self.ingest_btn.setMinimumHeight(40)
        self.ingest_btn.clicked.connect(self._start_ingest)
        ingest_lay.addWidget(self.ingest_btn)

        self.result_bar = QLabel("")
        self.result_bar.setObjectName("log_console")
        self.result_bar.setWordWrap(True)
        self.result_bar.hide()
        ingest_lay.addWidget(self.result_bar)
        outer.addWidget(ingest_card)

        tb = QHBoxLayout()
        tb.setSpacing(4)
        self.refresh_btn = QPushButton("↺  Refresh Jobs")
        self.refresh_btn.setObjectName("tb_btn")
        self.refresh_btn.clicked.connect(self.refresh_jobs)
        tb.addWidget(self.refresh_btn)

        self.cancel_btn = QPushButton("✕  Cancel Job")
        self.cancel_btn.setObjectName("tb_btn_danger")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_selected)
        tb.addWidget(self.cancel_btn)

        self.active_only_cb = QCheckBox("Active only")
        self.active_only_cb.setChecked(True)
        self.active_only_cb.toggled.connect(lambda _: self.refresh_jobs())
        tb.addWidget(self.active_only_cb)
        tb.addStretch()

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color:#9ca3af; font-size:11px; background:transparent;")
        tb.addWidget(self.status_lbl)
        outer.addLayout(tb)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["File", "Status", "Progress", "Job ID"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSortingEnabled(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        hdr = self.tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        outer.addWidget(self.tree, 1)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(5000)
        self.refresh_timer.timeout.connect(self.refresh_jobs)

    def _make_card(self):
        frame = QFrame()
        frame.setObjectName("card")
        return frame

    def _browse_dest(self):
        api_key = self.get_api_key()
        if not api_key:
            self._status("⚠ Enter your API key in Settings first.")
            return
        dlg = FolderBrowserDialog(
            api_key,
            self.base_url,
            self.path_edit.text().strip() or "/",
            parent=self,
        )
        dlg.setWindowTitle("Choose remote ingest destination")
        if dlg.exec():
            self.path_edit.setText(dlg.selected)

    def _start_ingest(self):
        api_key = self.get_api_key()
        source_url = self.url_edit.text().strip()
        if not api_key:
            self._status("⚠ Enter your API key in Settings first.")
            return
        if not source_url:
            self._status("⚠ Paste a source URL first.")
            return
        file_name = self.file_name_edit.text().strip() or self._filename_from_url(source_url)
        if not file_name:
            self._status("⚠ Enter a filename for this URL.")
            return

        self.result_bar.hide()
        self.ingest_btn.setEnabled(False)
        self._status("Starting remote ingest…")
        self._run_worker(
            "ingest",
            source_url=source_url,
            file_name=file_name,
            path=self._normalized_path(),
        )

    def refresh_jobs(self):
        if not self.get_api_key():
            self._status("⚠ Enter your API key in Settings first.")
            return
        self._status("Loading jobs…")
        self._run_worker("jobs", active_only=self.active_only_cb.isChecked())

    def _cancel_selected(self):
        meta = self._selected_meta()
        if not meta:
            return
        if QMessageBox.question(
            self,
            "Cancel Transfer",
            f"Cancel transfer job {meta['job_id']!r}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._status("Cancelling job…")
        self._run_worker("cancel", job_id=meta["job_id"])

    def _run_worker(self, op, **kwargs):
        w = RemoteWorker(op, self.get_api_key(), self.base_url, **kwargs)
        w.done.connect(self._on_done)
        w.error.connect(self._on_error)
        w.finished.connect(lambda: self._workers.remove(w) if w in self._workers else None)
        self._workers.append(w)
        w.start()

    def _on_done(self, result):
        op = result.get("op")
        if op == "ingest":
            self.ingest_btn.setEnabled(True)
            data = result.get("data") or {}
            job_id = data.get("jobId") or data.get("id") or ""
            original_name = data.get("originalName") or data.get("fileName") or self.file_name_edit.text().strip()
            self.result_bar.setText(f"Queued: {original_name}  Job: {job_id or '—'}")
            self.result_bar.show()
            self._status("✓ Remote ingest queued")
            if job_id:
                self._watched_jobs[str(job_id)] = {
                    "name": original_name,
                    "seen": False,
                    "checks": 0,
                }
            if self._is_active:
                self.refresh_timer.start()
            self.refresh_jobs()
        elif op == "jobs":
            self._populate_jobs(result.get("data"))
        elif op == "cancel":
            self._watched_jobs.pop(str(result.get("job_id", "")), None)
            self._status("✓ Job cancelled")
            self.refresh_jobs()

    def _on_error(self, msg):
        self.ingest_btn.setEnabled(True)
        self._status(f"✗ {msg}")
        QMessageBox.warning(self, "Remote Ingest Error", msg)

    def _populate_jobs(self, data):
        jobs = data.get("jobs", data) if isinstance(data, dict) else data
        if not isinstance(jobs, list):
            jobs = []

        self.tree.setSortingEnabled(False)
        self.tree.clear()
        active_job_ids = set()
        for job in jobs:
            if not isinstance(job, dict):
                continue
            job_id = job.get("id") or job.get("jobId") or job.get("job_id") or ""
            if job_id:
                active_job_ids.add(str(job_id))
            name = (
                job.get("originalName")
                or job.get("fileName")
                or job.get("file_name")
                or job.get("name")
                or job.get("sourceUrl")
                or "—"
            )
            status = job.get("status") or job.get("state") or "—"
            progress = job.get("progress")
            if progress is None:
                progress = job.get("percent") or job.get("progressPercent")
            progress_text = f"{progress}%" if progress not in (None, "") else "—"

            item = QTreeWidgetItem([str(name), str(status), str(progress_text), str(job_id)])
            item.setData(0, Qt.ItemDataRole.UserRole, {**job, "job_id": str(job_id)})
            if str(status).lower() in ("failed", "error", "cancelled", "canceled"):
                item.setForeground(1, QColor("#f87171"))
            elif str(status).lower() in ("complete", "completed", "done", "success"):
                item.setForeground(1, QColor("#4ade80"))
            else:
                item.setForeground(1, QColor("#e11d48"))
            self.tree.addTopLevelItem(item)

        self.tree.setSortingEnabled(True)
        count = self.tree.topLevelItemCount()
        self._status(f"{count} job{'s' if count != 1 else ''}")
        self._update_watched_jobs(active_job_ids)
        if self._is_active and self.active_only_cb.isChecked() and count:
            self.refresh_timer.start()
        else:
            self.refresh_timer.stop()
        self._on_selection_changed()

    def _on_selection_changed(self):
        meta = self._selected_meta()
        self.cancel_btn.setEnabled(bool(meta and meta.get("job_id")))

    def _selected_meta(self):
        items = self.tree.selectedItems()
        return items[0].data(0, Qt.ItemDataRole.UserRole) if items else None

    def _normalized_path(self):
        path = self.path_edit.text().strip() or "/"
        if not path.startswith("/"):
            path = "/" + path
        return path.rstrip("/") + "/"

    @staticmethod
    def _filename_from_url(source_url):
        parsed = urlparse(source_url)
        return unquote(os.path.basename(parsed.path.rstrip("/")))

    def _update_watched_jobs(self, active_job_ids):
        if not self.active_only_cb.isChecked():
            return
        finished = []
        for job_id, state in self._watched_jobs.items():
            if job_id in active_job_ids:
                state["seen"] = True
                continue
            state["checks"] += 1
            if state["seen"] or state["checks"] >= 2:
                finished.append(job_id)
        for job_id in finished:
            state = self._watched_jobs.pop(job_id)
            self._notify_ingest_finished(state["name"], job_id)

    def _notify_ingest_finished(self, name, job_id):
        self.result_bar.setText(f"Finished: {name}  Job: {job_id}")
        self.result_bar.show()
        self._status(f"✓ Remote ingest finished: {name}")
        if self._is_active:
            QMessageBox.information(self, "Remote Ingest Finished", f"{name} finished ingesting.")

    def set_active(self, active):
        self._is_active = active
        if active:
            self.refresh_jobs()
        else:
            self.refresh_timer.stop()

    def _status(self, msg):
        self.status_lbl.setText(msg)



# ── Shares Tab ────────────────────────────────────────────────────────────────
class SharesTab(QWidget):
    """Lists all active shares with copy-link and delete actions."""

    def __init__(self, get_api_key, parent=None):
        super().__init__(parent)
        self.get_api_key = get_api_key
        self.base_url    = HARDCODED_BASE_URL
        self._workers    = []
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        # ── Toolbar ──────────────────────────────────────────────────────────
        tb = QHBoxLayout()
        tb.setSpacing(4)

        self.refresh_btn = QPushButton("↺  Refresh")
        self.refresh_btn.setObjectName("tb_btn")
        self.refresh_btn.clicked.connect(self.refresh)
        tb.addWidget(self.refresh_btn)

        self.copy_btn = QPushButton("⧉  Copy Link")
        self.copy_btn.setObjectName("tb_btn")
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._copy_selected)
        tb.addWidget(self.copy_btn)

        self.toggle_btn = QPushButton("◎  Toggle Active")
        self.toggle_btn.setObjectName("tb_btn")
        self.toggle_btn.setEnabled(False)
        self.toggle_btn.clicked.connect(self._toggle_selected)
        tb.addWidget(self.toggle_btn)

        self.delete_btn = QPushButton("✕  Delete")
        self.delete_btn.setObjectName("tb_btn_danger")
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self._delete_selected)
        tb.addWidget(self.delete_btn)

        tb.addStretch()
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color:#9ca3af; font-size:11px; background:transparent;")
        tb.addWidget(self.status_lbl)
        outer.addLayout(tb)

        # ── Table ─────────────────────────────────────────────────────────────
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["File", "Share Link", "Active", "Expires"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)

        hdr = self.tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

        outer.addWidget(self.tree, 1)

        # ── Copied feedback bar ───────────────────────────────────────────────
        self.copy_bar = QLabel("")
        self.copy_bar.setObjectName("log_console")
        self.copy_bar.setWordWrap(True)
        self.copy_bar.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.copy_bar.setOpenExternalLinks(True)
        self.copy_bar.hide()
        outer.addWidget(self.copy_bar)

    # ── Data ──────────────────────────────────────────────────────────────────
    def refresh(self):
        api_key = self.get_api_key()
        if not api_key:
            self._status("⚠ Enter your API key in Settings first.")
            return
        self._status("Loading…")
        self.tree.clear()
        self.copy_bar.hide()
        w = FilesWorker("shares", api_key, self.base_url)
        w.done.connect(self._on_done)
        w.error.connect(self._on_error)
        w.finished.connect(lambda: self._workers.remove(w) if w in self._workers else None)
        self._workers.append(w)
        w.start()

    def _on_done(self, result):
        if result.get("op") != "shares":
            return
        data   = result["data"]
        shares = data.get("shares", data) if isinstance(data, dict) else data
        self.tree.setSortingEnabled(False)
        self.tree.clear()

        for s in shares:
            token     = s.get("token", "")
            file_name = (
                s.get("originalName")
                or s.get("original_name")
                or s.get("name")
                or s.get("fileName")
                or s.get("file_name")
                or token
            )
            is_active = s.get("is_active", s.get("isActive", True))
            expires   = s.get("expires_at") or s.get("expiresAt") or s.get("expiry") or "Never"
            if expires and expires != "Never" and len(expires) > 10:
                expires = expires[:10]
            url = f"{self.base_url}/share/{token}" if token else ""

            active_text  = "● Active"   if is_active else "○ Inactive"
            active_color = "#4ade80"    if is_active else "#9ca3af"

            item = QTreeWidgetItem([file_name, url, active_text, expires])
            item.setData(0, Qt.ItemDataRole.UserRole, {
                "token": token, "url": url,
                "is_active": is_active, "file_name": file_name,
            })
            item.setForeground(2, QColor(active_color))
            item.setForeground(1, QColor("#9ca3af"))
            self.tree.addTopLevelItem(item)

        self.tree.setSortingEnabled(True)
        count = self.tree.topLevelItemCount()
        self._status(f"{count} share{'s' if count != 1 else ''}")

    def _on_error(self, msg):
        self._status(f"✗ {msg}")
        QMessageBox.warning(self, "Error", msg)

    # ── Selection ─────────────────────────────────────────────────────────────
    def _on_selection_changed(self):
        has = len(self.tree.selectedItems()) > 0
        self.copy_btn.setEnabled(has)
        self.toggle_btn.setEnabled(has)
        self.delete_btn.setEnabled(has)

    def _selected_meta(self):
        return [item.data(0, Qt.ItemDataRole.UserRole)
                for item in self.tree.selectedItems()]

    # ── Actions ───────────────────────────────────────────────────────────────
    def _copy_selected(self):
        items = self._selected_meta()
        if not items:
            return
        if len(items) == 1:
            url = items[0]["url"]
            QApplication.clipboard().setText(url)
            self.copy_bar.setText(f'Copied: <a href="{url}" style="color:#e11d48;">{url}</a>')
            self.copy_bar.show()
        else:
            urls = "\n".join(m["url"] for m in items)
            QApplication.clipboard().setText(urls)
            self.copy_bar.setText(f"Copied {len(items)} links to clipboard.")
            self.copy_bar.show()

    def _toggle_selected(self):
        api_key = self.get_api_key()
        for meta in self._selected_meta():
            token      = meta["token"]
            new_active = not meta["is_active"]
            import requests as _req
            try:
                resp = _req.patch(
                    f"{self.base_url}/api/shares/{token}",
                    headers={"Authorization": f"Bearer {api_key}",
                             "Content-Type": "application/json"},
                    json={"isActive": new_active},
                    timeout=15,
                )
                resp.raise_for_status()
            except Exception as e:
                QMessageBox.warning(self, "Error", str(e))
                return
        self.refresh()

    def _delete_selected(self):
        items = self._selected_meta()
        if not items:
            return
        msg = (f"Delete share for {items[0]['file_name']!r}?"
               if len(items) == 1
               else f"Delete {len(items)} shares?")
        if QMessageBox.question(self, "Confirm Delete", msg,
                                QMessageBox.StandardButton.Yes |
                                QMessageBox.StandardButton.No
                                ) != QMessageBox.StandardButton.Yes:
            return
        api_key = self.get_api_key()
        import requests as _req
        for meta in items:
            try:
                resp = _req.delete(
                    f"{self.base_url}/api/shares/{meta['token']}",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=15,
                )
                resp.raise_for_status()
            except Exception as e:
                QMessageBox.warning(self, "Error", str(e))
                return
        self.copy_bar.hide()
        self.refresh()

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background:#1f1f1f; border:1px solid #3a3a3a; border-radius:8px;
                    color:#f0f0f0; font-size:12px; }
            QMenu::item { padding:6px 24px; }
            QMenu::item:selected { background:#c8a96e33; }
        """)
        menu.addAction("⧉  Copy Link",     self._copy_selected)
        menu.addAction("◎  Toggle Active", self._toggle_selected)
        menu.addSeparator()
        menu.addAction("✕  Delete",        self._delete_selected)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _status(self, msg):
        self.status_lbl.setText(msg)


# ── Main Window ──────────────────────────────────────────────────────────────
class MochaTools(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Mocha Tools")
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
            get_api_key=lambda: self.api_key_edit.text().strip(),
            get_upload_path=lambda: self.upload_path_edit.text().strip(),
            set_upload_path=lambda p: self.upload_path_edit.setText(p),
        )

        # ── Remote tab ───────────────────────────────────────────────────────
        self.remote_tab = RemoteTab(
            get_api_key=lambda: self.api_key_edit.text().strip(),
        )

        # ── Shares tab ───────────────────────────────────────────────────────
        self.shares_tab = SharesTab(
            get_api_key=lambda: self.api_key_edit.text().strip(),
        )

        # Update worker state
        self._update_tag: str = ""
        self._update_url: str = ""
        self._update_dl_worker: UpdateDownloadWorker | None = None

        # ── Settings tab ─────────────────────────────────────────────────────
        settings_tab = QWidget()
        settings_lay = QVBoxLayout(settings_tab)
        settings_lay.setContentsMargins(16, 16, 16, 16)
        settings_lay.setSpacing(14)

        settings_lay.addWidget(self._make_section_header("API"))
        api_card = self._make_card()
        api_lay  = QVBoxLayout(api_card)
        api_lay.setSpacing(10)

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

        # upload_path_edit is used by _start_upload; it is shown in the Upload tab
        self.upload_path_edit = QLineEdit()
        self.upload_path_edit.setText("/")

        self.remember_cb = QCheckBox("Remember settings across sessions")
        api_lay.addWidget(self.remember_cb)

        settings_lay.addWidget(api_card)
        settings_lay.addWidget(self._make_section_header("Logging"))
        debug_card = self._make_card()
        debug_lay  = QVBoxLayout(debug_card)
        debug_lay.setSpacing(6)

        self.debug_cb = QCheckBox("Enable debug logging")
        self.debug_cb.setToolTip(
            "Show [DEBUG] lines in the status console and log file.\n"
            "Turn off to see only high-level status messages."
        )
        debug_lay.addWidget(self.debug_cb)

        debug_note = QLabel("When enabled, all status messages are shown in the console and written to the log file.")
        debug_note.setObjectName("field_label")
        debug_note.setWordWrap(True)
        debug_lay.addWidget(debug_note)

        settings_lay.addWidget(debug_card)

        # ── Multipart Upload ──────────────────────────────────────────────────
        settings_lay.addWidget(self._make_section_header("Multipart Upload"))
        chunk_card = self._make_card()
        chunk_lay  = QVBoxLayout(chunk_card)
        chunk_lay.setSpacing(10)

        chunk_note = QLabel(
            "Files larger than one chunk size are uploaded in multiple parts. "
            "Larger chunks reduce overhead; more parallel chunks can increase throughput "
            "on fast connections."
        )
        chunk_note.setObjectName("field_label")
        chunk_note.setWordWrap(True)
        chunk_lay.addWidget(chunk_note)

        # Chunk size row
        cs_row = QHBoxLayout()
        cs_lbl = QLabel("Chunk size")
        cs_lbl.setObjectName("field_label")
        self.chunk_size_spin = QSpinBox()
        self.chunk_size_spin.setRange(1, 100)
        self.chunk_size_spin.setValue(DEFAULT_CHUNK_SIZE_MB)
        self.chunk_size_spin.setSuffix(" MB")
        self.chunk_size_spin.setToolTip(
            "Size of each upload part (1–100 MB).\n"
            "Files ≤ this size are uploaded in a single request.\n"
            "Files larger than this are split into multiple parts."
        )
        cs_row.addWidget(cs_lbl)
        cs_row.addWidget(self.chunk_size_spin, 1)
        chunk_lay.addLayout(cs_row)

        # Max concurrent chunks row
        mc_row = QHBoxLayout()
        mc_lbl = QLabel("Max parallel chunks")
        mc_lbl.setObjectName("field_label")
        self.max_chunks_spin = QSpinBox()
        self.max_chunks_spin.setRange(1, 20)
        self.max_chunks_spin.setValue(DEFAULT_MAX_CHUNKS)
        self.max_chunks_spin.setSuffix(" chunks")
        self.max_chunks_spin.setToolTip(
            "Maximum number of upload parts sent in parallel (1–20).\n"
            "Higher values improve throughput on fast connections but use more memory."
        )
        mc_row.addWidget(mc_lbl)
        mc_row.addWidget(self.max_chunks_spin, 1)
        chunk_lay.addLayout(mc_row)

        settings_lay.addWidget(chunk_card)

        # ── Updates ──────────────────────────────────────────────────────────
        settings_lay.addWidget(self._make_section_header("Updates"))
        update_card = self._make_card()
        update_lay  = QVBoxLayout(update_card)
        update_lay.setSpacing(8)

        self.update_status_lbl = QLabel(f"Current version: {APP_VERSION}")
        self.update_status_lbl.setObjectName("field_label")
        self.update_status_lbl.setWordWrap(True)
        update_lay.addWidget(self.update_status_lbl)

        self.update_progress = QProgressBar()
        self.update_progress.setValue(0)
        self.update_progress.hide()
        update_lay.addWidget(self.update_progress)

        update_btn_row = QHBoxLayout()
        self.check_update_btn = QPushButton("Check for updates")
        self.check_update_btn.setObjectName("browse_btn")
        self.check_update_btn.clicked.connect(self._check_for_updates)
        update_btn_row.addWidget(self.check_update_btn)

        self.install_update_btn = QPushButton("↓  Install update")
        self.install_update_btn.setObjectName("upload_btn")
        self.install_update_btn.clicked.connect(self._install_update)
        self.install_update_btn.hide()
        update_btn_row.addWidget(self.install_update_btn)
        update_btn_row.addStretch()
        update_lay.addLayout(update_btn_row)

        settings_lay.addWidget(update_card)
        settings_lay.addStretch()

        self.tabs.addTab(upload_tab, "↑  Upload")
        self.tabs.addTab(self.remote_tab, "⇣  Remote")
        self.tabs.addTab(self.files_tab, "📁  Files")
        self.tabs.addTab(self.shares_tab, "⤴  Shares")
        self.tabs.addTab(settings_tab, "⚙  Settings")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # ── FILE ─────────────────────────────────────────────────────────────
        main.addWidget(self._make_section_header("File"))
        file_card = self._make_card()
        file_lay  = QVBoxLayout(file_card)
        self.drop_zone = DropZone()
        self.drop_zone.selection_changed.connect(self._on_files_selected)
        file_lay.addWidget(self.drop_zone)
        main.addWidget(file_card)

        # ── DESTINATION ───────────────────────────────────────────────────────
        main.addWidget(self._make_section_header("Destination"))
        dest_card = self._make_card()
        dest_lay  = QVBoxLayout(dest_card)
        dest_lay.setSpacing(8)

        dest_row = QHBoxLayout()
        dest_lbl = QLabel("Folder")
        dest_lbl.setObjectName("field_label")
        self.upload_path_edit.setPlaceholderText("/")
        browse_dest_btn = QPushButton("Browse…")
        browse_dest_btn.setObjectName("browse_btn")
        browse_dest_btn.setToolTip("Browse remote folders to pick an upload destination")
        browse_dest_btn.clicked.connect(self._browse_upload_dest)
        dest_row.addWidget(dest_lbl)
        dest_row.addWidget(self.upload_path_edit, 1)
        dest_row.addWidget(browse_dest_btn)
        dest_lay.addLayout(dest_row)
        main.addWidget(dest_card)

        # ── UPLOAD ────────────────────────────────────────────────────────────
        main.addWidget(self._make_section_header("Upload"))
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
        self.log_label = QLabel("Ready — select a file and destination folder, then upload.")
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
            Qt.TextInteractionFlag.TextSelectableByKeyboard |
            Qt.TextInteractionFlag.LinksAccessibleByMouse |
            Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
        self.share_result.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        self.share_result.setOpenExternalLinks(True)
        self.share_result.hide()
        status_lay.addWidget(self.share_result)

        main.addWidget(status_card)

        # ── SHARE OPTIONS ─────────────────────────────────────────────────────
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
        # Display label → hours (None = no expiry)
        self._expiry_map = [
            ("Never",    None),
            ("1 hour",   1),
            ("6 hours",  6),
            ("12 hours", 12),
            ("1 day",    24),
            ("3 days",   72),
            ("7 days",   168),
            ("14 days",  336),
            ("30 days",  720),
        ]
        self.expiry_combo.addItems([label for label, _ in self._expiry_map])
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

    def _browse_upload_dest(self):
        api_key = self.api_key_edit.text().strip()
        if not api_key:
            self._log("⚠ Enter your API key in Settings before browsing folders.")
            return
        dlg = FolderBrowserDialog(
            api_key, HARDCODED_BASE_URL,
            self.upload_path_edit.text().strip() or "/",
            parent=self,
        )
        dlg.setWindowTitle("Choose upload destination folder")
        if dlg.exec():
            self.upload_path_edit.setText(dlg.selected)

    def _toggle_key_visibility(self, checked):
        mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        self.api_key_edit.setEchoMode(mode)

    def _toggle_share_options(self, checked):
        self.share_opts_widget.setVisible(checked)

    def _on_files_selected(self, file_list, root):
        self.selected_files = file_list
        self.selected_root  = root
        if len(file_list) == 1:
            self._log(f"[DEBUG] Selected: {os.path.basename(file_list[0])}")
        else:
            self._log(f"[DEBUG] Selected folder: {len(file_list)} files")
        self.share_result.hide()

    # ── Settings ──────────────────────────────────────────────────────────────
    def _load_settings(self):
        self.api_key_edit.setText(self.settings.value("api_key", ""))
        self.upload_path_edit.setText(self.settings.value("upload_path", "/"))
        self.remote_tab.path_edit.setText(self.settings.value("remote_path", "/"))
        remember = self.settings.value("remember", False, type=bool)
        self.remember_cb.setChecked(remember)
        debug = self.settings.value("debug", False, type=bool)
        self.debug_cb.setChecked(debug)
        self.chunk_size_spin.setValue(
            self.settings.value("chunk_size_mb", DEFAULT_CHUNK_SIZE_MB, type=int)
        )
        self.max_chunks_spin.setValue(
            self.settings.value("max_chunks", DEFAULT_MAX_CHUNKS, type=int)
        )

    def _save_settings(self):
        # Always persist debug toggle and chunk config regardless of remember_cb
        self.settings.setValue("debug", self.debug_cb.isChecked())
        self.settings.setValue("chunk_size_mb", self.chunk_size_spin.value())
        self.settings.setValue("max_chunks",    self.max_chunks_spin.value())
        if self.remember_cb.isChecked():
            self.settings.setValue("api_key",     self.api_key_edit.text())
            self.settings.setValue("upload_path", self.upload_path_edit.text())
            self.settings.setValue("remote_path", self.remote_tab.path_edit.text())
            self.settings.setValue("remember",    True)
        else:
            self.settings.remove("api_key")
            self.settings.remove("upload_path")
            self.settings.remove("remote_path")
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
        self._badge("Uploading", "#c8a96e")

        expiry_hours = self._expiry_map[self.expiry_combo.currentIndex()][1] if self.create_share_cb.isChecked() else None
        max_dl       = self.max_dl_spin.value() if self.create_share_cb.isChecked() else 0

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
            self.create_share_cb.isChecked(), expiry_hours, max_dl,
            chunk_size_mb=self.chunk_size_spin.value(),
            max_chunks=self.max_chunks_spin.value(),
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
            # Disconnect live signals so in-flight thread callbacks don't
            # update the UI after the user has already cancelled.
            try:
                self.worker.progress.disconnect()
                self.worker.speed.disconnect()
                self.worker.status.disconnect()
                self.worker.finished.disconnect()
                self.worker.error.disconnect()
            except RuntimeError:
                pass  # already disconnected
        self._set_uploading(False)
        self._badge("Cancelled", "#9ca3af")
        self.progress_bar.setValue(0)
        self.pct_label.setText("0%")
        self.speed_label.setText("")
        self.share_result.hide()
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
            self.share_result.setText(f'<a href="{url}" style="color:#c8a96e;">{url}</a>')
            self.share_result.show()

    def _on_error(self, msg):
        self._set_uploading(False)
        self._badge("Error", "#f87171")
        self._log(f"✗ Error: {msg}")

    def _log(self, msg):
        debug_enabled = getattr(self, "debug_cb", None) and self.debug_cb.isChecked()
        if msg.startswith("[DEBUG]") and not debug_enabled:
            return
        self.log_label.setText(msg)
        if not debug_enabled:
            return
        write_debug_log(msg)

    def _badge(self, text, color):
        self.status_badge.setText(f"● {text}")
        self.status_badge.setStyleSheet(
            f"background-color: {color}22; border: 1px solid {color}55; "
            f"border-radius: 0px; color: {color}; font-size: 11px; "
            f"font-weight: 600; padding: 2px 10px;"
        )

    def _on_tab_changed(self, index):
        self.remote_tab.set_active(index == 1)
        # Auto-refresh the Remote tab when switched to (if API key is present)
        if index == 1:
            return
        # Auto-refresh the Files tab when switched to (if API key is present)
        if index == 2:
            if self.api_key_edit.text().strip():
                self.files_tab._refresh()
        # Auto-refresh the Shares tab when switched to
        elif index == 3:
            if self.api_key_edit.text().strip():
                self.shares_tab.refresh()
        # Auto-save settings when leaving Settings tab (now index 4)
        elif index != 4:
            self._save_settings()


    # ── Auto-update ───────────────────────────────────────────────────────────

    def _check_for_updates(self, silent: bool = False):
        """Kick off a background update check. silent=True suppresses 'up to date' toast."""
        self.check_update_btn.setEnabled(False)
        self.update_status_lbl.setText("Checking for updates…")
        w = UpdateCheckWorker(self)
        w.update_available.connect(self._on_update_available)
        w.up_to_date.connect(lambda: self._on_up_to_date(silent))
        w.error.connect(lambda msg: self._on_update_error(msg, silent))
        w.finished.connect(lambda: self.check_update_btn.setEnabled(True))
        w.start()

    def _on_update_available(self, tag: str, url: str, notes: str):
        self._update_tag = tag
        self._update_url = url
        self.update_status_lbl.setText(
            f"Update available: {tag}  (current: {APP_VERSION})"
        )
        self.install_update_btn.setVisible(bool(url))
        if not url:
            self.update_status_lbl.setText(
                f"Update {tag} available — no binary for this platform. "
                "Download manually from github.com/nxllxvxxd2/Mocha-Tools/releases"
            )

    def _on_up_to_date(self, silent: bool):
        self.update_status_lbl.setText(f"You're up to date ({APP_VERSION})")
        self.install_update_btn.hide()
        if not silent:
            QMessageBox.information(self, "Up to date", f"Mocha Tools {APP_VERSION} is the latest version.")

    def _on_update_error(self, msg: str, silent: bool):
        self.update_status_lbl.setText(f"Update check failed: {msg}")
        if not silent:
            QMessageBox.warning(self, "Update check failed", msg)

    def _install_update(self):
        if not self._update_url:
            return
        self.install_update_btn.setEnabled(False)
        self.update_progress.setValue(0)
        self.update_progress.show()

        w = UpdateDownloadWorker(self._update_url, self)
        w.progress.connect(self.update_progress.setValue)
        w.status.connect(self.update_status_lbl.setText)
        w.done.connect(self._on_update_done)
        w.error.connect(self._on_update_dl_error)
        w.start()
        self._update_dl_worker = w

    def _on_update_done(self):
        self.update_progress.setValue(100)
        self.install_update_btn.hide()
        result = QMessageBox.question(
            self, "Restart required",
            f"Mocha Tools {self._update_tag} has been installed.\n\nRestart now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if result == QMessageBox.StandardButton.Yes:
            import subprocess, sys
            subprocess.Popen([sys.executable] + sys.argv)
            QApplication.quit()

    def _on_update_dl_error(self, msg: str):
        self.update_progress.hide()
        self.install_update_btn.setEnabled(True)
        self.update_status_lbl.setText(f"Download failed: {msg}")
        QMessageBox.warning(self, "Update failed", msg)

    def closeEvent(self, event):
        self._save_settings()
        # Stop any running workers
        self.remote_tab.set_active(False)
        for w in list(self.remote_tab._workers):
            w.quit()
        for w in list(self.files_tab._workers):
            w.quit()
        for w in list(self.shares_tab._workers):
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
    palette.setColor(QPalette.ColorRole.Window,           QColor("#111010"))
    palette.setColor(QPalette.ColorRole.WindowText,       QColor("#f0ece6"))
    palette.setColor(QPalette.ColorRole.Base,             QColor("#141210"))
    palette.setColor(QPalette.ColorRole.Text,             QColor("#f0ece6"))
    palette.setColor(QPalette.ColorRole.Button,           QColor("#1e1c19"))
    palette.setColor(QPalette.ColorRole.ButtonText,       QColor("#f0ece6"))
    palette.setColor(QPalette.ColorRole.Highlight,        QColor("#c8a96e"))
    palette.setColor(QPalette.ColorRole.HighlightedText,  QColor("#111010"))
    app.setPalette(palette)

    win = MochaTools()
    win.show()
    # Silent background update check on every launch
    QTimer.singleShot(2000, lambda: win._check_for_updates(silent=True))
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
#     • Windows : HKEY_CURRENT_USER\Software\Mocha\MochaTools
#     • macOS   : ~/Library/Preferences/com.Mocha.MochaTools.plist
#     • Linux   : ~/.config/Mocha/MochaTools.ini
#   Only saved when "Remember settings across sessions" is checked.
#
# UPLOAD LOGIC
# ------------
#   ≤ 50 MB  → POST /api/files          (direct upload)
#   > 50 MB  → multipart: init → parts → complete
#              Each part is 50 MB. Abort is called on cancel.
#
# SHARE OPTIONS
# -------------
#   Expiration values are sent as expiresInHours.
#   Max downloads = 0 means "Unlimited" (field omitted from request).
# ═══════════════════════════════════════════════════════════════════════════