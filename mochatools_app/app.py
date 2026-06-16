"""
app.py — MochaTools main window and entry point.

MochaTools is the application shell.  All tab content lives in
mochatools_app/tabs/ and shared widgets in mochatools_app/ui/.

Tab index reference:
  0  Upload        1  Remote       2  Files
  3  Shares        4  Sync         5  Settings
"""

import os
import sys
import itertools

from PyQt6.QtCore import Qt, QSize, QTimer
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QProgressBar, QPushButton, QCheckBox, QComboBox, QScrollArea,
    QSizePolicy, QSpinBox, QVBoxLayout, QWidget, QMessageBox,
    QTreeWidget, QTreeWidgetItem, QAbstractItemView, QHeaderView,
    QInputDialog, QMenu,
)

from .constants import (
    APP_NAME, APP_VERSION, HARDCODED_BASE_URL,
    DEFAULT_CHUNK_SIZE_MB, DEFAULT_MAX_CHUNKS, ORG_NAME,
)
from .logging_utils import write_debug_log
from .styles import STYLESHEET, build_stylesheet
from .workers import UploadWorker
from .dialogs import FolderBrowserDialog
from .updater import UpdateCheckWorker, UpdateDownloadWorker, launch_update_batch
from .remote_cache import cache, registry, CachePoller

from .ui import lucide_icon, CustomTitleBar, DropZone, FullWidthTabWidget
from .tabs import (
    FilesBrowserTab, RemoteTab, SharesTab, SyncTab,
    build_settings_tab, load_settings, save_settings,
)
from .theme import get_accent, accent_qcolor, get_font

