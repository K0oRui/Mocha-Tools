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
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QProgressBar, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
    QSizePolicy,
    QFontComboBox,
)

from ..constants import (
    APP_NAME, APP_VERSION, DEFAULT_CHUNK_SIZE_MB, DEFAULT_MAX_CHUNKS, ORG_NAME,
)
from ..theme import DEFAULT_ACCENT, BACKGROUND_THEMES, BACKGROUND_LABELS, DEFAULT_BACKGROUND


# ── Settings tab UI ───────────────────────────────────────────────────────────

def build_settings_tab(win) -> QWidget:
    """
    Build and return the Settings tab widget.
    All interactive widgets are attached as attributes of `win` so that
    _start_upload, _load_settings, _save_settings, etc. can reach them.
    """
    tab    = QWidget()
    tab_lay = QVBoxLayout(tab)
    tab_lay.setContentsMargins(0, 0, 0, 0)
    center_row = QHBoxLayout()
    center_row.setContentsMargins(0, 0, 0, 0)

    # We'll show the tab bar fixed at the top and make each tab page a
    # scrollable area. This prevents the tab bar and its pages from
    # "floating" inside a single scroll area which caused the large gap.

    # Split settings into tabs for easier maintenance
    from .settings_sections import (
        build_basic_tab, build_upload_tab, build_updates_tab, build_sounds_tab,
    )
    # Use the app's FullWidthTabWidget so sub-tabs match the main upper tabs
    from ..ui.widgets import FullWidthTabWidget

    tabs = FullWidthTabWidget()
    tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # Each tab page uses a scroll area with slightly larger top padding so
    # section headers don't overlap the tab underline.
    from PyQt6.QtWidgets import QScrollArea
    basic_page = QWidget(); basic_l = QVBoxLayout(basic_page); basic_l.setContentsMargins(8, 12, 8, 8); basic_l.setSpacing(12)
    upload_page = QWidget(); upload_l = QVBoxLayout(upload_page); upload_l.setContentsMargins(8, 12, 8, 8); upload_l.setSpacing(12)
    updates_page = QWidget(); updates_l = QVBoxLayout(updates_page); updates_l.setContentsMargins(8, 12, 8, 8); updates_l.setSpacing(12)
    sounds_page = QWidget(); sounds_l = QVBoxLayout(sounds_page); sounds_l.setContentsMargins(8, 12, 8, 8); sounds_l.setSpacing(12)
    for p in (basic_page, upload_page, updates_page, sounds_page):
        try:
            p.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        except Exception:
            pass

    build_basic_tab(win, basic_l)
    build_upload_tab(win, upload_l)
    build_updates_tab(win, updates_l)
    build_sounds_tab(win, sounds_l)

    # Appearance / Accent tab
    def _build_appearance_tab(win, lay: QVBoxLayout):
        # rename tab label to UI and use a simpler header
        lay.addWidget(_sh("UI"))
        card = _card(); card_lay = QVBoxLayout(card); card_lay.setSpacing(10); card_lay.setContentsMargins(12,8,12,12)

        # ── Accent colour picker ─────────────────────────────────────────────
        # A small live preview (swatch + hex) above the picker itself.
        pick_lbl = QLabel("Accent colour"); pick_lbl.setObjectName("field_label")
        try:
            from PyQt6.QtWidgets import QSizePolicy as _SP
            pick_lbl.setSizePolicy(_SP.Policy.Fixed, _SP.Policy.Fixed)
        except Exception:
            pass
        card_lay.addWidget(pick_lbl)

        preview_row = QHBoxLayout(); preview_row.setContentsMargins(0, 2, 0, 6); preview_row.setSpacing(10)
        win.acc_swatch = QLabel()
        win.acc_swatch.setFixedSize(52, 34)
        win.acc_swatch.setStyleSheet(
            f"border:1px solid #2e2b27; border-radius:8px; background:{DEFAULT_ACCENT};"
        )
        win.acc_hex = QLineEdit(); win.acc_hex.setReadOnly(True); win.acc_hex.setFixedSize(130, 34)
        win.acc_hex.setText(DEFAULT_ACCENT)
        win.acc_hex.setToolTip("Current accent colour (hex)")
        preview_row.addWidget(win.acc_swatch)
        preview_row.addWidget(win.acc_hex)
        preview_row.addStretch()
        card_lay.addLayout(preview_row)

        # Embed the colour picker directly in the page instead of opening it
        # as a separate dialog — there's no point making the user open a
        # popup for something that fits fine inline.
        from PyQt6.QtWidgets import QColorDialog, QDialogButtonBox
        win.acc_dialog = QColorDialog(QColor(DEFAULT_ACCENT), win)
        win.acc_dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
        try:
            win.acc_dialog.setOption(QColorDialog.ColorDialogOption.NoButtons, True)
        except Exception:
            pass
        win.acc_dialog.setWindowFlags(Qt.WindowType.Widget)
        # Belt-and-braces: hide any OK/Cancel button box that may still be
        # present so only the picker controls themselves are shown.
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
            win.acc_swatch.setStyleSheet(f"border:1px solid #2e2b27; border-radius:8px; background:{hx};")

        win.acc_dialog.currentColorChanged.connect(_on_color_changed)

        # Give the embedded picker's own Hue/Sat/Val/Red/Green/Blue spinboxes
        # the same lucide chevron treatment used everywhere else in the app.
        def _style_dialog_spinboxes():
            try:
                for sb in win.acc_dialog.findChildren(QSpinBox):
                    _install_lucide_spin_arrows(sb)
            except Exception:
                pass
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, _style_dialog_spinboxes)

        # Background theme selector (Mocha / White / Black)
        bg_row = QHBoxLayout(); bg_row.setContentsMargins(0, 0, 0, 0); bg_row.setSpacing(8)
        bg_lbl = QLabel("Background"); bg_lbl.setObjectName("field_label")
        try:
            from PyQt6.QtWidgets import QSizePolicy as _SP
            bg_lbl.setSizePolicy(_SP.Policy.Fixed, _SP.Policy.Fixed)
        except Exception:
            pass
        win.bg_combo = QComboBox(); win.bg_combo.setFixedHeight(34); win.bg_combo.setMinimumWidth(140)
        for key in ("mocha", "white", "black"):
            win.bg_combo.addItem(BACKGROUND_LABELS.get(key, key.title()), key)
        try:
            from ..theme import get_background
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

        # ── Font (its own section, separate from accent/background) ──────────
        lay.addWidget(_sh("Font"))
        font_card = _card(); font_card_lay = QVBoxLayout(font_card)
        font_card_lay.setSpacing(10); font_card_lay.setContentsMargins(12, 8, 12, 12)

        font_row = QHBoxLayout(); font_row.setSpacing(8)
        font_lbl = QLabel("Font")
        font_lbl.setObjectName("field_label")
        win.font_combo = QFontComboBox()
        win.font_size = _spinbox(8, 24, 13, "", "Font size in points")
        win.font_size.setFixedWidth(80)   # must be set before overlay timers fire
        win.font_size.setRange(8, 24)  # re-apply after _spinbox (no-op, just safe)
        # initialize font controls from persisted/runtime values
        try:
            from ..theme import get_font
            from PyQt6.QtGui import QFont
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
        # make font combo visually compact to match other controls
        try:
            from PyQt6.QtGui import QFont
            win.font_combo.setFixedHeight(34)
            # enforce a compact, uniform rendering for the popup list so items
            # don't render using their own family at large sizes
            try:
                v = win.font_combo.view()
                try:
                    # Prevent the popup from rendering each item using its own
                    # font by installing a delegate that forces a uniform
                    # preview font and item height.
                    from PyQt6.QtWidgets import QStyledItemDelegate

                    class _FixedFontDelegate(QStyledItemDelegate):
                        def initStyleOption(self, option, index):
                            try:
                                super().initStyleOption(option, index)
                            except Exception:
                                pass
                            try:
                                option.font = QFont(DEFAULT_FONT_FAMILY, 12)
                            except Exception:
                                pass

                    v.setItemDelegate(_FixedFontDelegate(v))
                except Exception:
                    try:
                        v.setFont(QFont(v.font().family(), 12))
                    except Exception:
                        pass
                # tighten item height and font size via stylesheet as a fallback
                try:
                    try:
                        from ..theme import get_font
                        fsz = int(get_font()[1])
                    except Exception:
                        fsz = 12
                    v.setStyleSheet(f"QListView {{ font-size: {fsz}px; }} QListView::item {{ height: 26px; }}")
                except Exception:
                    pass
            except Exception:
                pass
            # also ensure the combo itself uses a normal UI font for the current
            # selection display (prevents the selected name rendering in huge
            # sample sizes for its own font)
            try:
                win.font_combo.setStyleSheet("QComboBox { font-size: 13px; }")
            except Exception:
                pass
        except Exception:
            pass
        # add a chevron overlay on the right to mimic the spinbox arrows
        try:
            from ..ui.icons import lucide_icon
            from PyQt6.QtCore import QEvent, QSize, QObject
            # NOTE: Qt is already imported at module scope; importing it again
            # here would make Python treat `Qt` as a function-local everywhere
            # in build_settings_tab, breaking earlier uses (UnboundLocalError).
            from PyQt6.QtWidgets import QToolButton
            class _ComboOverlay(QObject):
                def __init__(self, cmb):
                    super().__init__(cmb)
                    self.cmb = cmb
                    ico = lucide_icon('chevron-down', '#f0ece6', 12)
                    btn = QToolButton(cmb)
                    btn.setIcon(ico)
                    btn.setIconSize(QSize(12, 12))
                    btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
                    btn.setStyleSheet('background: transparent; border: none;')
                    btn.setCursor(cmb.cursor())
                    btn.setFixedSize(26, 26)
                    btn.clicked.connect(lambda: cmb.showPopup())
                    cmb._overlay_btn = btn
                    cmb.installEventFilter(self)
                    try:
                        btn.show(); btn.raise_()
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
                        btn = getattr(cmb, '_overlay_btn', None)
                        if not btn:
                            return
                        w = cmb.width(); h = cmb.height()
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
            # ensure overlay positioned after initial layout
            try:
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(40, lambda: getattr(win.font_combo, '_overlay_btn', None) and getattr(win.font_combo, '_overlay_btn').raise_())
            except Exception:
                pass
        except Exception:
            pass
        # Font family row
        font_row.addWidget(font_lbl)
        font_row.addWidget(win.font_combo)
        font_row.addStretch()
        font_card_lay.addLayout(font_row)
        # Font size on its own row so the spinbox has a clean fixed width
        # and _add_spin_row can install the lucide arrow overlay correctly.
        _add_spin_row(font_card_lay, "Font size", win.font_size)
        lay.addWidget(font_card)

        # Apply / Reset — applies accent, background, and font together.
        btn_row = QHBoxLayout(); btn_row.setSpacing(12)
        apply_btn = QPushButton("Apply")
        reset_btn = QPushButton("Reset")
        btn_row.addWidget(apply_btn); btn_row.addWidget(reset_btn); btn_row.addStretch()
        lay.addLayout(btn_row)

        def _apply():
            # Apply accent and font selections to the running app and persist
            hx = win.acc_hex.text() or DEFAULT_ACCENT
            if not hx.startswith("#"):
                hx = "#" + hx
            hx = hx.lower()

            # Persist accent via theme helper when available, fallback to QSettings
            try:
                from ..theme import set_accent
                set_accent(hx, persist=bool(win.remember_cb.isChecked()))
                try:
                    if hasattr(win, '_refresh_accented_icons'):
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
                        from ..theme import notifier
                        notifier().accent_changed.emit(str(old), hx)
                    except Exception:
                        pass
                except Exception:
                    pass

            # Persist/apply background theme selection
            try:
                from ..theme import set_background
                bg_key = win.bg_combo.currentData() or DEFAULT_BACKGROUND
                set_background(bg_key, persist=bool(win.remember_cb.isChecked()))
            except Exception:
                pass

            # Update palette and global stylesheet
            try:
                from PyQt6.QtWidgets import QApplication
                from ..styles import build_stylesheet
                from ..theme import get_accent, get_background
                a = QApplication.instance()
                if a:
                    try:
                        pal = a.palette()
                        pal.setColor(QPalette.ColorRole.Highlight, QColor(hx))
                        a.setPalette(pal)
                    except Exception:
                        pass
                    try:
                        a.setStyleSheet(build_stylesheet(get_accent(), background_key=get_background()))
                    except Exception:
                        pass
            except Exception:
                pass

            # Apply font selection
            try:
                from ..theme import set_font, notifier
                from PyQt6.QtGui import QFont
                from PyQt6.QtWidgets import QApplication
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
            # reset background theme to default (mocha)
            try:
                idx = win.bg_combo.findData(DEFAULT_BACKGROUND)
                if idx >= 0:
                    win.bg_combo.setCurrentIndex(idx)
            except Exception:
                pass
            # reset font to defaults
            try:
                from ..theme import DEFAULT_FONT_FAMILY, DEFAULT_FONT_SIZE
                from PyQt6.QtGui import QFont
                # Try several methods to ensure the combo actually selects the family
                try:
                    win.font_combo.setCurrentFont(QFont(DEFAULT_FONT_FAMILY))
                except Exception:
                    pass
                try:
                    win.font_combo.setCurrentText(DEFAULT_FONT_FAMILY)
                except Exception:
                    pass
                # Try selecting by iterating items (some QFontComboBox implementations)
                try:
                    for i in range(win.font_combo.count()):
                        try:
                            if win.font_combo.itemText(i).lower() == DEFAULT_FONT_FAMILY.lower():
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
                # apply the reset values immediately
                try:
                    _apply()
                except Exception:
                    pass
            except Exception:
                pass

        win.bg_combo.currentIndexChanged.connect(_apply)
        apply_btn.clicked.connect(_apply)
        reset_btn.clicked.connect(_reset)

    appearance_page = QWidget(); appearance_l = QVBoxLayout(appearance_page); appearance_l.setContentsMargins(8,12,8,8); appearance_l.setSpacing(12)
    _build_appearance_tab(win, appearance_l)

    # Wrap pages in QScrollArea so pages are scrollable but scrollbar
    # widgets are hidden for a cleaner look.
    basic_scroll = QScrollArea()
    basic_scroll.setWidgetResizable(True)
    basic_scroll.setFrameShape(QFrame.Shape.NoFrame)
    basic_scroll.setWidget(basic_page)
    basic_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    basic_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    # Use overlay scrollbars via stylesheet so scrolling still works with mouse/trackpad
    basic_scroll.setStyleSheet("QScrollBar:vertical {width:0px;} QScrollBar:horizontal{height:0px;}")

    upload_scroll = QScrollArea()
    upload_scroll.setWidgetResizable(True)
    upload_scroll.setFrameShape(QFrame.Shape.NoFrame)
    upload_scroll.setWidget(upload_page)
    upload_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    upload_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    upload_scroll.setStyleSheet("QScrollBar:vertical {width:0px;} QScrollBar:horizontal{height:0px;}")

    updates_scroll = QScrollArea()
    updates_scroll.setWidgetResizable(True)
    updates_scroll.setFrameShape(QFrame.Shape.NoFrame)
    updates_scroll.setWidget(updates_page)
    updates_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    updates_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    updates_scroll.setStyleSheet("QScrollBar:vertical {width:0px;} QScrollBar:horizontal{height:0px;}")

    appearance_scroll = QScrollArea()
    appearance_scroll.setWidgetResizable(True)
    appearance_scroll.setFrameShape(QFrame.Shape.NoFrame)
    appearance_scroll.setWidget(appearance_page)
    appearance_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    appearance_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    appearance_scroll.setStyleSheet("QScrollBar:vertical {width:0px;} QScrollBar:horizontal{height:0px;}")

    sounds_scroll = QScrollArea()
    sounds_scroll.setWidgetResizable(True)
    sounds_scroll.setFrameShape(QFrame.Shape.NoFrame)
    sounds_scroll.setWidget(sounds_page)
    sounds_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    sounds_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    sounds_scroll.setStyleSheet("QScrollBar:vertical {width:0px;} QScrollBar:horizontal{height:0px;}")

    tabs.addTab(basic_scroll, "Basic")
    tabs.addTab(upload_scroll, "Upload")
    tabs.addTab(updates_scroll, "Updates")
    tabs.addTab(appearance_scroll, "UI")
    tabs.addTab(sounds_scroll, "Sounds")

    # When the UI tab becomes visible, raise any overlay buttons that may have
    # been obscured by the tab widget's own paint pass.
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
                    up = getattr(sb, '_overlay_up_btn', None)
                    dn = getattr(sb, '_overlay_dn_btn', None)
                    if up:
                        up.show(); up.raise_()
                    if dn:
                        dn.show(); dn.raise_()
                except Exception:
                    pass
        except Exception:
            pass

    try:
        tabs.currentChanged.connect(_ensure_accent_spin_arrows)
    except Exception:
        pass
    from PyQt6.QtCore import QTimer
    QTimer.singleShot(120, lambda: _ensure_accent_spin_arrows(None))

    center_row.addWidget(tabs, 1)
    tab_lay.addLayout(center_row, 1)
    lay = tab_lay  # keep variable for callers if needed
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


