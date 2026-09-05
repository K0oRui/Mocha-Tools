"""
settings.py — Settings tab UI, persistence, and sub-tab builders.

Consolidated from the former tabs/settings_tab.py and
tabs/settings_sections.py into a single module with a clear internal
structure:

  Public API
  ----------
  build_settings_tab(win) -> QWidget
  load_settings(win) -> None
  save_settings(win) -> None

Internal helpers
----------------
  build_basic_tab(win, lay)
  build_upload_tab(win, lay)
  build_updates_tab(win, lay)
  build_appearance_tab(win, lay)
  build_sounds_tab(win, lay)
  _sh(text) -> QLabel
  _card() -> QFrame
  _spinbox(...) -> QSpinBox
  _add_spin_row(card_lay, label, spinbox)
  _install_lucide_spin_arrows(sb) -> overlay
"""

import json
import os

from PySide6.QtCore import QEvent, QObject, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialogButtonBox,
    QFileDialog,
    QFontComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from .constants import (
    APP_NAME,
    APP_VERSION,
    DEFAULT_CHUNK_SIZE_MB,
    DEFAULT_MAX_CHUNKS,
    ORG_NAME,
)
from .upload_pipeline import DEFAULT_GLOBAL_CONCURRENCY
from .theme import (
    BACKGROUND_LABELS,
    DEFAULT_ACCENT,
    DEFAULT_BACKGROUND,
)

try:
    import keyring
    import keyring.errors

    _KEYRING_OK = True
except ImportError:
    _KEYRING_OK = False

_KR_SERVICE = "MochaTools"
_KR_USER = "api_key"

# ── Canonical helpers (from settings_sections) ───────────────────────────────


