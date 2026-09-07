"""main.py — Mocha Tools custom installer entry point."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import install
import ui
from _meta import LICENSE_TEXT, VERSION
from PySide6.QtCore import QPoint, QRectF, Qt, QThread, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

APP_NAME = "Mocha Tools"
_DIR = Path(__file__).resolve().parent
_ICON_PATH = str(_DIR / "icon.ico")

PAGE_WELCOME = 0
PAGE_LICENSE = 1
PAGE_OPTIONS = 2
PAGE_PROGRESS = 3
PAGE_DONE = 4


class TitleBar(QFrame):
    """Frameless titlebar: coffee icon, gold app name, version, window controls."""

    def __init__(self, window: QMainWindow, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._window = window
        self._drag_pos: QPoint | None = None
        self.setObjectName("titlebar")
        self.setFixedHeight(34)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 6, 0)
        lay.setSpacing(0)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(ui.app_icon(20).pixmap(20, 20))
        icon_lbl.setStyleSheet("background:transparent; padding-right:6px;")
        lay.addWidget(icon_lbl)

        name_lbl = QLabel(APP_NAME)
        name_lbl.setObjectName("title_app_name")
        lay.addWidget(name_lbl)

        sep = QLabel(" ")
        sep.setStyleSheet("background:transparent;")
        lay.addWidget(sep)

        ver_lbl = QLabel(VERSION)
        ver_lbl.setObjectName("title_version")
        lay.addWidget(ver_lbl)

        lay.addStretch()

        self._min_btn = self._make_btn(
            "tb_minmax",
            "minus",
            ui.TEXT_DIM,
            13,
            "Minimise",
            window.showMinimized,
        )
        self._cls_btn = self._make_btn(
            "tb_close",
            "x",
            ui.TEXT_DIM,
            13,
            "Close",
            window.close,
        )
        lay.addWidget(self._min_btn)
        lay.addWidget(self._cls_btn)

    def _make_btn(
        self,
        obj_name: str,
        icon_name: str,
        color: str,
        icon_size: int,
        tooltip: str,
        slot: object,
    ) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName(obj_name)
        btn.setIcon(ui.lucide_icon(icon_name, color, icon_size))
        btn.setIconSize(btn.iconSize())
        btn.setToolTip(tooltip)
        btn.clicked.connect(slot)
        return btn

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint()
                - self._window.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            pos = event.globalPosition().toPoint() - self._drag_pos
            screen = self._window.screen()
            if screen is not None:
                avail = screen.availableGeometry()
                win = self._window.frameGeometry()
                x = min(max(pos.x(), avail.left()), avail.right() - win.width() + 1)
                y = min(max(pos.y(), avail.top()), avail.bottom() - win.height() + 1)
                self._window.move(x, y)
            else:
                self._window.move(pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_pos = None
        event.accept()


class InstallWorker(QThread):
    progress = Signal(int, str)
    done = Signal()
    error = Signal(str)

    def __init__(
        self,
        install_dir: str,
        desktop_shortcut: bool,
        start_menu_shortcut: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.install_dir = install_dir
        self.desktop_shortcut = desktop_shortcut
        self.start_menu_shortcut = start_menu_shortcut

    def run(self) -> None:
        try:
            install.install(
                self.install_dir,
                self.desktop_shortcut,
                self.start_menu_shortcut,
                VERSION,
                progress_cb=lambda p, s: self.progress.emit(p, s),
            )
            self.done.emit()
        except Exception as e:  # noqa: BLE001
            self.error.emit(str(e))


class InstallerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {VERSION} — Setup")
        self.setFixedSize(500, 340)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._corner_radius = 12
        self.setWindowIcon(ui.app_icon(32))

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)

        self.titlebar = TitleBar(self)
        lay.addWidget(self.titlebar)

        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)

        self.welcome_page = self._build_welcome_page()
        self.license_page = self._build_license_page()
        self.options_page = self._build_options_page()
        self.progress_page = self._build_progress_page()
        self.done_page = self._build_done_page()
        for page in (
            self.welcome_page,
            self.license_page,
            self.options_page,
            self.progress_page,
            self.done_page,
        ):
            self.stack.addWidget(page)

        lay.addWidget(self._build_nav())

        self._page = PAGE_WELCOME
        self._update_nav()

    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint the rounded window background with anti-aliased corners."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = float(self._corner_radius)
        path = QPainterPath()
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path.addRoundedRect(rect, r, r)
        painter.fillPath(path, QColor(ui.BG0))
        pen = painter.pen()
        pen.setColor(QColor(ui.BORDER2))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawPath(path)
        painter.end()
        super().paintEvent(event)

    # ── Pages ─────────────────────────────────────────────────────────────────

    def _build_welcome_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(40, 20, 40, 16)
        lay.addStretch()

        icon_lbl = QLabel()
        icon_lbl.setPixmap(ui.app_icon(32).pixmap(32, 32))
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(icon_lbl)
        lay.addSpacing(8)

        title = QLabel(APP_NAME)
        title.setObjectName("big_title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)
        lay.addSpacing(4)

        ver = QLabel(f"Version {VERSION}")
        ver.setObjectName("dim")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(ver)

        lay.addStretch()
        return page

    def _build_license_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 14, 24, 16)
        self.license_view = QPlainTextEdit()
        self.license_view.setReadOnly(True)
        self.license_view.setPlainText(LICENSE_TEXT)
        lay.addWidget(self.license_view, 1)
        lay.addSpacing(8)
        self.agree_cb = QCheckBox("I agree to the license terms and conditions")
        self.agree_cb.toggled.connect(self._update_nav)
        lay.addWidget(self.agree_cb)
        return page

    def _build_options_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 14, 24, 16)
        label = QLabel("Install location")
        label.setObjectName("field_label")
        lay.addWidget(label)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.install_dir_edit = QLineEdit(install.default_install_dir())
        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.setObjectName("browse")
        self.browse_btn.clicked.connect(self._browse)
        row.addWidget(self.install_dir_edit, 1)
        row.addWidget(self.browse_btn)
        lay.addLayout(row)
        lay.addSpacing(12)
        self.desktop_cb = QCheckBox("Create a desktop shortcut")
        self.desktop_cb.setChecked(True)
        self.start_menu_cb = QCheckBox("Create a Start Menu shortcut")
        self.start_menu_cb.setChecked(True)
        self.launch_cb = QCheckBox("Launch Mocha Tools after install")
        self.launch_cb.setChecked(True)
        lay.addWidget(self.desktop_cb)
        lay.addSpacing(4)
        lay.addWidget(self.start_menu_cb)
        lay.addSpacing(4)
        lay.addWidget(self.launch_cb)
        lay.addStretch()
        return page

    def _build_progress_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(40, 20, 40, 16)
        lay.addStretch()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        lay.addWidget(self.progress_bar)
        lay.addSpacing(8)
        self.progress_label = QLabel("Preparing...")
        self.progress_label.setObjectName("muted")
        self.progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.progress_label)
        lay.addStretch()
        return page

    def _build_done_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(40, 20, 40, 16)
        lay.addStretch()
        check_lbl = QLabel()
        check_lbl.setPixmap(ui.make_check_icon(40))
        check_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(check_lbl)
        lay.addSpacing(10)
        title = QLabel("Installation Complete")
        title.setObjectName("big_title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)
        lay.addSpacing(12)
        self.done_launch_cb = QCheckBox("Launch Mocha Tools")
        lay.addWidget(self.done_launch_cb, 0, Qt.AlignmentFlag.AlignCenter)
        lay.addStretch()
        return page

    # ── Nav bar ───────────────────────────────────────────────────────────────

    def _build_nav(self) -> QWidget:
        nav = QWidget()
        nlay = QHBoxLayout(nav)
        nlay.setContentsMargins(20, 10, 20, 12)
        self.back_btn = QPushButton("Back")
        self.back_btn.clicked.connect(self._go_back)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.close)
        self.next_btn = QPushButton("Install")
        self.next_btn.setObjectName("primary")
        self.next_btn.clicked.connect(self._go_next)
        nlay.addWidget(self.back_btn)
        nlay.addStretch()
        nlay.addWidget(self.cancel_btn)
        nlay.addSpacing(8)
        nlay.addWidget(self.next_btn)
        return nav

    # ── Navigation ────────────────────────────────────────────────────────────

    def _update_nav(self) -> None:
        page = self._page
        self.back_btn.setVisible(page in (PAGE_LICENSE, PAGE_OPTIONS))
        self.cancel_btn.setVisible(page in (PAGE_WELCOME, PAGE_LICENSE, PAGE_OPTIONS))
        self.next_btn.setVisible(
            page in (PAGE_WELCOME, PAGE_LICENSE, PAGE_OPTIONS, PAGE_DONE)
        )
        if page == PAGE_WELCOME:
            self.cancel_btn.setText("Exit")
            self.next_btn.setText("Install")
        elif page == PAGE_LICENSE:
            self.cancel_btn.setText("Cancel")
            self.next_btn.setText("I Agree")
            self.next_btn.setEnabled(self.agree_cb.isChecked())
        elif page == PAGE_OPTIONS:
            self.cancel_btn.setText("Cancel")
            self.next_btn.setText("Install")
            self.next_btn.setEnabled(True)
        elif page == PAGE_DONE:
            self.next_btn.setText("Finish")
            self.next_btn.setEnabled(True)

    def _go_back(self) -> None:
        if self._page == PAGE_LICENSE:
            self._page = PAGE_WELCOME
        elif self._page == PAGE_OPTIONS:
            self._page = PAGE_LICENSE
        self.stack.setCurrentIndex(self._page)
        self._update_nav()

    def _go_next(self) -> None:
        if self._page == PAGE_WELCOME:
            self._page = PAGE_LICENSE
            self.stack.setCurrentIndex(self._page)
        elif self._page == PAGE_LICENSE:
            self._page = PAGE_OPTIONS
            self.stack.setCurrentIndex(self._page)
        elif self._page == PAGE_OPTIONS:
            self._start_install()
        elif self._page == PAGE_DONE:
            self._finish()
        self._update_nav()

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self,
            "Choose install location",
            self.install_dir_edit.text(),
        )
        if d:
            self.install_dir_edit.setText(d)

    def _start_install(self) -> None:
        self._page = PAGE_PROGRESS
        self.stack.setCurrentIndex(PAGE_PROGRESS)
        self.back_btn.hide()
        self.cancel_btn.hide()
        self.next_btn.hide()
        self.progress_bar.setValue(0)
        self.progress_label.setText("Preparing...")
        self._worker = InstallWorker(
            self.install_dir_edit.text().strip() or install.default_install_dir(),
            self.desktop_cb.isChecked(),
            self.start_menu_cb.isChecked(),
            self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, pct: int, msg: str) -> None:
        self.progress_bar.setValue(pct)
        self.progress_label.setText(msg)

    def _on_done(self) -> None:
        self.done_launch_cb.setChecked(self.launch_cb.isChecked())
        self._page = PAGE_DONE
        self.stack.setCurrentIndex(PAGE_DONE)
        self._update_nav()

    def _on_error(self, msg: str) -> None:
        QMessageBox.critical(self, "Installation Failed", msg)
        self._page = PAGE_OPTIONS
        self.stack.setCurrentIndex(PAGE_OPTIONS)
        self._update_nav()

    def _finish(self) -> None:
        if self.done_launch_cb.isChecked():
            exe = Path(self.install_dir_edit.text().strip()) / "Mocha Tools.exe"
            if exe.exists():
                subprocess.Popen([str(exe)])
        self.close()


def _run_silent() -> int:
    try:
        install.install(
            install.default_install_dir(),
            desktop_shortcut=False,
            start_menu_shortcut=True,
            version=VERSION,
        )
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"Installation failed: {e}\n")
        return 1
    return 0


def main() -> None:
    if "--silent" in sys.argv:
        sys.exit(_run_silent())

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("nxllxvxxd2")
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 13))
    app.setStyleSheet(ui.STYLESHEET)
    app.setPalette(ui.build_palette())

    win = InstallerWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
