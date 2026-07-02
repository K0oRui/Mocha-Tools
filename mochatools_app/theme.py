from PyQt6.QtCore import QSettings, QObject, pyqtSignal
from PyQt6.QtGui import QColor
from .constants import ORG_NAME, APP_NAME

# Default accent color used across the app
DEFAULT_ACCENT = "#c8a96e"
DEFAULT_FONT_FAMILY = "Segoe UI"
DEFAULT_FONT_SIZE = 13

# Default background theme key
DEFAULT_BACKGROUND = "mocha"

# ── Background theme palettes ────────────────────────────────────────────────
# Each palette supplies every background/border/text token used by the
# QSS template in styles.py. "mocha" reproduces the original hardcoded
# dark-tan scheme; "white" and "black" are new high-contrast variants.
#
# Keys:
#   bg0        window/root background (darkest panel-behind-panel)
#   bg1        titlebar / tab bar / dialog background
#   bg2        card background
#   bg3        input/control background
#   bg4        input focus background
#   bg5        button/spinbox segment background
#   bg6        hover background for button/spinbox segments
#   bg7        tree/list/log console background (often same as bg1 or darker)
#   border     default border
#   border2    elevated/hover border
#   text       primary text
#   text_muted secondary/muted text
#   text_dim   placeholder/disabled text
BACKGROUND_THEMES: dict[str, dict[str, str]] = {
	"mocha": {
		# Layered warm-dark scheme: distinct root → card → input elevation
		# so panels read as separate surfaces instead of one flat wash.
		# Wider steps between bg0/bg2/bg3 give clearly-readable depth.
		"bg0": "#0d0c0b", "bg1": "#1c1a18", "bg2": "#211e1b", "bg3": "#2a2724",
		"bg4": "#332f2a", "bg5": "#2e2a26", "bg6": "#423d37", "bg7": "#131110",
		"border": "#34302b", "border2": "#4a453e",
		"text": "#f4f1eb", "text_muted": "#aca496", "text_dim": "#6f6a60",
	},
	"white": {
		# Warm off-white with a clear card > page > input elevation ladder.
		"bg0": "#f1f0ed", "bg1": "#ffffff", "bg2": "#ffffff", "bg3": "#f3f2ef",
		"bg4": "#ffffff", "bg5": "#ebe9e5", "bg6": "#d8d4cd", "bg7": "#f6f5f2",
		"border": "#e5e2dc", "border2": "#cec9c1",
		"text": "#1a1815", "text_muted": "#605c55", "text_dim": "#948f86",
	},
	"black": {
		# True-black OLED variant with subtly warm neutral steps.
		"bg0": "#000000", "bg1": "#0d0d0c", "bg2": "#131311", "bg3": "#1b1a18",
		"bg4": "#232220", "bg5": "#1f1e1c", "bg6": "#302e2b", "bg7": "#050504",
		"border": "#282624", "border2": "#3a3733",
		"text": "#f4f1eb", "text_muted": "#aca496", "text_dim": "#6f6a60",
	},
}

BACKGROUND_LABELS: dict[str, str] = {
	"mocha": "Mocha",
	"white": "White",
	"black": "Black",
}


def get_background_palette(name: str | None = None) -> dict[str, str]:
	"""Return the palette dict for the given theme name (or current/default)."""
	key = (name or get_background() or DEFAULT_BACKGROUND).lower()
	return BACKGROUND_THEMES.get(key, BACKGROUND_THEMES[DEFAULT_BACKGROUND])


# runtime cached background theme key (may be non-persisted)
_current_background: str | None = None


def get_background() -> str:
	"""Return the current background theme key (runtime cached or persisted)."""
	global _current_background
	if _current_background:
		return _current_background
	try:
		s = QSettings(ORG_NAME, APP_NAME)
		v = s.value("background", None)
		if v and str(v).lower() in BACKGROUND_THEMES:
			return str(v).lower()
	except Exception:
		pass
	return DEFAULT_BACKGROUND


def set_background(name: str, persist: bool = True) -> None:
	"""Set the background theme.

	If persist is True the value is written to QSettings; otherwise the
	value is cached at runtime only. In both cases background_changed is
	emitted with (old, new) theme keys.
	"""
	key = (name or DEFAULT_BACKGROUND).lower()
	if key not in BACKGROUND_THEMES:
		key = DEFAULT_BACKGROUND

	old = get_background()

	global _current_background
	_current_background = key

	if persist:
		try:
			s = QSettings(ORG_NAME, APP_NAME)
			s.setValue("background", key)
			try:
				s.sync()
			except Exception:
				pass
		except Exception:
			pass

	try:
		_notifier.background_changed.emit(old, key)
	except Exception:
		pass


