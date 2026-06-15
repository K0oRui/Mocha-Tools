"""
tabs/sync_tab.py — Folder sync tab for MochaTools.

Lets the user map local folders to remote destinations and keeps them
in sync automatically.  Every SCAN_INTERVAL seconds the watcher
compares local mtimes against a manifest of what has been uploaded and
queues changed files through the existing UploadWorker.

UI hierarchy
────────────
  SyncTab (QWidget)
    toolbar (QPushButton × 3)
    QTreeWidget
      ▶ Folder pair item  (local ↔ remote, status badge)
          └─ File child items (filename | status | speed/size)

State machine per folder pair
──────────────────────────────
  IDLE      → watcher sees changes → SCANNING
  SCANNING  → diff computed       → UPLOADING (or back to IDLE if nothing new)
  UPLOADING → all files done      → IDLE
  PAUSED    → user toggles        → IDLE
  ERROR     → user clears         → IDLE

Persistence
───────────
  Pairs are stored in QSettings under sync_pairs as a JSON list.
  The uploaded-file manifest is also persisted so restarts don't
  re-upload unchanged files.
"""

from __future__ import annotations

import json
import os
import time
from typing import Callable

from PyQt6.QtCore import Qt, QSize, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QFileDialog, QHBoxLayout, QLabel,
    QMenu, QMessageBox, QPushButton, QSizePolicy, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..constants import (
    APP_NAME, DEFAULT_CHUNK_SIZE_MB, DEFAULT_MAX_CHUNKS,
    HARDCODED_BASE_URL, ORG_NAME,
)
from ..ui.icons import lucide_icon
from ..workers import UploadWorker

# Seconds between filesystem scans per pair
SCAN_INTERVAL = 5

# Status constants
_ST_IDLE      = "idle"
_ST_SCANNING  = "scanning"
_ST_UPLOADING = "uploading"
_ST_PAUSED    = "paused"
_ST_ERROR     = "error"


# ── Scan Worker ───────────────────────────────────────────────────────────────

class _ScanWorker(QThread):
    """
    Walks a local folder and emits the list of files whose mtime is newer
    than the manifest entry (or are absent from the manifest entirely).
    Runs off the main thread so large trees don't block the UI.
    """
    found = pyqtSignal(str, list)   # (pair_id, [(local_path, rel_path), ...])

    def __init__(self, pair_id: str, local_root: str, manifest: dict):
        super().__init__()
        self.pair_id    = pair_id
        self.local_root = local_root
        self.manifest   = manifest   # {rel_path: mtime_float}

    def run(self):
        changed: list[tuple[str, str]] = []
        try:
            for dirpath, _dirs, files in os.walk(self.local_root):
                for fname in files:
                    abs_path = os.path.join(dirpath, fname)
                    rel_path = os.path.relpath(abs_path, self.local_root).replace("\\", "/")
                    try:
                        mtime = os.path.getmtime(abs_path)
                    except OSError:
                        continue
                    known_mtime = self.manifest.get(rel_path)
                    if known_mtime is None or mtime > known_mtime + 0.5:
                        changed.append((abs_path, rel_path))
        except Exception:
            pass
        self.found.emit(self.pair_id, changed)


# ── SyncTab ───────────────────────────────────────────────────────────────────

