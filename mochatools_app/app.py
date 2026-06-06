"""
Mocha Tools
A cross-platform PyQt6 application for uploading files to mocha
Written by nxllxvxxd && Bink-lab
To compile:
    python build.py

    DO NOT use --onefile on macOS — PyQt6 segfaults because Qt cannot
    locate its plugin/framework paths from the temp extraction directory.
    Use build.py which handles per-platform flags automatically:
      Windows / Linux : --onefile  (single executable, zipped)
      macOS           : --onedir   (packaged as .app bundle, zipped)

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
    QSplitter, QStackedWidget
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




# ── Lucide Icon Helper ────────────────────────────────────────────────────────
# Minimal subset of Lucide icon paths used in MochaTools.
# Each value is the SVG <path d="..."> content for a 24x24 viewBox icon.
_LUCIDE_PATHS = {
    "upload":        'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12',
    "download-cloud":'M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242M12 12v9M8 17l4 4 4-4',
    "folder":        'M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z',
    "share-2":       'M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14 21 3',
    "settings":      'M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
    "x":             'M18 6 6 18M6 6l12 12',
    "minus":         'M5 12h14',
    "square":        'M3 3h18v18H3z',
    "copy":          'M20 9h-9a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2v-9a2 2 0 0 0-2-2z M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1',
    "refresh-cw":    'M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8 M21 3v5h-5 M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16 M8 16H3v5',
    "trash-2":       'M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M10 11v6M14 11v6',
    "move":          'M5 9l-3 3 3 3M9 5l3-3 3 3M15 19l-3 3-3-3M19 9l3 3-3 3M2 12h20M12 2v20',
    "link":          'M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71 M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71',
    "coffee":        'M17 8h1a4 4 0 1 1 0 8h-1M3 8h14v9a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4V8zM6 1v3M10 1v3M14 1v3',
}

def lucide_icon(name: str, color: str = "#c8a96e", size: int = 16) -> "QIcon":
    """Return a QIcon rendered from a Lucide SVG path string."""
    from PyQt6.QtSvg import QSvgRenderer
    from PyQt6.QtGui import QIcon, QPixmap, QPainter
    from PyQt6.QtCore import QByteArray, QSize

    path_d = _LUCIDE_PATHS.get(name, "")
    # Build each space-separated sub-path as its own <path> element
    sub_paths = "".join(
        f'<path d="{p.strip()}" stroke="{color}" stroke-width="1.75" '
        f'stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
        for p in path_d.split("M") if p.strip()
        # Reconstruct "M..." prefix that was split off
    )
    # Rebuild properly without losing the M prefix
    svg_paths = ""
    segments = path_d.split(" M ")
    for i, seg in enumerate(segments):
        seg = seg.strip()
        if not seg:
            continue
        d = seg if i == 0 else "M " + seg
        svg_paths += (
            f'<path d="{d}" stroke="{color}" stroke-width="1.75" '
            f'stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
        )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'width="{size}" height="{size}">{svg_paths}</svg>'
    ).encode()

    renderer = QSvgRenderer(QByteArray(svg))
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    renderer.render(painter)
    painter.end()
    return QIcon(pm)


# ── Mass Upload Tab ───────────────────────────────────────────────────────────
class MassUploadTab(QWidget):
    """
    Queue-based multi-file uploader.

    • Drop files/folders onto the zone; each entry gets its own remote destination.
    • Concurrent files: how many upload simultaneously.
    • Chunk size + parallel chunks: independent of the global Settings values.
    • Double-click a queue row to edit its destination folder.
    """

    _COL_NAME   = 0
    _COL_SIZE   = 1
    _COL_DEST   = 2
    _COL_STATUS = 3

    def __init__(self, get_api_key, parent=None):
        super().__init__(parent)
        self.get_api_key = get_api_key
        self._queue: list[dict] = []
        self._active_workers: list = []
        self._pending_iter = iter([])
        self._cancelled = False
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        outer = QVBoxLayout(inner)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        scroll.setWidget(inner)
        root_lay = QVBoxLayout(self)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.addWidget(scroll)

        # ── Drop / add zone ──────────────────────────────────────────────────
        outer.addWidget(self._sh("Queue"))

        add_card = self._card()
        add_lay  = QVBoxLayout(add_card)
        add_lay.setSpacing(8)

        self._drop = DropZone()
        self._drop.selection_changed.connect(self._on_drop)
        add_lay.addWidget(self._drop)

        dest_row = QHBoxLayout()
        dest_lbl = QLabel("Default destination")
        dest_lbl.setObjectName("field_label")
        self._default_dest = QLineEdit("/")
        self._default_dest.setPlaceholderText("/remote/folder")
        browse_btn = QPushButton("Browse…")
        browse_btn.setObjectName("browse_btn")
        browse_btn.setFixedSize(80, 34)
        browse_btn.clicked.connect(self._browse_default_dest)
        dest_row.addWidget(dest_lbl)
        dest_row.addWidget(self._default_dest, 1)
        dest_row.addWidget(browse_btn)
        add_lay.addLayout(dest_row)
        outer.addWidget(add_card)

        # ── Queue table ──────────────────────────────────────────────────────
        self._tree = QTreeWidget()
        self._tree.setColumnCount(4)
        self._tree.setHeaderLabels(["File / Folder", "Size", "Destination", "Status"])
        self._tree.setRootIsDecorated(False)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        hdr = self._tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.resizeSection(2, 160)
        self._tree.setMinimumHeight(160)
        self._tree.itemDoubleClicked.connect(self._edit_dest)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._queue_context_menu)
        outer.addWidget(self._tree, 1)

        # Queue toolbar
        qtb = QHBoxLayout()
        qtb.setSpacing(6)

        rm_btn = QPushButton("Remove selected")
        rm_btn.setObjectName("tb_btn_danger")
        rm_btn.clicked.connect(self._remove_selected)
        qtb.addWidget(rm_btn)

        done_btn = QPushButton("Clear done")
        done_btn.setObjectName("tb_btn")
        done_btn.clicked.connect(self._clear_done)
        qtb.addWidget(done_btn)

        all_btn = QPushButton("Clear all")
        all_btn.setObjectName("tb_btn_danger")
        all_btn.clicked.connect(self._clear_all)
        qtb.addWidget(all_btn)

        qtb.addSpacing(8)

        up_btn = QPushButton("▲ Move up")
        up_btn.setObjectName("tb_btn")
        up_btn.clicked.connect(self._move_selected_up)
        qtb.addWidget(up_btn)

        dn_btn = QPushButton("▼ Move down")
        dn_btn.setObjectName("tb_btn")
        dn_btn.clicked.connect(self._move_selected_down)
        qtb.addWidget(dn_btn)

        qtb.addStretch()
        self._queue_lbl = QLabel("0 items")
        self._queue_lbl.setStyleSheet("color:#9c9484; font-size:11px; background:transparent;")
        qtb.addWidget(self._queue_lbl)
        outer.addLayout(qtb)

        # ── Upload options ────────────────────────────────────────────────────
        outer.addWidget(self._sh("Upload Options"))
        opts_card = self._card()
        opts_lay  = QVBoxLayout(opts_card)
        opts_lay.setSpacing(10)

        note = QLabel(
            "These settings apply only to Mass Upload and are independent of "
            "the global Settings values."
        )
        note.setObjectName("field_label")
        note.setWordWrap(True)
        opts_lay.addWidget(note)

        grid = QHBoxLayout()
        grid.setSpacing(16)

        def _spin_col(label, lo, hi, default, suffix, tip):
            col = QVBoxLayout()
            col.setSpacing(4)
            lbl = QLabel(label)
            lbl.setObjectName("field_label")
            sp = QSpinBox()
            sp.setRange(lo, hi)
            sp.setValue(default)
            sp.setSuffix(f" {suffix}")
            sp.setToolTip(tip)
            col.addWidget(lbl)
            col.addWidget(sp)
            grid.addLayout(col)
            return sp

        self._conc_spin = _spin_col(
            "Concurrent files", 1, 10, 2, "files",
            "How many files upload at the same time.\n"
            "Higher values can saturate slower connections."
        )
        self._chunk_spin = _spin_col(
            "Chunk size", 1, 100, DEFAULT_CHUNK_SIZE_MB, "MB",
            "Size of each multipart part (1–100 MB).\n"
            "Files smaller than this upload in one request."
        )
        self._maxchunk_spin = _spin_col(
            "Parallel chunks", 1, 20, DEFAULT_MAX_CHUNKS, "chunks",
            "Max parts sent in parallel per file (1–20)."
        )
        grid.addStretch()
        opts_lay.addLayout(grid)
        outer.addWidget(opts_card)

        # ── Progress card ─────────────────────────────────────────────────────
        prog_card = self._card()
        prog_lay  = QVBoxLayout(prog_card)
        prog_lay.setSpacing(6)

        top_row = QHBoxLayout()
        self._badge_lbl = QLabel("● Idle")
        self._badge_lbl.setObjectName("status_badge")
        top_row.addWidget(self._badge_lbl)
        top_row.addStretch()
        self._speed_lbl = QLabel("")
        self._speed_lbl.setStyleSheet("color:#9ca3af; font-size:11px; background:transparent;")
        top_row.addWidget(self._speed_lbl)
        self._transferred_lbl = QLabel("")
        self._transferred_lbl.setStyleSheet("color:#9ca3af; font-size:11px; background:transparent; margin-left:10px;")
        top_row.addWidget(self._transferred_lbl)
        prog_lay.addLayout(top_row)

        pbar_row = QHBoxLayout()
        self._prog_bar = QProgressBar()
        self._prog_bar.setValue(0)
        self._pct_lbl = QLabel("0%")
        self._pct_lbl.setObjectName("status_label")
        self._pct_lbl.setFixedWidth(36)
        pbar_row.addWidget(self._prog_bar, 1)
        pbar_row.addWidget(self._pct_lbl)
        prog_lay.addLayout(pbar_row)

        self._log_lbl = QLabel("Add files or folders above, then click Start.")
        self._log_lbl.setObjectName("log_console")
        self._log_lbl.setWordWrap(True)
        self._log_lbl.setMinimumHeight(46)
        prog_lay.addWidget(self._log_lbl)
        outer.addWidget(prog_card)

        # ── Buttons ───────────────────────────────────────────────────────────
        self._start_btn = QPushButton("  Start upload")
        self._start_btn.setObjectName("upload_btn")
        self._start_btn.setIcon(lucide_icon("upload", "#111010", 15))
        self._start_btn.setIconSize(QSize(15, 15))
        self._start_btn.setMinimumHeight(42)
        self._start_btn.clicked.connect(self._start)
        outer.addWidget(self._start_btn)

        self._cancel_btn = QPushButton("  Cancel")
        self._cancel_btn.setObjectName("browse_btn")
        self._cancel_btn.setIcon(lucide_icon("x", "#c8a96e", 13))
        self._cancel_btn.setIconSize(QSize(13, 13))
        self._cancel_btn.setMinimumHeight(36)
        self._cancel_btn.clicked.connect(self._cancel)
        self._cancel_btn.hide()
        outer.addWidget(self._cancel_btn)

    # ── Helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _sh(text):
        lbl = QLabel(text.upper())
        lbl.setObjectName("section_header")
        return lbl

    @staticmethod
    def _card():
        f = QFrame()
        f.setObjectName("card")
        return f

    @staticmethod
    def _fmt(n):
        if n < 1024:       return f"{n} B"
        elif n < 1024**2:  return f"{n/1024:.1f} KB"
        elif n < 1024**3:  return f"{n/1024**2:.1f} MB"
        return f"{n/1024**3:.2f} GB"

    def _set_badge(self, text, color):
        self._badge_lbl.setText(f"● {text}")
        bg = {"#c8a96e":"#2a2215","#4ade80":"#0f2318","#f87171":"#2a0f0f","#9ca3af":"#1e1c19"}
        bd = {"#c8a96e":"#4a3b1e","#4ade80":"#1e4a30","#f87171":"#4a1e1e","#9ca3af":"#2e2b27"}
        self._badge_lbl.setStyleSheet(
            f"background-color:{bg.get(color,'#1e1c19')};"
            f"border:1px solid {bd.get(color,'#2e2b27')};"
            f"border-radius:10px; color:{color}; font-size:11px;"
            f"font-weight:600; padding:2px 10px;"
        )

    def _log(self, msg):
        self._log_lbl.setText(msg)

    def _update_queue_label(self):
        total  = len(self._queue)
        done   = sum(1 for e in self._queue if e["status"] == "done")
        errors = sum(1 for e in self._queue if e["status"] == "error")
        parts  = [f"{total} item{'s' if total != 1 else ''}"]
        if done:   parts.append(f"{done} done")
        if errors: parts.append(f"{errors} failed")
        self._queue_lbl.setText(" · ".join(parts))

    def _update_overall_progress(self):
        if not self._queue:
            return
        total_pct = 0
        for e in self._queue:
            if e["status"] in ("done",):
                total_pct += 100
            elif e["status"] in ("error", "cancelled"):
                total_pct += 100
            else:
                total_pct += e.get("_pct", 0)
        pct = int(total_pct / len(self._queue))
        self._prog_bar.setValue(pct)
        self._pct_lbl.setText(f"{pct}%")

    def _on_file_progress(self, pct, entry):
        entry["_pct"] = pct
        entry["item"].setText(
            self._COL_STATUS,
            f"{pct}%  ·  {entry.get('_xfr', '')}",
        )
        self._update_overall_progress()

    # ── Queue management ──────────────────────────────────────────────────────
    def _on_drop(self, file_list, root):
        dest_base = "/" + (self._default_dest.text().strip("/") or "")
        for local in file_list:
            rel = os.path.relpath(local, root).replace(os.sep, "/")
            if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
                rel = os.path.basename(local)
            rdest = f"{dest_base}/{rel}" if dest_base != "/" else f"/{rel}"
            entry = {
                "local": local, "root": root, "dest": rdest,
                "size": os.path.getsize(local),
                "status": "pending", "worker": None, "item": None,
                "_bytes_done": 0,
                "_bytes_total": os.path.getsize(local),
            }
            self._queue.append(entry)
            display_name = os.path.relpath(local, root).replace(os.sep, "/")
            item = QTreeWidgetItem([
                display_name, self._fmt(entry["size"]), rdest, "Pending",
            ])
            item.setForeground(3, QColor("#5a5650"))
            entry["item"] = item
            self._tree.addTopLevelItem(item)
        self._update_queue_label()

    def _browse_default_dest(self):
        api_key = self.get_api_key()
        if not api_key:
            self._log("⚠ Enter API key in Settings first.")
            return
        dlg = FolderBrowserDialog(api_key, HARDCODED_BASE_URL,
                                  self._default_dest.text().strip() or "/",
                                  parent=self)
        dlg.setWindowTitle("Choose default destination")
        if dlg.exec():
            self._default_dest.setText(dlg.selected)

    def _edit_dest(self, item, _col):
        row = next((e for e in self._queue if e["item"] is item), None)
        if row is None or row["status"] in ("uploading", "done"):
            return
        api_key = self.get_api_key()
        if api_key:
            dlg = FolderBrowserDialog(
                api_key, HARDCODED_BASE_URL,
                row["dest"].rsplit("/", 1)[0] or "/",
                parent=self,
            )
            dlg.setWindowTitle("Choose destination folder")
            if dlg.exec():
                # Keep the original filename, swap the folder
                filename = row["dest"].rsplit("/", 1)[-1]
                folder   = dlg.selected.rstrip("/")
                row["dest"] = f"{folder}/{filename}"
                item.setText(self._COL_DEST, row["dest"])
        else:
            new_dest, ok = QInputDialog.getText(
                self, "Edit destination", "Remote destination path:",
                QLineEdit.EchoMode.Normal, row["dest"],
            )
            if ok and new_dest.strip():
                row["dest"] = new_dest.strip()
                item.setText(self._COL_DEST, row["dest"])

    def _queue_context_menu(self, pos):
        item = self._tree.itemAt(pos)
        menu = QMenu(self)
        if item:
            entry = next((e for e in self._queue if e["item"] is item), None)
            idx   = self._tree.indexOfTopLevelItem(item)
            count = self._tree.topLevelItemCount()

            move_up = menu.addAction("▲  Move up")
            move_up.setEnabled(idx > 0 and entry and entry["status"] == "pending")
            move_up.triggered.connect(self._move_selected_up)

            move_dn = menu.addAction("▼  Move down")
            move_dn.setEnabled(idx < count - 1 and entry and entry["status"] == "pending")
            move_dn.triggered.connect(self._move_selected_down)

            menu.addSeparator()

            edit_dest = menu.addAction("✎  Edit destination")
            edit_dest.setEnabled(entry and entry["status"] not in ("uploading", "done"))
            edit_dest.triggered.connect(lambda: self._edit_dest(item, 0))

            menu.addSeparator()

            rm = menu.addAction("✕  Remove")
            rm.setEnabled(entry and entry["status"] != "uploading")
            rm.triggered.connect(self._remove_selected)
        else:
            clr_done = menu.addAction("Clear done")
            clr_done.triggered.connect(self._clear_done)
            clr_all  = menu.addAction("Clear all")
            clr_all.triggered.connect(self._clear_all)

        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _move_entry(self, delta):
        """Move each selected pending entry by delta (-1 up, +1 down) in both the
        internal queue list and the tree widget, preserving relative order."""
        selected = [
            e for e in self._queue
            if e["item"] in self._tree.selectedItems() and e["status"] == "pending"
        ]
        if not selected:
            return
        # Process in reverse order when moving down so we don't overwrite each other
        if delta > 0:
            selected = list(reversed(selected))
        for entry in selected:
            qi = self._queue.index(entry)
            ti = self._tree.indexOfTopLevelItem(entry["item"])
            new_qi = qi + delta
            new_ti = ti + delta
            if new_qi < 0 or new_qi >= len(self._queue):
                continue
            if new_ti < 0 or new_ti >= self._tree.topLevelItemCount():
                continue
            # Swap in queue list
            self._queue[qi], self._queue[new_qi] = self._queue[new_qi], self._queue[qi]
            # Swap in tree widget (take + re-insert)
            taken = self._tree.takeTopLevelItem(ti)
            self._tree.insertTopLevelItem(new_ti, taken)
            self._tree.setCurrentItem(taken)

    def _move_selected_up(self):
        self._move_entry(-1)

    def _move_selected_down(self):
        self._move_entry(1)

    def _remove_selected(self):
        for item in list(self._tree.selectedItems()):
            row = next((e for e in self._queue if e["item"] is item), None)
            if row and row["status"] == "uploading":
                continue
            if row:
                self._queue.remove(row)
            idx = self._tree.indexOfTopLevelItem(item)
            if idx >= 0:
                self._tree.takeTopLevelItem(idx)
        self._update_queue_label()

    def _clear_done(self):
        for entry in list(self._queue):
            if entry["status"] in ("done","error","cancelled"):
                idx = self._tree.indexOfTopLevelItem(entry["item"])
                if idx >= 0:
                    self._tree.takeTopLevelItem(idx)
                self._queue.remove(entry)
        self._update_queue_label()

    def _clear_all(self):
        if self._active_workers:
            self._cancel()
        self._tree.clear()
        self._queue.clear()
        self._prog_bar.setValue(0)
        self._pct_lbl.setText("0%")
        self._speed_lbl.setText("")
        self._transferred_lbl.setText("")
        self._update_queue_label()
        self._set_badge("Idle", "#9ca3af")
        self._log("Queue cleared.")

    # ── Upload engine ─────────────────────────────────────────────────────────
    def _start(self):
        api_key = self.get_api_key()
        if not api_key:
            self._log("⚠ Enter your API key in Settings first.")
            return
        pending = [e for e in self._queue if e["status"] == "pending"]
        if not pending:
            self._log("⚠ No pending items in the queue.")
            return
        self._cancelled = False
        self._start_btn.hide()
        self._cancel_btn.show()
        self._set_badge("Uploading", "#c8a96e")
        self._log(f"Starting {len(pending)} upload{'s' if len(pending)!=1 else ''}…")
        self._pending_iter = iter(pending)
        for _ in range(min(self._conc_spin.value(), len(pending))):
            self._launch_next(api_key)

    def _launch_next(self, api_key=None):
        if self._cancelled:
            return
        api_key = api_key or self.get_api_key()
        while True:
            try:
                entry = next(self._pending_iter)
            except StopIteration:
                return
            # Entry may have been removed from the queue while we were uploading
            # other files — skip it rather than starting a ghost upload.
            if entry not in self._queue:
                continue
            break
        entry["status"] = "uploading"
        entry["_bytes_done"]  = 0
        # _bytes_total was already seeded from the real file size at queue-add
        # time; only update it if it wasn't set for some reason.
        if not entry.get("_bytes_total"):
            entry["_bytes_total"] = entry.get("size", 0)
        entry["item"].setText(self._COL_STATUS, "Uploading…")
        entry["item"].setForeground(3, QColor("#c8a96e"))

        w = UploadWorker(
            api_key, HARDCODED_BASE_URL,
            [(entry["local"], entry["dest"])],
            False, None, 0,
            chunk_size_mb=self._chunk_spin.value(),
            max_chunks=self._maxchunk_spin.value(),
        )
        entry["worker"] = w
        self._active_workers.append(w)
        w.progress.connect(lambda pct, e=entry: self._on_file_progress(pct, e))
        w.speed.connect(self._on_speed)
        w.status.connect(lambda msg, e=entry: self._log(msg) if not msg.startswith("[DEBUG]") else None)
        w.finished.connect(lambda result, e=entry: self._on_file_done(e))
        w.error.connect(lambda msg, e=entry: self._on_file_error(msg, e))
        if hasattr(w, "bytes_progress"):
            w.bytes_progress.connect(lambda done, total, e=entry: self._on_file_bytes(done, total, e))
        w.start()

    def _on_speed(self, bps):
        if bps < 1024:         txt = f"{bps:.0f} B/s"
        elif bps < 1024**2:    txt = f"{bps/1024:.1f} KB/s"
        else:                  txt = f"{bps/1024**2:.2f} MB/s"
        self._speed_lbl.setText(txt)

    def _on_file_bytes(self, done_bytes, total_bytes, entry):
        # Ignore stale signals that arrive after the entry has already finished
        # (parallel chunk threads can still be in-flight when done() fires).
        if entry["status"] in ("done", "error", "cancelled"):
            return
        # Clamp: total_bytes from the tracker is authoritative; never let
        # done exceed it even if a retry briefly re-feeds bytes.
        if total_bytes > 0:
            entry["_bytes_total"] = total_bytes
        done_bytes = min(done_bytes, entry["_bytes_total"])
        entry["_bytes_done"] = done_bytes
        entry["_xfr"] = f"{self._fmt(done_bytes)} / {self._fmt(entry['_bytes_total'])}"
        # Sum across every entry — pending entries already have _bytes_total
        # initialised from their file size at queue-add time, so this always
        # reflects the true queue-wide transferred / total.
        all_done  = sum(e.get("_bytes_done",  0) for e in self._queue)
        all_total = sum(e.get("_bytes_total", 0) for e in self._queue)
        if all_total > 0:
            self._transferred_lbl.setText(
                f"{self._fmt(all_done)} / {self._fmt(all_total)}"
            )

    def _on_file_done(self, entry):
        entry["status"] = "done"
        # Snap byte counter to 100% so the transferred label never
        # shows less than full for a completed file.
        entry["_bytes_done"] = entry.get("_bytes_total", entry.get("size", 0))
        entry["item"].setText(self._COL_STATUS, "✓ Done")
        entry["item"].setForeground(3, QColor("#4ade80"))
        if entry["worker"] in self._active_workers:
            self._active_workers.remove(entry["worker"])
        self._update_queue_label()
        self._update_overall_progress()
        self._launch_next()
        self._check_all_done()

    def _on_file_error(self, msg, entry):
        entry["status"] = "error"
        entry["item"].setText(self._COL_STATUS, "✗ Failed")
        entry["item"].setForeground(3, QColor("#f87171"))
        entry["item"].setToolTip(self._COL_STATUS, msg)
        if entry["worker"] in self._active_workers:
            self._active_workers.remove(entry["worker"])
        self._log(f"✗ {os.path.basename(entry['local'])}: {msg}")
        self._update_queue_label()
        self._update_overall_progress()
        self._launch_next()
        self._check_all_done()

    def _check_all_done(self):
        if self._active_workers:
            return
        if any(e["status"] == "pending" for e in self._queue):
            return
        errors = sum(1 for e in self._queue if e["status"] == "error")
        self._start_btn.show()
        self._cancel_btn.hide()
        self._speed_lbl.setText("")
        self._transferred_lbl.setText("")
        if errors:
            self._set_badge(f"Done ({errors} failed)", "#f87171")
            self._log(f"✓ Queue finished — {errors} file(s) failed.")
        else:
            self._set_badge("Complete", "#4ade80")
            self._log("✓ All uploads complete.")

    def _cancel(self):
        self._cancelled = True
        for w in list(self._active_workers):
            try:
                w.cancel()
                w.progress.disconnect()
                w.speed.disconnect()
                w.status.disconnect()
                w.finished.disconnect()
                w.error.disconnect()
                if hasattr(w, "bytes_progress"):
                    try: w.bytes_progress.disconnect()
                    except RuntimeError: pass
            except RuntimeError:
                pass
        self._active_workers.clear()
        for entry in self._queue:
            if entry["status"] == "uploading":
                entry["status"] = "cancelled"
                entry["item"].setText(self._COL_STATUS, "Cancelled")
                entry["item"].setForeground(3, QColor("#9ca3af"))
        self._start_btn.show()
        self._cancel_btn.hide()
        self._speed_lbl.setText("")
        self._transferred_lbl.setText("")
        self._set_badge("Cancelled", "#9ca3af")
        self._log("Upload cancelled.")
        self._update_queue_label()


# ── Full-Width Tab Widget ─────────────────────────────────────────────────────
class FullWidthTabWidget(QWidget):
    """
    Drop-in QTabWidget replacement whose tab bar always fills the full widget
    width — no bare gap to the right of the last tab.
    """
    currentChanged = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tabs: list[tuple[QPushButton, QWidget]] = []
        self._current = -1

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._bar = QWidget()
        self._bar.setObjectName("tabbar_row")
        self._bar.setStyleSheet(
            "QWidget#tabbar_row {"
            "  background: #181614;"
            "  border-bottom: 1px solid #2e2b27;"
            "}"
        )
        self._bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._bar_lay = QHBoxLayout(self._bar)
        self._bar_lay.setContentsMargins(0, 0, 0, 0)
        self._bar_lay.setSpacing(0)
        outer.addWidget(self._bar)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet("QStackedWidget { background: #181614; }")
        outer.addWidget(self._stack, 1)

    def addTab(self, widget: QWidget, label: str) -> int:
        idx = len(self._tabs)
        btn = QPushButton(label)
        btn.setCheckable(True)
        btn.setObjectName("tab_btn")
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn.setStyleSheet(self._btn_style(False))
        btn.clicked.connect(lambda _checked, i=idx: self.setCurrentIndex(i))
        self._bar_lay.addWidget(btn)
        self._stack.addWidget(widget)
        self._tabs.append((btn, widget))
        if idx == 0:
            self.setCurrentIndex(0)
        return idx

    def setTabIcon(self, index: int, icon):
        if 0 <= index < len(self._tabs):
            self._tabs[index][0].setIcon(icon)
            self._tabs[index][0].setIconSize(QSize(14, 14))

    def setIconSize(self, size): pass
    def tabBar(self): return self
    def setExpanding(self, _): pass
    def setDrawBase(self, _): pass
    def setCornerWidget(self, *_): pass

    def currentIndex(self) -> int:
        return self._current

    def setCurrentIndex(self, index: int):
        if index == self._current:
            return
        old = self._current
        self._current = index
        for i, (btn, _) in enumerate(self._tabs):
            active = (i == index)
            btn.setChecked(active)
            btn.setStyleSheet(self._btn_style(active))
        self._stack.setCurrentIndex(index)
        if old != index:
            self.currentChanged.emit(index)

    @staticmethod
    def _btn_style(active: bool) -> str:
        if active:
            return (
                "QPushButton { background:transparent; color:#c8a96e; border:none;"
                " border-bottom:2px solid #c8a96e; padding:11px 22px 9px 22px;"
                " font-size:12px; font-weight:600; letter-spacing:0.2px; border-radius:0px; }"
            )
        return (
            "QPushButton { background:transparent; color:#5a5650; border:none;"
            " border-bottom:2px solid transparent; padding:11px 22px 9px 22px;"
            " font-size:12px; font-weight:600; letter-spacing:0.2px; border-radius:0px; }"
            "QPushButton:hover { color:#9c9484; border-bottom:2px solid #3d3a35; }"
        )


# ── Custom Title Bar ──────────────────────────────────────────────────────────
class CustomTitleBar(QFrame):
    """Frameless window titlebar with drag-to-move, minimise and close."""

    def __init__(self, window: QMainWindow, app_name: str, version: str, parent=None):
        super().__init__(parent)
        self._window    = window
        self._drag_pos  = None
        self.setObjectName("titlebar")
        self.setFixedHeight(42)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 8, 0)
        lay.setSpacing(0)

        # Coffee icon + app name
        icon_lbl = QLabel()
        icon_lbl.setPixmap(lucide_icon("coffee", "#c8a96e", 15).pixmap(QSize(15, 15)))
        icon_lbl.setStyleSheet("background:transparent; padding-right:6px;")
        lay.addWidget(icon_lbl)

        name_lbl = QLabel(app_name)
        name_lbl.setObjectName("title_app_name")
        lay.addWidget(name_lbl)

        sep = QLabel(" ")
        sep.setStyleSheet("background:transparent;")
        lay.addWidget(sep)

        ver_lbl = QLabel(version)
        ver_lbl.setObjectName("title_version")
        lay.addWidget(ver_lbl)

        lay.addStretch()

        # Minimise button
        self._min_btn = QPushButton()
        self._min_btn.setObjectName("tb_minmax")
        self._min_btn.setIcon(lucide_icon("minus", "#5a5650", 13))
        self._min_btn.setIconSize(QSize(13, 13))
        self._min_btn.setToolTip("Minimise")
        self._min_btn.clicked.connect(window.showMinimized)
        lay.addWidget(self._min_btn)

        # Maximise/restore button
        self._max_btn = QPushButton()
        self._max_btn.setObjectName("tb_minmax")
        self._max_btn.setIcon(lucide_icon("square", "#5a5650", 11))
        self._max_btn.setIconSize(QSize(11, 11))
        self._max_btn.setToolTip("Maximise")
        self._max_btn.clicked.connect(self._toggle_maximise)
        lay.addWidget(self._max_btn)

        # Close button
        self._close_btn = QPushButton()
        self._close_btn.setObjectName("tb_close")
        self._close_btn.setIcon(lucide_icon("x", "#5a5650", 13))
        self._close_btn.setIconSize(QSize(13, 13))
        self._close_btn.setToolTip("Close")
        self._close_btn.clicked.connect(window.close)
        lay.addWidget(self._close_btn)

    def _toggle_maximise(self):
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()
        self._sync_max_icon()

    def _sync_max_icon(self):
        """Swap the maximise icon between square (restore) and square (max) states."""
        if self._window.isMaximized():
            self._max_btn.setToolTip("Restore")
            self._max_btn.setIcon(lucide_icon("square", "#9c9484", 11))
        else:
            self._max_btn.setToolTip("Maximise")
            self._max_btn.setIcon(lucide_icon("square", "#5a5650", 11))

    # ── Drag-to-move ──────────────────────────────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            self._window.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximise()


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
        """Pop a small menu so the user can choose file(s) or folder(s)."""
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        act_file   = menu.addAction("📄  Select files…")
        act_folder = menu.addAction("📁  Select folder…")
        chosen = menu.exec(self.mapToGlobal(self.rect().center()))
        if chosen == act_file:
            paths, _ = QFileDialog.getOpenFileNames(self, "Select files")
            if paths:
                # Use the common parent directory as root
                root = os.path.commonpath(paths) if len(paths) > 1 else os.path.dirname(paths[0])
                if os.path.isfile(root):
                    root = os.path.dirname(root)
                self._set_paths(paths, root, is_folder=False)
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
        elif is_folder:
            # Folder was selected (may contain 1 or more files)
            total = sum(os.path.getsize(p) for p in file_list)
            label = f"{name}/  —  {len(file_list)} files  ({UploadWorker._fmt_size(total)})"
            # For a folder, set selected_root to the parent so the folder name is preserved
            selected_root = os.path.dirname(root.rstrip("/\\"))
        else:
            # Multiple files selected individually
            total = sum(os.path.getsize(p) for p in file_list)
            label = f"{len(file_list)} files selected  ({UploadWorker._fmt_size(total)})"
            selected_root = root
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

        self.refresh_btn  = self._tb("Refresh",      "refresh-cw", self._refresh)
        self.mkdir_btn    = self._tb("New Folder",   "folder",     self._create_folder)
        self.move_btn     = self._tb("Move",         "move",       self._move_selected)
        self.share_btn    = self._tb("Share",        "share-2",    self._share_selected)
        self.delete_btn   = self._tb("Delete",       "trash-2",    self._delete_selected, danger=True)

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

    def _tb(self, label, icon_name, slot, danger=False):
        btn = QPushButton(f"  {label}")
        btn.setObjectName("tb_btn_danger" if danger else "tb_btn")
        color = "#f87171" if danger else "#9c9484"
        btn.setIcon(lucide_icon(icon_name, color, 13))
        btn.setIconSize(QSize(13, 13))
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
            QMenu::item:selected { background:#332b1a; }
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
        dest_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        dest_lbl = QLabel("Folder")
        dest_lbl.setObjectName("field_label")
        self.path_edit = QLineEdit()
        self.path_edit.setText("/")
        browse_btn = QPushButton("Browse…")
        browse_btn.setObjectName("browse_btn")
        browse_btn.setFixedSize(80, 34)
        browse_btn.clicked.connect(self._browse_dest)
        dest_row.addWidget(dest_lbl)
        dest_row.addWidget(self.path_edit, 1)
        dest_row.addWidget(browse_btn)
        ingest_lay.addLayout(dest_row)

        self.ingest_btn = QPushButton("  Remote ingest")
        self.ingest_btn.setObjectName("upload_btn")
        self.ingest_btn.setIcon(lucide_icon("download-cloud", "#111010", 15))
        self.ingest_btn.setIconSize(QSize(15, 15))
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
        self.refresh_btn = QPushButton("  Refresh Jobs")
        self.refresh_btn.setObjectName("tb_btn")
        self.refresh_btn.setIcon(lucide_icon("refresh-cw", "#9c9484", 13))
        self.refresh_btn.setIconSize(QSize(13, 13))
        self.refresh_btn.clicked.connect(self.refresh_jobs)
        tb.addWidget(self.refresh_btn)

        self.cancel_btn = QPushButton("  Cancel Job")
        self.cancel_btn.setObjectName("tb_btn_danger")
        self.cancel_btn.setIcon(lucide_icon("x", "#f87171", 13))
        self.cancel_btn.setIconSize(QSize(13, 13))
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

        self.refresh_btn = QPushButton("  Refresh")
        self.refresh_btn.setObjectName("tb_btn")
        self.refresh_btn.setIcon(lucide_icon("refresh-cw", "#9c9484", 13))
        self.refresh_btn.setIconSize(QSize(13, 13))
        self.refresh_btn.clicked.connect(self.refresh)
        tb.addWidget(self.refresh_btn)

        self.copy_btn = QPushButton("  Copy Link")
        self.copy_btn.setObjectName("tb_btn")
        self.copy_btn.setIcon(lucide_icon("copy", "#9c9484", 13))
        self.copy_btn.setIconSize(QSize(13, 13))
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._copy_selected)
        tb.addWidget(self.copy_btn)

        self.toggle_btn = QPushButton("  Toggle Active")
        self.toggle_btn.setObjectName("tb_btn")
        self.toggle_btn.setIcon(lucide_icon("link", "#9c9484", 13))
        self.toggle_btn.setIconSize(QSize(13, 13))
        self.toggle_btn.setEnabled(False)
        self.toggle_btn.clicked.connect(self._toggle_selected)
        tb.addWidget(self.toggle_btn)

        self.delete_btn = QPushButton("  Delete")
        self.delete_btn.setObjectName("tb_btn_danger")
        self.delete_btn.setIcon(lucide_icon("trash-2", "#f87171", 13))
        self.delete_btn.setIconSize(QSize(13, 13))
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
            QMenu::item:selected { background:#332b1a; }
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
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
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

        # ── Custom titlebar ──────────────────────────────────────────────────
        self.titlebar = CustomTitleBar(self, APP_NAME, APP_VERSION)
        root_lay.addWidget(self.titlebar)

        # ── Tab widget ───────────────────────────────────────────────────────
        self.tabs = FullWidthTabWidget()
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

        # ── Mass Upload tab ──────────────────────────────────────────────────
        self.mass_upload_tab = MassUploadTab(
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

        self.tabs.addTab(upload_tab,           "Upload")
        self.tabs.addTab(self.mass_upload_tab, "Mass Upload")
        self.tabs.addTab(self.remote_tab,      "Remote")
        self.tabs.addTab(self.files_tab,       "Files")
        self.tabs.addTab(self.shares_tab,      "Shares")
        self.tabs.addTab(settings_tab,         "Settings")

        # Set Lucide icons on each tab
        _tab_icons = [
            ("upload",        "#9c9484"),
            ("upload",        "#9c9484"),   # Mass Upload — reuse upload icon
            ("download-cloud","#9c9484"),
            ("folder",        "#9c9484"),
            ("share-2",       "#9c9484"),
            ("settings",      "#9c9484"),
        ]
        for i, (icon_name, color) in enumerate(_tab_icons):
            self.tabs.setTabIcon(i, lucide_icon(icon_name, color, 14))
        self.tabs.setIconSize(QSize(14, 14))
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
        dest_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        dest_lbl = QLabel("Folder")
        dest_lbl.setObjectName("field_label")
        self.upload_path_edit.setPlaceholderText("/")
        browse_dest_btn = QPushButton("Browse…")
        browse_dest_btn.setObjectName("browse_btn")
        browse_dest_btn.setFixedSize(80, 34)
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
        self.transferred_label = QLabel("")
        self.transferred_label.setStyleSheet("color: #9ca3af; font-size: 11px; background:transparent;")
        speed_row.addWidget(self.transferred_label)
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
        self.upload_btn = QPushButton("  Upload file")
        self.upload_btn.setObjectName("upload_btn")
        self.upload_btn.setIcon(lucide_icon("upload", "#111010", 15))
        self.upload_btn.setIconSize(QSize(15, 15))
        self.upload_btn.setMinimumHeight(42)
        self.upload_btn.clicked.connect(self._start_upload)
        main.addWidget(self.upload_btn)

        self.cancel_btn = QPushButton("  Cancel")
        self.cancel_btn.setObjectName("browse_btn")
        self.cancel_btn.setIcon(lucide_icon("x", "#c8a96e", 13))
        self.cancel_btn.setIconSize(QSize(13, 13))
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
        self.transferred_label.setText("")
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

        # Pre-compute the grand total bytes for all files so _on_bytes_progress
        # can always show "<done> / <grand_total>" rather than per-file sizes.
        self._upload_grand_total = sum(
            os.path.getsize(lp) for lp, _ in file_pairs
            if os.path.isfile(lp)
        )

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
        if hasattr(self.worker, "bytes_progress"):
            self.worker.bytes_progress.connect(self._on_bytes_progress)
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
                if hasattr(self.worker, "bytes_progress"):
                    try: self.worker.bytes_progress.disconnect()
                    except RuntimeError: pass
            except RuntimeError:
                pass  # already disconnected
        self._set_uploading(False)
        self._badge("Cancelled", "#9ca3af")
        self.progress_bar.setValue(0)
        self.pct_label.setText("0%")
        self.speed_label.setText("")
        self.transferred_label.setText("")
        self.share_result.hide()
        self._log("Upload cancelled by user.")

    def _set_uploading(self, active):
        self.upload_btn.setVisible(not active)
        self.cancel_btn.setVisible(active)
        self.upload_btn.setEnabled(not active)

    def _on_progress(self, pct):
        self.progress_bar.setValue(pct)
        self.pct_label.setText(f"{pct}%")

    def _on_bytes_progress(self, done_bytes, total_bytes):
        # done_bytes and total_bytes are already cumulative values emitted by
        # the worker's run() method (it injects an offset-aware callback per file).
        # Use _upload_grand_total as the authoritative denominator so the label
        # is always consistent even before the first signal fires.
        grand = getattr(self, "_upload_grand_total", 0) or total_bytes
        def _fmt(n):
            if n < 1024:       return f"{n} B"
            elif n < 1024**2:  return f"{n/1024:.1f} KB"
            elif n < 1024**3:  return f"{n/1024**2:.1f} MB"
            return f"{n/1024**3:.2f} GB"
        self.transferred_label.setText(f"{_fmt(done_bytes)} / {_fmt(grand)}")

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
        self.transferred_label.setText("")
        self._log(f"✓ Done! File ID: {result['file_id']}")
        if result.get("share_url"):
            url = result["share_url"]
            self.share_result.setText(f'<a href="{url}" style="color:#c8a96e;">{url}</a>')
            self.share_result.show()

    def _on_error(self, msg):
        self._set_uploading(False)
        self._badge("Error", "#f87171")
        self.transferred_label.setText("")
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
        # Use solid border/bg derived from the status color — no 8-digit RGBA
        bg_map = {
            "#c8a96e": "#2a2215",
            "#4ade80": "#0f2318",
            "#f87171": "#2a0f0f",
            "#9ca3af": "#1e1c19",
        }
        bd_map = {
            "#c8a96e": "#4a3b1e",
            "#4ade80": "#1e4a30",
            "#f87171": "#4a1e1e",
            "#9ca3af": "#2e2b27",
        }
        bg = bg_map.get(color, "#1e1c19")
        bd = bd_map.get(color, "#2e2b27")
        self.status_badge.setStyleSheet(
            f"background-color: {bg}; border: 1px solid {bd}; "
            f"border-radius: 10px; color: {color}; font-size: 11px; "
            f"font-weight: 600; padding: 2px 10px;"
        )

    def _on_tab_changed(self, index):
        # Tab order: 0=Upload, 1=Mass Upload, 2=Remote, 3=Files, 4=Shares, 5=Settings
        self.remote_tab.set_active(index == 2)
        if index == 2:
            return
        if index == 3:
            if self.api_key_edit.text().strip():
                self.files_tab._refresh()
        elif index == 4:
            if self.api_key_edit.text().strip():
                self.shares_tab.refresh()
        elif index != 5:
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