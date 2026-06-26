"""
ui/widgets.py — Reusable custom Qt widgets for MochaTools.

  DropZone          — drag-and-drop / click-to-browse file picker
  FullWidthTabWidget — tab bar that always fills the full widget width
  CustomTitleBar    — frameless window titlebar with drag-to-move
"""

import os

from PyQt6.QtCore import Qt, QSize, pyqtSignal, QUrl
from PyQt6.QtGui import QColor, QDragEnterEvent, QDropEvent, QDesktopServices
from PyQt6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QMenu, QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from .icons import lucide_icon
from ..workers import UploadWorker


# ── Drop Zone ─────────────────────────────────────────────────────────────────

class DropZone(QFrame):
    """
    Drag-and-drop / click-to-browse file/folder picker.

    Emits selection_changed(file_list, root) where root is the authoritative
    base for os.path.relpath so common-path guessing is never needed.
    """

    selection_changed = pyqtSignal(list, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("drop_zone")
        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(110)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(4)

        icon = QLabel("↑")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("color: #4a4a4a; font-size: 24px; background: transparent;")

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
            fam, fsz = get_font()
            bold.setStyleSheet(f"font-size:{int(fsz)}px; background:transparent;")
            rest.setStyleSheet(f"font-size:{int(fsz)}px; background:transparent;")
            def _on_font_changed(_fam, sz):
                try:
                    bold.setStyleSheet(f"font-size:{int(sz)}px; background:transparent;")
                    rest.setStyleSheet(f"font-size:{int(sz)}px; background:transparent;")
                except Exception:
                    pass
            notifier().font_changed.connect(_on_font_changed)
        except Exception:
            pass
        row.addWidget(bold)
        row.addWidget(rest)

        self.file_label = QLabel("")
        self.file_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        from ..theme import get_accent
        try:
            from ..theme import get_font
            fsz = int(get_font()[1])
        except Exception:
            fsz = 12
        self.file_label.setStyleSheet(
            f"color: {get_accent()}; font-size: {fsz}px; font-weight:600; background:transparent;"
        )
        try:
            from ..theme import notifier
            def _on_font_changed(_fam, sz):
                try:
                    self.file_label.setStyleSheet(
                        f"color: {get_accent()}; font-size: {int(sz)}px; font-weight:600; background:transparent;"
                    )
                except Exception:
                    pass
            notifier().font_changed.connect(_on_font_changed)
        except Exception:
            pass
        try:
            from ..theme import notifier
            def _on_accent_changed(_old, _new):
                try:
                    # Re-polish outer drop zone so stylesheet rules targeting #drop_zone update
                    self.style().unpolish(self)
                    self.style().polish(self)
                except Exception:
                    pass
                try:
                    # Refresh the file label color as it uses the accent in its stylesheet
                    self.file_label.style().unpolish(self.file_label)
                    self.file_label.style().polish(self.file_label)
                except Exception:
                    try:
                        self.file_label.update()
                    except Exception:
                        pass
            notifier().accent_changed.connect(_on_accent_changed)
        except Exception:
            pass

        layout.addWidget(icon)
        layout.addLayout(row)
        layout.addWidget(self.file_label)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # ── Events ────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        self._browse()

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_drag_active(True)

    def dragLeaveEvent(self, event):
        self._set_drag_active(False)

    def dropEvent(self, event: QDropEvent):
        self._set_drag_active(False)
        urls = event.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if os.path.isfile(path):
            self._set_paths([path], os.path.dirname(path), is_folder=False)
        elif os.path.isdir(path):
            files = self._collect_folder(path)
            if files:
                self._set_paths(files, path, is_folder=True)

    # ── Browse menu ───────────────────────────────────────────────────────────

    def _browse(self):
        menu = QMenu(self)
        from ..theme import get_accent
        act_file   = menu.addAction(lucide_icon("copy", get_accent(), 12), "Select files…")
        act_folder = menu.addAction(lucide_icon("folder", get_accent(), 12), "Select folder…")
        chosen = menu.exec(self.mapToGlobal(self.rect().center()))
        if chosen == act_file:
            paths, _ = QFileDialog.getOpenFileNames(self, "Select files")
            if paths:
                root = os.path.commonpath(paths) if len(paths) > 1 else os.path.dirname(paths[0])
                if os.path.isfile(root):
                    root = os.path.dirname(root)
                self._set_paths(paths, root, is_folder=False)
        elif chosen == act_folder:
            path = QFileDialog.getExistingDirectory(self, "Select folder")
            if path:
                files = self._collect_folder(path)
                if files:
                    self._set_paths(files, path, is_folder=True)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_drag_active(self, active: bool):
        self.setProperty("drag_active", "true" if active else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    @staticmethod
    def _collect_folder(folder_path: str) -> list[str]:
        result = []
        for dirpath, _dirnames, filenames in os.walk(folder_path):
            for fname in filenames:
                result.append(os.path.join(dirpath, fname))
        return sorted(result)

    def _set_paths(self, file_list: list[str], root: str, is_folder: bool = False):
        if not file_list:
            return
        name = os.path.basename(root.rstrip("/\\"))
        if len(file_list) == 1 and not is_folder:
            size  = os.path.getsize(file_list[0])
            label = f"{os.path.basename(file_list[0])}  ({UploadWorker._fmt_size(size)})"
            selected_root = root
        elif is_folder:
            total = sum(os.path.getsize(p) for p in file_list)
            label = f"{name}/  —  {len(file_list)} files  ({UploadWorker._fmt_size(total)})"
            selected_root = os.path.dirname(root.rstrip("/\\"))
        else:
            total = sum(os.path.getsize(p) for p in file_list)
            label = f"{len(file_list)} files selected  ({UploadWorker._fmt_size(total)})"
            selected_root = root
        self.file_label.setText(label)
        self.selection_changed.emit(file_list, selected_root)


# ── Full-Width Tab Widget ─────────────────────────────────────────────────────

class FullWidthTabWidget(QWidget):
    """
    Drop-in QTabWidget replacement whose tab bar always fills the full widget
    width — no bare gap to the right of the last tab.
    """

    currentChanged = pyqtSignal(int)

    def __init__(self, parent=None):
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
            notifier().accent_changed.connect(lambda _old, _new: self._refresh_tab_styles())
            try:
                # also refresh tab styles when the font size/family changes
                notifier().font_changed.connect(lambda _fam, _sz: self._refresh_tab_styles())
            except Exception:
                pass
            try:
                # background theme switches (Mocha/White/Black) need both the
                # bar/stack backgrounds AND the tab text colors recomputed —
                # these were previously hardcoded to mocha hex values and
                # never refreshed, which is why the tab bar stayed stuck on
                # the old theme even after the rest of the app switched.
                notifier().background_changed.connect(lambda _old, _new: self._refresh_bar_background())
                notifier().background_changed.connect(lambda _old, _new: self._refresh_tab_styles())
            except Exception:
                pass
        except Exception:
            pass

    def _refresh_bar_background(self):
        """Rebuild the tab-bar-row and stacked-widget background/border from
        the active background theme palette instead of a hardcoded mocha hex."""
        try:
            from ..theme import get_background_palette
            pal = get_background_palette()
            bg1 = pal["bg1"]
            border = pal["border"]
        except Exception:
            bg1, border = "#181614", "#2e2b27"
        try:
            self._bar.setStyleSheet(
                "QWidget#tabbar_row {"
                f"  background: {bg1};"
                f"  border-bottom: 1px solid {border};"
                "}"
            )
        except Exception:
            pass
        try:
            self._stack.setStyleSheet(f"QStackedWidget {{ background: {bg1}; }}")
        except Exception:
            pass

    def addTab(self, widget: QWidget, label: str) -> int:
        idx = len(self._tabs)
        btn = QPushButton(label)
        btn.setCheckable(True)
        btn.setObjectName("tab_btn")
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn.setStyleSheet(self._btn_style(False))
        btn.clicked.connect(lambda _checked, i=idx: self.setCurrentIndex(i))
        self._bar_lay.addWidget(btn)
        self._stack.addWidget(widget)
        self._tabs.append((btn, widget))
        if idx == 0:
            self.setCurrentIndex(0)
        return idx

    def setTabIcon(self, index: int, icon):
        if 0 <= index < len(self._tabs):
            self._tabs[index][0].setIcon(icon)
            self._tabs[index][0].setIconSize(QSize(14, 14))

    # Compat shims so callers don't need to know this isn't a real QTabWidget
    def setIconSize(self, size): pass
    def tabBar(self): return self
    def setExpanding(self, _): pass
    def setDrawBase(self, _): pass
    def setCornerWidget(self, *_): pass

    def currentIndex(self) -> int:
        return self._current

    def setCurrentIndex(self, index: int):
        if index == self._current:
            return
        old = self._current
        self._current = index
        for i, (btn, _) in enumerate(self._tabs):
            active = (i == index)
            btn.setChecked(active)
            btn.setStyleSheet(self._btn_style(active))
        self._stack.setCurrentIndex(index)
        if old != index:
            self.currentChanged.emit(index)
        # ensure button styles reflect any possible accent change
        self._refresh_tab_styles()

    def _refresh_tab_styles(self):
        for i, (btn, _) in enumerate(self._tabs):
            active = (i == self._current)
            btn.setStyleSheet(self._btn_style(active))

    @staticmethod
    def _btn_style(active: bool) -> str:
        # Build tab button CSS dynamically from current accent + background
        # theme so the tab bar updates immediately when either changes.
        try:
            from ..theme import get_accent, get_font
            from ..styles import compute_accent_variants
            acc, hov, _ = compute_accent_variants(get_accent())
            fam, fsz = get_font()
        except Exception:
            from ..theme import DEFAULT_ACCENT, DEFAULT_FONT_SIZE
            from ..styles import compute_accent_variants
            acc, hov, _ = compute_accent_variants(DEFAULT_ACCENT)
            fsz = DEFAULT_FONT_SIZE

        try:
            from ..theme import get_background_palette
            pal = get_background_palette()
            text_dim = pal["text_dim"]
            text_muted = pal["text_muted"]
            border2 = pal["border2"]
        except Exception:
            text_dim, text_muted, border2 = "#5a5650", "#9c9484", "#3d3a35"

        if active:
            return (
                f"QPushButton {{ background:transparent; color:{acc}; border:none;"
                f" border-bottom:2px solid {acc}; padding:11px 22px 9px 22px;"
                f" font-size:{int(fsz)}px; font-weight:600; letter-spacing:0.2px; border-radius:0px; }}"
            )
        return (
            f"QPushButton {{ background:transparent; color:{text_dim}; border:none;"
            f" border-bottom:2px solid transparent; padding:11px 22px 9px 22px;"
            f" font-size:{int(fsz)}px; font-weight:600; letter-spacing:0.2px; border-radius:0px; }}"
            f"QPushButton:hover {{ color:{text_muted}; border-bottom:2px solid {border2}; }}"
        )


# ── Custom Title Bar ──────────────────────────────────────────────────────────

class CustomTitleBar(QFrame):
    """Frameless window titlebar with drag-to-move, minimise, maximise, and close."""

    def __init__(self, window: QMainWindow, app_name: str, version: str, parent=None):
        super().__init__(parent)
        self._window   = window
        self.setObjectName("titlebar")
        self.setFixedHeight(42)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 8, 0)
        lay.setSpacing(0)

        # Coffee icon (clickable) + app name — keep original QLabel appearance
        self._icon_lbl = QLabel()
        from ..theme import get_accent
        self._icon_lbl.setPixmap(lucide_icon("coffee", get_accent(), 15).pixmap(QSize(15, 15)))
        self._icon_lbl.setStyleSheet("background:transparent; padding-right:6px;")
        self._icon_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self._icon_lbl.setToolTip("Open https://mocha.my")
        def _icon_clicked(event):
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
        self._eta_lbl.setStyleSheet("background:transparent; margin-bottom:3px; margin-right:10px;")
        self._eta_lbl.hide()
        lay.addWidget(self._eta_lbl)

        self._storage_lbl = QLabel("")
        self._storage_lbl.setObjectName("title_storage")
        self._storage_lbl.setStyleSheet("background:transparent; margin-bottom:3px;")
        self._storage_lbl.hide()
        lay.addWidget(self._storage_lbl)

        self._min_btn = self._make_btn("tb_minmax", "minus",  "#5a5650", 13, "Minimise",        window.showMinimized)
        self._max_btn = self._make_btn("tb_minmax", "square", "#5a5650", 11, "Maximise",        self._toggle_maximise)
        self._cls_btn = self._make_btn("tb_close",  "x",      "#5a5650", 13, "Close",           window.close)

        for btn in (self._min_btn, self._max_btn, self._cls_btn):
            lay.addWidget(btn)

    def _make_btn(self, obj_name, icon_name, color, icon_size, tooltip, slot) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName(obj_name)
        btn.setIcon(lucide_icon(icon_name, color, icon_size))
        btn.setIconSize(QSize(icon_size, icon_size))
        btn.setToolTip(tooltip)
        btn.clicked.connect(slot)
        return btn

    # ── ETA indicator ──────────────────────────────────────────────────────────

    def set_eta_text(self, text: str):
        """Update the upload ETA label shown in the titlebar, before the
        storage indicator. Pass an empty string to hide it."""
        self._eta_lbl.setText(text)
        self._eta_lbl.setVisible(bool(text))

    # ── Storage indicator ─────────────────────────────────────────────────────

    def set_storage_text(self, text: str):
        """Update the storage-capacity label shown before the minimise button."""
        self._storage_lbl.setText(text)
        self._storage_lbl.setVisible(bool(text))

    # ── Maximise / restore ────────────────────────────────────────────────────

    def _toggle_maximise(self):
        if self._window.isMaximized():
            self._window.setMaximumWidth(640)
            self._window.showNormal()
        else:
            self._window.setMaximumWidth(16777215)  # QWIDGETSIZE_MAX
            self._window.showMaximized()
        self._sync_max_icon()

    def _sync_max_icon(self):
        if self._window.isMaximized():
            self._max_btn.setToolTip("Restore")
            self._max_btn.setIcon(lucide_icon("square", "#9c9484", 11))
        else:
            self._max_btn.setToolTip("Maximise")
            self._max_btn.setIcon(lucide_icon("square", "#5a5650", 11))

    def _refresh_icons(self):
        """Called by app to refresh titlebar icons when the accent changes."""
        try:
            from ..theme import get_accent
            acc = get_accent()
            # update coffee icon (keep it tinted with accent)
            try:
                self._icon_lbl.setPixmap(lucide_icon("coffee", acc, 15).pixmap(QSize(15, 15)))
            except Exception:
                pass
            # refresh min/max/close icons as well
            try:
                self._min_btn.setIcon(lucide_icon("minus", "#5a5650", 13))
                self._max_btn.setIcon(lucide_icon("square", "#5a5650", 11))
                self._cls_btn.setIcon(lucide_icon("x", "#5a5650", 13))
            except Exception:
                pass
            try:
                self.update()
                self.repaint()
            except Exception:
                pass
        except Exception:
            pass

    # ── Drag-to-move ──────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # startSystemMove() works on both X11 and Wayland.
            # Manual move() calls are silently ignored by Wayland compositors,
            # so the old _drag_pos approach only ever worked on X11.
            win = self._window.windowHandle()
            if win is not None:
                win.startSystemMove()
            event.accept()

    def mouseMoveEvent(self, event):
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximise()