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
		"bg0": "#111010", "bg1": "#181614", "bg2": "#181614", "bg3": "#1e1c19",
		"bg4": "#222018", "bg5": "#252320", "bg6": "#3d3a35", "bg7": "#141210",
		"border": "#2e2b27", "border2": "#3d3a35",
		"text": "#f0ece6", "text_muted": "#9c9484", "text_dim": "#5a5650",
	},
	"white": {
		"bg0": "#fafafa", "bg1": "#ffffff", "bg2": "#ffffff", "bg3": "#f2f2f0",
		"bg4": "#ffffff", "bg5": "#ececea", "bg6": "#dcdad6", "bg7": "#f5f5f3",
		"border": "#dedcd8", "border2": "#c7c4be",
		"text": "#191816", "text_muted": "#5d5a54", "text_dim": "#9d9a92",
	},
	"black": {
		"bg0": "#000000", "bg1": "#0a0a0a", "bg2": "#0a0a0a", "bg3": "#141414",
		"bg4": "#181818", "bg5": "#1c1c1c", "bg6": "#2c2c2c", "bg7": "#000000",
		"border": "#242424", "border2": "#333333",
		"text": "#f0ece6", "text_muted": "#9c9484", "text_dim": "#5a5650",
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