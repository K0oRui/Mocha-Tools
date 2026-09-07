"""ui.py — the Mocha Tools installer UI (matches the app's dark mocha theme)."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer

ACCENT = "#c8a96e"
ACCENT_HOVER = "#d4b87a"
ACCENT_PRESSED = "#a88950"
ON_ACCENT = "#111010"
BG0 = "#0d0c0b"
BG1 = "#1c1a18"
BG2 = "#211e1b"
BG3 = "#2a2724"
BG4 = "#332f2a"
BG5 = "#2e2a26"
BG7 = "#131110"
BORDER = "#34302b"
BORDER2 = "#4a453e"
TEXT = "#f4f1eb"
TEXT_MUTED = "#aca496"
TEXT_DIM = "#6f6a60"

# App brand icon (coffee cup), Phosphor-style 256x256 — same SVG the app uses.
_APP_ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" '
    'fill="#c8a96e">'
    '<path d="M208,88v48a88,88,0,0,1-51.3,80H83.3A88,88,0,0,1,32,136V88Z" '
    'opacity="0.2"></path>'
    '<path d="M80,56V24a8,8,0,0,1,16,0V56a8,8,0,0,1-16,0Zm40,8a8,8,0,0,0,'
    "8-8V24a8,8,0,0,0-16,0V56A8,8,0,0,0,120,64Zm32,0a8,8,0,0,0,8-8V24a8,8,"
    "0,0,0-16,0V56A8,8,0,0,0,152,64Zm96,56v8a40,40,0,0,1-37.51,39.91,96.59,"
    "96.59,0,0,1-27,40.09H208a8,8,0,0,1,0,16H32a8,8,0,0,1,0-16H56.54A96.3,"
    "96.3,0,0,1,24,136V88a8,8,0,0,1,8-8H208A40,40,0,0,1,248,120ZM200,96H40"
    "v40a80.27,80.27,0,0,0,45.12,72h69.76A80.27,80.27,0,0,0,200,136Zm32,24a"
    '24,24,0,0,0-16-22.62V136a95.78,95.78,0,0,1-1.2,15A24,24,0,0,0,232,128Z">'
    "</path></svg>"
)

_LUCIDE_PATHS: dict[str, str] = {
    "minus": "M5 12h14",
    "x": "M18 6 6 18M6 6l12 12",
    "check": "M20 6 9 17l-5-5",
}

STYLESHEET = f"""
QMainWindow, QWidget#root {{
    background: transparent;
}}
QWidget {{
    background-color: transparent;
    color: {TEXT};
    font-family: "Segoe UI";
    font-size: 13px;
}}
QFrame#titlebar {{
    background: transparent;
    border-bottom: 1px solid #181512;
    min-height: 34px;
    max-height: 34px;
}}
QLabel#title_app_name {{
    color: {ACCENT};
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.4px;
    background: transparent;
}}
QLabel#title_version {{
    color: {TEXT_DIM};
    font-size: 13px;
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
    color: {TEXT_DIM};
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
    color: {TEXT_DIM};
    padding: 0px;
}}
QPushButton#tb_minmax:hover {{ background: {BG5}; color: {TEXT_MUTED}; }}
QPushButton#tb_minmax:pressed {{ background: {BG7}; }}
QLabel#big_title {{
    color: {ACCENT};
    font-size: 20px;
    font-weight: 700;
    letter-spacing: 0.4px;
    background: transparent;
}}
QLabel#field_label {{
    color: {TEXT_MUTED};
    font-size: 13px;
    font-weight: 500;
    background: transparent;
}}
QLabel#muted {{
    color: {TEXT_MUTED};
    background: transparent;
}}
QLabel#dim {{
    color: {TEXT_DIM};
    background: transparent;
}}
QLineEdit {{
    background-color: {BG3};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 0px 12px;
    color: {TEXT};
    font-size: 12px;
    selection-background-color: {ACCENT};
    selection-color: {ON_ACCENT};
    min-height: 34px;
    max-height: 34px;
}}
QLineEdit:hover {{ border-color: {BORDER2}; }}
QLineEdit:focus {{
    border: 1px solid {ACCENT};
    background-color: {BG4};
}}
QLineEdit::placeholder {{ color: {TEXT_DIM}; }}
QPushButton {{
    background: {BG3};
    color: {TEXT};
    border: 1px solid {BORDER2};
    border-radius: 8px;
    padding: 0px 14px;
    min-height: 28px;
    max-height: 28px;
    font-size: 12px;
    font-weight: 600;
}}
QPushButton:hover {{ background: {BG5}; border-color: {BORDER2}; }}
QPushButton:pressed {{ background: {BG7}; }}
QPushButton:disabled {{
    color: {TEXT_DIM};
    border-color: {BG3};
    background: {BG1};
}}
QPushButton#primary {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {ACCENT_HOVER}, stop:1 {ACCENT});
    color: {ON_ACCENT};
    border: none;
    border-radius: 8px;
    padding: 0px 22px;
    min-height: 30px;
    max-height: 30px;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.3px;
}}
QPushButton#primary:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #dec38a, stop:1 {ACCENT_HOVER});
}}
QPushButton#primary:pressed {{ background: {ACCENT_PRESSED}; }}
QPushButton#primary:disabled {{
    background: {BG5};
    color: {TEXT_DIM};
}}
QPushButton#browse {{
    background: {BG3};
    color: {ACCENT};
    border: 1px solid {BORDER2};
    border-radius: 8px;
    padding: 0px 14px;
    font-weight: 600;
    min-height: 34px;
    max-height: 34px;
}}
QPushButton#browse:hover {{ background: {BG5}; border-color: {ACCENT}; color: {ACCENT_HOVER}; }}
QCheckBox {{
    color: {TEXT_MUTED};
    font-size: 13px;
    spacing: 8px;
    background: transparent;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {BORDER2};
    border-radius: 5px;
    background: {BG3};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QProgressBar {{
    background-color: {BG3};
    border: none;
    border-radius: 5px;
    height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT_PRESSED}, stop:1 {ACCENT_HOVER});
    border-radius: 5px;
}}
QPlainTextEdit {{
    background-color: {BG7};
    border: 1px solid {BORDER};
    border-radius: 10px;
    color: {TEXT};
    font-size: 13px;
    padding: 11px 13px;
    selection-background-color: #5a4a28;
}}
QScrollBar:vertical {{ background: transparent; width: 6px; }}
QScrollBar::handle:vertical {{ background: {BORDER2}; border-radius: 3px; min-height: 20px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


def build_palette() -> QPalette:
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(BG0))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Base, QColor(BG7))
    pal.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Button, QColor(BG3))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(ON_ACCENT))
    return pal


