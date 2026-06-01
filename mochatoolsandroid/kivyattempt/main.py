"""
Mocha Tools — Android (Kivy)
Rewrite of the PyQt6 desktop app for Android using Kivy.

All API/upload logic is ported directly from workers.py.
UI is rebuilt in Kivy with KivyMD for Material Design components.

Tabs:
  1. Upload       — pick file, multipart upload, progress, share
  2. Files        — browse remote folders, delete, move, new folder
  3. Remote       — server-side URL ingest + job list
  4. Shares       — list and delete share links
  5. Settings     — API key (persisted), chunk config
"""

import os
import json
import math
import time
import threading
import mimetypes
import platform
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Kivy config must be set BEFORE importing kivy.core ───────────────────────
import kivy
kivy.require("2.3.0")

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.core.clipboard import Clipboard
from kivy.metrics import dp
from kivy.properties import StringProperty, BooleanProperty, NumericProperty
from kivy.storage.jsonstore import JsonStore
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from kivymd.app import MDApp
from kivymd.uix.behaviors import RectangularRippleBehavior
from kivymd.uix.bottomnavigation import MDBottomNavigation, MDBottomNavigationItem
from kivymd.uix.button import MDFlatButton, MDRaisedButton, MDIconButton
from kivymd.uix.card import MDCard
from kivymd.uix.dialog import MDDialog
from kivymd.uix.label import MDLabel
from kivymd.uix.list import (
    MDList, OneLineListItem, TwoLineListItem,
    TwoLineIconListItem, IconLeftWidget,
)
from kivymd.uix.menu import MDDropdownMenu
from kivymd.uix.progressbar import MDProgressBar
from kivymd.uix.screen import MDScreen
from kivymd.uix.selectioncontrol import MDCheckbox
from kivymd.uix.snackbar import Snackbar
from kivymd.uix.spinner import MDSpinner
from kivymd.uix.tab import MDTabsBase
from kivymd.uix.textfield import MDTextField
from kivymd.uix.toolbar import MDTopAppBar

import requests

# ── Constants (from constants.py) ────────────────────────────────────────────
HARDCODED_BASE_URL      = "https://mocha.my"
CHUNK_SIZE              = 50 * 1024 * 1024
PART_UPLOAD_RETRIES     = 10
PART_UPLOAD_TIMEOUT     = 7200
S3_DEFAULT_CONCURRENCY  = 24
S3_MAX_CONCURRENCY      = 24
RELAY_DEFAULT_CONCURRENCY = 1
RELAY_MAX_CONCURRENCY   = 1
DEFAULT_CHUNK_SIZE_MB   = 50
DEFAULT_MAX_CHUNKS      = 20
APP_NAME                = "MochaTools"

EXPIRY_OPTIONS = ["Never", "1h", "6h", "12h", "1d", "3d", "7d", "14d", "30d"]
EXPIRY_LABEL_TO_HOURS = {
    "1h": 1, "6h": 6, "12h": 12,
    "1d": 24, "3d": 72, "7d": 168, "14d": 336, "30d": 720,
}

# ── Mocha colour palette ──────────────────────────────────────────────────────
C_BG        = (17/255, 16/255, 16/255, 1)        # #111010
C_CARD      = (24/255, 22/255, 20/255, 1)        # #181614
C_SURFACE   = (30/255, 28/255, 25/255, 1)        # #1e1c19
C_ACCENT    = (200/255, 169/255, 110/255, 1)     # #c8a96e
C_TEXT      = (240/255, 236/255, 230/255, 1)     # #f0ece6
C_MUTED     = (156/255, 148/255, 132/255, 1)     # #9c9484
C_SUCCESS   = (74/255, 222/255, 128/255, 1)      # #4ade80
C_ERROR     = (248/255, 113/255, 113/255, 1)     # #f87171

# ── Helpers ───────────────────────────────────────────────────────────────────

def fmt_size(n):
    if n < 1024:
        return f"{n} B"
    elif n < 1024 ** 2:
        return f"{n/1024:.1f} KB"
    elif n < 1024 ** 3:
        return f"{n/1024**2:.2f} MB"
    return f"{n/1024**3:.2f} GB"


def toast(msg, duration=3):
    """Show a snackbar notification. KivyMD 1.2.0 compatible."""
    try:
        from kivymd.uix.snackbar import Snackbar as _SB
        sb = _SB(snackbar_x=dp(8), snackbar_y=dp(8),
                 size_hint_x=0.95, duration=duration)
        try:
            sb.ids.label.text = str(msg)
        except Exception:
            pass
        sb.open()
    except Exception:
        pass


def _android_paste():
    """Return clipboard text via Android ClipboardManager, falls back to Kivy."""
    try:
        from jnius import autoclass
        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        Context = autoclass('android.content.Context')
        activity = PythonActivity.mActivity
        cm = activity.getSystemService(Context.CLIPBOARD_SERVICE)
        if cm is not None and cm.hasPrimaryClip():
            clip = cm.getPrimaryClip()
            if clip and clip.getItemCount() > 0:
                return str(clip.getItemAt(0).coerceToText(activity))
    except Exception:
        pass
    try:
        return Clipboard.paste() or ""
    except Exception:
        return ""


def _request_storage_permission(callback=None):
    """
    Grant the broadest possible file access on Android:
      • Android 11+ (API 30+): MANAGE_EXTERNAL_STORAGE via the system settings
        page.  If already granted, calls callback immediately.
      • Android 6–10: requests READ/WRITE_EXTERNAL_STORAGE at runtime.
        If already granted, calls callback immediately without showing a dialog.
      • Android 13+: also requests READ_MEDIA_* and READ_MEDIA_DOCUMENTS.
      • Non-Android: calls callback immediately.
    """
    try:
        from jnius import autoclass
        Build = autoclass('android.os.Build')
        sdk_int = Build.VERSION.SDK_INT

        if sdk_int >= 30:
            # Android 11+ — MANAGE_EXTERNAL_STORAGE is the only permission that
            # grants unrestricted file system access.
            try:
                Environment = autoclass('android.os.Environment')
                if Environment.isExternalStorageManager():
                    # Already granted — invoke callback right now.
                    if callback:
                        callback()
                    return
                # Not granted — open the system settings page.
                Intent = autoclass('android.content.Intent')
                Settings = autoclass('android.provider.Settings')
                Uri = autoclass('android.net.Uri')
                PythonActivity = autoclass('org.kivy.android.PythonActivity')
                activity = PythonActivity.mActivity
                intent = Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION)
                intent.setData(Uri.parse(f"package:{activity.getPackageName()}"))
                activity.startActivity(intent)
                # Poll every 500 ms until the user grants access and returns.
                def _poll(dt):
                    if Environment.isExternalStorageManager():
                        if callback:
                            callback()
                        return False  # cancels the interval
                Clock.schedule_interval(_poll, 0.5)
                return
            except Exception:
                pass  # fall through to standard permissions below

        # Android 6–10 (and fallback for 11+ if jnius MANAGE path failed)
        from android.permissions import request_permissions, check_permission, Permission

        perms_to_request = []
        all_attrs = (
            "READ_EXTERNAL_STORAGE",
            "WRITE_EXTERNAL_STORAGE",
            "READ_MEDIA_IMAGES",
            "READ_MEDIA_VIDEO",
            "READ_MEDIA_AUDIO",
            "READ_MEDIA_DOCUMENTS",   # Android 13+ only — AttributeError is swallowed
        )
        for attr in all_attrs:
            try:
                p = getattr(Permission, attr)
                # Only request permissions that are not already granted — avoids
                # devices where request_permissions() skips the callback entirely
                # when all requested permissions are already held.
                if not check_permission(p):
                    perms_to_request.append(p)
            except AttributeError:
                pass  # permission doesn't exist on this API level

        if not perms_to_request:
            # Everything already granted — fire callback immediately.
            if callback:
                callback()
            return

        def _cb(permissions, grants):
            # Callback fires regardless of outcome — proceed and let the upload
            # fail with a clear error if access is still denied.
            if callback:
                Clock.schedule_once(lambda dt: callback())

        request_permissions(perms_to_request, _cb)
        return
    except Exception:
        pass
    # Not on Android (desktop / CI) — invoke callback immediately.
    if callback:
        callback()


