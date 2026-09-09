"""window_chrome.py — Frameless-window resize and cursor logic.

Extracted from the former MochaTools God-Object in app.py.

Public API (free functions that receive a QMainWindow subclass instance):
  event_filter(win, obj, event)          — installed on the main window
  set_resize_cursor(win, edges)          — override / restore cursor
  resize_edges_at(win, global_pos)       — hit-test window edges
  cursor_for_edges(edges)                — map Qt.Edges → Qt.CursorShape
  event_global_pos(event)                — compat shim for globalPosition

Corner rounding is handled by the window's own anti-aliased paintEvent
(see MochaTools.paintEvent in app.py); no mask is used.
"""

from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt
from PySide6.QtGui import QHoverEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget

from .logging_utils import write_debug_log


def _qapp() -> QApplication | None:
    """Return the running QApplication (typed correctly for static analysers)."""
    a = QApplication.instance()
    if isinstance(a, QApplication):
        return a
    return None  # pragma: no cover


# ── Internal helpers ─────────────────────────────────────────────────────────


def event_global_pos(event: QMouseEvent | QHoverEvent) -> QPoint | None:
    """QMouseEvent / QHoverEvent → QPoint, cross-version."""
    try:
        return event.globalPosition().toPoint()
    except (AttributeError, TypeError, RuntimeError) as e:
        write_debug_log(f"[Silenced] event_global_pos: {e}")
        if isinstance(event, QMouseEvent):
            try:
                return event.globalPos()
            except (AttributeError, TypeError, RuntimeError) as e:
                write_debug_log(f"[Silenced] event_global_pos: {e}")
        return None


def resize_edges_at(win: QWidget, global_pos: QPoint | None) -> Qt.Edge | None:
    """Return Qt.Edges flags if *global_pos* is within the resize margin
    of *win*, or ``None`` if the window is maximised/minimised or the
    position is outside the resize zone.
    """
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
    except (AttributeError, TypeError, RuntimeError, ValueError) as e:
        write_debug_log(f"[Silenced] resize_edges_at: {e}")
        return None
    else:
        return edges


def cursor_for_edges(edges: Qt.Edge | None) -> Qt.CursorShape | None:
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
    except (AttributeError, TypeError, RuntimeError) as e:
        write_debug_log(f"[Silenced] cursor_for_edges: {e}")
    return None


def set_resize_cursor(win: QWidget, edges: Qt.Edge | None) -> None:
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
    except (AttributeError, TypeError, RuntimeError) as e:
        write_debug_log(f"[Silenced] set_resize_cursor: {e}")


# ── eventFilter (installed on the main window) ──────────────────────────────


