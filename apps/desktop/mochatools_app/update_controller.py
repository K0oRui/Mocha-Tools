"""
update_controller.py — Update check, download, install, and restart logic.

Extracted from MochaTools in app.py. All functions receive a ``win``
instance and a ``ctx`` (AppContext) for shared state.

Public API
----------
  check_for_updates(win, ctx, silent=False)
  install_update(win, ctx)
  show_release_info(win, ctx)
  trigger_test_update(win, ctx)

Attached on ``win``:
  win._check_for_updates(silent)
  win._install_update()
  win._show_release_info()
  win._trigger_test_update()
"""

from PySide6.QtCore import QSettings, Qt, QThread
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
)

from .constants import APP_NAME, APP_VERSION, ORG_NAME
from .dialogs import MochaDialog
from .theme import get_accent
from .updater import UpdateCheckWorker, UpdateDownloadWorker, launch_update_batch
from .utils import parse_release_notes_md

# ── Release-info dialog (shared by startup popup + Settings button) ────────


def _build_release_info_dialog(win, tag: str, notes: str, with_buttons: bool = True):
    """
    Build the MochaDialog shared by both the startup "update available"
    popup and the Settings "Release Info" button.

    Returns (dlg, update_btn, skip_btn, later_btn) — the latter three
    are ``None`` when *with_buttons* is False.
    """
    whats_new_md = parse_release_notes_md(notes)

    update_btn = skip_btn = later_btn = None

    if with_buttons:
        _tmp_row = QHBoxLayout()
        _tmp_buttons = [
            QPushButton(t)
            for t in ("Update Now", "Skip This Version", "Remind Me Later")
        ]
        for b in _tmp_buttons:
            b.setMinimumHeight(32)
            _tmp_row.addWidget(b)
        btn_row_width = _tmp_row.sizeHint().width()
        for b in _tmp_buttons:
            b.deleteLater()
        dlg_width = max(460, btn_row_width + 28 * 2 + 8)
    else:
        dlg_width = 460

    dlg = MochaDialog("Update available", win, min_size=(dlg_width, 160))
    lay = dlg.content_layout
    grip_item = lay.takeAt(lay.count() - 1)

    header = QLabel(f"Mocha Tools {tag} is available (you have {APP_VERSION}).")
    header.setWordWrap(True)
    header.setStyleSheet("font-size: 14px; font-weight: 600; background: transparent;")
    lay.addWidget(header)

    if whats_new_md:
        body = QLabel()
        body.setTextFormat(Qt.TextFormat.MarkdownText)
        body.setWordWrap(True)
        body.setOpenExternalLinks(True)
        body.setText(f"**What's New**\n\n{whats_new_md}")
        body.setStyleSheet("background: transparent;")
        lay.addWidget(body)

    if with_buttons:
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        update_btn = QPushButton("Update Now")
        skip_btn = QPushButton("Skip This Version")
        later_btn = QPushButton("Remind Me Later")
        for b in (update_btn, skip_btn, later_btn):
            b.setMinimumHeight(32)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_row.addWidget(b)
        lay.addLayout(btn_row)

        try:
            acc = get_accent()
            update_btn.setStyleSheet(
                f"background: {acc}; color: #111010; font-weight: 700; "
                "border: none; border-radius: 6px; padding: 4px 16px;"
            )
            for b in (skip_btn, later_btn):
                b.setStyleSheet("border-radius: 6px; padding: 4px 16px;")
        except Exception:
            pass

    if grip_item:
        lay.addItem(grip_item)

    return dlg, update_btn, skip_btn, later_btn


def _show_update_available_popup(win, ctx, tag: str, notes: str):
    """Startup notification — lets the user update now, snooze, or skip."""
    dlg, update_btn, skip_btn, later_btn = _build_release_info_dialog(
        win,
        tag,
        notes,
        with_buttons=True,
    )

    result_holder = {"clicked": None}

    def _set_clicked(name):
        result_holder["clicked"] = name
        dlg.accept()

    update_btn.clicked.connect(lambda: _set_clicked("update"))
    skip_btn.clicked.connect(lambda: _set_clicked("skip"))
    later_btn.clicked.connect(lambda: _set_clicked("later"))

    dlg.exec()
    clicked = result_holder["clicked"]

    if clicked == "update":
        win.tabs.setCurrentIndex(5)
        _install_update(win, ctx)
    elif clicked == "skip":
        QSettings(ORG_NAME, APP_NAME).setValue("skip_update_tag", tag)


