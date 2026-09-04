"""
window_chrome.py — Frameless-window resize, rounding, and cursor logic.

Extracted from the former MochaTools God-Object in app.py.

Public API (free functions that receive a QMainWindow subclass instance):
  event_filter(win, obj, event)          — installed on the main window
  apply_window_rounding(win)             — corner-mask after resize/show
  set_resize_cursor(win, edges)          — override / restore cursor
  resize_edges_at(win, global_pos)       — hit-test window edges
  cursor_for_edges(edges)                — map Qt.Edges → Qt.CursorShape
  event_global_pos(event)                — compat shim for globalPosition
"""

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QPainterPath, QRegion
from PySide6.QtWidgets import QApplication, QWidget


def _qapp():
    """Return the running QApplication (typed correctly for static analysers)."""
    a = QApplication.instance()
    if isinstance(a, QApplication):
        return a
    return None  # pragma: no cover


# ── Internal helpers ─────────────────────────────────────────────────────────


def event_global_pos(event):
    """QMouseEvent / QHoverEvent → QPoint, cross-version."""
    try:
        return event.globalPosition().toPoint()
    except Exception:
        try:
            return event.globalPos()
        except Exception:
            return None


def resize_edges_at(win, global_pos):
    """Return Qt.Edges flags if *global_pos* is within the resize margin
    of *win*, or ``None`` if the window is maximised/minimised or the
    position is outside the resize zone."""
    if global_pos is None or win.isMaximized() or win.isMinimized():
        return None
    try:
        p = win.mapFromGlobal(global_pos)
        r = win.rect()
        m = int(getattr(win, "_resize_margin", 7))
        left = 0 <= p.x() <= m
        right = r.width() - m <= p.x() <= r.width()
        top = 0 <= p.y() <= m
        bottom = r.height() - m <= p.y() <= r.height()

        edges = None
        if left:
            edges = Qt.Edge.LeftEdge
        elif right:
            edges = Qt.Edge.RightEdge
        if top:
            edges = Qt.Edge.TopEdge if edges is None else edges | Qt.Edge.TopEdge
        elif bottom:
            edges = Qt.Edge.BottomEdge if edges is None else edges | Qt.Edge.BottomEdge
        return edges
    except Exception:
        return None


def cursor_for_edges(edges):
    """Map a combination of Qt.Edges to the standard resize cursor shape."""
    try:
        if edges in (
            Qt.Edge.LeftEdge | Qt.Edge.TopEdge,
            Qt.Edge.RightEdge | Qt.Edge.BottomEdge,
        ):
            return Qt.CursorShape.SizeFDiagCursor
        if edges in (
            Qt.Edge.RightEdge | Qt.Edge.TopEdge,
            Qt.Edge.LeftEdge | Qt.Edge.BottomEdge,
        ):
            return Qt.CursorShape.SizeBDiagCursor
        if edges in (Qt.Edge.LeftEdge, Qt.Edge.RightEdge):
            return Qt.CursorShape.SizeHorCursor
        if edges in (Qt.Edge.TopEdge, Qt.Edge.BottomEdge):
            return Qt.CursorShape.SizeVerCursor
    except Exception:
        pass
    return None


def set_resize_cursor(win, edges):
    """Push / swap / pop the override cursor based on the current hit edge."""
    cursor = cursor_for_edges(edges) if edges else None
    try:
        if cursor:
            if not getattr(win, "_resize_cursor_active", False):
                app = _qapp()
                if app is not None:
                    app.setOverrideCursor(cursor)
                win._resize_cursor_active = True
            else:
                app = _qapp()
                if app is not None:
                    app.changeOverrideCursor(cursor)
        elif getattr(win, "_resize_cursor_active", False):
            app = _qapp()
            if app is not None:
                app.restoreOverrideCursor()
            win._resize_cursor_active = False
    except Exception:
        pass


# ── Corner rounding ──────────────────────────────────────────────────────────


def apply_window_rounding(win):
    """Apply a corner-radius mask on the frameless window.

    Maximised / full-screen windows get an unmasked rectangle so there are
    no transparent gaps at the corners.
    """
    try:
        if win.isMaximized() or win.isFullScreen():
            win.clearMask()
            return
        radius = int(getattr(win, "_corner_radius", 12))
        path = QPainterPath()
        path.addRoundedRect(QRectF(win.rect()), radius, radius)
        win.setMask(QRegion(path.toFillPolygon().toPolygon()))
    except Exception:
        try:
            win.clearMask()
        except Exception:
            pass


# ── eventFilter (installed on the main window) ──────────────────────────────


def event_filter(win, obj, event):
    """QWidget.eventFilter – resize cursor / edge-drag logic.

    Install on the main window with::

        self.installEventFilter(self)

    Then delegate from the window's ``eventFilter``::

        def eventFilter(self, obj, event):
            from .window_chrome import event_filter
            if event_filter(self, obj, event):
                return True
            return super().eventFilter(obj, event)
    """
    try:
        if isinstance(obj, QWidget) and obj.window() is win:
            et = event.type()
            if et == QEvent.Type.MouseMove:
                set_resize_cursor(win, resize_edges_at(win, event_global_pos(event)))
            elif (
                et == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
            ):
                edges = resize_edges_at(win, event_global_pos(event))
                if edges:
                    set_resize_cursor(win, edges)
                    wh = win.windowHandle()
                    if wh is not None and hasattr(wh, "startSystemResize"):
                        try:
                            if wh.startSystemResize(edges):
                                event.accept()
                                return True
                        except Exception:
                            pass
            elif et == QEvent.Type.MouseButtonRelease or et == QEvent.Type.Leave:
                set_resize_cursor(win, None)
    except Exception:
        pass
    return False
