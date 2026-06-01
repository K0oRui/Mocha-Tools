import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from PySide6.QtCore import QThread, Signal as pyqtSignal

from .constants import (
    CHUNK_SIZE,
    DEFAULT_CHUNK_SIZE_MB,
    DEFAULT_MAX_CHUNKS,
    PART_UPLOAD_RETRIES,
    PART_UPLOAD_TIMEOUT,
    RELAY_DEFAULT_CONCURRENCY,
    RELAY_MAX_CONCURRENCY,
    S3_DEFAULT_CONCURRENCY,
    S3_MAX_CONCURRENCY,
)
from .logging_utils import write_debug_log


# ── Progress Tracker ─────────────────────────────────────────────────────────
class ProgressTracker:
    """Thread-safe byte counter shared across all parallel upload workers.

    Each worker calls feed(n) as bytes leave the socket. The tracker
    accumulates totals and fires progress/speed callbacks at most once
    every EMIT_INTERVAL seconds so the UI isn't flooded.
    """
    EMIT_INTERVAL = 0.25   # seconds between UI updates

    def __init__(self, total_bytes, on_progress, on_speed):
        self._total     = total_bytes
        self._sent      = 0             # bytes confirmed sent
        self._lock      = threading.Lock()
        self._start     = time.monotonic()
        self._last_emit = 0.0
        self._on_prog   = on_progress   # callable(int pct)
        self._on_speed  = on_speed      # callable(float bps)

    def feed(self, n_bytes):
        """Called by upload threads as bytes leave the socket."""
        with self._lock:
            self._sent += n_bytes
            now     = time.monotonic()
            elapsed = max(now - self._start, 0.001)
            if now - self._last_emit >= self.EMIT_INTERVAL:
                self._last_emit = now
                pct = min(int(self._sent / self._total * 100), 99)
                bps = self._sent / elapsed
                self._on_prog(pct)
                self._on_speed(bps)

    def finish(self):
        """Call once when all parts are done to snap to 100%."""
        with self._lock:
            elapsed = max(time.monotonic() - self._start, 0.001)
            bps     = self._sent / elapsed
        self._on_prog(100)
        self._on_speed(bps)

    def make_streaming_body(self, chunk: bytes, read_size: int = 65536):
        class ChunkStream:
            def __init__(self, chunk_bytes: bytes, tracker, block_size: int):
                self.chunk = chunk_bytes
                self.tracker = tracker
                self.block_size = block_size
                self.offset = 0
                self.length = len(chunk_bytes)
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


