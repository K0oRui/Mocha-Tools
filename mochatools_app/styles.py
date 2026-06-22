# styles.py — Mocha Tools stylesheet
# Colors are theme-tokenized so the app can switch between background
# themes (Mocha / White / Black) without touching this template.
# See theme.py:BACKGROUND_THEMES for the per-theme palette values and
# build_stylesheet() below for how __BG*__/__BORDER*__/__TEXT*__ tokens
# get substituted at runtime.
#   Accent     : #c8a96e (warm tan/gold) → #d4b87a (hover) → #a88950 (pressed)
#   Success    : #4ade80   Error: #f87171
#
# ARROW NOTE: Qt stylesheets do NOT support SVG data URIs — they silently
# render as grey rectangles.  All arrows use base64-encoded PNG data URIs,
# which Qt does support.

# ── Arrow PNGs (base64, 7×5 px) ──────────────────────────────────────────────
# Generated via Python's zlib/struct PNG encoder; colors:
#   muted = #9c9484   gold = #c8a96e
_UP_MUTED   = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAcAAAAFCAIAAAAG+GGPAAAAIElEQVR42mNgQAJzprT8Z8AGQBIwjFMCRQE2CawmoAMAlvgrMbXX9zwAAAAASUVORK5CYII="
_DOWN_MUTED = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAcAAAAFCAIAAAAG+GGPAAAAGklEQVR42mNgwAfmTGn5jwvjVIDTBJxWIPMB0CMrMYA7BBgAAAAASUVORK5CYII="
_UP_GOLD    = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAcAAAAFCAIAAAAG+GGPAAAAIElEQVR42mNgQAInVub9Z8AGQBIwjFMCRQE2CawmoAMAVZkt4eluqL8AAAAASUVORK5CYII="
_DOWN_GOLD  = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAcAAAAFCAIAAAAG+GGPAAAAGklEQVR42mNgwAdOrMz7jwvjVIDTBJxWIPMBokAt4QW1R0wAAAAASUVORK5CYII="

