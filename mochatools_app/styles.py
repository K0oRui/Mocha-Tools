# styles.py — Mocha Tools stylesheet
# Colors matched to mocha.my web UI:
#   Background : #111010 → #181614 → #1e1c19 → #252320
#   Borders    : #2e2b27 (default), #3d3a35 (elevated)
#   Accent     : #c8a96e (warm tan/gold) → #d4b87a (hover) → #a88950 (pressed)
#   Text       : #f0ece6 (primary), #9c9484 (muted), #5a5650 (placeholder)
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
    background-color: #111010;
}}
QFrame#titlebar {{
    background-color: #141210;
    border-bottom: 1px solid #2e2b27;
    min-height: 42px;
    max-height: 42px;
}}
QLabel#title_app_name {{
    color: #c8a96e;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.5px;
    background: transparent;
}}
QLabel#title_version {{
    color: #3d3a35;
    font-size: 11px;
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
    font-size: 14px;
    color: #5a5650;
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
    font-size: 13px;
    color: #5a5650;
    padding: 0px;
}}
QPushButton#tb_minmax:hover {{ background: #252320; color: #9c9484; }}
QPushButton#tb_minmax:pressed {{ background: #1a1816; }}
QWidget {{
    background-color: transparent;
    color: #f0ece6;
    font-family: "Segoe UI", "SF Pro Display", "Inter", "Helvetica Neue", sans-serif;
    font-size: 13px;
}}
QFrame#card {{
    background-color: #181614;
    border: 1px solid #2e2b27;
    border-radius: 10px;
    padding: 4px;
}}
QLabel#section_header {{
    color: #5a5650;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    padding: 0px;
    background: transparent;
}}
QLabel#field_label {{
    color: #9c9484;
    font-size: 12px;
    min-width: 90px;
    background: transparent;
}}
QLabel#status_label {{
    color: #f0ece6;
    font-size: 12px;
    background: transparent;
}}
QLineEdit {{
    background-color: #1e1c19;
    border: 1px solid #2e2b27;
    border-radius: 8px;
    padding: 0px 10px;
    color: #f0ece6;
    font-size: 13px;
    selection-background-color: #c8a96e;
    min-height: 34px;
    max-height: 34px;
}}
QLineEdit:focus {{
    border: 1px solid #c8a96e;
    background-color: #222018;
}}
QLineEdit::placeholder {{ color: #5a5650; }}

/* ── QSpinBox ── PNG arrows, no SVG ───────────────────────────────────────── */
QSpinBox {{
    background-color: #1e1c19;
    border: 1px solid #2e2b27;
    border-radius: 8px;
    padding: 6px 8px;
    color: #f0ece6;
    font-size: 13px;
}}
QSpinBox:focus {{ border-color: #c8a96e; }}
QSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 22px;
    background: #252320;
    border: none;
    border-left: 1px solid #2e2b27;
    border-bottom: 1px solid #2e2b27;
    border-top-right-radius: 7px;
}}
QSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 22px;
    background: #252320;
    border: none;
    border-left: 1px solid #2e2b27;
    border-bottom-right-radius: 7px;
}}
QSpinBox::up-button:hover   {{ background: #3d3a35; }}
QSpinBox::down-button:hover {{ background: #3d3a35; }}
QSpinBox::up-arrow   {{ width: 7px; height: 5px; image: url("{_UP_MUTED}"); }}
QSpinBox::up-arrow:hover   {{ image: url("{_UP_GOLD}"); }}
QSpinBox::down-arrow {{ width: 7px; height: 5px; image: url("{_DOWN_MUTED}"); }}
QSpinBox::down-arrow:hover {{ image: url("{_DOWN_GOLD}"); }}

/* ── QComboBox ── PNG arrow, no SVG ──────────────────────────────────────── */
QComboBox {{
    background-color: #1e1c19;
    border: 1px solid #2e2b27;
    border-radius: 8px;
    padding: 6px 10px;
    color: #f0ece6;
    font-size: 13px;
}}
QComboBox:focus {{ border-color: #c8a96e; }}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 28px;
    border: none;
    border-left: 1px solid #2e2b27;
    border-top-right-radius: 7px;
    border-bottom-right-radius: 7px;
    background: #252320;
}}
QComboBox::drop-down:hover {{ background: #3d3a35; }}
QComboBox::down-arrow      {{ width: 7px; height: 5px; image: url("{_DOWN_MUTED}"); }}
QComboBox::down-arrow:on   {{ image: url("{_UP_GOLD}"); }}
QComboBox QAbstractItemView {{
    background-color: #1e1c19;
    border: 1px solid #3d3a35;
    border-radius: 8px;
    selection-background-color: #5a4a28;
    selection-color: #f0ece6;
    outline: none;
}}
QPushButton#upload_btn {{
    background: #c8a96e;
    color: #111010;
    border: none;
    border-radius: 8px;
    padding: 10px 24px;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.3px;
}}
QPushButton#upload_btn:hover   {{ background: #d4b87a; }}
QPushButton#upload_btn:pressed {{ background: #a88950; }}
QPushButton#upload_btn:disabled {{ background: #2e2b27; color: #5a5650; }}
QPushButton#browse_btn {{
    background-color: #1e1c19;
    color: #c8a96e;
    border: 1px solid #4a3f2a;
    border-radius: 8px;
    padding: 0px 14px;
    font-size: 12px;
    min-height: 34px;
    max-height: 34px;
}}
QPushButton#browse_btn:hover {{ background-color: #252320; border-color: #7a6035; }}
QCheckBox {{
    color: #9c9484;
    font-size: 12px;
    spacing: 6px;
    background: transparent;
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid #3d3a35;
    border-radius: 4px;
    background: #1e1c19;
}}
QCheckBox::indicator:checked {{ background: #c8a96e; border-color: #c8a96e; image: none; }}
QCheckBox::indicator:hover   {{ border-color: #c8a96e; }}
QProgressBar {{
    background-color: #1e1c19;
    border: 1px solid #2e2b27;
    border-radius: 5px;
    height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #a88950, stop:1 #d4b87a);
    border-radius: 5px;
}}
QLabel#log_console {{
    background-color: #141210;
    border: 1px solid #2e2b27;
    border-radius: 8px;
    color: #c8a96e;
    font-family: "Consolas", "Fira Code", "Courier New", monospace;
    font-size: 11px;
    padding: 8px 10px;
    min-height: 46px;
}}
QLabel#status_badge {{
    background-color: #1e1c19;
    border: 1px solid #2e2b27;
    border-radius: 10px;
    color: #9c9484;
    font-size: 11px;
    font-weight: 600;
    padding: 2px 10px;
}}
QFrame#drop_zone {{
    background-color: #141210;
    border: 2px dashed #2e2b27;
    border-radius: 12px;
    min-height: 110px;
}}
QFrame#drop_zone[drag_active="true"] {{
    border-color: #c8a96e;
    background-color: #1a1710;
}}
QLabel#drop_label      {{ color: #5a5650; font-size: 13px; background: transparent; }}
QLabel#drop_label_bold {{ color: #c8a96e; font-size: 13px; font-weight: 700; background: transparent; }}
QFrame#divider {{ background-color: #2e2b27; max-height: 1px; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 6px; }}
QScrollBar::handle:vertical {{ background: #3d3a35; border-radius: 3px; min-height: 20px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QTabWidget::pane {{ border: none; background: transparent; }}
QTabWidget::tab-bar {{ left: 0px; }}
QTabBar {{
    background: #181614;
    border-bottom: 1px solid #2e2b27;
}}
QTabBar::tab {{
    background: transparent;
    color: #5a5650;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 11px 22px 10px 22px;
    font-size: 12px;
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
    color: #9c9484;
    border-bottom: 2px solid #3d3a35;
}}
QTabBar::scroller {{ width: 0px; }}
QTabWidget > QTabBar {{ background: #181614; }}
QTabWidget > QWidget {{ background: #181614; }}
QTreeWidget {{
    background: #141210;
    border: 1px solid #2e2b27;
    border-radius: 8px;
    color: #f0ece6;
    font-size: 12px;
    outline: none;
    show-decoration-selected: 1;
}}
QTreeWidget::item {{ padding: 5px 4px; border-bottom: 1px solid #1a1816; }}
QTreeWidget::item:selected {{ background: #332b1a; color: #f0ece6; }}
QTreeWidget::item:hover:!selected {{ background: #1e1c19; }}
QHeaderView::section {{
    background: #1e1c19;
    color: #5a5650;
    border: none;
    border-right: 1px solid #2e2b27;
    border-bottom: 1px solid #2e2b27;
    padding: 5px 8px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.5px;
}}
QPushButton#tb_btn {{
    background: #1e1c19;
    color: #c8a96e;
    border: 1px solid #2e2b27;
    border-radius: 7px;
    padding: 5px 12px;
    font-size: 11px;
    font-weight: 600;
    min-height: 28px;
}}
QPushButton#tb_btn:hover    {{ background: #252320; border-color: #6a5535; }}
QPushButton#tb_btn:pressed  {{ background: #141210; }}
QPushButton#tb_btn:disabled {{ color: #5a5650; border-color: #1e1c19; }}
QPushButton#tb_btn_danger {{
    background: #1e1c19;
    color: #f87171;
    border: 1px solid #2e2b27;
    border-radius: 7px;
    padding: 5px 12px;
    font-size: 11px;
    font-weight: 600;
    min-height: 28px;
}}
QPushButton#tb_btn_danger:hover    {{ background: #251a1a; border-color: #8a3535; }}
QPushButton#tb_btn_danger:disabled {{ color: #5a5650; border-color: #1e1c19; }}
QMenu {{
    background: #1e1c19;
    border: 1px solid #3d3a35;
    border-radius: 8px;
    color: #f0ece6;
    font-size: 12px;
}}
QMenu::item {{ padding: 6px 24px; border-radius: 4px; }}
QMenu::item:selected {{ background: #332b1a; }}
QMenu::separator {{ height: 1px; background: #2e2b27; margin: 4px 8px; }}
QDialog {{ background-color: #181614; }}
QDialogButtonBox QPushButton {{
    min-width: 72px;
    min-height: 30px;
    border-radius: 7px;
    font-size: 12px;
    font-weight: 600;
    padding: 4px 16px;
    background: #1e1c19;
    color: #f0ece6;
    border: 1px solid #3d3a35;
}}
QDialogButtonBox QPushButton:hover  {{ background: #252320; }}
QDialogButtonBox QPushButton#upload_btn {{
    background: #c8a96e;
    color: #111010;
    border: none;
}}
QDialogButtonBox QPushButton#upload_btn:hover {{ background: #d4b87a; }}
QListWidget {{
    background: #141210;
    border: 1px solid #2e2b27;
    border-radius: 8px;
    color: #f0ece6;
    font-size: 13px;
}}
QListWidget::item {{ padding: 6px 10px; }}
QListWidget::item:selected {{ background: #332b1a; color: #f0ece6; }}
QListWidget::item:hover {{ background: #1e1c19; }}
QMessageBox {{ background-color: #181614; }}
QMessageBox QLabel {{ color: #f0ece6; background: transparent; }}
QPushButton {{
    background: #1e1c19;
    color: #f0ece6;
    border: 1px solid #3d3a35;
    border-radius: 7px;
    padding: 6px 16px;
    min-height: 32px;
}}
QPushButton:hover  {{ background: #252320; }}
QPushButton:pressed {{ background: #141210; }}
"""