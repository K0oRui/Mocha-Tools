# Mocha Tools — Android Port Guide (PySide6 / Qt for Android)
**Desktop: PyQt6 on Ubuntu → Android APK via PySide6 + Buildozer**

---

## Overview & Honest Scope Assessment

Before diving in, a clear-eyed reality check:

**What works well:** The entire backend — `workers.py` (UploadWorker, FilesWorker, RemoteWorker, ProgressTracker), `logging_utils.py`, `constants.py` — is 100% portable to Android with zero changes. It's pure Python using `requests` and `threading`, both available via Buildozer.

**What requires a rewrite:** The UI layer (`app.py`, `dialogs.py`, `styles.py`) must be rewritten for mobile. The critical constraint is **file access**: Android's storage model is sandboxed. You cannot use `QFileDialog.getOpenFileName()` the same way — you must use Qt's `QFileDialog` with Android content URIs, or the Android Storage Access Framework via `QtCore.QStandardPaths`.

**What to drop:** `updater.py` is irrelevant on Android (APK updates go through the Play Store or manual APK installs). Drop it entirely.

**What to adapt:** `QSettings` works on Android (it maps to SharedPreferences). `QTabWidget` works but tabs should be at the bottom (Android convention). Drag & drop (`DropZone`) doesn't exist on Android — replace with a file picker button only.

---

## Part 1: Environment Setup on Ubuntu

### 1.1 Install PySide6 and Qt for Android

PySide6 (not PyQt6) is the correct choice because it ships official Android wheels and integrates with Qt's `androiddeployqt` toolchain.

```bash
# System dependencies
sudo apt update
sudo apt install -y python3-pip python3-venv openjdk-17-jdk \
    libgl1-mesa-dev libglib2.0-dev git wget unzip

# Create a dedicated venv for the Android project
python3 -m venv ~/mochatools-android-env
source ~/mochatools-android-env/bin/activate

# PySide6 — the desktop version for UI development/testing
pip install PySide6

# For cross-compilation you need the Qt for Android toolchain.
# PySide6 ships android wheels via pip for arm64-v8a and x86_64:
pip install --index-url https://download.qt.io/snapshots/ci/pyside/dev/latest/ \
    PySide6           # only if the standard pypi build doesn't include android targets
# In practice, use the Qt Online Installer (recommended):
# https://www.qt.io/download-qt-installer
# Install Qt 6.7+ with: Qt for Android (arm64-v8a), Android SDK/NDK
```

**Recommended path**: Use the Qt Online Installer. Install:
- Qt 6.7 (or latest 6.x)
- Qt for Android (arm64-v8a) — covers 98%+ of modern devices
- Android SDK API 34
- Android NDK r26 (Qt 6.7 requires NDK 26b or later)

Then set environment variables:

```bash
export ANDROID_SDK_ROOT=~/Android/Sdk
export ANDROID_NDK_ROOT=~/Android/Sdk/ndk/26.1.10909125   # adjust to your NDK version
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
export Qt6_DIR=~/Qt/6.7.0/android_arm64_v8a/lib/cmake/Qt6
```

Add these to `~/.bashrc` to persist them.

### 1.2 Install Buildozer (for the Python packaging layer)

Buildozer wraps the Qt for Android build into a single `buildozer android debug` command.

```bash
pip install buildozer cython
sudo apt install -y build-essential libssl-dev libffi-dev \
    python3-dev libsqlite3-dev zlib1g-dev

# Buildozer also needs these for its own Android SDK management:
sudo apt install -y autoconf automake libtool pkg-config \
    lld libltdl-dev libxml2-dev libxslt1-dev
```

> **Note:** Buildozer traditionally targets Kivy. For PySide6, you use `buildozer` mainly for the packaging skeleton. The actual compilation uses Qt's `pyside6-android-deploy` tool (shipped with PySide6 6.5+). See Part 4 for the exact build command.

### 1.3 Verify your setup

```bash
python3 -c "import PySide6; print(PySide6.__version__)"
pyside6-android-deploy --help    # should print usage if PySide6 >= 6.5
java -version                    # should show openjdk 17
```

---

## Part 2: Project Structure

Reorganise the files as follows. **Do not modify the originals** until you've confirmed the structure:

```
mocha_tools_android/
├── main.py                  # Entry point (replaces mochatools.py)
├── buildozer.spec           # Buildozer config (see Part 4)
├── pysidedeploy.spec        # pyside6-android-deploy config (see Part 4)
├── VERSION                  # Keep as-is
├── assets/
│   └── icon.png             # 512×512 app icon
└── mochatools_app/
    ├── __init__.py          # Unchanged
    ├── constants.py         # Unchanged
    ├── logging_utils.py     # Unchanged
    ├── workers.py           # Unchanged — pure Python, fully portable
    ├── styles.py            # Keep — QSS works on Android with minor tweaks (see Part 3)
    ├── app_android.py       # NEW — replaces app.py, mobile-adapted UI
    └── dialogs_android.py   # NEW — replaces dialogs.py, mobile-adapted dialogs
```

`updater.py` is excluded — Android apps don't self-update outside the Play Store.

---

## Part 3: Code Changes

### 3.1 `main.py` (entry point)

```python
# main.py
import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from mochatools_app.app_android import MochaTools
from mochatools_app.styles import STYLESHEET
from mochatools_app.constants import APP_NAME, ORG_NAME

def main():
    # On Android, argv is managed by Qt — don't pass sys.argv directly
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    # Fusion style works on Android
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    win = MochaTools()
    win.showMaximized()   # Android apps always fill the screen
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
```

### 3.2 `styles.py` — changes needed

The existing stylesheet is almost entirely portable. Make three targeted changes:

**Change 1** — Increase touch targets. Android minimum tap target is 48dp. Edit the button min-heights:

```css
/* In STYLESHEET, find and change: */
QPushButton#upload_btn {
    /* ... existing properties ... */
    padding: 14px 24px;        /* was 10px 24px */
    min-height: 48px;          /* was unset — add this */
}
QPushButton#browse_btn {
    min-height: 48px;          /* was 34px */
    max-height: 48px;          /* was 34px */
}
QPushButton#tb_btn {
    min-height: 44px;          /* was 28px */
}
QPushButton#tb_btn_danger {
    min-height: 44px;          /* was 28px */
}
QLineEdit {
    min-height: 44px;          /* was 34px */
    max-height: 44px;          /* was 34px */
    font-size: 15px;           /* was 13px — easier to read on mobile */
}
```

**Change 2** — Remove the `DropZone` frame styling (you'll keep the QFrame#drop_zone rule but it won't be used for drag-and-drop, only as a styled container):

