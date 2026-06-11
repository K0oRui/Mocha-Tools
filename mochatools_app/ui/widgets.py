"""
ui/widgets.py — Reusable custom Qt widgets for MochaTools.

  DropZone          — drag-and-drop / click-to-browse file picker
  FullWidthTabWidget — tab bar that always fills the full widget width
  CustomTitleBar    — frameless window titlebar with drag-to-move
"""

import os

from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QDragEnterEvent, QDropEvent
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
        row.addWidget(bold)
        row.addWidget(rest)

        self.file_label = QLabel("")
        self.file_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.file_label.setStyleSheet(
            "color: #c8a96e; font-size: 12px; font-weight:600; background:transparent;"
        )

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
        act_file   = menu.addAction("📄  Select files…")
        act_folder = menu.addAction("📁  Select folder…")
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
        self._bar.setStyleSheet(
            "QWidget#tabbar_row {"
            "  background: #181614;"
            "  border-bottom: 1px solid #2e2b27;"
            "}"
        )
        self._bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._bar_lay = QHBoxLayout(self._bar)
        self._bar_lay.setContentsMargins(0, 0, 0, 0)
        self._bar_lay.setSpacing(0)
        outer.addWidget(self._bar)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet("QStackedWidget { background: #181614; }")
        outer.addWidget(self._stack, 1)

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

    @staticmethod
    def _btn_style(active: bool) -> str:
        if active:
            return (
                "QPushButton { background:transparent; color:#c8a96e; border:none;"
                " border-bottom:2px solid #c8a96e; padding:11px 22px 9px 22px;"
                " font-size:12px; font-weight:600; letter-spacing:0.2px; border-radius:0px; }"
            )
        return (
            "QPushButton { background:transparent; color:#5a5650; border:none;"
            " border-bottom:2px solid transparent; padding:11px 22px 9px 22px;"
            " font-size:12px; font-weight:600; letter-spacing:0.2px; border-radius:0px; }"
            "QPushButton:hover { color:#9c9484; border-bottom:2px solid #3d3a35; }"
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

        # Coffee icon + app name
        icon_lbl = QLabel()
        icon_lbl.setPixmap(lucide_icon("coffee", "#c8a96e", 15).pixmap(QSize(15, 15)))
        icon_lbl.setStyleSheet("background:transparent; padding-right:6px;")
        lay.addWidget(icon_lbl)

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