def _render_svg(svg: str, size: int) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    renderer.render(painter)
    painter.end()
    return pm


def app_icon(size: int = 256) -> QIcon:
    """Return the MochaTools brand icon (coffee cup), same as the app."""
    icon = QIcon()
    for s in sorted({16, 24, 32, 48, 64, 128, 256, size}):
        icon.addPixmap(_render_svg(_APP_ICON_SVG, s))
    return icon


def lucide_icon(name: str, color: str, size: int = 16) -> QIcon:
    """Return a QIcon rendered from a Lucide SVG path string."""
    path_d = _LUCIDE_PATHS.get(name, "")
    svg_paths = ""
    segments = path_d.split(" M ")
    for i, seg in enumerate(segments):
        seg_stripped = seg.strip()
        if not seg_stripped:
            continue
        d = seg_stripped if i == 0 else "M " + seg_stripped
        svg_paths += (
            f'<path d="{d}" stroke="{color}" stroke-width="1.75" '
            f'stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
        )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'width="{size}" height="{size}">{svg_paths}</svg>'
    )
    return QIcon(_render_svg(svg, size))


def make_check_icon(size: int = 64) -> QPixmap:
    return _render_svg(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'width="{size}" height="{size}">'
        f'<path d="M20 6 9 17l-5-5" stroke="{ACCENT}" stroke-width="2.5" '
        f'stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>',
        size,
    )
