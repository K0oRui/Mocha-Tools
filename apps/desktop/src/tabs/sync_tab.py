"""tabs/sync_tab.py — Folder sync tab for MochaTools.

Lets the user map local folders to remote destinations and keeps them
in sync automatically.  Every SCAN_INTERVAL seconds the watcher
compares local mtimes against a manifest of what has been uploaded and
queues changed files through the existing UploadWorker.

UI hierarchy
────────────
  SyncTab (QWidget)
    toolbar (QPushButton x 3)
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

import contextlib
import json
import os
import pathlib
import time
from functools import partial
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..constants import (
    APP_NAME,
    ORG_NAME,
)
from ..logging_utils import write_debug_log
from ..ui.icons import lucide_icon
from ..upload_pipeline import PRIORITY_SYNC, UploadJob, UploadManager
from ..utils import decay_speed
from ..workers import UploadWorker

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..mocha_client import MochaClient
    from ..providers import SyncSettingsProvider

# Seconds between filesystem scans per pair
SCAN_INTERVAL = 5

_KB = 1024

# Status constants
_ST_IDLE = "idle"
_ST_SCANNING = "scanning"
_ST_UPLOADING = "uploading"
_ST_PAUSED = "paused"
_ST_ERROR = "error"


# ── Scan Worker ───────────────────────────────────────────────────────────────


class _ScanWorker(QThread):
    """Walks a local folder and emits the list of files whose mtime is newer
    than the manifest entry (or are absent from the manifest entirely).
    Runs off the main thread so large trees don't block the UI.
    """

    found = Signal(str, list)  # (pair_id, [(local_path, rel_path), ...])

    def __init__(self, pair_id: str, local_root: str, manifest: dict) -> None:
        super().__init__()
        self.pair_id = pair_id
        self.local_root = local_root
        self.manifest = manifest  # {rel_path: mtime_float}

    def run(self) -> None:
        changed: list[tuple[str, str]] = []
        try:
            for dirpath, _dirs, files in os.walk(self.local_root):
                for fname in files:
                    abs_path = str(pathlib.Path(dirpath) / fname)
                    rel_path = os.path.relpath(abs_path, self.local_root).replace(
                        "\\",
                        "/",
                    )
                    try:
                        mtime = pathlib.Path(abs_path).stat().st_mtime
                    except OSError:
                        continue
                    known_mtime = self.manifest.get(rel_path)
                    if known_mtime is None or mtime > known_mtime + 0.5:
                        changed.append((abs_path, rel_path))
        except (AttributeError, TypeError, RuntimeError, OSError) as e:
            write_debug_log(f"[Silenced] run: {e}")
        self.found.emit(self.pair_id, changed)


# ── SyncTab ───────────────────────────────────────────────────────────────────


class SyncTab(QWidget):
    """Folder sync tab.  Presents a list of watched folder pairs and shows
    per-file upload status beneath each pair.
    """

    def __init__(
        self,
        client: MochaClient,
        sync_settings_provider: SyncSettingsProvider,
        parent: QWidget | None = None,
        upload_manager: UploadManager | None = None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._manager = upload_manager
        self._sync_settings_provider = sync_settings_provider

        # pair_id → {local, remote, status, manifest, worker, scan_worker,
        #             tree_item, file_items, paused, error_msg}
        self._pairs: dict[str, dict] = {}
        self._workers: list[QThread] = []
        # job_id → (pair_id, rel_path) for routing manager signals
        self._job_map: dict[int, tuple[str, str]] = {}

        self._scan_timer = QTimer(self)
        self._scan_timer.setInterval(SCAN_INTERVAL * 1000)
        self._scan_timer.timeout.connect(self._scan_all)

        self._speed_decay_timer = QTimer(self)
        self._speed_decay_timer.setInterval(1000)
        self._speed_decay_timer.timeout.connect(self._refresh_decayed_speeds)

        self._build_ui()
        self._load_pairs()
        self._connect_manager()
        self._scan_timer.start()
        self._speed_decay_timer.start()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        self._build_toolbar(outer)
        self._build_tree(outer)
        self._build_status_bar(outer)

    def _build_toolbar(self, parent_lay: QVBoxLayout) -> None:
        tb = QHBoxLayout()
        tb.setSpacing(4)

        from ..theme import accent_qcolor, get_accent, notifier

        self.add_btn = self._tb("  Add Folder", "folder", get_accent(), self._add_pair)
        self.refresh_btn = self._tb(
            "  Refresh",
            "refresh-cw",
            get_accent(),
            self._refresh_action,
        )
        self.pause_btn = self._tb(
            "  Pause All",
            "pause",
            get_accent(),
            self._toggle_pause_all,
        )
        self.remove_btn = self._tb(
            "  Remove",
            "trash-2",
            "#f87171",
            self._remove_selected,
            danger=True,
        )

        self.remove_btn.setEnabled(False)

        for btn in (self.add_btn, self.pause_btn, self.remove_btn):
            tb.addWidget(btn)
        tb.addStretch()

        from ..theme import get_font

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet(
            f"color:{accent_qcolor().name()}; font-size:{int(get_font()[1])}px; background:transparent;",
        )
        tb.addWidget(self.status_lbl)
        parent_lay.addLayout(tb)
        with contextlib.suppress(Exception):
            notifier().accent_changed.connect(self._on_accent_changed)

    def _on_accent_changed(self, _old: str, _new: str) -> None:
        try:
            from ..theme import accent_qcolor, get_accent

            self.add_btn.setIcon(lucide_icon("folder", get_accent(), 13))
            self.refresh_btn.setIcon(lucide_icon("refresh-cw", get_accent(), 13))
            self.pause_btn.setIcon(lucide_icon("pause", get_accent(), 13))
            from ..theme import get_font

            self.status_lbl.setStyleSheet(
                f"color:{accent_qcolor().name()}; font-size:{int(get_font()[1])}px; background:transparent;",
            )
        except (AttributeError, TypeError, RuntimeError, ImportError, ValueError) as e:
            write_debug_log(f"[Silenced] _on_accent_changed: {e}")

    def _build_tree(self, parent_lay: QVBoxLayout) -> None:
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

        from PySide6.QtWidgets import QHeaderView

        hdr = self.tree.header()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(True)
        hdr.resizeSection(0, 260)
        hdr.resizeSection(1, 120)
        hdr.resizeSection(2, 120)
        parent_lay.addWidget(self.tree, 1)

    def _build_status_bar(self, parent_lay: QVBoxLayout) -> None:
        self.footer_lbl = QLabel("")
        self.footer_lbl.setObjectName("log_console")
        self.footer_lbl.setWordWrap(True)
        self.footer_lbl.hide()
        parent_lay.addWidget(self.footer_lbl)

    def _tb(
        self,
        label: str,
        icon_name: str,
        color: str,
        slot: Callable[[], None],
        danger: bool = False,
    ) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName("tb_btn_danger" if danger else "tb_btn")
        btn.setIcon(lucide_icon(icon_name, color, 13))
        btn.setIconSize(QSize(13, 13))
        btn.clicked.connect(slot)
        return btn

    # ── Pair management ───────────────────────────────────────────────────────

    def _add_pair(self) -> None:
        if not self._client.has_api_key:
            QMessageBox.warning(
                self,
                "API key required",
                "Enter your API key in Settings before adding sync folders.",
            )
            return

        # 1. Pick local folder
        local = QFileDialog.getExistingDirectory(self, "Select local folder to sync")
        if not local:
            return

        # 2. Pick remote folder via existing dialog
        from ..dialogs import FolderBrowserDialog

        dlg = FolderBrowserDialog(self._client, "/", parent=self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        remote = dlg.selected or "/"
        # Create a subfolder on the remote using the local folder's base name so
        # the watched folder contents live under <remote>/<local_basename>/...
        local_name = pathlib.Path(local.rstrip("/\\")).name or local
        if remote.rstrip("/") == "" or remote == "/":
            remote = f"/{local_name}"
        else:
            remote = remote.rstrip("/") + f"/{local_name}"

        pair_id = f"{local}::{remote}"
        if pair_id in self._pairs:
            QMessageBox.information(
                self,
                "Already watching",
                "This local → remote combination is already in the list.",
            )
            return

        self._register_pair(pair_id, local, remote, manifest={}, paused=False)
        self._save_pairs()
        self._set_status(
            f"{len(self._pairs)} pair{'s' if len(self._pairs) != 1 else ''} watched",
        )

        # Immediate first scan
        self._scan_pair(pair_id)

    def _register_pair(
        self,
        pair_id: str,
        local: str,
        remote: str,
        manifest: dict,
        paused: bool,
    ) -> None:
        """Create the tree item and state entry for a pair."""
        local_name = pathlib.Path(local.rstrip("/\\")).name or local
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
            "local": local,
            "remote": remote,
            "status": _ST_PAUSED if paused else _ST_IDLE,
            "manifest": manifest,  # {rel_path: mtime_float}
            "scan_worker": None,
            "tree_item": root_item,
            "file_items": {},  # rel_path → QTreeWidgetItem
            "folder_items": {},  # rel_folder_path → QTreeWidgetItem
            "paused": paused,
            "error_msg": "",
        }
        self._refresh_pair_badge(pair_id)
        # Populate initial folder/file tree from disk so the user sees a
        # navigable nested view immediately instead of a flat list.
        with contextlib.suppress(Exception):
            self._populate_initial_tree(pair_id)

    def _remove_selected(self) -> None:
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

        if (
            QMessageBox.question(
                self,
                "Remove sync pair",
                "Stop watching this folder?\n(Local and remote files are not deleted.)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        self._stop_pair(pair_id)
        pair = self._pairs.pop(pair_id)
        idx = self.tree.indexOfTopLevelItem(pair["tree_item"])
        if idx >= 0:
            self.tree.takeTopLevelItem(idx)
        self._save_pairs()
        self._set_status(
            f"{len(self._pairs)} pair{'s' if len(self._pairs) != 1 else ''} watched",
        )

    def _stop_pair(self, pair_id: str) -> None:
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        # Cancel any queued/running uploads for this pair
        if self._manager is not None:
            for job_id in [
                jid for jid, (pid, _rel) in self._job_map.items() if pid == pair_id
            ]:
                self._manager.cancel(job_id)
                self._job_map.pop(job_id, None)
        sw = pair.get("scan_worker")
        if sw and not sw.isFinished():
            sw.terminate()

    # ── Pause / resume ────────────────────────────────────────────────────────

    def _toggle_pause_all(self) -> None:
        any_active = any(not p["paused"] for p in self._pairs.values())
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

    def _toggle_pause_pair(self, pair_id: str) -> None:
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

    def _scan_all(self) -> None:
        for pair_id, pair in self._pairs.items():
            if pair["paused"]:
                continue
            if pair["status"] in (_ST_UPLOADING, _ST_SCANNING):
                continue
            self._scan_pair(pair_id)

    def _scan_pair(self, pair_id: str) -> None:
        pair = self._pairs.get(pair_id)
        if not pair or pair["paused"]:
            return
        if pair.get("scan_worker") and not pair["scan_worker"].isFinished():
            return

        pair["status"] = _ST_SCANNING
        self._refresh_pair_badge(pair_id)

        sw = _ScanWorker(pair_id, pair["local"], pair["manifest"])
        sw.found.connect(self._on_scan_done)
        sw.finished.connect(partial(self._remove_worker, sw))
        pair["scan_worker"] = sw
        self._workers.append(sw)
        sw.start()

    def _remove_worker(self, w: _ScanWorker) -> None:
        if w in self._workers:
            self._workers.remove(w)

    def _on_scan_done(self, pair_id: str, changed: list) -> None:
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

    def _start_upload(self, pair_id: str, changed: list[tuple[str, str]]) -> None:
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        assert self._manager is not None

        _conc, chunk_mb, max_chunks = self._sync_settings_provider.get_sync_settings()
        remote_root = pair["remote"].rstrip("/")

        # Enqueue per-file uploads through the shared pipeline
        pair["status"] = _ST_UPLOADING
        self._refresh_pair_badge(pair_id)

        # Ensure file child rows exist / reset them and enqueue
        for abs_path, rel_path in changed:
            self._ensure_file_item(pair_id, rel_path, "Queued")
            remote_dest = remote_root + "/" + rel_path
            job = UploadJob(
                file_pairs=[(abs_path, remote_dest)],
                chunk_size_mb=chunk_mb,
                max_chunks=max_chunks,
                source="sync",
                ref=(pair_id, rel_path),
                priority=PRIORITY_SYNC,
            )
            job_id = self._manager.enqueue(job)
            self._job_map[job_id] = (pair_id, rel_path)

    def _on_upload_status(self, pair_id: str, rel_path: str, msg: str) -> None:
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        # Update the specific file's status
        pair["_active_rel"] = rel_path
        if "[DEBUG]" not in msg:
            self._set_file_status(pair_id, rel_path, "Uploading…")

    def _on_upload_speed(
        self,
        pair_id: str,
        bps: float,
        rel_path: str | None = None,
    ) -> None:
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        pair["_speed_bps"] = bps
        pair["_speed_ts"] = time.monotonic()
        rel = rel_path or pair.get("_active_rel")
        if rel:
            if bps < _KB:
                speed_str = f"{bps:.3f} B/s"
            elif bps < _KB**2:
                speed_str = f"{bps / _KB:.3f} KB/s"
            else:
                speed_str = f"{bps / 1024**2:.3f} MB/s"
            # Use per-file bytes (stored under file-specific state) if present
            file_state = pair.get("file_state", {}).get(rel, {})
            done = file_state.get("done", pair.get("_bytes_done", 0))
            total = file_state.get("total", pair.get("_bytes_total", 0))
            size_str = (
                (f"{UploadWorker._fmt_size(done)} / {UploadWorker._fmt_size(total)}")
                if total
                else ""
            )
            self._set_file_detail(pair_id, rel, speed_str, size_str)
        self._refresh_pair_badge(pair_id)

    def _on_upload_bytes(
        self,
        pair_id: str,
        done: int,
        total: int,
        rel_path: str | None = None,
    ) -> None:
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
                    size_str = f"{UploadWorker._fmt_size(int(done))} / {UploadWorker._fmt_size(int(total))}"
                else:
                    size_str = ""
                # format speed using last known _speed_bps
                bps = pair.get("_speed_bps", 0.0)
                if bps < _KB:
                    speed_str = f"{bps:.3f} B/s"
                elif bps < _KB**2:
                    speed_str = f"{bps / _KB:.3f} KB/s"
                else:
                    speed_str = f"{bps / 1024**2:.3f} MB/s"
                self._set_file_detail(pair_id, rel, speed_str, size_str)
        else:
            pair["_bytes_done"] = int(done)
            pair["_bytes_total"] = int(total)

    def _on_upload_done(self, pair_id: str, changed: list, result: dict) -> None:
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

        from ..sound_player import play_sound_event

        for rel_path in uploaded:
            try:
                abs_path = str(pathlib.Path(pair["local"]) / rel_path)
                mtime = pathlib.Path(abs_path).stat().st_mtime
            except (AttributeError, TypeError, RuntimeError, OSError) as e:
                write_debug_log(f"[Silenced] _on_upload_done: {e}")
                mtime = time.time()
            pair["manifest"][rel_path] = mtime
            self._set_file_status(pair_id, rel_path, "Synced ✓")
            self._set_file_detail(pair_id, rel_path, "", "")
            # clear file_state for completed file
            if pair.get("file_state") and rel_path in pair["file_state"]:
                pair["file_state"].pop(rel_path, None)
            play_sound_event("sound_sync_file")

        # If nothing left for this pair mark idle
        still_pending = any(pid == pair_id for pid, _rel in self._job_map.values())
        if not still_pending:
            pair["status"] = _ST_IDLE
            pair["_active_rel"] = None
            self._refresh_pair_badge(pair_id)
            self._save_pairs()
            play_sound_event("sound_sync_folder")

    def _on_upload_error(self, pair_id: str, msg: str) -> None:
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        pair["status"] = _ST_ERROR
        pair["error_msg"] = msg
        self._refresh_pair_badge(pair_id)

    def _connect_manager(self) -> None:
        if self._manager is None:
            return
        mgr = self._manager
        mgr.job_status.connect(self._on_job_status)
        mgr.job_speed.connect(self._on_job_speed)
        mgr.job_bytes.connect(self._on_job_bytes)
        mgr.job_done.connect(self._on_job_done)
        mgr.job_error.connect(self._on_job_error)

    def _on_job_status(
        self,
        _job_id: int,
        ref: tuple[str, str] | None,
        msg: str,
    ) -> None:
        if ref is None:
            return
        pair_id, rel_path = ref
        self._on_upload_status(pair_id, rel_path, msg)

    def _on_job_speed(
        self,
        _job_id: int,
        ref: tuple[str, str] | None,
        bps: float,
    ) -> None:
        if ref is None:
            return
        pair_id, rel_path = ref
        self._on_upload_speed(pair_id, bps, rel_path)

    def _on_job_bytes(
        self,
        _job_id: int,
        ref: tuple[str, str] | None,
        done: int,
        total: int,
    ) -> None:
        if ref is None:
            return
        pair_id, rel_path = ref
        self._on_upload_bytes(pair_id, done, total, rel_path)

    def _on_job_done(
        self,
        job_id: int,
        ref: tuple[str, str] | None,
        result: dict,
    ) -> None:
        if ref is None:
            return
        pair_id, rel_path = ref
        self._job_map.pop(job_id, None)
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        abs_path = str(pathlib.Path(pair["local"]) / rel_path)
        self._on_upload_done(pair_id, [(abs_path, rel_path)], result)

    def _on_job_error(self, job_id: int, ref: tuple[str, str] | None, msg: str) -> None:
        if ref is None:
            return
        pair_id, _rel_path = ref
        self._job_map.pop(job_id, None)
        self._on_upload_error(pair_id, msg)

    # ── Tree helpers ──────────────────────────────────────────────────────────

    def _ensure_file_item(self, pair_id: str, rel_path: str, status_text: str) -> None:
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        # Build/reuse intermediate folder nodes so file items appear in a
        # nested tree instead of being direct children of the pair root.
        root_item = pair["tree_item"]
        if rel_path not in pair["file_items"]:
            # Determine parent folder and ensure folder nodes exist
            parent_rel = str(pathlib.Path(rel_path).parent).replace("\\", "/")
            if parent_rel:
                parent_item = self._ensure_folder_item(pair_id, parent_rel)
                if parent_item is None:
                    parent_item = root_item
            else:
                parent_item = root_item

            child = QTreeWidgetItem()
            child.setText(0, f"   {pathlib.Path(rel_path).name}")
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

    def _ensure_folder_item(
        self,
        pair_id: str,
        folder_rel: str,
    ) -> QTreeWidgetItem | None:
        """Ensure a QTreeWidgetItem exists for the given folder relative
        path under the pair root. Returns the folder item (creates parents
        recursively as needed) and caches it in pair['folder_items'].
        """
        pair = self._pairs.get(pair_id)
        if not pair:
            return None
        # normalize
        folder_rel = folder_rel.replace("\\", "/").strip("/")
        if folder_rel in pair.get("folder_items", {}):
            return pair["folder_items"][folder_rel]

        parent_rel = str(pathlib.Path(folder_rel).parent).replace("\\", "/").strip("/")
        if parent_rel:
            parent_item = self._ensure_folder_item(pair_id, parent_rel)
            if parent_item is None:
                return None
        else:
            parent_item = pair["tree_item"]

        # create folder item
        folder_item = QTreeWidgetItem()
        folder_item.setText(0, f"   {pathlib.Path(folder_rel).name}")
        from ..theme import get_accent

        folder_item.setIcon(0, lucide_icon("folder", get_accent(), 12))
        folder_item.setForeground(0, QColor("#f0ece6"))
        folder_item.setForeground(1, QColor("#9c9484"))
        folder_item.setForeground(2, QColor("#9c9484"))
        folder_item.setExpanded(False)
        parent_item.addChild(folder_item)
        pair.setdefault("folder_items", {})[folder_rel] = folder_item
        return folder_item

    def _populate_initial_tree(self, pair_id: str) -> None:
        """Walk the local folder on disk and populate folder & file nodes so
        the UI shows a nested tree immediately.
        """
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        local_root = pair.get("local")
        if not local_root or not pathlib.Path(local_root).is_dir():
            return
        # Walk and create folder nodes first, then file items
        for dirpath, _dirs, files in os.walk(local_root):
            rel_dir = os.path.relpath(dirpath, local_root).replace("\\", "/")
            if rel_dir == ".":
                rel_dir = ""
            # create folder node (skip root)
            if rel_dir:
                with contextlib.suppress(Exception):
                    self._ensure_folder_item(pair_id, rel_dir)
            # create file nodes
            for fname in files:
                abs_path = str(pathlib.Path(dirpath) / fname)
                rel_path = os.path.relpath(abs_path, local_root).replace("\\", "/")
                # mark synced if present in manifest, otherwise blank
                status = "Synced ✓" if rel_path in (pair.get("manifest") or {}) else ""
                with contextlib.suppress(Exception):
                    self._ensure_file_item(pair_id, rel_path, status)

    def _set_file_status(self, pair_id: str, rel_path: str, status: str) -> None:
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        item = pair["file_items"].get(rel_path)
        if item:
            item.setText(1, status)
            from ..theme import get_accent

            color = "#4ade80" if "✓" in status else get_accent()
            item.setForeground(1, QColor(color))

    def _set_file_detail(
        self,
        pair_id: str,
        rel_path: str,
        speed: str,
        size: str,
    ) -> None:
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        item = pair["file_items"].get(rel_path)
        if item:
            item.setText(2, f"{speed}  {size}".strip())

    def _refresh_pair_badge(self, pair_id: str) -> None:
        pair = self._pairs.get(pair_id)
        if not pair:
            return
        root = pair["tree_item"]
        state = pair["status"]

        from ..theme import get_accent

        badge_map = {
            _ST_IDLE: ("● Idle", "#5a5650"),
            _ST_SCANNING: ("◌ Scanning", "#9c9484"),
            _ST_UPLOADING: ("↑ Uploading", get_accent()),
            _ST_PAUSED: ("‖ Paused", "#5a5650"),
            _ST_ERROR: ("✕ Error", "#f87171"),
        }
        text, color = badge_map.get(state, ("", "#5a5650"))
        root.setText(1, text)
        root.setForeground(1, QColor(color))

        # Show speed on root when uploading
        if state == _ST_UPLOADING:
            bps = pair.get("_speed_bps", 0.0)
            if bps > 0:
                if bps < _KB:
                    speed_str = f"{bps:.3f} B/s"
                elif bps < _KB**2:
                    speed_str = f"{bps / _KB:.3f} KB/s"
                else:
                    speed_str = f"{bps / 1024**2:.3f} MB/s"
                root.setText(2, speed_str)
                from ..theme import accent_qcolor

                root.setForeground(2, accent_qcolor())
            else:
                root.setText(2, "")
        elif state == _ST_ERROR:
            root.setText(2, pair.get("error_msg", "")[:40])
            root.setForeground(2, QColor("#f87171"))
        # When idle, show "Up to date" if we have a manifest / synced files
        elif state == _ST_IDLE:
            has_synced = bool(pair.get("manifest") or pair.get("file_items"))
            if has_synced:
                root.setText(2, "Up to date")
                root.setForeground(2, QColor("#4ade80"))
            else:
                root.setText(2, "")
        else:
            root.setText(2, "")

    def _refresh_decayed_speeds(self) -> None:
        """Re-render pair speeds with stall decay so a frozen transfer doesn't
        keep showing its last value forever."""
        now = time.monotonic()
        for pair in self._pairs.values():
            if pair.get("status") != _ST_UPLOADING:
                continue
            root = pair.get("tree_item")
            if root is None:
                continue
            bps = decay_speed(
                pair.get("_speed_bps", 0.0),
                pair.get("_speed_ts", 0.0),
                now,
            )
            if bps > 0:
                if bps < _KB:
                    speed_str = f"{bps:.3f} B/s"
                elif bps < _KB**2:
                    speed_str = f"{bps / _KB:.3f} KB/s"
                else:
                    speed_str = f"{bps / 1024**2:.3f} MB/s"
                root.setText(2, speed_str)
                from ..theme import accent_qcolor

                root.setForeground(2, accent_qcolor())
            else:
                root.setText(2, "")

    # ── Selection / context menu ──────────────────────────────────────────────

    def _on_selection_changed(self) -> None:
        items = self.tree.selectedItems()
        has = bool(items)
        self.remove_btn.setEnabled(has)
        # Enable refresh btn when selection exists, otherwise allow global refresh
        self.refresh_btn.setEnabled(True)

    def _context_menu(self, pos: QPoint) -> None:
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
            "QMenu::item:selected { background:#332b1a; }",
        )

        from ..theme import get_accent

        if pair["paused"]:
            a = menu.addAction(lucide_icon("play", get_accent(), 12), "Resume")
            a.triggered.connect(partial(self._toggle_pause_pair, pair_id))
        else:
            a = menu.addAction(lucide_icon("pause", get_accent(), 12), "Pause")
            a.triggered.connect(partial(self._toggle_pause_pair, pair_id))

        s1 = menu.addAction(lucide_icon("refresh-cw", get_accent(), 12), "Sync now")
        s1.triggered.connect(partial(self._scan_pair, pair_id))

        s2 = menu.addAction(lucide_icon("refresh-cw", get_accent(), 12), "Refresh")
        s2.triggered.connect(partial(self._refresh_action, pair_id))
        menu.addSeparator()
        menu.addAction(
            lucide_icon("trash-2", "#f87171", 12),
            "Remove",
        ).triggered.connect(self._remove_selected)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _refresh_action(self, pair_id: str | None = None) -> None:
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
                parent = it.parent()
                if pid is None and parent:
                    pid = parent.data(0, Qt.ItemDataRole.UserRole)
                if pid and pid not in seen:
                    seen.add(pid)
                    self._scan_pair(pid)
            return

        # no selection — refresh all
        for pid in list(self._pairs.keys()):
            self._scan_pair(pid)

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load_pairs(self) -> None:
        from PySide6.QtCore import QSettings

        s = QSettings(ORG_NAME, APP_NAME)
        raw = s.value("sync_pairs", None)
        if not raw:
            return
        try:
            pairs = json.loads(raw)
        except (AttributeError, TypeError, RuntimeError) as e:
            write_debug_log(f"[Silenced] _load_pairs: {e}")
            return
        for p in pairs:
            pair_id = f"{p['local']}::{p['remote']}"
            if pair_id in self._pairs:
                continue
            if not pathlib.Path(p.get("local", "")).is_dir():
                continue  # local folder gone — skip silently
            self._register_pair(
                pair_id=pair_id,
                local=p["local"],
                remote=p["remote"],
                manifest=p.get("manifest", {}),
                paused=p.get("paused", False),
            )
            # Populate child file rows from the saved manifest so users can
            # expand a pair and see previously uploaded files as "Synced ✓".
            try:
                self._pairs.get(pair_id)
                manifest = p.get("manifest", {}) or {}
                for rel_path in sorted(manifest.keys()):
                    # ensure child exists and mark as synced
                    self._ensure_file_item(pair_id, rel_path, "Synced ✓")
                    self._set_file_detail(pair_id, rel_path, "", "")
            except (AttributeError, TypeError, RuntimeError) as e:
                write_debug_log(f"[Silenced] _load_pairs: {e}")
        self._set_status(
            f"{len(self._pairs)} pair{'s' if len(self._pairs) != 1 else ''} watched"
            if self._pairs
            else "No folders watched",
        )

    def _save_pairs(self) -> None:
        from PySide6.QtCore import QSettings

        s = QSettings(ORG_NAME, APP_NAME)
        data = [
            {
                "local": pair["local"],
                "remote": pair["remote"],
                "manifest": pair["manifest"],
                "paused": pair["paused"],
            }
            for pair in self._pairs.values()
        ]
        with contextlib.suppress(Exception):
            s.setValue("sync_pairs", json.dumps(data))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_status(self, msg: str) -> None:
        self.status_lbl.setText(msg)

    def _log(self, msg: str) -> None:
        if msg.startswith("[DEBUG]") and not self._sync_settings_provider.get_debug():
            return
        self.footer_lbl.setText(msg)
        self.footer_lbl.show()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._scan_timer.stop()
        for pair_id in list(self._pairs):
            self._stop_pair(pair_id)
        super().closeEvent(event)