def confirm_dialog(title, text, on_yes):
    """Show a simple Yes/No dialog; calls on_yes() if confirmed."""
    dialog = MDDialog(
        title=title,
        text=text,
        buttons=[
            MDFlatButton(text="Cancel", on_release=lambda x: dialog.dismiss()),
            MDRaisedButton(text="Confirm", on_release=lambda x: (dialog.dismiss(), on_yes())),
        ],
    )
    dialog.open()


def input_dialog(title, hint, on_ok, prefill=""):
    """One-line text input dialog."""
    content = MDTextField(hint_text=hint, text=prefill, size_hint_y=None, height=dp(48))
    dialog = MDDialog(
        title=title,
        type="custom",
        content_cls=content,
        buttons=[
            MDFlatButton(text="Cancel", on_release=lambda x: dialog.dismiss()),
            MDRaisedButton(text="OK", on_release=lambda x: (dialog.dismiss(), on_ok(content.text))),
        ],
    )
    dialog.open()


# ── Progress Tracker (identical logic to desktop workers.py) ─────────────────

class ProgressTracker:
    EMIT_INTERVAL = 0.25

    def __init__(self, total_bytes, on_progress, on_speed):
        self._total     = total_bytes
        self._sent      = 0
        self._lock      = threading.Lock()
        self._start     = time.monotonic()
        self._last_emit = 0.0
        self._on_prog   = on_progress
        self._on_speed  = on_speed

    def feed(self, n_bytes):
        with self._lock:
            self._sent += n_bytes
            now     = time.monotonic()
            elapsed = max(now - self._start, 0.001)
            if now - self._last_emit >= self.EMIT_INTERVAL:
                self._last_emit = now
                pct = min(int(self._sent / self._total * 100), 99)
                bps = self._sent / elapsed
                Clock.schedule_once(lambda dt: self._on_prog(pct))
                Clock.schedule_once(lambda dt: self._on_speed(bps))

    def finish(self):
        with self._lock:
            elapsed = max(time.monotonic() - self._start, 0.001)
            bps = self._sent / elapsed
        Clock.schedule_once(lambda dt: self._on_prog(100))
        Clock.schedule_once(lambda dt: self._on_speed(bps))

    def make_streaming_body(self, chunk, read_size=65536):
        class ChunkStream:
            def __init__(self, data, tracker, block_size):
                self.chunk = data
                self.tracker = tracker
                self.block_size = block_size
                self.offset = 0
                self.length = len(data)
                self.len = self.length

            def read(self, size=-1):
                if self.offset >= self.length:
                    return b""
                if size is None or size < 0:
                    size = self.block_size
                end = min(self.offset + size, self.length)
                piece = self.chunk[self.offset:end]
                if piece:
                    self.tracker.feed(len(piece))
                    self.offset = end
                return piece

            def __len__(self):
                return self.length

        return ChunkStream(chunk, self, read_size)


# ── Upload logic (ported from UploadWorker in workers.py) ────────────────────