# ── Upload Worker ────────────────────────────────────────────────────────────
class UploadWorker(QThread):
    progress    = pyqtSignal(int)          # 0-100
    speed       = pyqtSignal(float)        # bytes/sec
    status      = pyqtSignal(str)          # log message
    finished    = pyqtSignal(dict)         # result dict
    error       = pyqtSignal(str)

    def __init__(self, api_key, base_url, file_pairs,
                 create_share, share_expiry, share_max_downloads,
                 chunk_size_mb=None, max_chunks=None):
        """
        file_pairs: list of (local_abs_path, remote_dest_path) tuples.
        remote_dest_path is already the full absolute path on Mocha,
        e.g. '/Music/Album/CD1/track.flac'.
        chunk_size_mb: size of each multipart chunk in MB (1–100).
        max_chunks: maximum number of in-flight parallel chunks (1–20).
        """
        super().__init__()
        self.api_key             = api_key
        self.base_url            = base_url.rstrip("/")
        self.file_pairs          = file_pairs          # [(local, dest), ...]
        self.create_share        = create_share
        self.share_expiry_hours  = share_expiry  # int hours or None
        self.share_max_downloads = share_max_downloads
        # Chunk config — clamp to valid ranges
        mb = int(chunk_size_mb) if chunk_size_mb is not None else DEFAULT_CHUNK_SIZE_MB
        self._chunk_size  = max(1, min(mb, 100)) * 1024 * 1024  # bytes
        mc = int(max_chunks) if max_chunks is not None else DEFAULT_MAX_CHUNKS
        self._max_chunks  = max(1, min(mc, 20))
        self._cancel             = False

    def cancel(self):
        self._cancel = True

    def _headers(self, file_name=None):
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if file_name:
            headers["x-file-name"] = file_name
        return headers

    # ── helpers ──────────────────────────────────────────────────────────────

    def run(self):
        total_files  = len(self.file_pairs)
        last_file_id = None
        last_share_url = None

        # ── Pre-create every unique destination directory ──────────────────────
        dest_dirs = sorted({
            "/".join(dest.rstrip("/").split("/")[:-1]) or "/"
            for _, dest in self.file_pairs
        })
        for d in dest_dirs:
            if d == "/":
                continue
            self.status.emit(f"[DEBUG] Creating folder: {d}")
            try:
                self._ensure_folder(d)
            except Exception as e:
                self.error.emit(f"Failed to create folder {d!r}: {e}")
                return

        # ── Upload each file directly into its destination folder ──────────────
        for idx, (local_path, dest_path) in enumerate(self.file_pairs, 1):
            if self._cancel:
                return

            file_name = os.path.basename(local_path)
            prefix    = f"[{idx}/{total_files}] " if total_files > 1 else ""

            try:
                file_size = os.path.getsize(local_path)

                if file_size == 0:
                    self.status.emit(f"{prefix}{file_name}  ⊘ Skipped (empty file)")
                    self.status.emit(f"[DEBUG] Skipped empty file: {local_path}")
                    continue

                self.status.emit(f"{prefix}{file_name}  ({self._fmt_size(file_size)})")
                self.status.emit(f"[DEBUG] Local path: {local_path}")
                self.status.emit(f"[DEBUG] Remote dest: {dest_path}")
                self.status.emit(f"[DEBUG] File size (bytes): {file_size}")

                self.status.emit("[DEBUG] Strategy: multipart upload")
                file_id = self._multipart_upload(file_size, local_path, dest_path)

                if self._cancel or file_id is None:
                    return

                self.status.emit(f"[DEBUG] File ID returned to run(): {file_id}")
                last_file_id = file_id

                if self.create_share and idx == total_files:
                    self.status.emit("Creating share link…")
                    last_share_url = self._create_share(file_id)
                    self.status.emit(f"Share: {last_share_url}")

            except Exception as e:
                self.error.emit(f"{prefix}{file_name}: {e}")
                return

        self.finished.emit({"file_id": last_file_id, "share_url": last_share_url})

    # ── multipart upload ─────────────────────────────────────────────────────
    def _multipart_upload(self, file_size, local_path, dest_path):
        import mimetypes

        file_name = os.path.basename(local_path)
        mime_type, _ = mimetypes.guess_type(local_path)
        mime_type = mime_type or "application/octet-stream"

        dest_folder = "/".join(dest_path.rstrip("/").split("/")[:-1]) or "/"

        # Init
        init_resp = requests.post(
            f"{self.base_url}/api/files/multipart/init",
            headers={**self._headers(), "Content-Type": "application/json"},
            json={
                "fileName": file_name,
                "fileSize": file_size,
                "mimeType": mime_type,
                "path": dest_folder,
            },
            timeout=30,
        )
        init_resp.raise_for_status()
        init_data  = init_resp.json()
        upload_id  = init_data.get("uploadId") or init_data.get("upload_id")
        file_id    = (init_data.get("fileId") or init_data.get("file_id")
                      or (init_data.get("file") or {}).get("id"))

        self.status.emit(f"[DEBUG] Multipart init — uploadId={upload_id} fileId={file_id}")

        # Parts
        num_parts = math.ceil(file_size / self._chunk_size)
        tracker   = ProgressTracker(file_size, self.progress.emit, self.speed.emit)
        etags     = {}

        def upload_part(part_number):
            if self._cancel:
                return None, None
            offset = (part_number - 1) * self._chunk_size
            with open(local_path, "rb") as fh:
                fh.seek(offset)
                chunk = fh.read(self._chunk_size)

            body = tracker.make_streaming_body(chunk)
            for attempt in range(1, PART_UPLOAD_RETRIES + 1):
                try:
                    url_resp = requests.post(
                        f"{self.base_url}/api/files/multipart/part-url",
                        headers={**self._headers(), "Content-Type": "application/json"},
                        json={"uploadId": upload_id, "fileId": file_id,
                              "partNumber": part_number},
                        timeout=30,
                    )
                    url_resp.raise_for_status()
                    part_url = url_resp.json().get("url") or url_resp.json().get("uploadUrl")

                    body.offset = 0  # reset stream for retry
                    put_resp = requests.put(
                        part_url, data=body,
                        headers={"Content-Length": str(len(chunk))},
                        timeout=PART_UPLOAD_TIMEOUT,
                    )
                    put_resp.raise_for_status()
                    etag = put_resp.headers.get("ETag", "")
                    return part_number, etag
                except Exception as exc:
                    if attempt == PART_UPLOAD_RETRIES:
                        raise
                    self.status.emit(f"[DEBUG] Part {part_number} attempt {attempt} failed: {exc}")
                    time.sleep(min(2 ** attempt, 30))
                    body.offset = 0

        with ThreadPoolExecutor(max_workers=self._max_chunks) as pool:
            futures = {pool.submit(upload_part, p): p for p in range(1, num_parts + 1)}
            for fut in as_completed(futures):
                if self._cancel:
                    break
                pn, etag = fut.result()
                if pn is not None:
                    etags[pn] = etag

        if self._cancel:
            try:
                requests.post(
                    f"{self.base_url}/api/files/multipart/abort",
                    headers={**self._headers(), "Content-Type": "application/json"},
                    json={"uploadId": upload_id, "fileId": file_id},
                    timeout=15,
                )
            except Exception:
                pass
            return None

        tracker.finish()

        # Complete
        parts_list = [{"partNumber": p, "etag": etags[p]}
                      for p in sorted(etags)]
        comp_resp = requests.post(
            f"{self.base_url}/api/files/multipart/complete",
            headers={**self._headers(), "Content-Type": "application/json"},
            json={"uploadId": upload_id, "fileId": file_id, "parts": parts_list},
            timeout=60,
        )
        comp_resp.raise_for_status()
        comp_data = comp_resp.json()
        self.status.emit(f"[DEBUG] Complete response: {comp_data}")

        returned_id = (comp_data.get("fileId") or comp_data.get("file_id")
                       or (comp_data.get("file") or {}).get("id") or file_id)
        return returned_id

    def _ensure_folder(self, path):
        parts  = path.rstrip("/").split("/")
        for i in range(1, len(parts) + 1):
            sub    = "/".join(parts[:i])
            parent = "/".join(parts[:i-1]) or "/"
            name   = parts[i-1]
            try:
                resp = requests.post(
                    f"{self.base_url}/api/files/folders",
                    headers={**self._headers(), "Content-Type": "application/json"},
                    json={"path": parent, "name": name},
                    timeout=15,
                )
                # 409 Conflict = folder already exists — that's fine
                if resp.status_code not in (200, 201, 409):
                    resp.raise_for_status()
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 409:
                    pass
                else:
                    raise

    def _create_share(self, file_id):
        payload = {"fileId": file_id}
        if self.share_expiry_hours is not None:
            payload["expiresInHours"] = self.share_expiry_hours
        if self.share_max_downloads > 0:
            payload["maxDownloads"] = self.share_max_downloads
        resp = requests.post(
            f"{self.base_url}/api/shares",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        data  = resp.json()
        token = data.get("token") or (data.get("share") or {}).get("token", "")
        return f"{self.base_url}/share/{token}" if token else ""

    @staticmethod
    def _fmt_size(n):
        if n < 1024:
            return f"{n} B"
        if n < 1024**2:
            return f"{n/1024:.1f} KB"
        if n < 1024**3:
            return f"{n/1024**2:.2f} MB"
        return f"{n/1024**3:.2f} GB"


# ── Files Worker ─────────────────────────────────────────────────────────────
class FilesWorker(QThread):
    done  = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, op, api_key, base_url, **kwargs):
        super().__init__()
        self.op       = op
        self.api_key  = api_key
        self.base_url = base_url.rstrip("/")
        self.kwargs   = kwargs

    def _h(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def run(self):
        try:
            if self.op == "list":
                self._list()
            elif self.op == "delete":
                self._delete()
            elif self.op == "delete_folder":
                self._delete_folder()
            elif self.op == "move":
                self._move()
            elif self.op == "share":
                self._share()
            elif self.op == "mkdir":
                self._mkdir()
            elif self.op == "shares":
                self._list_shares()
            elif self.op == "delete_shares":
                self._delete_shares()
        except Exception as e:
            self.error.emit(str(e))

    def _list(self):
        path = self.kwargs.get("path", "/")
        resp = requests.get(
            f"{self.base_url}/api/files",
            headers={"Authorization": f"Bearer {self.api_key}"},
            params={"path": path, "includeSubfolders": "0"},
            timeout=15,
        )
        resp.raise_for_status()
        self.done.emit({"op": "list", "path": path, "data": resp.json()})

    def _delete(self):
        file_id = self.kwargs.get("file_id")
        resp = requests.delete(
            f"{self.base_url}/api/files/{file_id}",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        self.done.emit({"op": "delete", "file_id": file_id})

    def _delete_folder(self):
        full_path = self.kwargs.get("path", "").rstrip("/")
        parts     = full_path.rsplit("/", 1)
        parent    = parts[0] or "/"
        name      = parts[1] if len(parts) > 1 else full_path.lstrip("/")
        try:
            resp = requests.delete(
                f"{self.base_url}/api/files/folders",
                headers=self._h(),
                json={"path": parent, "name": name},
                timeout=15,
            )
            resp.raise_for_status()
        except requests.HTTPError as e:
            write_debug_log(f"[DEBUG] Response content: {getattr(e.response, 'text', None)}")
            raise
        except Exception as e:
            write_debug_log(f"[DEBUG] Exception: {e}")
            raise

        self.done.emit({"op": "delete_folder", "path": full_path})

    def _move(self):
        file_id   = self.kwargs.get("file_id")
        is_folder = self.kwargs.get("is_folder", False)
        new_path  = self.kwargs["new_path"]
        to_path   = new_path if new_path.endswith("/") else new_path.rstrip("/") + "/"
        if is_folder:
            payload = {
                "folderPath": self.kwargs.get("source_path", ""),
                "toPath": to_path,
            }
        elif file_id:
            payload = {"fileId": file_id, "toPath": to_path}
        else:
            payload = {"sourcePath": self.kwargs.get("source_path", ""), "toPath": to_path}
        resp = requests.post(
            f"{self.base_url}/api/files/move",
            headers=self._h(),
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        self.done.emit({"op": "move", "new_path": new_path})

    _EXPIRY_LABEL_TO_HOURS = {
        "1h": 1, "6h": 6, "12h": 12,
        "1d": 24, "3d": 72, "7d": 168, "14d": 336, "30d": 720,
    }

    def _share(self):
        file_id      = self.kwargs["file_id"]
        expiry_label = self.kwargs.get("expiry", "Never")
        expiry_hours = self._EXPIRY_LABEL_TO_HOURS.get(expiry_label)
        max_dl       = self.kwargs.get("max_downloads", 0)
        payload      = {"fileId": file_id}
        if expiry_hours is not None:
            payload["expiresInHours"] = expiry_hours
        if max_dl > 0:
            payload["maxDownloads"] = max_dl
        resp = requests.post(
            f"{self.base_url}/api/shares",
            headers=self._h(),
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        data  = resp.json()
        token = data.get("token") or (data.get("share") or {}).get("token", "")
        url   = f"{self.base_url}/share/{token}" if token else ""
        self.done.emit({"op": "share", "url": url, "token": token})

    def _mkdir(self):
        full_path = self.kwargs["path"].rstrip("/")
        parts     = full_path.rsplit("/", 1)
        parent    = parts[0] or "/"
        name      = parts[1] if len(parts) > 1 else full_path.lstrip("/")
        resp = requests.post(
            f"{self.base_url}/api/files/folders",
            headers=self._h(),
            json={"path": parent, "name": name},
            timeout=15,
        )
        resp.raise_for_status()
        self.done.emit({"op": "mkdir", "path": full_path})

    def _delete_shares(self):
        tokens  = self.kwargs.get("tokens", [])
        deleted = 0
        errors  = []
        for token in tokens:
            try:
                resp = requests.delete(
                    f"{self.base_url}/api/shares/{token}",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=15,
                )
                resp.raise_for_status()
                deleted += 1
            except Exception as e:
                errors.append(f"{token}: {e}")
        self.done.emit({
            "op":      "delete_shares",
            "deleted": deleted,
            "errors":  errors,
        })

    def _list_shares(self):
        resp = requests.get(
            f"{self.base_url}/api/shares",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        data   = resp.json()
        shares = data.get("shares", data) if isinstance(data, dict) else data

        if isinstance(shares, list):
            for share in shares:
                if not isinstance(share, dict):
                    continue
                token = share.get("token")
                if not token:
                    continue
                try:
                    meta_resp = requests.get(
                        f"{self.base_url}/api/shares/{token}",
                        timeout=15,
                    )
                    meta_resp.raise_for_status()
                    meta = meta_resp.json().get("share", {})
                except Exception:
                    continue

                original_name = (
                    meta.get("originalName") or meta.get("original_name")
                    or meta.get("fileName")  or meta.get("file_name")
                )
                if original_name:
                    share["originalName"] = original_name
                if meta.get("fileSize") is not None:
                    share["fileSize"] = meta.get("fileSize")
                if meta.get("mimeType"):
                    share["mimeType"] = meta.get("mimeType")

        self.done.emit({"op": "shares", "data": data})


# ── Remote Ingest Worker ─────────────────────────────────────────────────────
class RemoteWorker(QThread):
    done  = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, op, api_key, base_url, **kwargs):
        super().__init__()
        self.op       = op
        self.api_key  = api_key
        self.base_url = base_url.rstrip("/")
        self.kwargs   = kwargs

    def _h(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def run(self):
        try:
            if self.op == "ingest":
                self._ingest()
            elif self.op == "jobs":
                self._jobs()
            elif self.op == "cancel":
                self._cancel()
        except Exception as e:
            self.error.emit(str(e))

    def _ingest(self):
        payload = {
            "sourceUrl": self.kwargs["source_url"],
            "fileName":  self.kwargs["file_name"],
            "path":      self.kwargs["path"],
        }
        resp = requests.post(
            f"{self.base_url}/api/files/remote-download",
            headers=self._h(),
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        self.done.emit({"op": "ingest", "data": data})

    def _jobs(self):
        params = {"active": "true"} if self.kwargs.get("active_only", True) else {}
        resp = requests.get(
            f"{self.base_url}/api/admin/transfer-jobs",
            headers={"Authorization": f"Bearer {self.api_key}"},
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        self.done.emit({"op": "jobs", "data": resp.json()})

    def _cancel(self):
        job_id = self.kwargs["job_id"]
        resp = requests.delete(
            f"{self.base_url}/api/admin/transfer-jobs",
            headers={"Authorization": f"Bearer {self.api_key}"},
            params={"id": job_id},
            timeout=15,
        )
        resp.raise_for_status()
        self.done.emit({"op": "cancel", "job_id": job_id, "data": resp.json()})
