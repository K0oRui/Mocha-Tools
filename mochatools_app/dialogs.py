import requests

from PyQt6.QtCore import Qt, QThread, QTimer, QPoint, pyqtSignal
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

from .ui import lucide_icon


# Keep references to in-flight fetch threads here so dialogs can be destroyed
# without the QThread objects being garbage-collected while still running.
_OUTSTANDING_FETCH_WORKERS = []

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

        try:
            from .theme import get_accent, get_font
            _dot_color = get_accent()
            _fs = int(get_font()[1])
        except Exception:
            _dot_color = "#c8a96e"
            _fs = 13
        dot = QLabel("◆")
        dot.setStyleSheet(f"color:{_dot_color}; font-size:{max(8, _fs-3)}px; background:transparent;")
        tb_lay.addWidget(dot)

        if title:
            title_lbl = QLabel()
            title_lbl.setStyleSheet(
                f"color:#dcd6cc; font-size:{max(9, _fs-2)}px; font-weight:600;"
                f" background:transparent; margin-left:6px;"
            )
            metrics = title_lbl.fontMetrics()
            title_lbl.setText(metrics.elidedText(title, Qt.TextElideMode.ElideRight, 320))
            title_lbl.setToolTip(title)
            tb_lay.addWidget(title_lbl)
            self._title_lbl = title_lbl
        else:
            self._title_lbl = None

        tb_lay.addStretch()

        close_btn = QPushButton()
        try:
            from .theme import get_accent
            _xcol = get_accent()
        except Exception:
            _xcol = "#c8a96e"
        close_btn.setIcon(lucide_icon("x", _xcol, 12))
        close_btn.setObjectName("tb_close")
        close_btn.setFixedSize(32, 28)
        close_btn.clicked.connect(self.reject)
        tb_lay.addWidget(close_btn)

        root.addWidget(tb)

        div = QFrame()
        div.setObjectName("divider")
        div.setFixedHeight(1)
        root.addWidget(div)

        content_widget = QFrame()
        content_widget.setStyleSheet("background:#181614;")
        self.content_layout = QVBoxLayout(content_widget)
        self.content_layout.setContentsMargins(14, 14, 14, 14)
        self.content_layout.setSpacing(10)
        root.addWidget(content_widget)

        grip_row = QHBoxLayout()
        grip_row.addStretch()
        grip = QSizeGrip(self)
        grip.setStyleSheet("background:transparent;")
        grip_row.addWidget(grip)
        self.content_layout.addLayout(grip_row)

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
    try:
        acc = get_accent()
    except Exception:
        from .theme import DEFAULT_ACCENT
        acc = DEFAULT_ACCENT
    try:
        from .theme import get_font
        fsz = int(get_font()[1])
    except Exception:
        fsz = 13
    btn.setStyleSheet(
        f"min-height:0px; padding:0px 16px; font-size:{fsz}px; font-weight:700;"
        f"background:{acc}; color:#111010; border:none; border-radius:7px;"
    )
    return btn


def _grey_btn(text: str, width=160) -> QPushButton:
    btn = QPushButton(text)
    btn.setFixedSize(width, 36)
    try:
        from .theme import get_font
        fsz = int(get_font()[1])
    except Exception:
        fsz = 13
    btn.setStyleSheet(
        f"min-height:0px; padding:0px 16px; font-size:{fsz}px; font-weight:600;"
        "background:#1e1c19; color:#f0ece6; border:1px solid #3d3a35; border-radius:7px;"
    )
    return btn


