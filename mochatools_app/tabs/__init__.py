"""tabs — per-tab widgets for MochaTools."""
from .files_tab import FilesBrowserTab
from .mass_upload_tab import MassUploadTab
from .remote_tab import RemoteTab
from .shares_tab import SharesTab
from .sync_tab import SyncTab
from .settings_tab import build_settings_tab, load_settings, save_settings

__all__ = [
    "FilesBrowserTab",
    "MassUploadTab",
    "RemoteTab",
    "SharesTab",
    "SyncTab",
    "build_settings_tab",
    "load_settings",
    "save_settings",
]