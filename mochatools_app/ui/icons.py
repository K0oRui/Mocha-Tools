"""
ui/icons.py — Lucide SVG icon helper for MochaTools.

Minimal subset of Lucide icon paths used throughout the app.
Each value is the SVG <path d="..."> content for a 24x24 viewBox icon.
"""

from PyQt6.QtCore import QByteArray, QSize, Qt
from PyQt6.QtGui import QIcon, QPixmap, QPainter
from PyQt6.QtSvg import QSvgRenderer

_LUCIDE_PATHS: dict[str, str] = {
    "upload":         'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12',
    "download-cloud": 'M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242M12 12v9M8 17l4 4 4-4',
    "folder":         'M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z',
    "share-2":        'M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14 21 3',
    "settings":       'M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
    "x":              'M18 6 6 18M6 6l12 12',
    "minus":          'M5 12h14',
    "square":         'M3 3h18v18H3z',
    "copy":           'M20 9h-9a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2v-9a2 2 0 0 0-2-2z M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1',
    "refresh-cw":     'M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8 M21 3v5h-5 M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16 M8 16H3v5',
    "trash-2":        'M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M10 11v6M14 11v6',
    "move":           'M5 9l-3 3 3 3M9 5l3-3 3 3M15 19l-3 3-3-3M19 9l3 3-3 3M2 12h20M12 2v20',
    "link":           'M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71 M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71',
    "coffee":         'M17 8h1a4 4 0 1 1 0 8h-1M3 8h14v9a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4V8zM6 1v3M10 1v3M14 1v3',
    "pencil":         'M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z M15 5l4 4',
    "pause":          'M6 4h4v16H6zM14 4h4v16h-4z',
    "play":           'M5 3v18l15-9z',
    "chevron-up":     'M6 15l6-6 6 6',
    "chevron-down":   'M6 9l6 6 6-6',
    "eye":            'M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
    "volume-2":       'M11 5 6 9H2v6h4l5 4z M19.07 4.93a10 10 0 0 1 0 14.14 M15.54 8.46a5 5 0 0 1 0 7.07',
    "volume-x":       'M11 5 6 9H2v6h4l5 4z M22 9l-6 6 M16 9l6 6',
    "music":          'M9 18V5l12-2v13 M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0z M21 16a3 3 0 1 1-6 0 3 3 0 0 1 6 0z',
    "eye":            'M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
}


def lucide_icon(name: str, color: str | None = None, size: int = 16) -> QIcon:
    """Return a QIcon rendered from a Lucide SVG path string.

    This helper always creates and returns a fresh QIcon (no caching) so
    callers can request a new pixmap when needed. To support legacy code
    that used a special literal for the theme accent, if color is None
    or equals the legacy accent literal the current accent color is
    resolved from theme.get_accent().

    Note: callers that need icons to automatically update when the
    application's accent changes should recreate the icon on demand (for
    example by connecting to the theme notifier). A module-level cache is
    intentionally not used here; if one is introduced in future, a
    connection to the theme notifier should be used to clear it on
    updates.
    """
    # Resolve dynamic accent if caller passed None or the original default
    try:
        if color is None:
            from ..theme import get_accent
            color = get_accent()
        else:
            from ..theme import DEFAULT_ACCENT, get_accent
            if color == DEFAULT_ACCENT:
                color = get_accent()
    except Exception:
        # fall back to provided color or the module default
        if not color:
            from ..theme import DEFAULT_ACCENT
            color = DEFAULT_ACCENT

    path_d = _LUCIDE_PATHS.get(name, "")

    svg_paths = ""
    segments = path_d.split(" M ")
    for i, seg in enumerate(segments):
        seg = seg.strip()
        if not seg:
            continue
        d = seg if i == 0 else "M " + seg
        svg_paths += (
            f'<path d="{d}" stroke="{color}" stroke-width="1.75" '
            f'stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
        )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'width="{size}" height="{size}">{svg_paths}</svg>'
    ).encode()

    renderer = QSvgRenderer(QByteArray(svg))
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    renderer.render(painter)
    painter.end()
    return QIcon(pm)