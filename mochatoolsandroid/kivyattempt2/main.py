"""
Mocha Tools — Android (Kivy) — Attempt 2
Uses shared mochatools_core for upload/API logic; Kivy-only for UI.

Screens:
  1. Upload       — pick file, multipart upload, progress, share
  2. Files        — browse remote folders, delete, move, new folder
  3. Remote       — server-side URL ingest + job list
  4. Shares       — list and delete share links
  5. Settings     — API key (persisted), chunk config
"""

import os
import json
import time
import threading
import mimetypes
from typing import Optional

# ── Kivy config must be set BEFORE importing kivy.core ───────────────────────
import kivy
kivy.require("2.3.0")

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.core.clipboard import Clipboard
from kivy.metrics import dp
from kivy.storage.jsonstore import JsonStore
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

from kivymd.app import MDApp
from kivymd.uix.bottomnavigation import MDBottomNavigation, MDBottomNavigationItem
from kivymd.uix.button import MDFlatButton, MDRaisedButton, MDIconButton
from kivymd.uix.card import MDCard
from kivymd.uix.dialog import MDDialog
from kivymd.uix.label import MDLabel
from kivymd.uix.list import MDList, OneLineListItem, TwoLineListItem
from kivymd.uix.menu import MDDropdownMenu
from kivymd.uix.progressbar import MDProgressBar
from kivymd.uix.screen import MDScreen
from kivymd.uix.selectioncontrol import MDCheckbox
from kivymd.uix.snackbar import Snackbar
from kivymd.uix.textfield import MDTextField
from kivymd.uix.toolbar import MDTopAppBar

import requests

# ── Import shared core logic ──────────────────────────────────────────────────
from mochatools_core.constants import (
	HARDCODED_BASE_URL,
	DEFAULT_CHUNK_SIZE_MB,
	DEFAULT_MAX_CHUNKS,
)
from mochatools_core.api import api_get, api_post, api_delete
from mochatools_core.workers import UploadWorkerCore, ProgressTracker

# ── Constants ─────────────────────────────────────────────────────────────────
C_BG        = (17/255, 16/255, 16/255, 1)        # #111010
C_CARD      = (24/255, 22/255, 20/255, 1)        # #181614
C_SURFACE   = (30/255, 28/255, 25/255, 1)        # #1e1c19
C_ACCENT    = (200/255, 169/255, 110/255, 1)     # #c8a96e
C_TEXT      = (240/255, 236/255, 230/255, 1)     # #f0ece6
C_MUTED     = (156/255, 148/255, 132/255, 1)     # #9c9484
C_SUCCESS   = (74/255, 222/255, 128/255, 1)      # #4ade80
C_ERROR     = (248/255, 113/255, 113/255, 1)     # #f87171

