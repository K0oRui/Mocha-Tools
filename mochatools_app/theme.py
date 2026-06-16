from PyQt6.QtCore import QSettings, QObject, pyqtSignal
from PyQt6.QtGui import QColor
from .constants import ORG_NAME, APP_NAME

# Default accent color used across the app
DEFAULT_ACCENT = "#c8a96e"

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
