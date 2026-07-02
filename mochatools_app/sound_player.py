"""
sound_player.py — Optional per-event sound playback for MochaTools.

Six events can each be assigned an audio file (any format PyQt6 Multimedia
can decode — wav, mp3, ogg, flac, m4a, etc.). If an event has no file
assigned, playing it is a silent no-op — nothing happens, by design.

Settings are stored directly in QSettings (not gated behind the
"Remember settings" checkbox, same as other app preferences like chunk
size) so file paths persist across restarts as soon as they're picked.
"""

import os

from PyQt6.QtCore import QSettings, QUrl

from .constants import ORG_NAME, APP_NAME

# (settings_key, human-readable label) — drives both the Settings → Sounds
# UI and the lookup used by play_sound_event(). Keep the key stable; it's
# used as a QSettings key suffix.
SOUND_EVENTS = [
    ("sound_single_upload", "Single file upload completion"),
    ("sound_mass_file",     "Each file completed in mass upload"),
    ("sound_mass_all",      "All files complete in mass upload"),
    ("sound_remote_ingest", "Remote ingest completion"),
    ("sound_sync_file",     "Individual file synced"),
    ("sound_sync_folder",   "Folder up to date after syncing"),
]

# Keep references to in-flight players alive until playback stops; QMediaPlayer
# / QAudioOutput are garbage-collected (and playback cut off) if nothing holds
# a reference to them once this function returns.
_active_players: list = []


def sound_path(event_key: str) -> str:
    """Return the configured file path for an event, or '' if unset."""
    s = QSettings(ORG_NAME, APP_NAME)
    return s.value(f"{event_key}_path", "", type=str) or ""


def set_sound_path(event_key: str, path: str) -> None:
    """Persist (or clear, if path is falsy) the sound file for an event."""
    s = QSettings(ORG_NAME, APP_NAME)
    if path:
        s.setValue(f"{event_key}_path", path)
    else:
        s.remove(f"{event_key}_path")


def play_sound_event(event_key: str) -> None:
    """
    Play the sound configured for `event_key`, if any.

    Does nothing (no error, no sound) if the event has no file assigned,
    the file no longer exists, or QtMultimedia isn't available.
    """
    path = sound_path(event_key)
    if not path or not os.path.isfile(path):
        return

    try:
        from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
    except Exception:
        return

    try:
        player = QMediaPlayer()
        audio_out = QAudioOutput()
        player.setAudioOutput(audio_out)
        player.setSource(QUrl.fromLocalFile(path))

        entry = (player, audio_out)
        _active_players.append(entry)

        def _on_state_changed(state, _entry=entry):
            try:
                if state == QMediaPlayer.PlaybackState.StoppedState:
                    if _entry in _active_players:
                        _active_players.remove(_entry)
            except Exception:
                pass

        player.playbackStateChanged.connect(_on_state_changed)
        player.play()
    except Exception:
        pass