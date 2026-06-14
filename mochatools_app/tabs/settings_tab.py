"""
tabs/settings_tab.py — Settings tab UI builder and persistence helpers.

Exposes:
  build_settings_tab(win)  → QWidget   (call once in MochaTools._build_ui)
  load_settings(win)       → None      (restores QSettings values onto win)
  save_settings(win)       → None      (persists values from win to QSettings)
"""

import json

try:
    import keyring
    import keyring.errors
    _KEYRING_OK = True
except ImportError:
    _KEYRING_OK = False

_KR_SERVICE = "MochaTools"
_KR_USER    = "api_key"

from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QProgressBar, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from ..constants import (
    APP_NAME, APP_VERSION, DEFAULT_CHUNK_SIZE_MB, DEFAULT_MAX_CHUNKS, ORG_NAME,
)


# ── Settings tab UI ───────────────────────────────────────────────────────────

def build_settings_tab(win) -> QWidget:
    """
    Build and return the Settings tab widget.
    All interactive widgets are attached as attributes of `win` so that
    _start_upload, _load_settings, _save_settings, etc. can reach them.
    """
    tab    = QWidget()
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setMaximumWidth(680)

    inner = QWidget()
    lay   = QVBoxLayout(inner)
    lay.setContentsMargins(16, 16, 16, 16)
    lay.setSpacing(14)
    scroll.setWidget(inner)

    tab_lay = QVBoxLayout(tab)
    tab_lay.setContentsMargins(0, 0, 0, 0)
    center_row = QHBoxLayout()
    center_row.setContentsMargins(0, 0, 0, 0)
    center_row.addStretch()
    center_row.addWidget(scroll, 1)
    center_row.addStretch()
    tab_lay.addLayout(center_row)

    _build_api_section(win, lay)
    _build_logging_section(win, lay)
    _build_mass_upload_section(win, lay)
    _build_multipart_section(win, lay)
    _build_updates_section(win, lay)
    lay.addStretch()

    return tab


# ── Section builders ──────────────────────────────────────────────────────────

def _build_api_section(win, lay: QVBoxLayout):
    lay.addWidget(_sh("API"))
    card     = _card()
    card_lay = QVBoxLayout(card)
    card_lay.setSpacing(10)

    # API key row
    key_row = QHBoxLayout()
    key_lbl = QLabel("API key")
    key_lbl.setObjectName("field_label")
    win.api_key_edit = QLineEdit()
    win.api_key_edit.setPlaceholderText("mocha_your_api_key_here")
    win.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
    win.show_key_cb = QCheckBox("Show")
    win.show_key_cb.toggled.connect(win._toggle_key_visibility)
    key_row.addWidget(key_lbl)
    key_row.addWidget(win.api_key_edit, 1)
    key_row.addWidget(win.show_key_cb)
    card_lay.addLayout(key_row)

    # upload_path_edit is created in _build_upload_tab and lives in the Upload tab UI.
    # Do NOT reassign it here — that would replace the visible widget with an orphan.

    win.remember_cb = QCheckBox("Remember settings across sessions")
    card_lay.addWidget(win.remember_cb)

    win.browser_download_cb = QCheckBox("Use browser for file downloads")
    win.browser_download_cb.setToolTip(
        "When checked, downloads open in your default browser.\n"
        "When unchecked, files download directly through Mocha Tools."
    )
    card_lay.addWidget(win.browser_download_cb)
    lay.addWidget(card)


def _build_logging_section(win, lay: QVBoxLayout):
    lay.addWidget(_sh("Logging"))
    card     = _card()
    card_lay = QVBoxLayout(card)
    card_lay.setSpacing(6)

    win.debug_cb = QCheckBox("Enable debug logging")
    win.debug_cb.setToolTip(
        "Show [DEBUG] lines in the status console and log file.\n"
        "Turn off to see only high-level status messages."
    )
    card_lay.addWidget(win.debug_cb)

    note = QLabel("When enabled, all status messages are shown in the console and written to the log file.")
    note.setObjectName("field_label")
    note.setWordWrap(True)
    card_lay.addWidget(note)
    lay.addWidget(card)


def _build_mass_upload_section(win, lay: QVBoxLayout):
    lay.addWidget(_sh("Mass Upload"))
    card     = _card()
    card_lay = QVBoxLayout(card)
    card_lay.setSpacing(10)

    win.mass_conc_spin = _spinbox(1, 10, 2, " files",
        "How many files upload at the same time.\nHigher values can saturate slower connections.")
    _add_spin_row(card_lay, "Concurrent files", win.mass_conc_spin)

    win.mass_chunk_spin = _spinbox(1, 100, DEFAULT_CHUNK_SIZE_MB, " MB",
        "Size of each multipart part (1–100 MB).\nFiles smaller than this upload in one request.")
    _add_spin_row(card_lay, "Chunk size", win.mass_chunk_spin)

    win.mass_maxchunk_spin = _spinbox(1, 20, DEFAULT_MAX_CHUNKS, " chunks",
        "Max parts sent in parallel per file (1–20).")
    _add_spin_row(card_lay, "Parallel chunks", win.mass_maxchunk_spin)
    lay.addWidget(card)


