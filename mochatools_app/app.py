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

from PyQt6.QtCore import Qt, QSize, QTimer, QEvent, QSettings
from PyQt6.QtGui import QColor, QPalette, QAction
from PyQt6.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QProgressBar, QPushButton, QCheckBox, QComboBox, QScrollArea,
    QSizePolicy, QSpinBox, QVBoxLayout, QWidget, QMessageBox,
    QSystemTrayIcon, QMenu,
)

from .constants import (
    APP_NAME, APP_VERSION, HARDCODED_BASE_URL, ORG_NAME,
)
from .logging_utils import write_debug_log
from .styles import STYLESHEET, build_stylesheet
from .workers import UploadWorker, StorageWorker
from .dialogs import FolderBrowserDialog, MochaDialog
from .updater import UpdateCheckWorker, UpdateDownloadWorker, launch_update_batch
from .remote_cache import cache, registry, CachePoller

from .ui import lucide_icon, CustomTitleBar, DropZone, FullWidthTabWidget
from .tabs import (
    FilesBrowserTab, MassUploadSection, RemoteTab, SharesTab, SyncTab,
    build_settings_tab, load_settings, save_settings,
)
from .theme import get_accent, accent_qcolor, get_font, get_background, get_background_palette

import re


def _parse_release_notes_md(notes: str) -> str:
    """
    Extract just the "What's New" section from a GitHub release body, as
    markdown — for feeding straight into a QLabel with
    setTextFormat(Qt.TextFormat.MarkdownText), which renders bullets/bold/etc
    natively without any manual HTML conversion.

    Strips the leading <img> (the gif/screenshot always put at the top of a
    release), the "## What's New" heading itself, and everything after the
    section (additional headings, the "Full Changelog: ...compare/..."
    footer) — but leaves the remaining markdown syntax (bullets, bold,
    links) untouched so the renderer can do its job.
    """
    if not notes:
        return ""

    # Normalize line endings FIRST. GitHub's API returns release bodies
    # with \r\n line endings; with re.MULTILINE, the trailing \r before \n
    # breaks the $ anchor in the heading regex below (it doesn't match
    # whitespace), which silently fails the heading match — and that
    # failure cascades into the "cut at next heading" step truncating the
    # body down to nothing. Normalizing up front avoids all of that.
    text = notes.replace("\r\n", "\n").replace("\r", "\n").strip()

    # Strip any <img ...> or <img ...>...</img> tag anywhere in the body.
    text = re.sub(r"<img\b[^>]*?/?>(?:.*?</img>)?", "", text, flags=re.IGNORECASE | re.DOTALL)

    # Find the "What's New" heading (## What's New, ### What's New, etc,
    # tolerant of straight/curly apostrophes or a missing apostrophe).
    heading_re = re.compile(
        r"^[ \t]*#{1,6}[ \t]*what.?s\s+new[ \t]*$",
        re.IGNORECASE | re.MULTILINE,
    )
    m = heading_re.search(text)
    body = text[m.end():] if m else text

    # Cut off at the next markdown heading, or a "Full Changelog"/compare
    # link line — whichever comes first.
    cutoffs = []
    next_heading = re.search(r"^[ \t]*#{1,6}\s+\S", body, re.MULTILINE)
    if next_heading:
        cutoffs.append(next_heading.start())
    changelog_line = re.search(
        r"^.*(Full Changelog|github\.com/.+/compare/).*$", body,
        re.IGNORECASE | re.MULTILINE,
    )
    if changelog_line:
        cutoffs.append(changelog_line.start())
    if cutoffs:
        body = body[:min(cutoffs)]

    return body.strip()


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
        self._last_speed_bps: float = 0.0
        self._is_uploading: bool = False
        self._last_bytes_done: int = 0
        self._last_bytes_total: int = 0

        # Update worker state
        self._update_tag:       str                      = ""
        self._update_url:       str                      = ""
        self._update_notes:     str                      = ""
        self._update_dl_worker: UpdateDownloadWorker | None = None
        self._pending_silent_update_popup: bool = False

        # Storage capacity indicator state
        self._storage_worker: StorageWorker | None = None
        self._storage_timer:  QTimer | None         = None

        # System tray state
        self._tray_icon: QSystemTrayIcon | None = None
        self._quitting: bool = False  # set True when the user really wants to quit
        self._tray_tooltip_timer: QTimer | None = None

        self._build_ui()
        self._build_tray_icon()
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
        self.tabs.addTab(upload_tab,       "Upload")
        self.tabs.addTab(self.remote_tab,  "Remote")
        self.tabs.addTab(self.files_tab,   "Files")
        self.tabs.addTab(self.shares_tab,  "Shares")
        self.tabs.addTab(self.sync_tab,    "Sync")
        self.tabs.addTab(settings_tab,     "Settings")

        # ── Remote cache poller ───────────────────────────────────────────────
        self._poller = CachePoller(self)
        self._poller.add("shares", lambda: self.api_key_edit.text().strip(),
                         HARDCODED_BASE_URL)
        self._poller.add("list",   lambda: self.api_key_edit.text().strip(),
                         HARDCODED_BASE_URL, path="/")
        self.files_tab.attach_cache_poller(self._poller)
        self.shares_tab.attach_cache_poller(self._poller)

        # ── Storage capacity indicator ──────────────────────────────────────────
        self._storage_timer = QTimer(self)
        self._storage_timer.setInterval(30_000)
        self._storage_timer.timeout.connect(self._refresh_storage)
        self._storage_timer.start()
        QTimer.singleShot(300, self._refresh_storage)

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
        self._style_copy_share_btn()
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
        self._is_uploading = active
        self.upload_btn.setVisible(not active)
        self.cancel_btn.setVisible(active)
        self.upload_btn.setEnabled(not active)

    # ── Upload signal handlers ────────────────────────────────────────────────

    def _on_progress(self, pct: float):
        self.progress_bar.setValue(int(pct * 1000))
        self.pct_label.setText(f"{pct:.3f}%")

    def _on_bytes_progress(self, done_bytes: int, total_bytes: int):
        grand = getattr(self, "_upload_grand_total", 0) or total_bytes
        self._last_bytes_done  = done_bytes
        self._last_bytes_total = grand
        self.transferred_label.setText(f"{self._fmt(done_bytes)} / {self._fmt(grand)}")

    def _on_speed(self, bps: float):
        self._last_speed_bps = bps
        if bps < 1024:      txt = f"{bps:.3f} B/s"
        elif bps < 1024**2: txt = f"{bps/1024:.3f} KB/s"
        else:               txt = f"{bps/1024**2:.3f} MB/s"
        self.speed_label.setText(txt)

    def _on_finished(self, result: dict):
        self._set_uploading(False)
        self._badge("Complete", "#4ade80")
        self.transferred_label.setText("")
        self._log(f"✓ Done! File ID: {result['file_id']}")
        upload_path = self.upload_path_edit.text().strip() or "/"
        self._on_upload_done(upload_path)
        if result.get("share_url"):
            url = result["share_url"]
            self._share_result_url = url
            from .theme import get_accent
            self.share_result.setText(f'<a href="{url}" style="color:{get_accent()};">{url}</a>')
            self._share_result_widget.show()
            self._on_share_created()

    def _on_error(self, msg: str):
        self._set_uploading(False)
        self._badge("Error", "#f87171")
        self.transferred_label.setText("")
        self._log(f"✗ Error: {msg}")

    # ── Cache invalidation helpers ────────────────────────────────────────────

    def _on_upload_done(self, remote_folder: str):
        """
        Called when any upload finishes (single-file tab or mass upload section).
        Invalidates the file-list cache for the destination folder and triggers
        a background refresh so the Files tab stays current.
        """
        if not self._poller:
            return
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
        from .theme import get_accent, DEFAULT_ACCENT, get_background_palette
        self._last_badge_args = (text, color)
        self.status_badge.setText(f"● {text}")
        if color == DEFAULT_ACCENT:
            color = get_accent()
        try:
            pal = get_background_palette()
            neutral_bg, neutral_border = pal["bg3"], pal["border"]
        except Exception:
            neutral_bg, neutral_border = "#1e1c19", "#2e2b27"
        bg_map = {"#c8a96e": "#2a2215", "#4ade80": "#0f2318",
                  "#f87171": "#2a0f0f", "#9ca3af": neutral_bg}
        bd_map = {"#c8a96e": "#4a3b1e", "#4ade80": "#1e4a30",
                  "#f87171": "#4a1e1e", "#9caaf": neutral_border}
        bg = bg_map.get(color, neutral_bg)
        bd = bd_map.get(color, neutral_border)
        self.status_badge.setStyleSheet(
            f"background-color: {bg}; border: 1px solid {bd}; "
            f"border-radius: 10px; color: {color}; font-size: 11px; "
            f"font-weight: 600; padding: 2px 10px;"
        )

    def _style_copy_share_btn(self):
        from .theme import get_background_palette
        try:
            pal = get_background_palette()
            bg3, text, border2 = pal["bg3"], pal["text"], pal["border2"]
        except Exception:
            bg3, text, border2 = "#1e1c19", "#f0ece6", "#3d3a35"
        self.copy_share_result_btn.setStyleSheet(
            "min-height:0px; padding:0px 16px; font-size:13px; font-weight:600;"
            f"background:{bg3}; color:{text}; border:1px solid {border2}; border-radius:7px;"
        )

    @staticmethod
    def _fmt(n: int) -> str:
        if n < 1024:      return f"{n} B"
        if n < 1024**2:   return f"{n/1024:.3f} KB"
        if n < 1024**3:   return f"{n/1024**2:.3f} MB"
        return f"{n/1024**3:.3f} GB"

    # ── Storage capacity indicator ──────────────────────────────────────────────

    def _refresh_storage(self):
        api_key = self.api_key_edit.text().strip()
        if not api_key:
            return
        if self._storage_worker and self._storage_worker.isRunning():
            return
        w = StorageWorker(api_key, HARDCODED_BASE_URL)
        w.done.connect(self._on_storage_done)
        w.error.connect(self._on_storage_error)
        w.finished.connect(lambda: setattr(self, "_storage_worker", None))
        self._storage_worker = w
        w.start()

    def _on_storage_done(self, data: dict):
        available = data.get("availableBytes")
        if available is None:
            text = "Unlimited"
        else:
            text = f"{self._fmt(available)} free"
        self.titlebar.set_storage_text(text)

    def _on_storage_error(self, msg: str):
        # Leave whatever was last shown (or nothing) rather than showing an error
        # in the titlebar — the next poll will recover once the API is reachable.
        pass

    # ── Tab switching ─────────────────────────────────────────────────────────

    def _on_tab_changed(self, index: int):
        # 0=Upload, 1=Remote, 2=Files, 3=Shares, 4=Sync, 5=Settings
        self.remote_tab.set_active(index == 1)

        api_key = self.api_key_edit.text().strip()
        if not api_key:
            return

        if index in (2, 3) and self._poller:
            self._poller.start()

        if index == 2:
            self.files_tab._navigate(self.files_tab.current_path)
        elif index == 3:
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
        self._pending_silent_update_popup = silent
        w = UpdateCheckWorker(self)
        w.update_available.connect(self._on_update_available)
        w.up_to_date.connect(lambda: self._on_up_to_date(silent))
        w.error.connect(lambda msg: self._on_update_error(msg, silent))
        w.finished.connect(lambda: self.check_update_btn.setEnabled(True))
        w.start()

    def _on_update_available(self, tag: str, url: str, notes: str):
        self._update_tag = tag
        self._update_url = url
        self._update_notes = notes
        self.update_status_lbl.setText(f"Update available: {tag}  (current: {APP_VERSION})")
        self.install_update_btn.setVisible(bool(url))
        self.release_info_btn.setVisible(bool(url))
        if not url:
            self.update_status_lbl.setText(
                f"Update {tag} available — no binary for this platform. "
                "Download manually from github.com/nxllxvxxd2/Mocha-Tools/releases"
            )
            return

        # Only pop up the startup-launch notification dialog (not on a
        # manual "Check for updates" click — the Settings tab already
        # reflects the new state for that case) and only if the user
        # hasn't chosen to skip this specific version.
        if getattr(self, "_pending_silent_update_popup", False):
            self._pending_silent_update_popup = False
            skipped = QSettings(ORG_NAME, APP_NAME).value("skip_update_tag", "")
            if skipped != tag:
                self._show_update_available_popup(tag, notes)

    def _build_release_info_dialog(self, tag: str, notes: str, with_buttons: bool = True):
        """
        Builds the MochaDialog shared by both the startup "update available"
        popup and the Settings → "Release Info" button, so the two always
        render identically. When with_buttons is False, the dialog shows
        only the header + "What's New" markdown (no Update/Skip/Remind Me
        buttons) — used for the Settings-tab "Release Info" view.

        Returns (dlg, update_btn, skip_btn, later_btn) — the latter three
        are None when with_buttons is False.
        """
        whats_new_md = _parse_release_notes_md(notes)

        update_btn = skip_btn = later_btn = None

        if with_buttons:
            # Pre-compute a width wide enough that the three buttons never
            # clip (this is what caused "kip This Versio" / "emind Me
            # Later" before), while keeping a sane floor for the body text.
            _tmp_row = QHBoxLayout()
            _tmp_buttons = [QPushButton(t) for t in ("Update Now", "Skip This Version", "Remind Me Later")]
            for b in _tmp_buttons:
                b.setMinimumHeight(32)
                _tmp_row.addWidget(b)
            btn_row_width = _tmp_row.sizeHint().width()
            for b in _tmp_buttons:
                b.deleteLater()
            dlg_width = max(460, btn_row_width + 28 * 2 + 8)
        else:
            dlg_width = 460

        dlg = MochaDialog("Update available", self, min_size=(dlg_width, 160))
        lay = dlg.content_layout
        grip_item = lay.takeAt(lay.count() - 1)  # pop the size-grip row, re-add at the end

        header = QLabel(f"Mocha Tools {tag} is available (you have {APP_VERSION}).")
        header.setWordWrap(True)
        header.setStyleSheet("font-size: 14px; font-weight: 600; background: transparent;")
        lay.addWidget(header)

        if whats_new_md:
            body = QLabel()
            body.setTextFormat(Qt.TextFormat.MarkdownText)
            body.setWordWrap(True)
            body.setOpenExternalLinks(True)
            body.setText(f"**What's New**\n\n{whats_new_md}")
            body.setStyleSheet("background: transparent;")
            lay.addWidget(body)

        if with_buttons:
            btn_row = QHBoxLayout()
            btn_row.addStretch()
            update_btn = QPushButton("Update Now")
            skip_btn   = QPushButton("Skip This Version")
            later_btn  = QPushButton("Remind Me Later")
            for b in (update_btn, skip_btn, later_btn):
                b.setMinimumHeight(32)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                btn_row.addWidget(b)
            lay.addLayout(btn_row)

            try:
                acc = get_accent()
                update_btn.setStyleSheet(
                    f"background: {acc}; color: #111010; font-weight: 700; "
                    f"border: none; border-radius: 6px; padding: 4px 16px;"
                )
                for b in (skip_btn, later_btn):
                    b.setStyleSheet("border-radius: 6px; padding: 4px 16px;")
            except Exception:
                pass

        if grip_item:
            lay.addItem(grip_item)

        return dlg, update_btn, skip_btn, later_btn

    def _show_update_available_popup(self, tag: str, notes: str):
        """
        Startup notification: lets the user update now, snooze, or skip
        this version. Built on MochaDialog so its titlebar (◆ + title +
        close button, draggable) matches every other dialog in the app,
        instead of a generic OS-chrome dialog.
        """
        dlg, update_btn, skip_btn, later_btn = self._build_release_info_dialog(
            tag, notes, with_buttons=True
        )

        result_holder = {"clicked": None}

        def _set_clicked(name):
            result_holder["clicked"] = name
            dlg.accept()

        update_btn.clicked.connect(lambda: _set_clicked("update"))
        skip_btn.clicked.connect(lambda: _set_clicked("skip"))
        later_btn.clicked.connect(lambda: _set_clicked("later"))

        dlg.exec()
        clicked = result_holder["clicked"]

        if clicked == "update":
            self.tabs.setCurrentIndex(5)
            self._install_update()
        elif clicked == "skip":
            QSettings(ORG_NAME, APP_NAME).setValue("skip_update_tag", tag)
        # "later" (or dialog dismissed via Esc/X) → do nothing further;
        # it'll be offered again on the next launch.

    def _show_release_info(self):
        """
        Settings → "Release Info" button. Shows the exact same dialog as
        the startup update popup (same MochaDialog titlebar, same header
        line, same "What's New" markdown) but with no action buttons —
        just the release info, for whatever update was last found.
        """
        if not self._update_tag:
            return
        dlg, _u, _s, _l = self._build_release_info_dialog(
            self._update_tag, self._update_notes, with_buttons=False
        )
        dlg.exec()

    def _on_up_to_date(self, silent: bool):
        try:
            from .updater import _is_portable_windows
            _portable_suffix = " (portable)" if _is_portable_windows() else ""
        except Exception:
            _portable_suffix = ""
        self.update_status_lbl.setText(f"You're up to date ({APP_VERSION}{_portable_suffix})")
        self.install_update_btn.hide()
        self.release_info_btn.hide()
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
        self.release_info_btn.hide()
        result = QMessageBox.question(
            self, "Restart required",
            f"Mocha Tools {self._update_tag} has been installed.\n\nRestart now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if result == QMessageBox.StandardButton.Yes:
            self.update_status_lbl.setText("Restarting…")
            launch_update_batch(self._update_bat_path)
            QApplication.quit()

    def _on_update_done(self):
        self.update_progress.setValue(100)
        self.install_update_btn.hide()
        self.release_info_btn.hide()
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

        self.tabs.setCurrentIndex(6)
        self.update_status_lbl.setText("Test mode: fetching latest release info…")
        self.update_progress.setValue(0)
        self.update_progress.show()
        self.check_update_btn.setEnabled(False)
        self.install_update_btn.hide()
        self.release_info_btn.hide()

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

        from PyQt6.QtCore import QThread
        class _FetchThread(QThread):
            def run(self_):
                _fetch()

        self._test_fetch_thread = _FetchThread(self)
        self._test_fetch_thread.start()

    # ── System tray ───────────────────────────────────────────────────────────

    def _build_tray_icon(self):
        """Create the QSystemTrayIcon (hidden until the setting is enabled)."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._tray_icon = None
            return

        tray = QSystemTrayIcon(self)
        try:
            tray.setIcon(lucide_icon("coffee", get_accent(), 32))
        except Exception:
            pass
        tray.setToolTip(APP_NAME)

        menu = QMenu()
        show_action = QAction("Show Mocha Tools", self)
        show_action.triggered.connect(self._restore_from_tray)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self._quit_from_tray)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        tray.setContextMenu(menu)

        tray.activated.connect(self._on_tray_activated)

        self._tray_icon = tray
        # Hidden until the user enables "Minimize and close to tray"
        tray.hide()

        self._tray_tooltip_timer = QTimer(self)
        self._tray_tooltip_timer.setInterval(1000)
        self._tray_tooltip_timer.timeout.connect(self._refresh_tray_tooltip)
        self._tray_tooltip_timer.start()

    def _on_tray_setting_toggled(self, enabled: bool):
        """Called when the Settings > System Tray checkbox changes."""
        if not self._tray_icon:
            return
        if enabled:
            self._tray_icon.show()
        else:
            self._tray_icon.hide()

    def _on_tray_activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._restore_from_tray()

    def _restore_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self):
        self._quitting = True
        self.close()

    def _tray_enabled(self) -> bool:
        cb = getattr(self, "minimize_to_tray_cb", None)
        return bool(cb and cb.isChecked() and self._tray_icon is not None)

    # ── Tray tooltip: live upload status ────────────────────────────────────

    @staticmethod
    def _fmt_speed(bps: float) -> str:
        if bps < 1024:       return f"{bps:.3f} B/s"
        if bps < 1024 ** 2:  return f"{bps/1024:.3f} KB/s"
        return f"{bps/1024**2:.3f} MB/s"

    def _upload_tab_status(self):
        """Return (active, pct, speed_bps, remaining_bytes) for the single-file
        Upload tab.

        Uses the explicit `_is_uploading` flag rather than checking widget
        visibility — visibility collapses to False for every child widget
        once the main window is hidden (e.g. minimised to tray), which
        would otherwise make the tray think nothing is uploading even
        though the background worker is still running.
        """
        active = bool(getattr(self, "_is_uploading", False))
        if not active:
            return False, 0.0, 0.0, None
        pct = 0.0
        try:
            pct = self.progress_bar.value() / 1000.0
        except Exception:
            pass
        speed = getattr(self, "_last_speed_bps", 0.0)
        done  = getattr(self, "_last_bytes_done", 0)
        total = getattr(self, "_last_bytes_total", 0)
        remaining = max(total - done, 0) if total else None
        return True, pct, speed, remaining

    def _mass_upload_status(self):
        """Return (active, pct, speed_bps, remaining_bytes) for the Mass
        Upload section."""
        sec = getattr(self, "mass_upload_section", None)
        if not sec:
            return False, 0.0, 0.0, None
        active = bool(getattr(sec, "_active_workers", None))
        if not active:
            return False, 0.0, 0.0, None
        pct = 0.0
        try:
            pct = sec._prog_bar.value() / 1000.0
        except Exception:
            pass
        speed = getattr(sec, "_last_speed_bps", 0.0)
        remaining = None
        try:
            queue = getattr(sec, "_queue", [])
            all_done  = sum(e.get("_bytes_done", 0)  for e in queue)
            all_total = sum(e.get("_bytes_total", 0) for e in queue)
            if all_total:
                remaining = max(all_total - all_done, 0)
        except Exception:
            pass
        return True, pct, speed, remaining

    def _sync_tab_status(self):
        """Return (active, pct, speed_bps, remaining_bytes) for the Sync tab.

        Sync pairs run independently of one another, so there is no single
        meaningful overall percentage the way there is for one upload or
        one mass-upload queue. We still report `active` + summed speed;
        pct is left at 0 and the caller treats multi-pair sync as a
        "speed only" source, same as when several tabs run together.
        Remaining bytes are summed across active pairs when known.
        """
        st = getattr(self, "sync_tab", None)
        if not st:
            return False, 0.0, 0.0, None
        pairs = getattr(st, "_pairs", {}) or {}
        active_pairs = [p for p in pairs.values() if p.get("status") == "uploading"]
        if not active_pairs:
            return False, 0.0, 0.0, None
        speed = sum(p.get("_speed_bps", 0.0) for p in active_pairs)
        pct = 0.0
        if len(active_pairs) == 1:
            # Single active pair — approximate its progress from bytes done/total
            # if available, otherwise leave unknown (0).
            p = active_pairs[0]
            done, total = p.get("_bytes_done", 0), p.get("_bytes_total", 0)
            if total:
                pct = (done / total) * 100.0
        remaining = None
        totals = [(p.get("_bytes_done", 0), p.get("_bytes_total", 0)) for p in active_pairs]
        if all(total for _, total in totals):
            remaining = sum(max(total - done, 0) for done, total in totals)
        return True, pct, speed, remaining

    @staticmethod
    def _fmt_eta(seconds: float) -> str:
        seconds = max(0, int(seconds))
        h, rem = divmod(seconds, 3600)
        m, s   = divmod(rem, 60)
        if h:
            return f"{h:d}h {m:02d}m"
        if m:
            return f"{m:d}m {s:02d}s"
        return f"{s:d}s"

    def _refresh_tray_tooltip(self):
        sources = [
            self._upload_tab_status(),
            self._mass_upload_status(),
            self._sync_tab_status(),
        ]
        active_sources = [s for s in sources if s[0]]

        if not active_sources:
            if self._tray_icon:
                self._tray_icon.setToolTip(APP_NAME)
            if getattr(self, "titlebar", None):
                self.titlebar.set_eta_text("")
            return

        total_speed = sum(s[2] for s in active_sources)

        # ETA: only meaningful when every active source knows its remaining
        # bytes, and only worth showing once there's measurable speed —
        # otherwise a brief speed dip to ~0 would flash a huge/garbage ETA.
        remainings = [s[3] for s in active_sources]
        eta_text = ""
        if total_speed > 1024 and all(r is not None for r in remainings):
            total_remaining = sum(remainings)
            eta_seconds = total_remaining / total_speed
            eta_text = f"ETA {self._fmt_eta(eta_seconds)}"

        if getattr(self, "titlebar", None):
            self.titlebar.set_eta_text(eta_text)

        if not self._tray_icon:
            return

        if len(active_sources) == 1:
            _, pct, speed, _ = active_sources[0]
            tooltip = f"{APP_NAME}\n{pct:.3f}% · {self._fmt_speed(speed)}"
        else:
            # Multiple tabs/features uploading at once — a single combined
            # percentage isn't meaningful, so show total speed only.
            tooltip = f"{APP_NAME}\nUploading · {self._fmt_speed(total_speed)}"

        if eta_text:
            tooltip += f"\n{eta_text}"

        self._tray_icon.setToolTip(tooltip)

    def changeEvent(self, event):
        if (
            event.type() == QEvent.Type.WindowStateChange
            and self.isMinimized()
            and self._tray_enabled()
        ):
            # Defer to the next event loop pass so the minimise animation
            # completes normally before we hide the window into the tray.
            QTimer.singleShot(0, self.hide)
        super().changeEvent(event)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self._tray_enabled() and not self._quitting:
            event.ignore()
            self.hide()
            if self._tray_icon:
                self._tray_icon.showMessage(
                    APP_NAME,
                    "Mocha Tools is still running in the system tray.",
                    QSystemTrayIcon.MessageIcon.Information,
                    2000,
                )
            return

        save_settings(self)
        self.remote_tab.set_active(False)
        self.sync_tab.closeEvent(event)
        if self._poller:
            self._poller.stop()
        if self._storage_timer:
            self._storage_timer.stop()
        if self._tray_tooltip_timer:
            self._tray_tooltip_timer.stop()
        if self._storage_worker:
            self._storage_worker.quit()
        for w in list(self.remote_tab._workers):
            w.quit()
        for w in list(self.files_tab._workers):
            w.quit()
        for w in list(self.shares_tab._workers):
            w.quit()
        if self._tray_icon:
            self._tray_icon.hide()
        super().closeEvent(event)
        QApplication.instance().quit()


def _build_app_palette() -> QPalette:
    """Build a QPalette from the active background theme + accent.

    Centralized so startup and background-theme switches stay in sync —
    previously this was hardcoded to the mocha hex values and never
    refreshed when the background theme changed, which is why switching
    to White/Black left the titlebar, tab bar, and other palette-driven
    chrome stuck on the old mocha colors even though the QSS had updated.
    """
    pal_colors = get_background_palette()
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window,          QColor(pal_colors["bg0"]))
    palette.setColor(QPalette.ColorRole.WindowText,      QColor(pal_colors["text"]))
    palette.setColor(QPalette.ColorRole.Base,            QColor(pal_colors["bg7"]))
    palette.setColor(QPalette.ColorRole.Text,            QColor(pal_colors["text"]))
    palette.setColor(QPalette.ColorRole.Button,          QColor(pal_colors["bg3"]))
    palette.setColor(QPalette.ColorRole.ButtonText,      QColor(pal_colors["text"]))
    palette.setColor(QPalette.ColorRole.Highlight,       accent_qcolor())
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#111010"))
    return palette


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setStyle("Fusion")
    # Closing/minimising to the system tray hides the window rather than
    # closing it, but we still don't want Qt to quit the app the moment
    # the (now hidden) main window's close event fires in other code paths.
    app.setQuitOnLastWindowClosed(False)
    try:
        app.setStyleSheet(build_stylesheet(get_accent(), background_key=get_background()))
    except Exception:
        app.setStyleSheet(STYLESHEET)

    try:
        from .theme import get_font
        from PyQt6.QtGui import QFont
        fam, sz = get_font()
        if fam:
            app.setFont(QFont(fam, int(sz)))
    except Exception:
        pass

    app.setPalette(_build_app_palette())

    test_update = "--test-update" in sys.argv

    win = MochaTools()
    win.show()

    def _refresh_accented_icons():
        try:
            from .theme import get_accent
            from .ui import lucide_icon
            if hasattr(win, 'upload_btn'):
                win.upload_btn.setIcon(lucide_icon('upload', '#111010', 15))
                win.upload_btn.setIconSize(QSize(15, 15))
            if hasattr(win, 'cancel_btn'):
                win.cancel_btn.setIcon(lucide_icon('x', get_accent(), 13))
                win.cancel_btn.setIconSize(QSize(13, 13))
            try:
                if hasattr(win, 'mass_upload_section') and hasattr(win.mass_upload_section, '_start_btn'):
                    win.mass_upload_section._start_btn.setIcon(lucide_icon('upload', '#111010', 15))
                    win.mass_upload_section._start_btn.setIconSize(QSize(15, 15))
            except Exception:
                pass
            try:
                if hasattr(win, 'titlebar') and hasattr(win.titlebar, '_refresh_icons'):
                    win.titlebar._refresh_icons()
            except Exception:
                pass
            try:
                if hasattr(win, 'install_update_btn'):
                    acc = get_accent()
                    win.install_update_btn.setStyleSheet(
                        f"min-height:0px; padding:0px 16px; font-size:13px; font-weight:700;"
                        f"background:{acc}; color:#111010; border:none; border-radius:7px;"
                    )
            except Exception:
                pass
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

    win._refresh_accented_icons = _refresh_accented_icons

    try:
        from .theme import notifier, get_accent, get_background
        from .styles import build_stylesheet

        def _on_accent_changed(old_hx: str, hx: str):
            try:
                a = QApplication.instance()
                if a:
                    a.setStyleSheet(build_stylesheet(hx, background_key=get_background()))
                    pal = a.palette()
                    from .theme import accent_qcolor
                    pal.setColor(QPalette.ColorRole.Highlight, accent_qcolor())
                    a.setPalette(pal)
                    try:
                        if hasattr(win, '_refresh_accented_icons'):
                            win._refresh_accented_icons()
                    except Exception:
                        pass
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
        try:
            _on_accent_changed(None, get_accent())
        except Exception:
            pass

        def _on_background_changed(old_key: str, new_key: str):
            # Switching background themes needs both the QSS (cards, tabs,
            # inputs, etc — handled by build_stylesheet tokens) AND the
            # QPalette (titlebar/tab-bar chrome and any unstyled native
            # widgets that fall back to palette roles) rebuilt together,
            # or the palette-driven chrome stays stuck on the old theme.
            try:
                a = QApplication.instance()
                if a:
                    a.setStyleSheet(build_stylesheet(get_accent(), background_key=new_key))
                    a.setPalette(_build_app_palette())
                    try:
                        if hasattr(win, '_refresh_accented_icons'):
                            win._refresh_accented_icons()
                    except Exception:
                        pass
                    try:
                        if hasattr(win, 'titlebar') and hasattr(win.titlebar, '_refresh_icons'):
                            win.titlebar._refresh_icons()
                    except Exception:
                        pass
                    try:
                        if hasattr(win, '_style_copy_share_btn'):
                            win._style_copy_share_btn()
                    except Exception:
                        pass
                    try:
                        # re-apply whatever status text/color is currently shown so
                        # the badge's tinted background tracks the new theme too
                        if hasattr(win, 'status_badge') and hasattr(win, '_last_badge_args'):
                            win._badge(*win._last_badge_args)
                    except Exception:
                        pass
            except Exception:
                pass

        notifier().background_changed.connect(_on_background_changed)

        try:
            from .theme import notifier as _notifier
            def _on_font_change(fam, sz):
                try:
                    from PyQt6.QtGui import QFont
                    a = QApplication.instance()
                    if a:
                        a.setFont(QFont(fam, int(sz)))
                        a.processEvents()
                        widgets = a.topLevelWidgets()
                        for w in widgets:
                            try:    a.style().unpolish(w)
                            except Exception: pass
                            try:    a.style().polish(w)
                            except Exception: pass
                        try:
                            a.setStyleSheet(build_stylesheet(get_accent(), background_key=get_background()))
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
            win._poller.start()

    QTimer.singleShot(300, _preload)

    if test_update:
        QTimer.singleShot(500, win._trigger_test_update)
    elif getattr(win, "check_updates_on_launch_cb", None) is None or win.check_updates_on_launch_cb.isChecked():
        QTimer.singleShot(2000, lambda: win._check_for_updates(silent=True))

    sys.exit(app.exec())


if __name__ == "__main__":
    main()