from PyQt6.QtCore import QSettings, QObject, pyqtSignal
from PyQt6.QtGui import QColor
from .constants import ORG_NAME, APP_NAME

# Default accent color used across the app
DEFAULT_ACCENT = "#c8a96e"
DEFAULT_FONT_FAMILY = "Segoe UI"
DEFAULT_FONT_SIZE = 13

def get_accent() -> str:
	"""Return the persisted accent color (hex) or the default."""


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