class UploadTask:
    """
    Runs the full multipart upload in a background thread.
    Callbacks are always dispatched back to the main thread via Clock.
    """

    def __init__(self, api_key, base_url, local_path, dest_path,
                 create_share, share_expiry, share_max_downloads,
                 on_progress, on_speed, on_status,
                 on_done, on_error,
                 chunk_size_mb=DEFAULT_CHUNK_SIZE_MB,
                 max_chunks=DEFAULT_MAX_CHUNKS,
                 original_name=None,
                 cleanup_path=None):
        self.api_key              = api_key
        self.base_url             = base_url.rstrip("/")
        self.local_path           = local_path
        self.dest_path            = dest_path
        self.create_share         = create_share
        self.share_expiry         = share_expiry
        self.share_max_downloads  = share_max_downloads
        self.on_progress          = on_progress
        self.on_speed             = on_speed
        self.on_status            = on_status
        self.on_done              = on_done
        self.on_error             = on_error
        self.original_name        = original_name
        self.cleanup_path         = cleanup_path
        self._chunk_size          = max(1, min(chunk_size_mb, 100)) * 1024 * 1024
        self._max_chunks          = max(1, min(max_chunks, 20))
        self._cancel              = False
        self._thread              = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def cancel(self):
        self._cancel = True

    def _headers(self, file_name=None):
        h = {"Authorization": f"Bearer {self.api_key}"}
        if file_name:
            h["x-file-name"] = file_name
        return h

    def _emit_status(self, msg):
        Clock.schedule_once(lambda dt: self.on_status(msg))

    def _run(self):
        try:
            # Prefer the provided original name (useful if the selected path is a temp file from a content URI)
            file_name = self.original_name if self.original_name else os.path.basename(self.local_path)
            try:
                file_size = os.path.getsize(self.local_path)
            except Exception:
                # Provide a clear error if the path is not a regular file (e.g. content:// URI)
                Clock.schedule_once(lambda dt: self.on_error("Unable to access file. Content URIs are not supported in this build."))
                return

            if file_size == 0:
                Clock.schedule_once(lambda dt: self.on_error("File is empty."))
                return

            # Ensure destination folder exists
            dest_dir = "/".join(self.dest_path.rstrip("/").split("/")[:-1]) or "/"
            if dest_dir != "/":
                try:
                    self._ensure_folder(dest_dir)
                except Exception as e:
                    err = str(e)
                    Clock.schedule_once(lambda dt, _err=err: self.on_error(f"Failed to create dest folder: {_err}"))
                    return

            self._emit_status(f"Uploading {file_name} ({fmt_size(file_size)})...")
            try:
                file_id = self._multipart_upload(file_size, self.local_path, self.dest_path)
            except Exception as e:
                # Surface multipart errors via on_error
                err = str(e)
                Clock.schedule_once(lambda dt, _err=err: self.on_error(_err))
                return

            if self._cancel or file_id is None:
                return

            share_url = None
            if self.create_share:
                self._emit_status("Creating share link...")
                try:
                    share_url = self._create_share(file_id)
                except Exception as e:
                    err = str(e)
                    Clock.schedule_once(lambda dt, _err=err: self.on_error(f"Share creation failed: {_err}"))

            result = {"file_id": file_id, "share_url": share_url}
            Clock.schedule_once(lambda dt: self.on_done(result))

        except Exception as e:
            err = str(e)
            Clock.schedule_once(lambda dt: self.on_error(err))
        finally:
            # Cleanup temporary file if requested
            if self.cleanup_path:
                try:
                    if os.path.exists(self.cleanup_path):
                        os.remove(self.cleanup_path)
                except Exception:
                    pass

    def _ensure_folder(self, path):
        parts = path.rstrip("/").rsplit("/", 1)
        parent = parts[0] or "/"
        name   = parts[1] if len(parts) > 1 else path.lstrip("/")
        requests.post(
            f"{self.base_url}/api/files/folders",
            headers={**self._headers(), "Content-Type": "application/json"},
            json={"path": parent, "name": name},
            timeout=15,
        )  # ignore errors — folder may already exist

    def _multipart_upload(self, file_size, local_path, dest_path):
        file_name = os.path.basename(local_path)
        dest_dir  = "/".join(dest_path.rstrip("/").split("/")[:-1]) or "/"
        dest_dir  = dest_dir.rstrip("/") + "/"
        mime_type = mimetypes.guess_type(local_path)[0] or "application/octet-stream"

        init_resp = requests.post(
            f"{self.base_url}/api/files/multipart/init",
            headers={**self._headers(), "Content-Type": "application/json"},
            json={
                "originalName": file_name,
                "path": dest_dir,
                "size": file_size,
                "mimeType": mime_type,
            },
            timeout=30,
        )
        init_resp.raise_for_status()
        init_data  = init_resp.json()
        strategy   = init_data.get("strategy")
        upload_id  = init_data.get("uploadId")
        key        = init_data.get("key")
        node_id    = init_data.get("nodeId")
        direct     = init_data.get("directUploadEnabled") is not False

        if strategy not in ("s3", "webdav") or not upload_id or not key or not node_id:
            raise RuntimeError(f"Invalid multipart init response: {init_data}")

        session = {
            "strategy": strategy, "uploadId": upload_id, "key": key,
            "nodeId": node_id,
            "originalName": init_data.get("originalName") or file_name,
            "path": dest_dir, "size": file_size, "mimeType": mime_type,
        }

        chunk_size  = self._chunk_size
        total_parts = math.ceil(file_size / chunk_size)
        mode        = "direct S3" if strategy == "s3" and direct else "server relay"
        default_c   = S3_DEFAULT_CONCURRENCY if mode == "direct S3" else RELAY_DEFAULT_CONCURRENCY
        max_c       = min(self._max_chunks, S3_MAX_CONCURRENCY if mode == "direct S3" else RELAY_MAX_CONCURRENCY)
        concurrency = max(1, min(
            int(init_data.get("partUploadConcurrency", default_c)),
            total_parts, max_c,
        ))

        tracker = ProgressTracker(file_size, self.on_progress, self.on_speed)
        parts   = []

        def upload_part(part_num):
            offset    = (part_num - 1) * chunk_size
            read_size = min(chunk_size, file_size - offset)
            if self._cancel:
                return None
            with open(local_path, "rb") as f:
                f.seek(offset)
                chunk = f.read(read_size)
            if self._cancel:
                return None
            if strategy == "s3" and direct:
                etag = self._upload_part_s3(session, part_num, chunk, tracker)
            else:
                etag = self._upload_part_relay(session, part_num, chunk, tracker)
            return {"partNumber": part_num, "etag": etag, "size": len(chunk)}

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {
                executor.submit(upload_part, n): n
                for n in range(1, total_parts + 1)
            }
            for future in as_completed(futures):
                if self._cancel:
                    for f in futures:
                        f.cancel()
                    return None
                result = future.result()
                if result is None or result["etag"] is None:
                    return None
                parts.append({"partNumber": result["partNumber"], "etag": result["etag"]})

        complete_payload = {**session, "parts": sorted(parts, key=lambda p: p["partNumber"])}
        j = self._complete_multipart(complete_payload)
        file_id = j.get("fileId") or j.get("id") or (j.get("file") or {}).get("id")
        tracker.finish()
        return file_id

    def _upload_part_relay(self, session, part_num, chunk, tracker):
        last_error = None
        with requests.Session() as http:
            for attempt in range(1, PART_UPLOAD_RETRIES + 1):
                if self._cancel:
                    return None
                try:
                    resp = http.put(
                        f"{self.base_url}/api/files/multipart/part",
                        headers=self._headers(),
                        params={
                            "strategy": session["strategy"],
                            "uploadId": session["uploadId"],
                            "key": session["key"],
                            "nodeId": session["nodeId"],
                            "originalName": session["originalName"],
                            "path": session["path"],
                            "partNumber": part_num,
                        },
                        data=tracker.make_streaming_body(chunk),
                        timeout=PART_UPLOAD_TIMEOUT,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    etag = data.get("etag") or resp.headers.get("ETag", "")
                    if not etag:
                        raise RuntimeError(f"No ETag for part {part_num}")
                    return etag
                except Exception as e:
                    last_error = e
                    time.sleep(min(2 ** (attempt - 1), 10))
        raise last_error

    def _presign_part_url(self, session, part_num, http=None):
        """Ask Mocha for a presigned S3 URL. Handles every response shape the server may return."""
        client = http or requests
        presign_resp = client.post(
            f"{self.base_url}/api/files/multipart/presigned",
            headers={**self._headers(), "Content-Type": "application/json"},
            json={**session, "partNumbers": [part_num]},
            timeout=30,
        )
        presign_resp.raise_for_status()
        data = presign_resp.json()

        signed_url = None
        if "url" in data:
            # Flat {"url": "https://..."} response
            signed_url = data["url"]
        elif "presignedUrl" in data:
            signed_url = data["presignedUrl"]
        elif "urls" in data and isinstance(data["urls"], list):
            # List of {"partNumber": N, "url": "..."} -- match by part number
            for entry in data["urls"]:
                if isinstance(entry, dict) and entry.get("partNumber") == part_num:
                    signed_url = entry.get("url")
                    break
            # Fallback: flat list of URL strings
            if not signed_url and data["urls"] and isinstance(data["urls"][0], str):
                signed_url = data["urls"][0]
        elif "parts" in data and isinstance(data["parts"], list):
            # {"parts": [{"partNumber": 1, "url": "..."}]}
            # Must call .get("url") on the dict -- NOT return the dict itself
            for entry in data["parts"]:
                if isinstance(entry, dict) and entry.get("partNumber") == part_num:
                    signed_url = entry.get("url")
                    break

        if not signed_url:
            raise RuntimeError(f"No presigned URL in response for part {part_num}: {data}")
        return signed_url

    def _upload_part_s3(self, session, part_num, chunk, tracker):
        """Upload one part directly to S3 via a presigned URL. Re-presigns on every retry."""
        last_error = None
        with requests.Session() as http:
            for attempt in range(1, PART_UPLOAD_RETRIES + 1):
                if self._cancel:
                    return None
                try:
                    signed_url = self._presign_part_url(session, part_num, http)
                    # PUT directly to S3 -- no auth header, URL is pre-signed
                    s3_resp = http.put(
                        signed_url,
                        data=tracker.make_streaming_body(chunk),
                        timeout=PART_UPLOAD_TIMEOUT,
                    )
                    s3_resp.raise_for_status()
                    etag = s3_resp.headers.get("ETag", "")
                    if not etag:
                        raise RuntimeError(f"No ETag returned for S3 part {part_num}")
                    return etag
                except requests.HTTPError as e:
                    content = getattr(e.response, "text", "")
                    if "NoSuchUpload" in content:
                        self._abort(session)
                        raise RuntimeError("S3 upload session expired (NoSuchUpload). Please retry.")
                    last_error = e
                except Exception as e:
                    last_error = e
                if attempt >= PART_UPLOAD_RETRIES:
                    raise last_error
                time.sleep(min(2 ** (attempt - 1), 10))
        raise last_error

    def _abort(self, session, part_numbers=None):
        try:
            payload = dict(session)
            if part_numbers:
                payload["partNumbers"] = part_numbers
            requests.post(
                f"{self.base_url}/api/files/multipart/abort",
                headers={**self._headers(), "Content-Type": "application/json"},
                json=payload,
                timeout=15,
            )
        except Exception:
            pass

    def _complete_multipart(self, payload):
        last_error = None
        for attempt in range(1, 9):
            if self._cancel:
                return {}
            try:
                resp = requests.post(
                    f"{self.base_url}/api/files/multipart/complete",
                    headers={**self._headers(), "Content-Type": "application/json"},
                    json=payload,
                    timeout=180,
                )
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                last_error = e
                time.sleep(min(10 * attempt, 60))
        raise last_error

    def _create_share(self, file_id):
        expiry_hours = EXPIRY_LABEL_TO_HOURS.get(self.share_expiry)
        payload = {"fileId": file_id}
        if expiry_hours:
            payload["expiresInHours"] = expiry_hours
        if self.share_max_downloads > 0:
            payload["maxDownloads"] = self.share_max_downloads
        resp = requests.post(
            f"{self.base_url}/api/shares",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=payload, timeout=15,
        )
        resp.raise_for_status()
        data  = resp.json()
        token = data.get("token") or (data.get("share") or {}).get("token", "")
        return f"{self.base_url}/share/{token}" if token else ""


# ── API helpers (run in threads, call back via Clock) ─────────────────────────

def api_get(api_key, base_url, path, params=None, on_done=None, on_error=None):
    def _run():
        try:
            r = requests.get(
                f"{base_url.rstrip('/')}{path}",
                headers={"Authorization": f"Bearer {api_key}"},
                params=params or {},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            if on_done:
                Clock.schedule_once(lambda dt: on_done(data))
        except Exception as e:
            if on_error:
                err = str(e)
                Clock.schedule_once(lambda dt: on_error(err))
    threading.Thread(target=_run, daemon=True).start()


def api_post(api_key, base_url, path, payload, on_done=None, on_error=None):
    def _run():
        try:
            r = requests.post(
                f"{base_url.rstrip('/')}{path}",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload, timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            if on_done:
                Clock.schedule_once(lambda dt: on_done(data))
        except Exception as e:
            if on_error:
                err = str(e)
                Clock.schedule_once(lambda dt: on_error(err))
    threading.Thread(target=_run, daemon=True).start()


def api_delete(api_key, base_url, path, params=None, on_done=None, on_error=None):
    def _run():
        try:
            r = requests.delete(
                f"{base_url.rstrip('/')}{path}",
                headers={"Authorization": f"Bearer {api_key}"},
                params=params or {},
                timeout=15,
            )
            r.raise_for_status()
            if on_done:
                Clock.schedule_once(lambda dt: on_done(r.json() if r.content else {}))
        except Exception as e:
            if on_error:
                err = str(e)
                Clock.schedule_once(lambda dt: on_error(err))
    threading.Thread(target=_run, daemon=True).start()


# ═════════════════════════════════════════════════════════════════════════════
# SCREENS
# ═════════════════════════════════════════════════════════════════════════════

# ── Upload Screen ─────────────────────────────────────────────────────────────

class UploadScreen(MDScreen):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app          = app
        self._task        = None
        self._file_path   = None
        self._dest_path   = "/"
        self._build_ui()

    def _build_ui(self):
        layout = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12))

        # ── File picker area ──
        pick_card = MDCard(
            orientation="vertical",
            padding=dp(16), spacing=dp(8),
            size_hint_y=None, height=dp(120),
            md_bg_color=C_CARD,
            elevation=4, radius=[12,12,12,12],
        )
        self._pick_label = MDLabel(
            text="No file selected",
            halign="center", theme_text_color="Custom",
            text_color=C_MUTED, font_style="Subtitle1",
        )
        pick_btn = MDRaisedButton(
            text="Choose File",
            md_bg_color=C_ACCENT,
            size_hint_x=None, width=dp(160),
            pos_hint={"center_x": 0.5},
            on_release=self._pick_file,
        )
        pick_card.add_widget(self._pick_label)
        pick_card.add_widget(pick_btn)
        layout.add_widget(pick_card)

        # ── Destination path ──
        self._dest_field = MDTextField(
            hint_text="Destination path on Mocha",
            text="/",
            size_hint_y=None, height=dp(48),
        )
        self._dest_field.bind(text=self._on_dest_changed)
        layout.add_widget(self._dest_field)

        # ── Share options ──
        share_row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(8))
        self._share_cb = MDCheckbox(size_hint_x=None, width=dp(40))
        share_row.add_widget(self._share_cb)
        share_row.add_widget(MDLabel(text="Create share link", theme_text_color="Custom", text_color=C_TEXT))

        self._expiry_label = MDLabel(
            text="Expiry: Never",
            size_hint_x=None, width=dp(120),
            theme_text_color="Custom", text_color=C_MUTED,
        )
        expiry_btn = MDFlatButton(
            text="v",
            on_release=self._open_expiry_menu,
            size_hint_x=None, width=dp(40),
        )
        share_row.add_widget(self._expiry_label)
        share_row.add_widget(expiry_btn)
        layout.add_widget(share_row)

        self._expiry = "Never"
        self._expiry_menu = MDDropdownMenu(
            items=[{"text": e, "on_release": lambda e=e: self._set_expiry(e)} for e in EXPIRY_OPTIONS],
            width_mult=3,
        )

        # ── Upload button ──
        self._upload_btn = MDRaisedButton(
            text="Upload",
            md_bg_color=C_ACCENT,
            size_hint_x=1, height=dp(48),
            on_release=self._start_upload,
        )
        layout.add_widget(self._upload_btn)

        # ── Progress ──
        self._progress_bar = MDProgressBar(value=0, size_hint_y=None, height=dp(6))
        layout.add_widget(self._progress_bar)

        prog_row = BoxLayout(size_hint_y=None, height=dp(24), spacing=dp(8))
        self._pct_label   = MDLabel(text="", theme_text_color="Custom", text_color=C_MUTED, size_hint_x=None, width=dp(50))
        self._speed_label = MDLabel(text="", theme_text_color="Custom", text_color=C_MUTED)
        prog_row.add_widget(self._pct_label)
        prog_row.add_widget(self._speed_label)
        layout.add_widget(prog_row)

        # ── Status log ──
        self._status_label = MDLabel(
            text="Ready",
            theme_text_color="Custom", text_color=C_MUTED,
            size_hint_y=None, height=dp(40),
            halign="center", font_style="Caption",
        )
        layout.add_widget(self._status_label)

        # ── Share result ──
        self._share_result = MDLabel(
            text="", theme_text_color="Custom", text_color=C_ACCENT,
            size_hint_y=None, height=dp(40),
            halign="center", font_style="Caption",
        )
        layout.add_widget(self._share_result)

        layout.add_widget(Widget())  # spacer
        self.add_widget(layout)

    def _pick_file(self, *a):
        # Request storage permissions first, then open the picker.
        def _do_pick():
            # Use Android's ACTION_OPEN_DOCUMENT directly via pyjnius.
            # This opens the full system file browser (not the restricted media
            # picker that plyer uses) and lets the user select any file type
            # from any location: internal storage, SD card, Downloads,
            # cloud providers, OTG drives, etc.
            # Falls back to plyer on non-Android platforms.
            try:
                from jnius import autoclass
                PythonActivity  = autoclass('org.kivy.android.PythonActivity')
                Intent          = autoclass('android.content.Intent')
                activity        = PythonActivity.mActivity

                intent = Intent(Intent.ACTION_OPEN_DOCUMENT)
                intent.addCategory(Intent.CATEGORY_OPENABLE)
                # "*/*" means ALL file types — no restriction
                intent.setType("*/*")
                # Grant persistent read permission for the returned URI so we
                # can open the file stream even after the picker closes.
                intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                intent.addFlags(Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)

                activity.startActivityForResult(intent, 0x4d43)   # 0x4d43 = "MC"

                # The result comes back via onActivityResult.  We hook into it
                # by polling the app's _last_pick_result which is set by the
                # on_activity_result handler registered below.
                self.app._pick_callback = self._on_file_chosen
                return
            except Exception:
                pass
            # Non-Android fallback (desktop testing)
            try:
                from plyer import filechooser
                filechooser.open_file(on_selection=self._on_file_chosen)
            except Exception as e:
                toast(f"File picker error: {e}")
        _request_storage_permission(callback=_do_pick)

    def _on_file_chosen(self, selection):
        if not selection:
            return
        path = selection[0]
        self._file_path = path
        original_name = None
        size = 0
        # If the filechooser returned a content:// URI, attempt to query metadata via pyjnius
        if path.startswith("content://"):
            try:
                from jnius import autoclass
                Uri = autoclass('android.net.Uri')
                PythonActivity = autoclass('org.kivy.android.PythonActivity')
                activity = PythonActivity.mActivity
                cr = activity.getContentResolver()
                uri = Uri.parse(path)
                cursor = cr.query(uri, None, None, None, None)
                if cursor is not None and cursor.moveToFirst():
                    idx_name = cursor.getColumnIndex("_display_name")
                    if idx_name != -1:
                        original_name = cursor.getString(idx_name)
                    idx_size = cursor.getColumnIndex("_size")
                    if idx_size != -1:
                        size = cursor.getLong(idx_size)
                    cursor.close()
            except Exception:
                original_name = None
                size = 0
        else:
            try:
                original_name = os.path.basename(path)
                size = os.path.getsize(path)
            except Exception:
                original_name = os.path.basename(path)
                size = 0

        display_name = original_name or os.path.basename(path)
        self._pick_label.text = f"{display_name}  ({fmt_size(size)})"
        # Auto-set dest path to /filename if not customised
        if self._dest_field.text.strip() in ("", "/"):
            self._dest_field.text = f"/{display_name}"

    def _on_dest_changed(self, instance, value):
        self._dest_path = value.strip() or "/"

    def _open_expiry_menu(self, btn):
        self._expiry_menu.caller = btn
        self._expiry_menu.open()

    def _set_expiry(self, val):
        self._expiry = val
        self._expiry_label.text = f"Expiry: {val}"
        self._expiry_menu.dismiss()

    def _start_upload(self, *a):
        api_key = self.app.api_key
        if not api_key:
            toast("Enter your API key in Settings first.")
            return
        if not self._file_path:
            toast("Select a file first.")
            return

        self._upload_btn.disabled = True
        self._progress_bar.value  = 0
        self._share_result.text   = ""

        stored = (self.app.store.get("settings").get("value", {}) if self.app.store.exists("settings") else {})
        chunk_mb = int(stored.get("chunk_mb", DEFAULT_CHUNK_SIZE_MB))
        max_chunks = int(stored.get("max_chunks", DEFAULT_MAX_CHUNKS))

        # Handle content:// URIs returned by plyer by copying to a temp file
        original_name = None
        cleanup_tmp = None
        if self._file_path.startswith("content://"):
            try:
                tmp_dir = App.get_running_app().user_data_dir
                os.makedirs(tmp_dir, exist_ok=True)
                tmp_path = os.path.join(tmp_dir, f"mocha_tmp_{int(time.time())}")
                from android.storage import primary_external_storage_path
                # Use pyjnius to open content resolver stream if available
                try:
                    from jnius import autoclass
                    PythonActivity = autoclass('org.kivy.android.PythonActivity')
                    Uri = autoclass('android.net.Uri')
                    activity = PythonActivity.mActivity
                    cr = activity.getContentResolver()
                    uri = Uri.parse(self._file_path)
                    cursor = cr.query(uri, None, None, None, None)
                    display_name = None
                    size = None
                    if cursor is not None and cursor.moveToFirst():
                        idx_name = cursor.getColumnIndex("_display_name")
                        if idx_name != -1:
                            display_name = cursor.getString(idx_name)
                        idx_size = cursor.getColumnIndex("_size")
                        if idx_size != -1:
                            size = cursor.getLong(idx_size)
                        cursor.close()
                    inp = cr.openInputStream(uri)
                    out_f = open(tmp_path, "wb")
                    buf = bytearray(65536)
                    while True:
                        read = inp.read(buf)
                        if read is None or read <= 0:
                            break
                        out_f.write(buf[:read])
                    out_f.close()
                    inp.close()
                    original_name = display_name or os.path.basename(self._file_path)
                    cleanup_tmp = tmp_path
                    self._file_path = tmp_path
                except Exception:
                    toast("Failed to copy content:// URI — try selecting via Files app instead.")
            except Exception:
                toast("Failed to prepare temporary file for upload.")

        self._task = UploadTask(
            api_key        = api_key,
            base_url       = HARDCODED_BASE_URL,
            local_path     = self._file_path,
            dest_path      = self._dest_path,
            create_share   = self._share_cb.active,
            share_expiry   = self._expiry,
            share_max_downloads = 0,
            on_progress    = self._on_progress,
            on_speed       = self._on_speed,
            on_status      = self._on_status,
            on_done        = self._on_done,
            on_error       = self._on_error,
            chunk_size_mb  = chunk_mb,
            max_chunks     = max_chunks,
            original_name  = original_name,
            cleanup_path   = cleanup_tmp,
        )
        self._task.start()

    def _on_progress(self, pct):
        self._progress_bar.value = pct
        self._pct_label.text     = f"{pct}%"

    def _on_speed(self, bps):
        if bps < 1024:
            self._speed_label.text = f"{bps:.0f} B/s"
        elif bps < 1024**2:
            self._speed_label.text = f"{bps/1024:.1f} KB/s"
        else:
            self._speed_label.text = f"{bps/1024**2:.2f} MB/s"

    def _on_status(self, msg):
        if not msg.startswith("[DEBUG]"):
            self._status_label.text = msg

    def _on_done(self, result):
        self._upload_btn.disabled = False
        self._status_label.text   = f"Done! File ID: {result['file_id']}"
        if result.get("share_url"):
            self._share_result.text = result["share_url"]
            toast("Upload complete — share link ready!")
        else:
            toast("Upload complete!")

    def _on_error(self, msg):
        self._upload_btn.disabled = False
        self._status_label.text   = f"Error: {msg}"
        toast(f"Upload failed: {msg}", duration=5)