def _build_sync_section(win, lay: QVBoxLayout):
    lay.addWidget(_sh("Sync"))
    card     = _card()
    card_lay = QVBoxLayout(card)
    card_lay.setSpacing(10)

    win.sync_conc_spin = _spinbox(1, 10, 2, " files",
        "How many files the sync watcher uploads at the same time.\n"
        "Higher values can saturate slower connections.")
    _add_spin_row(card_lay, "Concurrent files", win.sync_conc_spin)

    win.sync_chunk_spin = _spinbox(1, 100, DEFAULT_CHUNK_SIZE_MB, " MB",
        "Size of each multipart part for sync uploads (1–100 MB).\n"
        "Files smaller than this upload in one request.")
    _add_spin_row(card_lay, "Chunk size", win.sync_chunk_spin)

    win.sync_maxchunk_spin = _spinbox(1, 20, DEFAULT_MAX_CHUNKS, " chunks",
        "Max parts sent in parallel per file during sync (1–20).")
    _add_spin_row(card_lay, "Parallel chunks", win.sync_maxchunk_spin)
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

    try:
        from ..updater import _is_portable_windows
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
        "min-height:0px; padding:0px 16px; font-size:13px; font-weight:600;"
        "background:#1e1c19; color:#f0ece6; border:1px solid #3d3a35; border-radius:7px;"
    )
    win.check_update_btn.clicked.connect(win._check_for_updates)
    btn_row.addWidget(win.check_update_btn)

    win.install_update_btn = QPushButton("↓  Install update")
    win.install_update_btn.setObjectName("upload_btn")
    win.install_update_btn.setFixedHeight(36)
    try:
        from ..theme import get_accent
        _inst_acc = get_accent()
    except Exception:
        _inst_acc = "#c8a96e"
    win.install_update_btn.setStyleSheet(
        f"min-height:0px; padding:0px 16px; font-size:13px; font-weight:700;"
        f"background:{_inst_acc}; color:#111010; border:none; border-radius:7px;"
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
    if hasattr(win, 'upload_path_edit') and win.upload_path_edit is not None:
        win.upload_path_edit.setText(s.value("upload_path", "/"))
    if hasattr(win, 'remote_tab') and hasattr(win.remote_tab, 'path_edit'):
        win.remote_tab.path_edit.setText(s.value("remote_path", "/"))

    win.remember_cb.setChecked(s.value("remember", False, type=bool))
    win.debug_cb.setChecked(s.value("debug", False, type=bool))
    win.minimize_to_tray_cb.setChecked(s.value("minimize_to_tray", False, type=bool))
    win.chunk_size_spin.setValue(s.value("chunk_size_mb", DEFAULT_CHUNK_SIZE_MB, type=int))
    win.max_chunks_spin.setValue(s.value("max_chunks", DEFAULT_MAX_CHUNKS, type=int))
    win.mass_conc_spin.setValue(s.value("mass_conc", 2, type=int))
    win.mass_chunk_spin.setValue(s.value("mass_chunk_mb", DEFAULT_CHUNK_SIZE_MB, type=int))
    win.mass_maxchunk_spin.setValue(s.value("mass_max_chunks", DEFAULT_MAX_CHUNKS, type=int))
    win.sync_conc_spin.setValue(s.value("sync_conc", 2, type=int))
    win.sync_chunk_spin.setValue(s.value("sync_chunk_mb", DEFAULT_CHUNK_SIZE_MB, type=int))
    win.sync_maxchunk_spin.setValue(s.value("sync_max_chunks", DEFAULT_MAX_CHUNKS, type=int))
    win.browser_download_cb.setChecked(s.value("browser_download", False, type=bool))
    win.check_updates_on_launch_cb.setChecked(
        s.value("check_updates_on_launch", True, type=bool)
    )
    win.auto_restart_cb.setChecked(
        s.value("auto_restart_after_update", False, type=bool)
    )

    # Sound settings — populate each event's path field from QSettings
    try:
        from ..sound_player import SOUND_EVENTS, sound_path
        for _key, _label in SOUND_EVENTS:
            widgets = getattr(win, "sound_widgets", {}).get(_key)
            if not widgets:
                continue
            p = sound_path(_key)
            widgets["edit"].setText(p)
            widgets["edit"].setToolTip(p)
    except Exception:
        pass
    # Accent color — update appearance tab controls if present
    try:
        accent = s.value("accent", None)
        if accent and getattr(win, 'acc_hex', None) is not None:
            win.acc_hex.setText(accent)
            win.acc_swatch.setStyleSheet(f"border:1px solid #2e2b27; border-radius:8px; background:{accent};")
            if getattr(win, 'acc_dialog', None) is not None:
                try:
                    win.acc_dialog.setCurrentColor(QColor(accent))
                except Exception:
                    pass
        # legacy swatch used earlier
        if accent and getattr(win, 'accent_swatch', None) is not None:
            win.accent_swatch.setStyleSheet(f"border:1px solid #2e2b27; border-radius:3px; background:{accent};")
    except Exception:
        pass

    # Background theme — update the UI tab's dropdown if present
    try:
        bg_key = s.value("background", None)
        if bg_key and getattr(win, 'bg_combo', None) is not None:
            idx = win.bg_combo.findData(str(bg_key).lower())
            if idx >= 0:
                win.bg_combo.setCurrentIndex(idx)
    except Exception:
        pass

    # Pre-populate shares cache so both tabs render before the first network fetch
    raw = s.value("shares_cache", None)
    if raw:
        try:
            cached = json.loads(raw)
            # Seed the shared remote_cache store so both tabs get instant render
            from ..remote_cache import cache as _rc, registry as _reg
            _rc.set("shares", cached)
            # Also seed t tab-level caches for immediate rendering before
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
    s.setValue("minimize_to_tray",           win.minimize_to_tray_cb.isChecked())
    s.setValue("chunk_size_mb",             win.chunk_size_spin.value())
    s.setValue("max_chunks",                win.max_chunks_spin.value())
    s.setValue("mass_conc",                 win.mass_conc_spin.value())
    s.setValue("mass_chunk_mb",             win.mass_chunk_spin.value())
    s.setValue("mass_max_chunks",           win.mass_maxchunk_spin.value())
    s.setValue("sync_conc",                 win.sync_conc_spin.value())
    s.setValue("sync_chunk_mb",             win.sync_chunk_spin.value())
    s.setValue("sync_max_chunks",           win.sync_maxchunk_spin.value())
    s.setValue("browser_download",          win.browser_download_cb.isChecked())
    s.setValue("check_updates_on_launch",   win.check_updates_on_launch_cb.isChecked())
    s.setValue("auto_restart_after_update", win.auto_restart_cb.isChecked())

    # Sound settings — already persisted live as they're picked/reset, but
    # save here too so the on-disk value always matches what's shown.
    try:
        from ..sound_player import SOUND_EVENTS, set_sound_path
        for _key, _label in SOUND_EVENTS:
            widgets = getattr(win, "sound_widgets", {}).get(_key)
            if not widgets:
                continue
            set_sound_path(_key, widgets["edit"].text().strip())
    except Exception:
        pass

    cache = getattr(getattr(win, 'shares_tab', None), '_cache', None)
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
        if hasattr(win, 'upload_path_edit') and win.upload_path_edit is not None:
            s.setValue("upload_path", win.upload_path_edit.text())
        if hasattr(win, 'remote_tab') and hasattr(win.remote_tab, 'path_edit'):
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

    # Persist accent only when 'Remember settings' is checked (consistent)
    try:
        from ..theme import get_accent
        if win.remember_cb.isChecked():
            s.setValue("accent", get_accent())
        else:
            # remove persisted accent so next launch uses DEFAULT or runtime value
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

    # Persist background theme only when 'Remember settings' is checked
    try:
        from ..theme import get_background
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
    # Make sure values are flushed to the system store
    try:
        s.sync()
    except Exception:
        pass
# ── Private helpers ───────────────────────────────────────────────────────────

def _sh(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setObjectName("section_header")
    lbl.setContentsMargins(0, 0, 0, 0)
    lbl.setFixedHeight(18)
    lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    return lbl


def _install_lucide_spin_arrows(sb: QSpinBox):
    """Hide a QSpinBox's native up/down buttons and overlay lucide chevron
    buttons instead. Same visual treatment as the Upload-tab / Fine-tune
    RGB spinboxes, factored out so it can also be applied to spinboxes we
    don't build ourselves (e.g. the ones inside Qt's built-in QColorDialog).
    """
    try:
        sb.setStyleSheet(
            "QSpinBox::up-button { width: 0px; border: none; }"
            "QSpinBox::down-button { width: 0px; border: none; }"
        )
        from ..ui.icons import lucide_icon
        from PyQt6.QtCore import QEvent, QObject, QSize, QTimer
        from PyQt6.QtCore import Qt as _Qt
        from PyQt6.QtWidgets import QToolButton

        class _SpinOverlay(QObject):
            def __init__(self, spinbox: QSpinBox):
                super().__init__(spinbox)
                self.sb = spinbox
                try:
                    ico_up = lucide_icon("chevron-up",   "#f0ece6", 16)
                    ico_dn = lucide_icon("chevron-down", "#f0ece6", 16)
                    up = QToolButton(spinbox)
                    dn = QToolButton(spinbox)
                    up.setIcon(ico_up);  dn.setIcon(ico_dn)
                    up.setIconSize(QSize(12, 12)); dn.setIconSize(QSize(12, 12))
                    up.setToolButtonStyle(_Qt.ToolButtonStyle.ToolButtonIconOnly)
                    dn.setToolButtonStyle(_Qt.ToolButtonStyle.ToolButtonIconOnly)
                    up.setStyleSheet("background: transparent; border: none;")
                    dn.setStyleSheet("background: transparent; border: none;")
                    up.setCursor(spinbox.cursor()); dn.setCursor(spinbox.cursor())
                    up.setFixedSize(22, 17); dn.setFixedSize(22, 17)
                    up.clicked.connect(spinbox.stepUp)
                    dn.clicked.connect(spinbox.stepDown)
                    spinbox._overlay_up_btn = up
                    spinbox._overlay_dn_btn = dn
                    spinbox.installEventFilter(self)
                    up.show(); dn.show()
                    up.raise_(); dn.raise_()
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
                    sb  = self.sb
                    up  = getattr(sb, "_overlay_up_btn", None)
                    dn  = getattr(sb, "_overlay_dn_btn", None)
                    if not up or not dn:
                        return
                    w = sb.width(); h = sb.height(); bw = 22
                    x = w - bw
                    up.move(x, max(0, (h // 4)     - (up.height() // 2)))
                    dn.move(x, max(0, (3 * h // 4) - (dn.height() // 2)))
                except Exception:
                    pass

        ov = _SpinOverlay(sb)
        sb._lucide_overlay = ov  # keep a ref so it isn't garbage-collected
        QTimer.singleShot(40,  lambda: ov._reposition())
        QTimer.singleShot(120, lambda: ov._reposition())
        return ov
    except Exception:
        return None


def _card() -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    return f


def _spinbox(min_val: int, max_val: int, default: int,
             suffix: str, tooltip: str) -> QSpinBox:
    """Create a QSpinBox with lucide chevron arrow overlays.

    Delegates to settings_sections._spinbox so the Upload-tab arrow
    treatment is used everywhere.  A full local implementation is kept
    as the fallback so the arrows still appear even if the import fails.
    """
    try:
        from .settings_sections import _spinbox as _shared_spinbox
        return _shared_spinbox(min_val, max_val, default, suffix, tooltip)
    except Exception:
        pass

    # ── Full local fallback (mirrors settings_sections._spinbox) ─────────────
    sb = QSpinBox()
    sb.setRange(min_val, max_val)
    sb.setValue(default)
    sb.setSuffix(suffix)
    sb.setToolTip(tooltip)
    sb.setMaximumWidth(200)
    try:
        sb.setFixedHeight(34)
    except Exception:
        pass

    # Hide the native up/down buttons and overlay lucide chevrons
    # (same approach as Upload tab).
    _install_lucide_spin_arrows(sb)

    return sb


def _add_spin_row(card_lay: QVBoxLayout, label: str, spinbox: QSpinBox):
    """Delegate to settings_sections._add_spin_row so the lucide arrow overlay
    is created for every spinbox, including the UI-tab R/G/B and font-size
    controls (which previously used this stripped-down local version and got
    no overlay on Windows)."""
    try:
        from .settings_sections import _add_spin_row as _shared_add_spin_row
        _shared_add_spin_row(card_lay, label, spinbox)
        return
    except Exception:
        pass
    # Fallback: plain row without overlay (should rarely be reached)
    row = QHBoxLayout()
    lbl = QLabel(label)
    lbl.setObjectName("field_label")
    try:
        from PyQt6.QtWidgets import QSizePolicy
        lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    except Exception:
        pass
    row.addWidget(lbl)
    row.addWidget(spinbox)
    row.addStretch()
    card_lay.addLayout(row)