EXPIRY_OPTIONS = ["Never", "1h", "6h", "12h", "1d", "3d", "7d", "14d", "30d"]
EXPIRY_LABEL_TO_HOURS = {
	"1h": 1, "6h": 6, "12h": 12,
	"1d": 24, "3d": 72, "7d": 168, "14d": 336, "30d": 720,
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def fmt_size(n: int) -> str:
	if n < 1024:
		return f"{n} B"
	elif n < 1024 ** 2:
		return f"{n/1024:.1f} KB"
	elif n < 1024 ** 3:
		return f"{n/1024**2:.2f} MB"
	return f"{n/1024**3:.2f} GB"


def toast(msg: str, duration: int = 3):
	Snackbar(text=msg, snackbar_x=dp(8), snackbar_y=dp(8),
			 size_hint_x=0.95, duration=duration).open()


def confirm_dialog(title: str, text: str, on_yes):
	"""Show a simple Yes/No dialog; calls on_yes() if confirmed."""
	dialog = MDDialog(
		title=title,
		text=text,
		buttons=[
			MDFlatButton(text="Cancel", on_release=lambda x: dialog.dismiss()),
			MDRaisedButton(text="Confirm", on_release=lambda x: (dialog.dismiss(), on_yes())),
		],
	)
	dialog.open()


def input_dialog(title: str, hint: str, on_ok, prefill: str = ""):
	"""One-line text input dialog."""
	content = MDTextField(hint_text=hint, text=prefill, size_hint_y=None, height=dp(48))
	dialog = MDDialog(
		title=title,
		type="custom",
		content_cls=content,
		buttons=[
			MDFlatButton(text="Cancel", on_release=lambda x: dialog.dismiss()),
			MDRaisedButton(text="OK", on_release=lambda x: (dialog.dismiss(), on_ok(content.text))),
		],
	)
	dialog.open()


# ═════════════════════════════════════════════════════════════════════════════
# SCREENS
# ═════════════════════════════════════════════════════════════════════════════

class UploadScreen(MDScreen):
	def __init__(self, app, **kwargs):
		super().__init__(**kwargs)
		self.app = app
		self._task = None
		self._file_path = None
		self._dest_path = "/"
		self._build_ui()

	def _build_ui(self):
		layout = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12))

		# File picker area
		pick_card = MDCard(
			orientation="vertical",
			padding=dp(16), spacing=dp(8),
			size_hint_y=None, height=dp(120),
			md_bg_color=C_CARD,
			elevation=4, radius=[12,12,12,12],
		)
		self._pick_label = MDLabel(
			text="No file selected",
			halign="center", theme_text_color="Custom",
			text_color=C_MUTED, font_style="Subtitle1",
		)
		pick_btn = MDRaisedButton(
			text="Choose File",
			md_bg_color=C_ACCENT,
			size_hint_x=None, width=dp(160),
			pos_hint={"center_x": 0.5},
			on_release=self._pick_file,
		)
		pick_card.add_widget(self._pick_label)
		pick_card.add_widget(pick_btn)
		layout.add_widget(pick_card)

		# Destination path
		self._dest_field = MDTextField(
			hint_text="Destination path on Mocha",
			text="/",
			size_hint_y=None, height=dp(48),
		)
		self._dest_field.bind(text=self._on_dest_changed)
		layout.add_widget(self._dest_field)

		# Share options
		share_row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(8))
		self._share_cb = MDCheckbox(size_hint_x=None, width=dp(40))
		share_row.add_widget(self._share_cb)
		share_row.add_widget(MDLabel(text="Create share link", theme_text_color="Custom", text_color=C_TEXT))
		self._expiry_label = MDLabel(text="Expiry: Never", size_hint_x=None, width=dp(120),
									 theme_text_color="Custom", text_color=C_MUTED)
		expiry_btn = MDFlatButton(text="▾", on_release=self._open_expiry_menu, size_hint_x=None, width=dp(40))
		share_row.add_widget(self._expiry_label)
		share_row.add_widget(expiry_btn)
		layout.add_widget(share_row)

		self._expiry = "Never"
		self._expiry_menu = MDDropdownMenu(
			items=[{"text": e, "on_release": lambda x, e=e: self._set_expiry(e)} for e in EXPIRY_OPTIONS],
			width_mult=3,
		)

		# Upload button
		self._upload_btn = MDRaisedButton(
			text="Upload",
			md_bg_color=C_ACCENT,
			size_hint_x=1, height=dp(48),
			on_release=self._start_upload,
		)
		layout.add_widget(self._upload_btn)

		# Progress
		self._progress_bar = MDProgressBar(value=0, size_hint_y=None, height=dp(6))
		layout.add_widget(self._progress_bar)

		prog_row = BoxLayout(size_hint_y=None, height=dp(24), spacing=dp(8))
		self._pct_label = MDLabel(text="", theme_text_color="Custom", text_color=C_MUTED, size_hint_x=None, width=dp(50))
		self._speed_label = MDLabel(text="", theme_text_color="Custom", text_color=C_MUTED)
		prog_row.add_widget(self._pct_label)
		prog_row.add_widget(self._speed_label)
		layout.add_widget(prog_row)

		# Status
		self._status_label = MDLabel(
			text="Ready",
			theme_text_color="Custom", text_color=C_MUTED,
			size_hint_y=None, height=dp(40),
			halign="center", font_style="Caption",
		)
		layout.add_widget(self._status_label)

		# Share result
		self._share_result = MDLabel(
			text="", theme_text_color="Custom", text_color=C_ACCENT,
			size_hint_y=None, height=dp(40),
			halign="center", font_style="Caption",
		)
		layout.add_widget(self._share_result)

		layout.add_widget(Widget())
		self.add_widget(layout)

	def _pick_file(self, *a):
		try:
			from plyer import filechooser
			filechooser.open_file(on_selection=self._on_file_chosen)
		except Exception as e:
			toast(f"File picker error: {e}")

	def _on_file_chosen(self, selection):
		if not selection:
			return
		path = selection[0]
		self._file_path = path
		display_name = os.path.basename(path)
		try:
			size = os.path.getsize(path)
		except Exception:
			size = 0
		self._pick_label.text = f"{display_name}  ({fmt_size(size)})"
		if self._dest_field.text.strip() in ("", "/"):
			self._dest_field.text = f"/{display_name}"

	def _on_dest_changed(self, instance, value):
		self._dest_path = value.strip() or "/"

	def _open_expiry_menu(self, btn):
		self._expiry_menu.caller = btn
		self._expiry_menu.open()

	def _set_expiry(self, val):
		self._expiry = val
		self._expiry_label.text = f"Expiry: {val}"
		self._expiry_menu.dismiss()

	def _start_upload(self, *a):
		api_key = self.app.api_key
		if not api_key:
			toast("Enter your API key in Settings first.")
			return
		if not self._file_path:
			toast("Select a file first.")
			return

		self._upload_btn.disabled = True
		self._progress_bar.value = 0
		self._share_result.text = ""

		stored = (self.app.store.get("settings").get("value", {}) if self.app.store.exists("settings") else {})
		chunk_mb = int(stored.get("chunk_mb", DEFAULT_CHUNK_SIZE_MB))
		max_chunks = int(stored.get("max_chunks", DEFAULT_MAX_CHUNKS))

		def on_progress(pct):
			Clock.schedule_once(lambda dt: self._on_progress(pct))

		def on_speed(bps):
			Clock.schedule_once(lambda dt: self._on_speed(bps))

		def on_status(msg):
			Clock.schedule_once(lambda dt: self._on_status(msg))

		def on_done(result):
			Clock.schedule_once(lambda dt: self._on_done(result))

		def on_error(msg):
			Clock.schedule_once(lambda dt: self._on_error(msg))

		worker = UploadWorkerCore(
			api_key=api_key,
			base_url=HARDCODED_BASE_URL,
			file_pairs=[(self._file_path, self._dest_path)],
			create_share=self._share_cb.active,
			share_expiry=EXPIRY_LABEL_TO_HOURS.get(self._expiry),
			share_max_downloads=0,
			chunk_size_mb=chunk_mb,
			max_chunks=max_chunks,
			on_progress=on_progress,
			on_speed=on_speed,
			on_status=on_status,
			on_done=on_done,
			on_error=on_error,
		)

		# Run in background thread
		self._task = threading.Thread(target=worker.run, daemon=True)
		self._task.start()

	def _on_progress(self, pct):
		self._progress_bar.value = pct
		self._pct_label.text = f"{pct}%"

	def _on_speed(self, bps):
		if bps < 1024:
			self._speed_label.text = f"{bps:.0f} B/s"
		elif bps < 1024**2:
			self._speed_label.text = f"{bps/1024:.1f} KB/s"
		else:
			self._speed_label.text = f"{bps/1024**2:.2f} MB/s"

	def _on_status(self, msg):
		if not msg.startswith("[DEBUG]"):
			self._status_label.text = msg

	def _on_done(self, result):
		self._upload_btn.disabled = False
		self._status_label.text = f"✓ Done! File ID: {result.get('file_id', 'unknown')}"
		if result.get("share_url"):
			self._share_result.text = result["share_url"]
			toast("Upload complete — share link ready!")
		else:
			toast("Upload complete!")

	def _on_error(self, msg):
		self._upload_btn.disabled = False
		self._status_label.text = f"✗ {msg}"
		toast(f"Upload failed: {msg}", duration=5)