def event_filter(win: QWidget, obj: QObject, event: QEvent) -> bool:
    """QWidget.eventFilter - resize cursor / edge-drag logic.

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
        # With native WM_NCHITTEST hit-testing active, Windows handles the
        # resize edges itself (cursor + drag), so this manual logic is skipped.
        if getattr(win, "_native_hit_test", False):
            return False
        if isinstance(obj, QWidget) and obj.window() is win:
            et = event.type()
            if et == QEvent.Type.MouseMove and isinstance(
                event,
                (QMouseEvent, QHoverEvent),
            ):
                set_resize_cursor(win, resize_edges_at(win, event_global_pos(event)))
            elif (
                et == QEvent.Type.MouseButtonPress
                and isinstance(event, QMouseEvent)
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
                        except (AttributeError, TypeError, RuntimeError) as e:
                            write_debug_log(f"[Silenced] event_filter: {e}")
            elif et in (QEvent.Type.MouseButtonRelease, QEvent.Type.Leave):
                set_resize_cursor(win, None)
    except (AttributeError, TypeError, RuntimeError) as e:
        write_debug_log(f"[Silenced] event_filter: {e}")
    return False


# ── Native Windows hit-testing (Aero Snap / native resize) ──────────────────

WM_NCHITTEST = 0x0084
WM_NCCALCSIZE = 0x0083
WM_GETMINMAXINFO = 0x0024
GWL_STYLE = -16
WS_THICKFRAME = 0x00040000

HTCLIENT = 1
HTCAPTION = 2
HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MINMAXINFO(ctypes.Structure):
    _fields_ = [
        ("ptReserved", _POINT),
        ("ptMaxSize", _POINT),
        ("ptMaxPosition", _POINT),
        ("ptMinTrackSize", _POINT),
        ("ptMaxTrackSize", _POINT),
    ]


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", ctypes.c_ulong),
    ]


def enable_native_snap(win: QWidget) -> None:
    """Add WS_THICKFRAME so Windows treats the frameless window as resizable,
    which is what enables native Aero Snap.  The frame itself stays hidden via
    the WM_NCCALCSIZE handler in handle_native_message."""
    try:
        if not (win.windowFlags() & Qt.WindowType.FramelessWindowHint):
            return
        hwnd = int(win.winId())
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
        user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style | WS_THICKFRAME)
        user32.SetWindowPos(
            hwnd,
            0,
            0,
            0,
            0,
            0,
            0x0020
            | 0x0001
            | 0x0002
            | 0x0004
            | 0x0010,  # FRAMECHANGED|NOSIZE|NOMOVE|NOZORDER|NOACTIVATE
        )
        win._native_hit_test = True
    except (AttributeError, TypeError, RuntimeError, OSError) as e:
        write_debug_log(f"[Silenced] enable_native_snap: {e}")


def ht_for_edges(edges: Qt.Edge) -> int:
    """Map Qt.Edges flags to the matching Windows HT* hit-test constant."""
    if edges == Qt.Edge.LeftEdge | Qt.Edge.TopEdge:
        return HTTOPLEFT
    if edges == Qt.Edge.RightEdge | Qt.Edge.TopEdge:
        return HTTOPRIGHT
    if edges == Qt.Edge.LeftEdge | Qt.Edge.BottomEdge:
        return HTBOTTOMLEFT
    if edges == Qt.Edge.RightEdge | Qt.Edge.BottomEdge:
        return HTBOTTOMRIGHT
    if edges == Qt.Edge.LeftEdge:
        return HTLEFT
    if edges == Qt.Edge.RightEdge:
        return HTRIGHT
    if edges == Qt.Edge.TopEdge:
        return HTTOP
    if edges == Qt.Edge.BottomEdge:
        return HTBOTTOM
    return HTCLIENT


def _over_titlebar_button(tb: QWidget, x: int, y: int) -> bool:
    for name in ("_min_btn", "_max_btn", "_cls_btn"):
        btn = getattr(tb, name, None)
        if (
            btn is not None
            and btn.isVisible()
            and btn.rect().contains(btn.mapFromGlobal(QPoint(x, y)))
        ):
            return True
    return False


def hit_test(win: QWidget, x: int, y: int) -> int:
    """WM_NCHITTEST result: resize edges → HT*, titlebar → HTCAPTION, else HTCLIENT."""
    try:
        edges = resize_edges_at(win, QPoint(x, y))
        if edges:
            return ht_for_edges(edges)
        tb = getattr(win, "titlebar", None)
        if (
            tb is not None
            and not getattr(tb, "_native_frame", False)
            and tb.rect().contains(tb.mapFromGlobal(QPoint(x, y)))
        ):
            if _over_titlebar_button(tb, x, y):
                return HTCLIENT
            return HTCAPTION
    except (AttributeError, TypeError, RuntimeError, ValueError) as e:
        write_debug_log(f"[Silenced] hit_test: {e}")
    return HTCLIENT


def _set_max_work_area(win: QWidget, lparam: int) -> None:
    if sys.platform != "win32":
        return
    from ctypes import wintypes

    try:
        mmi = _MINMAXINFO.from_address(int(lparam))
        hwnd = int(win.winId())
        user32 = ctypes.windll.user32
        user32.MonitorFromWindow.restype = ctypes.c_void_p
        user32.GetMonitorInfoW.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_MONITORINFO),
        ]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if user32.GetMonitorInfoW(monitor, ctypes.byref(mi)):
            mmi.ptMaxSize.x = mi.rcWork.right - mi.rcWork.left
            mmi.ptMaxSize.y = mi.rcWork.bottom - mi.rcWork.top
            mmi.ptMaxPosition.x = mi.rcWork.left
            mmi.ptMaxPosition.y = mi.rcWork.top
        mmi.ptMinTrackSize.x = win.minimumWidth()
        mmi.ptMinTrackSize.y = win.minimumHeight()
    except (AttributeError, TypeError, RuntimeError, OSError) as e:
        write_debug_log(f"[Silenced] _set_max_work_area: {e}")


def adjacent_window_edges(win: QWidget, geo: QRect, tol: int = 8) -> set[str]:
    """Return a set of edges ('top', 'bottom', 'left', 'right') where another
    visible top-level window sits flush against *geo* (the window's screen
    geometry).  Used to square off corners that touch a neighbouring window.
    The tolerance accounts for Windows' invisible resize borders (~7px)."""
    if sys.platform != "win32":
        return set()
    from ctypes import wintypes

    edges: set[str] = set()
    try:
        user32 = ctypes.windll.user32
        own = int(win.winId())

        WNDENUMPROC = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HWND,
            wintypes.LPARAM,
        )
        user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL

        rects: list[_RECT] = []

        @WNDENUMPROC
        def _cb(hwnd: int, _lparam: int) -> bool:
            if int(hwnd) == own:
                return True
            if not user32.IsWindowVisible(hwnd):
                return True
            if user32.IsIconic(hwnd):
                return True
            r = _RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
                return True
            rects.append(r)
            return True

        user32.EnumWindows(_cb, 0)

        top = geo.top()
        bottom = geo.bottom()
        left = geo.left()
        right = geo.right()

        for r in rects:
            if (
                abs(r.bottom - top) <= tol
                and r.left < right - tol
                and r.right > left + tol
            ):
                edges.add("top")
            if (
                abs(r.top - bottom) <= tol
                and r.left < right - tol
                and r.right > left + tol
            ):
                edges.add("bottom")
            if (
                abs(r.right - left) <= tol
                and r.top < bottom - tol
                and r.bottom > top + tol
            ):
                edges.add("left")
            if (
                abs(r.left - right) <= tol
                and r.top < bottom - tol
                and r.bottom > top + tol
            ):
                edges.add("right")
    except (AttributeError, TypeError, RuntimeError, OSError) as e:
        write_debug_log(f"[Silenced] adjacent_window_edges: {e}")
    return edges


