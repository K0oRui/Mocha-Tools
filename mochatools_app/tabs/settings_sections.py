from PyQt6.QtCore import Qt, QObject, QEvent
from PyQt6.QtWidgets import (
	QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
	QProgressBar, QPushButton, QSpinBox, QVBoxLayout, QWidget,
	QAbstractButton, QSizePolicy, QLayout,
)

from ..constants import (
	APP_VERSION, DEFAULT_CHUNK_SIZE_MB, DEFAULT_MAX_CHUNKS,
)

# Helpers replicated from settings_tab (shared)

def _sh(text: str) -> QLabel:
	lbl = QLabel(text.upper())
	lbl.setObjectName("section_header")
	# Reduce vertical footprint of section headers so they don't add large
	# gaps inside tab pages. Keep alignment consistent with other fields.
	lbl.setContentsMargins(0, 0, 0, 0)
	# Let the label size naturally (no fixed height) so font metrics are respected
	lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
	return lbl


def _card() -> QFrame:
	f = QFrame()
	f.setObjectName("card")
	# Use a consistent inner layout margin by returning a frame with no
	# external margins; the caller's layout controls spacing.
	try:
		f.setContentsMargins(6, 6, 6, 6)
	except Exception:
		pass
	return f


def _spinbox(min_val: int, max_val: int, default: int,
			 suffix: str, tooltip: str) -> QSpinBox:
	sb = QSpinBox()
	sb.setRange(min_val, max_val)
	sb.setValue(default)
	sb.setSuffix(suffix)
	sb.setToolTip(tooltip)
	sb.setMaximumWidth(200)
	# Keep a compact appearance to match other inputs in the UI.
	try:
		sb.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
	except Exception:
		pass
	try:
		sb.setFixedHeight(34)
	except Exception:
		pass
	# Try to replace internal arrow button icons after the widget is shown.
	# If not available on the platform, fall back to stylesheet rules.
	try:
		# Try a simpler approach: render the lucide chevrons to small PNGs and
		# inject them into this spinbox's stylesheet. This avoids platform
		# differences in internal button types and ensures arrows render on
		# Windows.
		from ..ui.icons import lucide_icon
		from PyQt6.QtCore import QSize, QTimer, QBuffer, QIODevice
		from PyQt6.QtGui import QPixmap

		def _inject_arrow_images():
			try:
				ico_up = lucide_icon("chevron-up", "#f0ece6", 16)
				ico_dn = lucide_icon("chevron-down", "#f0ece6", 16)
				pm_up = ico_up.pixmap(12, 12)
				pm_dn = ico_dn.pixmap(12, 12)
				# Write to PNG bytes
				buf = QBuffer()
				buf.open(QIODevice.OpenModeFlag.WriteOnly)
				pm_up.save(buf, "PNG")
				b64_up = bytes(buf.data().toBase64()).decode()
				buf.close()
				buf = QBuffer()
				buf.open(QIODevice.OpenModeFlag.WriteOnly)
				pm_dn.save(buf, "PNG")
				b64_dn = bytes(buf.data().toBase64()).decode()
				buf.close()
				css = (
					f"QSpinBox::up-arrow {{ image: url(data:image/png;base64,{b64_up}); width:10px; height:6px; }} "
					f"QSpinBox::down-arrow {{ image: url(data:image/png;base64,{b64_dn}); width:10px; height:6px; }}"
				)
				# Append to any existing widget stylesheet so other rules remain
				sb.setStyleSheet(sb.styleSheet() + "\n" + css)
			except Exception:
				pass

		# Run after widget is shown so style can apply
		QTimer.singleShot(0, _inject_arrow_images)

		# Also attempt to replace any internal QAbstractButton children with iconized buttons.
		# Some platforms only create the internal buttons after the widget is shown and laid out,
		# so attempt multiple passes with short delays until success or a small retry limit.
		def _apply_icons():
			try:
				btns = sb.findChildren(QAbstractButton)
				if not btns:
					# try again shortly
					_apply_icons.attempt = getattr(_apply_icons, 'attempt', 0) + 1
					if _apply_icons.attempt < 4:
						QTimer.singleShot(80, _apply_icons)
					return
				ico_up = lucide_icon("chevron-up", "#f0ece6", 16)
				ico_dn = lucide_icon("chevron-down", "#f0ece6", 16)
				pm_up = ico_up.pixmap(12, 12)
				pm_dn = ico_dn.pixmap(12, 12)
				from PyQt6.QtGui import QIcon
				icon_up = QIcon(pm_up)
				icon_dn = QIcon(pm_dn)
				# Order by vertical position so we map up/down correctly
				try:
					ordered = sorted(btns, key=lambda b: b.mapToParent(b.rect().topLeft()).y())
				except Exception:
					ordered = btns
				for i, b in enumerate(ordered[:2]):
					try:
						icon = icon_up if i == 0 else icon_dn
						b.setIcon(icon)
						b.setIconSize(QSize(10, 10))
						b.setStyleSheet('background: transparent; border: none; padding:0px;')
					except Exception:
						pass
				# schedule one more pass to handle deferred layout updates
				_apply_icons.attempt = getattr(_apply_icons, 'attempt', 0) + 1
				if _apply_icons.attempt < 4:
					QTimer.singleShot(140, _apply_icons)
			except Exception:
				pass

		QTimer.singleShot(0, _apply_icons)
	except Exception:
		# If anything fails, keep the native controls and let stylesheets handle arrows.
		pass
	return sb