def _build_multipart_section(win, lay: QVBoxLayout):
    lay.addWidget(_sh("Multipart Upload"))
    card     = _card()
    card_lay = QVBoxLayout(card)
    card_lay.setSpacing(10)

    note = QLabel(
        "Files larger than one chunk size are uploaded in multiple parts. "
        "Larger chunks reduce overhead; more parallel chunks can increase throughput "
        "on fast connections."
    )
    note.setObjectName("field_label")
    note.setWordWrap(True)
    card_lay.addWidget(note)

    win.chunk_size_spin = _spinbox(1, 100, DEFAULT_CHUNK_SIZE_MB, " MB",
        "Size of each upload part (1–100 MB).\n"
        "Files ≤ this size are uploaded in a single request.\n"
        "Files larger than this are split into multiple parts.")
    _add_spin_row(card_lay, "Chunk size", win.chunk_size_spin)

    win.max_chunks_spin = _spinbox(1, 20, DEFAULT_MAX_CHUNKS, " chunks",
        "Maximum number of upload parts sent in parallel (1–20).\n"
        "Higher values improve throughput on fast connections but use more memory.")
    _add_spin_row(card_lay, "Max parallel chunks", win.max_chunks_spin)
    lay.addWidget(card)


def _build_updates_section(win, lay: QVBoxLayout):
    lay.addWidget(_sh("Updates"))
    card     = _card()
    card_lay = QVBoxLayout(card)
    card_lay.setSpacing(8)

    win.update_status_lbl = QLabel(f"Current version: {APP_VERSION}")
    win.update_status_lbl.setObjectName("field_label")
    win.update_status_lbl.setWordWrap(True)
    card_lay.addWidget(win.update_status_lbl)

    win.update_progress = QProgressBar()
    win.update_progress.setValue(0)
    win.update_progress.hide()
    card_lay.addWidget(win.update_progress)

    btn_row = QHBoxLayout()
    win.check_update_btn = QPushButton("Check for updates")
    win.check_update_btn.setObjectName("browse_btn")
    win.check_update_btn.setFixedHeight(36)
    win.check_update_btn.setStyleSheet(
        "min-height:0px; padding:0px 16px; font-size:13px; font-weight:600;"
        "background:#1e1c19; color:#f0ece6; border:1px solid #3d3a35; border-radius:7px;"
    )
    win.check_update_btn.clicked.connect(win._check_for_updates)
    btn_row.addWidget(win.check_update_btn)

    win.install_update_btn = QPushButton("↓  Install update")
    win.install_update_btn.setObjectName("upload_btn")
    win.install_update_btn.setFixedHeight(36)
    win.install_update_btn.setStyleSheet(
        "min-height:0px; padding:0px 16px; font-size:13px; font-weight:700;"
        "background:#c8a96e; color:#111010; border:none; border-radius:7px;"
    )
    win.install_update_btn.clicked.connect(win._install_update)
    win.install_update_btn.hide()
    btn_row.addWidget(win.install_update_btn)
    btn_row.addStretch()
    card_lay.addLayout(btn_row)

    # ── Behaviour checkboxes ──────────────────────────────────────────────────
    win.check_updates_on_launch_cb = QCheckBox("Check for updates on launch")
    win.check_updates_on_launch_cb.setToolTip(
        "Automatically check for a new version each time Mocha Tools starts.\n"
        "If an update is found you will be prompted to download it."
    )
    win.check_updates_on_launch_cb.setChecked(True)   # default on
    card_lay.addWidget(win.check_updates_on_launch_cb)

    win.auto_restart_cb = QCheckBox("Auto-restart after update downloads")
    win.auto_restart_cb.setToolTip(
        "Restart Mocha Tools automatically once an update has finished\n"
        "downloading, without showing a confirmation prompt."
    )
    card_lay.addWidget(win.auto_restart_cb)

    lay.addWidget(card)


# ── Settings persistence ──────────────────────────────────────────────────────

