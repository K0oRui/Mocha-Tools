"""
app.py — MochaTools main window and entry point.

MochaTools is the application shell.  All tab content lives in
mochatools_app/tabs/ and shared widgets in mochatools_app/ui/.

Tab index reference:
  0  Upload        1  Mass Upload   2  Remote
  3  Files         4  Shares        5  Settings
"""

import os
import sys

from PyQt6.QtCore import Qt, QSize, QTimer
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QProgressBar, QPushButton, QCheckBox, QComboBox, QScrollArea,
    QSizePolicy, QSpinBox, QVBoxLayout, QWidget, QMessageBox,
)

from .constants import (
    APP_NAME, APP_VERSION, HARDCODED_BASE_URL,
    DEFAULT_CHUNK_SIZE_MB, DEFAULT_MAX_CHUNKS, ORG_NAME,
)
from .logging_utils import write_debug_log
from .styles import STYLESHEET
from .workers import UploadWorker
from .dialogs import FolderBrowserDialog
from .updater import UpdateCheckWorker, UpdateDownloadWorker
from .remote_cache import cache, registry, CachePoller

from .ui import lucide_icon, CustomTitleBar, DropZone, FullWidthTabWidget
from .tabs import (
    FilesBrowserTab, MassUploadTab, RemoteTab, SharesTab,
    build_settings_tab, load_settings, save_settings,
)


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

        self.mass_upload_tab = MassUploadTab(
            get_api_key=lambda: self.api_key_edit.text().strip(),
            get_mass_settings=lambda: (
                self.mass_conc_spin.value(),
                self.mass_chunk_spin.value(),
                self.mass_maxchunk_spin.value(),
            ),
            get_debug=lambda: self.debug_cb.isChecked(),
            on_upload_done=self._on_upload_done,
        )
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

        # Add tabs in order
        self.tabs.addTab(upload_tab,           "Upload")
        self.tabs.addTab(self.mass_upload_tab, "Mass Upload")
        self.tabs.addTab(self.remote_tab,      "Remote")
        self.tabs.addTab(self.files_tab,       "Files")
        self.tabs.addTab(self.shares_tab,      "Shares")
        self.tabs.addTab(settings_tab,         "Settings")

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
            ("upload",         "#9c9484"),
            ("upload",         "#9c9484"),
            ("download-cloud", "#9c9484"),
            ("folder",         "#9c9484"),
            ("share-2",        "#9c9484"),
            ("settings",       "#9c9484"),
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
        self.progress_bar.setValue(0)
        self.pct_label = QLabel("0%")
        self.pct_label.setObjectName("status_label")
        self.pct_label.setFixedWidth(36)
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
        self.cancel_btn.setIcon(lucide_icon("x", "#c8a96e", 13))
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
        self.share_result.hide()

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
        self.share_result.hide()
        self.progress_bar.setValue(0)
        self.pct_label.setText("0%")
        self.speed_label.setText("")
        self.transferred_label.setText("")
        self._badge("Uploading", "#c8a96e")

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
        self.pct_label.setText("0%")
        self.speed_label.setText("")
        self.transferred_label.setText("")
        self.share_result.hide()
        self._log("Upload cancelled by user.")

    def _set_uploading(self, active: bool):
        self.upload_btn.setVisible(not active)
        self.cancel_btn.setVisible(active)
        self.upload_btn.setEnabled(not active)

    # ── Upload signal handlers ────────────────────────────────────────────────

    def _on_progress(self, pct: int):
        self.progress_bar.setValue(pct)
        self.pct_label.setText(f"{pct}%")

    def _on_bytes_progress(self, done_bytes: int, total_bytes: int):
        grand = getattr(self, "_upload_grand_total", 0) or total_bytes
        self.transferred_label.setText(f"{self._fmt(done_bytes)} / {self._fmt(grand)}")

    def _on_speed(self, bps: float):
        if bps < 1024:      txt = f"{bps:.0f} B/s"
        elif bps < 1024**2: txt = f"{bps/1024:.1f} KB/s"
        else:               txt = f"{bps/1024**2:.2f} MB/s"
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
            self.share_result.setText(f'<a href="{url}" style="color:#c8a96e;">{url}</a>')
            self.share_result.show()
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

    # ── Status helpers ────────────────────────────────────────────────────────

    def _log(self, msg: str):
        debug_enabled = getattr(self, "debug_cb", None) and self.debug_cb.isChecked()
        if msg.startswith("[DEBUG]") and not debug_enabled:
            return
        self.log_label.setText(msg)
        if debug_enabled:
            write_debug_log(msg)

    def _badge(self, text: str, color: str):
        self.status_badge.setText(f"● {text}")
        bg_map = {"#c8a96e": "#2a2215", "#4ade80": "#0f2318",
                  "#f87171": "#2a0f0f", "#9ca3af": "#1e1c19"}
        bd_map = {"#c8a96e": "#4a3b1e", "#4ade80": "#1e4a30",
                  "#f87171": "#4a1e1e", "#9ca3af": "#2e2b27"}
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
        if n < 1024**2:   return f"{n/1024:.1f} KB"
        if n < 1024**3:   return f"{n/1024**2:.1f} MB"
        return f"{n/1024**3:.2f} GB"

    # ── Tab switching ─────────────────────────────────────────────────────────

    def _on_tab_changed(self, index: int):
        # 0=Upload, 1=Mass Upload, 2=Remote, 3=Files, 4=Shares, 5=Settings
        self.remote_tab.set_active(index == 2)

        api_key = self.api_key_edit.text().strip()
        if not api_key:
            return

        if index in (3, 4) and self._poller:
            # Ensure poller is running whenever Files or Shares tab is visible
            self._poller.start()

        if index == 3:
            self.files_tab._navigate(self.files_tab.current_path)
        elif index == 4:
            # Poller is already running after start() above; serve stale cache
            # instantly if available, fresh data arrives via subscription.
            stale = cache.get("shares")
            if stale is not None:
                self.shares_tab._cache = stale
                self.shares_tab._render(stale)
                self.shares_tab._status("Refreshing…")
        elif index != 2 and index != 5:
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
            import subprocess
            subprocess.Popen([sys.executable] + sys.argv)
            QApplication.quit()

    def _on_update_dl_error(self, msg: str):
        self.update_progress.hide()
        self.install_update_btn.setEnabled(True)
        self.update_status_lbl.setText(f"Download failed: {msg}")
        QMessageBox.warning(self, "Update failed", msg)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        save_settings(self)
        self.remote_tab.set_active(False)
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
    app.setStyleSheet(STYLESHEET)

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window,          QColor("#111010"))
    palette.setColor(QPalette.ColorRole.WindowText,      QColor("#f0ece6"))
    palette.setColor(QPalette.ColorRole.Base,            QColor("#141210"))
    palette.setColor(QPalette.ColorRole.Text,            QColor("#f0ece6"))
    palette.setColor(QPalette.ColorRole.Button,          QColor("#1e1c19"))
    palette.setColor(QPalette.ColorRole.ButtonText,      QColor("#f0ece6"))
    palette.setColor(QPalette.ColorRole.Highlight,       QColor("#c8a96e"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#111010"))
    app.setPalette(palette)

    win = MochaTools()
    win.show()

    def _preload():
        if win.api_key_edit.text().strip():
            # poller.start() immediately polls all registered slots (list /, shares)
            # and delivers results via cache subscriptions — no extra workers needed.
            win._poller.start()

    QTimer.singleShot(300,  _preload)
    QTimer.singleShot(2000, lambda: win._check_for_updates(silent=True))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()