STYLESHEET = f"""
QMainWindow, QWidget#root {{
    background-color: __BG0__;
}}
QFrame#titlebar {{
    background-color: __BG7__;
    border-bottom: 1px solid __BORDER__;
    min-height: 42px;
    max-height: 42px;
}}
QLabel#title_app_name {{
    color: #c8a96e;
    font-size: __FONT_SIZE__px;
    font-weight: 700;
    letter-spacing: 0.5px;
    background: transparent;
}}
QLabel#title_version {{
    color: __BORDER2__;
    font-size: __FONT_SIZE__px;
    font-weight: 500;
    background: transparent;
}}
QPushButton#tb_close {{
    background: transparent;
    border: none;
    border-radius: 7px;
    min-width: 32px;
    max-width: 32px;
    min-height: 28px;
    max-height: 28px;
    font-size: __FONT_SIZE__px;
    color: __TEXT_DIM__;
    padding: 0px;
}}
QPushButton#tb_close:hover {{ background: #3d1515; color: #f87171; }}
QPushButton#tb_close:pressed {{ background: #2a0f0f; }}
QPushButton#tb_minmax {{
    background: transparent;
    border: none;
    border-radius: 7px;
    min-width: 32px;
    max-width: 32px;
    min-height: 28px;
    max-height: 28px;
    font-size: __FONT_SIZE__px;
    color: __TEXT_DIM__;
    padding: 0px;
}}
QPushButton#tb_minmax:hover {{ background: __BG5__; color: __TEXT_MUTED__; }}
QPushButton#tb_minmax:pressed {{ background: __BG6__; }}
QWidget {{
    background-color: transparent;
    color: __TEXT__;
    font-family: __FONT_FAMILY__;
    font-size: __FONT_SIZE__px;
}}
QFrame#card {{
    background-color: __BG1__;
    border: 1px solid __BORDER__;
    border-radius: 10px;
    padding: 12px;
}}
QLabel#section_header {{
    color: __TEXT_DIM__;
    font-size: __FONT_SIZE__px;
    font-weight: 700;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    padding: 8px 2px 8px 0px; /* add top/bottom padding so header sits clear of tab bar */
    margin: 0px 0px 8px 0px;
    background: transparent;
}}
QLabel#field_label {{
    color: __TEXT_MUTED__;
    font-size: __FONT_SIZE__px;
    min-width: 90px;
    background: transparent;
}}
QLabel#status_label {{
    color: __TEXT__;
    font-size: __FONT_SIZE__px;
    background: transparent;
}}
QLineEdit {{
    background-color: __BG3__;
    border: 1px solid __BORDER__;
    border-radius: 8px;
    padding: 0px 10px;
    color: __TEXT__;
    font-size: __FONT_SIZE__px;
    selection-background-color: #c8a96e;
    min-height: 34px;
    max-height: 34px;
}}
QLineEdit:focus {{
    border: 1px solid #c8a96e;
    background-color: __BG4__;
}}
QLineEdit::placeholder {{ color: __TEXT_DIM__; }}

/* ── QSpinBox ── PNG arrows, no SVG ───────────────────────────────────────── */
QSpinBox {{
    background-color: __BG3__;
    border: 1px solid __BORDER__;
    border-radius: 8px;
    padding: 6px 8px;
    color: __TEXT__;
    font-size: __FONT_SIZE__px;
}}
QSpinBox:focus {{ border-color: #c8a96e; }}
QSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 22px;
    background: __BG5__;
    border: none;
    border-left: 1px solid __BORDER__;
    border-bottom: 1px solid __BORDER__;
    border-top-right-radius: 7px;
}}
QSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 22px;
    background: __BG5__;
    border: none;
    border-left: 1px solid __BORDER__;
    border-bottom-right-radius: 7px;
}}
QSpinBox::up-button:hover   {{ background: __BORDER2__; }}
QSpinBox::down-button:hover {{ background: __BORDER2__; }}
QSpinBox::up-arrow   {{ width: 7px; height: 5px; image: url("{_UP_MUTED}"); }}
QSpinBox::up-arrow:hover   {{ image: url("{_UP_GOLD}"); }}
QSpinBox::down-arrow {{ width: 7px; height: 5px; image: url("{_DOWN_MUTED}"); }}
QSpinBox::down-arrow:hover {{ image: url("{_DOWN_GOLD}"); }}

/* ── QComboBox ── PNG arrow, no SVG ──────────────────────────────────────── */
QComboBox {{
    background-color: __BG3__;
    border: 1px solid __BORDER__;
    border-radius: 8px;
    padding: 6px 10px;
    color: __TEXT__;
    font-size: __FONT_SIZE__px;
}}
QComboBox:focus {{ border-color: #c8a96e; }}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 28px;
    border: none;
    border-left: 1px solid __BORDER__;
    border-top-right-radius: 7px;
    border-bottom-right-radius: 7px;
    background: __BG5__;
}}
QComboBox::drop-down:hover {{ background: __BORDER2__; }}
QComboBox::down-arrow      {{ width: 7px; height: 5px; image: url("{_DOWN_MUTED}"); }}
QComboBox::down-arrow:on   {{ image: url("{_UP_GOLD}"); }}
QComboBox QAbstractItemView {{
    background-color: __BG3__;
    border: 1px solid __BORDER2__;
    border-radius: 8px;
    selection-background-color: #5a4a28;
    selection-color: __TEXT__;
    outline: none;
}}
QPushButton#upload_btn {{
    background: #c8a96e;
    color: __ON_ACCENT_TEXT__;
    border: none;
    border-radius: 8px;
    padding: 10px 24px;
    font-size: __FONT_SIZE__px;
    font-weight: 700;
    letter-spacing: 0.3px;
}}
QPushButton#upload_btn:hover   {{ background: #d4b87a; }}
QPushButton#upload_btn:pressed {{ background: #a88950; }}
QPushButton#upload_btn:disabled {{ background: __BORDER__; color: __TEXT_DIM__; }}
QPushButton#browse_btn {{
    background-color: __BG3__;
    color: #c8a96e;
    border: 1px solid #c8a96e;
    border-radius: 8px;
    padding: 0px 14px;
    font-size: __FONT_SIZE__px;
    min-height: 34px;
    max-height: 34px;
}}
QPushButton#browse_btn:hover {{ background-color: __BG5__; border-color: __ACCENT_HOVER__; }}
QCheckBox {{
    color: __TEXT_MUTED__;
    font-size: __FONT_SIZE__px;
    spacing: 6px;
    background: transparent;
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid __BORDER2__;
    border-radius: 4px;
    background: __BG3__;
}}
QCheckBox::indicator:checked {{ background: #c8a96e; border-color: #c8a96e; image: none; }}
QCheckBox::indicator:hover   {{ border-color: #c8a96e; }}
QProgressBar {{
    background-color: __BG3__;
    border: 1px solid __BORDER__;
    border-radius: 5px;
    height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #a88950, stop:1 #d4b87a);
    border-radius: 5px;
}}
QLabel#log_console,
QPlainTextEdit#log_console {{
    background-color: __BG7__;
    border: 1px solid __BORDER__;
    border-radius: 8px;
    color: #c8a96e;
    font-family: __FONT_FAMILY__;
    font-size: __FONT_SIZE__px;
    padding: 8px 10px;
    min-height: 46px;
}}
QPlainTextEdit#log_console {{
    selection-background-color: #5a4a28;
}}
QLabel#status_badge {{
    background-color: __BG3__;
    border: 1px solid __BORDER__;
    border-radius: 10px;
    color: __TEXT_MUTED__;
    font-size: __FONT_SIZE__px;
    font-weight: 600;
    padding: 2px 10px;
}}
QFrame#drop_zone {{
    background-color: __BG7__;
    /* use the accent (with subtle alpha) so the outer dashed border follows the selected accent */
    border: 2px dashed __ACCENT__33;
    border-radius: 12px;
    min-height: 110px;
}}
QFrame#drop_zone[drag_active="true"] {{
    /* active state uses full accent color */
    border-color: __ACCENT__;
    background-color: __BG_ACCENT_TINT__;
}}
QFrame#drop_zone QFrame#drop_zone_inner {{
    /* inner dashed rectangle (smaller) — match accent with subtle alpha */
    border: 1px dashed __ACCENT__33;
    border-radius: 10px;
    padding: 8px 16px;
    background: transparent;
}}
QFrame#drop_zone[drag_active="true"] QFrame#drop_zone_inner {{
    border-color: __ACCENT__;
    background: transparent;
}}
QLabel#drop_label      {{ color: __TEXT_DIM__; font-size: __FONT_SIZE__px; background: transparent; }}
QLabel#drop_label_bold {{ color: __ACCENT__; font-size: __FONT_SIZE__px; font-weight: 700; background: transparent; }}
QLabel#drop_file_label {{ color: __ACCENT__; font-size: __FONT_SIZE__px; font-weight: 600; background: transparent; }}
QFrame#divider {{ background-color: __BORDER__; max-height: 1px; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 6px; }}
QScrollBar::handle:vertical {{ background: __BORDER2__; border-radius: 3px; min-height: 20px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
/* Horizontal scrollbar: slim, rounded, modern style */
/* Horizontal scrollbar: slim, rounded, modern style */
QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
    margin: 0px 6px 0px 6px; /* give space for rounded handle */
}}
QScrollBar::handle:horizontal {{
    background: __BORDER2_RGBA75__;
    border-radius: 4px;
    min-width: 24px;
    border: 1px solid rgba(0,0,0,0.15);
}}
QScrollBar::handle:horizontal:hover {{ background: __BORDER2_RGBA95__; }}
QScrollBar::handle:horizontal:pressed {{ background: __BORDER2__; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

/* Make scrollbars overlay-ish and subtle on desktop: rounded translucent handle */
QScrollBar {{ background: transparent; }}
QTabWidget::pane {{ border: none; background: transparent; }}
QTabWidget::tab-bar {{ left: 0px; }}
QTabBar {{
    background: __BG1__;
    border-bottom: 1px solid __BORDER__;
}}
QTabBar::tab {{
    background: transparent;
    color: __TEXT_DIM__;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 8px 16px 6px 16px;
    font-size: __FONT_SIZE__px;
    font-weight: 600;
    letter-spacing: 0.2px;
    margin-right: 0px;
    min-width: 64px;
}}
QTabBar::tab:selected {{
    background: transparent;
    color: #c8a96e;
    border-bottom: 2px solid #c8a96e;
}}
QTabBar::tab:hover:!selected {{
    background: transparent;
    color: __TEXT_MUTED__;
    border-bottom: 2px solid __BORDER2__;
}}
QTabBar::scroller {{ width: 0px; }}
QTabWidget > QTabBar {{ background: __BG1__; }}
QTabWidget > QWidget {{ background: __BG1__; }}
QTreeWidget {{
    background: __BG7__;
    border: 1px solid __BORDER__;
    border-radius: 8px;
    color: __TEXT__;
    font-size: __FONT_SIZE__px;
    outline: none;
    show-decoration-selected: 1;
}}
QTreeWidget::item {{ padding: 5px 4px; border-bottom: 1px solid __BG6__; }}
QTreeWidget::item:selected {{ background: __ACCENT_PRESSED__; color: __TEXT__; }}
QTreeWidget::item:hover:!selected {{ background: __BG3__; }}
QHeaderView::section {{
    background: __BG3__;
    color: __TEXT_DIM__;
    border: none;
    border-right: 1px solid __BORDER__;
    border-bottom: 1px solid __BORDER__;
    padding: 5px 8px;
    font-size: __FONT_SIZE__px;
    font-weight: 700;
    letter-spacing: 0.5px;
}}
QPushButton#tb_btn {{
    background: __BG3__;
    color: #c8a96e;
    border: 1px solid __BORDER__;
    border-radius: 7px;
    padding: 5px 12px;
    font-size: __FONT_SIZE__px;
    font-weight: 600;
    min-height: 28px;
}}
QPushButton#tb_btn:hover    {{ background: __BG5__; border-color: #6a5535; }}
QPushButton#tb_btn:pressed  {{ background: __BG7__; }}
QPushButton#tb_btn:disabled {{ color: __TEXT_DIM__; border-color: __BG3__; }}
QPushButton#tb_btn_danger {{
    background: __BG3__;
    color: #f87171;
    border: 1px solid __BORDER__;
    border-radius: 7px;
    padding: 5px 12px;
    font-size: __FONT_SIZE__px;
    font-weight: 600;
    min-height: 28px;
}}
QPushButton#tb_btn_danger:hover    {{ background: #251a1a; border-color: #8a3535; }}
QPushButton#tb_btn_danger:disabled {{ color: __TEXT_DIM__; border-color: __BG3__; }}
QMenu {{
    background: __BG3__;
    border: 1px solid __BORDER2__;
    border-radius: 8px;
    color: __TEXT__;
    font-size: __FONT_SIZE__px;
}}
QMenu::item {{ padding: 6px 8px; border-radius: 4px; }}
QMenu::item:selected {{ background: __ACCENT_PRESSED__; }}
QMenu::separator {{ height: 1px; background: __BORDER__; margin: 4px 8px; }}
QMenu::icon {{ padding: 0 6px 0 4px; width: 12px; height: 12px; }}
QDialog {{ background-color: __BG1__; }}
QDialogButtonBox QPushButton {{
    min-width: 72px;
    min-height: 30px;
    border-radius: 7px;
    font-size: __FONT_SIZE__px;
    font-weight: 600;
    padding: 4px 16px;
    background: __BG3__;
    color: __TEXT__;
    border: 1px solid __BORDER2__;
}}
QDialogButtonBox QPushButton:hover  {{ background: __BG5__; }}
QDialogButtonBox QPushButton#upload_btn {{
    background: #c8a96e;
    color: __ON_ACCENT_TEXT__;
    border: none;
}}
QDialogButtonBox QPushButton#upload_btn:hover {{ background: #d4b87a; }}
QListWidget {{
    background: __BG7__;
    border: 1px solid __BORDER__;
    border-radius: 8px;
    color: __TEXT__;
    font-size: __FONT_SIZE__px;
}}
QListWidget::item {{ padding: 6px 10px; }}
QListWidget::item:selected {{ background: __ACCENT_PRESSED__; color: __TEXT__; }}
QListWidget::item:hover {{ background: __BG3__; }}
QMessageBox {{ background-color: __BG1__; }}
QMessageBox QLabel {{ color: __TEXT__; background: transparent; }}
QPushButton {{
    background: __BG3__;
    color: __TEXT__;
    border: 1px solid __BORDER2__;
    border-radius: 7px;
    padding: 6px 16px;
    min-height: 32px;
}}
QPushButton:hover  {{ background: __BG5__; }}
QPushButton:pressed {{ background: __BG7__; }}
"""


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def _mix(hex1: str, hex2: str, t: float) -> str:
    r1, g1, b1 = _hex_to_rgb(hex1)
    r2, g2, b2 = _hex_to_rgb(hex2)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return _rgb_to_hex(r, g, b)