def load_settings(win):
    """Restore persisted QSettings onto win's widgets."""
    s = QSettings(ORG_NAME, APP_NAME)
    # Load API key from OS credential store; migrate from QSettings on first run
    if _KEYRING_OK:
        key = keyring.get_password(_KR_SERVICE, _KR_USER) or ""
        if not key:
            # one-time migration from old plaintext QSettings value
            key = s.value("api_key", "")
            if key:
                keyring.set_password(_KR_SERVICE, _KR_USER, key)
                s.remove("api_key")
    else:
        key = s.value("api_key", "")
    win.api_key_edit.setText(key)
    win.upload_path_edit.setText(s.value("upload_path", "/"))
    win.remote_tab.path_edit.setText(s.value("remote_path", "/"))
    win.remember_cb.setChecked(s.value("remember", False, type=bool))
    win.debug_cb.setChecked(s.value("debug", False, type=bool))
    win.chunk_size_spin.setValue(s.value("chunk_size_mb", DEFAULT_CHUNK_SIZE_MB, type=int))
    win.max_chunks_spin.setValue(s.value("max_chunks", DEFAULT_MAX_CHUNKS, type=int))
    win.mass_conc_spin.setValue(s.value("mass_conc", 2, type=int))
    win.mass_chunk_spin.setValue(s.value("mass_chunk_mb", DEFAULT_CHUNK_SIZE_MB, type=int))
    win.mass_maxchunk_spin.setValue(s.value("mass_max_chunks", DEFAULT_MAX_CHUNKS, type=int))
    win.browser_download_cb.setChecked(s.value("browser_download", False, type=bool))
    win.check_updates_on_launch_cb.setChecked(
        s.value("check_updates_on_launch", True, type=bool)
    )
    win.auto_restart_cb.setChecked(
        s.value("auto_restart_after_update", False, type=bool)
    )

    # Pre-populate shares cache so both tabs render before the first network fetch
    raw = s.value("shares_cache", None)
    if raw:
        try:
            cached = json.loads(raw)
            # Seed the shared remote_cache store so both tabs get instant render
            from ..remote_cache import cache as _rc, registry as _reg
            _rc.set("shares", cached)
            # Also seed the tab-level caches for immediate rendering before
            # the poller's first callback fires
            win.shares_tab._cache = cached
            win.shares_tab._render(cached)
            win.files_tab._shares_cache = cached
            win.files_tab._index_shares(cached)
        except Exception:
            pass


def save_settings(win):
    """Persist win's widget values to QSettings."""
    s = QSettings(ORG_NAME, APP_NAME)
    s.setValue("debug",                     win.debug_cb.isChecked())
    s.setValue("chunk_size_mb",             win.chunk_size_spin.value())
    s.setValue("max_chunks",                win.max_chunks_spin.value())
    s.setValue("mass_conc",                 win.mass_conc_spin.value())
    s.setValue("mass_chunk_mb",             win.mass_chunk_spin.value())
    s.setValue("mass_max_chunks",           win.mass_maxchunk_spin.value())
    s.setValue("browser_download",          win.browser_download_cb.isChecked())
    s.setValue("check_updates_on_launch",   win.check_updates_on_launch_cb.isChecked())
    s.setValue("auto_restart_after_update", win.auto_restart_cb.isChecked())

    cache = win.shares_tab._cache
    if cache is not None:
        try:
            s.setValue("shares_cache", json.dumps(cache))
        except Exception:
            pass

    if win.remember_cb.isChecked():
        if _KEYRING_OK:
            keyring.set_password(_KR_SERVICE, _KR_USER, win.api_key_edit.text())
        else:
            s.setValue("api_key", win.api_key_edit.text())
        s.setValue("upload_path", win.upload_path_edit.text())
        s.setValue("remote_path", win.remote_tab.path_edit.text())
        s.setValue("remember",    True)
    else:
        if _KEYRING_OK:
            try:
                keyring.delete_password(_KR_SERVICE, _KR_USER)
            except keyring.errors.PasswordDeleteError:
                pass
        s.remove("api_key")
        s.remove("upload_path")
        s.remove("remote_path")
        s.setValue("remember", False)


# ── Private helpers ───────────────────────────────────────────────────────────

def _sh(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setObjectName("section_header")
    return lbl


def _card() -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    return f


def _spinbox(min_val: int, max_val: int, default: int,
             suffix: str, tooltip: str) -> QSpinBox:
    sb = QSpinBox()
    sb.setRange(min_val, max_val)
    sb.setValue(default)
    sb.setSuffix(suffix)
    sb.setToolTip(tooltip)
    sb.setMaximumWidth(200)
    return sb


def _add_spin_row(card_lay: QVBoxLayout, label: str, spinbox: QSpinBox):
    row = QHBoxLayout()
    lbl = QLabel(label)
    lbl.setObjectName("field_label")
    row.addWidget(lbl)
    row.addWidget(spinbox, 1)
    card_lay.addLayout(row)