# --- Mass upload section (inlined from tabs/mass_upload_tab.py) -----------------
class MassUploadSection(QWidget):

    _COL_NAME   = 0
    _COL_SIZE   = 1
    _COL_DEST   = 2
    _COL_STATUS = 3

    def __init__(self, get_api_key, get_mass_settings=None, get_debug=None,
                 on_upload_done=None, parent=None, embedded: bool = True):
        super().__init__(parent)
        self.get_api_key       = get_api_key
        self.get_mass_settings = get_mass_settings or (lambda: (1, DEFAULT_CHUNK_SIZE_MB, DEFAULT_MAX_CHUNKS))
        self.get_debug         = get_debug or (lambda: False)
        # on_upload_done(remote_dest: str) — called when each file finishes
        self._on_upload_done_cb = on_upload_done
        self._queue: list[dict] = []
        self._active_workers: list = []
        self._pending_iter    = iter([])
        self._cancelled       = False
        self._embedded        = embedded
        self._build_ui()

    def _build_ui(self):
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

        inner   = QWidget()
        outer   = QVBoxLayout(inner)
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

    def _build_drop_section(self, parent_lay: QVBoxLayout):
        from .ui.widgets import DropZone
        parent_lay.addWidget(self._sh("Multi-Upload"))

        add_card = self._card()
        add_lay  = QVBoxLayout(add_card)
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

    def _build_queue_table(self, parent_lay: QVBoxLayout):
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

    def _build_queue_toolbar(self, parent_lay: QVBoxLayout):
        qtb = QHBoxLayout()
        qtb.setSpacing(6)

        rm_btn = QPushButton("Remove selected")
        rm_btn.setObjectName("tb_btn_danger")
        rm_btn.clicked.connect(self._remove_selected)
        qtb.addWidget(rm_btn)

        from .theme import notifier, accent_qcolor
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
        self._queue_lbl.setStyleSheet(f"color: {accent_qcolor().name()}; font-size:{int(get_font()[1])}px; background:transparent;")
        qtb.addWidget(self._queue_lbl)
        parent_lay.addLayout(qtb)
        try:
            notifier().accent_changed.connect(lambda _old, _new: self._on_accent_changed(_old, _new))
        except Exception:
            pass

    def _on_accent_changed(self, old, new):
        try:
            from .theme import accent_qcolor
            self._queue_lbl.setStyleSheet(f"color: {accent_qcolor().name()}; font-size:{int(get_font()[1])}px; background:transparent;")
        except Exception:
            pass

    def _build_progress_card(self, parent_lay: QVBoxLayout):
        prog_card = self._card()
        prog_lay  = QVBoxLayout(prog_card)
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
        from .theme import accent_qcolor
        self._speed_lbl.setStyleSheet(f"color:{accent_qcolor().name()}; font-size:{int(get_font()[1])}px; background:transparent;")
        speed_row.addWidget(speed_lbl)
        speed_row.addWidget(self._speed_lbl)
        speed_row.addStretch()
        self._transferred_lbl = QLabel("")
        self._transferred_lbl.setStyleSheet(f"color:{accent_qcolor().name()}; font-size:{int(get_font()[1])}px; background:transparent;")
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

    def _build_action_buttons(self, parent_lay: QVBoxLayout):
        self._start_btn = QPushButton("  Start upload")
        self._start_btn.setObjectName("upload_btn")
        # Keep upload icon dark/black so it contrasts with accent backgrounds
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

    @staticmethod
    def _sh(text) -> QLabel:
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
        if n < 1024:      return f"{n} B"
        if n < 1024**2:   return f"{n/1024:.3f} KB"
        if n < 1024**3:   return f"{n/1024**2:.3f} MB"
        return f"{n/1024**3:.3f} GB"

    def _set_badge(self, text: str, color: str):
        self._badge_lbl.setText(f"● {text}")
        bg = {get_accent(): "#2a2215", "#4ade80": "#0f2318", "#f87171": "#2a0f0f", "#9ca3af": "#1e1c19"}
        bd = {get_accent(): "#4a3b1e", "#4ade80": "#1e4a30", "#f87171": "#4a1e1e", "#9ca3af": "#2e2b27"}
        self._badge_lbl.setStyleSheet(
            f"background-color:{bg.get(color,'#1e1c19')};"
            f"border:1px solid {bd.get(color,'#2e2b27')};"
            f"border-radius:10px; color:{color}; font-size:11px;"
            f"font-weight:600; padding:2px 10px;"
        )

    def _log(self, msg: str):
        if msg.startswith("[DEBUG]"):
            write_debug_log(msg)
            if not self.get_debug():
                return
        self._log_lbl.setText(msg)

    # ── Queue label + overall progress ───────────────────────────────────────

    def _update_queue_label(self):
        total  = len(self._queue)
        done   = sum(1 for e in self._queue if e.get("status") == "done")
        errors = sum(1 for e in self._queue if e.get("status") == "error")
        parts  = [f"{total} item{'s' if total != 1 else ''}"]
        if done:   parts.append(f"{done} done")
        if errors: parts.append(f"{errors} failed")
        self._queue_lbl.setText(" · ".join(parts))

    def _update_overall_progress(self):
        if not self._queue:
            return
        total_pct = sum(
            100.0 if e.get("status") in ("done", "error", "cancelled") else e.get("_pct", 0.0)
            for e in self._queue
        )
        pct = total_pct / len(self._queue)
        self._prog_bar.setValue(int(pct * 1000))
        self._pct_lbl.setText(f"{pct:.3f}%")

    def _on_file_progress(self, pct: int, entry: dict):
        if entry not in self._queue or entry.get("item") is None:
            return
        entry["_pct"] = float(pct)
        entry["item"].setText(self._COL_STATUS, f"{pct:.3f}%  ·  {entry.get('_xfr', '')}")
        self._update_overall_progress()

    # ── Queue management ───────────────────────────────────────────────────

    def _on_drop(self, file_list: list[str], root: str):
        new_entries = []
        for local in file_list:
            rel = os.path.relpath(local, root).replace(os.sep, "/")
            if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
                rel = os.path.basename(local)
            dest_base = "/" + (self._default_dest.text().strip("/") or "")
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
            item = QTreeWidgetItem([display_name, self._fmt(entry["size"]), rdest, "Pending"])
            from .theme import accent_qcolor
            item.setForeground(3, accent_qcolor())
            entry["item"] = item
            self._tree.addTopLevelItem(item)
            new_entries.append(entry)

        self._update_queue_label()

        # Feed new entries into a live iterator if uploads are already running
        if self._active_workers:
            pending_new = [e for e in new_entries if e["status"] == "pending"]
            self._pending_iter = itertools.chain(self._pending_iter, iter(pending_new))
            conc, _cm, _mc = self.get_mass_settings()
            for _ in range(max(0, conc - len(self._active_workers))):
                self._launch_next()

        # Offer a destination-folder picker for this batch
        api_key = self.get_api_key()
        if api_key:
            dlg = FolderBrowserDialog(
                api_key, HARDCODED_BASE_URL,
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
                            f"{chosen}/{rel_filename}" if chosen != "/" else f"/{rel_filename}"
                        )
                        entry["item"].setText(self._COL_DEST, entry["dest"])

    def _browse_default_dest(self):
        api_key = self.get_api_key()
        if not api_key:
            self._log("⚠ Enter API key in Settings first.")
            return
        dlg = FolderBrowserDialog(
            api_key, HARDCODED_BASE_URL,
            self._default_dest.text().strip() or "/",
            parent=self,
        )
        dlg.setWindowTitle("Choose default destination")
        if dlg.exec():
            write_debug_log(f"[MassUpload BrowseDest] dlg.selected={dlg.selected!r}")
            self._default_dest.setText(dlg.selected)
            write_debug_log(f"[MassUpload BrowseDest] _default_dest now={self._default_dest.text()!r}")

    def _edit_dest(self, item: QTreeWidgetItem, _col):
        row = next((e for e in self._queue if e.get("item") is item), None)
        if row is None or row.get("status") in ("uploading", "done"):
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
                filename    = row["dest"].rsplit("/", 1)[-1]
                folder      = dlg.selected.rstrip("/")
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
        item  = self._tree.itemAt(pos)
        menu  = QMenu(self)
        # prefer central stylesheet QMenu::icon rules; keep only small item padding here
        menu.setStyleSheet(
            "QMenu { background:#1f1f1f; border:1px solid #3a3a3a; border-radius:8px; color:#f0f0f0; font-size:12px; }"
            "QMenu::item { padding:6px 8px; }"
            "QMenu::item:selected { background:#332b1a; }"
        )
        if item:
            entry = next((e for e in self._queue if e.get("item") is item), None)
            idx   = self._tree.indexOfTopLevelItem(item)
            count = self._tree.topLevelItemCount()

            move_up = menu.addAction(lucide_icon("move", get_accent(), 12), "Move up")
            move_up.setEnabled(idx > 0 and entry and entry.get("status") == "pending")
            move_up.triggered.connect(self._move_selected_up)

            move_dn = menu.addAction(lucide_icon("move", get_accent(), 12), "Move down")
            move_dn.setEnabled(idx < count - 1 and entry and entry.get("status") == "pending")
            move_dn.triggered.connect(self._move_selected_down)

            menu.addSeparator()

            edit_dest = menu.addAction(lucide_icon("pencil", get_accent(), 12), "Edit destination")
            edit_dest.setEnabled(entry and entry.get("status") not in ("uploading", "done"))
            edit_dest.triggered.connect(lambda: self._edit_dest(item, 0))

            menu.addSeparator()

            rm = menu.addAction(lucide_icon("trash-2", "#f87171", 12), "Remove")
            rm.setEnabled(entry and entry.get("status") != "uploading")
            rm.triggered.connect(self._remove_selected)
        else:
            c1 = menu.addAction(lucide_icon("check", get_accent(), 12) if hasattr(lucide_icon, '__call__') else "", "Clear done")
            c1.triggered.connect(self._clear_done)
            c2 = menu.addAction(lucide_icon("x", "#f87171", 12), "Clear all")
            c2.triggered.connect(self._clear_all)

        menu.exec(self._tree.viewport().mapToGlobal(pos))

    # ── Row reordering ───────────────────────────────────────────────────

    def _move_entry(self, delta: int):
        selected = [
            e for e in self._queue
            if e.get("item") in self._tree.selectedItems() and e.get("status") == "pending"
        ]
        if not selected:
            return
        if delta > 0:
            selected = list(reversed(selected))
        for entry in selected:
            qi     = self._queue.index(entry)
            ti     = self._tree.indexOfTopLevelItem(entry.get("item"))
            new_qi = qi + delta
            new_ti = ti + delta
            if new_qi < 0 or new_qi >= len(self._queue):
                continue
            if new_ti < 0 or new_ti >= self._tree.topLevelItemCount():
                continue
            if (entry.get("status") == "uploading" or self._queue[new_qi].get("status") == "uploading"):
                continue
            self._queue[qi], self._queue[new_qi] = self._queue[new_qi], self._queue[qi]
            taken = self._tree.takeTopLevelItem(ti)
            self._tree.insertTopLevelItem(new_ti, taken)
            self._tree.setCurrentItem(taken)

    def _move_selected_up(self):   self._move_entry(-1)
    def _move_selected_down(self): self._move_entry(1)

    # ── Row removal ───────────────────────────────────────────────────────

    def _detach_entry(self, entry: dict):
        """Disconnect all worker signals and clear the item ref before removal."""
        w = entry.get("worker")
        if w is not None:
            for sig_name in ("progress", "speed", "status", "finished", "error", "bytes_progress"):
                sig = getattr(w, sig_name, None)
                if sig is not None:
                    try:    sig.disconnect()
                    except RuntimeError: pass
        entry["item"] = None

    def _remove_selected(self):
        for item in list(self._tree.selectedItems()):
            row = next((e for e in self._queue if e.get("item") is item), None)
            if row:
                w = row.get("worker")
                if w is not None:
                    w.cancel()
                    if w in self._active_workers:
                        self._active_workers.remove(w)
                    if row.get("status") == "uploading":
                        row["status"] = "cancelled"
                self._detach_entry(row)
                self._queue.remove(row)
            idx = self._tree.indexOfTopLevelItem(item)
            if idx >= 0:
                self._tree.takeTopLevelItem(idx)
        self._update_queue_label()

    def _clear_done(self):
        for entry in list(self._queue):
            if entry.get("status") in ("done", "error", "cancelled"):
                item = entry.get("item")          # save ref BEFORE detach nulls it
                self._detach_entry(entry)
                if item is not None:
                    idx = self._tree.indexOfTopLevelItem(item)
                    if idx >= 0:
                        self._tree.takeTopLevelItem(idx)
                self._queue.remove(entry)
        self._update_queue_label()

    def _clear_all(self):
        if self._active_workers:
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

    def _start(self):
        api_key = self.get_api_key()
        if not api_key:
            self._log("⚠ Enter API key in Settings first.")
            return
        pending = [e for e in self._queue if e.get("status") == "pending"]
        if not pending:
            self._log("⚠ No pending items in the queue.")
            return
        self._cancelled = False
        self._start_btn.hide()
        self._cancel_btn.show()
        self._set_badge("Uploading", get_accent())
        self._log(f"Starting {len(pending)} upload{'s' if len(pending) != 1 else ''}…")
        self._pending_iter = iter(pending)
        conc, _cm, _mc = self.get_mass_settings()
        total_slots = min(conc, len(pending))
        for slot in range(total_slots):
            if slot == 0:
                self._launch_next(api_key)
            else:
                QTimer.singleShot(slot * 1500, lambda k=api_key: self._launch_next(k))

    def _launch_next(self, api_key=None):
        if self._cancelled:
            return
        api_key = api_key or self.get_api_key()
        while True:
            try:
                entry = next(self._pending_iter)
            except StopIteration:
                return
            if entry not in self._queue:
                continue
            if entry.get("item") is None:
                continue
            break

        entry["status"]       = "uploading"
        entry["_bytes_done"]  = 0
        if not entry.get("_bytes_total"):
            entry["_bytes_total"] = entry.get("size", 0)
        entry["_xfr"] = f"0 B / {self._fmt(entry['_bytes_total'])}"
        entry["item"].setText(self._COL_STATUS, f"Uploading…  ·  {entry['_xfr']}")
        from .theme import accent_qcolor
        entry["item"].setForeground(3, accent_qcolor())

        w = UploadWorker(
            api_key, HARDCODED_BASE_URL,
            [(entry["local"], entry["dest"])],
            False, None, 0,
            chunk_size_mb=self.get_mass_settings()[1],
            max_chunks=self.get_mass_settings()[2],
        )
        entry["worker"] = w
        self._active_workers.append(w)
        w.progress.connect(lambda pct, e=entry: self._on_file_progress(pct, e))
        w.speed.connect(self._on_speed)
        w.status.connect(lambda msg, e=entry: self._log(msg))
        w.finished.connect(lambda result, e=entry: self._on_file_done(e))
        w.error.connect(lambda msg, e=entry: self._on_file_error(msg, e))
        if hasattr(w, "bytes_progress"):
            w.bytes_progress.connect(lambda done, total, e=entry: self._on_file_bytes(done, total, e))
        w.start()

    # ── Signal handlers ────────────────────────────────────────────────────

    def _on_speed(self, bps: float):
        if bps < 1024:       txt = f"{bps:.0f} B/s"
        elif bps < 1024**2:  txt = f"{bps/1024:.1f} KB/s"
        else:                txt = f"{bps/1024**2:.2f} MB/s"
        self._speed_lbl.setText(txt)

    def _on_file_bytes(self, done_bytes: int, total_bytes: int, entry: dict):
        if entry not in self._queue:
            return
        if entry.get("status") in ("done", "error", "cancelled"):
            return
        done_bytes  = int(done_bytes)
        total_bytes = int(total_bytes)
        if total_bytes > 0:
            entry["_bytes_total"] = total_bytes
        done_bytes = min(done_bytes, entry.get("_bytes_total"))
        entry["_bytes_done"] = done_bytes
        entry["_xfr"] = f"{self._fmt(done_bytes)} / {self._fmt(entry['_bytes_total'])}"

        all_done  = sum(e.get("_bytes_done",  0) for e in self._queue)
        all_total = sum(e.get("_bytes_total", 0) for e in self._queue)
        if all_total > 0:
            self._transferred_lbl.setText(f"{self._fmt(all_done)} / {self._fmt(all_total)}")

    def _on_file_done(self, entry: dict):
        entry["status"]      = "done"
        entry["_bytes_done"] = entry.get("_bytes_total", entry.get("size", 0))
        if entry in self._queue and entry.get("item") is not None:
            entry["item"].setText(self._COL_STATUS, "✓ Done")
            entry["item"].setForeground(3, QColor("#4ade80"))
        if entry.get("worker") in self._active_workers:
            self._active_workers.remove(entry.get("worker"))
        if self._on_upload_done_cb is not None:
            dest = entry.get("dest", "")
            if dest:
                folder = "/".join(dest.rstrip("/").split("/")[:-1]) or "/"
                self._on_upload_done_cb(folder)
        self._update_queue_label()
        self._update_overall_progress()
        self._launch_next()
        self._check_all_done()

    def _on_file_error(self, msg: str, entry: dict):
        entry["status"] = "error"
        if entry in self._queue and entry.get("item") is not None:
            entry["item"].setText(self._COL_STATUS, "✗ Failed")
            entry["item"].setForeground(3, QColor("#f87171"))
            entry["item"].setToolTip(self._COL_STATUS, msg)
        if entry.get("worker") in self._active_workers:
            self._active_workers.remove(entry.get("worker"))
        self._log(f"✗ {os.path.basename(entry['local'])}: {msg}")
        self._update_queue_label()
        self._update_overall_progress()
        self._launch_next()
        self._check_all_done()

    def _check_all_done(self):
        if self._active_workers:
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

    def _cancel(self):
        self._cancelled = True
        for w in list(self._active_workers):
            try:
                w.cancel()
                for sig_name in ("progress", "speed", "status", "finished", "error"):
                    getattr(w, sig_name).disconnect()
                if hasattr(w, "bytes_progress"):
                    try:    w.bytes_progress.disconnect()
                    except RuntimeError: pass
            except RuntimeError:
                pass
        self._active_workers.clear()
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

    # For brevity the remaining methods (queue management, upload engine,
    # signal handlers) are implemented by delegating to the logic in the
    # original module via UploadWorker; this class mirrors that behaviour.