# ── Files Screen ──────────────────────────────────────────────────────────────

class FilesScreen(MDScreen):
	def __init__(self, app, **kwargs):
		super().__init__(**kwargs)
		self.app = app
		self._current = "/"
		self._items_meta = {}
		self._build_ui()

	def _build_ui(self):
		layout = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(8))

		# Path bar
		path_row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
		self._path_field = MDTextField(
			text="/", hint_text="Path",
			size_hint_y=None, height=dp(48),
			size_hint_x=0.9
		)
		go_btn = MDIconButton(icon="arrow-right", on_release=lambda *a: self._navigate(self._path_field.text.strip() or "/"))
		up_btn = MDIconButton(icon="arrow-up", on_release=lambda *a: self._go_up())
		path_row.add_widget(self._path_field)
		path_row.add_widget(go_btn)
		path_row.add_widget(up_btn)
		layout.add_widget(path_row)

		# Toolbar
		tb = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(4))
		for (label, cb) in [
			("Refresh", lambda *a: self._refresh()),
			("New Folder", lambda *a: self._create_folder()),
			("Delete", lambda *a: self._delete_selected()),
		]:
			tb.add_widget(MDRaisedButton(text=label, on_release=cb, height=dp(36), size_hint_y=None))
		self._status_lbl = MDLabel(text="", theme_text_color="Custom", text_color=C_MUTED)
		tb.add_widget(self._status_lbl)
		layout.add_widget(tb)

		# File list
		scroll = ScrollView()
		self._list = MDList()
		scroll.add_widget(self._list)
		layout.add_widget(scroll)

		self.add_widget(layout)

	def on_enter(self):
		self._refresh()

	def _refresh(self):
		self._navigate(self._current)

	def _navigate(self, path: str):
		if not self.app.api_key:
			toast("Enter your API key in Settings first.")
			return

		self._current = path
		self._path_field.text = path
		self._status_lbl.text = "Loading…"
		self._list.clear_widgets()
		self._items_meta = {}

		def on_done(data):
			Clock.schedule_once(lambda dt: self._on_list_done(data))

		def on_error(e):
			Clock.schedule_once(lambda dt: toast(f"Error: {e}"))

		def _run():
			try:
				result = api_get(
					self.app.api_key, HARDCODED_BASE_URL,
					"/api/files",
					params={"path": path, "includeSubfolders": "0"},
				)
				on_done(result)
			except Exception as e:
				on_error(str(e))

		threading.Thread(target=_run, daemon=True).start()

	def _on_list_done(self, data):
		self._list.clear_widgets()
		self._items_meta = {}

		raw_folders = data.get("folders") or []
		raw_files = data.get("files") or []

		if self._current != "/":
			item = OneLineListItem(text="↑  .. (go up)", on_release=lambda *a: self._go_up())
			self._list.add_widget(item)

		for entry in raw_folders:
			if isinstance(entry, str):
				name = entry.rstrip("/").split("/")[-1]
				fullpath = (self._current.rstrip("/") + "/" + name) if self._current != "/" else ("/" + name)
			elif isinstance(entry, dict):
				name = entry.get("name") or ""
				fullpath = entry.get("path") or (self._current.rstrip("/") + "/" + name)
			else:
				continue
			if not name:
				continue
			key = f"📁 {name}"
			self._items_meta[key] = {"_type": "folder", "name": name, "path": fullpath}
			item = TwoLineListItem(
				text=key, secondary_text=fullpath,
				on_release=lambda *a, p=fullpath: self._navigate(p),
			)
			self._list.add_widget(item)

		for entry in (raw_files if isinstance(raw_files, list) else []):
			if not isinstance(entry, dict):
				continue
			name = entry.get("originalName") or entry.get("name") or "unknown"
			size = entry.get("size") or 0
			fid = entry.get("id") or ""
			key = f"📄 {name}"
			self._items_meta[key] = {"_type": "file", "name": name, "id": fid, "size": size}
			item = TwoLineListItem(
				text=key, secondary_text=fmt_size(size),
				on_release=lambda *a, k=key: self._on_item_tap(k),
			)
			self._list.add_widget(item)

		count = len(raw_folders) + len(raw_files if isinstance(raw_files, list) else [])
		self._status_lbl.text = f"{count} items"

	def _on_item_tap(self, key):
		meta = self._items_meta.get(key, {})
		if meta.get("_type") == "folder":
			self._navigate(meta["path"])

	def _go_up(self):
		if self._current == "/":
			return
		parts = self._current.rstrip("/").split("/")
		parent = "/" + "/".join(parts[:-1]) if len(parts) > 1 else "/"
		self._navigate(parent)

	def _create_folder(self):
		input_dialog("New Folder", "Folder name", self._on_create_folder_input)

	def _on_create_folder_input(self, name):
		if not name.strip():
			return
		# Implementation: POST /api/files/folders with name
		toast(f"Would create folder: {name}")

	def _delete_selected(self):
		toast("Select a file or folder first (tap to select)")