# ── Files Screen ──────────────────────────────────────────────────────────────

class FilesScreen(MDScreen):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app          = app
        self._current     = "/"
        self._items_meta  = {}   # display_text → meta dict
        self._build_ui()

    def _build_ui(self):
        layout = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(8))

        # ── Path bar ──
        path_row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        self._path_field = MDTextField(
            text="/", hint_text="Path",
            size_hint_y=None, height=dp(48),
            size_hint_x=0.9
        )
        go_btn = MDIconButton(icon="arrow-right", on_release=lambda *a: self._navigate(self._path_field.text.strip() or "/"))
        up_btn = MDIconButton(icon="arrow-up",    on_release=lambda *a: self._go_up())
        path_row.add_widget(self._path_field)
        path_row.add_widget(go_btn)
        path_row.add_widget(up_btn)
        layout.add_widget(path_row)

        # ── Toolbar ──
        tb = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(4))
        for (label, cb) in [
            ("Refresh",    lambda *a: self._refresh()),
            ("New Folder", lambda *a: self._create_folder()),
            ("Delete",     lambda *a: self._delete_selected()),
        ]:
            tb.add_widget(MDRaisedButton(text=label, on_release=cb, height=dp(36), size_hint_y=None))
        self._status_lbl = MDLabel(text="", theme_text_color="Custom", text_color=C_MUTED)
        tb.add_widget(self._status_lbl)
        layout.add_widget(tb)

        # ── File list ──
        scroll = ScrollView()
        self._list = MDList()
        scroll.add_widget(self._list)
        layout.add_widget(scroll)

        self.add_widget(layout)

    def on_enter(self):
        self._refresh()

    def _refresh(self):
        self._navigate(self._current)

    def _navigate(self, path):
        if not self.app.api_key:
            toast("Enter your API key in Settings first.")
            return
        self._current = path
        self._path_field.text = path
        self._status_lbl.text = "Loading..."
        self._list.clear_widgets()
        self._items_meta = {}

        api_get(
            self.app.api_key, HARDCODED_BASE_URL,
            "/api/files",
            params={"path": path, "includeSubfolders": "0"},
            on_done=self._on_list_done,
            on_error=lambda e: toast(f"Error: {e}"),
        )

    def _on_list_done(self, data):
        self._list.clear_widgets()
        self._items_meta = {}

        raw_folders = []
        raw_files   = []
        if isinstance(data, dict):
            raw_folders = data.get("folders") or []
            raw_files   = data.get("files")   or []
        elif isinstance(data, list):
            raw_files = data

        # Add parent navigation if not at root
        if self._current != "/":
            item = OneLineListItem(text=".. (go up)", on_release=lambda *a: self._go_up())
            self._list.add_widget(item)

        for entry in raw_folders:
            if isinstance(entry, str):
                name = entry.rstrip("/").split("/")[-1]
                fullpath = (self._current.rstrip("/") + "/" + name) if self._current != "/" else ("/" + name)
            elif isinstance(entry, dict):
                name = entry.get("name") or entry.get("original_name") or ""
                fullpath = entry.get("path") or (self._current.rstrip("/") + "/" + name)
            else:
                continue
            if not name:
                continue
            key = f"[D] {name}"
            self._items_meta[key] = {"_type": "folder", "name": name, "path": fullpath}
            item = TwoLineListItem(
                text=key, secondary_text=fullpath,
                on_release=lambda *a, p=fullpath: self._navigate(p),
            )
            self._list.add_widget(item)

        for entry in (raw_files if isinstance(raw_files, list) else []):
            if not isinstance(entry, dict):
                continue
            name = (entry.get("originalName") or entry.get("original_name")
                    or entry.get("name") or entry.get("fileName") or "unknown")
            size = entry.get("size") or entry.get("fileSize") or 0
            fid  = entry.get("id") or entry.get("fileId") or ""
            key  = f"[F] {name}"
            self._items_meta[key] = {"_type": "file", "name": name, "id": fid, "size": size}
            item = TwoLineListItem(
                text=key, secondary_text=fmt_size(size),
                on_release=lambda *a, k=key: self._on_item_tap(k),
            )
            self._list.add_widget(item)

        count = len(raw_folders) + len(raw_files if isinstance(raw_files, list) else [])
        self._status_lbl.text = f"{count} items"

    def _on_item_tap(self, key):
        meta = self._items_meta.get(key, {})
        if meta.get("_type") == "folder":
            self._navigate(meta["path"])
        # Files: tap shows action menu
        else:
            self._show_file_menu(meta)

    def _show_file_menu(self, meta):
        name = meta.get("name", "")
        fid  = meta.get("id", "")

        def do_delete():
            confirm_dialog(
                "Delete file", f"Delete {name!r}?",
                on_yes=lambda: api_delete(
                    self.app.api_key, HARDCODED_BASE_URL,
                    f"/api/files/{fid}",
                    on_done=lambda _: (toast("Deleted."), self._refresh()),
                    on_error=lambda e: toast(f"Error: {e}"),
                )
            )

        def do_share():
            self._share_file(fid, name)

        def do_move():
            input_dialog("Move to folder", "Destination path", lambda dest: api_post(
                self.app.api_key, HARDCODED_BASE_URL,
                "/api/files/move",
                {"fileId": fid, "toPath": dest.rstrip("/") + "/"},
                on_done=lambda _: (toast("Moved."), self._refresh()),
                on_error=lambda e: toast(f"Error: {e}"),
            ))

        dialog = MDDialog(
            title=name,
            buttons=[
                MDFlatButton(text="Share",  on_release=lambda *a: (dialog.dismiss(), do_share())),
                MDFlatButton(text="Move",   on_release=lambda *a: (dialog.dismiss(), do_move())),
                MDRaisedButton(text="Delete", md_bg_color=C_ERROR,
                               on_release=lambda *a: (dialog.dismiss(), do_delete())),
                MDFlatButton(text="Cancel", on_release=lambda *a: dialog.dismiss()),
            ],
        )
        dialog.open()

    def _share_file(self, fid, name):
        items = [
            {"text": e, "on_release": lambda e=e, f=fid: self._do_share(f, e)}
            for e in EXPIRY_OPTIONS
        ]
        menu = MDDropdownMenu(items=items, width_mult=3)
        # Need a caller widget; use status label as anchor
        menu.caller = self._status_lbl
        menu.open()

    def _do_share(self, fid, expiry):
        expiry_hours = EXPIRY_LABEL_TO_HOURS.get(expiry)
        payload = {"fileId": fid}
        if expiry_hours:
            payload["expiresInHours"] = expiry_hours
        api_post(
            self.app.api_key, HARDCODED_BASE_URL, "/api/shares", payload,
            on_done=self._on_share_done,
            on_error=lambda e: toast(f"Share failed: {e}"),
        )

    def _on_share_done(self, data):
        token = data.get("token") or (data.get("share") or {}).get("token", "")
        url   = f"{HARDCODED_BASE_URL}/share/{token}" if token else ""
        if url:
            # Show the URL in a copyable dialog
            content = MDTextField(text=url, readonly=True, size_hint_y=None, height=dp(48))
            d = MDDialog(
                title="Share Link",
                type="custom",
                content_cls=content,
                buttons=[MDFlatButton(text="OK", on_release=lambda *a: d.dismiss())],
            )
            d.open()
        else:
            toast("Share created (no URL returned)")

    def _go_up(self):
        parts = self._current.strip("/").split("/")
        parent = "/" + "/".join(parts[:-1]) if len(parts) > 1 else "/"
        self._navigate(parent)

    def _create_folder(self):
        input_dialog(
            "New Folder", "Folder name",
            lambda name: self._do_mkdir(name) if name else None,
        )

    def _do_mkdir(self, name):
        path   = self._current.rstrip("/") + "/" + name.strip("/")
        parent = self._current
        api_post(
            self.app.api_key, HARDCODED_BASE_URL,
            "/api/files/folders",
            {"path": parent, "name": name},
            on_done=lambda _: (toast("Folder created."), self._refresh()),
            on_error=lambda e: toast(f"Error: {e}"),
        )

    def _delete_selected(self):
        # On mobile there's no multi-select tree; tap an item to get its menu.
        toast("Tap a file or folder to delete it.")


