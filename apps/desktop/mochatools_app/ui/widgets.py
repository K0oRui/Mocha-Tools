"""ui/widgets.py — Reusable custom Qt widgets for MochaTools.

DropZone          — drag-and-drop / click-to-browse file picker
FullWidthTabWidget — tab bar that always fills the full widget width
CustomTitleBar    — frameless window titlebar with drag-to-move
"""

from __future__ import annotations

import contextlib
import os
from functools import partial
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

import pathlib

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import (
    QDesktopServices,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDropEvent,
    QIcon,
    QMouseEvent,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..logging_utils import write_debug_log
from ..workers import UploadWorker
from .icons import lucide_icon

# ── Drop Zone ─────────────────────────────────────────────────────────────────


class DropZone(QFrame):
    """Drag-and-drop / click-to-browse file/folder picker.

    Emits selection_changed(file_list, root) where root is the authoritative
    base for os.path.relpath so common-path guessing is never needed.
    """

    selection_changed = Signal(list, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("drop_zone")
        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(110)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(4)

        self._icon_label = QLabel("↑")
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        from ..theme import get_accent as _ga

        self._icon_label.setStyleSheet(
            f"color: {_ga()}; font-size: 28px; font-weight: 700; background: transparent;",
        )

        row = QHBoxLayout()
        row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.setSpacing(4)

        bold = QLabel("Click to browse")
        bold.setObjectName("drop_label_bold")
        rest = QLabel("or drag & drop a file / folder here")
        rest.setObjectName("drop_label")
        # Ensure labels that rely on stylesheet tokens update when the font changes
        try:
            from ..theme import get_font, notifier

            _fam, fsz = get_font()
            bold.setStyleSheet(f"font-size:{int(fsz)}px; background:transparent;")
            rest.setStyleSheet(f"font-size:{int(fsz)}px; background:transparent;")

            def _on_font_changed(_fam: str, sz: int) -> None:
                try:
                    bold.setStyleSheet(
                        f"font-size:{int(sz)}px; background:transparent;",
                    )
                    rest.setStyleSheet(
                        f"font-size:{int(sz)}px; background:transparent;",
                    )
                except (AttributeError, TypeError, RuntimeError, ValueError) as e:
                    write_debug_log(f"[Silenced] _on_font_changed: {e}")

            notifier().font_changed.connect(_on_font_changed)
        except (AttributeError, TypeError, RuntimeError, ImportError, ValueError) as e:
            write_debug_log(f"[Silenced] __init__: {e}")
        row.addWidget(bold)
        row.addWidget(rest)

        self.file_label = QLabel("")
        self.file_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        from ..theme import get_accent

        try:
            from ..theme import get_font

            fsz = int(get_font()[1])
        except (AttributeError, TypeError, RuntimeError, ImportError, ValueError) as e:
            write_debug_log(f"[Silenced] __init__: {e}")
            fsz = 12
        self.file_label.setStyleSheet(
            f"color: {get_accent()}; font-size: {fsz}px; font-weight:600; background:transparent;",
        )
        try:
            from ..theme import notifier

            def _on_font_changed(_fam: str, sz: int) -> None:
                with contextlib.suppress(Exception):
                    self.file_label.setStyleSheet(
                        f"color: {get_accent()}; font-size: {int(sz)}px; font-weight:600; background:transparent;",
                    )

            notifier().font_changed.connect(_on_font_changed)
        except (AttributeError, TypeError, RuntimeError, ImportError, ValueError) as e:
            write_debug_log(f"[Silenced] __init__: {e}")
        try:
            from ..theme import notifier

            def _on_accent_changed(_old: str, _new: str) -> None:
                try:
                    # Re-polish outer drop zone so stylesheet rules targeting #drop_zone update
                    self.style().unpolish(self)
                    self.style().polish(self)
                except (AttributeError, TypeError, RuntimeError) as e:
                    write_debug_log(f"[Silenced] _on_accent_changed: {e}")
                try:
                    # Refresh the file label color as it uses the accent in its stylesheet
                    self.file_label.style().unpolish(self.file_label)
                    self.file_label.style().polish(self.file_label)
                except (AttributeError, TypeError, RuntimeError) as e:
                    write_debug_log(f"[Silenced] _on_accent_changed: {e}")
                    with contextlib.suppress(Exception):
                        self.file_label.update()
                try:
                    # The arrow icon's color is baked into an inline stylesheet
                    # rather than a QSS token, so it must be rebuilt explicitly
                    # on every accent change.
                    self._icon_label.setStyleSheet(
                        f"color: {_new}; font-size: 28px; font-weight: 700; background: transparent;",
                    )
                except (AttributeError, TypeError, RuntimeError) as e:
                    write_debug_log(f"[Silenced] _on_accent_changed: {e}")

            notifier().accent_changed.connect(_on_accent_changed)
        except (AttributeError, TypeError, RuntimeError, ImportError) as e:
            write_debug_log(f"[Silenced] __init__: {e}")

        layout.addWidget(self._icon_label)
        layout.addLayout(row)
        layout.addWidget(self.file_label)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # ── Events ────────────────────────────────────────────────────────────────

    def mousePressEvent(self, _event: QMouseEvent) -> None:
        self._browse()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_drag_active(True)

    def dragLeaveEvent(self, _event: QDragLeaveEvent) -> None:
        self._set_drag_active(False)

    def dropEvent(self, event: QDropEvent) -> None:
        self._set_drag_active(False)
        urls = event.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if pathlib.Path(path).is_file():
            self._set_paths([path], str(pathlib.Path(path).parent), is_folder=False)
        elif pathlib.Path(path).is_dir():
            files = self._collect_folder(path)
            if files:
                self._set_paths(files, path, is_folder=True)

    # ── Browse menu ───────────────────────────────────────────────────────────

    def _browse(self) -> None:
        menu = QMenu(self)
        from ..theme import get_accent

        act_file = menu.addAction(
            lucide_icon("copy", get_accent(), 12),
            "Select files…",
        )
        act_folder = menu.addAction(
            lucide_icon("folder", get_accent(), 12),
            "Select folder…",
        )
        chosen = menu.exec(self.mapToGlobal(self.rect().center()))
        if chosen == act_file:
            paths, _ = QFileDialog.getOpenFileNames(self, "Select files")
            if paths:
                root = (
                    os.path.commonpath(paths)
                    if len(paths) > 1
                    else str(pathlib.Path(paths[0]).parent)
                )
                if pathlib.Path(root).is_file():
                    root = str(pathlib.Path(root).parent)
                self._set_paths(paths, root, is_folder=False)
        elif chosen == act_folder:
            path = QFileDialog.getExistingDirectory(self, "Select folder")
            if path:
                files = self._collect_folder(path)
                if files:
                    self._set_paths(files, path, is_folder=True)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_drag_active(self, active: bool) -> None:
        self.setProperty("drag_active", "true" if active else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    @staticmethod
    def _collect_folder(folder_path: str) -> list[str]:
        result = [
            str(pathlib.Path(dirpath) / fname)
            for dirpath, _dirnames, filenames in os.walk(folder_path)
            for fname in filenames
        ]
        return sorted(result)

    def _set_paths(
        self,
        file_list: list[str],
        root: str,
        is_folder: bool = False,
    ) -> None:
        if not file_list:
            return
        name = pathlib.Path(root.rstrip("/\\")).name
        if len(file_list) == 1 and not is_folder:
            size = pathlib.Path(file_list[0]).stat().st_size
            label = (
                f"{pathlib.Path(file_list[0]).name}  ({UploadWorker._fmt_size(size)})"
            )
            selected_root = root
        elif is_folder:
            total = sum(pathlib.Path(p).stat().st_size for p in file_list)
            label = (
                f"{name}/  —  {len(file_list)} files  ({UploadWorker._fmt_size(total)})"
            )
            selected_root = str(pathlib.Path(root.rstrip("/\\")).parent)
        else:
            total = sum(pathlib.Path(p).stat().st_size for p in file_list)
            label = (
                f"{len(file_list)} files selected  ({UploadWorker._fmt_size(total)})"
            )
            selected_root = root
        self.file_label.setText(label)
        self.selection_changed.emit(file_list, selected_root)


# ── Full-Width Tab Widget ─────────────────────────────────────────────────────


class FullWidthTabWidget(QWidget):
    """Drop-in QTabWidget replacement whose tab bar always fills the full widget
    width — no bare gap to the right of the last tab.
    """

    currentChanged = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tabs: list[tuple[QPushButton, QWidget]] = []
        self._current = -1

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._bar = QWidget()
        self._bar.setObjectName("tabbar_row")
        self._bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._bar_lay = QHBoxLayout(self._bar)
        self._bar_lay.setContentsMargins(0, 0, 0, 0)
        self._bar_lay.setSpacing(0)
        outer.addWidget(self._bar)

        self._stack = QStackedWidget()
        # stacked widget uses default margins so the original tab appearance is preserved
        outer.addWidget(self._stack, 1)

        # background (bar + stack) tracks the active background theme; set
        # the initial colors now, then refresh whenever theme/accent/font change
        self._refresh_bar_background()

        # update tab styles when accent changes
        try:
            from ..theme import notifier

            notifier().accent_changed.connect(self._refresh_tab_styles)
            try:
                # also refresh tab styles when the font size/family changes
                notifier().font_changed.connect(self._refresh_tab_styles)
            except (AttributeError, TypeError, RuntimeError) as e:
                write_debug_log(f"[Silenced] __init__: {e}")
            try:
                # background theme switches (Mocha/White/Black) need both the
                # bar/stack backgrounds AND the tab text colors recomputed —
                # these were previously hardcoded to mocha hex values and
                # never refreshed, which is why the tab bar stayed stuck on
                # the old theme even after the rest of the app switched.
                notifier().background_changed.connect(self._refresh_bar_background)
                notifier().background_changed.connect(self._refresh_tab_styles)
            except (AttributeError, TypeError, RuntimeError) as e:
                write_debug_log(f"[Silenced] __init__: {e}")
        except (AttributeError, TypeError, RuntimeError, ImportError) as e:
            write_debug_log(f"[Silenced] __init__: {e}")

    def _refresh_bar_background(self) -> None:
        """Rebuild the tab-bar-row and stacked-widget background/border from
        the active background theme palette instead of a hardcoded mocha hex.
        """
        try:
            from ..theme import get_background_palette

            pal = get_background_palette()
            bg0 = pal["bg0"]
            bg1 = pal["bg1"]
            border = pal["border"]
        except (AttributeError, TypeError, RuntimeError, ImportError) as e:
            write_debug_log(f"[Silenced] _refresh_bar_background: {e}")
            bg0, bg1, border = "#111010", "#181614", "#2e2b27"
        try:
            # Segmented-pill nav sits on the root background so the active
            # pill reads as a floating chip; a hairline separates it from
            # the content area below.
            self._bar.setStyleSheet(
                "QWidget#tabbar_row {"
                f"  background: {bg1};"
                f"  border-bottom: 1px solid {border};"
                "}",
            )
        except (AttributeError, TypeError, RuntimeError, ImportError) as e:
            write_debug_log(f"[Silenced] _refresh_bar_background: {e}")
        with contextlib.suppress(Exception):
            self._stack.setStyleSheet(f"QStackedWidget {{ background: {bg0}; }}")

    def addTab(self, widget: QWidget, label: str) -> int:
        idx = len(self._tabs)
        btn = QPushButton(label)
        btn.setCheckable(True)
        btn.setObjectName("tab_btn")
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn.setStyleSheet(self._btn_style(False))
        btn.clicked.connect(partial(self._select_tab, idx))
        self._bar_lay.addWidget(btn)
        self._stack.addWidget(widget)
        self._tabs.append((btn, widget))
        if idx == 0:
            self.setCurrentIndex(0)
        return idx

    def setTabIcon(self, index: int, icon: QIcon) -> None:
        if 0 <= index < len(self._tabs):
            self._tabs[index][0].setIcon(icon)
            self._tabs[index][0].setIconSize(QSize(14, 14))

    # Compat shims so callers don't need to know this isn't a real QTabWidget
    def setIconSize(self, size: QSize) -> None:
        pass

    def tabBar(self) -> FullWidthTabWidget:
        return self

    def setExpanding(self, _: bool) -> None:
        pass

    def setDrawBase(self, _: bool) -> None:
        pass

    def setCornerWidget(self, *_: QWidget | None) -> None:
        pass

    def currentIndex(self) -> int:
        return self._current

    def setCurrentIndex(self, index: int) -> None:
        if index == self._current:
            return
        old = self._current
        self._current = index
        for i, (btn, _) in enumerate(self._tabs):
            active = i == index
            btn.setChecked(active)
            btn.setStyleSheet(self._btn_style(active))
        self._stack.setCurrentIndex(index)
        if old != index:
            self.currentChanged.emit(index)
        # ensure button styles reflect any possible accent change
        self._refresh_tab_styles()

    def _select_tab(self, index: int, _checked: bool = False) -> None:
        self.setCurrentIndex(index)

    def _refresh_tab_styles(self) -> None:
        for i, (btn, _) in enumerate(self._tabs):
            active = i == self._current
            btn.setStyleSheet(self._btn_style(active))

    @staticmethod
    def _btn_style(active: bool) -> str:
        # Build tab button CSS dynamically from current accent + background
        # theme so the tab bar updates immediately when either changes.
        #
        # Modern "segmented pill" navigation: the active tab is a rounded,
        # accent-tinted pill with accent text; inactive tabs are quiet and
        # light up softly on hover. Replaces the old underline tab style.
        try:
            from ..styles import compute_accent_variants
            from ..theme import get_accent, get_font

            acc, _hov, _ = compute_accent_variants(get_accent())
            _fam, fsz = get_font()
        except (AttributeError, TypeError, RuntimeError, ImportError) as e:
            write_debug_log(f"[Silenced] _btn_style: {e}")
            from ..styles import compute_accent_variants
            from ..theme import DEFAULT_ACCENT, DEFAULT_FONT_SIZE

            acc, _hov, _ = compute_accent_variants(DEFAULT_ACCENT)
            fsz = DEFAULT_FONT_SIZE

        try:
            from ..theme import get_background_palette

            pal = get_background_palette()
            pal["text_dim"]
            text_muted = pal["text_muted"]
            border2 = pal["border2"]
            pal["bg3"]
        except (AttributeError, TypeError, RuntimeError, ImportError) as e:
            write_debug_log(f"[Silenced] _btn_style: {e}")
            _text_dim, text_muted, border2, _bg3 = (
                "#5a5650",
                "#9c9484",
                "#3d3a35",
                "#1e1c19",
            )

        # Derive accent-tinted rgba fills for the active pill + hover state.
        def _rgba(hex_str: str, alpha: int) -> str:
            try:
                h = hex_str.lstrip("#")
                r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            except (AttributeError, TypeError, RuntimeError, ValueError) as e:
                write_debug_log(f"[Silenced] _rgba: {e}")
                return hex_str
            else:
                return f"rgba({r}, {g}, {b}, {alpha})"

        pill = _rgba(acc, 34)  # active pill fill (~13% accent)
        pill_hover = _rgba(acc, 48)
        hover_bg = _rgba(border2, 45)  # inactive hover fill

        if active:
            return (
                f"QPushButton {{ background:{pill}; color:{acc}; border:none;"
                f" border-radius:9px; padding:9px 20px; margin:6px 3px;"
                f" font-size:{int(fsz)}px; font-weight:700; letter-spacing:0.2px; }}"
                f"QPushButton:hover {{ background:{pill_hover}; }}"
            )
        return (
            f"QPushButton {{ background:transparent; color:{text_muted}; border:none;"
            f" border-radius:9px; padding:9px 20px; margin:6px 3px;"
            f" font-size:{int(fsz)}px; font-weight:600; letter-spacing:0.2px; }}"
            f"QPushButton:hover {{ background:{hover_bg}; color:{acc}; }}"
        )


# ── Custom Title Bar ──────────────────────────────────────────────────────────


class CustomTitleBar(QFrame):
    """Frameless window titlebar with drag-to-move, minimise, maximise, and close."""

    def __init__(
        self,
        window: QMainWindow,
        app_name: str,
        version: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._window = window
        self.setObjectName("titlebar")
        self.setFixedHeight(34)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 6, 0)
        lay.setSpacing(0)

        # Coffee icon (clickable) + app name — keep original QLabel appearance
        self._icon_lbl = QLabel()
        from ..theme import get_accent

        self._icon_lbl.setPixmap(
            lucide_icon("coffee", get_accent(), 15).pixmap(QSize(15, 15)),
        )
        self._icon_lbl.setStyleSheet("background:transparent; padding-right:6px;")
        self._icon_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self._icon_lbl.setToolTip("Open https://mocha.my")

        def _icon_clicked(event: QMouseEvent) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                QDesktopServices.openUrl(QUrl("https://mocha.my"))

        self._icon_lbl.mousePressEvent = _icon_clicked
        lay.addWidget(self._icon_lbl)

        name_lbl = QLabel(app_name)
        name_lbl.setObjectName("title_app_name")
        lay.addWidget(name_lbl)

        sep = QLabel(" ")
        sep.setStyleSheet("background:transparent;")
        lay.addWidget(sep)

        ver_lbl = QLabel(version)
        ver_lbl.setObjectName("title_version")
        lay.addWidget(ver_lbl)

        lay.addStretch()

        self._eta_lbl = QLabel("")
        self._eta_lbl.setObjectName("title_eta")
        self._eta_lbl.setStyleSheet(
            "background:transparent; margin-bottom:3px; margin-right:10px;",
        )
        self._eta_lbl.hide()
        lay.addWidget(self._eta_lbl)

        self._storage_lbl = QLabel("")
        self._storage_lbl.setObjectName("title_storage")
        self._storage_lbl.setStyleSheet("background:transparent; margin-bottom:3px;")
        self._storage_lbl.hide()
        lay.addWidget(self._storage_lbl)

        # Detect whether the parent window still uses a hand-drawn frame.
        # With a normal native OS frame the operating system already provides
        # minimise/maximise/close buttons plus drag-to-move, so our own copies
        # would be redundant. We only show them in the legacy frameless mode.
        try:
            self._native_frame = not bool(
                window.windowFlags() & Qt.WindowType.FramelessWindowHint,
            )
        except (AttributeError, TypeError, RuntimeError) as e:
            write_debug_log(f"[Silenced] __init__: {e}")
            self._native_frame = False

        self._min_btn = self._make_btn(
            "tb_minmax",
            "minus",
            "#5a5650",
            13,
            "Minimise",
            window.showMinimized,
        )
        self._max_btn = self._make_btn(
            "tb_minmax",
            "square",
            "#5a5650",
            11,
            "Maximise",
            self._toggle_maximise,
        )
        self._cls_btn = self._make_btn(
            "tb_close",
            "x",
            "#5a5650",
            13,
            "Close",
            window.close,
        )

        if self._native_frame:
            # Native frame: the OS draws the window controls, so hide ours and
            # keep this bar as a slim in-app header (icon, name, version, and
            # the storage / ETA indicators).
            for btn in (self._min_btn, self._max_btn, self._cls_btn):
                btn.hide()
        else:
            for btn in (self._min_btn, self._max_btn, self._cls_btn):
                lay.addWidget(btn)

    def _make_btn(
        self,
        obj_name: str,
        icon_name: str,
        color: str,
        icon_size: int,
        tooltip: str,
        slot: Callable[[], object],
    ) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName(obj_name)
        btn.setIcon(lucide_icon(icon_name, color, icon_size))
        btn.setIconSize(QSize(icon_size, icon_size))
        btn.setToolTip(tooltip)
        btn.clicked.connect(slot)
        return btn

    # ── ETA indicator ──────────────────────────────────────────────────────────

    def set_eta_text(self, text: str) -> None:
        """Update the upload ETA label shown in the titlebar, before the
        storage indicator. Pass an empty string to hide it.
        """
        self._eta_lbl.setText(text)
        self._eta_lbl.setVisible(bool(text))

    # ── Storage indicator ─────────────────────────────────────────────────────

    def set_storage_text(self, text: str) -> None:
        """Update the storage-capacity label shown before the minimise button."""
        self._storage_lbl.setText(text)
        self._storage_lbl.setVisible(bool(text))

    # ── Maximise / restore ────────────────────────────────────────────────────

    def _toggle_maximise(self) -> None:
        # NOTE: the old frameless mode capped the window at 640px wide and had
        # to lift the cap before maximising. The app now uses a native frame
        # with no width cap, so we simply toggle maximise/restore.
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()
        self._sync_max_icon()

    def _sync_max_icon(self) -> None:
        if self._window.isMaximized():
            self._max_btn.setToolTip("Restore")
            self._max_btn.setIcon(lucide_icon("square", "#9c9484", 11))
        else:
            self._max_btn.setToolTip("Maximise")
            self._max_btn.setIcon(lucide_icon("square", "#5a5650", 11))

    def _refresh_icons(self) -> None:
        """Called by app to refresh titlebar icons when the accent changes."""
        try:
            from ..theme import get_accent

            acc = get_accent()
            # update coffee icon (keep it tinted with accent)
            with contextlib.suppress(Exception):
                self._icon_lbl.setPixmap(
                    lucide_icon("coffee", acc, 15).pixmap(QSize(15, 15)),
                )
            # refresh min/max/close icons as well
            try:
                self._min_btn.setIcon(lucide_icon("minus", "#5a5650", 13))
                self._max_btn.setIcon(lucide_icon("square", "#5a5650", 11))
                self._cls_btn.setIcon(lucide_icon("x", "#5a5650", 13))
            except (AttributeError, TypeError, RuntimeError) as e:
                write_debug_log(f"[Silenced] _refresh_icons: {e}")
            try:
                self.update()
                self.repaint()
            except (AttributeError, TypeError, RuntimeError) as e:
                write_debug_log(f"[Silenced] _refresh_icons: {e}")
        except (AttributeError, TypeError, RuntimeError, ImportError) as e:
            write_debug_log(f"[Silenced] _refresh_icons: {e}")

    # ── Drag-to-move ──────────────────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:
        # With a native OS frame the system titlebar handles moving the
        # window, so this in-app header should not intercept drags.
        if getattr(self, "_native_frame", False):
            super().mousePressEvent(event)
            return
        if event.button() == Qt.MouseButton.LeftButton:
            with contextlib.suppress(Exception):
                self._window._titlebar_dragging = True
            # startSystemMove() works on both X11 and Wayland.
            # Manual move() calls are silently ignored by Wayland compositors,
            # so the old _drag_pos approach only ever worked on X11.
            win = self._window.windowHandle()
            if win is not None:
                with contextlib.suppress(Exception):
                    win.startSystemMove()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if getattr(self, "_native_frame", False):
            super().mouseMoveEvent(event)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if getattr(self, "_native_frame", False):
            super().mouseReleaseEvent(event)
            return
        with contextlib.suppress(Exception):
            self._window._titlebar_dragging = False
        try:
            if (
                event.button() == Qt.MouseButton.LeftButton
                and not self._window.isMaximized()
            ):
                try:
                    gp = event.globalPosition().toPoint()
                except (AttributeError, TypeError, RuntimeError) as e:
                    write_debug_log(f"[Silenced] mouseReleaseEvent: {e}")
                    gp = event.globalPos()
                screen = self._window.screen()
                if screen is not None:
                    top = screen.availableGeometry().top()
                    if gp.y() <= top + 3:
                        self._window.showMaximized()
                        self._sync_max_icon()
        except (AttributeError, TypeError, RuntimeError) as e:
            write_debug_log(f"[Silenced] mouseReleaseEvent: {e}")
        event.accept()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        # Native frame: let the OS handle double-click-to-maximise on its own
        # titlebar; our in-app header should stay passive.
        if getattr(self, "_native_frame", False):
            super().mouseDoubleClickEvent(event)
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximise()
        super().mouseDoubleClickEvent(event)
