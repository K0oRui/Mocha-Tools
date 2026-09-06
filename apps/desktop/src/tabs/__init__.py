"""tabs — per-tab widgets for MochaTools."""

# settings.py lives at the src package level
from ..settings import (
    build_basic_tab,
    build_settings_tab,
    build_updates_tab,
    build_upload_tab,
    load_settings,
    save_settings,
)
from .files_tab import FilesBrowserTab
from .mass_upload import MassUploadSection
from .remote_tab import RemoteTab
from .shares_tab import SharesTab
from .sync_tab import SyncTab

__all__ = [
    "FilesBrowserTab",
    "MassUploadSection",
    "RemoteTab",
    "SharesTab",
    "SyncTab",
    "build_basic_tab",
    "build_settings_tab",
    "build_updates_tab",
    "build_upload_tab",
    "load_settings",
    "save_settings",
]