def _apply_background_tokens(s: str, background_key: str | None, accent_hex: str | None) -> str:
    """Substitute __BG*__/__BORDER*__/__TEXT*__ tokens with the values from
    the selected background theme palette (theme.py:BACKGROUND_THEMES).

    Also resolves __ON_ACCENT_TEXT__ (always-dark text sitting on the gold/
    accent-colored buttons, independent of the background theme) and
    __BG_ACCENT_TINT__ (the drop-zone "drag active" background, computed by
    blending the accent into the theme's card background).
    """
    try:
        from .theme import get_background_palette
        pal = get_background_palette(background_key)
    except Exception:
        pal = {
            "bg0": "#111010", "bg1": "#181614", "bg2": "#181614", "bg3": "#1e1c19",
            "bg4": "#222018", "bg5": "#252320", "bg6": "#3d3a35", "bg7": "#141210",
            "border": "#2e2b27", "border2": "#3d3a35",
            "text": "#f0ece6", "text_muted": "#9c9484", "text_dim": "#5a5650",
        }

    s = s.replace('__BG0__', pal["bg0"])
    s = s.replace('__BG1__', pal["bg1"])
    s = s.replace('__BG2__', pal["bg2"])
    s = s.replace('__BG3__', pal["bg3"])
    s = s.replace('__BG4__', pal["bg4"])
    s = s.replace('__BG5__', pal["bg5"])
    s = s.replace('__BG6__', pal["bg6"])
    s = s.replace('__BG7__', pal["bg7"])
    s = s.replace('__BORDER__', pal["border"])
    s = s.replace('__BORDER2__', pal["border2"])
    s = s.replace('__TEXT__', pal["text"])
    s = s.replace('__TEXT_MUTED__', pal["text_muted"])
    s = s.replace('__TEXT_DIM__', pal["text_dim"])

    # Text sitting on top of the solid gold/accent buttons should stay dark
    # regardless of theme, for contrast against the warm accent color.
    s = s.replace('__ON_ACCENT_TEXT__', "#111010")

    # Drag-active drop-zone background: blend a touch of the accent into
    # the theme's elevated background so it reads as "lit up" in every theme.
    try:
        from .theme import DEFAULT_ACCENT
        a = accent_hex or DEFAULT_ACCENT
        tint = _mix(pal["bg7"], a, 0.12)
        s = s.replace('__BG_ACCENT_TINT__', tint)
    except Exception:
        s = s.replace('__BG_ACCENT_TINT__', pal["bg7"])

    # Horizontal scrollbar handle rgba colors, derived from border2.
    try:
        r, g, b = _hex_to_rgb(pal["border2"])
        s = s.replace('__BORDER2_RGBA75__', f"rgba({r}, {g}, {b}, 0.75)")
        s = s.replace('__BORDER2_RGBA95__', f"rgba({r}, {g}, {b}, 0.95)")
    except Exception:
        s = s.replace('__BORDER2_RGBA75__', "rgba(61,58,53,0.75)")
        s = s.replace('__BORDER2_RGBA95__', "rgba(61,58,53,0.95)")

    return s