# ── Remote Screen ─────────────────────────────────────────────────────────────

class RemoteScreen(MDScreen):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self._build_ui()

    def _build_ui(self):
        # Outer scroll so nothing is ever clipped on small screens
        outer_scroll = ScrollView()
        outer_layout = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12),
                                 size_hint_y=None)
        outer_layout.bind(minimum_height=outer_layout.setter("height"))

        # ── Ingest card — no fixed height, expands to fit content ──
        card = MDCard(orientation="vertical", padding=dp(16), spacing=dp(10),
                      size_hint_y=None, md_bg_color=C_CARD, elevation=4,
                      radius=[12, 12, 12, 12])
        card.bind(minimum_height=card.setter("height"))

        self._url_field = MDTextField(hint_text="Source URL", size_hint_y=None, height=dp(56))
        self._name_field = MDTextField(hint_text="Filename (optional)", size_hint_y=None, height=dp(56))
        self._path_field = MDTextField(hint_text="Destination folder", text="/",
                                       size_hint_y=None, height=dp(56))

        ingest_btn = MDRaisedButton(
            text="Remote Ingest", md_bg_color=C_ACCENT,
            size_hint_x=1, size_hint_y=None, height=dp(46),
            on_release=self._start_ingest,
        )

        self._result_label = MDLabel(
            text="", theme_text_color="Custom", text_color=C_MUTED,
            size_hint_y=None, height=dp(32),
        )

        for w in (self._url_field, self._name_field, self._path_field,
                  ingest_btn, self._result_label):
            card.add_widget(w)
        outer_layout.add_widget(card)

        # ── Jobs toolbar ──
        jobs_tb = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        jobs_tb.add_widget(MDRaisedButton(
            text="Refresh Jobs", size_hint_y=None, height=dp(36),
            on_release=lambda *a: self._refresh_jobs(),
        ))
        self._active_only = MDCheckbox(active=True, size_hint_x=None, width=dp(40))
        jobs_tb.add_widget(self._active_only)
        jobs_tb.add_widget(MDLabel(
            text="Active only", theme_text_color="Custom", text_color=C_TEXT,
        ))
        self._jobs_status = MDLabel(text="", theme_text_color="Custom", text_color=C_MUTED)
        jobs_tb.add_widget(self._jobs_status)
        outer_layout.add_widget(jobs_tb)

        # ── Jobs list — fixed height scroll area ──
        jobs_scroll = ScrollView(size_hint_y=None, height=dp(300))
        self._jobs_list = MDList()
        jobs_scroll.add_widget(self._jobs_list)
        outer_layout.add_widget(jobs_scroll)

        outer_scroll.add_widget(outer_layout)
        self.add_widget(outer_scroll)

    def _start_ingest(self, *a):
        if not self.app.api_key:
            toast("Enter your API key in Settings first.")
            return
        src = self._url_field.text.strip()
        if not src:
            toast("Enter a source URL.")
            return
        name = self._name_field.text.strip() or src.rstrip("/").split("/")[-1]
        path = self._path_field.text.strip() or "/"
        self._result_label.text = "Submitting..."
        api_post(
            self.app.api_key, HARDCODED_BASE_URL,
            "/api/files/remote-download",
            {"sourceUrl": src, "fileName": name, "path": path},
            on_done=self._on_ingest_done,
            on_error=lambda e: (setattr(self._result_label, "text", f"Error: {e}"), toast(f"Ingest failed: {e}")),
        )

    def _on_ingest_done(self, data):
        self._result_label.text = f"Job submitted"
        toast("Remote ingest started!")
        self._refresh_jobs()

    def _refresh_jobs(self):
        params = {"active": "true"} if self._active_only.active else {}
        api_get(
            self.app.api_key, HARDCODED_BASE_URL,
            "/api/admin/transfer-jobs",
            params=params,
            on_done=self._on_jobs_done,
            on_error=lambda e: toast(f"Error: {e}"),
        )

    def _on_jobs_done(self, data):
        self._jobs_list.clear_widgets()
        jobs = data if isinstance(data, list) else data.get("jobs", [])
        for job in jobs:
            jid    = job.get("id", "")
            status = job.get("status", "")
            url    = job.get("sourceUrl") or job.get("url") or ""
            item = TwoLineListItem(
                text=f"{status}  {jid[:12]}",
                secondary_text=url[:60],
                on_release=lambda *a, j=jid: self._cancel_job(j),
            )
            self._jobs_list.add_widget(item)
        self._jobs_status.text = f"{len(jobs)} job(s)"

    def _cancel_job(self, job_id):
        confirm_dialog(
            "Cancel job", f"Cancel job {job_id[:12]}?",
            on_yes=lambda: api_delete(
                self.app.api_key, HARDCODED_BASE_URL,
                "/api/admin/transfer-jobs",
                params={"id": job_id},
                on_done=lambda _: (toast("Job cancelled."), self._refresh_jobs()),
                on_error=lambda e: toast(f"Error: {e}"),
            )
        )


