"""upload_manager.py — Single-file Upload tab: construction, flow, and signals.

Extracted from the former MochaTools God-Object in app.py.

Public API
----------
  build_upload_tab(win)   -> QWidget   (call once in MochaTools._build_ui)
  install_upload(win)     -> None      (attach all convenience methods on win)

Attached on ``win`` during install_upload:
  win._start_upload()
  win._cancel_upload()
  win._set_uploading(active)
  win._on_progress(pct)
  win._on_bytes_progress(done, total)
  win._on_speed(bps)
  win._on_finished(result)
  win._on_error(msg)
  win._on_upload_done(remote_folder)
  win._on_share_created()
  win._copy_share_result()
  win._log(msg)
  win._badge(text, color)
  win._style_copy_share_btn()
  win._refresh_storage()
  win._on_storage_done(data)
  win._on_storage_error(msg)
  win._set_upload_mode(mode)
  win._browse_upload_dest()
  win._toggle_key_visibility(checked)
  win._toggle_share_options(checked)
  win._on_files_selected(file_list, root)
"""

from __future__ import annotations

import contextlib
import os
import pathlib
from functools import partial
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .dialogs import FolderBrowserDialog
from .logging_utils import write_debug_log
from .theme import get_accent
from .ui import lucide_icon
from .upload_pipeline import UploadJob
from .workers import StorageWorker

if TYPE_CHECKING:
    from .app import AppContext

# ── Small reusable section-header / card helpers (self-contained) ────────────


