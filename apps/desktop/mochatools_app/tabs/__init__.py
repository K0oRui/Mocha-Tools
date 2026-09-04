"""tabs — per-tab widgets for MochaTools."""

from .files_tab import FilesBrowserTab
from .mass_upload import MassUploadSection
from .remote_tab import RemoteTab
from .shares_tab import SharesTab
from .sync_tab import SyncTab

# settings.py lives at the mochatools_app package level
from ..settings import (
    build_settings_tab,
    load_settings,
    save_settings,
    build_basic_tab,
    build_upload_tab,
    build_updates_tab,
)

__all__ = [
    "FilesBrowserTab",
    "MassUploadSection",
    "RemoteTab",
    "SharesTab",
    "SyncTab",
    "build_settings_tab",
    "load_settings",
    "save_settings",
    "build_basic_tab",
    "build_upload_tab",
    "build_updates_tab",
]