# ── Background worker for folder listings ─────────────────────────────────────
class _FolderFetchWorker(QThread):
    """
    Fetches one folder listing off the main thread.

    Uses a class-level persistent requests.Session so the underlying
    TCP+TLS connection is reused across navigations and dialog openings.
    Without this, every folder navigation pays the full TLS handshake cost
    (200-400 ms extra on a typical connection) on top of the API round-trip.

    A cancel token (single-element list) lets the caller suppress the result
    if the user has already navigated elsewhere before the response arrives.
    """
    done = pyqtSignal(str, object)   # (path, data_dict | Exception)

    # Persistent session shared across all workers — keeps the TCP+TLS
    # connection alive so subsequent fetches skip the handshake entirely.
    _session: "requests.Session | None" = None

    @classmethod
    def _get_session(cls) -> "requests.Session":
        if cls._session is None:
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=1,
                pool_maxsize=4,
                max_retries=0,
            )
            cls._session = requests.Session()
            cls._session.mount("https://", adapter)
            cls._session.mount("http://",  adapter)
        return cls._session

    def __init__(self, api_key: str, base_url: str, path: str, cancel_token: list):
        super().__init__()
        self.api_key      = api_key
        self.base_url     = base_url.rstrip("/")
        self.path         = path
        self._cancel      = cancel_token

    def run(self):
        try:
            session = self._get_session()
            resp = session.get(
                f"{self.base_url}/api/files",
                headers={"Authorization": f"Bearer {self.api_key}"},
                params={"path": self.path, "includeSubfolders": "0"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            if not self._cancel[0]:
                self.done.emit(self.path, data)
        except Exception as e:
            if not self._cancel[0]:
                self.done.emit(self.path, e)


# ── Remote Folder Browser ─────────────────────────────────────────────────────
class FolderBrowserDialog(MochaDialog):
    """Fetches folders from the Mocha API and lets the user navigate, type, & pick one."""

    # Class-level cache shared across all dialog instances in this session.
    # Maps path -> folder list data so re-visiting a folder is instant.
    _path_cache: dict = {}

    def __init__(self, api_key, base_url, current_path="/", parent=None):
        super().__init__("Browse remote folders", parent, min_size=(460, 440))
        self.api_key  = api_key
        self.base_url = base_url.rstrip("/")
        self.current  = current_path or "/"
        self.selected = self.current
        self._worker: _FolderFetchWorker | None = None
        self._cancel_token: list = [False]
        self._dead_workers: list[_FolderFetchWorker] = []
        self._navigating: bool = False   # suppresses auto-highlight during render

        lay = self.content_layout
        grip_item = lay.takeAt(lay.count() - 1)

        # ── Path bar ──────────────────────────────────────────────────────────
        path_row = QHBoxLayout()
        path_row.setSpacing(6)

        path_icon = QLabel("📂")
        try:
            from .theme import get_font
            pfs = int(get_font()[1])
        except Exception:
            pfs = 14
        path_icon.setStyleSheet(f"background:transparent; font-size:{pfs}px;")
        path_row.addWidget(path_icon)

        self.path_edit = QLineEdit(self.current)
        self.path_edit.setPlaceholderText("Type or navigate to a path…")
        self.path_edit.returnPressed.connect(self._on_path_typed)
        path_row.addWidget(self.path_edit)

        go_btn = QPushButton("Go")
        go_btn.setFixedSize(48, 34)
        try:
            acc = get_accent()
            from .styles import compute_accent_variants
            _, acc_hov, _ = compute_accent_variants(acc)
        except Exception:
            acc = "#c8a96e"
            acc_hov = "#d4b87a"
        go_btn.setStyleSheet(
            f"background:#252320; color:{acc}; border:1px solid #4a3f2a;"
            f"border-radius:7px; font-size:12px; font-weight:700; min-height:0px;"
        )
        go_btn.clicked.connect(self._on_path_typed)
        path_row.addWidget(go_btn)

        lay.addLayout(path_row)

        # ── Folder list ───────────────────────────────────────────────────────
        self.list = QListWidget()
        try:
            acc = get_accent()
            acc_hov = acc + "33"
        except Exception:
            from .theme import DEFAULT_ACCENT
            acc = DEFAULT_ACCENT
            acc_hov = DEFAULT_ACCENT + "33"
        self.list.setStyleSheet(f"""
            QListWidget {{ background:#141210; border:1px solid #2e2b27;
                          border-radius:8px; color:#f0ece6; font-size:13px; }}
            QListWidget::item {{ padding:6px 10px; }}
            QListWidget::item:selected {{ background:{acc_hov}; color:#f0ece6; }}
            QListWidget::item:hover {{ background:#1e1c19; }}
        """)
        self.list.itemDoubleClicked.connect(self._on_double_click)
        self.list.currentItemChanged.connect(self._on_selection_changed)
        lay.addWidget(self.list)

        # Ensure folder icons update if the accent changes at runtime
        try:
            from .theme import notifier
            def _refresh_list(old, new):
                for i in range(self.list.count()):
                    it = self.list.item(i)
                    try:
                        data = it.data(Qt.ItemDataRole.UserRole)
                    except Exception:
                        data = None
                    if data and isinstance(data, tuple) and data[0] == "dir":
                        try:
                            it.setIcon(lucide_icon("folder", new, 12))
                        except Exception:
                            pass
            notifier().accent_changed.connect(_refresh_list)
        except Exception:
            pass

        # ── Status ────────────────────────────────────────────────────────────
        self.status_lbl = QLabel("")
        from .theme import accent_qcolor, notifier
        self.status_lbl.setStyleSheet(f"color:{accent_qcolor().name()}; font-size:11px; background:transparent;")
        lay.addWidget(self.status_lbl)

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        cancel_btn = _grey_btn("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self._ok_btn = _gold_btn("Select this folder")
        self._ok_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(self._ok_btn)

        lay.addLayout(btn_row)

        if grip_item:
            lay.addItem(grip_item)

        self._navigate(self.current)

    # ── Path typed manually ───────────────────────────────────────────────────
    def _on_path_typed(self):
        raw = self.path_edit.text().strip() or "/"
        if not raw.startswith("/"):
            raw = "/" + raw
        self._navigate(raw)

    # ── Navigate ──────────────────────────────────────────────────────────────
    def _navigate(self, path: str):
        # Cancel any in-flight worker
        self._cancel_token[0] = True
        self._cancel_token = [False]

        if self._worker is not None:
            try:
                self._worker.done.disconnect(self._on_fetch_done)
            except RuntimeError:
                pass
            self._dead_workers = [w for w in self._dead_workers if not w.isFinished()]
            self._dead_workers.append(self._worker)
            self._worker = None

        self.current  = path
        self.selected = path
        self.path_edit.setText(path)
        self._ok_btn.setEnabled(True)
        # Serve cached data instantly if available — identical render path as
        # the original synchronous version
        if path in FolderBrowserDialog._path_cache:
            self._render(path, FolderBrowserDialog._path_cache[path])
            self.status_lbl.setText(
                self.status_lbl.text().rstrip("…") + "  (refreshing…)"
                if self.status_lbl.text() else "Refreshing…"
            )
        else:
            self.list.clear()
            self.status_lbl.setText("Loading…")

        # Always fetch fresh in the background
        w = _FolderFetchWorker(self.api_key, self.base_url, path, self._cancel_token)
        w.done.connect(self._on_fetch_done)
        self._worker = w
        # retain a module-level reference to avoid QThread: Destroyed while running
        try:
            _OUTSTANDING_FETCH_WORKERS.append(w)
        except Exception:
            pass
        w.start()

    # ── Fetch result ──────────────────────────────────────────────────────────
    def _on_fetch_done(self, path: str, result):
        if path != self.current:
            return

        self._worker = None
        self._ok_btn.setEnabled(True)

        if isinstance(result, Exception):
            e = result
            if hasattr(e, "response") and e.response is not None:
                msg = f"Error {e.response.status_code}: {e.response.text[:200]}"
            else:
                first_line = str(e).splitlines()[0].strip()
                if "):" in first_line:
                    first_line = first_line.split("):", 1)[-1].strip()
                msg = f"Connection error: {first_line[:120]}"
            self.status_lbl.setText(msg)
            return

        # Cache the result for instant re-render next time
        FolderBrowserDialog._path_cache[path] = result
        self._render(path, result)
        # Remove finished worker references so list doesn't grow unbounded
        try:
            global _OUTSTANDING_FETCH_WORKERS
            _OUTSTANDING_FETCH_WORKERS = [w for w in _OUTSTANDING_FETCH_WORKERS if not getattr(w, 'isFinished', lambda: True)()]
        except Exception:
            pass

    # ── Render folder list — exact same logic as the original ─────────────────
    def _render(self, path: str, data):
        self._navigating = True
        self.list.blockSignals(True)
        self.list.clear()

        # "▲ .." entry
        if path and path != "/":
            parent = "/" + "/".join(path.strip("/").split("/")[:-1])
            parent = parent if parent != "/" else "/"
            item = QListWidgetItem(".. (go up)")
            item.setData(Qt.ItemDataRole.UserRole, ("dir", parent))
            from .theme import accent_qcolor
            item.setForeground(accent_qcolor())
            try:
                item.setIcon(lucide_icon("folder", get_accent(), 12))
            except Exception:
                pass
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
            item = QListWidgetItem(f"{name}")
            item.setData(Qt.ItemDataRole.UserRole, ("dir", fullpath))
            try:
                item.setIcon(lucide_icon("folder", get_accent(), 12))
            except Exception:
                pass
            self.list.addItem(item)

        self.list.blockSignals(False)

        # Restore path bar to the navigated folder, not the first child
        self.path_edit.setText(path)
        self.selected = path
        count = len(folders)
        self.status_lbl.setText(f"{count} folder{'s' if count != 1 else ''}")
        self._navigating = False

    # ── Selection / interaction ────────────────────────────────────────────────
    def _on_selection_changed(self, current, _previous):
        if self._navigating:
            return
        if current:
            _kind, path = current.data(Qt.ItemDataRole.UserRole)
            self.selected = path
            self.path_edit.setText(path)

    def _on_double_click(self, item):
        kind, path = item.data(Qt.ItemDataRole.UserRole)
        if kind == "dir":
            self._navigate(path)

    def _on_accept(self):
        # self.selected is either:
        #   - the folder the user explicitly single-clicked in the list, or
        #   - self.current (set in _render / _navigate) if no explicit click happened.
        # Either way it's the right answer — no need to override with self.current.
        self.accept()

    def closeEvent(self, event):
        self._cancel_token[0] = True
        if self._worker is not None:
            try:
                self._worker.done.disconnect(self._on_fetch_done)
            except RuntimeError:
                pass
        # also mark any outstanding workers for cancellation
        try:
            for w in list(_OUTSTANDING_FETCH_WORKERS):
                try:
                    w._cancel[0] = True
                except Exception:
                    pass
        except Exception:
            pass
        super().closeEvent(event)


# ── Share Link Dialog ─────────────────────────────────────────────────────────
class ShareLinkDialog(MochaDialog):
    """Modal dialog that displays a freshly created share URL with a Copy button."""

    def __init__(self, url, parent=None):
        super().__init__("Share Link Created", parent, min_size=(500, 200))
        self.url = url

        lay = self.content_layout
        grip_item = lay.takeAt(lay.count() - 1)

        header = QLabel("✓  Share link ready")
        try:
            from .theme import get_accent, get_font
            fam, fsz = get_font()
            header.setStyleSheet(f"color:{get_accent()}; font-size:{int(fsz)}px; font-weight:700; background:transparent;")
        except Exception:
            header.setStyleSheet("color:#4ade80; font-size:14px; font-weight:700; background:transparent;")
        lay.addWidget(header)

        self.url_edit = QLineEdit(url)
        self.url_edit.setReadOnly(True)
        try:
            from .theme import get_accent
            _url_col = get_accent()
        except Exception:
            _url_col = "#c8a96e"
        try:
            from .theme import get_font
            fsz = int(get_font()[1])
        except Exception:
            fsz = 12
        self.url_edit.setStyleSheet(
            f"background:#08090b; border:1px solid #35101a; border-radius:8px;"
            f"padding:8px 10px; color:{_url_col};"
            f"font-family:'Consolas','Fira Code','Courier New',monospace; font-size:{fsz}px;"
        )
        lay.addWidget(self.url_edit)

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
        try:
            from .theme import accent_qcolor
            hint.setStyleSheet(f"color:{accent_qcolor().name()}; font-size:12px; background:transparent;")
        except Exception:
            hint.setStyleSheet("color:#9c9484; font-size:12px; background:transparent;")
        lay.addWidget(hint)

        self.path_edit = QLineEdit(initial_path)
        self.path_edit.setPlaceholderText("e.g. /remote/my-project  or  uploads/photos")
        self.path_edit.returnPressed.connect(self._on_accept)
        lay.addWidget(self.path_edit)

        self.status_lbl = QLabel("")
        try:
            from .theme import accent_qcolor
            self.status_lbl.setStyleSheet(f"color:{accent_qcolor().name()}; font-size:11px; background:transparent;")
        except Exception:
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