def build_stylesheet(accent_hex: str | None, background_key: str | None = None) -> str:
    """Return a stylesheet string with accent and background colors substituted.

    This function takes the existing STYLESHEET template and replaces the
    canonical accent (#c8a96e) and its hover/pressed variants with computed
    values derived from accent_hex, and replaces the __BG*__/__BORDER*__/
    __TEXT*__ background-theme tokens with the palette for background_key
    (or the current/persisted theme if not given).
    """
    # start with template
    s = STYLESHEET
    # Always substitute font tokens from saved settings/runtime cache so
    # QSS-driven fonts track the user's selected family/size even when
    # only the font was changed (and accent_hex may be None).
    try:
        from .theme import get_font
        fam, fsz = get_font()
        # ensure family is quoted for QSS and provide a sensible fallback
        fam_q = f'"{fam}"'
        s = s.replace('__FONT_FAMILY__', fam_q)
        s = s.replace('__FONT_SIZE__', str(int(fsz)))
    except Exception:
        # fallback to defaults
        try:
            from .theme import DEFAULT_FONT_FAMILY, DEFAULT_FONT_SIZE
            s = s.replace('__FONT_FAMILY__', f'"{DEFAULT_FONT_FAMILY}"')
            s = s.replace('__FONT_SIZE__', str(int(DEFAULT_FONT_SIZE)))
        except Exception:
            pass

    # Background theme tokens always get substituted, accent or not.
    s = _apply_background_tokens(s, background_key, accent_hex)

    if not accent_hex:
        return s
    # compute hover (lighter) and pressed (darker) variants
    hover = _mix(accent_hex, "#ffffff", 0.12)
    pressed = _mix(accent_hex, "#000000", 0.22)
    from .theme import DEFAULT_ACCENT
    # compute semi-transparent variant and replace its placeholder first
    # NOTE: Qt's QSS parser does not support 8-digit #rrggbbaa hex colors,
    # so we must use rgba(r, g, b, a) instead or the border silently falls
    # back to its default and never tracks the accent.
    try:
        r, g, b = _hex_to_rgb(accent_hex)
        hover_alpha = f"rgba({r}, {g}, {b}, 51)"  # 0x33 == 51 (~20% opacity)
        s = s.replace('__ACCENT__33', hover_alpha)
    except Exception:
        pass
    # replace canonical/default accent token
    s = s.replace(DEFAULT_ACCENT, accent_hex)
    # also support legacy/plain placeholders used in some templates
    s = s.replace('__ACCENT__', accent_hex)
    # replace hover/pressed canonical placeholders and their token equivalents
    s = s.replace('#d4b87a', hover)
    s = s.replace('__ACCENT_HOVER__', hover)
    s = s.replace('#a88950', pressed)
    s = s.replace('__ACCENT_PRESSED__', pressed)
    return s


def compute_accent_variants(accent_hex: str | None) -> tuple[str, str, str]:
    """Return (accent, hover, pressed) hex strings for a given accent.

    Useful for sweeping inline styleSheet replacements.
    """
    if not accent_hex:
        return ("#c8a96e", "#d4b87a", "#a88950")
    hover = _mix(accent_hex, "#ffffff", 0.12)
    pressed = _mix(accent_hex, "#000000", 0.22)
    return (accent_hex, hover, pressed)