# runtime cached accent (may be non-persisted)
_current_accent: str | None = None


def get_accent() -> str:
	"""Return the current accent (runtime cached or persisted) as a hex string.

	If a runtime accent was set via set_accent(persist=False) it takes precedence
	so the UI reflects immediate changes even if they were not written to QSettings.
	Otherwise the persisted QSettings value is returned (or DEFAULT_ACCENT).
	"""
	global _current_accent
	if _current_accent:
		return _current_accent
	try:
		s = QSettings(ORG_NAME, APP_NAME)
		v = s.value("accent", None)
		if v:
			# normalize to lowercase hex string
			try:
				vh = str(v)
				if not vh.startswith("#"):
					vh = "#" + vh
				return vh.lower()
			except Exception:
				return str(v)
	except Exception:
		pass
	return DEFAULT_ACCENT


def accent_qcolor() -> QColor:
	return QColor(get_accent())


class _AccentNotifier(QObject):
	# emit (old_hex, new_hex)
	accent_changed = pyqtSignal(str, str)
	# emit (family, size)
	font_changed = pyqtSignal(str, int)
	# emit (old_theme_key, new_theme_key)
	background_changed = pyqtSignal(str, str)


_notifier = _AccentNotifier()


def notifier() -> _AccentNotifier:
	"""Return the module-level notifier object (use .accent_changed.connect).

	Example: from .theme import notifier; notifier().accent_changed.connect(handler)
	"""
	return _notifier


def set_accent(accent_hex: str, persist: bool = True) -> None:
	"""Set the accent color.

	If persist is True the value is written to QSettings; otherwise the value
	is cached at runtime only. In both cases the accent_changed notifier is
	emitted with (old, new) where new is the normalized hex string.
	"""
	old = DEFAULT_ACCENT
	try:
		s = QSettings(ORG_NAME, APP_NAME)
		old_v = s.value("accent", None)
		if old_v:
			old = old_v
	except Exception:
		pass
	# normalize
	ah = accent_hex or DEFAULT_ACCENT
	if not ah.startswith("#"):
		ah = "#" + ah
	ah = ah.lower()

	# update runtime cache
	global _current_accent
	_current_accent = ah

	# persist only if requested
	if persist:
		try:
			s = QSettings(ORG_NAME, APP_NAME)
			s.setValue("accent", ah)
			try:
				s.sync()
			except Exception:
				pass
		except Exception:
			pass

	try:
		_notifier.accent_changed.emit(old, ah)
	except Exception:
		pass


def get_font() -> tuple[str, int]:
	"""Return (family, size) from runtime cache or QSettings (or defaults)."""
	global _current_accent
	try:
		if '_current_font_family' in globals() and globals().get('_current_font_family'):
			fam = globals().get('_current_font_family')
			sz = globals().get('_current_font_size') or DEFAULT_FONT_SIZE
			return (fam, int(sz))
	except Exception:
		pass
	try:
		s = QSettings(ORG_NAME, APP_NAME)
		fam = s.value('font_family', DEFAULT_FONT_FAMILY) or DEFAULT_FONT_FAMILY
		sz = s.value('font_size', DEFAULT_FONT_SIZE) or DEFAULT_FONT_SIZE
		try:
			return (str(fam), int(sz))
		except Exception:
			return (str(fam), DEFAULT_FONT_SIZE)
	except Exception:
		return (DEFAULT_FONT_FAMILY, DEFAULT_FONT_SIZE)


def set_font(family: str, size: int, persist: bool = True) -> None:
	"""Set the application font (emit notifier.font_changed)."""
	old_family, old_size = get_font()
	# normalize
	fam = family or DEFAULT_FONT_FAMILY
	try:
		sz = int(size)
	except Exception:
		sz = DEFAULT_FONT_SIZE

	# update runtime cache
	globals()['_current_font_family'] = fam
	globals()['_current_font_size'] = sz

	if persist:
		try:
			s = QSettings(ORG_NAME, APP_NAME)
			s.setValue('font_family', fam)
			s.setValue('font_size', int(sz))
			try:
				s.sync()
			except Exception:
				pass
		except Exception:
			pass

	try:
		_notifier.font_changed.emit(fam, int(sz))
	except Exception:
		pass