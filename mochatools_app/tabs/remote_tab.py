"""
tabs/remote_tab.py — Server-side remote download / ingest tab for MochaTools.

Starts server-side remote downloads and displays transfer jobs.
"""

import os
from urllib.parse import urlparse, unquote

from PyQt6.QtCore import Qt, QSize, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget, QAbstractScrollArea, QSizePolicy,
)

from ..constants import HARDCODED_BASE_URL
from ..dialogs import FolderBrowserDialog
from ..workers import RemoteWorker
from ..ui.icons import lucide_icon


class RemoteTab(QWidget):
    """Starts server-side remote downloads and displays transfer jobs."""

    def __init__(self, get_api_key, on_ingest_done=None, on_share_created=None, parent=None):
        super().__init__(parent)
        self.get_api_key      = get_api_key
        self.base_url         = HARDCODED_BASE_URL
        self._workers         = []
        self._is_active       = False
        self._watched_jobs    = {}
        # Callbacks wired by app.py for cache invalidation
        self._on_ingest_done_cb    = on_ingest_done   # (dest_folder: str) -> None
        self._on_share_created_cb  = on_share_created # () -> None
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Ingest card lives inside a scroll area — same pattern as the Upload tab
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        self._inner_lay = QVBoxLayout(inner)
        self._inner_lay.setContentsMargins(16, 16, 16, 16)
        self._inner_lay.setSpacing(10)
        scroll.setWidget(inner)

        self._build_ingest_card(self._inner_lay)
        self._inner_lay.addStretch()

        # Put the toolbar and jobs tree inside the same scroll area so the
        # whole remote tab content scrolls as a single region.
        # Add a small separator spacing and then build the jobs UI into the
        # same inner layout used by the QScrollArea.
        sep = QWidget()
        sep.setFixedHeight(6)
        self._inner_lay.addWidget(sep)
        # toolbar
        self._build_jobs_toolbar(self._inner_lay)
        # tree
        self._build_jobs_tree(self._inner_lay)

        outer.addWidget(scroll)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(5000)
        self.refresh_timer.timeout.connect(self.refresh_jobs)

    def _build_ingest_card(self, parent_lay: QVBoxLayout):
        card = self._make_card()
        lay  = QVBoxLayout(card)
        lay.setSpacing(8)

        url_row = QHBoxLayout()
        url_lbl = QLabel("URL")
        url_lbl.setObjectName("field_label")
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://example.com/big-file.zip")
        url_row.addWidget(url_lbl)
        url_row.addWidget(self.url_edit, 1)
        lay.addLayout(url_row)

        name_row = QHBoxLayout()
        name_lbl = QLabel("Filename")
        name_lbl.setObjectName("field_label")
        self.file_name_edit = QLineEdit()
        self.file_name_edit.setPlaceholderText("Leave blank to use the URL filename")
        name_row.addWidget(name_lbl)
        name_row.addWidget(self.file_name_edit, 1)
        lay.addLayout(name_row)

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
        lay.addLayout(dest_row)

        from ..theme import notifier
        self.ingest_btn = QPushButton("  Remote ingest")
        self.ingest_btn.setObjectName("upload_btn")
        # Icon should be dark (match button text) so it remains visible on the accent background
        self.ingest_btn.setIcon(lucide_icon("download-cloud", "#111010", 15))
        self.ingest_btn.setIconSize(QSize(15, 15))
        self.ingest_btn.setMinimumHeight(40)
        self.ingest_btn.clicked.connect(self._start_ingest)
        lay.addWidget(self.ingest_btn)

        self.result_bar = QLabel("")
        self.result_bar.setObjectName("log_console")
        self.result_bar.setWordWrap(True)
        self.result_bar.hide()
        lay.addWidget(self.result_bar)
        parent_lay.addWidget(card)

    def _build_jobs_toolbar(self, parent_lay: QVBoxLayout):
        tb = QHBoxLayout()
        tb.setSpacing(4)

        from ..theme import get_accent, notifier
        self.refresh_btn = QPushButton("  Refresh Jobs")
        self.refresh_btn.setObjectName("tb_btn")
        self.refresh_btn.setIcon(lucide_icon("refresh-cw", get_accent(), 13))
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

        from ..theme import accent_qcolor, get_font
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet(f"color: {accent_qcolor().name()}; font-size:{int(get_font()[1])}px; background:transparent;")
        tb.addWidget(self.status_lbl)
        # parent_lay may be an outer layout or the inner scroll area layout;
        # accept either by adding the QHBoxLayout to the provided layout.
        parent_lay.addLayout(tb)
        try:
            notifier().accent_changed.connect(lambda _old, _new: self._on_accent_changed(_old, _new))
        except Exception:
            pass

    def _on_accent_changed(self, old, new):
        try:
            from ..theme import accent_qcolor
            # Keep ingest icon dark to match the button text color
            self.ingest_btn.setIcon(lucide_icon("download-cloud", "#111010", 15))
            self.refresh_btn.setIcon(lucide_icon("refresh-cw", get_accent(), 13))
            from ..theme import get_font
            self.status_lbl.setStyleSheet(f"color: {accent_qcolor().name()}; font-size:{int(get_font()[1])}px; background:transparent;")
        except Exception:
            pass

    def _build_jobs_tree(self, parent_lay: QVBoxLayout):
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["File", "Status", "Progress", "Job ID"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSortingEnabled(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        hdr = self.tree.header()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(True)
        hdr.resizeSection(0, 280)   # File
        hdr.resizeSection(1, 100)   # Status
        hdr.resizeSection(2, 90)    # Progress
        hdr.resizeSection(3, 160)   # Job ID
        # When the jobs tree is inside the scroll area, disable its own
        # vertical scrollbar and let the outer QScrollArea provide scrolling
        # for the whole page. Also adjust size to contents so the tree grows
        # naturally and the QScrollArea handles overflow.
        try:
            # Let the outer scroll area handle vertical scrolling; make the
            # tree expand to take available space so it isn't rendered tiny.
            self.tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.tree.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            # Provide a reasonable minimum so the jobs area is usable even
            # when there are few or no items yet.
            self.tree.setMinimumHeight(200)
        except Exception:
            pass
        parent_lay.addWidget(self.tree, 1)

    def _make_card(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("card")
        return frame

    # ── Actions ───────────────────────────────────────────────────────────────

    def _browse_dest(self):
        api_key = self.get_api_key()
        if not api_key:
            self._status("⚠ Enter your API key in Settings first.")
            return
        dlg = FolderBrowserDialog(
            api_key, self.base_url,
            self.path_edit.text().strip() or "/",
            parent=self,
        )
        dlg.setWindowTitle("Choose remote ingest destination")
        if dlg.exec():
            self.path_edit.setText(dlg.selected)

    def _start_ingest(self):
        api_key    = self.get_api_key()
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
            self, "Cancel Transfer",
            f"Cancel transfer job {meta['job_id']!r}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._status("Cancelling job…")
        self._run_worker("cancel", job_id=meta["job_id"])

    # ── Worker dispatch ───────────────────────────────────────────────────────

    def _run_worker(self, op: str, **kwargs):
        w = RemoteWorker(op, self.get_api_key(), self.base_url, **kwargs)
        w.done.connect(self._on_done)
        w.error.connect(self._on_error)
        w.finished.connect(lambda: self._workers.remove(w) if w in self._workers else None)
        self._workers.append(w)
        w.start()

    def _on_done(self, result: dict):
        op = result.get("op")
        if op == "ingest":
            self.ingest_btn.setEnabled(True)
            data          = result.get("data") or {}
            job_id        = data.get("jobId") or data.get("id") or ""
            original_name = (data.get("originalName") or data.get("fileName")
                             or self.file_name_edit.text().strip())
            self.result_bar.setText(f"Queued: {original_name}  Job: {job_id or '—'}")
            self.result_bar.show()
            self._status("✓ Remote ingest queued")
            if job_id:
                self._watched_jobs[str(job_id)] = {"name": original_name, "seen": False, "checks": 0}
            # Notify app that the destination folder cache should be invalidated
            if self._on_ingest_done_cb is not None:
                dest_folder = self._normalized_path()
                self._on_ingest_done_cb(dest_folder)
            if self._is_active:
                self.refresh_timer.start()
            self.refresh_jobs()
        elif op == "jobs":
            self._populate_jobs(result.get("data"))
        elif op == "cancel":
            self._watched_jobs.pop(str(result.get("job_id", "")), None)
            self._status("✓ Job cancelled")
            self.refresh_jobs()

    def _on_error(self, msg: str):
        self.ingest_btn.setEnabled(True)
        self._status(f"✗ {msg}")
        QMessageBox.warning(self, "Remote Ingest Error", msg)

    # ── Jobs table ────────────────────────────────────────────────────────────

    def _populate_jobs(self, data):
        jobs = data.get("jobs", data) if isinstance(data, dict) else data
        if not isinstance(jobs, list):
            jobs = []

        self.tree.setSortingEnabled(False)
        self.tree.clear()
        active_job_ids: set[str] = set()

        for job in jobs:
            if not isinstance(job, dict):
                continue
            job_id = job.get("id") or job.get("jobId") or job.get("job_id") or ""
            if job_id:
                active_job_ids.add(str(job_id))
            name = (
                job.get("originalName") or job.get("fileName") or job.get("file_name")
                or job.get("name") or job.get("sourceUrl") or "—"
            )
            status   = job.get("status") or job.get("state") or "—"
            progress = job.get("progress") or job.get("percent") or job.get("progressPercent")
            progress_text = f"{progress}%" if progress not in (None, "") else "—"

            item = QTreeWidgetItem([str(name), str(status), str(progress_text), str(job_id)])
            item.setData(0, Qt.ItemDataRole.UserRole, {**job, "job_id": str(job_id)})
            status_lower = str(status).lower()
            if status_lower in ("failed", "error", "cancelled", "canceled"):
                item.setForeground(1, QColor("#f87171"))
            elif status_lower in ("complete", "completed", "done", "success"):
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

    def _update_watched_jobs(self, active_job_ids: set[str]):
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

    def _notify_ingest_finished(self, name: str, job_id: str):
        self.result_bar.setText(f"Finished: {name}  Job: {job_id}")
        self.result_bar.show()
        self._status(f"✓ Remote ingest finished: {name}")
        if self._is_active:
            QMessageBox.information(self, "Remote Ingest Finished", f"{name} finished ingesting.")

    # ── Selection ─────────────────────────────────────────────────────────────

    def _on_selection_changed(self):
        meta = self._selected_meta()
        self.cancel_btn.setEnabled(bool(meta and meta.get("job_id")))

    def _selected_meta(self) -> dict | None:
        items = self.tree.selectedItems()
        return items[0].data(0, Qt.ItemDataRole.UserRole) if items else None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def set_active(self, active: bool):
        self._is_active = active
        if active:
            self.refresh_jobs()
        else:
            self.refresh_timer.stop()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _normalized_path(self) -> str:
        path = self.path_edit.text().strip() or "/"
        if not path.startswith("/"):
            path = "/" + path
        return path.rstrip("/") + "/"

    @staticmethod
    def _filename_from_url(source_url: str) -> str:
        parsed = urlparse(source_url)
        return unquote(os.path.basename(parsed.path.rstrip("/")))

    def _status(self, msg: str):
        self.status_lbl.setText(msg)