def _sh(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setObjectName("section_header")
    lbl.setContentsMargins(0, 0, 0, 0)
    lbl.setFixedHeight(18)
    lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    return lbl


def _card() -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    try:
        f.setContentsMargins(6, 6, 6, 6)
    except Exception:
        pass
    return f


def _spinbox(
    min_val: int, max_val: int, default: int, suffix: str, tooltip: str
) -> QSpinBox:
    """Create a QSpinBox with lucide chevron arrow overlays."""
    sb = QSpinBox()
    sb.setRange(min_val, max_val)
    sb.setValue(default)
    sb.setSuffix(suffix)
    sb.setToolTip(tooltip)
    sb.setMaximumWidth(200)
    try:
        sb.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    except Exception:
        pass
    try:
        sb.setFixedHeight(34)
    except Exception:
        pass

    # Try to replace internal arrow button icons after the widget is shown.
    try:
        from PySide6.QtCore import QBuffer, QIODevice

        from .ui.icons import lucide_icon

        def _inject_arrow_images():
            try:
                ico_up = lucide_icon("chevron-up", "#f0ece6", 16)
                ico_dn = lucide_icon("chevron-down", "#f0ece6", 16)
                pm_up = ico_up.pixmap(12, 12)
                pm_dn = ico_dn.pixmap(12, 12)
                buf = QBuffer()
                buf.open(QIODevice.OpenModeFlag.WriteOnly)
                pm_up.save(buf, "PNG")
                b64_up = bytes(buf.data().toBase64()).decode()
                buf.close()
                buf = QBuffer()
                buf.open(QIODevice.OpenModeFlag.WriteOnly)
                pm_dn.save(buf, "PNG")
                b64_dn = bytes(buf.data().toBase64()).decode()
                buf.close()
                css = (
                    "QSpinBox::up-arrow {"
                    f" image: url(data:image/png;base64,{b64_up});"
                    " width:10px; height:6px; } "
                    "QSpinBox::down-arrow {"
                    f" image: url(data:image/png;base64,{b64_dn});"
                    " width:10px; height:6px; }"
                )
                sb.setStyleSheet(sb.styleSheet() + "\n" + css)
            except Exception:
                pass

        QTimer.singleShot(0, _inject_arrow_images)

        def _apply_icons():
            try:
                btns = sb.findChildren(QAbstractButton)
                if not btns:
                    _apply_icons.attempt = getattr(_apply_icons, "attempt", 0) + 1
                    if _apply_icons.attempt < 4:
                        QTimer.singleShot(80, _apply_icons)
                    return
                ico_up = lucide_icon("chevron-up", "#f0ece6", 16)
                ico_dn = lucide_icon("chevron-down", "#f0ece6", 16)
                pm_up = ico_up.pixmap(12, 12)
                pm_dn = ico_dn.pixmap(12, 12)
                from PySide6.QtGui import QIcon

                icon_up = QIcon(pm_up)
                icon_dn = QIcon(pm_dn)
                try:
                    ordered = sorted(
                        btns, key=lambda b: b.mapToParent(b.rect().topLeft()).y()
                    )
                except Exception:
                    ordered = btns
                for i, b in enumerate(ordered[:2]):
                    try:
                        icon = icon_up if i == 0 else icon_dn
                        b.setIcon(icon)
                        b.setIconSize(QSize(10, 10))
                        b.setStyleSheet(
                            "background: transparent; border: none; padding:0px;"
                        )
                    except Exception:
                        pass
                _apply_icons.attempt = getattr(_apply_icons, "attempt", 0) + 1
                if _apply_icons.attempt < 4:
                    QTimer.singleShot(140, _apply_icons)
            except Exception:
                pass

        QTimer.singleShot(0, _apply_icons)
    except Exception:
        pass
    return sb


def _add_spin_row(card_lay: QVBoxLayout, label: str, spinbox: QSpinBox):
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    lbl = QLabel(label)
    lbl.setObjectName("field_label")
    try:
        lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    except Exception:
        pass
    row.addWidget(lbl)
    row.addWidget(spinbox)
    row.addStretch()
    try:
        row.setContentsMargins(0, 0, 12, 0)
    except Exception:
        pass
    card_lay.addLayout(row)

    # Deterministic lucide overlay on every spinbox
    try:
        from PySide6.QtWidgets import QToolButton

        from .ui.icons import lucide_icon

        class _SpinOverlayHandler(QObject):
            def __init__(self, sb: QSpinBox):
                super().__init__(sb)
                self.sb = sb
                self._create()

            def _create(self):
                try:
                    if getattr(self.sb, "_overlay_up_btn", None):
                        return
                    ico_up = lucide_icon("chevron-up", "#f0ece6", 16)
                    ico_dn = lucide_icon("chevron-down", "#f0ece6", 16)
                    up = QToolButton(self.sb)
                    dn = QToolButton(self.sb)
                    up.setIcon(ico_up)
                    dn.setIcon(ico_dn)
                    sz = QSize(12, 12)
                    up.setIconSize(sz)
                    dn.setIconSize(sz)
                    up.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
                    dn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
                    up.setStyleSheet("background: transparent; border: none;")
                    dn.setStyleSheet("background: transparent; border: none;")
                    up.setCursor(self.sb.cursor())
                    dn.setCursor(self.sb.cursor())
                    up.setFixedSize(22, 17)
                    dn.setFixedSize(22, 17)
                    up.clicked.connect(self.sb.stepUp)
                    dn.clicked.connect(self.sb.stepDown)
                    self.sb._overlay_up_btn = up
                    self.sb._overlay_dn_btn = dn
                    self.sb.installEventFilter(self)
                    up.show()
                    up.raise_()
                    dn.show()
                    dn.raise_()
                    self._reposition()
                except Exception:
                    pass

            def eventFilter(self, obj, ev):
                try:
                    if ev.type() in (QEvent.Type.Resize, QEvent.Type.Show):
                        self._reposition()
                except Exception:
                    pass
                return False

            def _reposition(self):
                try:
                    sb = self.sb
                    up = getattr(sb, "_overlay_up_btn", None)
                    dn = getattr(sb, "_overlay_dn_btn", None)
                    if not up or not dn:
                        return
                    w = sb.width()
                    h = sb.height()
                    button_w = 22
                    x = w - button_w
                    up.move(x, max(0, (h // 4) - (up.height() // 2)))
                    dn.move(x, max(0, (3 * h // 4) - (dn.height() // 2)))
                except Exception:
                    pass

        def _make():
            h = _SpinOverlayHandler(spinbox)
            QTimer.singleShot(50, h._reposition)
            QTimer.singleShot(150, h._reposition)

        QTimer.singleShot(0, _make)
    except Exception:
        pass


# ── Lucide spin arrow overlay (for spinboxes not built by _spinbox) ──────────


def _install_lucide_spin_arrows(sb: QSpinBox):
    """Hide a QSpinBox's native up/down buttons and overlay lucide chevron
    buttons instead. Used for the appearance-tab QColorDialog spinboxes."""
    try:
        sb.setStyleSheet(
            "QSpinBox::up-button { width: 0px; border: none; }"
            "QSpinBox::down-button { width: 0px; border: none; }"
        )
        from PySide6.QtWidgets import QToolButton

        from .ui.icons import lucide_icon

        class _SpinOverlay(QObject):
            def __init__(self, spinbox: QSpinBox):
                super().__init__(spinbox)
                self.sb = spinbox
                try:
                    ico_up = lucide_icon("chevron-up", "#f0ece6", 16)
                    ico_dn = lucide_icon("chevron-down", "#f0ece6", 16)
                    up = QToolButton(spinbox)
                    dn = QToolButton(spinbox)
                    up.setIcon(ico_up)
                    dn.setIcon(ico_dn)
                    up.setIconSize(QSize(12, 12))
                    dn.setIconSize(QSize(12, 12))
                    up.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
                    dn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
                    up.setStyleSheet("background: transparent; border: none;")
                    dn.setStyleSheet("background: transparent; border: none;")
                    up.setCursor(spinbox.cursor())
                    dn.setCursor(spinbox.cursor())
                    up.setFixedSize(22, 17)
                    dn.setFixedSize(22, 17)
                    up.clicked.connect(spinbox.stepUp)
                    dn.clicked.connect(spinbox.stepDown)
                    spinbox._overlay_up_btn = up
                    spinbox._overlay_dn_btn = dn
                    spinbox.installEventFilter(self)
                    up.show()
                    dn.show()
                    up.raise_()
                    dn.raise_()
                    self._reposition()
                except Exception:
                    pass

            def eventFilter(self, obj, ev):
                try:
                    if ev.type() in (QEvent.Type.Resize, QEvent.Type.Show):
                        self._reposition()
                except Exception:
                    pass
                return False

            def _reposition(self):
                try:
                    sb = self.sb
                    up = getattr(sb, "_overlay_up_btn", None)
                    dn = getattr(sb, "_overlay_dn_btn", None)
                    if not up or not dn:
                        return
                    w = sb.width()
                    h = sb.height()
                    bw = 22
                    x = w - bw
                    up.move(x, max(0, (h // 4) - (up.height() // 2)))
                    dn.move(x, max(0, (3 * h // 4) - (dn.height() // 2)))
                except Exception:
                    pass

        ov = _SpinOverlay(sb)
        sb._lucide_overlay = ov
        QTimer.singleShot(40, lambda: ov._reposition())
        QTimer.singleShot(120, lambda: ov._reposition())
        return ov
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  SUB-TAB BUILDERS
# ══════════════════════════════════════════════════════════════════════════════

# ── Basic tab ────────────────────────────────────────────────────────────────


def build_basic_tab(win, lay: QVBoxLayout):
    lay.setAlignment(Qt.AlignmentFlag.AlignTop)
    lay.setSpacing(1)
    lay.addWidget(_sh("API"))
    card = _card()
    card_lay = QVBoxLayout(card)
    card_lay.setSpacing(10)

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

    win.remember_cb = QCheckBox("Remember settings across sessions")
    card_lay.addWidget(win.remember_cb)

    win.browser_download_cb = QCheckBox("Use browser for file downloads")
    win.browser_download_cb.setToolTip(
        "When checked, downloads open in your default browser.\n"
        "When unchecked, files download directly through Mocha Tools."
    )
    card_lay.addWidget(win.browser_download_cb)
    lay.addWidget(card)

    # Logging
    lay.addWidget(_sh("Logging"))
    card = _card()
    card_lay = QVBoxLayout(card)
    card_lay.setSpacing(6)

    win.debug_cb = QCheckBox("Enable debug logging")
    win.debug_cb.setToolTip(
        "Show [DEBUG] lines in the status console and log file.\n"
        "Turn off to see only high-level status messages."
    )
    card_lay.addWidget(win.debug_cb)

    note = QLabel(
        "When enabled, all status messages are shown"
        " in the console and written to the log file."
    )
    note.setObjectName("field_label")
    note.setWordWrap(True)
    card_lay.addWidget(note)
    lay.addWidget(card)

    # System tray
    lay.addWidget(_sh("System Tray"))
    card = _card()
    card_lay = QVBoxLayout(card)
    card_lay.setSpacing(6)

    win.minimize_to_tray_cb = QCheckBox("Minimize and close to tray")
    win.minimize_to_tray_cb.setToolTip(
        "When enabled, minimising or closing the window sends Mocha Tools\n"
        "to the system tray instead of quitting. Use the tray icon's menu\n"
        "to reopen the window or quit the app."
    )
    win.minimize_to_tray_cb.toggled.connect(win._on_tray_setting_toggled)
    card_lay.addWidget(win.minimize_to_tray_cb)

    note = QLabel(
        "When disabled, minimising uses the normal taskbar behaviour and "
        "closing the window quits the app."
    )
    note.setObjectName("field_label")
    note.setWordWrap(True)
    card_lay.addWidget(note)
    lay.addWidget(card)


# ── Upload tab ───────────────────────────────────────────────────────────────


def build_upload_tab(win, lay: QVBoxLayout):
    lay.setAlignment(Qt.AlignmentFlag.AlignTop)
    lay.setSpacing(0)
    lay.addWidget(_sh("Global"))
    card = _card()
    card_lay = QVBoxLayout(card)
    card_lay.setSpacing(10)

    win.global_conc_spin = _spinbox(
        1,
        10,
        DEFAULT_GLOBAL_CONCURRENCY,
        " uploads",
        "How many files upload at the same time across all uploads "
        "(single, mass, and sync).\n"
        "Higher values can saturate slower connections.",
    )
    _add_spin_row(card_lay, "Concurrent uploads", win.global_conc_spin)
    lay.addWidget(card)

    lay.addWidget(_sh("Mass Upload"))
    card = _card()
    card_lay = QVBoxLayout(card)
    card_lay.setSpacing(10)

    win.mass_chunk_spin = _spinbox(
        1,
        100,
        DEFAULT_CHUNK_SIZE_MB,
        " MB",
        "Size of each multipart part (1\u2013100 MB)."
        "\nFiles smaller than this upload in one request.",
    )
    _add_spin_row(card_lay, "Chunk size", win.mass_chunk_spin)

    win.mass_maxchunk_spin = _spinbox(
        1,
        20,
        DEFAULT_MAX_CHUNKS,
        " chunks",
        "Max parts sent in parallel per file (1\u201320).",
    )
    _add_spin_row(card_lay, "Parallel chunks", win.mass_maxchunk_spin)
    lay.addWidget(card)

    # Sync section
    lay.addWidget(_sh("Sync"))
    card = _card()
    card_lay = QVBoxLayout(card)
    card_lay.setSpacing(10)

    win.sync_chunk_spin = _spinbox(
        1,
        100,
        DEFAULT_CHUNK_SIZE_MB,
        " MB",
        "Size of each multipart part for sync uploads (1\u2013100 MB).\n"
        "Files smaller than this upload in one request.",
    )
    _add_spin_row(card_lay, "Chunk size", win.sync_chunk_spin)

    win.sync_maxchunk_spin = _spinbox(
        1,
        20,
        DEFAULT_MAX_CHUNKS,
        " chunks",
        "Max parts sent in parallel per file during sync (1\u201320).",
    )
    _add_spin_row(card_lay, "Parallel chunks", win.sync_maxchunk_spin)
    lay.addWidget(card)

    # Multipart
    lay.addWidget(_sh("Multipart Upload"))
    card = _card()
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

    win.chunk_size_spin = _spinbox(
        1,
        100,
        DEFAULT_CHUNK_SIZE_MB,
        " MB",
        "Size of each upload part (1\u2013100 MB).\n"
        "Files \u2264 this size are uploaded in a single request.\n"
        "Files larger than this are split into multiple parts.",
    )
    _add_spin_row(card_lay, "Chunk size", win.chunk_size_spin)

    win.max_chunks_spin = _spinbox(
        1,
        20,
        DEFAULT_MAX_CHUNKS,
        " chunks",
        "Maximum number of upload parts sent in parallel (1\u201320).\n"
        "Higher values improve throughput on fast connections but use more memory.",
    )
    _add_spin_row(card_lay, "Max parallel chunks", win.max_chunks_spin)
    lay.addWidget(card)


# ── Updates tab ──────────────────────────────────────────────────────────────


def build_updates_tab(win, lay: QVBoxLayout):
    lay.setAlignment(Qt.AlignmentFlag.AlignTop)
    lay.setSpacing(0)
    lay.addWidget(_sh("Updates"))
    card = _card()
    card_lay = QVBoxLayout(card)
    card_lay.setSpacing(8)

    try:
        from .updater import _is_portable_windows

        _portable_suffix = " (portable)" if _is_portable_windows() else ""
    except Exception:
        _portable_suffix = ""
    win.update_status_lbl = QLabel(f"Current version: {APP_VERSION}{_portable_suffix}")
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
        "min-height:0px; padding:0px 16px;"
        " font-size:13px; font-weight:600;"
        "background:#1e1c19; color:#f0ece6;"
        " border:1px solid #3d3a35; border-radius:7px;"
    )
    win.check_update_btn.clicked.connect(win._check_for_updates)
    btn_row.addWidget(win.check_update_btn)

    from .theme import get_accent, notifier

    win.install_update_btn = QPushButton("\u2193  Install update")
    win.install_update_btn.setObjectName("upload_btn")
    win.install_update_btn.setFixedHeight(36)
    win.install_update_btn.setStyleSheet(
        f"min-height:0px; padding:0px 16px; font-size:13px; font-weight:700;"
        f"background:{get_accent()}; color:#111010; border:none; border-radius:7px;"
    )
    win.install_update_btn.clicked.connect(win._install_update)
    win.install_update_btn.hide()
    btn_row.addWidget(win.install_update_btn)
    try:
        notifier().accent_changed.connect(
            lambda _old, _new: win.install_update_btn.setStyleSheet(
                f"min-height:0px; padding:0px 16px; font-size:13px; font-weight:700;"
                f"background:{_new}; color:#111010; border:none; border-radius:7px;"
            )
        )
    except Exception:
        pass

    win.release_info_btn = QPushButton("Release info")
    win.release_info_btn.setObjectName("browse_btn")
    win.release_info_btn.setFixedHeight(36)
    win.release_info_btn.setStyleSheet(
        "min-height:0px; padding:0px 16px;"
        " font-size:13px; font-weight:600;"
        "background:#1e1c19; color:#f0ece6;"
        " border:1px solid #3d3a35; border-radius:7px;"
    )
    win.release_info_btn.clicked.connect(win._show_release_info)
    win.release_info_btn.hide()
    btn_row.addWidget(win.release_info_btn)

    btn_row.addStretch()
    card_lay.addLayout(btn_row)

    win.check_updates_on_launch_cb = QCheckBox("Check for updates on launch")
    win.check_updates_on_launch_cb.setToolTip(
        "Automatically check for a new version each time Mocha Tools starts.\n"
        "If an update is found you will be prompted to download it."
    )
    win.check_updates_on_launch_cb.setChecked(True)
    card_lay.addWidget(win.check_updates_on_launch_cb)

    win.auto_restart_cb = QCheckBox("Auto-restart after update downloads")
    win.auto_restart_cb.setToolTip(
        "Restart Mocha Tools automatically once an update has finished\n"
        "downloading, without showing a confirmation prompt."
    )
    card_lay.addWidget(win.auto_restart_cb)

    lay.addWidget(card)


# ── Appearance tab ───────────────────────────────────────────────────────────


def build_appearance_tab(win, lay: QVBoxLayout):
    lay.addWidget(_sh("UI"))
    card = _card()
    card_lay = QVBoxLayout(card)
    card_lay.setSpacing(10)
    card_lay.setContentsMargins(12, 8, 12, 12)

    # Accent colour picker
    pick_lbl = QLabel("Accent colour")
    pick_lbl.setObjectName("field_label")
    try:
        pick_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    except Exception:
        pass
    card_lay.addWidget(pick_lbl)

    preview_row = QHBoxLayout()
    preview_row.setContentsMargins(0, 2, 0, 6)
    preview_row.setSpacing(10)
    win.acc_swatch = QLabel()
    win.acc_swatch.setFixedSize(52, 34)
    win.acc_swatch.setStyleSheet(
        f"border:1px solid #2e2b27; border-radius:8px; background:{DEFAULT_ACCENT};"
    )
    win.acc_hex = QLineEdit()
    win.acc_hex.setReadOnly(True)
    win.acc_hex.setFixedSize(130, 34)
    win.acc_hex.setText(DEFAULT_ACCENT)
    win.acc_hex.setToolTip("Current accent colour (hex)")
    preview_row.addWidget(win.acc_swatch)
    preview_row.addWidget(win.acc_hex)
    preview_row.addStretch()
    card_lay.addLayout(preview_row)

    # Embedded colour picker
    win.acc_dialog = QColorDialog(QColor(DEFAULT_ACCENT), win)
    win.acc_dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
    try:
        win.acc_dialog.setOption(QColorDialog.ColorDialogOption.NoButtons, True)
    except Exception:
        pass
    win.acc_dialog.setWindowFlags(Qt.WindowType.Widget)
    try:
        for bb in win.acc_dialog.findChildren(QDialogButtonBox):
            bb.hide()
    except Exception:
        pass
    card_lay.addWidget(win.acc_dialog)

    def _on_color_changed(col: QColor):
        if not col.isValid():
            return
        hx = col.name()
        win.acc_hex.setText(hx)
        win.acc_swatch.setStyleSheet(
            f"border:1px solid #2e2b27; border-radius:8px; background:{hx};"
        )

    win.acc_dialog.currentColorChanged.connect(_on_color_changed)

    def _style_dialog_spinboxes():
        try:
            for sb in win.acc_dialog.findChildren(QSpinBox):
                _install_lucide_spin_arrows(sb)
        except Exception:
            pass

    QTimer.singleShot(0, _style_dialog_spinboxes)

    # Background theme selector
    bg_row = QHBoxLayout()
    bg_row.setContentsMargins(0, 0, 0, 0)
    bg_row.setSpacing(8)
    bg_lbl = QLabel("Background")
    bg_lbl.setObjectName("field_label")
    try:
        bg_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    except Exception:
        pass
    win.bg_combo = QComboBox()
    win.bg_combo.setFixedHeight(34)
    win.bg_combo.setMinimumWidth(140)
    for key in ("mocha", "white", "black"):
        win.bg_combo.addItem(BACKGROUND_LABELS.get(key, key.title()), key)
    try:
        from .theme import get_background

        current_bg = get_background()
    except Exception:
        current_bg = DEFAULT_BACKGROUND
    idx = win.bg_combo.findData(current_bg)
    if idx >= 0:
        win.bg_combo.setCurrentIndex(idx)
    bg_row.addWidget(bg_lbl)
    bg_row.addWidget(win.bg_combo)
    bg_row.addStretch()
    card_lay.addLayout(bg_row)

    lay.addWidget(card)

    # ── Font section
    lay.addWidget(_sh("Font"))
    font_card = _card()
    font_card_lay = QVBoxLayout(font_card)
    font_card_lay.setSpacing(10)
    font_card_lay.setContentsMargins(12, 8, 12, 12)

    font_row = QHBoxLayout()
    font_row.setSpacing(8)
    font_lbl = QLabel("Font")
    font_lbl.setObjectName("field_label")
    win.font_combo = QFontComboBox()
    win.font_size = _spinbox(8, 24, 13, "", "Font size in points")
    win.font_size.setFixedWidth(80)
    win.font_size.setRange(8, 24)

    try:
        from .theme import get_font

        fam, fsz = get_font()
        try:
            win.font_combo.setCurrentFont(QFont(fam))
        except Exception:
            pass
        try:
            win.font_size.setValue(int(fsz))
        except Exception:
            win.font_size.setValue(13)
    except Exception:
        win.font_size.setValue(13)

    try:
        win.font_combo.setFixedHeight(34)
        try:
            v = win.font_combo.view()
            try:

                class _FixedFontDelegate(QStyledItemDelegate):
                    def initStyleOption(self, option, index):
                        try:
                            super().initStyleOption(option, index)
                        except Exception:
                            pass
                        try:
                            from .theme import DEFAULT_FONT_FAMILY

                            option.font = QFont(DEFAULT_FONT_FAMILY, 12)
                        except Exception:
                            pass

                v.setItemDelegate(_FixedFontDelegate(v))
            except Exception:
                try:
                    v.setFont(QFont(v.font().family(), 12))
                except Exception:
                    pass
            try:
                from .theme import get_font

                fsz = int(get_font()[1])
            except Exception:
                fsz = 12
            v.setStyleSheet(
                f"QListView {{ font-size: {fsz}px; }}"
                " QListView::item {{ height: 26px; }}"
            )
        except Exception:
            pass
        try:
            win.font_combo.setStyleSheet("QComboBox { font-size: 13px; }")
        except Exception:
            pass
    except Exception:
        pass

    # Chevron overlay on font combo
    try:
        from PySide6.QtWidgets import QToolButton

        from .ui.icons import lucide_icon

        class _ComboOverlay(QObject):
            def __init__(self, cmb):
                super().__init__(cmb)
                self.cmb = cmb
                ico = lucide_icon("chevron-down", "#f0ece6", 12)
                btn = QToolButton(cmb)
                btn.setIcon(ico)
                btn.setIconSize(QSize(12, 12))
                btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
                btn.setStyleSheet("background: transparent; border: none;")
                btn.setCursor(cmb.cursor())
                btn.setFixedSize(26, 26)
                btn.clicked.connect(lambda: cmb.showPopup())
                cmb._overlay_btn = btn
                cmb.installEventFilter(self)
                try:
                    btn.show()
                    btn.raise_()
                except Exception:
                    pass

            def eventFilter(self, obj, ev):
                try:
                    if ev.type() in (QEvent.Type.Resize, QEvent.Type.Show):
                        self._reposition()
                except Exception:
                    pass
                return False

            def _reposition(self):
                try:
                    cmb = self.cmb
                    btn = getattr(cmb, "_overlay_btn", None)
                    if not btn:
                        return
                    w = cmb.width()
                    h = cmb.height()
                    x = w - btn.width() - 6
                    y = max(0, (h - btn.height()) // 2)
                    btn.move(x, y)
                except Exception:
                    pass

        try:
            _ComboOverlay(win.font_combo)
        except Exception:
            pass
        try:
            _ComboOverlay(win.bg_combo)
        except Exception:
            pass
        try:
            QTimer.singleShot(
                40,
                lambda: (
                    getattr(win.font_combo, "_overlay_btn", None)
                    and win.font_combo._overlay_btn.raise_()
                ),
            )
        except Exception:
            pass
    except Exception:
        pass

    font_row.addWidget(font_lbl)
    font_row.addWidget(win.font_combo)
    font_row.addStretch()
    font_card_lay.addLayout(font_row)
    _add_spin_row(font_card_lay, "Font size", win.font_size)
    lay.addWidget(font_card)

    # Apply / Reset
    btn_row = QHBoxLayout()
    btn_row.setSpacing(12)
    apply_btn = QPushButton("Apply")
    reset_btn = QPushButton("Reset")
    btn_row.addWidget(apply_btn)
    btn_row.addWidget(reset_btn)
    btn_row.addStretch()
    lay.addLayout(btn_row)

    def _apply():
        hx = win.acc_hex.text() or DEFAULT_ACCENT
        if not hx.startswith("#"):
            hx = "#" + hx
        hx = hx.lower()

        try:
            from .theme import set_accent

            set_accent(hx, persist=bool(win.remember_cb.isChecked()))
            try:
                if hasattr(win, "_refresh_accented_icons"):
                    win._refresh_accented_icons()
            except Exception:
                pass
        except Exception:
            try:
                s = QSettings(ORG_NAME, APP_NAME)
                old = s.value("accent", DEFAULT_ACCENT) or DEFAULT_ACCENT
                s.setValue("accent", hx)
                try:
                    s.sync()
                except Exception:
                    pass
                try:
                    from .theme import notifier

                    notifier().accent_changed.emit(str(old), hx)
                except Exception:
                    pass
            except Exception:
                pass

        try:
            from .theme import set_background

            bg_key = win.bg_combo.currentData() or DEFAULT_BACKGROUND
            set_background(bg_key, persist=bool(win.remember_cb.isChecked()))
        except Exception:
            pass

        try:
            from PySide6.QtWidgets import QApplication

            from .styles import build_stylesheet
            from .theme import get_accent, get_background

            a = QApplication.instance()
            if a:
                try:
                    pal = a.palette()
                    pal.setColor(QPalette.ColorRole.Highlight, QColor(hx))
                    a.setPalette(pal)
                except Exception:
                    pass
                try:
                    a.setStyleSheet(
                        build_stylesheet(get_accent(), background_key=get_background())
                    )
                except Exception:
                    pass
        except Exception:
            pass

        try:
            from .theme import notifier, set_font

            fam = win.font_combo.currentFont().family()
            sz = int(win.font_size.value())
            set_font(fam, sz, persist=bool(win.remember_cb.isChecked()))
            try:
                notifier().font_changed.emit(fam, int(sz))
            except Exception:
                pass
            a = QApplication.instance()
            if a:
                try:
                    a.setFont(QFont(fam, int(sz)))
                except Exception:
                    pass
        except Exception:
            pass

    def _reset():
        win.acc_dialog.setCurrentColor(QColor(DEFAULT_ACCENT))
        try:
            idx = win.bg_combo.findData(DEFAULT_BACKGROUND)
            if idx >= 0:
                win.bg_combo.setCurrentIndex(idx)
        except Exception:
            pass
        try:
            from .theme import DEFAULT_FONT_FAMILY, DEFAULT_FONT_SIZE

            try:
                win.font_combo.setCurrentFont(QFont(DEFAULT_FONT_FAMILY))
            except Exception:
                pass
            try:
                win.font_combo.setCurrentText(DEFAULT_FONT_FAMILY)
            except Exception:
                pass
            try:
                for i in range(win.font_combo.count()):
                    try:
                        if (
                            win.font_combo.itemText(i).lower()
                            == DEFAULT_FONT_FAMILY.lower()
                        ):
                            win.font_combo.setCurrentIndex(i)
                            break
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                win.font_size.setValue(DEFAULT_FONT_SIZE)
            except Exception:
                pass
            try:
                _apply()
            except Exception:
                pass
        except Exception:
            pass

    win.bg_combo.currentIndexChanged.connect(_apply)
    apply_btn.clicked.connect(_apply)
    reset_btn.clicked.connect(_reset)


# ── Sounds tab ───────────────────────────────────────────────────────────────


def build_sounds_tab(win, lay: QVBoxLayout):
    lay.setAlignment(Qt.AlignmentFlag.AlignTop)
    lay.setSpacing(0)
    lay.addWidget(_sh("Sounds"))
    card = _card()
    card_lay = QVBoxLayout(card)
    card_lay.setSpacing(14)

    note = QLabel(
        "Optionally play a sound for each of these events. Leave one unset "
        "and nothing plays for it. Any audio format PySide6 Multimedia can "
        "decode is supported (WAV, MP3, OGG, FLAC, M4A, and more)."
    )
    note.setObjectName("field_label")
    note.setWordWrap(True)
    card_lay.addWidget(note)

    from .sound_player import SOUND_EVENTS, set_sound_path

    win.sound_widgets = {}

    for key, label in SOUND_EVENTS:
        block = QVBoxLayout()
        block.setSpacing(4)

        lbl = QLabel(label)
        lbl.setObjectName("field_label")
        block.addWidget(lbl)

        row = QHBoxLayout()
        row.setSpacing(6)

        path_edit = QLineEdit()
        path_edit.setReadOnly(True)
        path_edit.setPlaceholderText("No sound selected")

        browse_btn = QPushButton("Browse\u2026")
        browse_btn.setObjectName("browse_btn")
        browse_btn.setFixedSize(92, 32)
        browse_btn.setToolTip(f"Choose a sound file for \u201c{label}\u201d")

        reset_btn = QPushButton("Reset")
        reset_btn.setObjectName("browse_btn")
        reset_btn.setFixedSize(70, 32)
        reset_btn.setToolTip(f"Clear the sound for \u201c{label}\u201d")

        row.addWidget(path_edit, 1)
        row.addWidget(browse_btn)
        row.addWidget(reset_btn)
        block.addLayout(row)
        card_lay.addLayout(block)

        win.sound_widgets[key] = {
            "edit": path_edit,
            "browse": browse_btn,
            "reset": reset_btn,
        }

        def _make_browse(k=key, lbl_text=label, edit=path_edit):
            def _browse():
                start_dir = os.path.dirname(edit.text()) if edit.text() else ""
                path, _ = QFileDialog.getOpenFileName(
                    win,
                    f"Select sound for \u201c{lbl_text}\u201d",
                    start_dir,
                    "Audio Files (*.wav *.mp3 *.ogg *.flac"
                    " *.m4a *.aac *.wma *.opus *.aiff);;All Files (*)",
                )
                if path:
                    edit.setText(path)
                    edit.setToolTip(path)
                    set_sound_path(k, path)

            return _browse

        def _make_reset(k=key, edit=path_edit):
            def _reset():
                edit.clear()
                edit.setToolTip("")
                set_sound_path(k, "")

            return _reset

        browse_btn.clicked.connect(_make_browse())
        reset_btn.clicked.connect(_make_reset())

    lay.addWidget(card)


# ══════════════════════════════════════════════════════════════════════════════
#  TOP-LEVEL BUILDER
# ══════════════════════════════════════════════════════════════════════════════


def build_settings_tab(win) -> QWidget:
    """
    Build and return the Settings tab widget.
    All interactive widgets are attached as attributes of `win` so that
    _start_upload, _load_settings, _save_settings, etc. can reach them.
    """
    tab = QWidget()
    tab_lay = QVBoxLayout(tab)
    tab_lay.setContentsMargins(0, 0, 0, 0)
    center_row = QHBoxLayout()
    center_row.setContentsMargins(0, 0, 0, 0)

    from .ui.widgets import FullWidthTabWidget

    tabs = FullWidthTabWidget()
    tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # Build each sub-tab page
    basic_page = QWidget()
    basic_l = QVBoxLayout(basic_page)
    basic_l.setContentsMargins(8, 12, 8, 8)
    basic_l.setSpacing(12)

    upload_page = QWidget()
    upload_l = QVBoxLayout(upload_page)
    upload_l.setContentsMargins(8, 12, 8, 8)
    upload_l.setSpacing(12)

    updates_page = QWidget()
    updates_l = QVBoxLayout(updates_page)
    updates_l.setContentsMargins(8, 12, 8, 8)
    updates_l.setSpacing(12)

    appearance_page = QWidget()
    appearance_l = QVBoxLayout(appearance_page)
    appearance_l.setContentsMargins(8, 12, 8, 8)
    appearance_l.setSpacing(12)

    sounds_page = QWidget()
    sounds_l = QVBoxLayout(sounds_page)
    sounds_l.setContentsMargins(8, 12, 8, 8)
    sounds_l.setSpacing(12)

    for p in (basic_page, upload_page, updates_page, appearance_page, sounds_page):
        try:
            p.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        except Exception:
            pass

    build_basic_tab(win, basic_l)
    build_upload_tab(win, upload_l)
    build_updates_tab(win, updates_l)
    build_appearance_tab(win, appearance_l)
    build_sounds_tab(win, sounds_l)

    # Wrap in scroll areas
    def _make_scroll(widget):
        s = QScrollArea()
        s.setWidgetResizable(True)
        s.setFrameShape(QFrame.Shape.NoFrame)
        s.setWidget(widget)
        s.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        s.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        s.setStyleSheet(
            "QScrollBar:vertical {width:0px;} QScrollBar:horizontal{height:0px;}"
        )
        return s

    tabs.addTab(_make_scroll(basic_page), "Basic")
    tabs.addTab(_make_scroll(upload_page), "Upload")
    tabs.addTab(_make_scroll(updates_page), "Updates")
    tabs.addTab(_make_scroll(appearance_page), "UI")
    tabs.addTab(_make_scroll(sounds_page), "Sounds")

    # Ensure lucide spin arrows raise when UI tab is selected
    def _ensure_accent_spin_arrows(idx=None):
        if idx is not None and idx != 3:
            return
        try:
            spins = [win.font_size]
            try:
                spins.extend(win.acc_dialog.findChildren(QSpinBox))
            except Exception:
                pass
            for sb in spins:
                try:
                    up = getattr(sb, "_overlay_up_btn", None)
                    dn = getattr(sb, "_overlay_dn_btn", None)
                    if up:
                        up.show()
                        up.raise_()
                    if dn:
                        dn.show()
                        dn.raise_()
                except Exception:
                    pass
        except Exception:
            pass

    try:
        tabs.currentChanged.connect(_ensure_accent_spin_arrows)
    except Exception:
        pass
    QTimer.singleShot(120, lambda: _ensure_accent_spin_arrows(None))

    center_row.addWidget(tabs, 1)
    tab_lay.addLayout(center_row, 1)
    return tab


# ══════════════════════════════════════════════════════════════════════════════
#  SETTINGS PERSISTENCE
# ══════════════════════════════════════════════════════════════════════════════


def load_settings(win):
    """Restore persisted QSettings onto win's widgets."""
    s = QSettings(ORG_NAME, APP_NAME)
    if _KEYRING_OK:
        key = keyring.get_password(_KR_SERVICE, _KR_USER) or ""
        if not key:
            key = s.value("api_key", "")
            if key:
                keyring.set_password(_KR_SERVICE, _KR_USER, key)
                s.remove("api_key")
    else:
        key = s.value("api_key", "")
    win.api_key_edit.setText(key)
    if hasattr(win, "upload_path_edit") and win.upload_path_edit is not None:
        win.upload_path_edit.setText(s.value("upload_path", "/"))
    if hasattr(win, "remote_tab") and hasattr(win.remote_tab, "path_edit"):
        win.remote_tab.path_edit.setText(s.value("remote_path", "/"))

    win.remember_cb.setChecked(s.value("remember", False, type=bool))
    win.debug_cb.setChecked(s.value("debug", False, type=bool))
    win.minimize_to_tray_cb.setChecked(s.value("minimize_to_tray", False, type=bool))
    win.chunk_size_spin.setValue(
        s.value("chunk_size_mb", DEFAULT_CHUNK_SIZE_MB, type=int)
    )
    win.max_chunks_spin.setValue(s.value("max_chunks", DEFAULT_MAX_CHUNKS, type=int))
    win.global_conc_spin.setValue(
        s.value("global_conc", DEFAULT_GLOBAL_CONCURRENCY, type=int)
    )
    win.mass_chunk_spin.setValue(
        s.value("mass_chunk_mb", DEFAULT_CHUNK_SIZE_MB, type=int)
    )
    win.mass_maxchunk_spin.setValue(
        s.value("mass_max_chunks", DEFAULT_MAX_CHUNKS, type=int)
    )
    win.sync_chunk_spin.setValue(
        s.value("sync_chunk_mb", DEFAULT_CHUNK_SIZE_MB, type=int)
    )
    win.sync_maxchunk_spin.setValue(
        s.value("sync_max_chunks", DEFAULT_MAX_CHUNKS, type=int)
    )
    win.browser_download_cb.setChecked(s.value("browser_download", False, type=bool))
    win.check_updates_on_launch_cb.setChecked(
        s.value("check_updates_on_launch", True, type=bool)
    )
    win.auto_restart_cb.setChecked(
        s.value("auto_restart_after_update", False, type=bool)
    )

    # Sound settings
    try:
        from .sound_player import SOUND_EVENTS, sound_path

        for _key, _label in SOUND_EVENTS:
            widgets = getattr(win, "sound_widgets", {}).get(_key)
            if not widgets:
                continue
            p = sound_path(_key)
            widgets["edit"].setText(p)
            widgets["edit"].setToolTip(p)
    except Exception:
        pass

    # Accent color
    try:
        accent = s.value("accent", None)
        if accent and getattr(win, "acc_hex", None) is not None:
            win.acc_hex.setText(accent)
            win.acc_swatch.setStyleSheet(
                f"border:1px solid #2e2b27; border-radius:8px; background:{accent};"
            )
            if getattr(win, "acc_dialog", None) is not None:
                try:
                    win.acc_dialog.setCurrentColor(QColor(accent))
                except Exception:
                    pass
        if accent and getattr(win, "accent_swatch", None) is not None:
            win.accent_swatch.setStyleSheet(
                f"border:1px solid #2e2b27; border-radius:3px; background:{accent};"
            )
    except Exception:
        pass

    # Background theme
    try:
        bg_key = s.value("background", None)
        if bg_key and getattr(win, "bg_combo", None) is not None:
            idx = win.bg_combo.findData(str(bg_key).lower())
            if idx >= 0:
                win.bg_combo.setCurrentIndex(idx)
    except Exception:
        pass

    # Pre-populate shares cache
    raw = s.value("shares_cache", None)
    if raw:
        try:
            cached = json.loads(raw)
            from .remote_cache import cache as _rc

            _rc.set("shares", cached)
            win.shares_tab._cache = cached
            win.shares_tab._render(cached)
            win.files_tab._shares_cache = cached
            win.files_tab._index_shares(cached)
        except Exception:
            pass


def save_settings(win):
    """Persist win's widget values to QSettings."""
    s = QSettings(ORG_NAME, APP_NAME)
    s.setValue("debug", win.debug_cb.isChecked())
    s.setValue("minimize_to_tray", win.minimize_to_tray_cb.isChecked())
    s.setValue("chunk_size_mb", win.chunk_size_spin.value())
    s.setValue("max_chunks", win.max_chunks_spin.value())
    s.setValue("global_conc", win.global_conc_spin.value())
    s.setValue("mass_chunk_mb", win.mass_chunk_spin.value())
    s.setValue("mass_max_chunks", win.mass_maxchunk_spin.value())
    s.setValue("sync_chunk_mb", win.sync_chunk_spin.value())
    s.setValue("sync_max_chunks", win.sync_maxchunk_spin.value())
    s.setValue("browser_download", win.browser_download_cb.isChecked())
    s.setValue("check_updates_on_launch", win.check_updates_on_launch_cb.isChecked())
    s.setValue("auto_restart_after_update", win.auto_restart_cb.isChecked())

    # Sound settings
    try:
        from .sound_player import SOUND_EVENTS, set_sound_path

        for _key, _label in SOUND_EVENTS:
            widgets = getattr(win, "sound_widgets", {}).get(_key)
            if not widgets:
                continue
            set_sound_path(_key, widgets["edit"].text().strip())
    except Exception:
        pass

    cache = getattr(getattr(win, "shares_tab", None), "_cache", None)
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
        if hasattr(win, "upload_path_edit") and win.upload_path_edit is not None:
            s.setValue("upload_path", win.upload_path_edit.text())
        if hasattr(win, "remote_tab") and hasattr(win.remote_tab, "path_edit"):
            s.setValue("remote_path", win.remote_tab.path_edit.text())
        s.setValue("remember", True)
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

    try:
        from .theme import get_accent

        if win.remember_cb.isChecked():
            s.setValue("accent", get_accent())
        else:
            try:
                s.remove("accent")
            except Exception:
                pass
        try:
            s.sync()
        except Exception:
            pass
    except Exception:
        pass

    try:
        from .theme import get_background

        if win.remember_cb.isChecked():
            s.setValue("background", get_background())
        else:
            try:
                s.remove("background")
            except Exception:
                pass
        try:
            s.sync()
        except Exception:
            pass
    except Exception:
        pass

    try:
        s.sync()
    except Exception:
        pass
