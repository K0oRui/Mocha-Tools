"""
tabs/files_tab.py — Remote file browser tab for MochaTools.

Allows navigating folders, creating folders, deleting, moving,
sharing files, and downloading files from the remote storage.

Cache strategy
──────────────
All file-listing and shares data flows through remote_cache.  On
navigation we:
  1. Serve stale data instantly (zero-flash) if the cache has it.
  2. Subscribe for updates so the poller's background fetch auto-
     refreshes the view when fresh data arrives.
  3. On delete we optimistically prune both the in-memory cache
     entry AND the remote_cache store, then let the background
     poller confirm asynchronously.
"""

import os

import requests

from PyQt6.QtCore import Qt, QSize, QTimer, QUrl, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPixmap, QMovie, QFont, QTextCharFormat, QSyntaxHighlighter, QPainter
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMenu,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSlider,
    QStyle, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..constants import HARDCODED_BASE_URL, SHARE_BASE_URL
from ..dialogs import FolderBrowserDialog, ShareLinkDialog, MochaDialog, _gold_btn, _grey_btn
from ..logging_utils import write_debug_log
from ..workers import FilesWorker, UploadWorker
from ..ui.icons import lucide_icon
from ..theme import get_accent, accent_qcolor, get_font
from ..remote_cache import cache, registry


# ── File-type helpers ─────────────────────────────────────────────────────────

_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".ico",
              ".tiff", ".tif", ".svg"}
_VIDEO_EXT = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm",
              ".m4v", ".mpeg", ".mpg", ".3gp"}
_AUDIO_EXT = {".mp3", ".flac", ".m4a", ".wav", ".ogg", ".aac", ".wma",
              ".opus", ".aiff", ".aif"}
_TEXT_EXT = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv",
    ".py", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".html", ".htm", ".css", ".scss", ".sass", ".less",
    ".json", ".jsonc", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".java", ".kt", ".swift",
    ".go", ".rs", ".rb", ".php", ".lua", ".sh", ".bash", ".zsh", ".ps1",
    ".sql", ".env", ".gitignore", ".gitattributes", ".dockerfile",
    ".vue", ".svelte", ".graphql", ".proto", ".bat", ".r", ".pl",
}

# File size cap for text previews (bytes) — large files are read fully into
# memory and run through a syntax highlighter, so anything bigger than this
# would freeze the UI; we truncate instead.
_TEXT_PREVIEW_MAX_BYTES = 2 * 1024 * 1024  # 2 MB


def _preview_type(name: str) -> str | None:
    """Return 'image', 'video', 'audio', 'text', or None."""
    ext = os.path.splitext(name)[1].lower()
    if ext in _IMAGE_EXT:
        return "image"
    if ext in _VIDEO_EXT:
        return "video"
    if ext in _AUDIO_EXT:
        return "audio"
    if ext in _TEXT_EXT:
        return "text"
    return None


def _extract_album_art(file_path: str) -> bytes | None:
    """
    Best-effort extraction of embedded cover art from an audio file.
    Supports MP3 (ID3 APIC), FLAC, and MP4/M4A (covr atom) via mutagen.
    Returns raw image bytes (jpeg/png) or None if unavailable.
    """
    try:
        import mutagen
        from mutagen.id3 import ID3
        from mutagen.flac import FLAC
        from mutagen.mp4 import MP4
    except ImportError:
        return None

    try:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".flac":
            audio = FLAC(file_path)
            if audio.pictures:
                return audio.pictures[0].data
        elif ext == ".mp4" or ext == ".m4a":
            audio = MP4(file_path)
            covr = audio.tags.get("covr") if audio.tags else None
            if covr:
                return bytes(covr[0])
        else:
            # MP3 and most other tagged formats use ID3
            tags = ID3(file_path)
            for key in tags.keys():
                if key.startswith("APIC"):
                    return tags[key].data
    except Exception:
        pass
    return None


# ── Syntax highlighting (optional — falls back to plain text) ────────────────

def _pygments_available() -> bool:
    try:
        import pygments  # noqa: F401
        return True
    except ImportError:
        return False


class _PygmentsHighlighter(QSyntaxHighlighter):
    """
    Lexes the full document with Pygments and re-applies token colors on
    every change. This re-lexes the whole text each time rather than doing
    incremental per-block highlighting (Pygments lexers are generally not
    block-resumable), which is fine for the read-only, size-capped preview
    use case here but would be too slow for a real editor.
    """

    def __init__(self, document, lexer, accent: str):
        super().__init__(document)
        self._lexer = lexer
        from pygments.token import (
            Token, Keyword, Name, String, Number, Comment, Operator,
            Literal, Punctuation, Generic,
        )
        # A compact, dark-theme-friendly palette keyed by Pygments token type.
        # Falls back to the dialog accent color for anything unmatched.
        self._palette = {
            Keyword:      "#c586c0",
            Name.Function:"#dcdcaa",
            Name.Class:   "#4ec9b0",
            Name.Builtin: "#4ec9b0",
            Name.Decorator: "#dcdcaa",
            String:       "#ce9178",
            Number:       "#b5cea8",
            Comment:      "#6a9955",
            Operator:     "#d4d4d4",
            Punctuation:  "#d4d4d4",
            Literal:      "#b5cea8",
            Generic.Deleted: "#f87171",
            Generic.Inserted: "#4ade80",
        }
        self._default_color = accent
        self._Token = Token
        # Lexed once, up front — this preview content is read-only and never
        # changes after being set, so caching avoids re-lexing the whole
        # document on every block (which would be O(n^2) over n blocks).
        self._tokens_cache = None

    def _color_for(self, token_type) -> str:
        # Walk up the token hierarchy (e.g. Token.Literal.String.Doc ->
        # ... -> Token.Literal.String -> Token.Literal) until a palette
        # match is found, since Pygments subtypes tokens heavily.
        t = token_type
        while t is not None:
            if t in self._palette:
                return self._palette[t]
            t = t.parent
        return "#d4d4d4"

    def highlightBlock(self, text):
        if self._tokens_cache is None:
            self._build_token_buckets()

        block_num = self.currentBlock().blockNumber()
        for rel_start, rel_end, tok_type in self._tokens_cache.get(block_num, []):
            rel_start = max(0, rel_start)
            rel_end = min(len(text), rel_end)
            if rel_end <= rel_start:
                continue
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(self._color_for(tok_type)))
            self.setFormat(rel_start, rel_end - rel_start, fmt)

    def _build_token_buckets(self):
        """
        Lex the full document once and bucket each token by the line(s)
        it falls on, so highlightBlock() only looks at tokens belonging
        to the current line instead of scanning the whole file every call.

        Uses bisect against precomputed line-start offsets to map an
        absolute character offset to a line number in O(log n), and splits
        any token spanning multiple lines (e.g. a triple-quoted string)
        across each line it touches.
        """
        import bisect

        full_text = self.document().toPlainText()
        buckets: dict[int, list[tuple[int, int, object]]] = {}

        # line_starts[i] = absolute offset where line i begins
        line_starts = [0]
        for i, ch in enumerate(full_text):
            if ch == "\n":
                line_starts.append(i + 1)

        def line_at(offset: int) -> int:
            return bisect.bisect_right(line_starts, offset) - 1

        try:
            tokens = self._lexer.get_tokens_unprocessed(full_text)
        except Exception:
            tokens = []

        for start, tok_type, value in tokens:
            if not value:
                continue
            end = start + len(value)
            start_line = line_at(start)
            end_line = line_at(end - 1)

            if start_line == end_line:
                rel_start = start - line_starts[start_line]
                rel_end = end - line_starts[start_line]
                buckets.setdefault(start_line, []).append((rel_start, rel_end, tok_type))
            else:
                # Token spans multiple lines — split at each newline
                for ln in range(start_line, end_line + 1):
                    seg_start = max(start, line_starts[ln])
                    seg_end = min(end, line_starts[ln + 1] - 1 if ln + 1 < len(line_starts) else end)
                    rel_start = seg_start - line_starts[ln]
                    rel_end = seg_end - line_starts[ln]
                    if rel_end > rel_start:
                        buckets.setdefault(ln, []).append((rel_start, rel_end, tok_type))

        self._tokens_cache = buckets


