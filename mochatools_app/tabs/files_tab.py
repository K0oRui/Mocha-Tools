"""
tabs/files_tab.py — Remote file browser tab for MochaTools.

Allows navigating folders, creating folders, deleting, moving,
sharing files, and downloading files from the remote storage.

Cache strategy
──────────────
All file-listing and shares data flows through remote_cache.  On
navigation we:
  1. Serve stale data instantly (zero-flash) if the cache has it.
  2. Subscribe for updates so the poller's background fetch auto-
     refreshes the view when fresh data arrives.
  3. On delete we optimistically prune both the in-memory cache
     entry AND the remote_cache store, then let the background
     poller confirm asynchronously.
"""

import os

import requests

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..constants import HARDCODED_BASE_URL
from ..dialogs import FolderBrowserDialog, ShareLinkDialog
from ..logging_utils import write_debug_log
from ..workers import FilesWorker, UploadWorker
from ..ui.icons import lucide_icon
from ..remote_cache import cache, registry


class FilesBrowserTab(QWidget):
    """
    The 'Files' tab — lists remote files and folders, allows:
      • Navigate folders (double-click or path bar)
      • Create folder
      • Delete file or folder
      • Move file or folder
      • Create / copy share link
      • Download file (direct or via browser)
    """

    def __init__(self, get_api_key, get_upload_path, set_upload_path, parent=None):
        super().__init__(parent)
        self.get_api_key     = get_api_key
        self.get_upload_path = get_upload_path
        self.set_upload_path = set_upload_path
        self.base_url        = HARDCODED_BASE_URL
        self.current_path    = "/"
        self._workers        = []
        self._shares_map     = {}
        # Legacy in-tab cache kept only as a fallback seed before the poller
        # delivers its first result; remote_cache is authoritative.
        self._shares_cache   = None
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        self._build_path_bar(outer)
        self._build_toolbar(outer)
        self._build_tree(outer)
        self._build_share_bar(outer)
        self._set_action_btns_enabled(False)

    def _build_path_bar(self, parent_lay: QVBoxLayout):
        path_row = QHBoxLayout()
        path_row.setSpacing(6)

        self.path_edit = QLineEdit("/")
        self.path_edit.setPlaceholderText("/path/to/folder")
        self.path_edit.returnPressed.connect(self._on_path_entered)

        go_btn = QPushButton("Go")
        go_btn.setObjectName("tb_btn")
        go_btn.setFixedWidth(40)
        go_btn.clicked.connect(self._on_path_entered)

        up_btn = QPushButton("↑")
        up_btn.setObjectName("tb_btn")
        up_btn.setFixedWidth(32)
        up_btn.setToolTip("Go up one level")
        up_btn.clicked.connect(self._go_up)

        path_row.addWidget(QLabel("Path:"))
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(go_btn)
        path_row.addWidget(up_btn)
        parent_lay.addLayout(path_row)

    def _build_toolbar(self, parent_lay: QVBoxLayout):
        tb = QHBoxLayout()
        tb.setSpacing(4)

        self.refresh_btn = self._tb("Refresh",    "refresh-cw", self._refresh)
        self.mkdir_btn   = self._tb("New Folder", "folder",     self._create_folder)
        self.rename_btn  = self._tb("Rename",     "pencil",     self._rename_selected)
        self.move_btn    = self._tb("Move",       "move",       self._move_selected)
        self.share_btn   = self._tb("Share",      "share-2",    self._share_selected)
        self.delete_btn  = self._tb("Delete",     "trash-2",    self._delete_selected, danger=True)

        for btn in (self.refresh_btn, self.mkdir_btn, self.rename_btn,
                    self.move_btn, self.share_btn, self.delete_btn):
            tb.addWidget(btn)
        tb.addStretch()

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color:#9ca3af; font-size:11px; background:transparent;")
        tb.addWidget(self.status_lbl)
        parent_lay.addLayout(tb)

    def _build_tree(self, parent_lay: QVBoxLayout):
        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(["Name", "Size", "Type", "Shared", "Expires"])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setRootIsDecorated(False)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)

        hdr = self.tree.header()
        for col, mode in enumerate([
            QHeaderView.ResizeMode.Stretch,
            QHeaderView.ResizeMode.ResizeToContents,
            QHeaderView.ResizeMode.ResizeToContents,
            QHeaderView.ResizeMode.ResizeToContents,
            QHeaderView.ResizeMode.ResizeToContents,
        ]):
            hdr.setSectionResizeMode(col, mode)

        parent_lay.addWidget(self.tree, 1)

    def _build_share_bar(self, parent_lay: QVBoxLayout):
        self.share_bar = QLabel("")
        self.share_bar.setObjectName("log_console")
        self.share_bar.setWordWrap(True)
        self.share_bar.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.TextSelectableByKeyboard |
            Qt.TextInteractionFlag.LinksAccessibleByMouse |
            Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
        self.share_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        self.share_bar.setOpenExternalLinks(True)
        self.share_bar.hide()
        parent_lay.addWidget(self.share_bar)

    def _tb(self, label: str, icon_name: str, slot, danger: bool = False) -> QPushButton:
        btn = QPushButton(f"  {label}")
        btn.setObjectName("tb_btn_danger" if danger else "tb_btn")
        btn.setIcon(lucide_icon(icon_name, "#f87171" if danger else "#9c9484", 13))
        btn.setIconSize(QSize(13, 13))
        btn.clicked.connect(slot)
        return btn

    # ── Cache subscriptions ───────────────────────────────────────────────────

    def attach_cache_poller(self, poller):
        """Called by app.py after the poller is created.  Subscribes callbacks."""
        self._poller = poller
        registry.subscribe("shares", self._on_shares_cache_update)

    def _on_list_cache_update(self, data):
        """Called by remote_cache registry when a 'list' result for current_path arrives."""
        self._populate(self.current_path, data)
        if self._shares_cache is not None:
            self._index_shares(self._shares_cache)
            self._refresh_share_indicators()
        self._status(
            f"{self.tree.topLevelItemCount()} items"
        )

    def _on_shares_cache_update(self, data):
        """Called by remote_cache registry when a fresh 'shares' result arrives."""
        self._shares_cache = data
        self._index_shares(data)
        self._refresh_share_indicators()

    # ── Navigation ────────────────────────────────────────────────────────────

    def _on_path_entered(self):
        self._navigate(self.path_edit.text().strip() or "/")

    def _go_up(self):
        parts  = self.current_path.strip("/").split("/")
        parent = "/" + "/".join(parts[:-1]) if len(parts) > 1 else "/"
        self._navigate(parent)

    def _navigate(self, path: str):
        api_key = self.get_api_key()
        if not api_key:
            self._status("⚠ Enter your API key in the Settings tab first.")
            return

        # Unsubscribe from the old path's list updates
        registry.unsubscribe("list", self._on_list_cache_update,
                              path=self.current_path)

        self.current_path = path
        self.path_edit.setText(path)
        self.share_bar.hide()
        write_debug_log(f"[DEBUG] _navigate: navigating to path={path!r}")

        # Subscribe to cache updates for the new path
        registry.subscribe("list", self._on_list_cache_update, path=path)

        # Ensure the poller is tracking this path
        if hasattr(self, "_poller"):
            self._poller.add("list", self.get_api_key, self.base_url, path=path)
            self._poller.start()

        # Serve stale data instantly if available
        stale = cache.get("list", path=path)
        if stale is not None:
            self._populate(path, stale)
            if self._shares_cache is not None:
                self._index_shares(self._shares_cache)
                self._refresh_share_indicators()
            self._status("Refreshing…")
        else:
            self.tree.clear()
            self._status("Loading…")

        # Delegate all fetching to the poller — it manages concurrency and
        # error handling centrally. force_refresh triggers an immediate poll
        # and the result arrives via _on_list_cache_update subscription.
        if hasattr(self, "_poller"):
            self._poller.force_refresh("list", path=path)

    def _refresh(self):
        # Force-invalidate this path so next poll fetches fresh
        cache.invalidate("list", path=self.current_path)
        self._navigate(self.current_path)

    # ── Called externally to warm cache after an upload ───────────────────────

    def notify_upload_done(self, remote_folder: str):
        """
        Called by app.py / mass_upload_tab when a file finishes uploading.
        Invalidates the cache for the affected folder and re-fetches.
        """
        # Normalise to the folder part (strip trailing filename if present)
        folder = remote_folder.rstrip("/")
        # If this is a file path rather than a folder, take parent
        # (heuristic: if it has an extension it's a file)
        if "." in os.path.basename(folder):
            folder = "/".join(folder.split("/")[:-1]) or "/"
        folder = folder or "/"

        cache.invalidate("list", path=folder)
        if hasattr(self, "_poller"):
            self._poller.force_refresh("list", path=folder)

        # If we're currently viewing this folder, re-populate from cache now
        if self.current_path == folder:
            stale = cache.get("list", path=folder)
            if stale is not None:
                self._populate(folder, stale)

    # ── Worker dispatch ───────────────────────────────────────────────────────

    def _run_worker(self, op: str, **kwargs):
        api_key = self.get_api_key()
        w = FilesWorker(op, api_key, self.base_url, **kwargs)
        w.done.connect(self._on_worker_done)
        w.error.connect(self._on_worker_error)
        w.finished.connect(lambda: self._workers.remove(w) if w in self._workers else None)
        self._workers.append(w)
        w.start()

    def _on_worker_done(self, result: dict):
        op = result.get("op")
        if op == "list":
            path = result["path"]
            data = result["data"]
            # Write into remote_cache so poller and other subscribers see it
            cache.set("list", data, path=path)
            registry.notify("list", data, path=path)
        elif op == "shares":
            data = result["data"]
            cache.set("shares", data)
            registry.notify("shares", data)
        elif op in ("delete", "delete_folder"):
            self._status("✓ Done")
            # Invalidate + re-fetch via poller; view already shows optimistic state
            cache.invalidate("list", path=self.current_path)
            if hasattr(self, "_poller"):
                self._poller.force_refresh("list", path=self.current_path)
        elif op in ("move", "mkdir", "rename"):
            self._status("✓ Done")
            cache.invalidate("list", path=self.current_path)
            self._refresh()
        elif op == "share":
            url   = result.get("url", "")
            token = result.get("token", "")
            self._status("✓ Share created")
            if url:
                ShareLinkDialog(url, parent=self).exec()
            # Optimistically add the new share into the shares cache so the
            # indicator updates instantly without blanking the file list.
            if token:
                new_share = {
                    "token":    token,
                    "is_active": True,
                    "isActive":  True,
                }
                # Find the selected file's metadata so we can tag fileId/fileName
                sel = self._selected_items()
                if sel:
                    meta = sel[0].data(0, Qt.ItemDataRole.UserRole) or {}
                    fid  = meta.get("id") or meta.get("fileId") or ""
                    name = meta.get("name") or meta.get("file_name") or ""
                    if fid:
                        new_share["fileId"] = fid
                    if name:
                        new_share["originalName"] = name
                        new_share["fileName"]     = name
                # Splice the new share into both the remote_cache store and the
                # local shares map so _refresh_share_indicators works immediately.
                existing = cache.get("shares")
                if existing is not None:
                    shares_list = (
                        existing.get("shares", existing)
                        if isinstance(existing, dict) else existing
                    )
                    if isinstance(shares_list, list):
                        shares_list = [s for s in shares_list
                                       if s.get("token") != token] + [new_share]
                    updated = (
                        {**existing, "shares": shares_list}
                        if isinstance(existing, dict) else shares_list
                    )
                    cache.set("shares", updated)
                    registry.notify("shares", updated)
                # Re-index and repaint share indicators in-place — no tree wipe
                if self._shares_cache is not None:
                    self._index_shares(self._shares_cache)
                    self._refresh_share_indicators()
            # Background-refresh shares to get full server state
            cache.invalidate_op("shares")
            if hasattr(self, "_poller"):
                self._poller.force_refresh("shares")

    def _on_worker_error(self, msg: str):
        self._status(f"✗ {msg}")
        QMessageBox.warning(self, "Error", msg)

    # ── Tree population ───────────────────────────────────────────────────────

    def _populate(self, path: str, data):
        # Remember which items were selected so we can restore them after
        # the tree rebuild (background refreshes shouldn't steal focus).
        selected_keys = set()
        for item in self.tree.selectedItems():
            meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
            key = meta.get("id") or meta.get("path") or meta.get("name")
            if key:
                selected_keys.add(key)

        self.tree.blockSignals(True)
        self.tree.setSortingEnabled(False)
        self.tree.clear()

        folders, files = self._parse_listing(path, data)

        if path and path != "/":
            up_item = QTreeWidgetItem(["↑  ..", "", "folder", "", ""])
            up_item.setData(0, Qt.ItemDataRole.UserRole,
                            {"_type": "up", "path": self._parent_path(path)})
            up_item.setForeground(0, QColor("#9ca3af"))
            self.tree.addTopLevelItem(up_item)

        for f in sorted(folders, key=lambda x: x["name"].lower()):
            item = QTreeWidgetItem([f"📁  {f['name']}", "", "folder", "", ""])
            item.setData(0, Qt.ItemDataRole.UserRole, {"_type": "folder", **f})
            item.setForeground(0, QColor("#c8a96e"))
            self.tree.addTopLevelItem(item)

        for f in sorted(files, key=lambda x: (
                x.get("originalName") or x.get("original_name") or
                x.get("name") or x.get("file_name") or "").lower()):
            stored_name = f.get("file_name") or f.get("name") or ""
            name        = f.get("originalName") or f.get("original_name") or f.get("name") or stored_name
            size        = f.get("size") or f.get("fileSize") or 0
            fid         = f.get("id") or f.get("fileId") or ""
            expires     = f.get("expiresAt") or f.get("expiry") or "—"
            if expires and expires != "—":
                expires = expires[:10] if len(expires) > 10 else expires
            item = QTreeWidgetItem([
                f"  {name}",
                UploadWorker._fmt_size(int(size)) if size else "—",
                "file", "", expires,
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, {
                **f, "_type": "file", "name": name, "id": fid,
                "file_name": stored_name,
                "path": f.get("path") or f"{path.rstrip('/')}/{stored_name or name}",
            })
            self.tree.addTopLevelItem(item)

        self.tree.setSortingEnabled(True)
        self.tree.blockSignals(False)

        # Restore previous selection if those items still exist in the new listing
        if selected_keys:
            root = self.tree.invisibleRootItem()
            for i in range(root.childCount()):
                item = root.child(i)
                meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
                key = meta.get("id") or meta.get("path") or meta.get("name")
                if key in selected_keys:
                    item.setSelected(True)

        self._status(
            f"{len(folders)} folder{'s' if len(folders) != 1 else ''}, "
            f"{len(files)} file{'s' if len(files) != 1 else ''}"
        )
        # Fire selection-changed once to sync toolbar button states
        self._on_selection_changed()
        self._refresh_share_indicators()

    def _parse_listing(self, path: str, data) -> tuple[list, list]:
        """Normalise the API listing into (folders, files) lists."""
        if isinstance(data, dict):
            raw_folders = data.get("folders") or []
            raw_files   = data.get("files")   or []
        elif isinstance(data, list):
            raw_files, raw_folders = data, []
        else:
            return [], []

        write_debug_log(f"[DEBUG] _populate: path={path!r}, raw_folders={raw_folders}")

        folders: list[dict] = []
        for entry in raw_folders:
            if isinstance(entry, str):
                name = entry.rstrip("/").split("/")[-1]
                fullpath = entry if entry.startswith("/") else (
                    (path.rstrip("/") + "/" + name) if path != "/" else ("/" + name)
                )
                write_debug_log(f"[DEBUG]   String folder: {entry!r} -> {fullpath!r}")
                folders.append({"name": name, "path": fullpath})
            elif isinstance(entry, dict):
                entry_path = entry.get("path")
                name = (entry.get("name") or entry.get("originalName") or
                        (entry_path.rstrip("/").split("/")[-1] if entry_path else ""))
                fullpath = entry_path if (entry_path and entry_path.startswith("/")) else (
                    f"{path.rstrip('/')}/{name}" if path != "/" else f"/{name}"
                )
                write_debug_log(
                    f"[DEBUG]   Dict folder: name={name!r}, entry.path={entry_path!r}, "
                    f"current_path={path!r}, computed fullpath={fullpath!r}"
                )
                folders.append({**entry, "_type": "folder", "name": name, "path": fullpath})

        files: list[dict] = []
        for entry in raw_files:
            if not isinstance(entry, dict):
                continue
            if entry.get("type") == "folder" or entry.get("isFolder"):
                entry_path = entry.get("path")
                name       = entry.get("name") or (entry_path.rstrip("/").split("/")[-1] if entry_path else "")
                fullpath   = entry_path if (entry_path and entry_path.startswith("/")) else (
                    f"{path.rstrip('/')}/{name}" if path != "/" else f"/{name}"
                )
                folders.append({**entry, "name": name, "path": fullpath})
            else:
                files.append(entry)

        return folders, files

    # ── Share indicators ──────────────────────────────────────────────────────

    def _index_shares(self, data):
        self._shares_map = {}
        items = data if isinstance(data, list) else data.get("shares", [])
        for s in items:
            fid       = (s.get("fileId") or (s.get("file") or {}).get("id") or "")
            file_name = s.get("fileName") or s.get("file_name") or ""
            token     = s.get("token", "")
            share = {
                "url":     f"{self.base_url}/share/{token}" if token else "",
                "token":   token,
                "expires": s.get("expiresAt") or s.get("expires_at") or s.get("expiry") or "—",
                "active":  s.get("active", s.get("is_active", True)),
            }
            for key in (fid, file_name):
                if key:
                    self._shares_map[key] = share

    def _refresh_share_indicators(self):
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            item = root.child(i)
            meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
            if meta.get("_type") != "file":
                continue
            fid       = meta.get("id") or meta.get("fileId") or ""
            file_name = meta.get("file_name") or meta.get("name") or ""
            share     = self._shares_map.get(fid) or self._shares_map.get(file_name)
            if share:
                label = "● Shared" if share.get("active", True) else "○ Inactive"
                color = "#4ade80"  if share.get("active", True) else "#9ca3af"
                item.setText(3, label)
                item.setForeground(3, QColor(color))
                if item.text(4) in ("—", ""):
                    exp = share.get("expires", "—")
                    if exp and exp != "—":
                        item.setText(4, exp[:10] if len(exp) > 10 else exp)
            else:
                item.setText(3, "")

    # ── Selection helpers ─────────────────────────────────────────────────────

    def _on_selection_changed(self):
        items       = self._selected_items()
        has         = len(items) > 0
        single      = len(items) == 1
        single_file   = single and items[0].data(0, Qt.ItemDataRole.UserRole).get("_type") == "file"
        single_folder = single and items[0].data(0, Qt.ItemDataRole.UserRole).get("_type") == "folder"
        self.rename_btn.setEnabled(single_folder)
        self.move_btn.setEnabled(single)
        self.share_btn.setEnabled(single_file)
        self.delete_btn.setEnabled(has)

    def _set_action_btns_enabled(self, enabled: bool):
        self.rename_btn.setEnabled(enabled)
        self.move_btn.setEnabled(enabled)
        self.share_btn.setEnabled(enabled)
        self.delete_btn.setEnabled(enabled)

    def _selected_items(self) -> list[QTreeWidgetItem]:
        return [i for i in self.tree.selectedItems()
                if (i.data(0, Qt.ItemDataRole.UserRole) or {}).get("_type") in ("file", "folder")]

    def _on_double_click(self, item: QTreeWidgetItem, _col):
        meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if meta.get("_type") in ("folder", "up"):
            self._navigate(meta["path"])

    # ── Actions ───────────────────────────────────────────────────────────────

    def _create_folder(self):
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:")
        if not ok or not name.strip():
            return
        path = f"{self.current_path.rstrip('/')}/{name.strip()}"
        self._status(f"Creating {path}…")
        self._run_worker("mkdir", path=path)

    def _rename_selected(self):
        items = self._selected_items()
        if len(items) != 1:
            return
        meta = items[0].data(0, Qt.ItemDataRole.UserRole) or {}
        if meta.get("_type") != "folder":
            return

        folder_path = meta.get("path", "").rstrip("/")
        old_name    = folder_path.split("/")[-1]
        parent_path = "/".join(folder_path.split("/")[:-1]) or "/"

        if not old_name:
            QMessageBox.warning(self, "Rename", "Cannot determine the current folder name.")
            return

        new_name, ok = QInputDialog.getText(
            self, "Rename Folder", f"New name for {old_name!r}:", text=old_name
        )
        if not ok or not new_name.strip() or new_name.strip() == old_name:
            return

        new_name = new_name.strip()
        self._status(f"Renaming {old_name!r} → {new_name!r}…")
        self._run_worker(
            "rename",
            path=parent_path,
            old_name=old_name,
            new_name=new_name,
        )

    def _delete_selected(self):
        items = self._selected_items()
        if not items:
            return
        names = [item.text(0).strip().lstrip("📁").lstrip() for item in items]
        msg   = (f"Delete {names[0]!r}?" if len(names) == 1 else f"Delete {len(names)} items?")
        if QMessageBox.question(
            self, "Confirm Delete", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return

        # Optimistic removal from tree
        for tree_item in list(items):
            idx = self.tree.indexOfTopLevelItem(tree_item)
            if idx >= 0:
                self.tree.takeTopLevelItem(idx)

        # Prune both the in-tab convenience copy AND the remote_cache store
        self._prune_cache(items)

        for tree_item in items:
            meta = tree_item.data(0, Qt.ItemDataRole.UserRole) or {}
            if meta.get("_type") == "folder":
                self._run_worker("delete_folder", path=meta.get("path", ""))
            else:
                file_name = meta.get("file_name") or meta.get("name") or meta.get("path", "").lstrip("/")
                self._run_worker("delete", file_name=file_name)
        self._status("Deleting…")

    def _prune_cache(self, tree_items: list[QTreeWidgetItem]):
        """Remove deleted items from remote_cache so instant re-renders look correct."""
        deleted_names, deleted_paths = set(), set()
        for ti in tree_items:
            meta = ti.data(0, Qt.ItemDataRole.UserRole) or {}
            fn = meta.get("file_name") or meta.get("name") or ""
            fp = meta.get("path") or ""
            if fn: deleted_names.add(fn)
            if fp: deleted_paths.add(fp)

        def _keep_file(f):
            if not isinstance(f, dict):
                return str(f) not in deleted_names and str(f) not in deleted_paths
            fn = f.get("file_name") or f.get("originalName") or f.get("name") or ""
            fp = f.get("path") or ""
            return fn not in deleted_names and fp not in deleted_paths

        def _keep_folder(f):
            if not isinstance(f, dict):
                return str(f) not in deleted_names and str(f) not in deleted_paths
            return f.get("path", "") not in deleted_paths and f.get("name", "") not in deleted_names

        cached = cache.get("list", path=self.current_path)
        if cached is None:
            return
        if isinstance(cached, dict):
            pruned = {
                **cached,
                "files":   [f for f in cached.get("files", [])   if _keep_file(f)],
                "folders": [f for f in cached.get("folders", []) if _keep_folder(f)],
            }
        elif isinstance(cached, list):
            pruned = [f for f in cached if _keep_file(f)]
        else:
            return
        cache.set("list", pruned, path=self.current_path)

    def _move_selected(self):
        items = self._selected_items()
        if len(items) != 1:
            return
        meta      = items[0].data(0, Qt.ItemDataRole.UserRole) or {}
        is_folder = meta.get("_type") == "folder"
        fid       = meta.get("id") or meta.get("fileId") or ""
        src       = meta.get("path") or meta.get("name") or ""
        if is_folder and src and not src.endswith("/"):
            src += "/"

        dlg = FolderBrowserDialog(self.get_api_key(), self.base_url, self.current_path, parent=self)
        dlg.setWindowTitle("Move — choose destination folder")
        if not dlg.exec():
            return
        dest_folder = dlg.selected.rstrip("/") + "/"
        self._status(f"Moving to {dest_folder}…")
        self._run_worker("move", file_id=fid, source_path=src,
                         new_path=dest_folder, is_folder=is_folder)

    def _share_selected(self):
        items = self._selected_items()
        if len(items) != 1:
            return
        meta = items[0].data(0, Qt.ItemDataRole.UserRole) or {}
        fid  = meta.get("id") or meta.get("fileId") or ""
        name = meta.get("name") or ""

        if fid in self._shares_map:
            existing_url = self._shares_map[fid].get("url", "")
            ans = QMessageBox.question(
                self, "Already Shared",
                f"{name!r} already has a share link.\n\n{existing_url}\n\nCreate a new link anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
                QMessageBox.StandardButton.Cancel,
            )
            if ans == QMessageBox.StandardButton.No:
                self.share_bar.setText(
                    f'Share link: <a href="{existing_url}" style="color:#c8a96e;">'
                    f'{existing_url}</a>'
                )
                self.share_bar.show()
                return
            elif ans == QMessageBox.StandardButton.Cancel:
                return

        expiry, ok = QInputDialog.getItem(
            self, "Share Expiry", "Expiration:",
            ["Never", "1h", "6h", "12h", "1d", "3d", "7d", "14d", "30d"],
            editable=False,
        )
        if not ok:
            return
        self._status(f"Creating share for {name!r}…")
        self._run_worker("share", file_id=fid, expiry=expiry)

    def _download_selected(self):
        items = self._selected_items()
        if len(items) != 1:
            return
        meta = items[0].data(0, Qt.ItemDataRole.UserRole) or {}
        fid  = meta.get("id") or meta.get("fileId") or ""
        name = meta.get("name") or meta.get("file_name") or meta.get("original_name") or "download"
        if not fid:
            QMessageBox.warning(self, "Download", "Cannot determine file ID.")
            return

        api_key = self.get_api_key()
        try:
            resp = requests.get(
                f"{self.base_url}/api/files/presigned",
                headers={"Authorization": f"Bearer {api_key}"},
                params={"fileId": fid},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            url  = data.get("url") or data.get("presignedUrl") or data.get("downloadUrl") or ""
            if not url:
                QMessageBox.warning(self, "Download", f"No download URL returned: {data}")
                return
        except Exception as e:
            QMessageBox.warning(self, "Download", f"Failed to get download URL: {e}")
            return

        main_win    = self.window()
        use_browser = getattr(main_win, "browser_download_cb", None)
        if use_browser is not None and use_browser.isChecked():
            import webbrowser
            webbrowser.open(url)
            return

        dest_dir = QFileDialog.getExistingDirectory(self, "Save download to…")
        if not dest_dir:
            return
        dest_path = os.path.join(dest_dir, name)
        self._status(f"Downloading {name}…")

        from ..workers import DownloadWorker
        if not hasattr(self, "_dl_workers"):
            self._dl_workers = []
        w = DownloadWorker(url, dest_path)

        def _on_done(path, _w=w):
            self._status(f"✓ Saved to {path}")
            QMessageBox.information(self, "Download complete", f"Saved to:\n{path}")
            if _w in self._dl_workers:
                self._dl_workers.remove(_w)

        def _on_err(msg, _w=w):
            self._status(f"✗ Download failed: {msg}")
            QMessageBox.warning(self, "Download failed", msg)
            if _w in self._dl_workers:
                self._dl_workers.remove(_w)

        w.done.connect(_on_done)
        w.error.connect(_on_err)
        w.speed.connect(lambda bps: self._status(
            f"Downloading {name}… {bps/1024/1024:.1f} MB/s" if bps >= 1024 * 1024
            else f"Downloading {name}… {bps/1024:.0f} KB/s"
        ))
        self._dl_workers.append(w)
        w.start()

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if meta.get("_type") not in ("file", "folder"):
            return
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background:#1f1f1f; border:1px solid #3a3a3a; border-radius:8px;
                    color:#f0f0f0; font-size:12px; }
            QMenu::item { padding:6px 24px; }
            QMenu::item:selected { background:#332b1a; }
        """)
        if meta.get("_type") == "file":
            menu.addAction("⬇  Download", self._download_selected)
            menu.addAction("⤴  Share",    self._share_selected)
        if meta.get("_type") == "folder":
            menu.addAction("✎  Rename", self._rename_selected)
        menu.addAction("↦  Move",   self._move_selected)
        menu.addSeparator()
        menu.addAction("✕  Delete", self._delete_selected)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _status(self, msg: str):
        self.status_lbl.setText(msg)

    @staticmethod
    def _parent_path(path: str) -> str:
        parts = path.strip("/").split("/")
        return "/" + "/".join(parts[:-1]) if len(parts) > 1 else "/"