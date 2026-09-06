"""tabs/mass_upload.py — MassUploadSection widget.

Embedded into the Upload tab of MochaTools; not a standalone tab.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import time
from functools import partial
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..constants import DEFAULT_CHUNK_SIZE_MB, DEFAULT_MAX_CHUNKS
from ..dialogs import FolderBrowserDialog
from ..logging_utils import write_debug_log
from ..providers import DefaultMassSettingsProvider, MassSettingsProvider
from ..theme import accent_qcolor, get_accent, get_font, notifier
from ..ui import lucide_icon
from ..upload_pipeline import PRIORITY_MASS, UploadJob, UploadManager
from ..utils import decay_speed

_KB = 1024

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..mocha_client import MochaClient


class MassUploadSection(QWidget):
    _COL_NAME = 0
    _COL_SIZE = 1
    _COL_DEST = 2
    _COL_STATUS = 3

    def __init__(
        self,
        client: MochaClient,
        mass_settings_provider: MassSettingsProvider | None = None,
        on_upload_done: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
        embedded: bool = True,
        upload_manager: UploadManager | None = None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._manager = upload_manager
        self._mass_settings_provider = (
            mass_settings_provider
            or DefaultMassSettingsProvider(DEFAULT_CHUNK_SIZE_MB, DEFAULT_MAX_CHUNKS)
        )
        # on_upload_done(remote_dest: str) — called when each file finishes
        self._on_upload_done_cb = on_upload_done
        self._queue: list[dict] = []
        self._job_map: dict[int, dict] = {}
        self._cancelled = False
        self._embedded = embedded
        self._build_ui()
        self._connect_manager()
        self._speed_decay_timer = QTimer(self)
        self._speed_decay_timer.setInterval(1000)
        self._speed_decay_timer.timeout.connect(self._refresh_decayed_speed)
        self._speed_decay_timer.start()

    def _build_ui(self) -> None:
        # If embedded into another scroll area (the main Upload tab), avoid
        # creating an internal QScrollArea to prevent double scrolling.
        if self._embedded:
            root_lay = QVBoxLayout(self)
            root_lay.setContentsMargins(0, 0, 0, 0)
            root_lay.setSpacing(0)
            parent_lay = QVBoxLayout()
            parent_lay.setContentsMargins(0, 0, 0, 0)
            parent_lay.setSpacing(12)
            root_lay.addLayout(parent_lay)

            self._build_drop_section(parent_lay)
            self._build_queue_table(parent_lay)
            self._build_queue_toolbar(parent_lay)
            self._build_progress_card(parent_lay)
            self._build_action_buttons(parent_lay)
            return

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

        self._build_drop_section(outer)
        self._build_queue_table(outer)
        self._build_queue_toolbar(outer)
        self._build_progress_card(outer)
        self._build_action_buttons(outer)

    def _build_drop_section(self, parent_lay: QVBoxLayout) -> None:
        from ..ui.widgets import DropZone

        # The Upload tab's mode switcher already labels this view "Multiple
        # files", so use a plain section header here for consistency with the
        # single-file view ("File" / "Files").
        parent_lay.addWidget(self._sh("Files"))

        add_card = self._card()
        add_lay = QVBoxLayout(add_card)
        add_lay.setSpacing(8)

        self._drop = DropZone()
        self._drop.selection_changed.connect(self._on_drop)
        add_lay.addWidget(self._drop)

        dest_row = QHBoxLayout()
        dest_lbl = QLabel("Destination")
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
        parent_lay.addWidget(add_card)

    def _build_queue_table(self, parent_lay: QVBoxLayout) -> None:
        self._tree = QTreeWidget()
        self._tree.setColumnCount(4)
        self._tree.setHeaderLabels(["File / Folder", "Size", "Destination", "Status"])
        self._tree.setRootIsDecorated(False)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        hdr = self._tree.header()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(True)
        hdr.resizeSection(0, 280)
        hdr.resizeSection(1, 90)
        hdr.resizeSection(2, 160)
        hdr.resizeSection(3, 110)
        self._tree.setMinimumHeight(160)
        self._tree.itemDoubleClicked.connect(self._edit_dest)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._queue_context_menu)
        parent_lay.addWidget(self._tree)

    def _build_queue_toolbar(self, parent_lay: QVBoxLayout) -> None:
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
        self._queue_lbl.setStyleSheet(
            f"color: {accent_qcolor().name()}; font-size:{int(get_font()[1])}px; background:transparent;",
        )
        qtb.addWidget(self._queue_lbl)
        parent_lay.addLayout(qtb)
        with contextlib.suppress(Exception):
            notifier().accent_changed.connect(self._on_accent_changed)

    def _on_accent_changed(self, _old: str, _new: str) -> None:
        with contextlib.suppress(Exception):
            self._queue_lbl.setStyleSheet(
                f"color: {accent_qcolor().name()}; font-size:{int(get_font()[1])}px; background:transparent;",
            )

    def _build_progress_card(self, parent_lay: QVBoxLayout) -> None:
        prog_card = self._card()
        prog_lay = QVBoxLayout(prog_card)
        prog_lay.setSpacing(8)

        top_row = QHBoxLayout()
        self._badge_lbl = QLabel("● Idle")
        self._badge_lbl.setObjectName("status_badge")
        top_row.addWidget(self._badge_lbl)
        top_row.addStretch()
        prog_lay.addLayout(top_row)

        speed_row = QHBoxLayout()
        speed_lbl = QLabel("Speed:")
        speed_lbl.setObjectName("field_label")
        self._speed_lbl = QLabel("")
        self._speed_lbl.setObjectName("status_label")
        self._speed_lbl.setStyleSheet(
            f"color:{accent_qcolor().name()}; font-size:{int(get_font()[1])}px; background:transparent;",
        )
        speed_row.addWidget(speed_lbl)
        speed_row.addWidget(self._speed_lbl)
        speed_row.addStretch()
        self._transferred_lbl = QLabel("")
        self._transferred_lbl.setStyleSheet(
            f"color:{accent_qcolor().name()}; font-size:{int(get_font()[1])}px; background:transparent;",
        )
        speed_row.addWidget(self._transferred_lbl)
        prog_lay.addLayout(speed_row)

        pbar_row = QHBoxLayout()
        self._prog_bar = QProgressBar()
        self._prog_bar.setMaximum(100_000)
        self._prog_bar.setValue(0)
        self._pct_lbl = QLabel("0.000%")
        self._pct_lbl.setObjectName("status_label")
        self._pct_lbl.setFixedWidth(58)
        pbar_row.addWidget(self._prog_bar, 1)
        pbar_row.addWidget(self._pct_lbl)
        prog_lay.addLayout(pbar_row)

        self._log_lbl = QLabel("Add files or folders above, then click Start.")
        self._log_lbl.setObjectName("log_console")
        self._log_lbl.setWordWrap(True)
        self._log_lbl.setMinimumHeight(46)
        prog_lay.addWidget(self._log_lbl)
        parent_lay.addWidget(prog_card)

    def _build_action_buttons(self, parent_lay: QVBoxLayout) -> None:
        self._start_btn = QPushButton("  Start upload")
        self._start_btn.setObjectName("upload_btn")
        self._start_btn.setIcon(lucide_icon("upload", "#111010", 15))
        self._start_btn.setIconSize(QSize(15, 15))
        self._start_btn.setMinimumHeight(42)
        self._start_btn.clicked.connect(self._start)
        parent_lay.addWidget(self._start_btn)

        self._cancel_btn = QPushButton("  Cancel")
        self._cancel_btn.setObjectName("browse_btn")
        self._cancel_btn.setIcon(lucide_icon("x", get_accent(), 13))
        self._cancel_btn.setIconSize(QSize(13, 13))
        self._cancel_btn.setMinimumHeight(36)
        self._cancel_btn.clicked.connect(self._cancel)
        self._cancel_btn.hide()
        parent_lay.addWidget(self._cancel_btn)
        parent_lay.addStretch()

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _sh(text: str) -> QLabel:
        lbl = QLabel(text.upper())
        lbl.setObjectName("section_header")
        return lbl

    @staticmethod
    def _card() -> QFrame:
        f = QFrame()
        f.setObjectName("card")
        return f

    @staticmethod
    def _fmt(n: int) -> str:
        if n < _KB:
            return f"{n} B"
        if n < _KB**2:
            return f"{n / _KB:.3f} KB"
        if n < 1024**3:
            return f"{n / 1024**2:.3f} MB"
        return f"{n / 1024**3:.3f} GB"

    def _set_badge(self, text: str, color: str) -> None:
        self._badge_lbl.setText(f"● {text}")
        bg = {
            get_accent(): "#2a2215",
            "#4ade80": "#0f2318",
            "#f87171": "#2a0f0f",
            "#9ca3af": "#1e1c19",
        }
        bd = {
            get_accent(): "#4a3b1e",
            "#4ade80": "#1e4a30",
            "#f87171": "#4a1e1e",
            "#9ca3af": "#2e2b27",
        }
        self._badge_lbl.setStyleSheet(
            f"background-color:{bg.get(color, '#1e1c19')};"
            f"border:1px solid {bd.get(color, '#2e2b27')};"
            f"border-radius:10px; color:{color}; font-size:11px;"
            f"font-weight:600; padding:2px 10px;",
        )

    def _log(self, msg: str) -> None:
        if msg.startswith("[DEBUG]"):
            write_debug_log(msg)
            if not self._mass_settings_provider.get_debug():
                return
        self._log_lbl.setText(msg)

    # ── Queue label + overall progress ───────────────────────────────────────

    def _update_queue_label(self) -> None:
        total = len(self._queue)
        done = sum(1 for e in self._queue if e.get("status") == "done")
        errors = sum(1 for e in self._queue if e.get("status") == "error")
        parts = [f"{total} item{'s' if total != 1 else ''}"]
        if done:
            parts.append(f"{done} done")
        if errors:
            parts.append(f"{errors} failed")
        self._queue_lbl.setText(" · ".join(parts))

    def _update_overall_progress(self) -> None:
        if not self._queue:
            return
        total_pct = sum(
            100.0
            if e.get("status") in ("done", "error", "cancelled")
            else e.get("_pct", 0.0)
            for e in self._queue
        )
        pct = total_pct / len(self._queue)
        self._prog_bar.setValue(int(pct * 1000))
        self._pct_lbl.setText(f"{pct:.3f}%")

    # ── Queue management ───────────────────────────────────────────────────

    def _on_drop(self, file_list: list[str], root: str) -> None:
        new_entries = []
        for local in file_list:
            rel = os.path.relpath(local, root).replace(os.sep, "/")
            if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
                rel = pathlib.Path(local).name
            dest_base = "/" + (self._default_dest.text().strip("/") or "")
            rdest = f"{dest_base}/{rel}" if dest_base != "/" else f"/{rel}"
            try:
                fsize = pathlib.Path(local).stat().st_size
            except OSError:
                fsize = 0
            entry = {
                "local": local,
                "root": root,
                "dest": rdest,
                "size": fsize,
                "status": "pending",
                "worker": None,
                "item": None,
                "_bytes_done": 0,
                "_bytes_total": fsize,
            }
            self._queue.append(entry)
            display_name = os.path.relpath(local, root).replace(os.sep, "/")
            item = QTreeWidgetItem(
                [display_name, self._fmt(entry["size"]), rdest, "Pending"],
            )
            item.setForeground(3, accent_qcolor())
            entry["item"] = item
            self._tree.addTopLevelItem(item)
            new_entries.append(entry)

        self._update_queue_label()

        # Offer a destination-folder picker for this batch
        if self._client.has_api_key:
            dlg = FolderBrowserDialog(
                self._client,
                self._default_dest.text().strip() or "/",
                parent=self,
            )
            dlg.setWindowTitle("Choose upload destination")
            if dlg.exec():
                chosen = dlg.selected.rstrip("/") or "/"
                self._default_dest.setText(chosen)
                for entry in new_entries:
                    if entry["status"] == "pending":
                        rel_filename = entry["dest"].rsplit("/", 1)[-1]
                        entry["dest"] = (
                            f"{chosen}/{rel_filename}"
                            if chosen != "/"
                            else f"/{rel_filename}"
                        )
                        entry["item"].setText(self._COL_DEST, entry["dest"])

        # Feed new entries into the running upload
        if self._job_map:
            for entry in new_entries:
                if entry["status"] == "pending":
                    self._enqueue_entry(entry)

    def _browse_default_dest(self) -> None:
        if not self._client.has_api_key:
            self._log("⚠ Enter API key in Settings first.")
            return
        dlg = FolderBrowserDialog(
            self._client,
            self._default_dest.text().strip() or "/",
            parent=self,
        )
        dlg.setWindowTitle("Choose default destination")
        if dlg.exec():
            write_debug_log(f"[MassUpload BrowseDest] dlg.selected={dlg.selected!r}")
            self._default_dest.setText(dlg.selected)
            write_debug_log(
                f"[MassUpload BrowseDest] _default_dest now={self._default_dest.text()!r}",
            )

    def _edit_dest(self, item: QTreeWidgetItem, _col: int) -> None:
        row = next((e for e in self._queue if e.get("item") is item), None)
        if row is None or row.get("status") in ("uploading", "done"):
            return
        if self._client.has_api_key:
            dlg = FolderBrowserDialog(
                self._client,
                row["dest"].rsplit("/", 1)[0] or "/",
                parent=self,
            )
            dlg.setWindowTitle("Choose destination folder")
            if dlg.exec():
                filename = row["dest"].rsplit("/", 1)[-1]
                folder = dlg.selected.rstrip("/")
                row["dest"] = f"{folder}/{filename}"
                item.setText(self._COL_DEST, row["dest"])
        else:
            new_dest, ok = QInputDialog.getText(
                self,
                "Edit destination",
                "Remote destination path:",
                QLineEdit.EchoMode.Normal,
                row["dest"],
            )
            if ok and new_dest.strip():
                row["dest"] = new_dest.strip()
                item.setText(self._COL_DEST, row["dest"])

    def _queue_context_menu(self, pos: QPoint) -> None:
        item = self._tree.itemAt(pos)
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background:#1f1f1f; border:1px solid #3a3a3a; border-radius:8px; color:#f0f0f0; font-size:12px; }"
            "QMenu::item { padding:6px 8px; }"
            "QMenu::item:selected { background:#332b1a; }",
        )
        if item:
            entry = next((e for e in self._queue if e.get("item") is item), None)
            idx = self._tree.indexOfTopLevelItem(item)
            count = self._tree.topLevelItemCount()

            move_up = menu.addAction(lucide_icon("move", get_accent(), 12), "Move up")
            move_up.setEnabled(idx > 0 and entry and entry.get("status") == "pending")
            move_up.triggered.connect(self._move_selected_up)

            move_dn = menu.addAction(lucide_icon("move", get_accent(), 12), "Move down")
            move_dn.setEnabled(
                idx < count - 1 and entry and entry.get("status") == "pending",
            )
            move_dn.triggered.connect(self._move_selected_down)

            menu.addSeparator()

            edit_dest = menu.addAction(
                lucide_icon("pencil", get_accent(), 12),
                "Edit destination",
            )
            edit_dest.setEnabled(
                entry and entry.get("status") not in ("uploading", "done"),
            )
            edit_dest.triggered.connect(partial(self._edit_dest, item, 0))

            menu.addSeparator()

            rm = menu.addAction(lucide_icon("trash-2", "#f87171", 12), "Remove")
            rm.setEnabled(entry and entry.get("status") != "uploading")
            rm.triggered.connect(self._remove_selected)
        else:
            c1 = menu.addAction(lucide_icon("check", get_accent(), 12), "Clear done")
            c1.triggered.connect(self._clear_done)
            c2 = menu.addAction(lucide_icon("x", "#f87171", 12), "Clear all")
            c2.triggered.connect(self._clear_all)

        menu.exec(self._tree.viewport().mapToGlobal(pos))

    # ── Row reordering ────────────────────────────────────────────────────

    def _move_entry(self, delta: int) -> None:
        selected = [
            e
            for e in self._queue
            if e.get("item") in self._tree.selectedItems()
            and e.get("status") == "pending"
        ]
        if not selected:
            return
        if delta > 0:
            selected.reverse()
        for entry in selected:
            qi = self._queue.index(entry)
            ti = self._tree.indexOfTopLevelItem(entry.get("item"))
            new_qi = qi + delta
            new_ti = ti + delta
            if new_qi < 0 or new_qi >= len(self._queue):
                continue
            if new_ti < 0 or new_ti >= self._tree.topLevelItemCount():
                continue
            if (
                entry.get("status") == "uploading"
                or self._queue[new_qi].get("status") == "uploading"
            ):
                continue
            self._queue[qi], self._queue[new_qi] = self._queue[new_qi], self._queue[qi]
            taken = self._tree.takeTopLevelItem(ti)
            self._tree.insertTopLevelItem(new_ti, taken)
            self._tree.setCurrentItem(taken)

    def _move_selected_up(self) -> None:
        self._move_entry(-1)

    def _move_selected_down(self) -> None:
        self._move_entry(1)

    # ── Row removal ───────────────────────────────────────────────────────

    def _detach_entry(self, entry: dict) -> None:
        """Clear the item ref before removal."""
        entry["item"] = None

    def _remove_selected(self) -> None:
        assert self._manager is not None
        for item in list(self._tree.selectedItems()):
            row = next((e for e in self._queue if e.get("item") is item), None)
            if row:
                if row.get("status") == "uploading":
                    for job_id, entry in list(self._job_map.items()):
                        if entry is row:
                            self._manager.cancel(job_id)
                            self._job_map.pop(job_id, None)
                    row["status"] = "cancelled"
                self._detach_entry(row)
                self._queue.remove(row)
            idx = self._tree.indexOfTopLevelItem(item)
            if idx >= 0:
                self._tree.takeTopLevelItem(idx)
        self._update_queue_label()

    def _clear_done(self) -> None:
        for entry in list(self._queue):
            if entry.get("status") in ("done", "error", "cancelled"):
                item = entry.get("item")
                self._detach_entry(entry)
                if item is not None:
                    idx = self._tree.indexOfTopLevelItem(item)
                    if idx >= 0:
                        self._tree.takeTopLevelItem(idx)
                self._queue.remove(entry)
        self._update_queue_label()

    def _clear_all(self) -> None:
        if self._job_map:
            self._cancel()
        for entry in list(self._queue):
            self._detach_entry(entry)
        self._tree.clear()
        self._queue.clear()
        self._prog_bar.setValue(0)
        self._pct_lbl.setText("0.000%")
        self._speed_lbl.setText("")
        self._transferred_lbl.setText("")
        self._update_queue_label()
        self._set_badge("Idle", "#9ca3af")
        self._log("Queue cleared.")

    # ── Upload engine ──────────────────────────────────────────────────────

    def _connect_manager(self) -> None:
        if self._manager is None:
            return
        mgr = self._manager
        mgr.job_progress.connect(self._on_job_progress)
        mgr.job_speed.connect(self._on_job_speed)
        mgr.job_status.connect(self._on_job_status)
        mgr.job_done.connect(self._on_job_done)
        mgr.job_error.connect(self._on_job_error)

    def _enqueue_entry(self, entry: dict) -> None:
        assert self._manager is not None
        if entry.get("item") is None:
            return
        entry["status"] = "uploading"
        entry["_bytes_done"] = 0
        if not entry.get("_bytes_total"):
            entry["_bytes_total"] = entry.get("size", 0)
        entry["_xfr"] = f"0 B / {self._fmt(entry['_bytes_total'])}"
        entry["item"].setText(self._COL_STATUS, f"Uploading…  ·  {entry['_xfr']}")
        entry["item"].setForeground(3, accent_qcolor())
        _conc, chunk_mb, max_chunks = self._mass_settings_provider.get_mass_settings()
        job = UploadJob(
            file_pairs=[(entry["local"], entry["dest"])],
            chunk_size_mb=chunk_mb,
            max_chunks=max_chunks,
            source="mass",
            ref=entry,
            priority=PRIORITY_MASS,
        )
        job_id = self._manager.enqueue(job)
        self._job_map[job_id] = entry

    def _start(self) -> None:
        if not self._client.has_api_key:
            self._log("⚠ Enter API key in Settings first.")
            return
        pending = [
            e for e in self._queue if e.get("status") in ("pending", "cancelled")
        ]
        if not pending:
            self._log("⚠ No pending items in the queue.")
            return
        self._cancelled = False
        self._start_btn.hide()
        self._cancel_btn.show()
        self._set_badge("Uploading", get_accent())
        self._log(f"Starting {len(pending)} upload{'s' if len(pending) != 1 else ''}…")
        for entry in pending:
            self._enqueue_entry(entry)

    # ── Signal handlers ────────────────────────────────────────────────────

    def _on_job_progress(self, job_id: int, _ref: object, pct: float) -> None:
        entry = self._job_map.get(job_id)
        if entry is None or entry not in self._queue or entry.get("item") is None:
            return
        entry["_pct"] = float(pct)
        entry["item"].setText(
            self._COL_STATUS,
            f"{pct:.3f}%  ·  {entry.get('_xfr', '')}",
        )
        self._update_overall_progress()

    def _on_job_speed(self, job_id: int, _ref: object, bps: float) -> None:
        entry = self._job_map.get(job_id)
        if entry is None:
            return
        entry["_speed_bps"] = bps
        entry["_speed_ts"] = time.monotonic()
        self._speed_lbl.setText(self._fmt_speed(self._total_speed()))

    def _total_speed(self, now: float | None = None) -> float:
        return sum(
            decay_speed(e.get("_speed_bps", 0.0), e.get("_speed_ts", 0.0), now)
            for e in self._queue
            if e.get("status") == "uploading"
        )

    def _refresh_decayed_speed(self) -> None:
        if not any(e.get("status") == "uploading" for e in self._queue):
            return
        self._speed_lbl.setText(self._fmt_speed(self._total_speed()))

    @staticmethod
    def _fmt_speed(bps: float) -> str:
        if bps < _KB:
            return f"{bps:.0f} B/s"
        if bps < _KB**2:
            return f"{bps / _KB:.1f} KB/s"
        return f"{bps / 1024**2:.2f} MB/s"

    def _on_job_status(self, job_id: int, _ref: object, msg: str) -> None:
        if self._job_map.get(job_id) is not None:
            self._log(msg)

    def _on_job_bytes(
        self,
        job_id: int,
        _ref: object,
        done_bytes: int,
        total_bytes: int,
    ) -> None:
        entry = self._job_map.get(job_id)
        if entry is None or entry not in self._queue:
            return
        if entry.get("status") in ("done", "error", "cancelled"):
            return
        done_bytes = int(done_bytes)
        total_bytes = int(total_bytes)
        if total_bytes > 0:
            entry["_bytes_total"] = total_bytes
        done_bytes = min(done_bytes, entry.get("_bytes_total"))
        entry["_bytes_done"] = done_bytes
        entry["_xfr"] = f"{self._fmt(done_bytes)} / {self._fmt(entry['_bytes_total'])}"

        all_done = sum(e.get("_bytes_done", 0) for e in self._queue)
        all_total = sum(e.get("_bytes_total", 0) for e in self._queue)
        if all_total > 0:
            self._transferred_lbl.setText(
                f"{self._fmt(all_done)} / {self._fmt(all_total)}",
            )

    def _on_job_done(self, job_id: int, _ref: object, _result: dict) -> None:
        entry = self._job_map.pop(job_id, None)
        if entry is None:
            return
        entry["status"] = "done"
        entry["_bytes_done"] = entry.get("_bytes_total", entry.get("size", 0))
        if entry in self._queue and entry.get("item") is not None:
            entry["item"].setText(self._COL_STATUS, "✓ Done")
            entry["item"].setForeground(3, QColor("#4ade80"))
        from ..sound_player import play_sound_event

        play_sound_event("sound_mass_file")
        if self._on_upload_done_cb is not None:
            dest = entry.get("dest", "")
            if dest:
                folder = "/".join(dest.rstrip("/").split("/")[:-1]) or "/"
                self._on_upload_done_cb(folder)
        self._update_queue_label()
        self._update_overall_progress()
        self._check_all_done()

    def _on_job_error(self, job_id: int, _ref: object, msg: str) -> None:
        entry = self._job_map.pop(job_id, None)
        if entry is None:
            return
        entry["status"] = "error"
        if entry in self._queue and entry.get("item") is not None:
            entry["item"].setText(self._COL_STATUS, "✗ Failed")
            entry["item"].setForeground(3, QColor("#f87171"))
            entry["item"].setToolTip(self._COL_STATUS, msg)
        self._log(f"✗ {pathlib.Path(entry.get('local', 'unknown')).name}: {msg}")
        self._update_queue_label()
        self._update_overall_progress()
        self._check_all_done()

    def _check_all_done(self) -> None:
        if self._job_map:
            return
        if any(e.get("status") == "pending" for e in self._queue):
            return
        errors = sum(1 for e in self._queue if e.get("status") == "error")
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
        from ..sound_player import play_sound_event

        play_sound_event("sound_mass_all")

    def _cancel(self) -> None:
        assert self._manager is not None
        self._cancelled = True
        for job_id in list(self._job_map.keys()):
            self._manager.cancel(job_id)
        self._job_map.clear()
        for entry in self._queue:
            if entry.get("status") == "uploading":
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