# ── Remote Screen ─────────────────────────────────────────────────────────────

class RemoteScreen(MDScreen):
	def __init__(self, app, **kwargs):
		super().__init__(**kwargs)
		self.app = app
		self._build_ui()

	def _build_ui(self):
		layout = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12))
		layout.add_widget(MDLabel(text="Remote Ingest (TODO)", theme_text_color="Custom", text_color=C_TEXT))
		self.add_widget(layout)


# ── Shares Screen ─────────────────────────────────────────────────────────────

class SharesScreen(MDScreen):
	def __init__(self, app, **kwargs):
		super().__init__(**kwargs)
		self.app = app
		self._build_ui()

	def _build_ui(self):
		layout = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(8))
		layout.add_widget(MDLabel(text="Shares (TODO)", theme_text_color="Custom", text_color=C_TEXT))
		self.add_widget(layout)


# ── Settings Screen ───────────────────────────────────────────────────────────

class SettingsScreen(MDScreen):
	def __init__(self, app, **kwargs):
		super().__init__(**kwargs)
		self.app = app
		self._build_ui()

	def _build_ui(self):
		layout = BoxLayout(orientation="vertical", padding=dp(20), spacing=dp(16))

		layout.add_widget(MDLabel(
			text="Settings",
			font_style="H5",
			theme_text_color="Custom", text_color=C_TEXT,
			size_hint_y=None, height=dp(48),
		))

		# API key field
		self._key_field = MDTextField(
			hint_text="API Key",
			password=True,
			text=self.app.api_key,
			size_hint_y=None, height=dp(48),
		)
		layout.add_widget(self._key_field)

		save_btn = MDRaisedButton(
			text="Save",
			md_bg_color=C_ACCENT,
			size_hint_x=None, width=dp(120),
			on_release=self._save,
		)
		layout.add_widget(save_btn)

		layout.add_widget(MDLabel(
			text="Advanced",
			font_style="Subtitle1",
			theme_text_color="Custom", text_color=C_MUTED,
			size_hint_y=None, height=dp(36),
		))

		# Chunk settings
		chunk_row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
		chunk_row.add_widget(MDLabel(text="Chunk size (MB):", theme_text_color="Custom", text_color=C_TEXT, size_hint_x=None, width=dp(140)))
		stored = (self.app.store.get("settings").get("value", {}) if self.app.store.exists("settings") else {})
		self._chunk_field = MDTextField(
			text=str(stored.get("chunk_mb", DEFAULT_CHUNK_SIZE_MB)),
			input_filter="int",
			size_hint_y=None, height=dp(48),
		)
		chunk_row.add_widget(self._chunk_field)

		chunk_row.add_widget(MDLabel(text="Max chunks:", theme_text_color="Custom", text_color=C_TEXT, size_hint_x=None, width=dp(100)))
		self._max_chunks_field = MDTextField(
			text=str(stored.get("max_chunks", DEFAULT_MAX_CHUNKS)),
			input_filter="int",
			size_hint_y=None, height=dp(48),
		)
		chunk_row.add_widget(self._max_chunks_field)
		layout.add_widget(chunk_row)

		layout.add_widget(Widget())
		self.add_widget(layout)

	def _save(self, *a):
		key = self._key_field.text.strip()
		self.app.api_key = key
		try:
			chunk_mb = int(self._chunk_field.text.strip() or DEFAULT_CHUNK_SIZE_MB)
		except Exception:
			toast("Invalid chunk size — must be an integer.")
			return
		try:
			max_chunks = int(self._max_chunks_field.text.strip() or DEFAULT_MAX_CHUNKS)
		except Exception:
			toast("Invalid max chunks — must be an integer.")
			return
		chunk_mb = max(1, min(chunk_mb, 100))
		max_chunks = max(1, min(max_chunks, 20))
		self.app.store.put("settings", value={"api_key": key, "chunk_mb": chunk_mb, "max_chunks": max_chunks})
		toast("Settings saved.")