# ── Shares Screen ─────────────────────────────────────────────────────────────

class SharesScreen(MDScreen):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self._build_ui()

    def _build_ui(self):
        layout = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(8))

        tb = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        tb.add_widget(MDRaisedButton(text="Refresh", on_release=lambda *a: self._refresh()))
        self._status = MDLabel(text="", theme_text_color="Custom", text_color=C_MUTED)
        tb.add_widget(self._status)
        layout.add_widget(tb)

        scroll = ScrollView()
        self._list = MDList()
        scroll.add_widget(self._list)
        layout.add_widget(scroll)

        self.add_widget(layout)

    def on_enter(self):
        self._refresh()

    def _refresh(self):
        if not self.app.api_key:
            self._status.text = "No API key set."
            return
        self._status.text = "Loading..."
        self._list.clear_widgets()
        api_get(
            self.app.api_key, HARDCODED_BASE_URL,
            "/api/shares",
            on_done=self._on_done,
            on_error=lambda e: (setattr(self._status, "text", "Error loading shares."), toast(f"Shares error: {e}")),
        )

    def _on_done(self, data):
        self._list.clear_widgets()
        shares = data.get("shares", data) if isinstance(data, dict) else data
        if not isinstance(shares, list):
            shares = []
        self._status.text = f"{len(shares)} share(s)"
        for share in shares:
            token   = share.get("token", "")
            expires = share.get("expiresAt") or share.get("expires") or "Never"
            url     = f"{HARDCODED_BASE_URL}/share/{token}"
            name = (
                share.get("originalName")
                or share.get("fileName")
                or share.get("file_name")
                or share.get("name")
                or ""
            )
            display = name if name else token[:16]
            item = TwoLineListItem(
                text=display,
                secondary_text=f"Expires: {expires}  |  {url}",
                on_release=lambda *a, t=token, n=display: self._share_menu(t, n),
            )
            self._list.add_widget(item)
            if not name and token:
                self._fetch_share_detail(item, token)

    def _fetch_share_detail(self, item, token):
        """Background fetch of /api/shares/<token> to get the real filename."""
        def _run():
            try:
                r = requests.get(
                    f"{HARDCODED_BASE_URL}/api/shares/{token}",
                    headers={"Authorization": f"Bearer {self.app.api_key}"},
                    timeout=10,
                )
                r.raise_for_status()
                meta = r.json()
                share_meta = meta.get("share", meta)
                name = (
                    share_meta.get("originalName")
                    or share_meta.get("fileName")
                    or share_meta.get("file_name")
                    or share_meta.get("name")
                    or token[:16]
                )
                Clock.schedule_once(lambda dt: setattr(item, "text", name))
            except Exception:
                pass
        threading.Thread(target=_run, daemon=True).start()

    def _share_menu(self, token, name):
        url = f"{HARDCODED_BASE_URL}/share/{token}"
        dialog = MDDialog(
            title=name,
            text=url,
            buttons=[
                MDFlatButton(text="Delete", on_release=lambda *a: (dialog.dismiss(), self._delete(token))),
                MDFlatButton(text="Close",  on_release=lambda *a: dialog.dismiss()),
            ],
        )
        dialog.open()

    def _delete(self, token):
        confirm_dialog(
            "Delete share", "Delete this share link?",
            on_yes=lambda: api_delete(
                self.app.api_key, HARDCODED_BASE_URL,
                f"/api/shares/{token}",
                on_done=lambda _: (toast("Share deleted."), self._refresh()),
                on_error=lambda e: toast(f"Error: {e}"),
            )
        )