# ── Background fetch worker ───────────────────────────────────────────────────

class _FetchWorker(QThread):
    """Downloads a URL into memory on a background thread."""
    finished = pyqtSignal(bytes)
    error    = pyqtSignal(str)

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self._url = url

    def run(self):
        try:
            resp = requests.get(self._url, timeout=30, stream=True)
            resp.raise_for_status()
            data = b"".join(resp.iter_content(65536))
            self.finished.emit(data)
        except Exception as e:
            self.error.emit(str(e))


# ── Seekbar that jumps to clicked position ───────────────────────────────────

class _ClickableSlider(QSlider):
    """
    QSlider subclass that seeks directly to the clicked track position
    instead of the default page-step increment behaviour.
    """
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            val = QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(),
                event.position().toPoint().x(), self.width()
            )
            self.setValue(val)
            self.sliderMoved.emit(val)
            event.accept()
        else:
            super().mousePressEvent(event)


# ── Preview dialog ────────────────────────────────────────────────────────────

class PreviewDialog(MochaDialog):
    """
    In-app preview popup for images, video, and audio.
    Fetches the presigned URL in the background, then renders:
      - Images  -> QLabel with scaled pixmap
      - Video   -> QVideoWidget + transport controls (PyQt6.QtMultimediaWidgets)
      - Audio   -> embedded album art (or a music-note placeholder) + transport
    Falls back gracefully if QtMultimedia is not available.

    Uses the same frameless titlebar chrome as the rest of the app's dialogs
    (MochaDialog). Audio previews use a small fixed-size window since there's
    no large visual content to show; image/video previews keep a larger,
    resizable window.
    """

    _AUDIO_SIZE = (340, 360)

    def __init__(self, name: str, presigned_url: str,
                 media_type: str, parent=None):
        is_audio = media_type == "audio"
        min_size = self._AUDIO_SIZE if is_audio else (520, 400)

        super().__init__(f"Preview — {name}", parent, min_size=min_size)
        self._url          = presigned_url
        self._name         = name
        self._media_type   = media_type
        self._player       = None
        self._movie        = None
        self._fetch_worker = None
        self._cleaned_up   = False

        if is_audio:
            self.setFixedSize(*self._AUDIO_SIZE)
        else:
            self.resize(720, 520)

        # content_layout already has a grip row appended by MochaDialog;
        # pull it off so we can insert our widgets above it, then put it
        # back at the end. Audio mode drops the grip since it's fixed-size.
        self._lay = self.content_layout
        self._grip_item = self._lay.takeAt(self._lay.count() - 1)
        if is_audio and self._grip_item is not None:
            # Discard the grip widget entirely for the fixed-size audio window
            w = self._grip_item.widget()
            if w:
                w.setParent(None)
            self._grip_item = None

        self._lay.setContentsMargins(14, 10, 14, 10 if is_audio else 14)
        self._lay.setSpacing(8)

        # Loading placeholder
        self._loading_lbl = QLabel("Loading…")
        self._loading_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_lbl.setStyleSheet("color:#9c9484; background:transparent;")
        self._lay.addWidget(self._loading_lbl, 1)

        if self._grip_item is not None:
            self._lay.addItem(self._grip_item)

        # Start fetching in background
        self._fetch_worker = _FetchWorker(presigned_url, self)
        self._fetch_worker.finished.connect(self._on_data)
        self._fetch_worker.error.connect(self._on_error)
        self._fetch_worker.start()

    def _on_error(self, msg: str):
        self._loading_lbl.setText(f"Failed to load: {msg}")

    def _on_data(self, data: bytes):
        self._loading_lbl.hide()
        if self._media_type == "image":
            self._show_image(data)
        elif self._media_type == "video":
            self._show_video(data)
        elif self._media_type == "audio":
            self._show_audio(data)
        elif self._media_type == "text":
            self._show_text(data)

    # ── Image ──────────────────────────────────────────────────────────────────

    def _show_image(self, data: bytes):
        # Animated GIFs need QMovie (QPixmap only ever grabs one frame).
        if data[:6] in (b"GIF87a", b"GIF89a"):
            self._show_gif(data)
            return

        pm = QPixmap()
        pm.loadFromData(data)
        if pm.isNull():
            self._loading_lbl.setText("Could not decode image.")
            self._loading_lbl.show()
            return

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background:transparent;")

        img_lbl = QLabel()
        img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img_lbl.setStyleSheet("background:transparent;")

        target = self.size() - QSize(40, 140)
        if pm.width() > target.width() or pm.height() > target.height():
            pm = pm.scaled(target,
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        img_lbl.setPixmap(pm)
        scroll.setWidget(img_lbl)
        # Insert before the close-button row
        self._lay.insertWidget(self._lay.count() - 1, scroll, 1)

    def _show_gif(self, data: bytes):
        import tempfile as _tf

        # QMovie streams frames from disk/QIODevice as it plays, so the
        # backing file needs to outlive the dialog — keep the path around
        # and clean it up in closeEvent alongside the audio/video temp files.
        tmp = _tf.NamedTemporaryFile(delete=False, suffix=".gif")
        tmp.write(data); tmp.flush(); tmp.close()
        self._tmp_path = tmp.name

        self._movie = QMovie(tmp.name)
        if not self._movie.isValid():
            self._loading_lbl.setText("Could not decode GIF.")
            self._loading_lbl.show()
            return

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background:transparent;")
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)

        gif_lbl = QLabel()
        gif_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        gif_lbl.setStyleSheet("background:transparent;")

        # frameRect() is invalid (0x0) until a frame has actually been
        # decoded, so force-load frame 0 before reading native size —
        # otherwise the "is it too big?" check below always thinks the
        # GIF is fine and it plays back at native resolution.
        self._movie.jumpToFrame(0)
        native = self._movie.frameRect().size()
        if not native.isValid() or native.isEmpty():
            native = self._movie.currentPixmap().size()

        target = self.size() - QSize(40, 140)
        if native.isValid() and not native.isEmpty():
            scaled = QSize(native)
            scaled.scale(target, Qt.AspectRatioMode.KeepAspectRatio)
            self._movie.setScaledSize(scaled)

        gif_lbl.setMovie(self._movie)
        scroll.setWidget(gif_lbl)
        self._lay.insertWidget(self._lay.count() - 1, scroll, 1)
        self._movie.start()

    # ── Text / code ────────────────────────────────────────────────────────────

    def _show_text(self, data: bytes):
        truncated = False
        if len(data) > _TEXT_PREVIEW_MAX_BYTES:
            data = data[:_TEXT_PREVIEW_MAX_BYTES]
            truncated = True

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                # Fall back to latin-1, which never fails to decode (every
                # byte maps to a codepoint) — good enough for a preview of
                # a file that isn't actually UTF-8 text.
                text = data.decode("latin-1", errors="replace")

        if truncated:
            text += "\n\n… (preview truncated, file is larger than 2 MB)"

        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        try:
            acc = get_accent()
        except Exception:
            acc = "#c8a96e"
        editor.setStyleSheet(
            "QPlainTextEdit {"
            " background:#0e0d0c; color:#d4d4d4; border:1px solid #2a2722;"
            " border-radius:8px; padding:8px;"
            " font-family:'Consolas','Fira Code','Courier New',monospace;"
            " font-size:12px;"
            "}"
        )
        editor.setPlainText(text)

        # Syntax highlighting — best effort via Pygments, silently skipped
        # if it's not installed or no lexer matches the extension.
        if _pygments_available():
            try:
                from pygments.lexers import get_lexer_for_filename
                from pygments.util import ClassNotFound
                try:
                    lexer = get_lexer_for_filename(self._name, stripnl=False)
                    self._highlighter = _PygmentsHighlighter(editor.document(), lexer, acc)
                except ClassNotFound:
                    pass
            except Exception:
                pass

        self._lay.insertWidget(self._lay.count() - 1, editor, 1)

    # ── Video ──────────────────────────────────────────────────────────────────

    def _show_video(self, data: bytes):
        try:
            import tempfile as _tf, os as _os
            from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
            from PyQt6.QtMultimediaWidgets import QVideoWidget

            suffix = _os.path.splitext(self._name)[1] or ".mp4"
            tmp = _tf.NamedTemporaryFile(delete=False, suffix=suffix)
            tmp.write(data); tmp.flush(); tmp.close()
            self._tmp_path = tmp.name

            video_w = QVideoWidget()
            video_w.setMinimumHeight(280)

            self._player = QMediaPlayer(self)
            audio_out = QAudioOutput(self)
            audio_out.setVolume(1.0)
            self._player.setAudioOutput(audio_out)
            self._player.setVideoOutput(video_w)
            self._player.setSource(QUrl.fromLocalFile(tmp.name))

            controls = self._make_transport(self._player)
            self._lay.insertWidget(self._lay.count() - 1, video_w, 1)
            self._lay.insertLayout(self._lay.count() - 1, controls)
            self._player.play()

        except ImportError:
            self._loading_lbl.setText(
                "Video preview requires PyQt6-Qt6-Multimedia.\n"
                "Install with:  pip install PyQt6-Qt6-Multimedia"
            )
            self._loading_lbl.show()
        except Exception as exc:
            self._loading_lbl.setText(f"Video error: {exc}")
            self._loading_lbl.show()

    # ── Audio ──────────────────────────────────────────────────────────────────

    def _show_audio(self, data: bytes):
        try:
            import tempfile as _tf, os as _os
            from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

            suffix = _os.path.splitext(self._name)[1] or ".mp3"
            tmp = _tf.NamedTemporaryFile(delete=False, suffix=suffix)
            tmp.write(data); tmp.flush(); tmp.close()
            self._tmp_path = tmp.name

            art = QLabel()
            art.setAlignment(Qt.AlignmentFlag.AlignCenter)
            art.setFixedSize(220, 220)
            art.setStyleSheet("background:#0e0d0c; border-radius:10px;")

            art_bytes = _extract_album_art(tmp.name)
            art_pm = QPixmap()
            if art_bytes and art_pm.loadFromData(art_bytes) and not art_pm.isNull():
                art_pm = art_pm.scaled(
                    art.size(),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                art.setPixmap(art_pm)
            else:
                # No embedded art — fall back to a music-note glyph
                try:
                    acc = get_accent()
                    note_icon = lucide_icon("music", acc, 56)
                except Exception:
                    note_icon = lucide_icon("music", "#c8a96e", 56)
                art.setPixmap(note_icon.pixmap(56, 56))

            art_row = QHBoxLayout()
            art_row.addStretch()
            art_row.addWidget(art)
            art_row.addStretch()

            self._player = QMediaPlayer(self)
            audio_out = QAudioOutput(self)
            audio_out.setVolume(1.0)
            self._player.setAudioOutput(audio_out)
            self._player.setSource(QUrl.fromLocalFile(tmp.name))

            controls = self._make_transport(self._player)
            self._lay.insertLayout(self._lay.count() - 1, art_row)
            self._lay.insertLayout(self._lay.count() - 1, controls)
            self._player.play()

        except ImportError:
            self._loading_lbl.setText(
                "Audio preview requires PyQt6-Qt6-Multimedia.\n"
                "Install with:  pip install PyQt6-Qt6-Multimedia"
            )
            self._loading_lbl.show()
        except Exception as exc:
            self._loading_lbl.setText(f"Audio error: {exc}")
            self._loading_lbl.show()

    # ── Transport controls (shared by video + audio) ───────────────────────────

    @staticmethod
    def _nudged_icon(name: str, color: str, size: int, btn_size: int, dy: int) -> QIcon:
        """
        Build an icon for a fixed-size button with its artwork shifted up
        by dy pixels, without touching the button's own geometry/box model
        (a stylesheet padding/margin approach was tried and rejected since
        it perturbed the whole transport row's layout instead of just the
        icon). Draws the normal lucide pixmap onto a canvas the size of the
        button itself, offset vertically, so the QIcon already "is" the
        nudged artwork and the button's fixed size/layout are untouched.
        """
        src = lucide_icon(name, color, size).pixmap(size, size)
        canvas = QPixmap(btn_size, btn_size)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        x = (btn_size - size) // 2
        y = (btn_size - size) // 2 - dy
        painter.drawPixmap(x, y, src)
        painter.end()
        return QIcon(canvas)

    def _make_transport(self, player) -> QHBoxLayout:
        from PyQt6.QtMultimedia import QMediaPlayer

        row = QHBoxLayout()
        row.setSpacing(8)

        try:
            acc = get_accent()
        except Exception:
            acc = "#c8a96e"

        play_btn = QPushButton()
        play_btn.setFixedSize(30, 30)
        play_btn.setIconSize(QSize(14, 14))
        play_btn.setIcon(lucide_icon("pause", acc, 14))
        play_btn.setStyleSheet(
            f"QPushButton {{ background:transparent; color:{acc}; border:1px solid {acc};"
            f" border-radius:6px; padding:0px; }}"
            f"QPushButton:hover {{ background:rgba(200,169,110,0.12); }}"
        )

        seek = _ClickableSlider(Qt.Orientation.Horizontal)
        seek.setRange(0, 0)

        _VOL_BTN_SIZE = 28
        _VOL_ICON_SIZE = 14
        _VOL_NUDGE_Y = 3  # pixels to shift the icon up within the button

        vol_btn = QPushButton()
        vol_btn.setFixedSize(_VOL_BTN_SIZE, _VOL_BTN_SIZE)
        vol_btn.setIconSize(QSize(_VOL_BTN_SIZE, _VOL_BTN_SIZE))
        vol_btn.setIcon(self._nudged_icon(
            "volume-2", "#9c9484", _VOL_ICON_SIZE, _VOL_BTN_SIZE, _VOL_NUDGE_Y
        ))
        vol_btn.setStyleSheet("QPushButton { background:transparent; border:none; }")
        self._muted = False

        time_lbl = QLabel("0:00 / 0:00")
        time_lbl.setStyleSheet(
            "color:#9c9484; background:transparent; font-size:11px;"
        )
        time_lbl.setFixedWidth(78)
        time_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # Grouped as [play] [seek ────] [time  volume], so time and the
        # mute toggle sit together as a pair at the end of the row instead
        # of volume floating alone in the middle.
        row.addWidget(play_btn)
        row.addWidget(seek, 1)
        row.addWidget(time_lbl)
        row.addWidget(vol_btn)

        def _fmt(ms: int) -> str:
            s = max(0, ms) // 1000
            return f"{s // 60}:{s % 60:02d}"

        def _on_duration(dur):
            seek.setRange(0, dur)
            time_lbl.setText(f"0:00 / {_fmt(dur)}")

        def _on_position(pos):
            if not seek.isSliderDown():
                seek.setValue(pos)
            dur = player.duration()
            time_lbl.setText(f"{_fmt(pos)} / {_fmt(dur) if dur else '0:00'}")

        def _on_state(state):
            playing = state == QMediaPlayer.PlaybackState.PlayingState
            play_btn.setIcon(lucide_icon("pause" if playing else "play", acc, 14))

        def _toggle():
            if player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                player.pause()
            else:
                player.play()

        def _toggle_mute():
            self._muted = not self._muted
            try:
                player.audioOutput().setMuted(self._muted)
            except Exception:
                pass
            icon_name = "volume-x" if self._muted else "volume-2"
            vol_btn.setIcon(self._nudged_icon(
                icon_name, "#9c9484", _VOL_ICON_SIZE, _VOL_BTN_SIZE, _VOL_NUDGE_Y
            ))

        seek.sliderMoved.connect(player.setPosition)
        player.durationChanged.connect(_on_duration)
        player.positionChanged.connect(_on_position)
        player.playbackStateChanged.connect(_on_state)
        play_btn.clicked.connect(_toggle)
        vol_btn.clicked.connect(_toggle_mute)

        return row

    # ── Cleanup ────────────────────────────────────────────────────────────────

    def _stop_playback(self):
        try:
            if self._player:
                self._player.stop()
                self._player.setSource(QUrl())
        except Exception:
            pass
        try:
            if self._movie:
                self._movie.stop()
        except Exception:
            pass

    def _cleanup(self):
        # Called from both done() (accept/reject — including the titlebar
        # close button) and closeEvent(), since depending on platform/Qt
        # version not every close path reliably triggers the other.
        # Guarded so running it twice is harmless.
        if getattr(self, "_cleaned_up", False):
            return
        self._cleaned_up = True

        self._stop_playback()
        try:
            if self._fetch_worker and self._fetch_worker.isRunning():
                self._fetch_worker.terminate()
                self._fetch_worker.wait(500)
        except Exception:
            pass
        try:
            tmp = getattr(self, "_tmp_path", None)
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass

    def done(self, result):
        self._cleanup()
        super().done(result)

    def closeEvent(self, event):
        self._cleanup()
        super().closeEvent(event)


class FilesBrowserTab(QWidget):
    """
    The 'Files' tab — lists remote files and folders, allows:
      • Navigate folders (double-click or path bar)
      • Create folder
      • Delete file or folder
      • Move file or folder
      • Create / copy share link
      • Download file (direct or via browser)
    """

    def __init__(self, get_api_key, get_upload_path, set_upload_path, parent=None):
        super().__init__(parent)
        self.get_api_key     = get_api_key
        self.get_upload_path = get_upload_path
        self.set_upload_path = set_upload_path
        self.base_url        = HARDCODED_BASE_URL
        self.current_path    = "/"
        self._workers        = []
        self._shares_map     = {}
        self._current_share_url = ""
        # Legacy in-tab cache kept only as a fallback seed before the poller
        # delivers its first result; remote_cache is authoritative.
        self._shares_cache   = None
        self._build_ui()
        # Ensure toolbar icons update if the accent changes at runtime.
        try:
            from ..theme import notifier, get_accent
            def _apply_accent(_old, new):
                try:
                    # reuse same update logic as the notifier handler in _build_toolbar
                    try:
                        self.refresh_btn.setIcon(lucide_icon("refresh-cw", new, 13))
                        self.mkdir_btn.setIcon(lucide_icon("folder", new, 13))
                        self.rename_btn.setIcon(lucide_icon("pencil", new, 13))
                        self.move_btn.setIcon(lucide_icon("move", new, 13))
                        self.share_btn.setIcon(lucide_icon("share-2", new, 13))
                        self.refresh_btn.setIconSize(QSize(13, 13))
                    except Exception:
                        pass
                    try:
                        self.status_lbl.setStyleSheet(f"color:{new}; font-size:11px; background:transparent;")
                    except Exception:
                        pass
                except Exception:
                    pass
            notifier().accent_changed.connect(_apply_accent)
            # Apply current accent immediately so Apply in Settings mirrors startup
            try:
                _apply_accent(None, get_accent())
            except Exception:
                pass
        except Exception:
            pass

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        self._build_path_bar(outer)
        self._build_toolbar(outer)
        self._build_tree(outer)
        self._build_share_bar(outer)
        self._set_action_btns_enabled(False)

    def _build_path_bar(self, parent_lay: QVBoxLayout):
        path_row = QHBoxLayout()
        path_row.setSpacing(6)

        self.path_edit = QLineEdit("/")
        self.path_edit.setPlaceholderText("/path/to/folder")
        self.path_edit.returnPressed.connect(self._on_path_entered)

        go_btn = QPushButton("Go")
        go_btn.setObjectName("tb_btn")
        go_btn.setFixedWidth(40)
        go_btn.clicked.connect(self._on_path_entered)

        up_btn = QPushButton("↑")
        up_btn.setObjectName("tb_btn")
        up_btn.setFixedWidth(32)
        up_btn.setToolTip("Go up one level")
        up_btn.clicked.connect(self._go_up)

        path_row.addWidget(QLabel("Path:"))
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(go_btn)
        path_row.addWidget(up_btn)
        parent_lay.addLayout(path_row)

    def _build_toolbar(self, parent_lay: QVBoxLayout):
        tb = QHBoxLayout()
        tb.setSpacing(4)

        self.refresh_btn = self._tb("Refresh",    "refresh-cw", self._refresh)
        self.mkdir_btn   = self._tb("New Folder", "folder",     self._create_folder)
        self.rename_btn  = self._tb("Rename",     "pencil",     self._rename_selected)
        self.move_btn    = self._tb("Move",       "move",       self._move_selected)
        self.share_btn   = self._tb("Share",      "share-2",    self._share_selected)
        self.delete_btn  = self._tb("Delete",     "trash-2",    self._delete_selected, danger=True)

        for btn in (self.refresh_btn, self.mkdir_btn, self.rename_btn,
                    self.move_btn, self.share_btn, self.delete_btn):
            tb.addWidget(btn)
        tb.addStretch()

        from ..theme import accent_qcolor
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet(f"color:{accent_qcolor().name()}; font-size:{int(get_font()[1])}px; background:transparent;")
        tb.addWidget(self.status_lbl)
        parent_lay.addLayout(tb)
        # Update toolbar icons and status label when accent changes at runtime
        try:
            from ..theme import notifier
            def _on_accent_changed(_old, _new):
                try:
                    for btn, name in (
                        (self.refresh_btn, "refresh-cw"),
                        (self.mkdir_btn, "folder"),
                        (self.rename_btn, "pencil"),
                        (self.move_btn, "move"),
                        (self.share_btn, "share-2"),
                    ):
                        try:
                            btn.setIcon(lucide_icon(name, _new, 13))
                            btn.setIconSize(QSize(13, 13))
                            try:
                                # force the style to re-polish the widget so the
                                # new icon is picked up immediately on some
                                # platforms where icon pixmaps are cached.
                                app = QApplication.instance()
                                if app and hasattr(app, 'style'):
                                    try:
                                        app.style().unpolish(btn)
                                    except Exception:
                                        pass
                                    try:
                                        app.style().polish(btn)
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                            btn.update()
                            btn.repaint()
                        except Exception:
                            pass
                    self.status_lbl.setStyleSheet(f"color:{_new}; font-size:11px; background:transparent;")
                    # Finalize visual update: repaint and process events so
                    # icon pixmaps are refreshed immediately.
                    try:
                        self.update()
                        self.repaint()
                        app = QApplication.instance()
                        if app:
                            app.processEvents()
                    except Exception:
                        pass
                except Exception:
                    pass
            notifier().accent_changed.connect(_on_accent_changed)
        except Exception:
            pass

    def _build_tree(self, parent_lay: QVBoxLayout):
        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(["Name", "Size", "Type", "Shared", "Expires"])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setRootIsDecorated(False)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)

        hdr = self.tree.header()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(True)
        hdr.resizeSection(0, 320)   # Name
        hdr.resizeSection(1, 90)    # Size
        hdr.resizeSection(2, 90)    # Type
        hdr.resizeSection(3, 80)    # Shared
        hdr.resizeSection(4, 100)   # Expires

        parent_lay.addWidget(self.tree, 1)

    def _build_share_bar(self, parent_lay: QVBoxLayout):
        self._share_bar_row = QHBoxLayout()
        self._share_bar_row.setContentsMargins(0, 0, 0, 0)
        self._share_bar_row.setSpacing(8)

        self.share_bar = QLabel("")
        self.share_bar.setObjectName("log_console")
        self.share_bar.setWordWrap(True)
        self.share_bar.setOpenExternalLinks(True)

        self.copy_share_btn = QPushButton("Copy link")
        self.copy_share_btn.setFixedHeight(36)
        # Use the user's accent as the button background, keep text and icon dark
        acc = get_accent()
        self.copy_share_btn.setStyleSheet(
            f"min-height:0px; padding:0px 16px; font-size:13px; font-weight:600;"
            f"background:{acc}; color:#111010; border:1px solid rgba(0,0,0,0.12); border-radius:7px;"
        )
        # Icon stays dark/black so it contrasts with the accent background
        self.copy_share_btn.setIcon(lucide_icon("copy", "#111010", 13))
        self.copy_share_btn.setIconSize(QSize(13, 13))
        self.copy_share_btn.clicked.connect(self._copy_share_url)

        try:
            from ..theme import notifier
            def _on_accent_change(_old, _new):
                # refresh stylesheet background to new accent and keep text/icon dark
                try:
                    self.copy_share_btn.setStyleSheet(
                        f"min-height:0px; padding:0px 16px; font-size:13px; font-weight:600;"
                        f"background:{get_accent()}; color:#111010; border:1px solid rgba(0,0,0,0.12); border-radius:7px;"
                    )
                    self.copy_share_btn.setIcon(lucide_icon("copy", "#111010", 13))
                except Exception:
                    pass
            notifier().accent_changed.connect(_on_accent_change)
        except Exception:
            pass

        self._share_bar_row.addWidget(self.share_bar, 1)
        self._share_bar_row.addWidget(self.copy_share_btn)

        # Wrap in a container widget so we can show/hide the whole row
        self._share_bar_widget = QWidget()
        self._share_bar_widget.setLayout(self._share_bar_row)
        self._share_bar_widget.hide()
        parent_lay.addWidget(self._share_bar_widget)

    def _copy_share_url(self):
        QApplication.clipboard().setText(self._current_share_url)
        self.copy_share_btn.setText("Copied!")
        QTimer.singleShot(1500, lambda: self.copy_share_btn.setText("Copy link"))

    def _tb(self, label: str, icon_name: str, slot, danger: bool = False) -> QPushButton:
        btn = QPushButton(f"  {label}")
        btn.setObjectName("tb_btn_danger" if danger else "tb_btn")
        btn.setIcon(lucide_icon(icon_name, "#f87171" if danger else get_accent(), 13))
        btn.setIconSize(QSize(13, 13))
        btn.clicked.connect(slot)
        return btn

    # ── Cache subscriptions ───────────────────────────────────────────────────

    def attach_cache_poller(self, poller):
        """Called by app.py after the poller is created.  Subscribes callbacks."""
        self._poller = poller
        registry.subscribe("shares", self._on_shares_cache_update)

    def _on_list_cache_update(self, data):
        """Called by remote_cache registry when a 'list' result for current_path arrives."""
        self._populate(self.current_path, data)
        if self._shares_cache is not None:
            self._index_shares(self._shares_cache)
            self._refresh_share_indicators()
        self._status(
            f"{self.tree.topLevelItemCount()} items"
        )

    def _on_shares_cache_update(self, data):
        """Called by remote_cache registry when a fresh 'shares' result arrives."""
        self._shares_cache = data
        self._index_shares(data)
        self._refresh_share_indicators()

    # ── Navigation ────────────────────────────────────────────────────────────

    def _on_path_entered(self):
        self._navigate(self.path_edit.text().strip() or "/")

    def _go_up(self):
        parts  = self.current_path.strip("/").split("/")
        parent = "/" + "/".join(parts[:-1]) if len(parts) > 1 else "/"
        self._navigate(parent)

    def _navigate(self, path: str):
        api_key = self.get_api_key()
        if not api_key:
            self._status("⚠ Enter your API key in the Settings tab first.")
            return

        # Unsubscribe from the old path's list updates
        registry.unsubscribe("list", self._on_list_cache_update,
                              path=self.current_path)

        self.current_path = path
        self.path_edit.setText(path)
        self._share_bar_widget.hide()
        write_debug_log(f"[DEBUG] _navigate: navigating to path={path!r}")

        # Subscribe to cache updates for the new path
        registry.subscribe("list", self._on_list_cache_update, path=path)

        # Ensure the poller is tracking this path
        if hasattr(self, "_poller"):
            self._poller.add("list", self.get_api_key, self.base_url, path=path)
            self._poller.start()

        # Serve stale data instantly if available
        stale = cache.get("list", path=path)
        if stale is not None:
            self._populate(path, stale)
            if self._shares_cache is not None:
                self._index_shares(self._shares_cache)
                self._refresh_share_indicators()
            self._status("Refreshing…")
        else:
            self.tree.clear()
            self._status("Loading…")

        # Delegate all fetching to the poller — it manages concurrency and
        # error handling centrally. force_refresh triggers an immediate poll
        # and the result arrives via _on_list_cache_update subscription.
        if hasattr(self, "_poller"):
            self._poller.force_refresh("list", path=path)

    def _refresh(self):
        # Force-invalidate this path so next poll fetches fresh
        cache.invalidate("list", path=self.current_path)
        self._navigate(self.current_path)

    # ── Called externally to warm cache after an upload ───────────────────────

    def notify_upload_done(self, remote_folder: str):
        """
        Called by app.py / mass_upload_tab when a file finishes uploading.
        Invalidates the cache for the affected folder and re-fetches.
        """
        # Normalise to the folder part (strip trailing filename if present)
        folder = remote_folder.rstrip("/")
        # If this is a file path rather than a folder, take parent
        # (heuristic: if it has an extension it's a file)
        if "." in os.path.basename(folder):
            folder = "/".join(folder.split("/")[:-1]) or "/"
        folder = folder or "/"

        cache.invalidate("list", path=folder)
        if hasattr(self, "_poller"):
            self._poller.force_refresh("list", path=folder)

        # If we're currently viewing this folder, re-populate from cache now
        if self.current_path == folder:
            stale = cache.get("list", path=folder)
            if stale is not None:
                self._populate(folder, stale)

    # ── Worker dispatch ───────────────────────────────────────────────────────

    def _run_worker(self, op: str, **kwargs):
        api_key = self.get_api_key()
        w = FilesWorker(op, api_key, self.base_url, **kwargs)
        w.done.connect(self._on_worker_done)
        w.error.connect(self._on_worker_error)
        w.finished.connect(lambda: self._workers.remove(w) if w in self._workers else None)
        self._workers.append(w)
        w.start()

    def _on_worker_done(self, result: dict):
        op = result.get("op")
        if op == "list":
            path = result["path"]
            data = result["data"]
            # Write into remote_cache so poller and other subscribers see it
            cache.set("list", data, path=path)
            registry.notify("list", data, path=path)
        elif op == "shares":
            data = result["data"]
            cache.set("shares", data)
            registry.notify("shares", data)
        elif op in ("delete", "delete_folder"):
            self._status("✓ Done")
            # Invalidate + re-fetch via poller; view already shows optimistic state
            cache.invalidate("list", path=self.current_path)
            if hasattr(self, "_poller"):
                self._poller.force_refresh("list", path=self.current_path)
        elif op in ("move", "mkdir", "rename"):
            self._status("✓ Done")
            cache.invalidate("list", path=self.current_path)
            self._refresh()
        elif op == "share":
            url   = result.get("url", "")
            token = result.get("token", "")
            self._status("✓ Share created")
            if url:
                ShareLinkDialog(url, parent=self).exec()
            # Optimistically add the new share into the shares cache so the
            # indicator updates instantly without blanking the file list.
            if token:
                new_share = {
                    "token":    token,
                    "is_active": True,
                    "isActive":  True,
                }
                # Find the selected file's metadata so we can tag fileId/fileName
                sel = self._selected_items()
                if sel:
                    meta = sel[0].data(0, Qt.ItemDataRole.UserRole) or {}
                    fid  = meta.get("id") or meta.get("fileId") or ""
                    name = meta.get("name") or meta.get("file_name") or ""
                    if fid:
                        new_share["fileId"] = fid
                    if name:
                        new_share["originalName"] = name
                        new_share["fileName"]     = name
                # Splice the new share into both the remote_cache store and the
                # local shares map so _refresh_share_indicators works immediately.
                existing = cache.get("shares")
                if existing is not None:
                    shares_list = (
                        existing.get("shares", existing)
                        if isinstance(existing, dict) else existing
                    )
                    if isinstance(shares_list, list):
                        shares_list = [s for s in shares_list
                                       if s.get("token") != token] + [new_share]
                    updated = (
                        {**existing, "shares": shares_list}
                        if isinstance(existing, dict) else shares_list
                    )
                    cache.set("shares", updated)
                    registry.notify("shares", updated)
                # Re-index and repaint share indicators in-place — no tree wipe
                if self._shares_cache is not None:
                    self._index_shares(self._shares_cache)
                    self._refresh_share_indicators()
            # Background-refresh shares to get full server state
            cache.invalidate_op("shares")
            if hasattr(self, "_poller"):
                self._poller.force_refresh("shares")

    def _on_worker_error(self, msg: str):
        self._status(f"✗ {msg}")
        QMessageBox.warning(self, "Error", msg)

    # ── Tree population ───────────────────────────────────────────────────────

    def _populate(self, path: str, data):
        # Remember which items were selected so we can restore them after
        # the tree rebuild (background refreshes shouldn't steal focus).
        selected_keys = set()
        for item in self.tree.selectedItems():
            meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
            key = meta.get("id") or meta.get("path") or meta.get("name")
            if key:
                selected_keys.add(key)

        self.tree.blockSignals(True)
        self.tree.setSortingEnabled(False)
        self.tree.clear()

        folders, files = self._parse_listing(path, data)

        if path and path != "/":
            up_item = QTreeWidgetItem(["..", "", "folder", "", ""])
            up_item.setData(0, Qt.ItemDataRole.UserRole,
                            {"_type": "up", "path": self._parent_path(path)})
            up_item.setForeground(0, QColor("#9ca3af"))
            try:
                up_item.setIcon(0, lucide_icon("folder", get_accent(), 14))
            except Exception:
                pass
            self.tree.addTopLevelItem(up_item)

        for f in sorted(folders, key=lambda x: x["name"].lower()):
            item = QTreeWidgetItem([f"{f['name']}", "", "folder", "", ""])
            item.setData(0, Qt.ItemDataRole.UserRole, {"_type": "folder", **f})
            from ..theme import accent_qcolor
            item.setForeground(0, accent_qcolor())
            try:
                item.setIcon(0, lucide_icon("folder", get_accent(), 14))
            except Exception:
                pass
            self.tree.addTopLevelItem(item)

        for f in sorted(files, key=lambda x: (
                x.get("originalName") or x.get("original_name") or
                x.get("name") or x.get("file_name") or "").lower()):
            stored_name = f.get("file_name") or f.get("name") or ""
            name        = f.get("originalName") or f.get("original_name") or f.get("name") or stored_name
            size        = f.get("size") or f.get("fileSize") or 0
            fid         = f.get("id") or f.get("fileId") or ""
            expires     = f.get("expiresAt") or f.get("expiry") or "—"
            if expires and expires != "—":
                expires = expires[:10] if len(expires) > 10 else expires
            item = QTreeWidgetItem([
                f"  {name}",
                UploadWorker._fmt_size(int(size)) if size else "—",
                "file", "", expires,
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, {
                **f, "_type": "file", "name": name, "id": fid,
                "file_name": stored_name,
                "path": f.get("path") or f"{path.rstrip('/')}/{stored_name or name}",
            })
            self.tree.addTopLevelItem(item)

        # ensure accent-aware items update if the accent changes
        try:
            from ..theme import notifier
            def _refresh_items(old, new):
                for i in range(self.tree.topLevelItemCount()):
                    it = self.tree.topLevelItem(i)
                    meta = it.data(0, Qt.ItemDataRole.UserRole) or {}
                    if meta.get('_type') == 'folder':
                        from ..theme import accent_qcolor
                        it.setForeground(0, accent_qcolor())
                        try:
                            it.setIcon(0, lucide_icon("folder", new, 14))
                        except Exception:
                            pass
            notifier().accent_changed.connect(_refresh_items)
        except Exception:
            pass

        self.tree.setSortingEnabled(True)
        self.tree.blockSignals(False)

        # Restore previous selection if those items still exist in the new listing
        if selected_keys:
            root = self.tree.invisibleRootItem()
            for i in range(root.childCount()):
                item = root.child(i)
                meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
                key = meta.get("id") or meta.get("path") or meta.get("name")
                if key in selected_keys:
                    item.setSelected(True)

        self._status(
            f"{len(folders)} folder{'s' if len(folders) != 1 else ''}, "
            f"{len(files)} file{'s' if len(files) != 1 else ''}"
        )
        # Fire selection-changed once to sync toolbar button states
        self._on_selection_changed()
        self._refresh_share_indicators()

    def _parse_listing(self, path: str, data) -> tuple[list, list]:
        """Normalise the API listing into (folders, files) lists."""
        if isinstance(data, dict):
            raw_folders = data.get("folders") or []
            raw_files   = data.get("files")   or []
        elif isinstance(data, list):
            raw_files, raw_folders = data, []
        else:
            return [], []

        write_debug_log(f"[DEBUG] _populate: path={path!r}, raw_folders={raw_folders}")

        folders: list[dict] = []
        for entry in raw_folders:
            if isinstance(entry, str):
                name = entry.rstrip("/").split("/")[-1]
                fullpath = entry if entry.startswith("/") else (
                    (path.rstrip("/") + "/" + name) if path != "/" else ("/" + name)
                )
                write_debug_log(f"[DEBUG]   String folder: {entry!r} -> {fullpath!r}")
                folders.append({"name": name, "path": fullpath})
            elif isinstance(entry, dict):
                entry_path = entry.get("path")
                name = (entry.get("name") or entry.get("originalName") or
                        (entry_path.rstrip("/").split("/")[-1] if entry_path else ""))
                fullpath = entry_path if (entry_path and entry_path.startswith("/")) else (
                    f"{path.rstrip('/')}/{name}" if path != "/" else f"/{name}"
                )
                write_debug_log(
                    f"[DEBUG]   Dict folder: name={name!r}, entry.path={entry_path!r}, "
                    f"current_path={path!r}, computed fullpath={fullpath!r}"
                )
                folders.append({**entry, "_type": "folder", "name": name, "path": fullpath})

        files: list[dict] = []
        for entry in raw_files:
            if not isinstance(entry, dict):
                continue
            if entry.get("type") == "folder" or entry.get("isFolder"):
                entry_path = entry.get("path")
                name       = entry.get("name") or (entry_path.rstrip("/").split("/")[-1] if entry_path else "")
                fullpath   = entry_path if (entry_path and entry_path.startswith("/")) else (
                    f"{path.rstrip('/')}/{name}" if path != "/" else f"/{name}"
                )
                folders.append({**entry, "name": name, "path": fullpath})
            else:
                files.append(entry)

        return folders, files

    # ── Share indicators ──────────────────────────────────────────────────────

    def _index_shares(self, data):
        self._shares_map = {}
        items = data if isinstance(data, list) else data.get("shares", [])
        for s in items:
            fid       = (s.get("fileId") or (s.get("file") or {}).get("id") or "")
            file_name = s.get("fileName") or s.get("file_name") or ""
            token     = s.get("token", "")
            share = {
                "url":     f"{SHARE_BASE_URL}/share/{token}" if token else "",
                "token":   token,
                "expires": s.get("expiresAt") or s.get("expires_at") or s.get("expiry") or "—",
                "active":  s.get("active", s.get("is_active", True)),
            }
            for key in (fid, file_name):
                if key:
                    self._shares_map[key] = share

    def _refresh_share_indicators(self):
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            item = root.child(i)
            meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
            if meta.get("_type") != "file":
                continue
            fid       = meta.get("id") or meta.get("fileId") or ""
            file_name = meta.get("file_name") or meta.get("name") or ""
            share     = self._shares_map.get(fid) or self._shares_map.get(file_name)
            if share:
                label = "● Shared" if share.get("active", True) else "○ Inactive"
                color = "#4ade80"  if share.get("active", True) else "#9ca3af"
                item.setText(3, label)
                item.setForeground(3, QColor(color))
                if item.text(4) in ("—", ""):
                    exp = share.get("expires", "—")
                    if exp and exp != "—":
                        item.setText(4, exp[:10] if len(exp) > 10 else exp)
            else:
                item.setText(3, "")

    # ── Selection helpers ─────────────────────────────────────────────────────

    def _on_selection_changed(self):
        items       = self._selected_items()
        has         = len(items) > 0
        single      = len(items) == 1
        single_file   = single and items[0].data(0, Qt.ItemDataRole.UserRole).get("_type") == "file"
        single_folder = single and items[0].data(0, Qt.ItemDataRole.UserRole).get("_type") == "folder"
        self.rename_btn.setEnabled(single_folder)
        self.move_btn.setEnabled(single)
        self.share_btn.setEnabled(single_file)
        self.delete_btn.setEnabled(has)

    def _set_action_btns_enabled(self, enabled: bool):
        self.rename_btn.setEnabled(enabled)
        self.move_btn.setEnabled(enabled)
        self.share_btn.setEnabled(enabled)
        self.delete_btn.setEnabled(enabled)

    def _selected_items(self) -> list[QTreeWidgetItem]:
        return [i for i in self.tree.selectedItems()
                if (i.data(0, Qt.ItemDataRole.UserRole) or {}).get("_type") in ("file", "folder")]

    def _on_double_click(self, item: QTreeWidgetItem, _col):
        meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if meta.get("_type") in ("folder", "up"):
            self._navigate(meta["path"])

    # ── Actions ───────────────────────────────────────────────────────────────

    def _create_folder(self):
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:")
        if not ok or not name.strip():
            return
        path = f"{self.current_path.rstrip('/')}/{name.strip()}"
        self._status(f"Creating {path}…")
        self._run_worker("mkdir", path=path)

    def _rename_selected(self):
        items = self._selected_items()
        if len(items) != 1:
            return
        meta = items[0].data(0, Qt.ItemDataRole.UserRole) or {}
        if meta.get("_type") != "folder":
            return

        folder_path = meta.get("path", "").rstrip("/")
        old_name    = folder_path.split("/")[-1]
        parent_path = "/".join(folder_path.split("/")[:-1]) or "/"

        if not old_name:
            QMessageBox.warning(self, "Rename", "Cannot determine the current folder name.")
            return

        new_name, ok = QInputDialog.getText(
            self, "Rename Folder", f"New name for {old_name!r}:", text=old_name
        )
        if not ok or not new_name.strip() or new_name.strip() == old_name:
            return

        new_name = new_name.strip()
        self._status(f"Renaming {old_name!r} → {new_name!r}…")
        self._run_worker(
            "rename",
            path=parent_path,
            old_name=old_name,
            new_name=new_name,
        )

    def _delete_selected(self):
        items = self._selected_items()
        if not items:
            return
        names = [item.text(0).strip().lstrip("📁").lstrip() for item in items]
        msg   = (f"Delete {names[0]!r}?" if len(names) == 1 else f"Delete {len(names)} items?")
        if QMessageBox.question(
            self, "Confirm Delete", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return

        # Optimistic removal from tree
        for tree_item in list(items):
            idx = self.tree.indexOfTopLevelItem(tree_item)
            if idx >= 0:
                self.tree.takeTopLevelItem(idx)

        # Prune both the in-tab convenience copy AND the remote_cache store
        self._prune_cache(items)

        for tree_item in items:
            meta = tree_item.data(0, Qt.ItemDataRole.UserRole) or {}
            if meta.get("_type") == "folder":
                self._run_worker("delete_folder", path=meta.get("path", ""))
            else:
                file_name = meta.get("file_name") or meta.get("name") or meta.get("path", "").lstrip("/")
                self._run_worker("delete", file_name=file_name)
        self._status("Deleting…")

    def _prune_cache(self, tree_items: list[QTreeWidgetItem]):
        """Remove deleted items from remote_cache so instant re-renders look correct."""
        deleted_names, deleted_paths = set(), set()
        for ti in tree_items:
            meta = ti.data(0, Qt.ItemDataRole.UserRole) or {}
            fn = meta.get("file_name") or meta.get("name") or ""
            fp = meta.get("path") or ""
            if fn: deleted_names.add(fn)
            if fp: deleted_paths.add(fp)

        def _keep_file(f):
            if not isinstance(f, dict):
                return str(f) not in deleted_names and str(f) not in deleted_paths
            fn = f.get("file_name") or f.get("originalName") or f.get("name") or ""
            fp = f.get("path") or ""
            return fn not in deleted_names and fp not in deleted_paths

        def _keep_folder(f):
            if not isinstance(f, dict):
                return str(f) not in deleted_names and str(f) not in deleted_paths
            return f.get("path", "") not in deleted_paths and f.get("name", "") not in deleted_names

        cached = cache.get("list", path=self.current_path)
        if cached is None:
            return
        if isinstance(cached, dict):
            pruned = {
                **cached,
                "files":   [f for f in cached.get("files", [])   if _keep_file(f)],
                "folders": [f for f in cached.get("folders", []) if _keep_folder(f)],
            }
        elif isinstance(cached, list):
            pruned = [f for f in cached if _keep_file(f)]
        else:
            return
        cache.set("list", pruned, path=self.current_path)

    def _move_selected(self):
        items = self._selected_items()
        if len(items) != 1:
            return
        meta      = items[0].data(0, Qt.ItemDataRole.UserRole) or {}
        is_folder = meta.get("_type") == "folder"
        fid       = meta.get("id") or meta.get("fileId") or ""
        src       = meta.get("path") or meta.get("name") or ""
        if is_folder and src and not src.endswith("/"):
            src += "/"

        dlg = FolderBrowserDialog(self.get_api_key(), self.base_url, self.current_path, parent=self)
        dlg.setWindowTitle("Move — choose destination folder")
        if not dlg.exec():
            return
        dest_folder = dlg.selected.rstrip("/") + "/"
        self._status(f"Moving to {dest_folder}…")
        self._run_worker("move", file_id=fid, source_path=src,
                         new_path=dest_folder, is_folder=is_folder)

    def _share_selected(self):
        items = self._selected_items()
        if len(items) != 1:
            return
        meta = items[0].data(0, Qt.ItemDataRole.UserRole) or {}
        fid  = meta.get("id") or meta.get("fileId") or ""
        name = meta.get("name") or ""

        if fid in self._shares_map:
            existing_url = self._shares_map[fid].get("url", "")
            ans = QMessageBox.question(
                self, "Already Shared",
                f"{name!r} already has a share link.\n\n{existing_url}\n\nCreate a new link anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
                QMessageBox.StandardButton.Cancel,
            )
            if ans == QMessageBox.StandardButton.No:
                self._current_share_url = existing_url
                try:
                    from ..theme import get_accent
                    color = get_accent()
                except Exception:
                    color = "#c8a96e"
                self.share_bar.setText(
                    f'Share link: <a href="{existing_url}" style="color:{color};">'
                    f'{existing_url}</a>'
                )
                self._share_bar_widget.show()
                return
            elif ans == QMessageBox.StandardButton.Cancel:
                return

        expiry, ok = QInputDialog.getItem(
            self, "Share Expiry", "Expiration:",
            ["Never", "1h", "6h", "12h", "1d", "3d", "7d", "14d", "30d"],
            editable=False,
        )
        if not ok:
            return
        self._status(f"Creating share for {name!r}…")
        self._run_worker("share", file_id=fid, expiry=expiry)

    def _preview_selected(self):
        items = self._selected_items()
        if len(items) != 1:
            return
        meta  = items[0].data(0, Qt.ItemDataRole.UserRole) or {}
        fid   = meta.get("id") or meta.get("fileId") or ""
        name  = meta.get("name") or meta.get("file_name") or meta.get("original_name") or ""
        ptype = _preview_type(name)
        if not fid or not ptype:
            QMessageBox.information(self, "Preview", "This file type cannot be previewed.")
            return

        api_key = self.get_api_key()
        self._status(f"Loading preview for {name!r}…")
        try:
            resp = requests.get(
                f"{self.base_url}/api/files/presigned",
                headers={"Authorization": f"Bearer {api_key}"},
                params={"fileId": fid},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            url  = (data.get("url") or data.get("presignedUrl")
                    or data.get("downloadUrl") or "")
            if not url:
                QMessageBox.warning(self, "Preview", f"No URL returned: {data}")
                self._status("")
                return
        except Exception as e:
            QMessageBox.warning(self, "Preview", f"Failed to get URL: {e}")
            self._status("")
            return

        self._status("")
        dlg = PreviewDialog(name, url, ptype, parent=self)
        dlg.show()

    def _download_selected(self):
        items = self._selected_items()
        if len(items) != 1:
            return
        meta = items[0].data(0, Qt.ItemDataRole.UserRole) or {}
        fid  = meta.get("id") or meta.get("fileId") or ""
        name = meta.get("name") or meta.get("file_name") or meta.get("original_name") or "download"
        if not fid:
            QMessageBox.warning(self, "Download", "Cannot determine file ID.")
            return

        api_key = self.get_api_key()
        try:
            resp = requests.get(
                f"{self.base_url}/api/files/presigned",
                headers={"Authorization": f"Bearer {api_key}"},
                params={"fileId": fid},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            url  = data.get("url") or data.get("presignedUrl") or data.get("downloadUrl") or ""
            if not url:
                QMessageBox.warning(self, "Download", f"No download URL returned: {data}")
                return
        except Exception as e:
            QMessageBox.warning(self, "Download", f"Failed to get download URL: {e}")
            return

        main_win    = self.window()
        use_browser = getattr(main_win, "browser_download_cb", None)
        if use_browser is not None and use_browser.isChecked():
            import webbrowser
            webbrowser.open(url)
            return

        dest_dir = QFileDialog.getExistingDirectory(self, "Save download to…")
        if not dest_dir:
            return
        dest_path = os.path.join(dest_dir, name)
        self._status(f"Downloading {name}…")

        from ..workers import DownloadWorker
        if not hasattr(self, "_dl_workers"):
            self._dl_workers = []
        w = DownloadWorker(url, dest_path)

        def _on_done(path, _w=w):
            self._status(f"✓ Saved to {path}")
            QMessageBox.information(self, "Download complete", f"Saved to:\n{path}")
            if _w in self._dl_workers:
                self._dl_workers.remove(_w)

        def _on_err(msg, _w=w):
            self._status(f"✗ Download failed: {msg}")
            QMessageBox.warning(self, "Download failed", msg)
            if _w in self._dl_workers:
                self._dl_workers.remove(_w)

        w.done.connect(_on_done)
        w.error.connect(_on_err)
        w.speed.connect(lambda bps: self._status(
            f"Downloading {name}… {bps/1024/1024:.3f} MB/s" if bps >= 1024 * 1024
            else f"Downloading {name}… {bps/1024:.3f} KB/s"
        ))
        self._dl_workers.append(w)
        w.start()

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        meta = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if meta.get("_type") not in ("file", "folder"):
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background:#1f1f1f; border:1px solid #3a3a3a; border-radius:8px; color:#f0f0f0; font-size:12px; }"
            "QMenu::item { padding:6px 8px; }"
            "QMenu::item:selected { background:#332b1a; }"
        )
        from ..theme import get_accent
        if meta.get("_type") == "file":
            # Preview action — only shown for previewable file types
            _ptype = _preview_type(meta.get("name", "") or "")
            if _ptype:
                act = menu.addAction(
                    lucide_icon("eye", get_accent(), 12), "Preview"
                )
                act.triggered.connect(self._preview_selected)
                menu.addSeparator()
            act = menu.addAction(lucide_icon("download-cloud", get_accent(), 12), "Download")
            act.triggered.connect(self._download_selected)
            act = menu.addAction(lucide_icon("share-2", get_accent(), 12), "Share")
            act.triggered.connect(self._share_selected)
        if meta.get("_type") == "folder":
            act = menu.addAction(lucide_icon("pencil", get_accent(), 12), "Rename")
            act.triggered.connect(self._rename_selected)
        act = menu.addAction(lucide_icon("move", get_accent(), 12), "Move")
        act.triggered.connect(self._move_selected)
        menu.addSeparator()
        act = menu.addAction(lucide_icon("trash-2", "#f87171", 12), "Delete")
        act.triggered.connect(self._delete_selected)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _status(self, msg: str):
        self.status_lbl.setText(msg)

    @staticmethod
    def _parent_path(path: str) -> str:
        parts = path.strip("/").split("/")
        return "/" + "/".join(parts[:-1]) if len(parts) > 1 else "/"