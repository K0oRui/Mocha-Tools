"""
tabs/shares_tab.py — Active shares browser tab for MochaTools.

Lists all active shares with copy-link, toggle-active, and delete actions.

Cache strategy
──────────────
Shares data flows through remote_cache.  On tab activation we serve
stale data instantly, subscribe for updates, and let the poller deliver
fresh data in the background.  Deletes are applied optimistically to
both the tree and the cache store before the worker confirms.
"""

import requests as _req

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QHBoxLayout, QLabel, QMenu,
    QMessageBox, QPushButton, QSizePolicy, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from ..constants import HARDCODED_BASE_URL, SHARE_BASE_URL
from ..workers import FilesWorker
from ..ui.icons import lucide_icon
from ..remote_cache import cache, registry


class SharesTab(QWidget):
    """Lists all active shares with copy-link and delete actions."""

    def __init__(self, get_api_key, parent=None):
        super().__init__(parent)
        self.get_api_key = get_api_key
        self.base_url    = HARDCODED_BASE_URL
        self._workers    = []
        self._cache      = None   # last known data (also mirrored in remote_cache)
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        self._build_toolbar(outer)
        self._build_tree(outer)
        self._build_copy_bar(outer)

    def _build_toolbar(self, parent_lay: QVBoxLayout):
        tb = QHBoxLayout()
        tb.setSpacing(4)

        from ..theme import get_accent, notifier, accent_qcolor
        self.refresh_btn = self._tb("  Refresh",      "refresh-cw", get_accent(), self.refresh)
        self.copy_btn    = self._tb("  Copy Link",    "copy",       get_accent(), self._copy_selected)
        self.toggle_btn  = self._tb("  Toggle Active","link",       get_accent(), self._toggle_selected)
        self.delete_btn  = self._tb("  Delete",       "trash-2",    "#f87171", self._delete_selected,
                                    danger=True)

        self.copy_btn.setEnabled(False)
        self.toggle_btn.setEnabled(False)
        self.delete_btn.setEnabled(False)

        for btn in (self.refresh_btn, self.copy_btn, self.toggle_btn, self.delete_btn):
            tb.addWidget(btn)
        tb.addStretch()

        from ..theme import get_font
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet(f"color:{accent_qcolor().name()}; font-size:{int(get_font()[1])}px; background:transparent;")
        tb.addWidget(self.status_lbl)
        parent_lay.addLayout(tb)
        try:
            notifier().accent_changed.connect(lambda _old, _new: self._on_accent_changed(_old, _new))
        except Exception:
            pass

    def _on_accent_changed(self, old, new):
        try:
            from ..theme import get_accent, accent_qcolor
            self.refresh_btn.setIcon(lucide_icon("refresh-cw", get_accent(), 13))
            self.copy_btn.setIcon(lucide_icon("copy", get_accent(), 13))
            self.toggle_btn.setIcon(lucide_icon("link", get_accent(), 13))
            from ..theme import get_font
            self.status_lbl.setStyleSheet(f"color:{accent_qcolor().name()}; font-size:{int(get_font()[1])}px; background:transparent;")
        except Exception:
            pass

    def _build_tree(self, parent_lay: QVBoxLayout):
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["File", "Share Link", "Active", "Expires"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)

        from PyQt6.QtWidgets import QHeaderView
        hdr = self.tree.header()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(True)
        hdr.resizeSection(0, 220)   # File
        hdr.resizeSection(1, 320)   # Share Link
        hdr.resizeSection(2, 90)    # Active
        hdr.resizeSection(3, 100)   # Expires
        parent_lay.addWidget(self.tree, 1)

    def _build_copy_bar(self, parent_lay: QVBoxLayout):
        self.copy_bar = QLabel("")
        self.copy_bar.setObjectName("log_console")
        self.copy_bar.setWordWrap(True)
        self.copy_bar.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.copy_bar.setOpenExternalLinks(True)
        self.copy_bar.hide()
        parent_lay.addWidget(self.copy_bar)

    def _tb(self, label: str, icon_name: str, color: str, slot,
            danger: bool = False) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName("tb_btn_danger" if danger else "tb_btn")
        btn.setIcon(lucide_icon(icon_name, color, 13))
        btn.setIconSize(QSize(13, 13))
        btn.clicked.connect(slot)
        return btn

    # ── Cache subscription ────────────────────────────────────────────────────

    def attach_cache_poller(self, poller):
        """Called by app.py once the poller exists.  Subscribes for push updates."""
        self._poller = poller
        registry.subscribe("shares", self._on_shares_cache_update)

    def _on_shares_cache_update(self, data):
        """Receives fresh shares data from remote_cache registry (background poll)."""
        self._cache = data
        self._render(data)

    # ── Data ──────────────────────────────────────────────────────────────────

    def refresh(self):
        api_key = self.get_api_key()
        if not api_key:
            self._status("⚠ Enter your API key in Settings first.")
            return
        self.copy_bar.hide()

        # Serve stale data instantly; background fetch will update
        stale = cache.get("shares")
        if stale is not None:
            self._cache = stale
            self._render(stale)
            self._status("Refreshing…")
        else:
            self.tree.clear()
            self._status("Loading…")

        # Delegate to the poller so all shares fetches go through one code path
        # with consistent error handling (silent, status-bar only).
        # Falls back to a direct worker only if the poller isn't wired up yet.
        if hasattr(self, "_poller"):
            cache.invalidate_op("shares")
            self._poller.force_refresh("shares")
        else:
            w = FilesWorker("shares", api_key, self.base_url)
            w.done.connect(self._on_done)
            w.error.connect(lambda msg: self._status(f"✗ {msg}"))
            w.finished.connect(lambda: self._workers.remove(w) if w in self._workers else None)
            self._workers.append(w)
            w.start()

    def _render(self, data):
        shares = data.get("shares", data) if isinstance(data, dict) else data

        # Preserve current selection (by token) across rebuild
        selected_tokens = {
            meta["token"]
            for meta in self._selected_meta()
            if meta and meta.get("token")
        }

        self.tree.setSortingEnabled(False)
        self.tree.clear()

        restored_items = []

        for s in shares:
            token     = s.get("token", "")
            file_name = (
                s.get("originalName") or s.get("original_name") or s.get("name")
                or s.get("fileName") or s.get("file_name") or token
            )
            is_active = s.get("is_active", s.get("isActive", True))
            expires   = s.get("expires_at") or s.get("expiresAt") or s.get("expiry") or "Never"
            if expires and expires != "Never" and len(expires) > 10:
                expires = expires[:10]
            url = f"{SHARE_BASE_URL}/share/{token}" if token else ""

            active_text  = "● Active"  if is_active else "○ Inactive"
            active_color = "#4ade80"   if is_active else "#9ca3af"

            item = QTreeWidgetItem([file_name, url, active_text, expires])
            item.setData(0, Qt.ItemDataRole.UserRole, {
                "token": token, "url": url, "is_active": is_active, "file_name": file_name,
            })
            item.setForeground(2, QColor(active_color))
            item.setForeground(1, QColor("#9ca3af"))
            self.tree.addTopLevelItem(item)

            if token and token in selected_tokens:
                restored_items.append(item)

        if restored_items:
            # Block signals while restoring so we don't spam selection-changed
            self.tree.blockSignals(True)
            for item in restored_items:
                item.setSelected(True)
            self.tree.blockSignals(False)
            self._on_selection_changed()

        self.tree.setSortingEnabled(True)
        count = self.tree.topLevelItemCount()
        self._status(f"{count} share{'s' if count != 1 else ''}")

    def _on_done(self, result: dict):
        if result.get("op") != "shares":
            return
        data = result["data"]
        # Write into remote_cache and notify all subscribers (e.g. files_tab)
        cache.set("shares", data)
        registry.notify("shares", data)
        self._cache = data
        self._render(data)

    def _on_error(self, msg: str):
        self._status(f"✗ {msg}")
        QMessageBox.warning(self, "Error", msg)

    # ── Selection ─────────────────────────────────────────────────────────────

    def _on_selection_changed(self):
        has = len(self.tree.selectedItems()) > 0
        self.copy_btn.setEnabled(has)
        self.toggle_btn.setEnabled(has)
        self.delete_btn.setEnabled(has)

    def _selected_meta(self) -> list[dict]:
        return [item.data(0, Qt.ItemDataRole.UserRole) for item in self.tree.selectedItems()]

    # ── Actions ───────────────────────────────────────────────────────────────

    def _copy_selected(self):
        items = self._selected_meta()
        if not items:
            return
        if len(items) == 1:
            url = items[0]["url"]
            QApplication.clipboard().setText(url)
            self.copy_bar.setText(f'Copied: <a href="{url}" style="color:#e11d48;">{url}</a>')
        else:
            urls = "\n".join(m["url"] for m in items)
            QApplication.clipboard().setText(urls)
            self.copy_bar.setText(f"Copied {len(items)} links to clipboard.")
        self.copy_bar.show()

    def _toggle_selected(self):
        api_key = self.get_api_key()
        for meta in self._selected_meta():
            token      = meta["token"]
            new_active = not meta["is_active"]
            try:
                resp = _req.patch(
                    f"{self.base_url}/api/shares/{token}",
                    headers={"Authorization": f"Bearer {api_key}",
                             "Content-Type": "application/json"},
                    json={"isActive": new_active},
                    timeout=15,
                )
                resp.raise_for_status()
            except Exception as e:
                QMessageBox.warning(self, "Error", str(e))
                return
        # Invalidate shares cache; the poller will re-fetch and notify
        cache.invalidate_op("shares")
        if hasattr(self, "_poller"):
            self._poller.force_refresh("shares")
        else:
            self.refresh()

    def _delete_selected(self):
        items = self._selected_meta()
        if not items:
            return
        msg = (
            f"Delete share for {items[0]['file_name']!r}?"
            if len(items) == 1
            else f"Delete {len(items)} shares?"
        )
        if QMessageBox.question(
            self, "Confirm Delete", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return

        api_key        = self.get_api_key()
        deleted_tokens = set()
        for meta in items:
            try:
                resp = _req.delete(
                    f"{self.base_url}/api/shares/{meta['token']}",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=15,
                )
                resp.raise_for_status()
                deleted_tokens.add(meta["token"])
            except Exception as e:
                QMessageBox.warning(self, "Error", str(e))
                return

        self.copy_bar.hide()

        # Optimistic removal from tree
        for tree_item in self.tree.selectedItems():
            meta = tree_item.data(0, Qt.ItemDataRole.UserRole) or {}
            if meta.get("token") in deleted_tokens:
                idx = self.tree.indexOfTopLevelItem(tree_item)
                if idx >= 0:
                    self.tree.takeTopLevelItem(idx)

        # Prune both local _cache and remote_cache store
        self._prune_cache(deleted_tokens)

        count = self.tree.topLevelItemCount()
        self._status(f"{count} share{'s' if count != 1 else ''}")

        # Background re-fetch to confirm server state
        cache.invalidate_op("shares")
        if hasattr(self, "_poller"):
            self._poller.force_refresh("shares")
        else:
            self.refresh()

    def _prune_cache(self, deleted_tokens: set):
        """Remove deleted shares from remote_cache so instant re-renders are correct."""
        def _pruned(data):
            shares = data.get("shares", data) if isinstance(data, dict) else data
            kept   = [s for s in shares if s.get("token") not in deleted_tokens]
            if isinstance(data, dict):
                return {**data, "shares": kept}
            return kept

        # Prune in-tab cache
        if self._cache is not None:
            self._cache = _pruned(self._cache)

        # Prune remote_cache store
        cached = cache.get("shares")
        if cached is not None:
            cache.set("shares", _pruned(cached))

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background:#1f1f1f; border:1px solid #3a3a3a; border-radius:8px; color:#f0f0f0; font-size:12px; }"
            "QMenu::item { padding:6px 8px; }"
            "QMenu::item:selected { background:#332b1a; }"
        )
        from ..theme import get_accent
        a = menu.addAction(lucide_icon("copy", get_accent(), 12), "Copy Link")
        a.triggered.connect(self._copy_selected)
        a = menu.addAction(lucide_icon("share-2", get_accent(), 12), "Toggle Active")
        a.triggered.connect(self._toggle_selected)
        menu.addSeparator()
        a = menu.addAction(lucide_icon("trash-2", "#f87171", 12), "Delete")
        a.triggered.connect(self._delete_selected)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _status(self, msg: str):
        self.status_lbl.setText(msg)