# ── Settings Screen ───────────────────────────────────────────────────────────

class SettingsScreen(MDScreen):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self._show_key = False
        self._build_ui()

    def _section_label(self, text):
        """Desktop-style uppercase section header — matches desktop's section_header style."""
        lbl = MDLabel(
            text=text.upper(),
            theme_text_color="Custom",
            text_color=C_MUTED,
            font_style="Caption",
            size_hint_y=None, height=dp(28),
        )
        return lbl

    def _build_ui(self):
        scroll = ScrollView()
        layout = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(14),
                           size_hint_y=None)
        layout.bind(minimum_height=layout.setter("height"))

        # ── Header ────────────────────────────────────────────────────────────
        layout.add_widget(MDLabel(
            text="Settings",
            font_style="H5",
            theme_text_color="Custom", text_color=C_TEXT,
            size_hint_y=None, height=dp(44),
        ))

        # ══ API ═══════════════════════════════════════════════════════════════
        layout.add_widget(self._section_label("API"))

        api_card = MDCard(
            orientation="vertical", padding=dp(14), spacing=dp(8),
            size_hint_y=None, height=dp(210),
            md_bg_color=C_CARD, elevation=2, radius=[8, 8, 8, 8],
        )

        # Label row
        api_card.add_widget(MDLabel(
            text="API key",
            theme_text_color="Custom", text_color=C_MUTED,
            font_style="Caption",
            size_hint_y=None, height=dp(20),
        ))

        # Key field: native TextInput so Android long-press gives the system
        # Paste / Select All context menu without extra code.
        stored = (self.app.store.get("settings").get("value", {})
                  if self.app.store.exists("settings") else {})
        self._key_field = TextInput(
            hint_text="mocha_your_api_key_here",
            password=True,
            text=self.app.api_key,
            size_hint_y=None, height=dp(48),
            background_color=(30/255, 28/255, 25/255, 1),
            foreground_color=(240/255, 236/255, 230/255, 1),
            cursor_color=(200/255, 169/255, 110/255, 1),
            hint_text_color=(90/255, 86/255, 80/255, 1),
            font_size=dp(14),
            padding=[dp(10), dp(12), dp(10), dp(12)],
            multiline=False,
            use_bubble=True,
            use_handles=True,
        )
        api_card.add_widget(self._key_field)

        # Button row: Paste | Show/Hide | Save
        # Paste is gold so it stands out clearly against the dark card.
        btn_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))

        paste_btn = MDRaisedButton(
            text="Paste",
            md_bg_color=C_ACCENT,
            theme_text_color="Custom",
            text_color=(17/255, 16/255, 16/255, 1),
            size_hint_x=None, width=dp(100),
            on_release=self._paste_key,
        )
        self._show_btn = MDRaisedButton(
            text="Show",
            md_bg_color=C_SURFACE,
            theme_text_color="Custom", text_color=C_MUTED,
            size_hint_x=None, width=dp(80),
            on_release=self._toggle_show_key,
        )
        save_btn = MDRaisedButton(
            text="Save",
            md_bg_color=C_ACCENT,
            size_hint_x=None, width=dp(100),
            on_release=self._save,
        )
        btn_row.add_widget(paste_btn)
        btn_row.add_widget(self._show_btn)
        btn_row.add_widget(Widget())
        btn_row.add_widget(save_btn)
        api_card.add_widget(btn_row)

        layout.add_widget(api_card)

        # ══ MULTIPART UPLOAD ══════════════════════════════════════════════════
        layout.add_widget(self._section_label("Multipart Upload"))

        chunk_card = MDCard(
            orientation="vertical", padding=dp(14), spacing=dp(8),
            size_hint_y=None, height=dp(180),
            md_bg_color=C_CARD, elevation=2, radius=[8, 8, 8, 8],
        )

        note = MDLabel(
            text="Larger chunks reduce overhead; more parallel chunks can increase throughput on fast connections.",
            theme_text_color="Custom", text_color=C_MUTED,
            font_style="Caption",
            size_hint_y=None, height=dp(32),
        )
        chunk_card.add_widget(note)

        # Chunk size row
        cs_row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        cs_row.add_widget(MDLabel(
            text="Chunk size (MB):",
            theme_text_color="Custom", text_color=C_TEXT,
            size_hint_x=None, width=dp(150),
        ))
        self._chunk_field = MDTextField(
            text=str(stored.get("chunk_mb", DEFAULT_CHUNK_SIZE_MB)),
            input_filter="int",
            size_hint_y=None, height=dp(48),
        )
        cs_row.add_widget(self._chunk_field)
        chunk_card.add_widget(cs_row)

        # Max parallel chunks row
        mc_row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        mc_row.add_widget(MDLabel(
            text="Max parallel chunks:",
            theme_text_color="Custom", text_color=C_TEXT,
            size_hint_x=None, width=dp(150),
        ))
        self._max_chunks_field = MDTextField(
            text=str(stored.get("max_chunks", DEFAULT_MAX_CHUNKS)),
            input_filter="int",
            size_hint_y=None, height=dp(48),
        )
        mc_row.add_widget(self._max_chunks_field)
        chunk_card.add_widget(mc_row)

        layout.add_widget(chunk_card)
        layout.add_widget(Widget(size_hint_y=None, height=dp(20)))

        scroll.add_widget(layout)
        self.add_widget(scroll)

    def _paste_key(self, *a):
        """Paste from Android clipboard into the API key field."""
        text = _android_paste()
        if text:
            self._key_field.text = text.strip()
            toast("Pasted.")
        else:
            toast("Clipboard is empty.")

    def _toggle_show_key(self, *a):
        self._show_key = not self._show_key
        self._key_field.password = not self._show_key
        self._show_btn.text = "Hide" if self._show_key else "Show"
        try:
            self._key_field._trigger_refresh_text()
        except Exception:
            pass

    def _save(self, *a):
        key = self._key_field.text.strip()
        self.app.api_key = key
        try:
            chunk_mb = int(self._chunk_field.text.strip() or DEFAULT_CHUNK_SIZE_MB)
        except Exception:
            toast("Invalid chunk size — must be an integer.")
            return
        try:
            max_chunks = int(self._max_chunks_field.text.strip() or DEFAULT_MAX_CHUNKS)
        except Exception:
            toast("Invalid max chunks — must be an integer.")
            return
        chunk_mb = max(1, min(chunk_mb, 100))
        max_chunks = max(1, min(max_chunks, 20))
        self.app.store.put("settings", value={
            "api_key": key, "chunk_mb": chunk_mb, "max_chunks": max_chunks,
        })
        toast("Settings saved.")


