import requests

from PyQt6.QtCore import Qt, QTimer, QPoint
from PyQt6.QtGui import QColor, QMouseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QFrame,
    QSizeGrip,
)


# ── Shared: Mocha-styled frameless dialog base ────────────────────────────────
class MochaDialog(QDialog):
    """
    Frameless dialog base that draws the same dark titlebar as the main window.
    Subclasses call super().__init__(...) then build their content inside
    self.content_layout (a QVBoxLayout already added below the titlebar).
    """

    def __init__(self, title: str, parent=None, min_size=(420, 380)):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setMinimumSize(*min_size)
        self.setStyleSheet(parent.styleSheet() if parent else "")

        # Track drag
        self._drag_pos: QPoint | None = None

        # ── Root layout ───────────────────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Titlebar ──────────────────────────────────────────────────────────
        tb = QFrame()
        tb.setObjectName("titlebar")
        tb.setFixedHeight(42)
        tb_lay = QHBoxLayout(tb)
        tb_lay.setContentsMargins(12, 0, 8, 0)
        tb_lay.setSpacing(6)

        # App icon dot
        dot = QLabel("◆")
        dot.setStyleSheet("color:#c8a96e; font-size:10px; background:transparent;")
        tb_lay.addWidget(dot)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("title_app_name")
        title_lbl.setStyleSheet(
            "color:#c8a96e; font-size:13px; font-weight:700;"
            "letter-spacing:0.5px; background:transparent;"
        )
        tb_lay.addWidget(title_lbl)
        tb_lay.addStretch()

        close_btn = QPushButton("✕")
        close_btn.setObjectName("tb_close")
        close_btn.setFixedSize(32, 28)
        close_btn.clicked.connect(self.reject)
        tb_lay.addWidget(close_btn)

        root.addWidget(tb)

        # Divider
        div = QFrame()
        div.setObjectName("divider")
        div.setFixedHeight(1)
        root.addWidget(div)

        # ── Content area ──────────────────────────────────────────────────────
        content_widget = QFrame()
        content_widget.setStyleSheet("background:#181614;")
        self.content_layout = QVBoxLayout(content_widget)
        self.content_layout.setContentsMargins(14, 14, 14, 14)
        self.content_layout.setSpacing(10)
        root.addWidget(content_widget)

        # Size grip for resizing
        grip_row = QHBoxLayout()
        grip_row.addStretch()
        grip = QSizeGrip(self)
        grip.setStyleSheet("background:transparent;")
        grip_row.addWidget(grip)
        self.content_layout.addLayout(grip_row)

        # Drag support via titlebar
        tb.mousePressEvent   = self._tb_press
        tb.mouseMoveEvent    = self._tb_move
        tb.mouseReleaseEvent = self._tb_release

    def _tb_press(self, ev: QMouseEvent):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _tb_move(self, ev: QMouseEvent):
        if self._drag_pos and ev.buttons() == Qt.MouseButton.LeftButton:
            self.move(ev.globalPosition().toPoint() - self._drag_pos)

    def _tb_release(self, ev: QMouseEvent):
        self._drag_pos = None


# ── Shared: styled button helpers ─────────────────────────────────────────────
def _gold_btn(text: str, width=160) -> QPushButton:
    btn = QPushButton(text)
    btn.setObjectName("upload_btn")
    btn.setFixedSize(width, 36)
    btn.setStyleSheet(
        "min-height:0px; padding:0px 16px; font-size:13px; font-weight:700;"
        "background:#c8a96e; color:#111010; border:none; border-radius:7px;"
    )
    return btn


def _grey_btn(text: str, width=160) -> QPushButton:
    btn = QPushButton(text)
    btn.setFixedSize(width, 36)
    btn.setStyleSheet(
        "min-height:0px; padding:0px 16px; font-size:13px; font-weight:600;"
        "background:#1e1c19; color:#f0ece6; border:1px solid #3d3a35; border-radius:7px;"
    )
    return btn