class MochaTools(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Mocha Tools")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setMinimumWidth(520)
        self.setMaximumWidth(640)

        self.selected_files: list[str] = []
        self.selected_root:  str       = ""
        self.worker                    = None
        self._poller: CachePoller | None = None

        # Update worker state
        self._update_tag:       str                      = ""
        self._update_url:       str                      = ""
        self._update_dl_worker: UpdateDownloadWorker | None = None

        self._build_ui()
        load_settings(self)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)

        root_lay = QVBoxLayout(root)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        self.titlebar = CustomTitleBar(self, APP_NAME, APP_VERSION)
        root_lay.addWidget(self.titlebar)

        self.tabs = FullWidthTabWidget()
        root_lay.addWidget(self.tabs)

        # Build each tab
        upload_tab   = self._build_upload_tab()
        settings_tab = build_settings_tab(self)   # attaches spinboxes etc. to self

        # mass upload section will be created after settings (so spinboxes exist)
        self.files_tab = FilesBrowserTab(
            get_api_key=lambda: self.api_key_edit.text().strip(),
            get_upload_path=lambda: self.upload_path_edit.text().strip(),
            set_upload_path=lambda p: self.upload_path_edit.setText(p),
        )
        self.remote_tab = RemoteTab(
            get_api_key=lambda: self.api_key_edit.text().strip(),
            on_ingest_done=self._on_upload_done,
            on_share_created=self._on_share_created,
        )
        self.shares_tab = SharesTab(
            get_api_key=lambda: self.api_key_edit.text().strip(),
        )
        self.sync_tab = SyncTab(
            get_api_key=lambda: self.api_key_edit.text().strip(),
            get_sync_settings=lambda: (
                self.sync_conc_spin.value(),
                self.sync_chunk_spin.value(),
                self.sync_maxchunk_spin.value(),
            ),
            get_debug=lambda: self.debug_cb.isChecked(),
        )

        # Create and attach mass upload section now that settings/spinboxes
        # have been created and attached to self.
        self.mass_upload_section = MassUploadSection(
            get_api_key=lambda: self.api_key_edit.text().strip(),
            get_mass_settings=lambda: (
                self.mass_conc_spin.value(),
                self.mass_chunk_spin.value(),
                self.mass_maxchunk_spin.value(),
            ),
            get_debug=lambda: self.debug_cb.isChecked(),
            on_upload_done=self._on_upload_done,
            embedded=True,
        )
        # Attach into the Upload tab's main layout (stored by _build_upload_tab)
        try:
            self._upload_main_layout.addWidget(self.mass_upload_section)
        except Exception:
            upload_tab.layout().addWidget(self.mass_upload_section)

        # Add tabs in order
        self.tabs.addTab(upload_tab,      "Upload")
        self.tabs.addTab(self.remote_tab,  "Remote")
        self.tabs.addTab(self.files_tab,   "Files")
        self.tabs.addTab(self.shares_tab,  "Shares")
        self.tabs.addTab(self.sync_tab,    "Sync")
        self.tabs.addTab(settings_tab,     "Settings")

        # ── Remote cache poller ───────────────────────────────────────────────
        # Created here so it can be passed to tabs that need to dynamically
        # add paths (e.g. when the user navigates to a new folder).
        self._poller = CachePoller(self)
        self._poller.add("shares", lambda: self.api_key_edit.text().strip(),
                         HARDCODED_BASE_URL)
        self._poller.add("list",   lambda: self.api_key_edit.text().strip(),
                         HARDCODED_BASE_URL, path="/")
        # Give both tabs a reference to the poller so they can add paths on
        # the fly and subscribe their callbacks
        self.files_tab.attach_cache_poller(self._poller)
        self.shares_tab.attach_cache_poller(self._poller)

        _tab_icons = [
            ("upload",         get_accent()),
            ("download-cloud", get_accent()),
            ("folder",         get_accent()),
            ("share-2",        get_accent()),
            ("refresh-cw",     get_accent()),
            ("settings",       get_accent()),
        ]
        for i, (icon_name, color) in enumerate(_tab_icons):
            self.tabs.setTabIcon(i, lucide_icon(icon_name, color, 14))
        self.tabs.setIconSize(QSize(14, 14))
        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _build_upload_tab(self) -> QWidget:
        """Build the single-file Upload tab and return it as a QWidget."""
        upload_tab = QWidget()
        scroll     = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        main  = QVBoxLayout(inner)
        main.setContentsMargins(16, 16, 16, 20)
        main.setSpacing(12)
        scroll.setWidget(inner)

        tab_lay = QVBoxLayout(upload_tab)
        tab_lay.setContentsMargins(0, 0, 0, 0)
        tab_lay.addWidget(scroll)
        # keep reference to the main inner layout so other code can attach
        # widgets into the Upload tab's content area later
        self._upload_main_layout = main

        # FILE section
        main.addWidget(self._sh("File"))
        file_card = self._card()
        file_lay  = QVBoxLayout(file_card)
        self.drop_zone = DropZone()
        self.drop_zone.selection_changed.connect(self._on_files_selected)
        file_lay.addWidget(self.drop_zone)
        main.addWidget(file_card)

        # DESTINATION section
        main.addWidget(self._sh("Destination"))
        dest_card = self._card()
        dest_lay  = QVBoxLayout(dest_card)
        dest_lay.setSpacing(8)

        dest_row = QHBoxLayout()
        dest_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        dest_lbl = QLabel("Folder")
        dest_lbl.setObjectName("field_label")

        # upload_path_edit is created by build_settings_tab() later, so we
        # create it here first so the upload tab can reference it immediately.
        # build_settings_tab will assign the same attribute, which is fine.
        self.upload_path_edit = QLineEdit("/")
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

        # UPLOAD STATUS section
        main.addWidget(self._sh("Upload"))
        status_card = self._card()
        status_lay  = QVBoxLayout(status_card)
        status_lay.setSpacing(8)

        top_row = QHBoxLayout()
        self.status_badge = QLabel("● Idle")
        self.status_badge.setObjectName("status_badge")
        top_row.addWidget(self.status_badge)
        top_row.addStretch()
        status_lay.addLayout(top_row)

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

        prog_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximum(100_000)
        self.progress_bar.setValue(0)
        self.pct_label = QLabel("0.000%")
        self.pct_label.setObjectName("status_label")
        self.pct_label.setFixedWidth(58)
        prog_row.addWidget(self.progress_bar, 1)
        prog_row.addWidget(self.pct_label)
        status_lay.addLayout(prog_row)

        self.log_label = QLabel("Ready — select a file and destination folder, then upload.")
        self.log_label.setObjectName("log_console")
        self.log_label.setWordWrap(True)
        self.log_label.setMinimumHeight(46)
        self.log_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.log_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        status_lay.addWidget(self.log_label)

        self._share_result_url = ""
        share_result_row = QHBoxLayout()
        share_result_row.setContentsMargins(0, 0, 0, 0)
        share_result_row.setSpacing(8)
        self.share_result = QLabel("")
        self.share_result.setObjectName("log_console")
        self.share_result.setWordWrap(True)
        self.share_result.setOpenExternalLinks(True)
        self.copy_share_result_btn = QPushButton("Copy link")
        self.copy_share_result_btn.setFixedHeight(36)
        self.copy_share_result_btn.setStyleSheet(
            "min-height:0px; padding:0px 16px; font-size:13px; font-weight:600;"
            "background:#1e1c19; color:#f0ece6; border:1px solid #3d3a35; border-radius:7px;"
        )
        self.copy_share_result_btn.clicked.connect(self._copy_share_result)
        share_result_row.addWidget(self.share_result, 1)
        share_result_row.addWidget(self.copy_share_result_btn)
        self._share_result_widget = QWidget()
        self._share_result_widget.setLayout(share_result_row)
        self._share_result_widget.hide()
        status_lay.addWidget(self._share_result_widget)
        main.addWidget(status_card)

        # SHARE OPTIONS section
        share_card = self._card()
        share_lay  = QVBoxLayout(share_card)
        share_lay.setSpacing(10)

        self.create_share_cb = QCheckBox("Create share link after upload")
        share_lay.addWidget(self.create_share_cb)
        self.create_share_cb.toggled.connect(self._toggle_share_options)

        self.share_opts_widget = QWidget()
        share_opts_lay = QVBoxLayout(self.share_opts_widget)
        share_opts_lay.setContentsMargins(0, 4, 0, 0)
        share_opts_lay.setSpacing(8)

        exp_row = QHBoxLayout()
        exp_lbl = QLabel("Expiration")
        exp_lbl.setObjectName("field_label")
        self.expiry_combo = QComboBox()
        self._expiry_map = [
            ("Never",    None), ("1 hour",  1),  ("6 hours",  6),
            ("12 hours", 12),   ("1 day",   24), ("3 days",   72),
            ("7 days",   168),  ("14 days", 336),("30 days",  720),
        ]
        self.expiry_combo.addItems([label for label, _ in self._expiry_map])
        exp_row.addWidget(exp_lbl)
        exp_row.addWidget(self.expiry_combo, 1)
        share_opts_lay.addLayout(exp_row)

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

        # UPLOAD BUTTON
        self.upload_btn = QPushButton("  Upload file")
        self.upload_btn.setObjectName("upload_btn")
        # Keep upload icon dark/black so it contrasts with accent backgrounds
        self.upload_btn.setIcon(lucide_icon("upload", "#111010", 15))
        self.upload_btn.setIconSize(QSize(15, 15))
        self.upload_btn.setMinimumHeight(42)
        self.upload_btn.clicked.connect(self._start_upload)
        main.addWidget(self.upload_btn)

        self.cancel_btn = QPushButton("  Cancel")
        self.cancel_btn.setObjectName("browse_btn")
        self.cancel_btn.setIcon(lucide_icon("x", get_accent(), 13))
        self.cancel_btn.setIconSize(QSize(13, 13))
        self.cancel_btn.setMinimumHeight(36)
        self.cancel_btn.clicked.connect(self._cancel_upload)
        self.cancel_btn.hide()
        main.addWidget(self.cancel_btn)
        main.addStretch()

        return upload_tab

    # ── Widget helpers ────────────────────────────────────────────────────────

    def _sh(self, text: str) -> QLabel:
        lbl = QLabel(text.upper())
        lbl.setObjectName("section_header")
        return lbl

    def _card(self) -> QFrame:
        f = QFrame()
        f.setObjectName("card")
        return f

    # ── Settings passthrough ──────────────────────────────────────────────────

    def _load_settings(self):
        load_settings(self)

    def _save_settings(self):
        save_settings(self)

    # ── Upload tab helpers ────────────────────────────────────────────────────

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
            from .logging_utils import write_debug_log
            write_debug_log(f"[BrowseDest] dlg.selected={dlg.selected!r}")
            self.upload_path_edit.setText(dlg.selected)
            write_debug_log(f"[BrowseDest] upload_path_edit now={self.upload_path_edit.text()!r}")

    def _toggle_key_visibility(self, checked: bool):
        mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        self.api_key_edit.setEchoMode(mode)

    def _toggle_share_options(self, checked: bool):
        self.share_opts_widget.setVisible(checked)

    def _on_files_selected(self, file_list: list[str], root: str):
        self.selected_files = file_list
        self.selected_root  = root
        if len(file_list) == 1:
            self._log(f"[DEBUG] Selected: {os.path.basename(file_list[0])}")
        else:
            self._log(f"[DEBUG] Selected folder: {len(file_list)} files")
        self._share_result_widget.hide()

    # ── Upload flow ───────────────────────────────────────────────────────────

    def _start_upload(self):
        api_key     = self.api_key_edit.text().strip()
        upload_path = self.upload_path_edit.text().strip() or "/"

        if not api_key:
            self._log("⚠ Please enter an API key.")
            return
        if not self.selected_files:
            self._log("⚠ Please select a file or folder.")
            return

        save_settings(self)
        self._set_uploading(True)
        self._share_result_widget.hide()
        self.progress_bar.setValue(0)
        self.pct_label.setText("0.000%")
        self.speed_label.setText("")
        self.transferred_label.setText("")
        self._badge("Uploading", get_accent())

        expiry_hours = self._expiry_map[self.expiry_combo.currentIndex()][1] \
            if self.create_share_cb.isChecked() else None
        max_dl = self.max_dl_spin.value() if self.create_share_cb.isChecked() else 0

        base_remote = "/" + upload_path.strip("/")
        file_pairs: list[tuple[str, str]] = []
        for local in self.selected_files:
            rel = os.path.relpath(local, self.selected_root).replace(os.sep, "/")
            if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
                rel = os.path.basename(local)
            dest = f"{base_remote}/{rel}" if base_remote != "/" else f"/{rel}"
            file_pairs.append((local, dest))
        # Ensure the upload path textbox always shows with a trailing slash
        self.upload_path_edit.setText(base_remote + "/")

        self._log(f"[DEBUG] Upload path: {upload_path!r} → base_remote: {base_remote!r}")
        for local, dest in file_pairs[:3]:
            self._log(f"[DEBUG] Dest: {dest}")

        self._upload_grand_total = sum(
            os.path.getsize(lp) for lp, _ in file_pairs if os.path.isfile(lp)
        )

        self.worker = UploadWorker(
            api_key, HARDCODED_BASE_URL, file_pairs,
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
            try:
                self.worker.progress.disconnect()
                self.worker.speed.disconnect()
                self.worker.status.disconnect()
                self.worker.finished.disconnect()
                self.worker.error.disconnect()
                if hasattr(self.worker, "bytes_progress"):
                    try:    self.worker.bytes_progress.disconnect()
                    except RuntimeError: pass
            except RuntimeError:
                pass
        self._set_uploading(False)
        self._badge("Cancelled", "#9ca3af")
        self.progress_bar.setValue(0)
        self.pct_label.setText("0.000%")
        self.speed_label.setText("")
        self.transferred_label.setText("")
        self._share_result_widget.hide()
        self._log("Upload cancelled by user.")

    def _set_uploading(self, active: bool):
        self.upload_btn.setVisible(not active)
        self.cancel_btn.setVisible(active)
        self.upload_btn.setEnabled(not active)

    # ── Upload signal handlers ────────────────────────────────────────────────

    def _on_progress(self, pct: float):
        self.progress_bar.setValue(int(pct * 1000))
        self.pct_label.setText(f"{pct:.3f}%")

    def _on_bytes_progress(self, done_bytes: int, total_bytes: int):
        grand = getattr(self, "_upload_grand_total", 0) or total_bytes
        self.transferred_label.setText(f"{self._fmt(done_bytes)} / {self._fmt(grand)}")

    def _on_speed(self, bps: float):
        if bps < 1024:      txt = f"{bps:.3f} B/s"
        elif bps < 1024**2: txt = f"{bps/1024:.3f} KB/s"
        else:               txt = f"{bps/1024**2:.3f} MB/s"
        self.speed_label.setText(txt)

    def _on_finished(self, result: dict):
        self._set_uploading(False)
        self._badge("Complete", "#4ade80")
        self.transferred_label.setText("")
        self._log(f"✓ Done! File ID: {result['file_id']}")
        # Invalidate file-list cache for the destination folder
        upload_path = self.upload_path_edit.text().strip() or "/"
        self._on_upload_done(upload_path)
        if result.get("share_url"):
            url = result["share_url"]
            self._share_result_url = url
            from .theme import get_accent
            self.share_result.setText(f'<a href="{url}" style="color:{get_accent()};">{url}</a>')
            self._share_result_widget.show()
            # A new share was created — invalidate shares cache
            self._on_share_created()

    def _on_error(self, msg: str):
        self._set_uploading(False)
        self._badge("Error", "#f87171")
        self.transferred_label.setText("")
        self._log(f"✗ Error: {msg}")

    # ── Cache invalidation helpers ────────────────────────────────────────────

    def _on_upload_done(self, remote_folder: str):
        """
        Called when any upload finishes (single-file tab or mass upload tab).
        Invalidates the file-list cache for the destination folder and triggers
        a background refresh so the Files tab stays current.
        """
        if not self._poller:
            return
        # Normalise: if remote_folder looks like a file path, take the parent
        folder = remote_folder.rstrip("/")
        import os as _os
        if "." in _os.path.basename(folder):
            folder = "/".join(folder.split("/")[:-1]) or "/"
        folder = folder or "/"

        from .remote_cache import cache as _cache
        _cache.invalidate("list", path=folder)
        self._poller.add("list", lambda: self.api_key_edit.text().strip(),
                         HARDCODED_BASE_URL, path=folder)
        self._poller.force_refresh("list", path=folder)

        # Notify the Files tab so it can re-render if it's showing this folder
        self.files_tab.notify_upload_done(folder)

    def _on_share_created(self):
        """
        Called whenever a new share link is created (upload tab, files tab,
        or remote ingest tab).  Invalidates the shares cache and triggers a
        background refresh so the Shares tab reflects the new share instantly.
        """
        if not self._poller:
            return
        from .remote_cache import cache as _cache
        _cache.invalidate_op("shares")
        self._poller.force_refresh("shares")

    def _copy_share_result(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self._share_result_url)
        self.copy_share_result_btn.setText("Copied!")
        QTimer.singleShot(1500, lambda: self.copy_share_result_btn.setText("Copy link"))

    # ── Status helpers ────────────────────────────────────────────────────────

    def _log(self, msg: str):
        debug_enabled = getattr(self, "debug_cb", None) and self.debug_cb.isChecked()
        if msg.startswith("[DEBUG]") and not debug_enabled:
            return
        self.log_label.setText(msg)
        if debug_enabled:
            write_debug_log(msg)

    def _badge(self, text: str, color: str):
        from .theme import get_accent, DEFAULT_ACCENT
        self.status_badge.setText(f"● {text}")
        # resolve dynamic default token
        if color == DEFAULT_ACCENT:
            color = get_accent()
        bg_map = {"#c8a96e": "#2a2215", "#4ade80": "#0f2318",
                  "#f87171": "#2a0f0f", "#9ca3af": "#1e1c19"}
        bd_map = {"#c8a96e": "#4a3b1e", "#4ade80": "#1e4a30",
                  "#f87171": "#4a1e1e", "#9caaf": "#2e2b27"}
        bg = bg_map.get(color, "#1e1c19")
        bd = bd_map.get(color, "#2e2b27")
        self.status_badge.setStyleSheet(
            f"background-color: {bg}; border: 1px solid {bd}; "
            f"border-radius: 10px; color: {color}; font-size: 11px; "
            f"font-weight: 600; padding: 2px 10px;"
        )

    @staticmethod
    def _fmt(n: int) -> str:
        if n < 1024:      return f"{n} B"
        if n < 1024**2:   return f"{n/1024:.3f} KB"
        if n < 1024**3:   return f"{n/1024**2:.3f} MB"
        return f"{n/1024**3:.3f} GB"

    # ── Tab switching ─────────────────────────────────────────────────────────

    def _on_tab_changed(self, index: int):
        # 0=Upload, 1=Remote, 2=Files, 3=Shares, 4=Sync, 5=Settings
        self.remote_tab.set_active(index == 1)

        api_key = self.api_key_edit.text().strip()
        if not api_key:
            return

        if index in (2, 3) and self._poller:
            # Ensure poller is running whenever Files or Shares tab is visible
            self._poller.start()

        if index == 2:
            self.files_tab._navigate(self.files_tab.current_path)
        elif index == 3:
            # Poller is already running after start() above; serve stale cache
            # instantly if available, fresh data arrives via subscription.
            stale = cache.get("shares")
            if stale is not None:
                self.shares_tab._cache = stale
                self.shares_tab._render(stale)
                self.shares_tab._status("Refreshing…")
        elif index != 2 and index != 6:
            save_settings(self)

    # ── Auto-update ───────────────────────────────────────────────────────────

    def _check_for_updates(self, silent: bool = False):
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
        self.update_status_lbl.setText(f"Update available: {tag}  (current: {APP_VERSION})")
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
            QMessageBox.information(self, "Up to date",
                                    f"Mocha Tools {APP_VERSION} is the latest version.")

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

        w = UpdateDownloadWorker(self._update_url, self._update_tag)
        w.progress.connect(self.update_progress.setValue)
        w.status.connect(self.update_status_lbl.setText)
        w.done.connect(self._on_update_done)
        w.ready_to_restart.connect(self._on_update_ready_to_restart)
        w.error.connect(self._on_update_dl_error)
        w.start()
        self._update_dl_worker = w
        self._update_bat_path: str = ""

    def _on_update_ready_to_restart(self, bat_path: str):
        self._update_bat_path = bat_path
        self.update_progress.setValue(100)
        self.install_update_btn.hide()
        result = QMessageBox.question(
            self, "Restart required",
            f"Mocha Tools {self._update_tag} has been installed.\n\nRestart now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if result == QMessageBox.StandardButton.Yes:
            self.update_status_lbl.setText("Restarting…")
            launch_update_batch(self._update_bat_path)
            # Quit cleanly so the update script doesn't need to force-kill us.
            QApplication.quit()

    def _on_update_done(self):
        self.update_progress.setValue(100)
        self.install_update_btn.hide()
        QMessageBox.information(
            self, "Update installed",
            f"Mocha Tools {self._update_tag} has been installed.\n\n"
            "Please restart the application to apply the update.",
        )

    def _on_update_dl_error(self, msg: str):
        self.update_progress.hide()
        self.install_update_btn.setEnabled(True)
        self.update_status_lbl.setText(f"Download failed: {msg}")
        QMessageBox.warning(self, "Update failed", msg)

    # ── Test-update helper (--test-update flag only) ──────────────────────────

    def _trigger_test_update(self):
        """
        Fetch the latest GitHub release and immediately download+install it,
        skipping the version comparison.  Invoked only via --test-update.
        Navigates to Settings so progress is visible.
        """
        import requests as _req
        from .constants import UPDATE_CHECK_URL
        from .updater import _asset_name

        self.tabs.setCurrentIndex(6)          # jump to Settings tab
        self.update_status_lbl.setText("Test mode: fetching latest release info…")
        self.update_progress.setValue(0)
        self.update_progress.show()
        self.check_update_btn.setEnabled(False)
        self.install_update_btn.hide()

        def _fetch():
            try:
                resp = _req.get(
                    UPDATE_CHECK_URL,
                    headers={"Accept": "application/vnd.github+json"},
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                self.update_status_lbl.setText(f"Test-update fetch failed: {exc}")
                self.check_update_btn.setEnabled(True)
                return

            tag    = data.get("tag_name", "")
            assets = data.get("assets",   [])

            if not tag:
                self.update_status_lbl.setText("Test-update: release has no tag_name.")
                self.check_update_btn.setEnabled(True)
                return

            try:
                want = _asset_name(tag)
            except ValueError as exc:
                self.update_status_lbl.setText(f"Test-update asset name error: {exc}")
                self.check_update_btn.setEnabled(True)
                return

            url = next(
                (a["browser_download_url"] for a in assets if a["name"] == want),
                "",
            )
            if not url:
                self.update_status_lbl.setText(
                    f"Test-update: no asset '{want}' found in release {tag}.\n"
                    "Check that the build for this platform uploaded successfully."
                )
                self.check_update_btn.setEnabled(True)
                return

            self.update_status_lbl.setText(
                f"Test mode: installing {tag} ({want}) - version check skipped"
            )
            self._update_tag = tag
            self._update_url = url

            w = UpdateDownloadWorker(url, tag)
            w.progress.connect(self.update_progress.setValue)
            w.status.connect(self.update_status_lbl.setText)
            w.done.connect(self._on_update_done)
            w.ready_to_restart.connect(self._on_update_ready_to_restart)
            w.error.connect(self._on_update_dl_error)
            w.start()
            self._update_dl_worker = w

        # Run the blocking requests call on a plain QThread so the UI stays alive
        from PyQt6.QtCore import QThread
        class _FetchThread(QThread):
            def run(self_):
                _fetch()

        self._test_fetch_thread = _FetchThread(self)
        self._test_fetch_thread.start()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        save_settings(self)
        self.remote_tab.set_active(False)
        self.sync_tab.closeEvent(event)
        if self._poller:
            self._poller.stop()
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
    # Apply the stylesheet built from the persisted accent so runtime
    # appearance matches saved user preference.
    try:
        app.setStyleSheet(build_stylesheet(get_accent()))
    except Exception:
        app.setStyleSheet(STYLESHEET)

    # Apply saved font
    try:
        from .theme import get_font
        from PyQt6.QtGui import QFont
        fam, sz = get_font()
        if fam:
            app.setFont(QFont(fam, int(sz)))
    except Exception:
        pass

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window,          QColor("#111010"))
    palette.setColor(QPalette.ColorRole.WindowText,      QColor("#f0ece6"))
    palette.setColor(QPalette.ColorRole.Base,            QColor("#141210"))
    palette.setColor(QPalette.ColorRole.Text,            QColor("#f0ece6"))
    palette.setColor(QPalette.ColorRole.Button,          QColor("#1e1c19"))
    palette.setColor(QPalette.ColorRole.ButtonText,      QColor("#f0ece6"))
    palette.setColor(QPalette.ColorRole.Highlight,       accent_qcolor())
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#111010"))
    app.setPalette(palette)

    test_update = "--test-update" in sys.argv

    win = MochaTools()
    win.show()

    # Helper to refresh certain icons that were created earlier with the
    # accent color. This allows live theme changes to update icons already
    # attached to widgets.
    def _refresh_accented_icons():
        try:
            from .theme import get_accent
            from .ui import lucide_icon
            # common buttons on the main window
            if hasattr(win, 'upload_btn'):
                # keep the upload icon black so it always contrasts with accent
                win.upload_btn.setIcon(lucide_icon('upload', '#111010', 15))
                win.upload_btn.setIconSize(QSize(15, 15))
            if hasattr(win, 'cancel_btn'):
                win.cancel_btn.setIcon(lucide_icon('x', get_accent(), 13))
                win.cancel_btn.setIconSize(QSize(13, 13))
            # mass upload section start button (embedded section)
            try:
                if hasattr(win, 'mass_upload_section') and hasattr(win.mass_upload_section, '_start_btn'):
                    # mass upload start icon also stays dark
                    win.mass_upload_section._start_btn.setIcon(lucide_icon('upload', '#111010', 15))
                    win.mass_upload_section._start_btn.setIconSize(QSize(15, 15))
            except Exception:
                pass
            # refresh titlebar icons if present
            try:
                if hasattr(win, 'titlebar') and hasattr(win.titlebar, '_refresh_icons'):
                    win.titlebar._refresh_icons()
            except Exception:
                pass
            # refresh install update button background if present
            try:
                if hasattr(win, 'install_update_btn'):
                    from .theme import get_accent
                    acc = get_accent()
                    win.install_update_btn.setStyleSheet(
                        f"min-height:0px; padding:0px 16px; font-size:13px; font-weight:700;"
                        f"background:{acc}; color:#111010; border:none; border-radius:7px;"
                    )
            except Exception:
                pass
            # refresh tab icons (use same mapping as initial creation)
            try:
                _tab_icons = [
                    ("upload",         get_accent()),
                    ("download-cloud", get_accent()),
                    ("folder",         get_accent()),
                    ("share-2",        get_accent()),
                    ("refresh-cw",     get_accent()),
                    ("settings",       get_accent()),
                ]
                if hasattr(win, 'tabs'):
                    for i, (icon_name, color) in enumerate(_tab_icons):
                        try:
                            win.tabs.setTabIcon(i, lucide_icon(icon_name, color, 14))
                        except Exception:
                            pass
            except Exception:
                pass
        except Exception:
            pass

    # expose helper for other modules (settings tab will call it)
    win._refresh_accented_icons = _refresh_accented_icons

    # When the accent setting changes emit a global refresh: reapply stylesheet
    # and refresh common icons. Theme.notifier() is updated by settings.
    try:
        from .theme import notifier, get_accent
        from .styles import build_stylesheet

        def _on_accent_changed(old_hx: str, hx: str):
            try:
                a = QApplication.instance()
                if a:
                    a.setStyleSheet(build_stylesheet(hx))
                    pal = a.palette()
                    from .theme import accent_qcolor
                    pal.setColor(QPalette.ColorRole.Highlight, accent_qcolor())
                    a.setPalette(pal)
                    # refresh main window icons
                    try:
                        if hasattr(win, '_refresh_accented_icons'):
                            win._refresh_accented_icons()
                    except Exception:
                        pass
                    # Ensure titlebar and other widgets update for font changes too
                    try:
                        from .theme import get_font
                        fam, sz = get_font()
                        if fam:
                            from PyQt6.QtGui import QFont
                            a = QApplication.instance()
                            if a:
                                a.setFont(QFont(fam, int(sz)))
                    except Exception:
                        pass
            except Exception:
                pass

        notifier().accent_changed.connect(_on_accent_changed)
        # Also invoke once on startup so widgets created before the notifier
        # was connected get the same treatment as pressing Apply in Settings.
        try:
            _on_accent_changed(None, get_accent())
        except Exception:
            pass
        # Listen for font changes and apply them globally
        try:
            from .theme import notifier as _notifier
            def _on_font_change(fam, sz):
                try:
                    from PyQt6.QtGui import QFont
                    a = QApplication.instance()
                    if a:
                        a.setFont(QFont(fam, int(sz)))
                        a.processEvents()
                        # re-polish top-level windows so QSS-applied fonts update
                        widgets = a.topLevelWidgets()
                        for w in widgets:
                            try:
                                a.style().unpolish(w)
                            except Exception:
                                pass
                            try:
                                a.style().polish(w)
                            except Exception:
                                pass
                        # Reapply global stylesheet so any QSS tokens referencing
                        # the font family/size are regenerated from the current
                        # theme.get_font() values.
                        try:
                            a.setStyleSheet(build_stylesheet(get_accent()))
                        except Exception:
                            pass
                except Exception:
                    pass
            _notifier().font_changed.connect(_on_font_change)
            try:
                f, s = get_font()
                _on_font_change(f, s)
            except Exception:
                pass
        except Exception:
            pass
    except Exception:
        pass

    def _preload():
        if win.api_key_edit.text().strip():
            # poller.start() immediately polls all registered slots (list /, shares)
            # and delivers results via cache subscriptions — no extra workers needed.
            win._poller.start()

    QTimer.singleShot(300, _preload)

    if test_update:
        # Skip the normal silent update check; immediately download+install
        # the latest release regardless of version — for testing the updater.
        QTimer.singleShot(500, win._trigger_test_update)
    else:
        QTimer.singleShot(2000, lambda: win._check_for_updates(silent=True))

    sys.exit(app.exec())


if __name__ == "__main__":
    main()