# ═════════════════════════════════════════════════════════════════════════════
# MAIN APP
# ═════════════════════════════════════════════════════════════════════════════

class MochaToolsApp(MDApp):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.title          = "Mocha Tools"
        self.api_key        = ""
        self.store          = None
        self._pick_callback = None   # set by UploadScreen._pick_file before startActivityForResult

    def build(self):
        # Theme — match desktop palette as closely as KivyMD 1.2.0 allows
        self.theme_cls.theme_style     = "Dark"
        self.theme_cls.primary_palette = "Amber"
        self.theme_cls.primary_hue     = "700"
        self.theme_cls.accent_palette  = "Amber"
        self.theme_cls.accent_hue      = "200"

        # Kick off a storage permission request on startup so the user doesn't
        # hit a permission error the first time they try to pick a file.
        Clock.schedule_once(lambda dt: _request_storage_permission(), 1)

        # Persistent storage
        self.store = JsonStore("mochatools_settings.json")
        if self.store.exists("settings"):
            saved = self.store.get("settings").get("value", {})
            self.api_key = saved.get("api_key", "")

        # Bottom navigation
        nav = MDBottomNavigation(panel_color=C_CARD[:-1] + (1,))

        upload_screen = MDBottomNavigationItem(name="upload",   text="Upload", icon="upload")
        upload_screen.add_widget(UploadScreen(self, name="upload_inner"))

        files_screen = MDBottomNavigationItem(name="files",    text="Files",  icon="folder")
        files_screen.add_widget(FilesScreen(self, name="files_inner"))

        remote_screen = MDBottomNavigationItem(name="remote",  text="Remote", icon="download-network")
        remote_screen.add_widget(RemoteScreen(self, name="remote_inner"))

        shares_screen = MDBottomNavigationItem(name="shares",  text="Shares", icon="share-variant")
        shares_screen.add_widget(SharesScreen(self, name="shares_inner"))

        settings_screen = MDBottomNavigationItem(name="settings", text="Settings", icon="cog")
        settings_screen.add_widget(SettingsScreen(self, name="settings_inner"))

        for s in (upload_screen, files_screen, remote_screen, shares_screen, settings_screen):
            nav.add_widget(s)

        # Auto-load shares 2s after startup so the tab is populated before the user visits it
        def _preload_shares(dt):
            for screen in nav.children:
                for child in getattr(screen, 'children', []):
                    if isinstance(child, SharesScreen):
                        child._refresh()
                        return
        Clock.schedule_once(_preload_shares, 2)

        return nav

    def on_activity_result(self, request_code, result_code, intent):
        """
        Called by Kivy/p4a when startActivityForResult() returns.
        We use request code 0x4d43 ("MC") for our file picker.
        result_code -1 == RESULT_OK on Android.
        """
        RESULT_OK    = -1
        REQUEST_CODE = 0x4d43
        if request_code != REQUEST_CODE:
            return
        if result_code != RESULT_OK or intent is None:
            # User cancelled — clear the waiting callback
            self._pick_callback = None
            return
        try:
            uri = intent.getData()
            if uri is None:
                toast("No file was returned by the picker.")
                self._pick_callback = None
                return

            # Take persistable read permission so we can stream the file later
            try:
                from jnius import autoclass
                PythonActivity = autoclass('org.kivy.android.PythonActivity')
                Intent = autoclass('android.content.Intent')
                activity = PythonActivity.mActivity
                activity.getContentResolver().takePersistableUriPermission(
                    uri, Intent.FLAG_GRANT_READ_URI_PERMISSION
                )
            except Exception:
                pass

            uri_str = uri.toString()
            cb = self._pick_callback
            self._pick_callback = None
            if cb:
                # Dispatch back to main thread — _on_file_chosen expects a list
                Clock.schedule_once(lambda dt: cb([uri_str]))
        except Exception as e:
            toast(f"File picker result error: {e}")
            self._pick_callback = None


if __name__ == "__main__":
    MochaToolsApp().run()