# ── WinEvent hook (event-driven adjacency) ──────────────────────────────────

EVENT_OBJECT_CREATE = 0x8000
EVENT_OBJECT_DESTROY = 0x8001
EVENT_OBJECT_SHOW = 0x8002
EVENT_OBJECT_HIDE = 0x8003
EVENT_OBJECT_LOCATIONCHANGE = 0x800B
WINEVENT_OUTOFCONTEXT = 0x0000
OBJID_WINDOW = 0x0000

_WATCHED_EVENTS = {
    EVENT_OBJECT_CREATE,
    EVENT_OBJECT_DESTROY,
    EVENT_OBJECT_SHOW,
    EVENT_OBJECT_HIDE,
    EVENT_OBJECT_LOCATIONCHANGE,
}


def install_window_hook(win: QWidget) -> bool:
    """Install a WinEvent hook that notifies the window when other top-level
    windows move, resize, appear, or disappear.  Returns True on success.  The
    callback runs on the GUI thread and triggers a throttled adjacency
    recompute."""
    if sys.platform != "win32":
        return False
    from ctypes import wintypes

    WINEVENTPROC = ctypes.WINFUNCTYPE(
        None,
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.HWND,
        ctypes.c_long,
        ctypes.c_long,
        wintypes.DWORD,
        wintypes.DWORD,
    )

    try:
        if getattr(win, "_win_event_hook", None) is not None:
            return True
        user32 = ctypes.windll.user32
        user32.SetWinEventHook.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
            WINEVENTPROC,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        user32.SetWinEventHook.restype = wintypes.HANDLE
        user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
        user32.UnhookWinEvent.restype = wintypes.BOOL

        own = int(win.winId())

        @WINEVENTPROC
        def _cb(
            _hook: int,
            event: int,
            hwnd: int,
            id_obj: int,
            _id_child: int,
            _thread: int,
            _ms_time: int,
        ) -> None:
            try:
                if event not in _WATCHED_EVENTS:
                    return
                if id_obj != OBJID_WINDOW:
                    return
                if int(hwnd) == own:
                    return
                win._refresh_adjacency()
            except (AttributeError, TypeError, RuntimeError):
                pass

        hook = user32.SetWinEventHook(
            EVENT_OBJECT_CREATE,
            EVENT_OBJECT_LOCATIONCHANGE,
            0,
            _cb,
            0,
            0,
            WINEVENT_OUTOFCONTEXT,
        )
        if hook:
            win._win_event_hook = hook
            win._win_event_cb = _cb
            return True
    except (AttributeError, TypeError, RuntimeError, OSError) as e:
        write_debug_log(f"[Silenced] install_window_hook: {e}")
    return False


def uninstall_window_hook(win: QWidget) -> None:
    if sys.platform != "win32":
        return
    try:
        hook = getattr(win, "_win_event_hook", None)
        if hook:
            user32 = ctypes.windll.user32
            user32.UnhookWinEvent(hook)
            win._win_event_hook = None
    except (AttributeError, TypeError, RuntimeError, OSError) as e:
        write_debug_log(f"[Silenced] uninstall_window_hook: {e}")


def handle_native_message(
    win: QWidget,
    eventType: bytes,
    message: int,
) -> tuple[bool, int] | None:
    """Return (handled, result) for Windows native messages, or None to pass
    the message through to Qt's default handling."""
    if bytes(eventType) != b"windows_generic_MSG":
        return None
    from ctypes import wintypes

    if not (win.windowFlags() & Qt.WindowType.FramelessWindowHint):
        return None
    try:
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == WM_NCCALCSIZE:
            if msg.wParam:
                return True, 0
        elif msg.message == WM_NCHITTEST:
            x = ctypes.c_short(msg.lParam & 0xFFFF).value
            y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
            return True, hit_test(win, x, y)
        elif msg.message == WM_GETMINMAXINFO:
            _set_max_work_area(win, msg.lParam)
            return True, 0
    except (AttributeError, TypeError, RuntimeError, ValueError) as e:
        write_debug_log(f"[Silenced] handle_native_message: {e}")
    return None
