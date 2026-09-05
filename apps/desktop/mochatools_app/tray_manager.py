"""tray_manager.py — System-tray icon, context menu, and live upload tooltip.

All functions receive a ``win`` (MochaTools) instance and a ``ctx``
(AppContext) and attach private attributes / methods to ``win``, using
``ctx`` for cross-module mutable state.

Public API
----------
  setup_tray(win, ctx)   - call once after build_ui
  tray_enabled(win, ctx) - predicate
  quit_from_tray(win, ctx)

Attached on ``win`` during setup_tray:
  win._on_tray_setting_toggled(enabled)
  win._on_tray_activated(reason)
  win._restore_from_tray()
  win._quit_from_tray()
  win._tray_enabled()
  win._upload_tab_status()
  win._mass_upload_status()
  win._sync_tab_status()
"""

from __future__ import annotations

import contextlib
from functools import partial
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .constants import APP_NAME
from .logging_utils import write_debug_log
from .theme import get_accent
from .ui import lucide_icon
from .utils import fmt_eta, fmt_speed

if TYPE_CHECKING:
    from .app import AppContext

_KB = 1024

# ── Live upload status (one-liners) ─────────────────────────────────────────


def _upload_tab_status(
    win: Any,
    ctx: AppContext,
) -> tuple[bool, float, float, int | None]:
    """(active, pct, speed_bps, remaining_bytes) for the single-file Upload tab."""
    if not ctx.is_uploading:
        return False, 0.0, 0.0, None
    pct = 0.0
    with contextlib.suppress(Exception):
        pct = win.progress_bar.value() / 1000.0
    remaining = (
        max(ctx.last_bytes_total - ctx.last_bytes_done, 0)
        if ctx.last_bytes_total
        else None
    )
    return True, pct, ctx.last_speed_bps, remaining


def _mass_upload_status(
    win: Any,
    _ctx: AppContext,
) -> tuple[bool, float, float, int | None]:
    """(active, pct, speed_bps, remaining_bytes) for the Mass Upload section."""
    sec = getattr(win, "mass_upload_section", None)
    if not sec:
        return False, 0.0, 0.0, None
    active = bool(getattr(sec, "_active_workers", None))
    if not active:
        return False, 0.0, 0.0, None
    pct = 0.0
    with contextlib.suppress(Exception):
        pct = sec._prog_bar.value() / 1000.0
    speed = getattr(sec, "_last_speed_bps", 0.0)
    remaining = None
    try:
        queue = getattr(sec, "_queue", [])
        all_done = sum(e.get("_bytes_done", 0) for e in queue)
        all_total = sum(e.get("_bytes_total", 0) for e in queue)
        if all_total:
            remaining = max(all_total - all_done, 0)
    except (AttributeError, TypeError, RuntimeError) as e:
        write_debug_log(f"[Silenced] _mass_upload_status: {e}")
    return True, pct, speed, remaining


def _sync_tab_status(
    win: Any,
    _ctx: AppContext,
) -> tuple[bool, float, float, int | None]:
    """(active, pct, speed_bps, remaining_bytes) for the Sync tab."""
    st = getattr(win, "sync_tab", None)
    if not st:
        return False, 0.0, 0.0, None
    pairs = getattr(st, "_pairs", {}) or {}
    active_pairs = [p for p in pairs.values() if p.get("status") == "uploading"]
    if not active_pairs:
        return False, 0.0, 0.0, None
    speed = sum(p.get("_speed_bps", 0.0) for p in active_pairs)
    pct = 0.0
    if len(active_pairs) == 1:
        p = active_pairs[0]
        done, total = p.get("_bytes_done", 0), p.get("_bytes_total", 0)
        if total:
            pct = (done / total) * 100.0
    remaining = None
    totals = [(p.get("_bytes_done", 0), p.get("_bytes_total", 0)) for p in active_pairs]
    if all(total for _, total in totals):
        remaining = sum(max(total - done, 0) for done, total in totals)
    return True, pct, speed, remaining


# ── Tooltip refresh (called every 1 s) ─────────────────────────────────────


