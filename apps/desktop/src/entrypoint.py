"""entrypoint.py — Application entry point and top-level palette / theme wiring.

Public API
----------
  main()
"""

from __future__ import annotations

import contextlib
import sys
from functools import partial
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPalette
from PySide6.QtWidgets import QApplication

from .constants import APP_NAME, ORG_NAME
from .logging_utils import write_debug_log
from .styles import STYLESHEET, build_stylesheet
from .theme import (
    accent_qcolor,
    get_accent,
    get_background,
    get_background_palette,
    get_font,
    notifier,
)

# ── Palette ──────────────────────────────────────────────────────────────────


def _build_app_palette() -> QPalette:
    """Build a QPalette from the active background theme + accent."""
    pal_colors = get_background_palette()
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(pal_colors["bg0"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(pal_colors["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(pal_colors["bg7"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(pal_colors["text"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(pal_colors["bg3"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(pal_colors["text"]))
    palette.setColor(QPalette.ColorRole.Highlight, accent_qcolor())
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#111010"))
    return palette


# ── Accent / background / font live-update wiring ────────────────────────────


def _wire_theme_signals(win: Any) -> None:
    """Connect the theme-notifier signals to the app-level stylesheet /
    palette / font refreshers.  Called once from main() after the window
    is shown.
    """

    def _on_accent_changed(_old_hx: str, hx: str) -> None:
        try:
            a = QApplication.instance()
            if a:
                a.setStyleSheet(build_stylesheet(hx, background_key=get_background()))
                pal = a.palette()
                pal.setColor(QPalette.ColorRole.Highlight, accent_qcolor())
                a.setPalette(pal)
                try:
                    if hasattr(win, "_refresh_accented_icons"):
                        win._refresh_accented_icons()
                except (AttributeError, TypeError, RuntimeError) as e:
                    write_debug_log(f"[Silenced] _on_accent_changed: {e}")
                try:
                    fam, sz = get_font()
                    if fam:
                        a.setFont(QFont(fam, int(sz)))
                except (AttributeError, TypeError, RuntimeError, ValueError) as e:
                    write_debug_log(f"[Silenced] _on_accent_changed: {e}")
        except (AttributeError, TypeError, RuntimeError, ValueError) as e:
            write_debug_log(f"[Silenced] _on_accent_changed: {e}")

    def _on_background_changed(_old_key: str, new_key: str) -> None:
        try:
            a = QApplication.instance()
            if a:
                a.setStyleSheet(build_stylesheet(get_accent(), background_key=new_key))
                a.setPalette(_build_app_palette())
                try:
                    if hasattr(win, "_refresh_accented_icons"):
                        win._refresh_accented_icons()
                except (AttributeError, TypeError, RuntimeError) as e:
                    write_debug_log(f"[Silenced] _on_background_changed: {e}")
                try:
                    if hasattr(win, "titlebar") and hasattr(
                        win.titlebar,
                        "_refresh_icons",
                    ):
                        win.titlebar._refresh_icons()
                except (AttributeError, TypeError, RuntimeError) as e:
                    write_debug_log(f"[Silenced] _on_background_changed: {e}")
                try:
                    if hasattr(win, "_style_copy_share_btn"):
                        win._style_copy_share_btn()
                except (AttributeError, TypeError, RuntimeError) as e:
                    write_debug_log(f"[Silenced] _on_background_changed: {e}")
                try:
                    if hasattr(win, "status_badge") and hasattr(
                        win,
                        "_last_badge_args",
                    ):
                        win._badge(*win._last_badge_args)
                except (AttributeError, TypeError, RuntimeError) as e:
                    write_debug_log(f"[Silenced] _on_background_changed: {e}")
        except (AttributeError, TypeError, RuntimeError) as e:
            write_debug_log(f"[Silenced] _on_background_changed: {e}")

    def _on_font_change(fam: str, sz: int) -> None:
        try:
            a = QApplication.instance()
            if a:
                a.setFont(QFont(fam, int(sz)))
                for w in a.topLevelWidgets():
                    with contextlib.suppress(Exception):
                        a.style().unpolish(w)
                    with contextlib.suppress(Exception):
                        a.style().polish(w)
                with contextlib.suppress(Exception):
                    a.setStyleSheet(
                        build_stylesheet(get_accent(), background_key=get_background()),
                    )
        except (AttributeError, TypeError, RuntimeError, ValueError) as e:
            write_debug_log(f"[Silenced] _on_font_change: {e}")

    notifier().accent_changed.connect(_on_accent_changed)
    notifier().background_changed.connect(_on_background_changed)
    notifier().font_changed.connect(_on_font_change)

    with contextlib.suppress(Exception):
        _on_accent_changed("", get_accent())
    try:
        fam, sz = get_font()
        _on_font_change(fam, sz)
    except (AttributeError, TypeError, RuntimeError) as e:
        write_debug_log(f"[Silenced] _wire_theme_signals: {e}")


def _refresh_accented_icons(win: Any) -> None:
    """Re-apply accent-coloured icons after a theme change."""
    try:
        from .ui import lucide_icon

        if hasattr(win, "upload_btn"):
            win.upload_btn.setIcon(lucide_icon("upload", "#111010", 15))
            win.upload_btn.setIconSize(QSize(15, 15))
        if hasattr(win, "cancel_btn"):
            win.cancel_btn.setIcon(lucide_icon("x", get_accent(), 13))
            win.cancel_btn.setIconSize(QSize(13, 13))
        try:
            if hasattr(win, "mass_upload_section") and hasattr(
                win.mass_upload_section,
                "_start_btn",
            ):
                win.mass_upload_section._start_btn.setIcon(
                    lucide_icon("upload", "#111010", 15),
                )
                win.mass_upload_section._start_btn.setIconSize(QSize(15, 15))
        except (AttributeError, TypeError, RuntimeError) as e:
            write_debug_log(f"[Silenced] _refresh_accented_icons: {e}")
        try:
            if hasattr(win, "titlebar") and hasattr(win.titlebar, "_refresh_icons"):
                win.titlebar._refresh_icons()
        except (AttributeError, TypeError, RuntimeError) as e:
            write_debug_log(f"[Silenced] _refresh_accented_icons: {e}")
        try:
            if hasattr(win, "install_update_btn"):
                acc = get_accent()
                win.install_update_btn.setStyleSheet(
                    f"min-height:0px; padding:0px 16px;"
                    f" font-size:13px; font-weight:700;"
                    f" background:{acc}; color:#111010;"
                    " border:none; border-radius:7px;",
                )
        except (AttributeError, TypeError, RuntimeError) as e:
            write_debug_log(f"[Silenced] _refresh_accented_icons: {e}")
        try:
            tab_icons = [
                ("upload", get_accent()),
                ("download-cloud", get_accent()),
                ("folder", get_accent()),
                ("share-2", get_accent()),
                ("refresh-cw", get_accent()),
                ("settings", get_accent()),
            ]
            if hasattr(win, "tabs"):
                for i, (icon_name, color) in enumerate(tab_icons):
                    with contextlib.suppress(Exception):
                        win.tabs.setTabIcon(i, lucide_icon(icon_name, color, 14))
        except (AttributeError, TypeError, RuntimeError) as e:
            write_debug_log(f"[Silenced] _refresh_accented_icons: {e}")
    except (AttributeError, TypeError, RuntimeError, ImportError) as e:
        write_debug_log(f"[Silenced] _refresh_accented_icons: {e}")


# ── Entry point ──────────────────────────────────────────────────────────────


def _icon_path() -> str | None:
    here = Path(__file__).resolve().parent
    candidates = (
        here / "icon.ico",
        here.parent / "icon.ico",
        here.parent / "build" / "windows" / "icon.ico",
    )
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def _window_icon() -> QIcon:
    from .ui import app_icon

    p = _icon_path()
    if p:
        return QIcon(p)
    return app_icon(256)


_prev_win_icons: list[int] = []


def _set_window_icon(win: Any) -> None:
    """Send WM_SETICON to the native window handle so the taskbar shows the
    gold icon.  Must run after the window is visible.  Windows only.
    """
    if sys.platform != "win32":
        return
    global _prev_win_icons
    import ctypes
    from ctypes import wintypes

    p = _icon_path()
    if not p:
        return
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.LoadImageW.argtypes = [
        wintypes.HINSTANCE,
        wintypes.LPCWSTR,
        wintypes.UINT,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.SendMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.SendMessageW.restype = wintypes.LPARAM
    user32.DestroyIcon.argtypes = [wintypes.HICON]
    user32.DestroyIcon.restype = wintypes.BOOL

    hwnd = int(win.winId())
    hicon = user32.LoadImageW(None, p, 1, 32, 32, 0x10)
    hicon_sm = user32.LoadImageW(None, p, 1, 16, 16, 0x10)
    if hicon:
        user32.SendMessageW(hwnd, 0x0080, 1, hicon)
    if hicon_sm:
        user32.SendMessageW(hwnd, 0x0080, 0, hicon_sm)
    for h in _prev_win_icons:
        user32.DestroyIcon(h)
    _prev_win_icons = [h for h in (int(hicon), int(hicon_sm)) if h]


def _set_foreground(win: Any) -> None:
    """Bring the window to the foreground (Windows foreground-lock workaround)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.SetForegroundWindow(int(win.winId()))
    except (AttributeError, OSError, RuntimeError) as e:
        write_debug_log(f"[Silenced] _set_foreground: {e}")


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(_window_icon())
    try:
        app.setStyleSheet(
            build_stylesheet(get_accent(), background_key=get_background()),
        )
    except (AttributeError, TypeError, RuntimeError) as e:
        write_debug_log(f"[Silenced] main: {e}")
        app.setStyleSheet(STYLESHEET)

    try:
        fam, sz = get_font()
        if fam:
            app.setFont(QFont(fam, int(sz)))
    except (AttributeError, TypeError, RuntimeError, ValueError) as e:
        write_debug_log(f"[Silenced] main: {e}")

    app.setPalette(_build_app_palette())

    # Single-instance guard: a second launch focuses the running window.
    _win_holder: dict[str, Any] = {"win": None}

    def _focus_existing() -> None:
        win = _win_holder["win"]
        if win is None:
            return
        try:
            if hasattr(win, "_restore_from_tray"):
                win._restore_from_tray()
            else:
                win.showNormal()
                win.raise_()
                win.activateWindow()
            _set_foreground(win)
        except (AttributeError, RuntimeError, OSError) as e:
            write_debug_log(f"[Silenced] _focus_existing: {e}")

    from .single_instance import ensure_single_instance

    if not ensure_single_instance(_focus_existing):
        sys.exit(0)

    test_update = "--test-update" in sys.argv

    from .app import MochaTools

    win = MochaTools()
    _win_holder["win"] = win
    win.setWindowIcon(_window_icon())
    win.show()
    QTimer.singleShot(0, lambda: _set_window_icon(win))
    QTimer.singleShot(1000, lambda: _set_window_icon(win))

    # Wire accent refresh into win so settings / theme can call it
    win._refresh_accented_icons = lambda: _refresh_accented_icons(win)

    _wire_theme_signals(win)

    # Check API key + start poller
    def _preload() -> None:
        if win.api_key_edit.text().strip():
            win._poller.start()

    QTimer.singleShot(300, _preload)

    if test_update:
        QTimer.singleShot(500, win._trigger_test_update)
    elif (
        getattr(win, "check_updates_on_launch_cb", None) is None
        or win.check_updates_on_launch_cb.isChecked()
    ):
        QTimer.singleShot(2000, partial(win._check_for_updates, silent=True))

    sys.exit(app.exec())