class SyncTab(QWidget):
    """
    Folder sync tab.  Presents a list of watched folder pairs and shows
    per-file upload status beneath each pair.
    """

    def __init__(
        self,
        get_api_key: Callable[[], str],
        get_sync_settings: Callable[[], tuple[int, int, int]],  # (conc, chunk_mb, max_chunks)
        get_debug: Callable[[], bool] = lambda: False,
        parent=None,
    ):
        super().__init__(parent)
        self.get_api_key       = get_api_key
        self.get_sync_settings = get_sync_settings
        self.get_debug         = get_debug
        self.base_url          = HARDCODED_BASE_URL

        # pair_id → {local, remote, status, manifest, worker, scan_worker,
        #             tree_item, file_items, paused, error_msg}
        self._pairs: dict[str, dict] = {}
        self._workers:   list[QThread] = []

        self._scan_timer = QTimer(self)
        self._scan_timer.setInterval(SCAN_INTERVAL * 1000)
        self._scan_timer.timeout.connect(self._scan_all)

        self._build_ui()
        self._load_pairs()
        self._scan_timer.start()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        self._build_toolbar(outer)
        self._build_tree(outer)
        self._build_status_bar(outer)

    def _build_toolbar(self, parent_lay: QVBoxLayout):
        tb = QHBoxLayout()
        tb.setSpacing(4)

        self.add_btn    = self._tb("  Add Folder",   "folder",     "#9c9484", self._add_pair)
        self.pause_btn  = self._tb("  Pause All",    "link",       "#9c9484", self._toggle_pause_all)
        self.remove_btn = self._tb("  Remove",       "trash-2",    "#f87171", self._remove_selected, danger=True)

        self.remove_btn.setEnabled(False)

        for btn in (self.add_btn, self.pause_btn, self.remove_btn):
            tb.addWidget(btn)
        tb.addStretch()

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet(
            "color:#9ca3af; font-size:11px; background:transparent;"
        )
        tb.addWidget(self.status_lbl)
        parent_lay.addLayout(tb)

    def _build_tree(self, parent_lay: QVBoxLayout):
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Folder / File", "Status", "Speed / Size"])
        self.tree.setRootIsDecorated(True)
        self.tree.setSortingEnabled(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.setAnimated(True)

        from PyQt6.QtWidgets import QHeaderView
        hdr = self.tree.header()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(True)
        hdr.resizeSection(0, 260)
        hdr.resizeSection(1, 120)
        hdr.resizeSection(2, 120)
        parent_lay.addWidget(self.tree, 1)

    def _build_status_bar(self, parent_lay: QVBoxLayout):
        self.footer_lbl = QLabel("")
        self.footer_lbl.setObjectName("log_console")
        self.footer_lbl.setWordWrap(True)
        self.footer_lbl.hide()
        parent_lay.addWidget(self.footer_lbl)

    def _tb(self, label: str, icon_name: str, color: str, slot,
            danger: bool = False) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName("tb_btn_danger" if danger else "tb_btn")
        btn.setIcon(lucide_icon(icon_name, color, 13))
        btn.setIconSize(QSize(13, 13))
        btn.clicked.connect(slot)
        return btn

    # ── Pair management ───────────────────────────────────────────────────────

    def _add_pair(self):
        api_key = self.get_api_key()
        if not api_key:
            QMessageBox.warning(self, "API key required",
                                "Enter your API key in Settings before adding sync folders.")
            return

        # 1. Pick local folder
        local = QFileDialog.getExistingDirectory(self, "Select local folder to sync")
        if not local:
            return

        # 2. Pick remote folder via existing dialog
        from ..dialogs import FolderBrowserDialog
        dlg = FolderBrowserDialog(api_key, self.base_url, "/", parent=self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        remote = dlg.selected or "/"

        pair_id = f"{local}::{remote}"
        if pair_id in self._pairs:
            QMessageBox.information(self, "Already watching",
                                    "This local → remote combination is already in the list.")
            return

        self._register_pair(pair_id, local, remote, manifest={}, paused=False)
        self._save_pairs()
        self._set_status(f"{len(self._pairs)} pair{'s' if len(self._pairs) != 1 else ''} watched")

        # Immediate first scan
        self._scan_pair(pair_id)

    def _register_pair(self, pair_id: str, local: str, remote: str,
                       manifest: dict, paused: bool):
        """Create the tree item and state entry for a pair."""
        local_name  = os.path.basename(local.rstrip("/\\")) or local
        remote_name = remote

        root_item = QTreeWidgetItem()
        root_item.setData(0, Qt.ItemDataRole.UserRole, pair_id)
        root_item.setText(0, f"  {local_name}  →  {remote_name}")
        root_item.setIcon(0, lucide_icon("folder", "#c8a96e", 14))
        root_item.setForeground(0, QColor("#f0ece6"))
        root_item.setForeground(1, QColor("#9c9484"))
        root_item.setForeground(2, QColor("#9c9484"))
        root_item.setExpanded(True)
        self.tree.addTopLevelItem(root_item)

        self._pairs[pair_id] = {
            "local":       local,
            "remote":      remote,
            "status":      _ST_PAUSED if paused else _ST_IDLE,
            "manifest":    manifest,   # {rel_path: mtime_float}
            "worker":      None,
            "scan_worker": None,
            "tree_item":   root_item,
            "file_items":  {},   # rel_path → QTreeWidgetItem
            "paused":      paused,
            "error_msg":   "",
        }
        self._refresh_pair_badge(pair_id)

    def _remove_selected(self):
        items = self.tree.selectedItems()
        if not items:
            return
        item = items[0]
        pair_id = item.data(0, Qt.ItemDataRole.UserRole)

        # Walk up to root if a file child is selected
        if pair_id is None:
            parent = item.parent()
            if parent:
                pair_id = parent.data(0, Qt.ItemDataRole.UserRole)

        if pair_id not in self._pairs:
            return

        if QMessageBox.question(
            self, "Remove sync pair",
            "Stop watching this folder?\n"
            "(Local and remote files are not deleted.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        self._stop_pair(pair_id)
        pair = self._pairs.pop(pair_id)
        idx = self.tree.indexOfTopLevelItem(pair["tree_item"])
        if idx >= 0:
            self.tree.takeTopLevelItem(idx)
        self._save_pairs()
        self._set_status(f"{len(self._pairs)} pair{'s' if len(self._pairs) != 1 else ''} watched")

    def _stop_pair(self, pair_id: str):
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        w = pair.get("worker")
        if w and not w.isFinished():
            w.cancel()
        sw = pair.get("scan_worker")
        if sw and not sw.isFinished():
            sw.terminate()

    # ── Pause / resume ────────────────────────────────────────────────────────

    def _toggle_pause_all(self):
        any_active = any(
            not p["paused"] for p in self._pairs.values()
        )
        for pair_id, pair in self._pairs.items():
            pair["paused"] = any_active
            if any_active:
                pair["status"] = _ST_PAUSED
            else:
                pair["status"] = _ST_IDLE
            self._refresh_pair_badge(pair_id)

        self.pause_btn.setText("  Resume All" if any_active else "  Pause All")
        self._save_pairs()

    def _toggle_pause_pair(self, pair_id: str):
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        pair["paused"] = not pair["paused"]
        if pair["paused"]:
            pair["status"] = _ST_PAUSED
        else:
            pair["status"] = _ST_IDLE
        self._refresh_pair_badge(pair_id)
        self._save_pairs()

    # ── Scanning ──────────────────────────────────────────────────────────────

    def _scan_all(self):
        for pair_id, pair in self._pairs.items():
            if pair["paused"]:
                continue
            if pair["status"] in (_ST_UPLOADING, _ST_SCANNING):
                continue
            self._scan_pair(pair_id)

    def _scan_pair(self, pair_id: str):
        pair = self._pairs.get(pair_id)
        if not pair or pair["paused"]:
            return
        if pair.get("scan_worker") and not pair["scan_worker"].isFinished():
            return

        pair["status"] = _ST_SCANNING
        self._refresh_pair_badge(pair_id)

        sw = _ScanWorker(pair_id, pair["local"], pair["manifest"])
        sw.found.connect(self._on_scan_done)
        sw.finished.connect(lambda _sw=sw: self._workers.remove(_sw)
                            if _sw in self._workers else None)
        pair["scan_worker"] = sw
        self._workers.append(sw)
        sw.start()

    def _on_scan_done(self, pair_id: str, changed: list):
        pair = self._pairs.get(pair_id)
        if not pair:
            return

        if not changed:
            pair["status"] = _ST_IDLE
            self._refresh_pair_badge(pair_id)
            return

        # Start upload for the changed files
        self._start_upload(pair_id, changed)

    # ── Uploading ─────────────────────────────────────────────────────────────

    def _start_upload(self, pair_id: str, changed: list[tuple[str, str]]):
        pair    = self._pairs.get(pair_id)
        api_key = self.get_api_key()
        if not pair or not api_key:
            return

        conc, chunk_mb, max_chunks = self.get_sync_settings()

        # Respect the concurrent-files limit across all active pairs
        active_count = sum(
            1 for p in self._pairs.values() if p["status"] == _ST_UPLOADING
        )
        if active_count >= conc:
            # Already at the limit — leave the pair in SCANNING state so the
            # next scan cycle will retry once a slot opens up.
            pair["status"] = _ST_SCANNING
            self._refresh_pair_badge(pair_id)
            return

        remote_root = pair["remote"].rstrip("/")

        # Build (local_abs, remote_dest) pairs
        file_pairs = []
        for abs_path, rel_path in changed:
            remote_dest = remote_root + "/" + rel_path
            file_pairs.append((abs_path, remote_dest))

        pair["status"] = _ST_UPLOADING
        self._refresh_pair_badge(pair_id)

        # Ensure file child rows exist / reset them
        for abs_path, rel_path in changed:
            self._ensure_file_item(pair_id, rel_path, "Queued")

        w = UploadWorker(
            api_key        = api_key,
            base_url       = self.base_url,
            file_pairs     = file_pairs,
            create_share   = False,
            share_expiry   = None,
            share_max_downloads = None,
            chunk_size_mb  = chunk_mb,
            max_chunks     = max_chunks,
        )
        w.status.connect(lambda msg, pid=pair_id, fp=file_pairs:
                         self._on_upload_status(pid, fp, msg))
        w.speed.connect(lambda bps, pid=pair_id:
                        self._on_upload_speed(pid, bps))
        w.bytes_progress.connect(lambda done, total, pid=pair_id:
                                  self._on_upload_bytes(pid, done, total))
        w.finished.connect(lambda result, pid=pair_id, ch=changed:
                           self._on_upload_done(pid, ch, result))
        w.error.connect(lambda msg, pid=pair_id:
                        self._on_upload_error(pid, msg))
        w.status.connect(self._log)
        w.error.connect(lambda msg: self._log(f"✗ {msg}"))
        w.finished.connect(lambda _r, _w=w: self._workers.remove(_w)
                           if _w in self._workers else None)

        pair["worker"]       = w
        pair["_active_rel"]  = None   # tracks which rel_path is currently uploading
        pair["_speed_bps"]   = 0.0
        pair["_bytes_done"]  = 0
        pair["_bytes_total"] = 0
        self._workers.append(w)
        w.start()

    def _on_upload_status(self, pair_id: str, file_pairs: list, msg: str):
        pair = self._pairs.get(pair_id)
        if not pair:
            return

        # Try to identify which file the message belongs to by checking
        # if any known filename appears in the message
        for abs_path, rel_path in file_pairs:
            fname = os.path.basename(abs_path)
            if fname in msg:
                pair["_active_rel"] = rel_path
                if "[DEBUG]" not in msg:
                    self._set_file_status(pair_id, rel_path, "Uploading…")
                break

    def _on_upload_speed(self, pair_id: str, bps: float):
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        pair["_speed_bps"] = bps
        rel = pair.get("_active_rel")
        if rel:
            if bps < 1024:        speed_str = f"{bps:.3f} B/s"
            elif bps < 1024**2:   speed_str = f"{bps/1024:.3f} KB/s"
            else:                 speed_str = f"{bps/1024**2:.3f} MB/s"
            done  = pair.get("_bytes_done", 0)
            total = pair.get("_bytes_total", 0)
            size_str = (f"{UploadWorker._fmt_size(done)} / "
                        f"{UploadWorker._fmt_size(total)}") if total else ""
            self._set_file_detail(pair_id, rel, speed_str, size_str)
        self._refresh_pair_badge(pair_id)

    def _on_upload_bytes(self, pair_id: str, done: int, total: int):
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        pair["_bytes_done"]  = done
        pair["_bytes_total"] = total

    def _on_upload_done(self, pair_id: str, changed: list, result: dict):
        pair = self._pairs.get(pair_id)
        if not pair:
            return

        # Update manifest for all successfully uploaded files
        for abs_path, rel_path in changed:
            try:
                mtime = os.path.getmtime(abs_path)
            except OSError:
                mtime = time.time()
            pair["manifest"][rel_path] = mtime
            self._set_file_status(pair_id, rel_path, "Synced ✓")
            self._set_file_detail(pair_id, rel_path, "", "")

        pair["status"]      = _ST_IDLE
        pair["_active_rel"] = None
        self._refresh_pair_badge(pair_id)
        self._save_pairs()

    def _on_upload_error(self, pair_id: str, msg: str):
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        pair["status"]    = _ST_ERROR
        pair["error_msg"] = msg
        self._refresh_pair_badge(pair_id)

    # ── Tree helpers ──────────────────────────────────────────────────────────

    def _ensure_file_item(self, pair_id: str, rel_path: str, status_text: str):
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        root_item = pair["tree_item"]
        if rel_path not in pair["file_items"]:
            child = QTreeWidgetItem()
            child.setText(0, f"   {os.path.basename(rel_path)}")
            child.setText(1, status_text)
            child.setText(2, "")
            child.setForeground(0, QColor("#9c9484"))
            child.setForeground(1, QColor("#c8a96e"))
            child.setForeground(2, QColor("#9c9484"))
            root_item.addChild(child)
            pair["file_items"][rel_path] = child
        else:
            pair["file_items"][rel_path].setText(1, status_text)
            pair["file_items"][rel_path].setForeground(1, QColor("#c8a96e"))

    def _set_file_status(self, pair_id: str, rel_path: str, status: str):
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        item = pair["file_items"].get(rel_path)
        if item:
            item.setText(1, status)
            color = "#4ade80" if "✓" in status else "#c8a96e"
            item.setForeground(1, QColor(color))

    def _set_file_detail(self, pair_id: str, rel_path: str,
                         speed: str, size: str):
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        item = pair["file_items"].get(rel_path)
        if item:
            item.setText(2, f"{speed}  {size}".strip())

    def _refresh_pair_badge(self, pair_id: str):
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        root  = pair["tree_item"]
        state = pair["status"]

        badge_map = {
            _ST_IDLE:      ("● Idle",      "#5a5650"),
            _ST_SCANNING:  ("◌ Scanning",  "#9c9484"),
            _ST_UPLOADING: ("↑ Uploading", "#c8a96e"),
            _ST_PAUSED:    ("‖ Paused",    "#5a5650"),
            _ST_ERROR:     ("✕ Error",     "#f87171"),
        }
        text, color = badge_map.get(state, ("", "#5a5650"))
        root.setText(1, text)
        root.setForeground(1, QColor(color))

        # Show speed on root when uploading
        if state == _ST_UPLOADING:
            bps = pair.get("_speed_bps", 0.0)
            if bps > 0:
                if bps < 1024:        speed_str = f"{bps:.3f} B/s"
                elif bps < 1024**2:   speed_str = f"{bps/1024:.3f} KB/s"
                else:                 speed_str = f"{bps/1024**2:.3f} MB/s"
                root.setText(2, speed_str)
                root.setForeground(2, QColor("#c8a96e"))
            else:
                root.setText(2, "")
        elif state == _ST_ERROR:
            root.setText(2, pair.get("error_msg", "")[:40])
            root.setForeground(2, QColor("#f87171"))
        else:
            root.setText(2, "")

    # ── Selection / context menu ──────────────────────────────────────────────

    def _on_selection_changed(self):
        items = self.tree.selectedItems()
        has   = bool(items)
        self.remove_btn.setEnabled(has)

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return

        # Walk up to root pair item
        pair_id = item.data(0, Qt.ItemDataRole.UserRole)
        if pair_id is None:
            parent = item.parent()
            if parent:
                pair_id = parent.data(0, Qt.ItemDataRole.UserRole)
        if pair_id not in self._pairs:
            return

        pair = self._pairs[pair_id]
        menu = QMenu(self)

        if pair["paused"]:
            menu.addAction("▶  Resume",      lambda: self._toggle_pause_pair(pair_id))
        else:
            menu.addAction("‖  Pause",       lambda: self._toggle_pause_pair(pair_id))

        menu.addAction("↺  Sync now",        lambda: self._scan_pair(pair_id))
        menu.addSeparator()
        menu.addAction("✕  Remove",          self._remove_selected)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load_pairs(self):
        from PyQt6.QtCore import QSettings
        s   = QSettings(ORG_NAME, APP_NAME)
        raw = s.value("sync_pairs", None)
        if not raw:
            return
        try:
            pairs = json.loads(raw)
        except Exception:
            return
        for p in pairs:
            pair_id = f"{p['local']}::{p['remote']}"
            if pair_id in self._pairs:
                continue
            if not os.path.isdir(p.get("local", "")):
                continue   # local folder gone — skip silently
            self._register_pair(
                pair_id  = pair_id,
                local    = p["local"],
                remote   = p["remote"],
                manifest = p.get("manifest", {}),
                paused   = p.get("paused", False),
            )
        self._set_status(
            f"{len(self._pairs)} pair{'s' if len(self._pairs) != 1 else ''} watched"
            if self._pairs else "No folders watched"
        )

    def _save_pairs(self):
        from PyQt6.QtCore import QSettings
        s    = QSettings(ORG_NAME, APP_NAME)
        data = []
        for pair_id, pair in self._pairs.items():
            data.append({
                "local":    pair["local"],
                "remote":   pair["remote"],
                "manifest": pair["manifest"],
                "paused":   pair["paused"],
            })
        try:
            s.setValue("sync_pairs", json.dumps(data))
        except Exception:
            pass

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_status(self, msg: str):
        self.status_lbl.setText(msg)

    def _log(self, msg: str):
        if msg.startswith("[DEBUG]") and not self.get_debug():
            return
        self.footer_lbl.setText(msg)
        self.footer_lbl.show()

    def closeEvent(self, event):
        self._scan_timer.stop()
        for pair_id in list(self._pairs):
            self._stop_pair(pair_id)
        super().closeEvent(event)