def _sh(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setObjectName("section_header")
    return lbl


def _card() -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    return f


# ══════════════════════════════════════════════════════════════════════════════
#  UPLOAD TAB CONSTRUCTION
# ══════════════════════════════════════════════════════════════════════════════


def build_upload_tab(win: Any) -> QWidget:
    """Build the single-file Upload tab and return it as a QWidget."""
    upload_tab = QWidget()
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    inner = QWidget()
    main = QVBoxLayout(inner)
    main.setContentsMargins(18, 14, 18, 22)
    main.setSpacing(14)
    scroll.setWidget(inner)

    tab_lay = QVBoxLayout(upload_tab)
    tab_lay.setContentsMargins(0, 0, 0, 0)
    tab_lay.addWidget(scroll)
    win._upload_main_layout = main

    # ── Mode switcher ───────────────────────────────────────────────────────
    mode_row = QHBoxLayout()
    mode_row.setContentsMargins(0, 0, 0, 4)
    mode_row.setSpacing(8)

    win._mode_single_btn = QPushButton("  Single file")
    win._mode_multi_btn = QPushButton("  Multiple files")
    for b, icon in ((win._mode_single_btn, "upload"), (win._mode_multi_btn, "copy")):
        b.setCheckable(True)
        b.setObjectName("mode_btn")
        b.setIcon(lucide_icon(icon, get_accent(), 15))
        b.setIconSize(QSize(15, 15))
        b.setMinimumHeight(40)
        b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        b.setCursor(Qt.CursorShape.PointingHandCursor)

    win._mode_single_btn.clicked.connect(partial(win._set_upload_mode, "single"))
    win._mode_multi_btn.clicked.connect(partial(win._set_upload_mode, "multi"))
    mode_row.addWidget(win._mode_single_btn)
    mode_row.addWidget(win._mode_multi_btn)
    main.addLayout(mode_row)

    # ── Single-file container ───────────────────────────────────────────────
    win._single_box = QWidget()
    single_lay = QVBoxLayout(win._single_box)
    single_lay.setContentsMargins(0, 0, 0, 0)
    single_lay.setSpacing(14)
    main.addWidget(win._single_box)

    # FILE section
    single_lay.addWidget(_sh("File"))
    file_card = _card()
    file_lay = QVBoxLayout(file_card)
    from .ui import DropZone

    win.drop_zone = DropZone()
    win.drop_zone.selection_changed.connect(win._on_files_selected)
    file_lay.addWidget(win.drop_zone)
    single_lay.addWidget(file_card)

    # DESTINATION section
    single_lay.addWidget(_sh("Destination"))
    dest_card = _card()
    dest_lay = QVBoxLayout(dest_card)
    dest_lay.setSpacing(8)

    dest_row = QHBoxLayout()
    dest_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
    dest_lbl = QLabel("Folder")
    dest_lbl.setObjectName("field_label")
    win.upload_path_edit = QLineEdit("/")
    win.upload_path_edit.setPlaceholderText("/")
    browse_dest_btn = QPushButton("Browse…")
    browse_dest_btn.setObjectName("browse_btn")
    browse_dest_btn.setFixedSize(80, 34)
    browse_dest_btn.setToolTip("Browse remote folders to pick an upload destination")
    browse_dest_btn.clicked.connect(win._browse_upload_dest)
    dest_row.addWidget(dest_lbl)
    dest_row.addWidget(win.upload_path_edit, 1)
    dest_row.addWidget(browse_dest_btn)
    dest_lay.addLayout(dest_row)
    single_lay.addWidget(dest_card)

    # UPLOAD STATUS section
    single_lay.addWidget(_sh("Upload"))
    status_card = _card()
    status_lay = QVBoxLayout(status_card)
    status_lay.setSpacing(8)

    top_row = QHBoxLayout()
    win.status_badge = QLabel("● Idle")
    win.status_badge.setObjectName("status_badge")
    top_row.addWidget(win.status_badge)
    top_row.addStretch()
    status_lay.addLayout(top_row)

    speed_row = QHBoxLayout()
    speed_lbl = QLabel("Speed:")
    speed_lbl.setObjectName("field_label")
    win.speed_label = QLabel("")
    win.speed_label.setObjectName("status_label")
    win.speed_label.setStyleSheet(
        "color: #9ca3af; font-size: 11px; background:transparent;",
    )
    speed_row.addWidget(speed_lbl)
    speed_row.addWidget(win.speed_label)
    speed_row.addStretch()
    win.transferred_label = QLabel("")
    win.transferred_label.setStyleSheet(
        "color: #9ca3af; font-size: 11px; background:transparent;",
    )
    speed_row.addWidget(win.transferred_label)
    status_lay.addLayout(speed_row)

    prog_row = QHBoxLayout()
    win.progress_bar = QProgressBar()
    win.progress_bar.setMaximum(100_000)
    win.progress_bar.setValue(0)
    win.pct_label = QLabel("0.000%")
    win.pct_label.setObjectName("status_label")
    win.pct_label.setFixedWidth(58)
    prog_row.addWidget(win.progress_bar, 1)
    prog_row.addWidget(win.pct_label)
    status_lay.addLayout(prog_row)

    win.log_label = QLabel("Ready — select a file and destination folder, then upload.")
    win.log_label.setObjectName("log_console")
    win.log_label.setWordWrap(True)
    win.log_label.setMinimumHeight(46)
    win.log_label.setAlignment(
        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
    )
    win.log_label.setSizePolicy(
        QSizePolicy.Policy.Ignored,
        QSizePolicy.Policy.Preferred,
    )
    status_lay.addWidget(win.log_label)

    # Share result row
    share_result_row = QHBoxLayout()
    share_result_row.setContentsMargins(0, 0, 0, 0)
    share_result_row.setSpacing(8)
    win.share_result = QLabel("")
    win.share_result.setObjectName("log_console")
    win.share_result.setWordWrap(True)
    win.share_result.setOpenExternalLinks(True)
    win.copy_share_result_btn = QPushButton("Copy link")
    win.copy_share_result_btn.setFixedHeight(36)
    win._style_copy_share_btn()
    win.copy_share_result_btn.clicked.connect(win._copy_share_result)
    share_result_row.addWidget(win.share_result, 1)
    share_result_row.addWidget(win.copy_share_result_btn)
    win._share_result_widget = QWidget()
    win._share_result_widget.setLayout(share_result_row)
    win._share_result_widget.hide()
    status_lay.addWidget(win._share_result_widget)
    single_lay.addWidget(status_card)

    # SHARE OPTIONS section
    share_card = _card()
    share_lay = QVBoxLayout(share_card)
    share_lay.setSpacing(10)
    win.create_share_cb = QCheckBox("Create share link after upload")
    share_lay.addWidget(win.create_share_cb)
    win.create_share_cb.toggled.connect(win._toggle_share_options)

    win.share_opts_widget = QWidget()
    share_opts_lay = QVBoxLayout(win.share_opts_widget)
    share_opts_lay.setContentsMargins(0, 4, 0, 0)
    share_opts_lay.setSpacing(8)

    exp_row = QHBoxLayout()
    exp_lbl = QLabel("Expiration")
    exp_lbl.setObjectName("field_label")
    win.expiry_combo = QComboBox()
    win._expiry_map = [
        ("Never", None),
        ("1 hour", 1),
        ("6 hours", 6),
        ("12 hours", 12),
        ("1 day", 24),
        ("3 days", 72),
        ("7 days", 168),
        ("14 days", 336),
        ("30 days", 720),
    ]
    win.expiry_combo.addItems([label for label, _ in win._expiry_map])
    exp_row.addWidget(exp_lbl)
    exp_row.addWidget(win.expiry_combo, 1)
    share_opts_lay.addLayout(exp_row)

    dl_row = QHBoxLayout()
    dl_lbl = QLabel("Max downloads")
    dl_lbl.setObjectName("field_label")
    win.max_dl_spin = QSpinBox()
    win.max_dl_spin.setRange(0, 9999)
    win.max_dl_spin.setValue(0)
    win.max_dl_spin.setSpecialValueText("Unlimited")
    win.max_dl_spin.setSuffix(" downloads")
    dl_row.addWidget(dl_lbl)
    dl_row.addWidget(win.max_dl_spin, 1)
    share_opts_lay.addLayout(dl_row)

    share_lay.addWidget(win.share_opts_widget)
    win.share_opts_widget.hide()
    single_lay.addWidget(share_card)

    # UPLOAD BUTTON
    win.upload_btn = QPushButton("  Upload file")
    win.upload_btn.setObjectName("upload_btn")
    win.upload_btn.setIcon(lucide_icon("upload", "#111010", 15))
    win.upload_btn.setIconSize(QSize(15, 15))
    win.upload_btn.setMinimumHeight(42)
    win.upload_btn.clicked.connect(win._start_upload)
    single_lay.addWidget(win.upload_btn)

    win.cancel_btn = QPushButton("  Cancel")
    win.cancel_btn.setObjectName("browse_btn")
    win.cancel_btn.setIcon(lucide_icon("x", get_accent(), 13))
    win.cancel_btn.setIconSize(QSize(13, 13))
    win.cancel_btn.setMinimumHeight(36)
    win.cancel_btn.clicked.connect(win._cancel_upload)
    win.cancel_btn.hide()
    single_lay.addWidget(win.cancel_btn)
    single_lay.addStretch()

    return upload_tab


# ══════════════════════════════════════════════════════════════════════════════
#  INSTALLED METHOD BINDINGS
# ══════════════════════════════════════════════════════════════════════════════


def install_upload(win: Any, ctx: AppContext) -> None:
    """Attach all single-file-upload convenience methods to *win*.

    Uses *ctx* (AppContext) for cross-module mutable state.
    """
    from .settings import save_settings

    # ── Mode toggle ─────────────────────────────────────────────────────────
    def _set_upload_mode(mode: str, _checked: bool = False) -> None:
        multi = mode == "multi"
        with contextlib.suppress(Exception):
            win._single_box.setVisible(not multi)
        try:
            sec = getattr(win, "mass_upload_section", None)
            if sec is not None:
                sec.setVisible(multi)
        except (AttributeError, TypeError, RuntimeError) as e:
            write_debug_log(f"[Silenced] _set_upload_mode: {e}")
        try:
            win._mode_single_btn.setChecked(not multi)
            win._mode_multi_btn.setChecked(multi)
        except (AttributeError, TypeError, RuntimeError) as e:
            write_debug_log(f"[Silenced] _set_upload_mode: {e}")

    win._set_upload_mode = _set_upload_mode

    # ── Widget helpers ──────────────────────────────────────────────────────
    def _toggle_key_visibility(checked: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        win.api_key_edit.setEchoMode(mode)

    win._toggle_key_visibility = _toggle_key_visibility

    def _toggle_share_options(checked: bool) -> None:
        win.share_opts_widget.setVisible(checked)

    win._toggle_share_options = _toggle_share_options

    def _on_files_selected(file_list: list[str], root: str) -> None:
        ctx.selected_files = file_list
        ctx.selected_root = root
        if len(file_list) == 1:
            win._log(f"[DEBUG] Selected: {pathlib.Path(file_list[0]).name}")
        else:
            win._log(f"[DEBUG] Selected folder: {len(file_list)} files")
        win._share_result_widget.hide()

    win._on_files_selected = _on_files_selected

    # ── Browse destination ──────────────────────────────────────────────────
    def _browse_upload_dest() -> None:
        if not ctx.client.has_api_key:
            win._log("⚠ Enter your API key in Settings before browsing folders.")
            return
        dlg = FolderBrowserDialog(
            ctx.client,
            win.upload_path_edit.text().strip() or "/",
            parent=win,
        )
        dlg.setWindowTitle("Choose upload destination folder")
        if dlg.exec():
            write_debug_log(f"[BrowseDest] dlg.selected={dlg.selected!r}")
            win.upload_path_edit.setText(dlg.selected)
            write_debug_log(
                f"[BrowseDest] upload_path_edit now={win.upload_path_edit.text()!r}",
            )

    win._browse_upload_dest = _browse_upload_dest

    # ── Status / log helpers ────────────────────────────────────────────────
    def _log(msg: str) -> None:
        debug_enabled = getattr(win, "debug_cb", None) and win.debug_cb.isChecked()
        if msg.startswith("[DEBUG]") and not debug_enabled:
            return
        win.log_label.setText(msg)
        if debug_enabled:
            write_debug_log(msg)

    win._log = _log

    def _badge(text: str, color: str) -> None:
        from .theme import DEFAULT_ACCENT, get_accent, get_background_palette

        win._last_badge_args = (text, color)
        win.status_badge.setText(f"● {text}")
        if color == DEFAULT_ACCENT:
            color = get_accent()
        try:
            pal = get_background_palette()
            neutral_bg, neutral_border = pal["bg3"], pal["border"]
        except (AttributeError, TypeError, RuntimeError) as e:
            write_debug_log(f"[Silenced] _badge: {e}")
            neutral_bg, neutral_border = "#1e1c19", "#2e2b27"
        bg_map = {
            "#c8a96e": "#2a2215",
            "#4ade80": "#0f2318",
            "#f87171": "#2a0f0f",
            "#9ca3af": neutral_bg,
        }
        bd_map = {
            "#c8a96e": "#4a3b1e",
            "#4ade80": "#1e4a30",
            "#f87171": "#4a1e1e",
            "#9caaf": neutral_border,
        }
        bg = bg_map.get(color, neutral_bg)
        bd = bd_map.get(color, neutral_border)
        win.status_badge.setStyleSheet(
            f"background-color: {bg}; border: 1px solid {bd}; "
            f"border-radius: 10px; color: {color}; font-size: 11px; "
            f"font-weight: 600; padding: 2px 10px;",
        )

    win._badge = _badge

    def _style_copy_share_btn() -> None:
        from .theme import get_background_palette

        try:
            pal = get_background_palette()
            bg3, text, border2 = pal["bg3"], pal["text"], pal["border2"]
        except (AttributeError, TypeError, RuntimeError) as e:
            write_debug_log(f"[Silenced] _style_copy_share_btn: {e}")
            bg3, text, border2 = "#1e1c19", "#f0ece6", "#3d3a35"
        win.copy_share_result_btn.setStyleSheet(
            "min-height:0px; padding:0px 16px;"
            " font-size:13px; font-weight:600;"
            f"background:{bg3}; color:{text};"
            f" border:1px solid {border2}; border-radius:7px;",
        )

    win._style_copy_share_btn = _style_copy_share_btn

    def _copy_share_result() -> None:
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(ctx.share_result_url)
        win.copy_share_result_btn.setText("Copied!")
        QTimer.singleShot(1500, partial(win.copy_share_result_btn.setText, "Copy link"))

    win._copy_share_result = _copy_share_result

    # ── Upload state toggle ─────────────────────────────────────────────────
    def _set_uploading(active: bool) -> None:
        ctx.is_uploading = active
        win.upload_btn.setVisible(not active)
        win.cancel_btn.setVisible(active)
        win.upload_btn.setEnabled(not active)

    win._set_uploading = _set_uploading

    # ── Start / cancel ──────────────────────────────────────────────────────
    current_job_id: int | None = None

    def _start_upload() -> None:
        nonlocal current_job_id

        upload_path = win.upload_path_edit.text().strip() or "/"
        if not ctx.client.has_api_key:
            win._log("⚠ Please enter an API key.")
            return
        if not ctx.selected_files:
            win._log("⚠ Please select a file or folder.")
            return

        save_settings(win)
        win._set_uploading(True)
        win._share_result_widget.hide()
        win.progress_bar.setValue(0)
        win.pct_label.setText("0.000%")
        win.speed_label.setText("")
        win.transferred_label.setText("")
        win._badge("Uploading", get_accent())

        idx = win.expiry_combo.currentIndex()
        expiry_hours = (
            win._expiry_map[idx][1]
            if win.create_share_cb.isChecked() and 0 <= idx < len(win._expiry_map)
            else None
        )
        max_dl = win.max_dl_spin.value() if win.create_share_cb.isChecked() else 0

        base_remote = "/" + upload_path.strip("/")
        file_pairs: list[tuple[str, str]] = []
        for local in ctx.selected_files:
            try:
                rel = os.path.relpath(local, ctx.selected_root).replace(os.sep, "/")
            except ValueError:
                rel = pathlib.Path(local).name
            if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
                rel = pathlib.Path(local).name
            dest = f"{base_remote}/{rel}" if base_remote != "/" else f"/{rel}"
            file_pairs.append((local, dest))
        win.upload_path_edit.setText(base_remote + "/")

        win._log(f"[DEBUG] Upload path: {upload_path!r} → base_remote: {base_remote!r}")
        for _local, dest in file_pairs[:3]:
            win._log(f"[DEBUG] Dest: {dest}")

        grand_total = 0
        for lp, _ in file_pairs:
            if pathlib.Path(lp).is_file():
                with contextlib.suppress(OSError):
                    grand_total += pathlib.Path(lp).stat().st_size
        ctx.upload_grand_total = grand_total

        job = UploadJob(
            file_pairs=file_pairs,
            create_share=win.create_share_cb.isChecked(),
            share_expiry_hours=expiry_hours,
            share_max_downloads=max_dl,
            chunk_size_mb=win.chunk_size_spin.value(),
            max_chunks=win.max_chunks_spin.value(),
            source="single",
        )
        current_job_id = ctx.upload_manager.enqueue(job)
        ctx.upload_manager.subscribe(
            current_job_id,
            {
                "progress": _on_progress,
                "speed": _on_speed,
                "bytes": _on_bytes_progress,
                "status": win._log,
                "done": _on_finished,
                "error": _on_error,
            },
        )

    win._start_upload = _start_upload

    def _cancel_upload() -> None:
        nonlocal current_job_id
        if current_job_id is not None:
            ctx.upload_manager.cancel(current_job_id)
            ctx.upload_manager.unsubscribe(current_job_id)
            current_job_id = None
        win._set_uploading(False)
        win._badge("Cancelled", "#9ca3af")
        win.progress_bar.setValue(0)
        win.pct_label.setText("0.000%")
        win.speed_label.setText("")
        win.transferred_label.setText("")
        win._share_result_widget.hide()
        win._log("Upload cancelled by user.")

    win._cancel_upload = _cancel_upload

    # ── Upload signal handlers ──────────────────────────────────────────────
    def _on_progress(pct: float) -> None:
        win.progress_bar.setValue(int(pct * 1000))
        win.pct_label.setText(f"{pct:.3f}%")

    win._on_progress = _on_progress

    def _on_bytes_progress(done_bytes: int, total_bytes: int) -> None:
        from .utils import fmt_bytes

        grand = ctx.upload_grand_total or total_bytes
        ctx.last_bytes_done = done_bytes
        ctx.last_bytes_total = grand
        win.transferred_label.setText(f"{fmt_bytes(done_bytes)} / {fmt_bytes(grand)}")

    win._on_bytes_progress = _on_bytes_progress

    def _on_speed(bps: float) -> None:
        from .utils import fmt_speed

        ctx.last_speed_bps = bps
        win.speed_label.setText(fmt_speed(bps))

    win._on_speed = _on_speed

    def _on_finished(result: dict) -> None:
        nonlocal current_job_id
        current_job_id = None
        ctx.is_uploading = False
        win._badge("Complete", "#4ade80")
        win.transferred_label.setText("")
        win._log(f"✓ Done! File ID: {result.get('file_id', '')}")
        try:
            from .sound_player import play_sound_event

            play_sound_event("sound_single_upload")
        except (AttributeError, TypeError, RuntimeError, ImportError) as e:
            write_debug_log(f"[Silenced] _on_finished: {e}")
        upload_path = win.upload_path_edit.text().strip() or "/"
        win._on_upload_done(upload_path)
        if result.get("share_url"):
            url = result["share_url"]
            ctx.share_result_url = url
            from .theme import get_accent

            win.share_result.setText(
                f'<a href="{url}" style="color:{get_accent()};">{url}</a>',
            )
            win._share_result_widget.show()
            win._on_share_created()

    win._on_finished = _on_finished

    def _on_error(msg: str) -> None:
        nonlocal current_job_id
        current_job_id = None
        ctx.is_uploading = False
        win._badge("Error", "#f87171")
        win.transferred_label.setText("")
        win._log(f"✗ Error: {msg}")

    win._on_error = _on_error

    # ── Cache invalidation helpers ──────────────────────────────────────────
    def _on_upload_done(remote_folder: str) -> None:
        if not win._poller:
            return
        folder = remote_folder.rstrip("/")

        if "." in pathlib.Path(folder).name:
            folder = "/".join(folder.split("/")[:-1]) or "/"
        folder = folder or "/"
        from .remote_cache import cache as _cache

        _cache.invalidate("list", path=folder)
        win._poller.add("list", path=folder)
        win._poller.force_refresh("list", path=folder)
        win.files_tab.notify_upload_done(folder)

    win._on_upload_done = _on_upload_done

    def _on_share_created() -> None:
        if not win._poller:
            return
        from .remote_cache import cache as _cache

        _cache.invalidate_op("shares")
        win._poller.force_refresh("shares")

    win._on_share_created = _on_share_created

    # ── Storage capacity ────────────────────────────────────────────────────
    def _refresh_storage() -> None:

        if not ctx.client.has_api_key:
            return
        if ctx.storage_worker and ctx.storage_worker.isRunning():
            return
        w = StorageWorker(ctx.client)
        w.done.connect(win._on_storage_done)
        w.error.connect(win._on_storage_error)
        w.finished.connect(partial(setattr, ctx, "storage_worker", None))
        ctx.storage_worker = w
        w.start()

    win._refresh_storage = _refresh_storage

    def _on_storage_done(data: dict) -> None:
        from .utils import fmt_bytes

        available = data.get("availableBytes")
        text = "Unlimited" if available is None else f"{fmt_bytes(available)} free"
        win.titlebar.set_storage_text(text)

    win._on_storage_done = _on_storage_done

    def _on_storage_error(msg: str) -> None:
        pass  # keep last shown text

    win._on_storage_error = _on_storage_error

    # ── Shared pipeline wiring ─────────────────────────────────────────────
    # The single-file tab subscribes per job via UploadManager.subscribe();
    # mass/sync tabs route the global job signals through their own job maps.
