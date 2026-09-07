"""app.py — MochaTools main window and orchestrator.

MochaTools is the application shell.  Tab content lives in
src/tabs/, shared widgets in src/ui/,
and subsystems are factored out to:

  settings.py        - Settings tab UI + persistence
  upload_manager.py  - Upload tab construction + single-file upload flow
  tray_manager.py    - System tray icon, context menu, live tooltip
  update_controller.py - Check / download / install / restart
  window_chrome.py   - Frameless resize, rounding, cursor logic
  entrypoint.py      - main(), palette, theme/signal wiring
  utils.py           - Pure helper functions

Shared mutable state between modules lives in the ``AppContext``
dataclass (``self.ctx``), avoiding the need for every module to
reach into ``win`` for cross-cutting state.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QByteArray,
    QEvent,
    QObject,
    QRectF,
    QSize,
    Qt,
    QThread,
    QTimer,
)
from PySide6.QtGui import (
    QColor,
    QMoveEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QShowEvent,
)
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
from .providers import (
    WidgetApiKeyProvider,
    WidgetMassSettingsProvider,
    WidgetSyncSettingsProvider,
    WidgetUploadPathProvider,
)
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
from .window_chrome import event_filter as _chrome_event_filter

if TYPE_CHECKING:
    from PySide6.QtGui import QCloseEvent

    from .workers import StorageWorker

# ── Shared mutable state ─────────────────────────────────────────────────────

# Main window tab indices (order set in _build_tabs)
_TAB_FILES = 2
_TAB_SHARES = 3


@dataclass
class AppContext:
    """Cross-module mutable state, replacing the scattered win._xxx fields.

    Modules receive ``ctx`` alongside ``win`` and read/write these fields
    instead of attaching ad-hoc attributes to the window object.
    """

    # Upload runtime state
    is_uploading: bool = False
    last_speed_bps: float = 0.0
    last_speed_ts: float = 0.0
    last_bytes_done: int = 0
    last_bytes_total: int = 0
    upload_grand_total: int = 0
    selected_files: list[str] = field(default_factory=list)
    selected_root: str = ""
    share_result_url: str = ""

    # Shared API client (all HTTP goes through this)
    client: MochaClient | None = None

    # Worker references (transient — set during operations)
    storage_worker: StorageWorker | None = None

    # Shared upload pipeline (queue + concurrency + worker lifecycle)
    upload_manager: UploadManager | None = None

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

    # Test-update fetch thread (--test-update flag only)
    _test_fetch_thread: QThread | None = None


class MochaTools(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Mocha Tools")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._corner_radius = 12
        self._resize_margin = 12
        self._resize_cursor_active = False
        self._titlebar_dragging = False
        self._native_hit_test = False
        self._adj_cache: set[str] = set()
        self._adj_last_check: float = 0.0
        self._adj_fallback = QTimer(self)
        self._adj_fallback.setInterval(2000)
        self._adj_fallback.timeout.connect(self._refresh_adjacency)
        self.setMouseTracking(True)
        self.setMinimumWidth(520)
        self.setMinimumHeight(600)
        self.resize(760, 900)

        self.ctx = AppContext()

        self._poller: CachePoller | None = None
        self._storage_timer: QTimer | None = None

        install_upload(self, self.ctx)
        install_update_controller(self, self.ctx)
        setup_tray(self, self.ctx)

        self.ctx.client = MochaClient(
            api_key_provider=WidgetApiKeyProvider(self),
            logger=write_debug_log,
        )
        self.ctx.upload_manager = UploadManager(self.ctx.client, parent=self)

        self._build_ui()
        load_settings(self)
        try:
            app = QApplication.instance()
            if app:
                app.installEventFilter(self)
        except (AttributeError, TypeError, RuntimeError) as e:
            write_debug_log(f"[Silenced] __init__: {e}")

    # ── UI construction ─────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)

        root_lay = QVBoxLayout(root)
        # Inset the content by the border width so child widgets (titlebar,
        # tab bar, tab pages) don't paint over the window's rounded border.
        root_lay.setContentsMargins(1, 1, 1, 1)
        root_lay.setSpacing(0)

        self.titlebar = CustomTitleBar(self, APP_NAME, APP_VERSION)
        root_lay.addWidget(self.titlebar)

        self.tabs = FullWidthTabWidget()
        root_lay.addWidget(self.tabs)

        # Build each tab
        upload_tab = build_upload_tab(self)
        settings_tab = build_settings_tab(self)
        self.global_conc_spin.valueChanged.connect(
            self.ctx.upload_manager.set_concurrency,
        )

        self.files_tab = FilesBrowserTab(
            client=self.ctx.client,
            upload_path_provider=WidgetUploadPathProvider(self.upload_path_edit),
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
            sync_settings_provider=WidgetSyncSettingsProvider(
                self.sync_chunk_spin,
                self.sync_maxchunk_spin,
                self.debug_cb,
            ),
            upload_manager=self.ctx.upload_manager,
        )

        self.mass_upload_section = MassUploadSection(
            client=self.ctx.client,
            mass_settings_provider=WidgetMassSettingsProvider(
                self.mass_chunk_spin,
                self.mass_maxchunk_spin,
                self.debug_cb,
            ),
            on_upload_done=self._on_upload_done,
            embedded=True,
            upload_manager=self.ctx.upload_manager,
        )
        try:
            self._upload_main_layout.addWidget(self.mass_upload_section)
        except (AttributeError, TypeError, RuntimeError) as e:
            write_debug_log(f"[Silenced] _build_ui: {e}")
            upload_tab.layout().addWidget(self.mass_upload_section)

        with contextlib.suppress(Exception):
            self._set_upload_mode("single")

        self.tabs.addTab(upload_tab, "Upload")
        self.tabs.addTab(self.remote_tab, "Remote")
        self.tabs.addTab(self.files_tab, "Files")
        self.tabs.addTab(self.shares_tab, "Shares")
        self.tabs.addTab(self.sync_tab, "Sync")
        self.tabs.addTab(settings_tab, "Settings")

        # ── Remote cache poller ─────────────────────────────────────────────
        self._poller = CachePoller(self.ctx.client, self)
        self.files_tab.attach_cache_poller(self._poller)
        self.shares_tab.attach_cache_poller(self._poller)

        # ── Storage capacity indicator ──────────────────────────────────────
        self._storage_timer = QTimer(self)
        self._storage_timer.setInterval(30_000)
        self._storage_timer.timeout.connect(self._refresh_storage)
        self._storage_timer.start()
        QTimer.singleShot(300, self._refresh_storage)

        tab_icons = [
            ("upload", get_accent()),
            ("download-cloud", get_accent()),
            ("folder", get_accent()),
            ("share-2", get_accent()),
            ("refresh-cw", get_accent()),
            ("settings", get_accent()),
        ]
        for i, (icon_name, color) in enumerate(tab_icons):
            self.tabs.setTabIcon(i, lucide_icon(icon_name, color, 14))
        self.tabs.setIconSize(QSize(14, 14))
        self.tabs.currentChanged.connect(self._on_tab_changed)

    # ── Settings passthrough ────────────────────────────────────────────────

    def _load_settings(self) -> None:
        load_settings(self)

    def _save_settings(self) -> None:
        save_settings(self)

    # ── Tab switching ───────────────────────────────────────────────────────

    def _on_tab_changed(self, index: int) -> None:
        self.remote_tab.set_active(index == 1)
        if not self.ctx.client.has_api_key:
            return
        if self._poller:
            if index == _TAB_FILES:
                self._poller.add("list", path=self.files_tab.current_path)
                self._poller.add("shares")
                self._poller.start()
            elif index == _TAB_SHARES:
                self._poller.remove("list", path=self.files_tab.current_path)
                self._poller.add("shares")
                self._poller.start()
            else:
                self._poller.remove("list", path=self.files_tab.current_path)
                self._poller.remove("shares")
        if index == _TAB_FILES:
            self.files_tab._navigate(self.files_tab.current_path)
        elif index == _TAB_SHARES:
            stale = cache.get("shares")
            if stale is not None:
                self.shares_tab._cache = stale
                self.shares_tab._render(stale)
                self.shares_tab._status("Refreshing…")
        elif index not in {2, 6}:
            save_settings(self)

    # ── Window chrome (delegate to window_chrome.py) ────────────────────────

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if _chrome_event_filter(self, obj, event):
            return True
        return super().eventFilter(obj, event)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        from .window_chrome import enable_native_snap, install_window_hook

        enable_native_snap(self)
        if not install_window_hook(self):
            self._adj_fallback.start()

    def nativeEvent(
        self,
        eventType: QByteArray | bytes | bytearray | memoryview,
        message: int,
    ) -> tuple[bool, int]:
        from typing import cast

        from .window_chrome import handle_native_message

        result = handle_native_message(self, bytes(eventType), message)
        if result is not None:
            return result
        return cast("tuple[bool, int]", super().nativeEvent(eventType, message))

    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint the rounded window background with anti-aliased corners.
        Corners touching a screen edge (snapped / maximised) are squared off."""
        try:
            from .theme import get_background_palette

            pal = get_background_palette()
            bg0 = QColor(pal["bg0"])
            border = QColor(pal["border2"])
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            if self.isMaximized() or self.isFullScreen():
                painter.fillRect(self.rect(), bg0)
            else:
                tl, tr, bl, br = self._corner_radii()
                path = self._rounded_rect_path(
                    QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                    tl,
                    tr,
                    bl,
                    br,
                )
                painter.fillPath(path, bg0)
                pen = painter.pen()
                pen.setColor(border)
                pen.setWidth(1)
                painter.setPen(pen)
                painter.drawPath(path)
            painter.end()
        except (AttributeError, TypeError, RuntimeError, ValueError) as e:
            write_debug_log(f"[Silenced] paintEvent: {e}")
        super().paintEvent(event)

    def _corner_radii(self) -> tuple[float, float, float, float]:
        """Return (top-left, top-right, bottom-left, bottom-right) corner radii
        based on the window's snap state.  Corners whose adjacent edge touches
        the work area or sits flush against another visible window are squared
        off; maximised is fully square."""
        r = float(getattr(self, "_corner_radius", 12))
        if self.isMaximized() or self.isFullScreen():
            return (0.0, 0.0, 0.0, 0.0)
        screen = self.screen()
        if screen is None:
            return (r, r, r, r)
        avail = screen.availableGeometry()
        geo = self.geometry()
        tol = 2
        top = abs(geo.top() - avail.top()) <= tol
        bottom = abs(geo.bottom() - avail.bottom()) <= tol
        left = abs(geo.left() - avail.left()) <= tol
        right = abs(geo.right() - avail.right()) <= tol

        adj = self._adj_cache
        top = top or ("top" in adj)
        bottom = bottom or ("bottom" in adj)
        left = left or ("left" in adj)
        right = right or ("right" in adj)

        return (
            0.0 if (top or left) else r,
            0.0 if (top or right) else r,
            0.0 if (bottom or left) else r,
            0.0 if (bottom or right) else r,
        )

    def moveEvent(self, event: QMoveEvent) -> None:
        """Re-evaluate corner rounding on move (Aero Snap drags don't repaint
        on their own)."""
        super().moveEvent(event)
        self._refresh_adjacency()
        self.update()

    def _refresh_adjacency(self) -> None:
        """Recompute which edges sit flush against another visible window and
        repaint only when the set changes.  Throttled; triggered on move and
        by the WinEvent hook when other windows move."""
        if not self.isVisible():
            return
        import time

        from .window_chrome import adjacent_window_edges

        _ADJ_REFRESH_INTERVAL = 0.1
        now = time.monotonic()
        if now - self._adj_last_check < _ADJ_REFRESH_INTERVAL:
            return
        self._adj_last_check = now
        new = adjacent_window_edges(self, self.geometry())
        if new != self._adj_cache:
            self._adj_cache = new
            self.update()

    def _rounded_rect_path(
        self,
        rect: QRectF,
        tl: float,
        tr: float,
        bl: float,
        br: float,
    ) -> QPainterPath:
        """Build a rounded-rect QPainterPath with an independent radius per corner."""
        path = QPainterPath()
        if tl == 0 and tr == 0 and bl == 0 and br == 0:
            path.addRect(rect)
            return path
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        max_r = min(w, h) / 2
        tl = min(tl, max_r)
        tr = min(tr, max_r)
        bl = min(bl, max_r)
        br = min(br, max_r)
        path.moveTo(x + tl, y)
        path.lineTo(x + w - tr, y)
        if tr > 0:
            path.arcTo(x + w - 2 * tr, y, 2 * tr, 2 * tr, 90, -90)
        path.lineTo(x + w, y + h - br)
        if br > 0:
            path.arcTo(x + w - 2 * br, y + h - 2 * br, 2 * br, 2 * br, 0, -90)
        path.lineTo(x + bl, y + h)
        if bl > 0:
            path.arcTo(x, y + h - 2 * bl, 2 * bl, 2 * bl, -90, -90)
        path.lineTo(x, y + tl)
        if tl > 0:
            path.arcTo(x, y, 2 * tl, 2 * tl, 180, -90)
        path.closeSubpath()
        return path

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.WindowStateChange:
            try:
                if getattr(self, "titlebar", None):
                    self.titlebar._sync_max_icon()
            except (AttributeError, TypeError, RuntimeError) as e:
                write_debug_log(f"[Silenced] changeEvent: {e}")
            if self.isMinimized() and self._tray_enabled():
                QTimer.singleShot(0, self.hide)
            self._refresh_adjacency()
            self.update()
        super().changeEvent(event)

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def closeEvent(self, event: QCloseEvent) -> None:
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
        from .window_chrome import uninstall_window_hook

        uninstall_window_hook(self)
        self._adj_fallback.stop()
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