def _add_spin_row(card_lay: QVBoxLayout, label: str, spinbox: QSpinBox):
	row = QHBoxLayout()
	row.setContentsMargins(0, 0, 0, 0)
	row.setSpacing(6)
	lbl = QLabel(label)
	lbl.setObjectName("field_label")
	# Prevent the label from expanding and pushing other widgets apart
	try:
		lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
	except Exception:
		pass
	row.addWidget(lbl)

	# Place spinbox and up/down buttons directly in the same row so the
	# layout doesn't expand the area between them. Using a separate
	# container previously allowed the outer layout to stretch the widget
	# and create a large gap; placing them on the same row keeps them
	# tightly grouped.

	# Use the native spinbox arrow area; add the spinbox into the row.
	# _spinbox() already tries to set icons on its internal arrow buttons.
	row.addWidget(spinbox)

	# Keep widgets left-aligned and absorb extra space at the row end so
	# the button column doesn't float to the far right of the card.
	row.addStretch()

	# Small right margin so the row visually matches previous layout
	# where input and arrows were closer to the label area.
	try:
		row.setContentsMargins(0, 0, 12, 0)
	except Exception:
		pass
	card_lay.addLayout(row)

	# Deterministic overlays: always create two QToolButton children inside
	# the spinbox using the lucide_icon renderer. This avoids stylesheet
	# SVG or PNG pitfalls on Windows and keeps icons crisp at small sizes.
	try:
		from ..ui.icons import lucide_icon
		from PyQt6.QtCore import QTimer, QSize, QEvent
		from PyQt6.QtWidgets import QToolButton

		class _SpinOverlayHandler(QObject):
			def __init__(self, sb: QSpinBox):
				super().__init__(sb)
				self.sb = sb
				self._create()

			def _create(self):
				try:
					# only create once
					if getattr(self.sb, '_overlay_up_btn', None):
						return
					ico_up = lucide_icon('chevron-up', '#f0ece6', 16)
					ico_dn = lucide_icon('chevron-down', '#f0ece6', 16)
					up = QToolButton(self.sb)
					dn = QToolButton(self.sb)
					up.setIcon(ico_up); dn.setIcon(ico_dn)
					sz = QSize(12, 12)
					up.setIconSize(sz); dn.setIconSize(sz)
					from PyQt6.QtCore import Qt
					up.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
					dn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
					up.setStyleSheet('background: transparent; border: none;')
					dn.setStyleSheet('background: transparent; border: none;')
					up.setCursor(self.sb.cursor()); dn.setCursor(self.sb.cursor())
					up.setFixedSize(22, 17); dn.setFixedSize(22, 17)
					up.clicked.connect(self.sb.stepUp); dn.clicked.connect(self.sb.stepDown)
					self.sb._overlay_up_btn = up; self.sb._overlay_dn_btn = dn
					self.sb.installEventFilter(self)
					up.show(); up.raise_()
					dn.show(); dn.raise_()
					self._reposition()
				except Exception:
					pass

			def eventFilter(self, obj, ev):
				try:
					if ev.type() in (QEvent.Type.Resize, QEvent.Type.Show):
						self._reposition()
				except Exception:
					pass
				return False

			def _reposition(self):
				try:
					sb = self.sb
					up = getattr(sb, '_overlay_up_btn', None)
					dn = getattr(sb, '_overlay_dn_btn', None)
					if not up or not dn:
						return
					w = sb.width(); h = sb.height(); button_w = 22
					x = w - button_w
					up.move(x, max(0, (h//4) - (up.height()//2)))
					dn.move(x, max(0, (3*h//4) - (dn.height()//2)))
				except Exception:
					pass

		# schedule creation after layout settles
		def _make():
			h = _SpinOverlayHandler(spinbox)
			QTimer.singleShot(50,  h._reposition)
			QTimer.singleShot(150, h._reposition)
		QTimer.singleShot(0, _make)
	except Exception:
		pass




# Basic tab builders
def build_basic_tab(win, lay: QVBoxLayout):
	lay.setAlignment(Qt.AlignmentFlag.AlignTop)
	lay.setSpacing(1)
	lay.addWidget(_sh("API"))
	card     = _card()
	card_lay = QVBoxLayout(card)
	card_lay.setSpacing(10)

	# API key row
	key_row = QHBoxLayout()
	key_lbl = QLabel("API key")
	key_lbl.setObjectName("field_label")
	win.api_key_edit = QLineEdit()
	win.api_key_edit.setPlaceholderText("mocha_your_api_key_here")
	win.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
	win.show_key_cb = QCheckBox("Show")
	win.show_key_cb.toggled.connect(win._toggle_key_visibility)
	key_row.addWidget(key_lbl)
	key_row.addWidget(win.api_key_edit, 1)
	key_row.addWidget(win.show_key_cb)
	card_lay.addLayout(key_row)

	win.remember_cb = QCheckBox("Remember settings across sessions")
	card_lay.addWidget(win.remember_cb)

	win.browser_download_cb = QCheckBox("Use browser for file downloads")
	win.browser_download_cb.setToolTip(
		"When checked, downloads open in your default browser.\n"
		"When unchecked, files download directly through Mocha Tools."
	)
	card_lay.addWidget(win.browser_download_cb)
	lay.addWidget(card)

	# Logging
	lay.addWidget(_sh("Logging"))
	card     = _card()
	card_lay = QVBoxLayout(card)
	card_lay.setSpacing(6)

	win.debug_cb = QCheckBox("Enable debug logging")
	win.debug_cb.setToolTip(
		"Show [DEBUG] lines in the status console and log file.\n"
		"Turn off to see only high-level status messages."
	)
	card_lay.addWidget(win.debug_cb)

	note = QLabel("When enabled, all status messages are shown in the console and written to the log file.")
	note.setObjectName("field_label")
	note.setWordWrap(True)
	card_lay.addWidget(note)
	lay.addWidget(card)

	# System tray
	lay.addWidget(_sh("System Tray"))
	card     = _card()
	card_lay = QVBoxLayout(card)
	card_lay.setSpacing(6)

	win.minimize_to_tray_cb = QCheckBox("Minimize and close to tray")
	win.minimize_to_tray_cb.setToolTip(
		"When enabled, minimising or closing the window sends Mocha Tools\n"
		"to the system tray instead of quitting. Use the tray icon's menu\n"
		"to reopen the window or quit the app."
	)
	win.minimize_to_tray_cb.toggled.connect(win._on_tray_setting_toggled)
	card_lay.addWidget(win.minimize_to_tray_cb)

	note = QLabel(
		"When disabled, minimising uses the normal taskbar behaviour and "
		"closing the window quits the app."
	)
	note.setObjectName("field_label")
	note.setWordWrap(True)
	card_lay.addWidget(note)
	lay.addWidget(card)


# Upload tab builders
def build_upload_tab(win, lay: QVBoxLayout):
	lay.setAlignment(Qt.AlignmentFlag.AlignTop)
	lay.setSpacing(0)
	lay.addWidget(_sh("Mass Upload"))
	card     = _card()
	card_lay = QVBoxLayout(card)
	card_lay.setSpacing(10)

	win.mass_conc_spin = _spinbox(1, 10, 2, " files",
		"How many files upload at the same time.\nHigher values can saturate slower connections.")
	_add_spin_row(card_lay, "Concurrent files", win.mass_conc_spin)

	win.mass_chunk_spin = _spinbox(1, 100, DEFAULT_CHUNK_SIZE_MB, " MB",
		"Size of each multipart part (1–100 MB).\nFiles smaller than this upload in one request.")
	_add_spin_row(card_lay, "Chunk size", win.mass_chunk_spin)

	win.mass_maxchunk_spin = _spinbox(1, 20, DEFAULT_MAX_CHUNKS, " chunks",
		"Max parts sent in parallel per file (1–20).")
	_add_spin_row(card_lay, "Parallel chunks", win.mass_maxchunk_spin)
	lay.addWidget(card)

	# Sync section
	lay.addWidget(_sh("Sync"))
	card     = _card()
	card_lay = QVBoxLayout(card)
	card_lay.setSpacing(10)

	win.sync_conc_spin = _spinbox(1, 10, 2, " files",
		"How many files the sync watcher uploads at the same time.\n"
		"Higher values can saturate slower connections.")
	_add_spin_row(card_lay, "Concurrent files", win.sync_conc_spin)

	win.sync_chunk_spin = _spinbox(1, 100, DEFAULT_CHUNK_SIZE_MB, " MB",
		"Size of each multipart part for sync uploads (1–100 MB).\n"
		"Files smaller than this upload in one request.")
	_add_spin_row(card_lay, "Chunk size", win.sync_chunk_spin)

	win.sync_maxchunk_spin = _spinbox(1, 20, DEFAULT_MAX_CHUNKS, " chunks",
		"Max parts sent in parallel per file during sync (1–20).")
	_add_spin_row(card_lay, "Parallel chunks", win.sync_maxchunk_spin)
	lay.addWidget(card)

	# Multipart
	lay.addWidget(_sh("Multipart Upload"))
	card     = _card()
	card_lay = QVBoxLayout(card)
	card_lay.setSpacing(10)

	note = QLabel(
		"Files larger than one chunk size are uploaded in multiple parts. "
		"Larger chunks reduce overhead; more parallel chunks can increase throughput "
		"on fast connections."
	)
	note.setObjectName("field_label")
	note.setWordWrap(True)
	card_lay.addWidget(note)

	win.chunk_size_spin = _spinbox(1, 100, DEFAULT_CHUNK_SIZE_MB, " MB",
		"Size of each upload part (1–100 MB).\n"
		"Files ≤ this size are uploaded in a single request.\n"
		"Files larger than this are split into multiple parts.")
	_add_spin_row(card_lay, "Chunk size", win.chunk_size_spin)

	win.max_chunks_spin = _spinbox(1, 20, DEFAULT_MAX_CHUNKS, " chunks",
		"Maximum number of upload parts sent in parallel (1–20).\n"
		"Higher values improve throughput on fast connections but use more memory.")
	_add_spin_row(card_lay, "Max parallel chunks", win.max_chunks_spin)
	lay.addWidget(card)


# Updates tab builders
def build_updates_tab(win, lay: QVBoxLayout):
	lay.setAlignment(Qt.AlignmentFlag.AlignTop)
	lay.setSpacing(0)
	lay.addWidget(_sh("Updates"))
	card     = _card()
	card_lay = QVBoxLayout(card)
	card_lay.setSpacing(8)

	try:
		from ..updater import _is_portable_windows
		_portable_suffix = " (portable)" if _is_portable_windows() else ""
	except Exception:
		_portable_suffix = ""
	win.update_status_lbl = QLabel(f"Current version: {APP_VERSION}{_portable_suffix}")
	win.update_status_lbl.setObjectName("field_label")
	win.update_status_lbl.setWordWrap(True)
	card_lay.addWidget(win.update_status_lbl)

	win.update_progress = QProgressBar()
	win.update_progress.setValue(0)
	win.update_progress.hide()
	card_lay.addWidget(win.update_progress)

	btn_row = QHBoxLayout()
	win.check_update_btn = QPushButton("Check for updates")
	win.check_update_btn.setObjectName("browse_btn")
	win.check_update_btn.setFixedHeight(36)
	win.check_update_btn.setStyleSheet(
		"min-height:0px; padding:0px 16px; font-size:13px; font-weight:600;"
		"background:#1e1c19; color:#f0ece6; border:1px solid #3d3a35; border-radius:7px;"
	)
	win.check_update_btn.clicked.connect(win._check_for_updates)
	btn_row.addWidget(win.check_update_btn)

	from ..theme import get_accent, notifier, accent_qcolor
	win.install_update_btn = QPushButton("↓  Install update")
	win.install_update_btn.setObjectName("upload_btn")
	win.install_update_btn.setFixedHeight(36)
	win.install_update_btn.setStyleSheet(
		f"min-height:0px; padding:0px 16px; font-size:13px; font-weight:700;"
		f"background:{get_accent()}; color:#111010; border:none; border-radius:7px;"
	)
	win.install_update_btn.clicked.connect(win._install_update)
	win.install_update_btn.hide()
	btn_row.addWidget(win.install_update_btn)
	try:
		notifier().accent_changed.connect(lambda _old, _new: win.install_update_btn.setStyleSheet(
			f"min-height:0px; padding:0px 16px; font-size:13px; font-weight:700;"
			f"background:{_new}; color:#111010; border:none; border-radius:7px;"
		))
	except Exception:
		pass

	win.release_info_btn = QPushButton("Release info")
	win.release_info_btn.setObjectName("browse_btn")
	win.release_info_btn.setFixedHeight(36)
	win.release_info_btn.setStyleSheet(
		"min-height:0px; padding:0px 16px; font-size:13px; font-weight:600;"
		"background:#1e1c19; color:#f0ece6; border:1px solid #3d3a35; border-radius:7px;"
	)
	win.release_info_btn.clicked.connect(win._show_release_info)
	win.release_info_btn.hide()
	btn_row.addWidget(win.release_info_btn)

	btn_row.addStretch()
	card_lay.addLayout(btn_row)

	# ── Behaviour checkboxes ──────────────────────────────────────────────────
	win.check_updates_on_launch_cb = QCheckBox("Check for updates on launch")
	win.check_updates_on_launch_cb.setToolTip(
		"Automatically check for a new version each time Mocha Tools starts.\n"
		"If an update is found you will be prompted to download it."
	)
	win.check_updates_on_launch_cb.setChecked(True)   # default on
	card_lay.addWidget(win.check_updates_on_launch_cb)

	win.auto_restart_cb = QCheckBox("Auto-restart after update downloads")
	win.auto_restart_cb.setToolTip(
		"Restart Mocha Tools automatically once an update has finished\n"
		"downloading, without showing a confirmation prompt."
	)
	card_lay.addWidget(win.auto_restart_cb)

	lay.addWidget(card)