# ── Check ───────────────────────────────────────────────────────────────────


def check_for_updates(win, ctx, silent: bool = False):
    win.check_update_btn.setEnabled(False)
    win.update_status_lbl.setText("Checking for updates…")
    ctx.pending_silent_update_popup = silent

    def _on_available(tag: str, url: str, notes: str):
        ctx.update_tag = tag
        ctx.update_url = url
        ctx.update_notes = notes
        win.update_status_lbl.setText(
            f"Update available: {tag}  (current: {APP_VERSION})"
        )
        win.install_update_btn.setVisible(bool(url))
        win.release_info_btn.setVisible(bool(url))
        if not url:
            win.update_status_lbl.setText(
                f"Update {tag} available — no binary for this platform. "
                "Download manually from github.com/nxllxvxxd2/Mocha-Tools/releases"
            )
            return
        if ctx.pending_silent_update_popup:
            ctx.pending_silent_update_popup = False
            skipped = QSettings(ORG_NAME, APP_NAME).value("skip_update_tag", "")
            if skipped != tag:
                _show_update_available_popup(win, ctx, tag, notes)

    def _on_up_to_date(silent: bool = silent):
        try:
            from .updater import _is_portable_windows

            _portable_suffix = " (portable)" if _is_portable_windows() else ""
        except Exception:
            _portable_suffix = ""
        win.update_status_lbl.setText(
            f"You're up to date ({APP_VERSION}{_portable_suffix})"
        )
        win.install_update_btn.hide()
        win.release_info_btn.hide()
        if not silent:
            QMessageBox.information(
                win,
                "Up to date",
                f"Mocha Tools {APP_VERSION} is the latest version.",
            )

    def _on_error(msg: str, silent: bool = silent):
        win.update_status_lbl.setText(f"Update check failed: {msg}")
        if not silent:
            QMessageBox.warning(win, "Update check failed", msg)

    w = UpdateCheckWorker(win)
    w.update_available.connect(_on_available)
    w.up_to_date.connect(_on_up_to_date)
    w.error.connect(_on_error)
    w.finished.connect(lambda: win.check_update_btn.setEnabled(True))
    w.start()


# ── Download + install ──────────────────────────────────────────────────────