```css
/* Keep QFrame#drop_zone but drop the drag_active pseudo-state — Android has no drag */
QFrame#drop_zone {
    background-color: #141210;
    border: 2px dashed #2e2b27;
    border-radius: 12px;
    min-height: 90px;          /* shorter on mobile — no drag hint needed */
}
/* DELETE the QFrame#drop_zone[drag_active="true"] block entirely */
```

**Change 3** — Tab bar at the bottom. Qt's `QTabWidget` doesn't natively support bottom tabs via QSS alone, but you can force it in code (see app_android.py). Update the tab style to be more touch-friendly:

```css
QTabBar::tab {
    padding: 10px 16px;        /* was 7px 20px */
    font-size: 13px;           /* was 12px */
}
```

### 3.3 `app_android.py` — full rewrite of the UI layer

This is the most substantial change. Below is a complete, working rewrite. The logic (workers, signals, API calls) is identical to the original — only the UI construction differs.

Key differences from `app.py`:
- `DropZone` loses drag-and-drop; becomes a button-only file picker using `QFileDialog` with Android content URIs
- `QTabWidget` tabs moved to the bottom (`setTabPosition(QTabWidget.TabPosition.South)`)
- `QMainWindow` replaced with a maximised `QWidget` (Android doesn't use window chrome)
- `QTreeWidget` columns collapsed to 2 on mobile (Name + one data column) — too many columns don't fit a phone screen
- `QSplitter` removed — not useful on a portrait phone layout
- Font sizes bumped up 1-2pt throughout
- `updater.py` imports removed entirely

```python
# mochatools_app/app_android.py
"""
Mocha Tools — Android UI layer (PySide6)
All business logic lives in workers.py (unchanged).
"""

import sys
import os
import json
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QCheckBox, QProgressBar,
    QFileDialog, QFrame, QSpinBox, QComboBox, QScrollArea,
    QSizePolicy, QMessageBox, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QHeaderView, QMenu, QAbstractItemView, QInputDialog,
)
from PySide6.QtCore import (
    Qt, QThread, Signal, QTimer, QSettings, QStandardPaths
)
from PySide6.QtGui import QColor

from .constants import (
    APP_NAME, DEFAULT_CHUNK_SIZE_MB, DEFAULT_MAX_CHUNKS,
    HARDCODED_BASE_URL, ORG_NAME, APP_VERSION,
)
from .logging_utils import write_debug_log
from .workers import FilesWorker, RemoteWorker, UploadWorker
from .dialogs_android import FolderBrowserDialog, ShareLinkDialog


# ── File Picker Widget (replaces DropZone) ────────────────────────────────────
class FilePicker(QFrame):
    """
    Mobile-friendly replacement for the desktop DropZone.
    No drag-and-drop — just two buttons (File / Folder) and a label.
    On Android, QFileDialog uses the system file picker via content URIs.
    """
    selection_changed = Signal(list, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("drop_zone")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.file_btn = QPushButton("📄  Select File")
        self.file_btn.setObjectName("browse_btn")
        self.file_btn.clicked.connect(self._pick_file)

        self.folder_btn = QPushButton("📁  Select Folder")
        self.folder_btn.setObjectName("browse_btn")
        self.folder_btn.clicked.connect(self._pick_folder)

        btn_row.addWidget(self.file_btn)
        btn_row.addWidget(self.folder_btn)
        layout.addLayout(btn_row)

        self.file_label = QLabel("No file selected")
        self.file_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.file_label.setStyleSheet(
            "color: #9c9484; font-size: 12px; background:transparent;")
        layout.addWidget(self.file_label)

    def _pick_file(self):
        # On Android this opens the system file picker
        path, _ = QFileDialog.getOpenFileName(self, "Select file")
        if path:
            self._set_paths([path], os.path.dirname(path), is_folder=False)

    def _pick_folder(self):
        # On Android this opens the system folder/directory picker
        path = QFileDialog.getExistingDirectory(self, "Select folder")
        if path:
            files = self._collect_folder(path)
            if files:
                self._set_paths(files, path, is_folder=True)

    @staticmethod
    def _collect_folder(folder_path):
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
            size  = os.path.getsize(file_list[0])
            label = f"{os.path.basename(file_list[0])}  ({UploadWorker._fmt_size(size)})"
            selected_root = root
        else:
            total = sum(os.path.getsize(p) for p in file_list)
            label = f"{name}/  —  {len(file_list)} files  ({UploadWorker._fmt_size(total)})"
            selected_root = os.path.dirname(root.rstrip("/\\"))
        self.file_label.setText(label)
        self.file_label.setStyleSheet(
            "color: #c8a96e; font-size: 12px; font-weight:600; background:transparent;")
        self.selection_changed.emit(file_list, selected_root)


# ── Files Browser Tab ─────────────────────────────────────────────────────────
class FilesBrowserTab(QWidget):
    """
    Mobile-adapted Files tab.
    QTreeWidget reduced to 3 columns (Name, Size, Shared) — fits portrait screens.
    Toolbar buttons wrap to two rows on narrow screens.
    """

    def __init__(self, get_api_key, get_upload_path, set_upload_path, parent=None):
        super().__init__(parent)
        self.get_api_key     = get_api_key
        self.get_upload_path = get_upload_path
        self.set_upload_path = set_upload_path
        self.base_url        = HARDCODED_BASE_URL
        self.current_path    = "/"
        self._workers        = []
        self._shares_map     = {}
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # Path bar
        path_row = QHBoxLayout()
        self.path_edit = QLineEdit("/")
        self.path_edit.setPlaceholderText("/path/to/folder")
        self.path_edit.returnPressed.connect(self._on_path_entered)
        up_btn = QPushButton("↑")
        up_btn.setObjectName("tb_btn")
        up_btn.setFixedWidth(44)
        up_btn.clicked.connect(self._go_up)
        go_btn = QPushButton("Go")
        go_btn.setObjectName("tb_btn")
        go_btn.setFixedWidth(52)
        go_btn.clicked.connect(self._on_path_entered)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(up_btn)
        path_row.addWidget(go_btn)
        outer.addLayout(path_row)

        # Toolbar — two rows on mobile
        tb1 = QHBoxLayout()
        tb1.setSpacing(4)
        self.refresh_btn = self._tb("↺ Refresh",    self._refresh)
        self.mkdir_btn   = self._tb("+ Folder",     self._create_folder)
        self.move_btn    = self._tb("↦ Move",        self._move_selected)
        for b in (self.refresh_btn, self.mkdir_btn, self.move_btn):
            tb1.addWidget(b)
        outer.addLayout(tb1)

        tb2 = QHBoxLayout()
        tb2.setSpacing(4)
        self.share_btn  = self._tb("⤴ Share",  self._share_selected)
        self.delete_btn = self._tb("✕ Delete", self._delete_selected, danger=True)
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color:#9c9484; font-size:11px; background:transparent;")
        tb2.addWidget(self.share_btn)
        tb2.addWidget(self.delete_btn)
        tb2.addStretch()
        tb2.addWidget(self.status_lbl)
        outer.addLayout(tb2)

        # File tree — 3 columns for portrait layout
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Name", "Size", "Shared"])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setRootIsDecorated(False)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)

        hdr = self.tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        outer.addWidget(self.tree, 1)

        self.share_bar = QLabel("")
        self.share_bar.setObjectName("log_console")
        self.share_bar.setWordWrap(True)
        self.share_bar.setOpenExternalLinks(True)
        self.share_bar.hide()
        outer.addWidget(self.share_bar)

        self._set_action_btns_enabled(False)

    def _tb(self, label, slot, danger=False):
        btn = QPushButton(label)
        btn.setObjectName("tb_btn_danger" if danger else "tb_btn")
        btn.clicked.connect(slot)
        return btn

    def _set_action_btns_enabled(self, enabled):
        for b in (self.move_btn, self.share_btn, self.delete_btn):
            b.setEnabled(enabled)

    def _on_selection_changed(self):
        self._set_action_btns_enabled(bool(self.tree.selectedItems()))

    def _on_path_entered(self):
        self._navigate(self.path_edit.text().strip() or "/")

    def _go_up(self):
        parts = self.current_path.strip("/").split("/")
        parent = "/" + "/".join(parts[:-1]) if len(parts) > 1 else "/"
        self._navigate(parent)

    def _navigate(self, path):
        api_key = self.get_api_key()
        if not api_key:
            self._status("⚠ Enter API key in Settings first.")
            return
        self.current_path = path
        self.path_edit.setText(path)
        self._status("Loading…")
        self.tree.clear()
        self.share_bar.hide()
        self._run_worker("list", path=path)
        self._run_worker("shares")

    def _refresh(self):
        self._navigate(self.current_path)

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

    def _index_shares(self, data):
        """Build a token→metadata map from the shares API response."""
        self._shares_map = {}
        shares = data.get("shares", data) if isinstance(data, dict) else data
        if not isinstance(shares, list):
            return
        for s in shares:
            if not isinstance(s, dict):
                continue
            file_id = s.get("fileId") or s.get("file_id")
            if file_id:
                self._shares_map[str(file_id)] = s

    def _refresh_share_indicators(self):
        """Update the Shared column for all items in the tree."""
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
            file_id = str(meta.get("id") or meta.get("file_id") or "")
            shared = "✓" if file_id in self._shares_map else ""
            item.setText(2, shared)
            if shared:
                item.setForeground(2, QColor("#4ade80"))

    def _populate(self, path, data):
        self.tree.setSortingEnabled(False)
        self.tree.clear()

        raw_folders = []
        raw_files   = []
        if isinstance(data, dict):
            raw_folders = data.get("folders") or []
            raw_files   = data.get("files")   or []
        elif isinstance(data, list):
            raw_files = data

        # Folders first
        for entry in raw_folders:
            if isinstance(entry, str):
                name = entry.rstrip("/").split("/")[-1]
                fullpath = (path.rstrip("/") + "/" + name) if path != "/" else "/" + name
            elif isinstance(entry, dict):
                name = (entry.get("name") or entry.get("original_name") or "")
                fullpath = entry.get("path") or (path.rstrip("/") + "/" + name)
            else:
                continue
            item = QTreeWidgetItem([f"📁 {name}", "", ""])
            item.setData(0, Qt.ItemDataRole.UserRole,
                         {"type": "folder", "path": fullpath, "name": name})
            item.setForeground(0, QColor("#c8a96e"))
            self.tree.addTopLevelItem(item)

        # Files
        for entry in raw_files:
            if not isinstance(entry, dict):
                continue
            name = (entry.get("originalName") or entry.get("original_name")
                    or entry.get("name") or entry.get("fileName") or "?")
            size_b = entry.get("fileSize") or entry.get("file_size") or 0
            size_str = UploadWorker._fmt_size(size_b) if size_b else "—"
            file_id = str(entry.get("id") or entry.get("file_id") or "")
            shared = "✓" if file_id in self._shares_map else ""

            item = QTreeWidgetItem([name, size_str, shared])
            item.setData(0, Qt.ItemDataRole.UserRole,
                         {"type": "file", "id": file_id, "name": name,
                          "path": path, "entry": entry})
            if shared:
                item.setForeground(2, QColor("#4ade80"))
            self.tree.addTopLevelItem(item)

        self.tree.setSortingEnabled(True)
        self._status(f"{self.tree.topLevelItemCount()} items")

    def _on_double_click(self, item, _col):
        meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if meta.get("type") == "folder":
            self._navigate(meta["path"])

    # ── Actions (same logic as desktop, just calling _run_worker) ──────────────

    def _create_folder(self):
        text, ok = QInputDialog.getText(self, "New Folder", "Folder name:")
        if ok and text.strip():
            new_path = (self.current_path.rstrip("/") + "/" + text.strip())
            self._run_worker("mkdir", path=new_path)

    def _delete_selected(self):
        items = self.tree.selectedItems()
        if not items:
            return
        msg = f"Delete {len(items)} item(s)?"
        if QMessageBox.question(self, "Confirm Delete", msg,
                                QMessageBox.StandardButton.Yes |
                                QMessageBox.StandardButton.No
                                ) != QMessageBox.StandardButton.Yes:
            return
        for item in items:
            meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
            if meta.get("type") == "folder":
                self._run_worker("delete_folder", path=meta["path"])
            else:
                self._run_worker("delete", file_id=meta.get("id"),
                                 path=meta.get("path"), name=meta.get("name"))

    def _move_selected(self):
        items = self.tree.selectedItems()
        if not items:
            return
        api_key = self.get_api_key()
        dlg = FolderBrowserDialog(api_key, self.base_url,
                                  self.current_path, parent=self)
        if not dlg.exec():
            return
        dest = dlg.selected
        for item in items:
            meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
            if meta.get("type") == "folder":
                self._run_worker("move", is_folder=True,
                                 source_path=meta["path"], new_path=dest)
            else:
                self._run_worker("move", file_id=meta.get("id"),
                                 source_path=meta.get("path"), new_path=dest)

    def _share_selected(self):
        items = self.tree.selectedItems()
        if not items:
            return
        meta = items[0].data(0, Qt.ItemDataRole.UserRole) or {}
        if meta.get("type") == "folder":
            QMessageBox.information(self, "Share", "Folder sharing is not supported.")
            return
        self._run_worker("share", file_id=meta.get("id"),
                         expiry="Never", max_downloads=0)

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        menu.addAction("↦ Move",   self._move_selected)
        menu.addAction("⤴ Share",  self._share_selected)
        menu.addSeparator()
        menu.addAction("✕ Delete", self._delete_selected)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _status(self, msg):
        self.status_lbl.setText(msg)


# ── Remote Tab ────────────────────────────────────────────────────────────────
class RemoteTab(QWidget):
    """Mobile-adapted Remote Ingest tab. Logic identical to desktop."""

    def __init__(self, get_api_key, parent=None):
        super().__init__(parent)
        self.get_api_key   = get_api_key
        self.base_url      = HARDCODED_BASE_URL
        self._workers      = []
        self._is_active    = False
        self._watched_jobs = {}
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        card = self._make_card()
        lay  = QVBoxLayout(card)
        lay.setSpacing(8)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("Source URL (https://…)")
        lay.addWidget(QLabel("URL"))
        lay.addWidget(self.url_edit)

        self.file_name_edit = QLineEdit()
        self.file_name_edit.setPlaceholderText("Filename (optional)")
        lay.addWidget(QLabel("Filename"))
        lay.addWidget(self.file_name_edit)

        dest_row = QHBoxLayout()
        self.path_edit = QLineEdit("/")
        browse_btn = QPushButton("Browse…")
        browse_btn.setObjectName("browse_btn")
        browse_btn.setFixedWidth(90)
        browse_btn.clicked.connect(self._browse_dest)
        dest_row.addWidget(self.path_edit, 1)
        dest_row.addWidget(browse_btn)
        lay.addWidget(QLabel("Destination Folder"))
        lay.addLayout(dest_row)

        self.ingest_btn = QPushButton("⇣  Remote Ingest")
        self.ingest_btn.setObjectName("upload_btn")
        self.ingest_btn.setMinimumHeight(48)
        self.ingest_btn.clicked.connect(self._start_ingest)
        lay.addWidget(self.ingest_btn)

        self.result_bar = QLabel("")
        self.result_bar.setObjectName("log_console")
        self.result_bar.setWordWrap(True)
        self.result_bar.hide()
        lay.addWidget(self.result_bar)
        outer.addWidget(card)

        tb = QHBoxLayout()
        self.refresh_btn = QPushButton("↺ Refresh Jobs")
        self.refresh_btn.setObjectName("tb_btn")
        self.refresh_btn.clicked.connect(self.refresh_jobs)
        self.cancel_btn = QPushButton("✕ Cancel")
        self.cancel_btn.setObjectName("tb_btn_danger")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_selected)
        self.active_only_cb = QCheckBox("Active only")
        self.active_only_cb.setChecked(True)
        self.active_only_cb.toggled.connect(lambda _: self.refresh_jobs())
        tb.addWidget(self.refresh_btn)
        tb.addWidget(self.cancel_btn)
        tb.addWidget(self.active_only_cb)
        tb.addStretch()
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color:#9c9484; font-size:11px; background:transparent;")
        tb.addWidget(self.status_lbl)
        outer.addLayout(tb)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["File", "Status", "Progress"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSortingEnabled(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        hdr = self.tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        outer.addWidget(self.tree, 1)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(5000)
        self.refresh_timer.timeout.connect(self.refresh_jobs)

    def _make_card(self):
        f = QFrame(); f.setObjectName("card"); return f

    def _browse_dest(self):
        api_key = self.get_api_key()
        if not api_key:
            self._status("⚠ Enter API key in Settings first."); return
        dlg = FolderBrowserDialog(api_key, self.base_url,
                                  self.path_edit.text().strip() or "/", parent=self)
        if dlg.exec():
            self.path_edit.setText(dlg.selected)

    def _start_ingest(self):
        api_key = self.get_api_key()
        source_url = self.url_edit.text().strip()
        if not api_key:
            self._status("⚠ Enter API key first."); return
        if not source_url:
            self._status("⚠ Enter a source URL."); return
        from urllib.parse import urlparse, unquote
        file_name = (self.file_name_edit.text().strip()
                     or unquote(os.path.basename(urlparse(source_url).path.rstrip("/"))))
        if not file_name:
            self._status("⚠ Enter a filename."); return
        self.result_bar.hide()
        self.ingest_btn.setEnabled(False)
        self._status("Starting…")
        self._run_worker("ingest", source_url=source_url,
                         file_name=file_name, path=self._normalized_path())

    def refresh_jobs(self):
        if not self.get_api_key():
            self._status("⚠ Enter API key first."); return
        self._run_worker("jobs", active_only=self.active_only_cb.isChecked())

    def _cancel_selected(self):
        meta = self._selected_meta()
        if not meta: return
        if QMessageBox.question(self, "Cancel Job",
                                f"Cancel job {meta['job_id']!r}?",
                                QMessageBox.StandardButton.Yes |
                                QMessageBox.StandardButton.No
                                ) != QMessageBox.StandardButton.Yes:
            return
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
            name   = data.get("originalName") or data.get("fileName") or self.file_name_edit.text().strip()
            self.result_bar.setText(f"Queued: {name}  Job: {job_id or '—'}")
            self.result_bar.show()
            self._status("✓ Ingest queued")
            if job_id:
                self._watched_jobs[str(job_id)] = {"name": name, "seen": False, "checks": 0}
            if self._is_active:
                self.refresh_timer.start()
            self.refresh_jobs()
        elif op == "jobs":
            self._populate_jobs(result.get("data"))
        elif op == "cancel":
            self._watched_jobs.pop(str(result.get("job_id", "")), None)
            self._status("✓ Cancelled")
            self.refresh_jobs()

    def _on_error(self, msg):
        self.ingest_btn.setEnabled(True)
        self._status(f"✗ {msg}")
        QMessageBox.warning(self, "Error", msg)

    def _populate_jobs(self, data):
        jobs = data.get("jobs", data) if isinstance(data, dict) else data
        if not isinstance(jobs, list):
            jobs = []
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        active_ids = set()
        for job in jobs:
            if not isinstance(job, dict):
                continue
            job_id = str(job.get("id") or job.get("jobId") or "")
            if job_id:
                active_ids.add(job_id)
            name     = job.get("originalName") or job.get("fileName") or job.get("sourceUrl") or "—"
            status   = job.get("status") or job.get("state") or "—"
            progress = job.get("progress") or job.get("percent") or ""
            pct_text = f"{progress}%" if progress not in (None, "") else "—"
            item = QTreeWidgetItem([str(name), str(status), pct_text])
            item.setData(0, Qt.ItemDataRole.UserRole, {**job, "job_id": job_id})
            sl = status.lower()
            if sl in ("failed", "error", "cancelled", "canceled"):
                item.setForeground(1, QColor("#f87171"))
            elif sl in ("complete", "completed", "done", "success"):
                item.setForeground(1, QColor("#4ade80"))
            self.tree.addTopLevelItem(item)
        self.tree.setSortingEnabled(True)
        count = self.tree.topLevelItemCount()
        self._status(f"{count} job{'s' if count != 1 else ''}")
        if self._is_active and self.active_only_cb.isChecked() and count:
            self.refresh_timer.start()
        else:
            self.refresh_timer.stop()
        self._on_selection_changed()

    def _on_selection_changed(self):
        self.cancel_btn.setEnabled(bool(self._selected_meta()))

    def _selected_meta(self):
        items = self.tree.selectedItems()
        return items[0].data(0, Qt.ItemDataRole.UserRole) if items else None

    def _normalized_path(self):
        p = self.path_edit.text().strip() or "/"
        if not p.startswith("/"): p = "/" + p
        return p.rstrip("/") + "/"

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
    """Mobile-adapted Shares tab. Logic identical to desktop."""

    def __init__(self, get_api_key, parent=None):
        super().__init__(parent)
        self.get_api_key = get_api_key
        self.base_url    = HARDCODED_BASE_URL
        self._workers    = []
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        tb = QHBoxLayout()
        self.refresh_btn = QPushButton("↺ Refresh")
        self.refresh_btn.setObjectName("tb_btn")
        self.refresh_btn.clicked.connect(self.refresh)
        self.copy_btn = QPushButton("⧉ Copy")
        self.copy_btn.setObjectName("tb_btn")
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._copy_selected)
        self.delete_btn = QPushButton("✕ Delete")
        self.delete_btn.setObjectName("tb_btn_danger")
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self._delete_selected)
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color:#9c9484; font-size:11px; background:transparent;")
        for w in (self.refresh_btn, self.copy_btn, self.delete_btn):
            tb.addWidget(w)
        tb.addStretch()
        tb.addWidget(self.status_lbl)
        outer.addLayout(tb)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["File", "Active", "Expires"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSortingEnabled(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        hdr = self.tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        outer.addWidget(self.tree, 1)

        self.copy_bar = QLabel("")
        self.copy_bar.setObjectName("log_console")
        self.copy_bar.setWordWrap(True)
        self.copy_bar.hide()
        outer.addWidget(self.copy_bar)

    def refresh(self):
        api_key = self.get_api_key()
        if not api_key:
            self._status("⚠ Enter API key first."); return
        self._status("Loading…")
        self.tree.clear()
        self.copy_bar.hide()
        w = FilesWorker("shares", api_key, self.base_url)
        w.done.connect(self._on_done)
        w.error.connect(lambda msg: QMessageBox.warning(self, "Error", msg))
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
            token    = s.get("token", "")
            name     = (s.get("originalName") or s.get("original_name")
                        or s.get("name") or s.get("fileName") or token)
            is_active = s.get("is_active", s.get("isActive", True))
            expires   = s.get("expires_at") or s.get("expiresAt") or "Never"
            if expires and expires != "Never" and len(expires) > 10:
                expires = expires[:10]
            url = f"{self.base_url}/share/{token}" if token else ""
            active_text  = "● Active"  if is_active else "○ Off"
            active_color = "#4ade80"   if is_active else "#9c9484"
            item = QTreeWidgetItem([name, active_text, expires])
            item.setData(0, Qt.ItemDataRole.UserRole,
                         {"token": token, "url": url,
                          "is_active": is_active, "file_name": name})
            item.setForeground(1, QColor(active_color))
            self.tree.addTopLevelItem(item)
        self.tree.setSortingEnabled(True)
        count = self.tree.topLevelItemCount()
        self._status(f"{count} share{'s' if count != 1 else ''}")

    def _on_selection_changed(self):
        has = bool(self.tree.selectedItems())
        self.copy_btn.setEnabled(has)
        self.delete_btn.setEnabled(has)

    def _selected_meta(self):
        return [item.data(0, Qt.ItemDataRole.UserRole)
                for item in self.tree.selectedItems()]

    def _copy_selected(self):
        items = self._selected_meta()
        if not items: return
        if len(items) == 1:
            QApplication.clipboard().setText(items[0]["url"])
            self.copy_bar.setText(f"Copied: {items[0]['url']}")
        else:
            urls = "\n".join(m["url"] for m in items)
            QApplication.clipboard().setText(urls)
            self.copy_bar.setText(f"Copied {len(items)} links.")
        self.copy_bar.show()

    def _delete_selected(self):
        items = self._selected_meta()
        if not items: return
        msg = (f"Delete share for {items[0]['file_name']!r}?"
               if len(items) == 1 else f"Delete {len(items)} shares?")
        if QMessageBox.question(self, "Confirm", msg,
                                QMessageBox.StandardButton.Yes |
                                QMessageBox.StandardButton.No
                                ) != QMessageBox.StandardButton.Yes:
            return
        import requests as _req
        api_key = self.get_api_key()
        for meta in items:
            try:
                _req.delete(f"{self.base_url}/api/shares/{meta['token']}",
                            headers={"Authorization": f"Bearer {api_key}"},
                            timeout=15).raise_for_status()
            except Exception as e:
                QMessageBox.warning(self, "Error", str(e)); return
        self.copy_bar.hide()
        self.refresh()

    def _status(self, msg):
        self.status_lbl.setText(msg)


# ── Main Window ───────────────────────────────────────────────────────────────
class MochaTools(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Mocha Tools")
        self.selected_files = []
        self.selected_root  = ""
        self.worker         = None
        self.settings       = QSettings(ORG_NAME, APP_NAME)
        self._build_ui()
        self._load_settings()

    def _build_ui(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)

        root_lay = QVBoxLayout(root)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # ── Tab widget with tabs at the BOTTOM (Android convention) ───────────
        self.tabs = QTabWidget()
        self.tabs.setTabPosition(QTabWidget.TabPosition.South)
        root_lay.addWidget(self.tabs)

        # ── Upload tab ────────────────────────────────────────────────────────
        upload_scroll = QScrollArea()
        upload_scroll.setWidgetResizable(True)
        upload_scroll.setFrameShape(QFrame.Shape.NoFrame)
        upload_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        main  = QVBoxLayout(inner)
        main.setContentsMargins(12, 12, 12, 16)
        main.setSpacing(10)
        upload_scroll.setWidget(inner)

        # FILE PICKER
        main.addWidget(self._header("File"))
        file_card = self._card()
        file_lay  = QVBoxLayout(file_card)
        self.file_picker = FilePicker()
        self.file_picker.selection_changed.connect(self._on_files_selected)
        file_lay.addWidget(self.file_picker)
        main.addWidget(file_card)

        # DESTINATION
        main.addWidget(self._header("Destination"))
        dest_card = self._card()
        dest_lay  = QVBoxLayout(dest_card)
        dest_row  = QHBoxLayout()
        self.upload_path_edit = QLineEdit("/")
        browse_btn = QPushButton("Browse…")
        browse_btn.setObjectName("browse_btn")
        browse_btn.setFixedWidth(90)
        browse_btn.clicked.connect(self._browse_upload_dest)
        dest_row.addWidget(self.upload_path_edit, 1)
        dest_row.addWidget(browse_btn)
        dest_lay.addLayout(dest_row)
        main.addWidget(dest_card)

        # STATUS
        main.addWidget(self._header("Status"))
        status_card = self._card()
        status_lay  = QVBoxLayout(status_card)
        status_lay.setSpacing(6)

        top_row = QHBoxLayout()
        self.status_badge = QLabel("● Ready")
        self.status_badge.setObjectName("status_badge")
        self.speed_label = QLabel("")
        self.speed_label.setStyleSheet("color:#9c9484; font-size:11px; background:transparent;")
        top_row.addWidget(self.status_badge)
        top_row.addStretch()
        top_row.addWidget(self.speed_label)
        status_lay.addLayout(top_row)

        prog_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.pct_label = QLabel("0%")
        self.pct_label.setObjectName("status_label")
        self.pct_label.setFixedWidth(36)
        prog_row.addWidget(self.progress_bar, 1)
        prog_row.addWidget(self.pct_label)
        status_lay.addLayout(prog_row)

        self.log_label = QLabel("Ready — select a file and tap Upload.")
        self.log_label.setObjectName("log_console")
        self.log_label.setWordWrap(True)
        self.log_label.setMinimumHeight(46)
        status_lay.addWidget(self.log_label)

        self.share_result = QLabel("")
        self.share_result.setObjectName("log_console")
        self.share_result.setWordWrap(True)
        self.share_result.setOpenExternalLinks(True)
        self.share_result.hide()
        status_lay.addWidget(self.share_result)
        main.addWidget(status_card)

        # SHARE OPTIONS
        share_card = self._card()
        share_lay  = QVBoxLayout(share_card)
        share_lay.setSpacing(8)
        self.create_share_cb = QCheckBox("Create share link after upload")
        self.create_share_cb.toggled.connect(self._toggle_share_options)
        share_lay.addWidget(self.create_share_cb)

        self.share_opts_widget = QWidget()
        sow_lay = QVBoxLayout(self.share_opts_widget)
        sow_lay.setContentsMargins(0, 4, 0, 0)
        sow_lay.setSpacing(6)

        exp_row = QHBoxLayout()
        exp_row.addWidget(QLabel("Expiration"))
        self.expiry_combo = QComboBox()
        self._expiry_map = [
            ("Never", None), ("1 hour", 1), ("6 hours", 6), ("12 hours", 12),
            ("1 day", 24), ("3 days", 72), ("7 days", 168), ("14 days", 336),
            ("30 days", 720),
        ]
        self.expiry_combo.addItems([l for l, _ in self._expiry_map])
        exp_row.addWidget(self.expiry_combo, 1)
        sow_lay.addLayout(exp_row)

        dl_row = QHBoxLayout()
        dl_row.addWidget(QLabel("Max downloads"))
        self.max_dl_spin = QSpinBox()
        self.max_dl_spin.setRange(0, 9999)
        self.max_dl_spin.setValue(0)
        self.max_dl_spin.setSpecialValueText("Unlimited")
        dl_row.addWidget(self.max_dl_spin, 1)
        sow_lay.addLayout(dl_row)

        share_lay.addWidget(self.share_opts_widget)
        self.share_opts_widget.hide()
        main.addWidget(share_card)

        # UPLOAD BUTTON
        self.upload_btn = QPushButton("↑  Upload")
        self.upload_btn.setObjectName("upload_btn")
        self.upload_btn.setMinimumHeight(52)
        self.upload_btn.clicked.connect(self._start_upload)
        main.addWidget(self.upload_btn)

        self.cancel_btn = QPushButton("✕  Cancel")
        self.cancel_btn.setObjectName("browse_btn")
        self.cancel_btn.setMinimumHeight(44)
        self.cancel_btn.clicked.connect(self._cancel_upload)
        self.cancel_btn.hide()
        main.addWidget(self.cancel_btn)
        main.addStretch()

        # ── Other tabs ────────────────────────────────────────────────────────
        self.remote_tab = RemoteTab(get_api_key=lambda: self.api_key_edit.text().strip())
        self.files_tab  = FilesBrowserTab(
            get_api_key=lambda: self.api_key_edit.text().strip(),
            get_upload_path=lambda: self.upload_path_edit.text().strip(),
            set_upload_path=lambda p: self.upload_path_edit.setText(p),
        )
        self.shares_tab = SharesTab(get_api_key=lambda: self.api_key_edit.text().strip())

        # ── Settings tab ──────────────────────────────────────────────────────
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        settings_inner = QWidget()
        sl = QVBoxLayout(settings_inner)
        sl.setContentsMargins(12, 12, 12, 16)
        sl.setSpacing(10)
        settings_scroll.setWidget(settings_inner)

        sl.addWidget(self._header("API"))
        api_card = self._card()
        api_lay  = QVBoxLayout(api_card)
        api_lay.setSpacing(8)
        sl.addWidget(api_card)

        key_row = QHBoxLayout()
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setPlaceholderText("mocha_your_api_key_here")
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_key_cb = QCheckBox("Show")
        self.show_key_cb.toggled.connect(
            lambda c: self.api_key_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if c else QLineEdit.EchoMode.Password))
        key_row.addWidget(self.api_key_edit, 1)
        key_row.addWidget(self.show_key_cb)
        api_lay.addLayout(key_row)
        self.remember_cb = QCheckBox("Remember settings")
        api_lay.addWidget(self.remember_cb)

        sl.addWidget(self._header("Logging"))
        debug_card = self._card()
        debug_lay  = QVBoxLayout(debug_card)
        self.debug_cb = QCheckBox("Enable debug logging")
        debug_lay.addWidget(self.debug_cb)
        sl.addWidget(debug_card)

        sl.addWidget(self._header("Multipart Upload"))
        chunk_card = self._card()
        chunk_lay  = QVBoxLayout(chunk_card)
        chunk_lay.setSpacing(8)
        cs_row = QHBoxLayout()
        cs_row.addWidget(QLabel("Chunk size"))
        self.chunk_size_spin = QSpinBox()
        self.chunk_size_spin.setRange(1, 100)
        self.chunk_size_spin.setValue(DEFAULT_CHUNK_SIZE_MB)
        self.chunk_size_spin.setSuffix(" MB")
        cs_row.addWidget(self.chunk_size_spin, 1)
        chunk_lay.addLayout(cs_row)
        mc_row = QHBoxLayout()
        mc_row.addWidget(QLabel("Max parallel chunks"))
        self.max_chunks_spin = QSpinBox()
        self.max_chunks_spin.setRange(1, 20)
        self.max_chunks_spin.setValue(DEFAULT_MAX_CHUNKS)
        self.max_chunks_spin.setSuffix(" chunks")
        mc_row.addWidget(self.max_chunks_spin, 1)
        chunk_lay.addLayout(mc_row)
        sl.addWidget(chunk_card)

        sl.addWidget(self._header(f"Version: {APP_VERSION}"))
        sl.addStretch()

        # ── Register tabs (icon labels only on mobile — short labels) ─────────
        self.tabs.addTab(upload_scroll,     "↑ Upload")
        self.tabs.addTab(self.remote_tab,   "⇣ Remote")
        self.tabs.addTab(self.files_tab,    "📁 Files")
        self.tabs.addTab(self.shares_tab,   "⤴ Shares")
        self.tabs.addTab(settings_scroll,   "⚙ Settings")
        self.tabs.currentChanged.connect(self._on_tab_changed)

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _header(self, text):
        lbl = QLabel(text.upper())
        lbl.setObjectName("section_header")
        return lbl

    def _card(self):
        f = QFrame(); f.setObjectName("card"); return f

    def _browse_upload_dest(self):
        api_key = self.api_key_edit.text().strip()
        if not api_key:
            self._log("⚠ Enter API key in Settings first."); return
        dlg = FolderBrowserDialog(api_key, HARDCODED_BASE_URL,
                                  self.upload_path_edit.text().strip() or "/",
                                  parent=self)
        if dlg.exec():
            self.upload_path_edit.setText(dlg.selected)

    def _toggle_share_options(self, checked):
        self.share_opts_widget.setVisible(checked)

    def _on_files_selected(self, file_list, root):
        self.selected_files = file_list
        self.selected_root  = root
        self.share_result.hide()

    # ── Settings ──────────────────────────────────────────────────────────────
    def _load_settings(self):
        self.api_key_edit.setText(self.settings.value("api_key", ""))
        self.upload_path_edit.setText(self.settings.value("upload_path", "/"))
        self.remote_tab.path_edit.setText(self.settings.value("remote_path", "/"))
        self.remember_cb.setChecked(self.settings.value("remember", False, type=bool))
        self.debug_cb.setChecked(self.settings.value("debug", False, type=bool))
        self.chunk_size_spin.setValue(
            self.settings.value("chunk_size_mb", DEFAULT_CHUNK_SIZE_MB, type=int))
        self.max_chunks_spin.setValue(
            self.settings.value("max_chunks", DEFAULT_MAX_CHUNKS, type=int))

    def _save_settings(self):
        self.settings.setValue("debug", self.debug_cb.isChecked())
        self.settings.setValue("chunk_size_mb", self.chunk_size_spin.value())
        self.settings.setValue("max_chunks",    self.max_chunks_spin.value())
        if self.remember_cb.isChecked():
            self.settings.setValue("api_key",     self.api_key_edit.text())
            self.settings.setValue("upload_path", self.upload_path_edit.text())
            self.settings.setValue("remote_path", self.remote_tab.path_edit.text())
            self.settings.setValue("remember",    True)
        else:
            for k in ("api_key", "upload_path", "remote_path", "remember"):
                self.settings.remove(k)

    # ── Upload ─────────────────────────────────────────────────────────────────
    def _start_upload(self):
        api_key = self.api_key_edit.text().strip()
        if not api_key:
            self._log("⚠ Enter your API key in Settings."); return
        if not self.selected_files:
            self._log("⚠ Select a file or folder first."); return

        dest_folder = self.upload_path_edit.text().strip() or "/"
        if not dest_folder.startswith("/"):
            dest_folder = "/" + dest_folder

        # Build (local_path, remote_dest_path) pairs
        file_pairs = []
        for local in self.selected_files:
            rel = os.path.relpath(local, self.selected_root)
            if rel.startswith(".."):
                rel = os.path.basename(local)
            remote = dest_folder.rstrip("/") + "/" + rel.replace("\\", "/")
            file_pairs.append((local, remote))

        expiry_hours = None
        if self.create_share_cb.isChecked():
            idx = self.expiry_combo.currentIndex()
            expiry_hours = self._expiry_map[idx][1]

        self.worker = UploadWorker(
            api_key, HARDCODED_BASE_URL, file_pairs,
            create_share=self.create_share_cb.isChecked(),
            share_expiry=expiry_hours,
            share_max_downloads=self.max_dl_spin.value(),
            chunk_size_mb=self.chunk_size_spin.value(),
            max_chunks=self.max_chunks_spin.value(),
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.speed.connect(self._on_speed)
        self.worker.status.connect(self._log)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()
        self._set_uploading(True)
        self._badge("Uploading…", "#c8a96e")

    def _cancel_upload(self):
        if self.worker:
            self.worker.cancel()
        self._set_uploading(False)
        self._badge("Cancelled", "#f87171")

    def _set_uploading(self, uploading):
        self.upload_btn.setVisible(not uploading)
        self.cancel_btn.setVisible(uploading)

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
        debug_enabled = self.debug_cb.isChecked()
        if msg.startswith("[DEBUG]") and not debug_enabled:
            return
        self.log_label.setText(msg)
        if debug_enabled:
            write_debug_log(msg)

    def _badge(self, text, color):
        self.status_badge.setText(f"● {text}")
        self.status_badge.setStyleSheet(
            f"background-color: {color}22; border: 1px solid {color}55; "
            f"border-radius: 0px; color: {color}; font-size: 11px; "
            f"font-weight: 600; padding: 2px 10px;")

    def _on_tab_changed(self, index):
        self.remote_tab.set_active(index == 1)
        if index == 2 and self.api_key_edit.text().strip():
            self.files_tab._refresh()
        elif index == 3 and self.api_key_edit.text().strip():
            self.shares_tab.refresh()
        elif index != 4:
            self._save_settings()

    def closeEvent(self, event):
        self._save_settings()
        self.remote_tab.set_active(False)
        super().closeEvent(event)
```

### 3.4 `dialogs_android.py`

Port of `dialogs.py` to PySide6 — only the import line changes (PyQt6 → PySide6) and `pyqtSignal` → `Signal`.

```python
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
```

### 3.5 `workers.py` — one import change only

In `workers.py`, every line that says:
```python
from PyQt6.QtCore import QThread, pyqtSignal
```
becomes:
```python
from PySide6.QtCore import QThread, Signal as pyqtSignal
```

That single alias (`Signal as pyqtSignal`) means you don't need to rename any signal declarations in the file. Everything else in `workers.py` stays byte-for-byte identical.

---

## Part 4: Build Configuration

### 4.1 `pysidedeploy.spec` (Qt's official Android deploy tool)

```ini
[app]
title = MochaTools
project_dir = .
input_file = main.py
project_file = 

[python]
python_path = /path/to/your/venv/bin/python3   # set to your venv
packages = requests,certifi,urllib3,charset-normalizer,idna,packaging
protected_packages =

[qt]
modules = Core,Gui,Widgets
plugins = platforms/android,imageformats/qjpeg
android_deploy_abi = arm64-v8a

[android]
wheel_pyside = /path/to/PySide6-6.7.x-cp311-cp311-android_arm64.whl
wheel_shiboken = /path/to/shiboken6-6.7.x-cp311-cp311-android_arm64.whl
ndk_path = ~/Android/Sdk/ndk/26.1.10909125
sdk_path = ~/Android/Sdk
build_tools_version = 34.0.0
android_min_api = 24          # Android 7.0 — covers 98%+ of active devices
android_target_api = 34
application_binary = org.mochatools.app
activity_name = .MochaToolsActivity
```

Download the Android-specific PySide6 wheels from:
```
https://download.qt.io/snapshots/ci/pyside/dev/latest/
# Look for: PySide6-*-android_arm64.whl and shiboken6-*-android_arm64.whl
```

### 4.2 Build command

```bash
source ~/mochatools-android-env/bin/activate
cd mocha_tools_android/

# Deploy (creates the APK)
pyside6-android-deploy \
    --name "MochaTools" \
    --input main.py \
    --android-ndk ~/Android/Sdk/ndk/26.1.10909125 \
    --android-sdk ~/Android/Sdk \
    --android-api 34 \
    --android-min-api 24 \
    --arch arm64-v8a \
    --release
```

The output APK will be in `android-build/build/outputs/apk/release/`.

### 4.3 `buildozer.spec` (alternative / backup method)

If the `pyside6-android-deploy` route has issues, Buildozer is a fallback. Note: Buildozer's native support for PySide6 is limited — it works best with `kivy` recipes. Use the `pyside6-android-deploy` route above first.

```ini
[app]
title = MochaTools
package.name = mochatools
package.domain = org.mochatools
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,spec
version = 0.1
requirements = python3,PySide6,requests,certifi,packaging
orientation = portrait
fullscreen = 0
android.permissions = INTERNET,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE
android.minapi = 24
android.ndk = 26b
android.sdk = 34
android.accept_sdk_license = True
log_level = 2
warn_on_root = 1
```

---

## Part 5: Android-Specific Gotchas

### 5.1 File access on Android 10+

Android's Scoped Storage (enforced from API 29+) restricts what `QFileDialog` can access. By default on Android, your app can only read files in:
- Its own `getExternalFilesDir()` — `QStandardPaths.StandardLocation.AppDataLocation`
- Files the user explicitly grants via the file picker

The existing `QFileDialog.getOpenFileName()` call in `FilePicker._pick_file()` will trigger Android's native file picker when compiled via Qt for Android. This works correctly and returns a content URI that Qt translates to a local path for you. **No changes needed** — Qt handles the URI-to-path translation transparently.

If you need to read from Downloads or arbitrary locations, add to `pysidedeploy.spec`:
```ini
android.permissions = INTERNET,READ_EXTERNAL_STORAGE,MANAGE_EXTERNAL_STORAGE
```

For API 30+, `MANAGE_EXTERNAL_STORAGE` requires a special Play Store declaration. For sideloading/testing, it works fine.

### 5.2 Network on Android

Android blocks all HTTP (non-HTTPS) traffic by default since API 28. Since `HARDCODED_BASE_URL = "https://mocha.my"` is already HTTPS, you're fine. No changes needed.

If you ever need HTTP for testing, add `android.manifest.usesCleartextTraffic = true` to `buildozer.spec`.

### 5.3 QSettings on Android

`QSettings` maps to Android SharedPreferences automatically when the organization name and application name are set (which your code already does). Settings persist across app launches with no changes needed.

### 5.4 Threading

`QThread` and Python's `threading` module both work on Android. The `ProgressTracker` class in `workers.py` uses `threading.Lock()` — this is fully supported.

### 5.5 `concurrent.futures.ThreadPoolExecutor`

Used heavily in `workers.py` for parallel chunk uploads. This works on Android, but be conservative with thread counts — mobile CPUs throttle under sustained load. The existing `DEFAULT_MAX_CHUNKS = 20` may be too aggressive; consider defaulting to 4–8 for Android. You can add a platform check in `constants.py`:

```python
import sys
_on_android = hasattr(sys, 'getandroidapilevel')
DEFAULT_MAX_CHUNKS = 6 if _on_android else 20
```

### 5.6 Splash screen / startup time

Python startup on Android is slow (5–10 seconds on first launch). Add a splash screen in `pysidedeploy.spec`:
```ini
android.presplash_color = #111010
android.presplash = assets/splash.png   # 1080×1920 PNG
```

### 5.7 Back button

Android's back button sends a `QCloseEvent` to the top-level widget. Your existing `closeEvent` in `MochaTools` handles this correctly (calls `_save_settings()`). No changes needed.

---

## Part 6: Testing Workflow

### 6.1 Test the UI on desktop first

Before building for Android, test the mobile UI on your desktop. The PySide6 code runs identically on Linux:

```bash
source ~/mochatools-android-env/bin/activate
cd mocha_tools_android/
python main.py
```

Resize the window to ~390×844 pixels (iPhone-like) to simulate portrait mode.

### 6.2 Test on an Android emulator

```bash
# Install AVD via Android Studio, then:
~/Android/Sdk/emulator/emulator -avd Pixel_6_API_34 &

# Install the debug APK
~/Android/Sdk/platform-tools/adb install \
    android-build/build/outputs/apk/debug/mochatools-debug.apk

# View logcat (filter to your app)
adb logcat | grep -i mochatools
```

### 6.3 Test on a physical device

```bash
# Enable Developer Mode + USB Debugging on the device, then:
adb devices          # confirm device is listed
adb install mochatools-debug.apk
adb logcat | grep -E "(python|PySide|mochatools)"
```

---

## Part 7: What's Identical (No Changes Required)

| File | Status | Notes |
|---|---|---|
| `workers.py` | ✅ 1-line change | `PyQt6` → `PySide6` import only |
| `constants.py` | ✅ Unchanged | Works as-is |
| `logging_utils.py` | ✅ Unchanged | `open()` works on Android |
| `__init__.py` | ✅ Unchanged | |
| `mochatools.py` (entry) | ✅ Replace with `main.py` | Entry point only |
| `styles.py` | ⚠ Minor tweaks | Touch target sizes (see Part 3.2) |
| `updater.py` | ❌ Drop entirely | Android updates via APK/Play Store |
| `app.py` | ❌ Replace | `app_android.py` is the replacement |
| `dialogs.py` | ❌ Replace | `dialogs_android.py` is the replacement |

---

## Summary

The port is straightforward because the app is already well-structured with a clean separation between business logic (`workers.py`) and UI (`app.py`). The entire networking, upload, and file management logic carries over untouched. The UI rewrite in `app_android.py` mirrors the desktop version structurally — same tabs, same cards, same signals — just adapted for portrait touch screens: bottom tabs, larger touch targets, 3-column trees instead of 5, and a button-based file picker instead of drag-and-drop.