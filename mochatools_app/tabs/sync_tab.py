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
        self._pending_queue: list[tuple] = []  # (pair_id, abs_path, rel_path, remote_dest)

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

        from ..theme import get_accent, notifier, accent_qcolor
        self.add_btn    = self._tb("  Add Folder",   "folder",     get_accent(), self._add_pair)
        self.refresh_btn = self._tb("  Refresh",    "refresh-cw", get_accent(), self._refresh_action)
        self.pause_btn  = self._tb("  Pause All",    "pause",       get_accent(), self._toggle_pause_all)
        self.remove_btn = self._tb("  Remove",       "trash-2",    "#f87171", self._remove_selected, danger=True)

        self.remove_btn.setEnabled(False)

        for btn in (self.add_btn, self.pause_btn, self.remove_btn):
            tb.addWidget(btn)
        tb.addStretch()

        from ..theme import get_font
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet(f"color:{accent_qcolor().name()}; font-size:{int(get_font()[1])}px; background:transparent;")
        tb.addWidget(self.status_lbl)
        parent_lay.addLayout(tb)
        try:
            notifier().accent_changed.connect(lambda _old, _new: self._on_accent_changed(_old, _new))
        except Exception:
            pass

    def _on_accent_changed(self, old, new):
        try:
            from ..theme import get_accent, accent_qcolor
            self.add_btn.setIcon(lucide_icon('folder', get_accent(), 13))
            self.refresh_btn.setIcon(lucide_icon('refresh-cw', get_accent(), 13))
            self.pause_btn.setIcon(lucide_icon('pause', get_accent(), 13))
            from ..theme import get_font
            self.status_lbl.setStyleSheet(f"color:{accent_qcolor().name()}; font-size:{int(get_font()[1])}px; background:transparent;")
        except Exception:
            pass

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
        # Create a subfolder on the remote using the local folder's base name so
        # the watched folder contents live under <remote>/<local_basename>/...
        local_name = os.path.basename(local.rstrip("/\\")) or local
        if remote.rstrip("/") == "":
            remote = f"/{local_name}"
        elif remote == "/":
            remote = f"/{local_name}"
        else:
            remote = remote.rstrip("/") + f"/{local_name}"

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
        from ..theme import get_accent
        root_item.setIcon(0, lucide_icon("folder", get_accent(), 14))
        root_item.setForeground(0, QColor("#f0ece6"))
        from ..theme import accent_qcolor
        root_item.setForeground(1, accent_qcolor())
        root_item.setForeground(2, accent_qcolor())
        root_item.setExpanded(True)
        self.tree.addTopLevelItem(root_item)

        self._pairs[pair_id] = {
            "local":       local,
            "remote":      remote,
            "status":      _ST_PAUSED if paused else _ST_IDLE,
            "manifest":    manifest,   # {rel_path: mtime_float}
            "worker":      [],
            "scan_worker": None,
            "tree_item":   root_item,
            "file_items":  {},   # rel_path → QTreeWidgetItem
            "folder_items": {},  # rel_folder_path → QTreeWidgetItem
            "paused":      paused,
            "error_msg":   "",
            "pending_iter": iter([]),
        }
        self._refresh_pair_badge(pair_id)
        # Populate initial folder/file tree from disk so the user sees a
        # navigable nested view immediately instead of a flat list.
        try:
            self._populate_initial_tree(pair_id)
        except Exception:
            pass

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
        # worker may be a list of workers
        workers = w if isinstance(w, list) else ([w] if w is not None else [])
        for _w in list(workers):
            if not _w:
                continue
            try:
                # signal-disconnect to avoid callbacks after cancel
                for sig_name in ("progress", "speed", "status", "finished", "error"):
                    try:
                        getattr(_w, sig_name).disconnect()
                    except Exception:
                        pass
                if hasattr(_w, "bytes_progress"):
                    try:
                        _w.bytes_progress.disconnect()
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                if hasattr(_w, "cancel"):
                    _w.cancel()
            except Exception:
                pass
            try:
                if _w in self._workers:
                    self._workers.remove(_w)
            except Exception:
                pass
        pair["worker"] = []
        sw = pair.get("scan_worker")
        if sw and not sw.isFinished():
            sw.terminate()
        # drop any pending files
        pair["pending_iter"] = iter([])
        # Also remove pending items from global pending queue for this pair
        self._pending_queue = [it for it in self._pending_queue if it[0] != pair_id]
        # Also cancel any running workers that may not have been in pair["worker"]
        for _w in list(self._workers):
            try:
                if getattr(_w, "_sync_pair_id", None) == pair_id:
                    try:
                        for sig_name in ("progress", "speed", "status", "finished", "error"):
                            try: getattr(_w, sig_name).disconnect()
                            except Exception: pass
                        if hasattr(_w, "bytes_progress"):
                            try: _w.bytes_progress.disconnect()
                            except Exception: pass
                    except Exception:
                        pass
                    try:
                        if hasattr(_w, "cancel"):
                            _w.cancel()
                    except Exception:
                        pass
                    try:
                        if _w in self._workers:
                            self._workers.remove(_w)
                    except Exception:
                        pass
            except Exception:
                pass

    # ── Pause / resume ────────────────────────────────────────────────────────

    def _toggle_pause_all(self):
        any_active = any(
            not p["paused"] for p in self._pairs.values()
        )
        for pair_id, pair in self._pairs.items():
            pair["paused"] = any_active
            if any_active:
                pair["status"] = _ST_PAUSED
                # stop any active uploads for this pair
                self._stop_pair(pair_id)
            else:
                pair["status"] = _ST_IDLE
                # resume by triggering a scan
                self._scan_pair(pair_id)
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
            # stop active uploads immediately
            self._stop_pair(pair_id)
        else:
            pair["status"] = _ST_IDLE
            # resume scanning/upload
            self._scan_pair(pair_id)
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

        # Respect the concurrent-files limit across all active pairs (count files)
        active_files = sum(len(p.get("worker") or []) for p in self._pairs.values())
        if active_files >= conc:
            # Already at the limit — leave the pair in SCANNING state so the
            # next scan cycle will retry once a slot opens up.
            pair["status"] = _ST_SCANNING
            self._refresh_pair_badge(pair_id)
            return

        remote_root = pair["remote"].rstrip("/")

        # Enqueue per-file uploads into a global pending queue
        pair["status"] = _ST_UPLOADING
        self._refresh_pair_badge(pair_id)

        # Ensure file child rows exist / reset them and enqueue
        for abs_path, rel_path in changed:
            self._ensure_file_item(pair_id, rel_path, "Queued")
            remote_dest = remote_root + "/" + rel_path
            self._pending_queue.append((pair_id, abs_path, rel_path, remote_dest))

        # Try to schedule uploads up to the global concurrency
        self._schedule_uploads()

    def _on_upload_status(self, pair_id: str, rel_path: str, msg: str):
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        # Update the specific file's status
        pair["_active_rel"] = rel_path
        if "[DEBUG]" not in msg:
            self._set_file_status(pair_id, rel_path, "Uploading…")

    def _on_upload_speed(self, pair_id: str, bps: float, rel_path: str | None = None):
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        pair["_speed_bps"] = bps
        rel = rel_path or pair.get("_active_rel")
        if rel:
            if bps < 1024:        speed_str = f"{bps:.3f} B/s"
            elif bps < 1024**2:   speed_str = f"{bps/1024:.3f} KB/s"
            else:                 speed_str = f"{bps/1024**2:.3f} MB/s"
            # Use per-file bytes (stored under file-specific state) if present
            file_state = pair.get("file_state", {}).get(rel, {})
            done  = file_state.get("done", pair.get("_bytes_done", 0))
            total = file_state.get("total", pair.get("_bytes_total", 0))
            size_str = (f"{UploadWorker._fmt_size(done)} / "
                        f"{UploadWorker._fmt_size(total)}") if total else ""
            self._set_file_detail(pair_id, rel, speed_str, size_str)
        self._refresh_pair_badge(pair_id)

    def _on_upload_bytes(self, pair_id: str, done: int, total: int, rel_path: str | None = None):
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        # Associate current bytes to the provided rel_path or the active file
        rel = rel_path or pair.get("_active_rel")
        if rel:
            if "file_state" not in pair:
                pair["file_state"] = {}
            pair["file_state"][rel] = {"done": int(done), "total": int(total)}
            # Also update quick-access counters (last seen)
            pair["_bytes_done"] = int(done)
            pair["_bytes_total"] = int(total)
            # Refresh per-file detail display
            if pair.get("file_items") and rel in pair.get("file_items", {}):
                # update the displayed detail right away
                if int(total) > 0:
                    size_str = (f"{UploadWorker._fmt_size(int(done))} / {UploadWorker._fmt_size(int(total))}")
                else:
                    size_str = ""
                # format speed using last known _speed_bps
                bps = pair.get("_speed_bps", 0.0)
                if bps < 1024:        speed_str = f"{bps:.3f} B/s"
                elif bps < 1024**2:   speed_str = f"{bps/1024:.3f} KB/s"
                else:                 speed_str = f"{bps/1024**2:.3f} MB/s"
                self._set_file_detail(pair_id, rel, speed_str, size_str)
        else:
            pair["_bytes_done"]  = int(done)
            pair["_bytes_total"] = int(total)

    def _on_upload_done(self, pair_id: str, changed: list, result: dict):
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        # If result is a batch result, update those paths; otherwise treat
        # as single-file upload finished for the currently active file.
        # Support both code paths from UploadWorker.
        uploaded = []
        if isinstance(result, dict) and "uploaded_files" in result:
            uploaded = result["uploaded_files"]
        else:
            # Fallback: assume 'changed' describes the completed files
            uploaded = [rel for _abs, rel in changed]

        for rel_path in uploaded:
            try:
                abs_path = os.path.join(pair["local"], rel_path)
                mtime = os.path.getmtime(abs_path)
            except Exception:
                mtime = time.time()
            pair["manifest"][rel_path] = mtime
            self._set_file_status(pair_id, rel_path, "Synced ✓")
            self._set_file_detail(pair_id, rel_path, "", "")
            # clear file_state for completed file
            if pair.get("file_state") and rel_path in pair["file_state"]:
                pair["file_state"].pop(rel_path, None)

        # Try to schedule more uploads
        self._schedule_uploads()

        # If nothing left for this pair mark idle
        still_pending = any(p_id == pair_id for (p_id, *_rest) in self._pending_queue)
        active_count = len(pair.get("worker") or [])
        if not still_pending and active_count == 0:
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

    def _launch_next_file(self, pair_id: str, chunk_mb: int, max_chunks: int):
        """Start the next file upload for the pair, if any pending."""
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        # Pop from global pending queue the next item for this pair
        next_item = None
        for idx, item in enumerate(list(self._pending_queue)):
            p_id, abs_path, rel_path, remote_dest = item
            if p_id == pair_id:
                next_item = self._pending_queue.pop(idx)
                break
        if not next_item:
            return
        _, abs_path, rel_path, remote_dest = next_item
        api_key = self.get_api_key()
        if not api_key:
            pair["status"] = _ST_ERROR
            pair["error_msg"] = "Missing API key"
            self._refresh_pair_badge(pair_id)
            return

        # Create a single-file UploadWorker
        w = UploadWorker(
            api_key        = api_key,
            base_url       = self.base_url,
            file_pairs     = [(abs_path, remote_dest)],
            create_share   = False,
            share_expiry   = None,
            share_max_downloads = None,
            chunk_size_mb  = chunk_mb,
            max_chunks     = max_chunks,
        )

        # Connect signals to update this file's UI (pass rel_path so handlers
        # update the correct child row when multiple concurrent uploads run)
        w.status.connect(lambda msg, pid=pair_id, rel=rel_path:
                         self._on_upload_status(pid, rel, msg))
        w.speed.connect(lambda bps, pid=pair_id, rel=rel_path:
                        self._on_upload_speed(pid, bps, rel))
        w.bytes_progress.connect(lambda done, total, pid=pair_id, rel=rel_path:
                                  self._on_upload_bytes(pid, done, total, rel))
        def _on_finished_and_cleanup(result, pid=pair_id, ch=[(abs_path, rel_path)], worker_ref=w):
            # remove from per-pair worker list and global list then call done
            p = self._pairs.get(pid)
            if p and isinstance(p.get("worker"), list) and worker_ref in p["worker"]:
                try:
                    p["worker"].remove(worker_ref)
                except ValueError:
                    pass
            try:
                if worker_ref in self._workers:
                    self._workers.remove(worker_ref)
            except Exception:
                pass
            self._on_upload_done(pid, ch, result)

        w.finished.connect(_on_finished_and_cleanup)
        w.error.connect(lambda msg, pid=pair_id:
                        self._on_upload_error(pid, msg))
        w.status.connect(self._log)
        w.error.connect(lambda msg: self._log(f"✗ {msg}"))
        # finished cleanup removed from here; _on_finished_and_cleanup handles removals

        # Track worker in per-pair list
        if not isinstance(pair.get("worker"), list):
            pair["worker"] = []
        pair["worker"].append(w)
        # tag worker with pair/rel so stop/remove can find it reliably
        try:
            setattr(w, "_sync_pair_id", pair_id)
            setattr(w, "_sync_rel_path", rel_path)
        except Exception:
            pass
        pair["_active_rel"] = rel_path
        pair["status"] = _ST_UPLOADING
        self._refresh_pair_badge(pair_id)
        self._workers.append(w)
        w.start()

    def _schedule_uploads(self):
        """Schedule pending uploads from the global pending queue up to concurrency."""
        conc, chunk_mb, max_chunks = self.get_sync_settings()
        # Count only active upload workers (scan workers are also tracked in
        # self._workers so len(self._workers) is not a reliable measure).
        active_uploads = sum(1 for w in self._workers if getattr(w, "_sync_pair_id", None) is not None)
        # Launch until we hit the concurrency limit
        while self._pending_queue and active_uploads < conc:
            # Peek at first pending entry and attempt to launch for that pair
            p_id = self._pending_queue[0][0]
            pair = self._pairs.get(p_id)
            if not pair or pair.get("paused"):
                # drop or skip paused/removed pairs
                # remove all pending items for that pair
                self._pending_queue = [it for it in self._pending_queue if it[0] != p_id]
                continue
            self._launch_next_file(p_id, chunk_mb, max_chunks)
            active_uploads += 1

    # ── Tree helpers ──────────────────────────────────────────────────────────

    def _ensure_file_item(self, pair_id: str, rel_path: str, status_text: str):
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        # Build/reuse intermediate folder nodes so file items appear in a
        # nested tree instead of being direct children of the pair root.
        root_item = pair["tree_item"]
        if rel_path not in pair["file_items"]:
            # Determine parent folder and ensure folder nodes exist
            parent_rel = os.path.dirname(rel_path).replace("\\", "/")
            if parent_rel:
                parent_item = self._ensure_folder_item(pair_id, parent_rel)
            else:
                parent_item = root_item

            child = QTreeWidgetItem()
            child.setText(0, f"   {os.path.basename(rel_path)}")
            child.setText(1, status_text)
            child.setText(2, "")
            child.setForeground(0, QColor("#9c9484"))
            from ..theme import accent_qcolor
            child.setForeground(1, accent_qcolor())
            child.setForeground(2, QColor("#9c9484"))
            parent_item.addChild(child)
            pair["file_items"][rel_path] = child
        else:
            pair["file_items"][rel_path].setText(1, status_text)
            from ..theme import accent_qcolor
            pair["file_items"][rel_path].setForeground(1, accent_qcolor())


    def _ensure_folder_item(self, pair_id: str, folder_rel: str) -> QTreeWidgetItem:
        """Ensure a QTreeWidgetItem exists for the given folder relative
        path under the pair root. Returns the folder item (creates parents
        recursively as needed) and caches it in pair['folder_items']."""
        pair = self._pairs.get(pair_id)
        if not pair:
            return None
        # normalize
        folder_rel = folder_rel.replace("\\", "/").strip("/")
        if folder_rel in pair.get("folder_items", {}):
            return pair["folder_items"][folder_rel]

        parent_rel = os.path.dirname(folder_rel).replace("\\", "/").strip("/")
        if parent_rel:
            parent_item = self._ensure_folder_item(pair_id, parent_rel)
        else:
            parent_item = pair["tree_item"]

        # create folder item
        folder_item = QTreeWidgetItem()
        folder_item.setText(0, f"   {os.path.basename(folder_rel)}")
        from ..theme import get_accent
        folder_item.setIcon(0, lucide_icon("folder", get_accent(), 12))
        folder_item.setForeground(0, QColor("#f0ece6"))
        folder_item.setForeground(1, QColor("#9c9484"))
        folder_item.setForeground(2, QColor("#9c9484"))
        folder_item.setExpanded(False)
        parent_item.addChild(folder_item)
        pair.setdefault("folder_items", {})[folder_rel] = folder_item
        return folder_item


    def _populate_initial_tree(self, pair_id: str):
        """Walk the local folder on disk and populate folder & file nodes so
        the UI shows a nested tree immediately."""
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        local_root = pair.get("local")
        if not local_root or not os.path.isdir(local_root):
            return
        # Walk and create folder nodes first, then file items
        for dirpath, dirs, files in os.walk(local_root):
            rel_dir = os.path.relpath(dirpath, local_root).replace("\\", "/")
            if rel_dir == ".":
                rel_dir = ""
            # create folder node (skip root)
            if rel_dir:
                try:
                    self._ensure_folder_item(pair_id, rel_dir)
                except Exception:
                    pass
            # create file nodes
            for fname in files:
                abs_path = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(abs_path, local_root).replace("\\", "/")
                # mark synced if present in manifest, otherwise blank
                status = "Synced ✓" if rel_path in (pair.get("manifest") or {}) else ""
                try:
                    self._ensure_file_item(pair_id, rel_path, status)
                except Exception:
                    pass

    def _set_file_status(self, pair_id: str, rel_path: str, status: str):
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        item = pair["file_items"].get(rel_path)
        if item:
            item.setText(1, status)
            from ..theme import get_accent
            color = "#4ade80" if "✓" in status else get_accent()
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

        from ..theme import get_accent
        badge_map = {
            _ST_IDLE:      ("● Idle",      "#5a5650"),
            _ST_SCANNING:  ("◌ Scanning",  "#9c9484"),
            _ST_UPLOADING: ("↑ Uploading", get_accent()),
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
                from ..theme import accent_qcolor
                root.setForeground(2, accent_qcolor())
            else:
                root.setText(2, "")
        elif state == _ST_ERROR:
            root.setText(2, pair.get("error_msg", "")[:40])
            root.setForeground(2, QColor("#f87171"))
        else:
            # When idle, show "Up to date" if we have a manifest / synced files
            if state == _ST_IDLE:
                has_synced = bool(pair.get("manifest") or pair.get("file_items"))
                if has_synced:
                    root.setText(2, "Up to date")
                    root.setForeground(2, QColor("#4ade80"))
                else:
                    root.setText(2, "")
            else:
                root.setText(2, "")

    # ── Selection / context menu ──────────────────────────────────────────────

    def _on_selection_changed(self):
        items = self.tree.selectedItems()
        has   = bool(items)
        self.remove_btn.setEnabled(has)
        # Enable refresh btn when selection exists, otherwise allow global refresh
        self.refresh_btn.setEnabled(True)

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
        menu.setStyleSheet(
            "QMenu { background:#1f1f1f; border:1px solid #3a3a3a; border-radius:8px; color:#f0f0f0; font-size:12px; }"
            "QMenu::item { padding:6px 8px; }"
            "QMenu::item:selected { background:#332b1a; }"
        )

        from ..theme import get_accent
        if pair["paused"]:
            a = menu.addAction(lucide_icon("play", get_accent(), 12), "Resume")
            a.triggered.connect(lambda: self._toggle_pause_pair(pair_id))
        else:
            a = menu.addAction(lucide_icon("pause", get_accent(), 12), "Pause")
            a.triggered.connect(lambda: self._toggle_pause_pair(pair_id))

        s1 = menu.addAction(lucide_icon("refresh-cw", get_accent(), 12), "Sync now")
        s1.triggered.connect(lambda: self._scan_pair(pair_id))

        s2 = menu.addAction(lucide_icon("refresh-cw", get_accent(), 12), "Refresh")
        s2.triggered.connect(lambda: self._refresh_action(pair_id))
        menu.addSeparator()
        menu.addAction(lucide_icon("trash-2", "#f87171", 12), "Remove").triggered.connect(self._remove_selected)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _refresh_action(self, pair_id: str | None = None):
        """Refresh either all pairs (if pair_id is None) or the selected/supplied pair(s)."""
        if pair_id:
            # refresh single
            self._scan_pair(pair_id)
            return

        # If any selection, refresh those; otherwise refresh all
        items = self.tree.selectedItems()
        if items:
            seen = set()
            for it in items:
                pid = it.data(0, Qt.ItemDataRole.UserRole)
                if pid is None and it.parent():
                    pid = it.parent().data(0, Qt.ItemDataRole.UserRole)
                if pid and pid not in seen:
                    seen.add(pid)
                    self._scan_pair(pid)
            return

        # no selection — refresh all
        for pid in list(self._pairs.keys()):
            self._scan_pair(pid)

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
            # Populate child file rows from the saved manifest so users can
            # expand a pair and see previously uploaded files as "Synced ✓".
            try:
                pair = self._pairs.get(pair_id)
                manifest = p.get("manifest", {}) or {}
                for rel_path in sorted(manifest.keys()):
                    # ensure child exists and mark as synced
                    self._ensure_file_item(pair_id, rel_path, "Synced ✓")
                    self._set_file_detail(pair_id, rel_path, "", "")
            except Exception:
                pass
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