# ── Remote Folder Browser ─────────────────────────────────────────────────────
class FolderBrowserDialog(MochaDialog):
    """Fetches folders from the Mocha API and lets the user navigate, type, & pick one."""

    def __init__(self, api_key, base_url, current_path="/", parent=None):
        super().__init__("Browse remote folders", parent, min_size=(460, 440))
        self.api_key  = api_key
        self.base_url = base_url.rstrip("/")
        self.current  = current_path or "/"
        self.selected = self.current

        lay = self.content_layout
        # Remove the size-grip placeholder row (last item) so we insert before it
        grip_item = lay.takeAt(lay.count() - 1)

        # ── Path bar (typeable) ───────────────────────────────────────────────
        path_row = QHBoxLayout()
        path_row.setSpacing(6)

        path_icon = QLabel("📂")
        path_icon.setStyleSheet("background:transparent; font-size:14px;")
        path_row.addWidget(path_icon)

        self.path_edit = QLineEdit(self.current)
        self.path_edit.setPlaceholderText("Type or navigate to a path…")
        self.path_edit.returnPressed.connect(self._on_path_typed)
        path_row.addWidget(self.path_edit)

        go_btn = QPushButton("Go")
        go_btn.setFixedSize(48, 34)
        go_btn.setStyleSheet(
            "background:#252320; color:#c8a96e; border:1px solid #4a3f2a;"
            "border-radius:7px; font-size:12px; font-weight:700; min-height:0px;"
        )
        go_btn.clicked.connect(self._on_path_typed)
        path_row.addWidget(go_btn)

        lay.addLayout(path_row)

        # ── Folder list ───────────────────────────────────────────────────────
        self.list = QListWidget()
        self.list.setStyleSheet("""
            QListWidget { background:#141210; border:1px solid #2e2b27;
                          border-radius:8px; color:#f0ece6; font-size:13px; }
            QListWidget::item { padding:6px 10px; }
            QListWidget::item:selected { background:#c8a96e33; color:#f0ece6; }
            QListWidget::item:hover { background:#1e1c19; }
        """)
        self.list.itemDoubleClicked.connect(self._on_double_click)
        self.list.currentItemChanged.connect(self._on_selection_changed)
        lay.addWidget(self.list)

        # ── Status ────────────────────────────────────────────────────────────
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color:#9c9484; font-size:11px; background:transparent;")
        lay.addWidget(self.status_lbl)

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        cancel_btn = _grey_btn("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        ok_btn = _gold_btn("Select this folder")
        ok_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(ok_btn)

        lay.addLayout(btn_row)

        # Re-add grip row
        if grip_item:
            lay.addItem(grip_item)

        self._navigate(self.current)

    # ── Path typed manually ───────────────────────────────────────────────────
    def _on_path_typed(self):
        raw = self.path_edit.text().strip()
        if not raw:
            raw = "/"
        # Normalise: always starts with /
        if not raw.startswith("/"):
            raw = "/" + raw
        self._navigate(raw)

    # ── Navigate to a remote path ─────────────────────────────────────────────
    def _navigate(self, path):
        self.current  = path
        self.selected = path
        self.path_edit.setText(path)
        self.status_lbl.setText("Loading…")
        self.list.clear()

        try:
            resp = requests.get(
                f"{self.base_url}/api/files",
                headers={"Authorization": f"Bearer {self.api_key}"},
                params={"path": path, "includeSubfolders": "0"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            self.status_lbl.setText(f"Error: {e}")
            if hasattr(e, "response") and e.response is not None:
                self.status_lbl.setText(
                    f"Error {e.response.status_code}: {e.response.text[:200]}"
                )
            return

        # ".." entry
        if path and path != "/":
            parent = "/" + "/".join(path.strip("/").split("/")[:-1])
            parent = parent if parent != "/" else "/"
            item = QListWidgetItem("▲  .. (go up)")
            item.setData(Qt.ItemDataRole.UserRole, ("dir", parent))
            item.setForeground(QColor("#9c9484"))
            self.list.addItem(item)

        folders = []
        folder_entries = data.get("folders") if isinstance(data, dict) else []
        if not folder_entries and isinstance(data, list):
            folder_entries = data
        for entry in (folder_entries or []):
            if isinstance(entry, str):
                name = entry.rstrip("/").split("/")[-1]
                fullpath = entry if entry.startswith("/") else (
                    (path.rstrip("/") + "/" + name) if path != "/" else ("/" + name)
                )
            elif isinstance(entry, dict):
                name = (
                    entry.get("name") or entry.get("original_name")
                    or entry.get("originalName") or entry.get("file_name") or ""
                )
                fullpath = (
                    entry.get("path") or entry.get("fullPath")
                    or (path.rstrip("/") + "/" + name)
                )
            else:
                continue
            if name:
                folders.append((name, fullpath))

        folders.sort(key=lambda x: x[0].lower())
        for name, fullpath in folders:
            item = QListWidgetItem(f"▶  {name}")
            item.setData(Qt.ItemDataRole.UserRole, ("dir", fullpath))
            self.list.addItem(item)

        count = len(folders)
        self.status_lbl.setText(f"{count} folder{'s' if count != 1 else ''}")

    def _on_selection_changed(self, current, _previous):
        if current:
            _kind, path = current.data(Qt.ItemDataRole.UserRole)
            self.selected = path
            self.path_edit.setText(path)

    def _on_double_click(self, item):
        kind, path = item.data(Qt.ItemDataRole.UserRole)
        if kind == "dir":
            self._navigate(path)

    def _on_accept(self):
        sel = self.list.currentItem()
        if sel:
            _kind, path = sel.data(Qt.ItemDataRole.UserRole)
            self.selected = path
        else:
            self.selected = self.path_edit.text().strip() or self.current
        self.accept()


# ── Share Link Dialog ─────────────────────────────────────────────────────────
class ShareLinkDialog(MochaDialog):
    """Modal dialog that displays a freshly created share URL with a Copy button."""

    def __init__(self, url, parent=None):
        super().__init__("Share Link Created", parent, min_size=(500, 200))
        self.url = url

        lay = self.content_layout
        grip_item = lay.takeAt(lay.count() - 1)

        # Header
        header = QLabel("✓  Share link ready")
        header.setStyleSheet("color:#4ade80; font-size:14px; font-weight:700; background:transparent;")
        lay.addWidget(header)

        # URL box
        self.url_edit = QLineEdit(url)
        self.url_edit.setReadOnly(True)
        self.url_edit.setStyleSheet(
            "background:#08090b; border:1px solid #35101a; border-radius:8px;"
            "padding:8px 10px; color:#c8a96e;"
            "font-family:'Consolas','Fira Code','Courier New',monospace; font-size:12px;"
        )
        lay.addWidget(self.url_edit)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.copy_btn = _gold_btn("⧉  Copy URL", width=140)
        self.copy_btn.clicked.connect(self._copy)
        btn_row.addWidget(self.copy_btn)

        open_btn = _grey_btn("↗  Open", width=100)
        open_btn.clicked.connect(lambda: __import__("webbrowser").open(url))
        btn_row.addWidget(open_btn)

        close_btn = _grey_btn("Close", width=100)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        lay.addLayout(btn_row)

        if grip_item:
            lay.addItem(grip_item)

    def _copy(self):
        QApplication.clipboard().setText(self.url)
        self.copy_btn.setText("✓  Copied!")
        QTimer.singleShot(2000, lambda: self.copy_btn.setText("⧉  Copy URL"))


# ── Local path dialog (used in mass-upload file picker) ──────────────────────
class LocalPathDialog(MochaDialog):
    """
    Lets the user type a local destination path.
    If the path doesn't exist it offers to create it.
    Returns the chosen (and possibly created) path via .chosen_path.
    """

    def __init__(self, initial_path: str = "", parent=None):
        super().__init__("Set destination path", parent, min_size=(480, 200))
        self.chosen_path = initial_path

        lay = self.content_layout
        grip_item = lay.takeAt(lay.count() - 1)

        hint = QLabel("Type the destination folder path:")
        hint.setStyleSheet("color:#9c9484; font-size:12px; background:transparent;")
        lay.addWidget(hint)

        self.path_edit = QLineEdit(initial_path)
        self.path_edit.setPlaceholderText("e.g. /remote/my-project  or  uploads/photos")
        self.path_edit.returnPressed.connect(self._on_accept)
        lay.addWidget(self.path_edit)

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color:#f87171; font-size:11px; background:transparent;")
        lay.addWidget(self.status_lbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = _grey_btn("Cancel", width=120)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        ok_btn = _gold_btn("Set path", width=120)
        ok_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(ok_btn)
        lay.addLayout(btn_row)

        if grip_item:
            lay.addItem(grip_item)

    def _on_accept(self):
        import os
        path = self.path_edit.text().strip()
        if not path:
            self.status_lbl.setText("Path cannot be empty.")
            return
        # If it's a local path and doesn't exist, offer to create it
        if not path.startswith("/") or os.name == "nt":
            abs_path = os.path.abspath(path)
        else:
            abs_path = path

        if not os.path.exists(abs_path):
            reply = QMessageBox.question(
                self,
                "Create folder?",
                f'The path "{abs_path}" does not exist.\nCreate it now?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                try:
                    os.makedirs(abs_path, exist_ok=True)
                except Exception as e:
                    self.status_lbl.setText(f"Could not create: {e}")
                    return
            else:
                return

        self.chosen_path = abs_path
        self.accept()