def _install_update(win, ctx):
    if not ctx.update_url:
        return
    win.install_update_btn.setEnabled(False)
    win.update_progress.setValue(0)
    win.update_progress.show()

    def _on_done():
        win.update_progress.setValue(100)
        win.install_update_btn.hide()
        win.release_info_btn.hide()
        QMessageBox.information(
            win,
            "Update installed",
            f"Mocha Tools {ctx.update_tag} has been installed.\n\n"
            "Please restart the application to apply the update.",
        )

    def _on_ready(bat_path: str):
        ctx.update_bat_path = bat_path
        win.update_progress.setValue(100)
        win.install_update_btn.hide()
        win.release_info_btn.hide()
        result = QMessageBox.question(
            win,
            "Restart required",
            f"Mocha Tools {ctx.update_tag} has been installed.\n\nRestart now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if result == QMessageBox.StandardButton.Yes:
            win.update_status_lbl.setText("Restarting…")
            launch_update_batch(ctx.update_bat_path)
            QApplication.quit()

    def _on_dl_error(msg: str):
        win.update_progress.hide()
        win.install_update_btn.setEnabled(True)
        win.update_status_lbl.setText(f"Download failed: {msg}")
        QMessageBox.warning(win, "Update failed", msg)

    w = UpdateDownloadWorker(ctx.update_url, ctx.update_tag)
    w.progress.connect(win.update_progress.setValue)
    w.status.connect(win.update_status_lbl.setText)
    w.done.connect(_on_done)
    w.ready_to_restart.connect(_on_ready)
    w.error.connect(_on_dl_error)
    w.start()
    ctx.update_dl_worker = w
    ctx.update_bat_path = ""


def _on_update_done(win, ctx):
    """Also callable from the ``done`` signal directly."""
    win.update_progress.setValue(100)
    win.install_update_btn.hide()
    win.release_info_btn.hide()
    QMessageBox.information(
        win,
        "Update installed",
        f"Mocha Tools {ctx.update_tag} has been installed.\n\n"
        "Please restart the application to apply the update.",
    )


def _on_update_dl_error(win, msg: str):
    win.update_progress.hide()
    win.install_update_btn.setEnabled(True)
    win.update_status_lbl.setText(f"Download failed: {msg}")
    QMessageBox.warning(win, "Update failed", msg)


# ── Release info (Settings button) ──────────────────────────────────────────


def show_release_info(win, ctx):
    if not ctx.update_tag:
        return
    dlg, *_rest = _build_release_info_dialog(
        win,
        ctx.update_tag,
        ctx.update_notes,
        with_buttons=False,
    )
    dlg.exec()


# ── Test-update (--test-update flag only) ───────────────────────────────────


def trigger_test_update(win, ctx):
    """Fetch latest release and download+install, skipping version check."""
    import requests as _req

    from .constants import UPDATE_CHECK_URL
    from .updater import _asset_name

    win.tabs.setCurrentIndex(5)
    win.update_status_lbl.setText("Test mode: fetching latest release info…")
    win.update_progress.setValue(0)
    win.update_progress.show()
    win.check_update_btn.setEnabled(False)
    win.install_update_btn.hide()
    win.release_info_btn.hide()

    def _fetch():
        try:
            resp = _req.get(
                UPDATE_CHECK_URL,
                headers={"Accept": "application/vnd.github+json"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            win.update_status_lbl.setText(f"Test-update fetch failed: {exc}")
            win.check_update_btn.setEnabled(True)
            return

        tag = data.get("tag_name", "")
        assets = data.get("assets", [])

        if not tag:
            win.update_status_lbl.setText("Test-update: release has no tag_name.")
            win.check_update_btn.setEnabled(True)
            return

        try:
            want = _asset_name(tag)
        except ValueError as exc:
            win.update_status_lbl.setText(f"Test-update asset name error: {exc}")
            win.check_update_btn.setEnabled(True)
            return

        url = next(
            (
                a.get("browser_download_url", "")
                for a in assets
                if a.get("name") == want
            ),
            "",
        )
        if not url:
            win.update_status_lbl.setText(
                f"Test-update: no asset '{want}' found in release {tag}.\n"
                "Check that the build for this platform uploaded successfully."
            )
            win.check_update_btn.setEnabled(True)
            return

        win.update_status_lbl.setText(
            f"Test mode: installing {tag} ({want}) - version check skipped"
        )
        ctx.update_tag = tag
        ctx.update_url = url

        w = UpdateDownloadWorker(url, tag)
        w.progress.connect(win.update_progress.setValue)
        w.status.connect(win.update_status_lbl.setText)
        w.done.connect(lambda: _on_update_done(win, ctx))
        w.ready_to_restart.connect(
            lambda bp: _on_update_done(win, ctx)  # same flow
        )
        w.error.connect(lambda msg: _on_update_dl_error(win, msg))
        w.start()
        ctx.update_dl_worker = w

    class _FetchThread(QThread):
        def run(self_):
            _fetch()

    ctx._test_fetch_thread = _FetchThread(win)
    ctx._test_fetch_thread.start()


# ── Attach convenience methods to win ───────────────────────────────────────


def install_update_controller(win, ctx):
    """Wire up all update-related methods and initial state on *win*."""
    ctx.update_tag = ""
    ctx.update_url = ""
    ctx.update_notes = ""
    ctx.update_bat_path = ""
    ctx.update_dl_worker = None
    ctx.pending_silent_update_popup = False

    win._check_for_updates = lambda silent=False: check_for_updates(win, ctx, silent)
    win._install_update = lambda: _install_update(win, ctx)
    win._show_release_info = lambda: show_release_info(win, ctx)
    win._trigger_test_update = lambda: trigger_test_update(win, ctx)
