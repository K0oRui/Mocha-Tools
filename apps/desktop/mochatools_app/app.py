"""
app.py — MochaTools main window and orchestrator.

MochaTools is the application shell.  Tab content lives in
mochatools_app/tabs/, shared widgets in mochatools_app/ui/,
and subsystems are factored out to:

  settings.py        – Settings tab UI + persistence
  upload_manager.py  – Upload tab construction + single-file upload flow
  tray_manager.py    – System tray icon, context menu, live tooltip
  update_controller.py – Check / download / install / restart
  window_chrome.py   – Frameless resize, rounding, cursor logic
  entrypoint.py      – main(), palette, theme/signal wiring
  utils.py           – Pure helper functions

Shared mutable state between modules lives in the ``AppContext``
dataclass (``self.ctx``), avoiding the need for every module to
reach into ``win`` for cross-cutting state.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .constants import (
    APP_NAME,
    APP_VERSION,
)
from .logging_utils import write_debug_log
from .mocha_client import MochaClient
from .remote_cache import CachePoller, cache
from .tabs import (
    FilesBrowserTab,
    MassUploadSection,
    RemoteTab,
    SharesTab,
    SyncTab,
    build_settings_tab,
    load_settings,
    save_settings,
)
from .theme import (
    get_accent,
)
from .tray_manager import setup_tray
from .ui import CustomTitleBar, FullWidthTabWidget, lucide_icon
from .update_controller import install_update_controller

# Subsystems
from .upload_manager import build_upload_tab, install_upload
from .upload_pipeline import UploadManager
from .window_chrome import apply_window_rounding
from .window_chrome import event_filter as _chrome_event_filter
from .workers import StorageWorker


# ── Shared mutable state ─────────────────────────────────────────────────────


@dataclass
class AppContext:
    """Cross-module mutable state, replacing the scattered win._xxx fields.

    Modules receive ``ctx`` alongside ``win`` and read/write these fields
    instead of attaching ad-hoc attributes to the window object.
    """

    # Upload runtime state
    is_uploading: bool = False
    upload_job_id: int | None = None
    last_speed_bps: float = 0.0
    last_bytes_done: int = 0
    last_bytes_total: int = 0
    upload_grand_total: int = 0
    selected_files: list[str] = field(default_factory=list)
    selected_root: str = ""
    share_result_url: str = ""

    # Shared API client (all HTTP goes through this)
    client: object | None = None

    # Worker references (transient — set during operations)
    storage_worker: StorageWorker | None = None

    # Shared upload pipeline (queue + concurrency + worker lifecycle)
    upload_manager: object | None = None

    # Update state (set by update_controller)
    update_tag: str = ""
    update_url: str = ""
    update_notes: str = ""
    update_bat_path: str = ""
    update_dl_worker: object | None = None
    pending_silent_update_popup: bool = False

    # Tray state
    tray_icon: QSystemTrayIcon | None = None
    quitting: bool = False
    tray_tooltip_timer: QTimer | None = None


class MochaTools(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Mocha Tools")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self._corner_radius = 12
        self._resize_margin = 7
        self._resize_cursor_active = False
        self._titlebar_dragging = False
        self.setMouseTracking(True)
        self.setMinimumWidth(520)
        self.setMinimumHeight(600)
        self.resize(760, 900)

        self.ctx = AppContext()
        self.ctx.client = MochaClient(
            get_api_key=lambda: self.api_key_edit.text().strip(),
            logger=write_debug_log,
        )
        self.ctx.upload_manager = UploadManager(self.ctx.client, parent=self)

        self._poller: CachePoller | None = None
        self._storage_timer: QTimer | None = None

        install_upload(self, self.ctx)
        install_update_controller(self, self.ctx)
        setup_tray(self, self.ctx)
        self._build_ui()
        load_settings(self)
        try:
            app = QApplication.instance()
            if app:
                app.installEventFilter(self)
        except Exception:
            pass

    # ── UI construction ─────────────────────────────────────────────────────

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
        upload_tab = build_upload_tab(self)
        settings_tab = build_settings_tab(self)
        self.global_conc_spin.valueChanged.connect(
            lambda v: self.ctx.upload_manager.set_concurrency(v)
        )

        self.files_tab = FilesBrowserTab(
            client=self.ctx.client,
            get_upload_path=lambda: self.upload_path_edit.text().strip(),
            set_upload_path=lambda p: self.upload_path_edit.setText(p),
        )
        self.remote_tab = RemoteTab(
            client=self.ctx.client,
            on_ingest_done=self._on_upload_done,
            on_share_created=self._on_share_created,
        )
        self.shares_tab = SharesTab(
            client=self.ctx.client,
        )
        self.sync_tab = SyncTab(
            client=self.ctx.client,
            get_sync_settings=lambda: (
                1,
                self.sync_chunk_spin.value(),
                self.sync_maxchunk_spin.value(),
            ),
            get_debug=lambda: self.debug_cb.isChecked(),
            upload_manager=self.ctx.upload_manager,
        )

        self.mass_upload_section = MassUploadSection(
            client=self.ctx.client,
            get_mass_settings=lambda: (
                1,
                self.mass_chunk_spin.value(),
                self.mass_maxchunk_spin.value(),
            ),
            get_debug=lambda: self.debug_cb.isChecked(),
            on_upload_done=self._on_upload_done,
            embedded=True,
            upload_manager=self.ctx.upload_manager,
        )
        try:
            self._upload_main_layout.addWidget(self.mass_upload_section)
        except Exception:
            upload_tab.layout().addWidget(self.mass_upload_section)

        try:
            self._set_upload_mode("single")
        except Exception:
            pass

        self.tabs.addTab(upload_tab, "Upload")
        self.tabs.addTab(self.remote_tab, "Remote")
        self.tabs.addTab(self.files_tab, "Files")
        self.tabs.addTab(self.shares_tab, "Shares")
        self.tabs.addTab(self.sync_tab, "Sync")
        self.tabs.addTab(settings_tab, "Settings")

        # ── Remote cache poller ─────────────────────────────────────────────
        self._poller = CachePoller(self.ctx.client, self)
        self._poller.add("shares")
        self._poller.add("list", path="/")
        self.files_tab.attach_cache_poller(self._poller)
        self.shares_tab.attach_cache_poller(self._poller)

        # ── Storage capacity indicator ──────────────────────────────────────
        self._storage_timer = QTimer(self)
        self._storage_timer.setInterval(30_000)
        self._storage_timer.timeout.connect(self._refresh_storage)
        self._storage_timer.start()
        QTimer.singleShot(300, self._refresh_storage)

        _tab_icons = [
            ("upload", get_accent()),
            ("download-cloud", get_accent()),
            ("folder", get_accent()),
            ("share-2", get_accent()),
            ("refresh-cw", get_accent()),
            ("settings", get_accent()),
        ]
        for i, (icon_name, color) in enumerate(_tab_icons):
            self.tabs.setTabIcon(i, lucide_icon(icon_name, color, 14))
        self.tabs.setIconSize(QSize(14, 14))
        self.tabs.currentChanged.connect(self._on_tab_changed)

    # ── Settings passthrough ────────────────────────────────────────────────

    def _load_settings(self):
        load_settings(self)

    def _save_settings(self):
        save_settings(self)

    # ── Tab switching ───────────────────────────────────────────────────────

    def _on_tab_changed(self, index: int):
        self.remote_tab.set_active(index == 1)
        if not self.ctx.client.has_api_key:
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

    # ── Window chrome (delegate to window_chrome.py) ────────────────────────

    def eventFilter(self, obj, event):
        if _chrome_event_filter(self, obj, event):
            return True
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        try:
            apply_window_rounding(self)
        except Exception:
            pass
        super().resizeEvent(event)

    def showEvent(self, event):
        try:
            apply_window_rounding(self)
        except Exception:
            pass
        super().showEvent(event)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowStateChange:
            try:
                if getattr(self, "titlebar", None):
                    self.titlebar._sync_max_icon()
            except Exception:
                pass
            try:
                QTimer.singleShot(0, lambda: apply_window_rounding(self))
            except Exception:
                pass
            if self.isMinimized() and self._tray_enabled():
                QTimer.singleShot(0, self.hide)
        super().changeEvent(event)

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self._tray_enabled() and not self.ctx.quitting:
            event.ignore()
            self.hide()
            if self.ctx.tray_icon:
                self.ctx.tray_icon.showMessage(
                    APP_NAME,
                    "Mocha Tools is still running in the system tray.",
                    QSystemTrayIcon.MessageIcon.Information,
                    2000,
                )
            return

        save_settings(self)
        if hasattr(self, "remote_tab"):
            self.remote_tab.set_active(False)
        if hasattr(self, "sync_tab"):
            self.sync_tab.closeEvent(event)
        if self._poller:
            self._poller.stop()
        if self._storage_timer:
            self._storage_timer.stop()
        if self.ctx.tray_tooltip_timer:
            self.ctx.tray_tooltip_timer.stop()
        if self.ctx.storage_worker:
            self.ctx.storage_worker.quit()
        for w in list(getattr(self.remote_tab, "_workers", [])):
            w.quit()
        for w in list(getattr(self.files_tab, "_workers", [])):
            w.quit()
        for w in list(getattr(self.shares_tab, "_workers", [])):
            w.quit()
        if self.ctx.tray_icon:
            self.ctx.tray_icon.hide()
        super().closeEvent(event)
        app = QApplication.instance()
        if app is not None:
            app.quit()


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from .entrypoint import main

    main()