def _refresh_tray_tooltip(win: Any, ctx: AppContext) -> None:
    sources = [
        _upload_tab_status(win, ctx),
        _mass_upload_status(win, ctx),
        _sync_tab_status(win, ctx),
    ]
    active_sources = [s for s in sources if s[0]]

    if not active_sources:
        if ctx.tray_icon:
            ctx.tray_icon.setToolTip(APP_NAME)
        if getattr(win, "titlebar", None):
            win.titlebar.set_eta_text("")
        return

    total_speed = sum(s[2] for s in active_sources)

    remainings = [r for r in (s[3] for s in active_sources) if r is not None]
    eta_text = ""
    if total_speed > _KB and remainings:
        total_remaining = sum(remainings)
        eta_seconds = total_remaining / total_speed
        eta_text = f"ETA {fmt_eta(eta_seconds)}"

    if getattr(win, "titlebar", None):
        win.titlebar.set_eta_text(eta_text)

    if not ctx.tray_icon:
        return

    if len(active_sources) == 1:
        _, pct, speed, _ = active_sources[0]
        tooltip = f"{APP_NAME}\n{pct:.3f}% · {fmt_speed(speed)}"
    else:
        tooltip = f"{APP_NAME}\nUploading · {fmt_speed(total_speed)}"

    if eta_text:
        tooltip += f"\n{eta_text}"

    ctx.tray_icon.setToolTip(tooltip)


# ── Activation handler ──────────────────────────────────────────────────────


def _restore_from_tray(win: Any) -> None:
    try:
        win.showNormal()
        win.raise_()
        win.activateWindow()
    except RuntimeError:
        pass


def _on_tray_activated(win: Any, reason: QSystemTrayIcon.ActivationReason) -> None:
    if reason in (
        QSystemTrayIcon.ActivationReason.Trigger,
        QSystemTrayIcon.ActivationReason.DoubleClick,
    ):
        _restore_from_tray(win)


def _on_tray_setting_toggled(_win: Any, ctx: AppContext, enabled: bool) -> None:
    """Called when the Settings > System Tray checkbox changes."""
    if not ctx.tray_icon:
        return
    if enabled:
        ctx.tray_icon.show()
    else:
        ctx.tray_icon.hide()


def _quit_from_tray(win: Any, ctx: AppContext) -> None:
    ctx.quitting = True
    win.close()


def _tray_enabled(win: Any, ctx: AppContext) -> bool:
    cb = getattr(win, "minimize_to_tray_cb", None)
    return bool(cb and cb.isChecked() and ctx.tray_icon is not None)


# ── Setup ───────────────────────────────────────────────────────────────────


def setup_tray(win: Any, ctx: AppContext) -> None:
    """Create the QSystemTrayIcon and 1 s tooltip timer.

    Uses ``ctx`` for shared state; attaches convenience methods to ``win``
    so the rest of the codebase can keep the same call sites.
    """
    if not QSystemTrayIcon.isSystemTrayAvailable():
        ctx.tray_icon = None
        ctx.quitting = False
        win._on_tray_setting_toggled = lambda _enabled: None
        win._tray_enabled = lambda: False
        win._restore_from_tray = lambda: None
        win._quit_from_tray = lambda: None
        return

    tray = QSystemTrayIcon(win)
    with contextlib.suppress(Exception):
        tray.setIcon(lucide_icon("coffee", get_accent(), 32))
    tray.setToolTip(APP_NAME)

    menu = QMenu()
    show_action = QAction("Show Mocha Tools", win)
    quit_action = QAction("Quit", win)
    menu.addAction(show_action)
    menu.addSeparator()
    menu.addAction(quit_action)
    tray.setContextMenu(menu)

    # Bind handler closures that capture *win* and *ctx*
    def _restore() -> None:
        _restore_from_tray(win)

    def _quit() -> None:
        _quit_from_tray(win, ctx)

    def _activated(r: QSystemTrayIcon.ActivationReason) -> None:
        _on_tray_activated(win, r)

    show_action.triggered.connect(_restore)
    quit_action.triggered.connect(_quit)
    tray.activated.connect(_activated)

    # Store in ctx
    ctx.tray_icon = tray
    ctx.quitting = False

    # Show / hide wiring — called from Settings > System Tray checkbox
    def _on_toggled(enabled: bool) -> None:
        _on_tray_setting_toggled(win, ctx, enabled)

    win._on_tray_setting_toggled = _on_toggled

    # Convenience predicates / actions for changeEvent / closeEvent
    def _enabled() -> bool:
        return _tray_enabled(win, ctx)

    def _restore_from() -> None:
        _restore_from_tray(win)

    def _quit_from() -> None:
        _quit_from_tray(win, ctx)

    win._tray_enabled = _enabled
    win._restore_from_tray = _restore_from
    win._quit_from_tray = _quit_from

    # 1 s tooltip timer
    timer = QTimer(win)
    timer.setInterval(1000)
    timer.timeout.connect(partial(_refresh_tray_tooltip, win, ctx))
    timer.start()
    ctx.tray_tooltip_timer = timer

    # Hidden until the user enables "Minimize and close to tray"
    tray.hide()