# ═════════════════════════════════════════════════════════════════════════════
# MAIN APP
# ═════════════════════════════════════════════════════════════════════════════

class MochaToolsApp(MDApp):
	def __init__(self, **kwargs):
		super().__init__(**kwargs)
		self.title = "Mocha Tools"
		self.api_key = ""
		self.store = None

	def build(self):
		self.theme_cls.theme_style = "Dark"
		self.theme_cls.primary_palette = "Amber"
		self.theme_cls.primary_hue = "700"
		self.theme_cls.accent_palette = "Amber"
		self.theme_cls.accent_hue = "200"

		self.store = JsonStore("mochatools_settings.json")
		if self.store.exists("settings"):
			saved = self.store.get("settings").get("value", {})
			self.api_key = saved.get("api_key", "")

		# Bottom navigation
		nav = MDBottomNavigation(panel_color=C_CARD[:-1] + (1,))

		upload_screen = MDBottomNavigationItem(name="upload", text="Upload", icon="upload")
		upload_screen.add_widget(UploadScreen(self, name="upload_inner"))

		files_screen = MDBottomNavigationItem(name="files", text="Files", icon="folder")
		files_screen.add_widget(FilesScreen(self, name="files_inner"))

		remote_screen = MDBottomNavigationItem(name="remote", text="Remote", icon="download-network")
		remote_screen.add_widget(RemoteScreen(self, name="remote_inner"))

		shares_screen = MDBottomNavigationItem(name="shares", text="Shares", icon="share-variant")
		shares_screen.add_widget(SharesScreen(self, name="shares_inner"))

		settings_screen = MDBottomNavigationItem(name="settings", text="Settings", icon="cog")
		settings_screen.add_widget(SettingsScreen(self, name="settings_inner"))

		for s in (upload_screen, files_screen, remote_screen, shares_screen, settings_screen):
			nav.add_widget(s)

		return nav


if __name__ == "__main__":
	MochaToolsApp().run()
