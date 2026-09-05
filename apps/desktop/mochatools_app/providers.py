"""providers.py — Typed provider objects for constructor-injected dependencies.

Replaces the ad-hoc ``lambda`` closures previously passed into tab and
client constructors with small, typed objects that read live widget state.
Consumers depend on the ``Protocol`` types; the widget-backed classes are
the concrete implementations wired up in ``app.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from PySide6.QtWidgets import QCheckBox, QLineEdit, QSpinBox


class ApiKeyProvider(Protocol):
    def get_api_key(self) -> str: ...


class UploadPathProvider(Protocol):
    def get_upload_path(self) -> str: ...
    def set_upload_path(self, path: str) -> None: ...


class SyncSettingsProvider(Protocol):
    def get_sync_settings(self) -> tuple[int, int, int]: ...
    def get_debug(self) -> bool: ...


class MassSettingsProvider(Protocol):
    def get_mass_settings(self) -> tuple[int, int, int]: ...
    def get_debug(self) -> bool: ...


class WidgetApiKeyProvider:
    def __init__(self, win: Any) -> None:
        self._win = win

    def get_api_key(self) -> str:
        return self._win.api_key_edit.text().strip()


class WidgetUploadPathProvider:
    def __init__(self, upload_path_edit: QLineEdit) -> None:
        self._edit = upload_path_edit

    def get_upload_path(self) -> str:
        return self._edit.text().strip()

    def set_upload_path(self, path: str) -> None:
        self._edit.setText(path)


class WidgetSyncSettingsProvider:
    def __init__(
        self,
        chunk_spin: QSpinBox,
        maxchunk_spin: QSpinBox,
        debug_cb: QCheckBox,
    ) -> None:
        self._chunk = chunk_spin
        self._maxchunk = maxchunk_spin
        self._debug = debug_cb

    def get_sync_settings(self) -> tuple[int, int, int]:
        return (1, self._chunk.value(), self._maxchunk.value())

    def get_debug(self) -> bool:
        return self._debug.isChecked()


class WidgetMassSettingsProvider:
    def __init__(
        self,
        chunk_spin: QSpinBox,
        maxchunk_spin: QSpinBox,
        debug_cb: QCheckBox,
    ) -> None:
        self._chunk = chunk_spin
        self._maxchunk = maxchunk_spin
        self._debug = debug_cb

    def get_mass_settings(self) -> tuple[int, int, int]:
        return (1, self._chunk.value(), self._maxchunk.value())

    def get_debug(self) -> bool:
        return self._debug.isChecked()


class DefaultMassSettingsProvider:
    def __init__(self, chunk_mb: int = 0, max_chunks: int = 0) -> None:
        self._chunk_mb = chunk_mb
        self._max_chunks = max_chunks

    def get_mass_settings(self) -> tuple[int, int, int]:
        return (1, self._chunk_mb, self._max_chunks)

    def get_debug(self) -> bool:
        return False
