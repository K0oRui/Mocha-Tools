import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from PyQt6.QtCore import QThread, pyqtSignal

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
    SHARE_BASE_URL,
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

    def __init__(self, total_bytes, on_progress, on_speed, on_bytes_progress=None):
        self._total     = total_bytes
        self._sent      = 0             # bytes confirmed sent
        self._lock      = threading.Lock()
        self._start     = time.monotonic()
        self._last_emit = 0.0
        self._on_prog   = on_progress   # callable(int pct)
        self._on_speed  = on_speed      # callable(float bps)
        self._on_bytes  = on_bytes_progress  # callable(int done, int total) or None

    def feed(self, n_bytes):
        """Called by upload threads as bytes leave the socket."""
        with self._lock:
            self._sent = min(self._sent + n_bytes, self._total)
            now     = time.monotonic()
            elapsed = max(now - self._start, 0.001)
            if now - self._last_emit >= self.EMIT_INTERVAL:
                self._last_emit = now
                pct = min(self._sent / self._total * 100, 99.999)
                bps = self._sent / elapsed
                self._on_prog(pct)
                self._on_speed(bps)
                if self._on_bytes:
                    self._on_bytes(self._sent, self._total)

    def unfeed(self, n_bytes):
        """Subtract bytes that were fed for a part that is being retried,
        so the counter doesn't accumulate duplicate data."""
        with self._lock:
            self._sent = max(self._sent - n_bytes, 0)

    def finish(self):
        """Call once when all parts are done to snap to 100%."""
        with self._lock:
            elapsed = max(time.monotonic() - self._start, 0.001)
            bps     = self._sent / elapsed
            total   = self._total
        self._on_prog(100)
        self._on_speed(bps)
        if self._on_bytes:
            self._on_bytes(total, total)

    def make_streaming_body(self, chunk: bytes, read_size: int = 65536):
        class ChunkStream:
            def __init__(self, chunk_bytes: bytes, tracker, block_size: int):
                self.chunk = chunk_bytes
                self.tracker = tracker
                self.block_size = block_size
                self.offset = 0
                self.length = len(chunk_bytes)
                self.len = self.length
                self.fed = 0   # bytes fed to the tracker during this attempt

            def read(self, size=-1):
                if self.offset >= self.length:
                    return b""
                if size is None or size < 0:
                    size = self.block_size
                end = min(self.offset + size, self.length)
                piece = self.chunk[self.offset:end]
                if piece:
                    self.tracker.feed(len(piece))
                    self.fed += len(piece)
                    self.offset = end
                return piece

            def __len__(self):
                return self.length

        return ChunkStream(chunk, self, read_size)


# ── Upload Worker ────────────────────────────────────────────────────────────
class UploadWorker(QThread):
    progress        = pyqtSignal(float)                  # 0.0-100.0
    speed           = pyqtSignal(float)                  # bytes/sec
    bytes_progress  = pyqtSignal('qint64', 'qint64')     # (bytes_done, bytes_total) — 64-bit to handle files > 2 GB
    status          = pyqtSignal(str)          # log message
    finished        = pyqtSignal(dict)         # result dict
    error           = pyqtSignal(str)

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
        from urllib.parse import quote
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if file_name:
            # RFC 5987 encode so apostrophes/accents/etc don't corrupt the header
            headers["x-file-name"] = quote(file_name, safe="")
        return headers

    # ── helpers ──────────────────────────────────────────────────────────────

    # Files at or below this size skip multipart entirely and POST directly.
    _TIMEOUT = (5, 60)              # (connect_timeout, read_timeout)

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

        # ── Compute grand total bytes ─────────────────────────────────────────
        file_sizes: list[int] = []
        for local_path, _ in self.file_pairs:
            try:
                sz = os.path.getsize(local_path)
            except OSError:
                sz = 0
            file_sizes.append(sz)
        grand_total: int = sum(file_sizes)
        bytes_done_offset: int = 0

        # ── Upload each file ──────────────────────────────────────────────────
        for idx, (local_path, dest_path) in enumerate(self.file_pairs, 1):
            if self._cancel:
                return

            file_name = os.path.basename(local_path)
            prefix    = f"[{idx}/{total_files}] " if total_files > 1 else ""
            file_size = file_sizes[idx - 1]

            try:
                if file_size == 0:
                    self.status.emit(f"{prefix}{file_name}  ⊘ Skipped (empty file)")
                    continue

                self.status.emit(f"{prefix}{file_name}  ({self._fmt_size(file_size)})")
                self.status.emit(f"[DEBUG] Remote dest: {dest_path}")

                _offset = bytes_done_offset
                _grand  = grand_total

                def _on_bytes(done_this_file, _total_this_file,
                              offset=_offset, grand=_grand):
                    self.bytes_progress.emit(
                        min(offset + done_this_file, grand), grand
                    )

                self.status.emit("[DEBUG] Strategy: multipart upload")
                file_id = self._multipart_upload(
                    file_size, local_path, dest_path,
                    bytes_progress_cb=_on_bytes,
                )

                if self._cancel or file_id is None:
                    return

                bytes_done_offset += file_size
                last_file_id = file_id

                if self.create_share and idx == total_files:
                    self.status.emit("Creating share link…")
                    last_share_url = self._create_share(file_id)
                    self.status.emit(f"Share: {last_share_url}")

            except Exception as e:
                self.error.emit(f"{prefix}{file_name}: {e}")
                return

        self.finished.emit({"file_id": last_file_id, "share_url": last_share_url})




    # ── multipart upload (> 50 MB) ───────────────────────────────────────────
    def _multipart_upload(self, file_size, local_path, dest_path,
                          bytes_progress_cb=None):
        import mimetypes

        file_name = os.path.basename(local_path)
        dest_dir  = "/".join(dest_path.rstrip("/").split("/")[:-1]) or "/"
        dest_dir  = dest_dir.rstrip("/") + "/"
        mime_type = mimetypes.guess_type(local_path)[0] or "application/octet-stream"

        # ── Hold the file open for the whole upload ─────────────────────────
        # This is a "guard" handle: we never read from it, it exists purely
        # to keep an open file descriptor alive for the entire upload.
        #
        # On Windows, CPython's open() does NOT request FILE_SHARE_DELETE by
        # default, so simply having this handle open already makes Explorer
        # (and os.remove/os.rename from any process) fail with "the process
        # cannot access the file because it is being used by another
        # process" for as long as it's held — no special locking API needed.
        # Without this, each chunk in upload_part() below opens+closes its
        # own short-lived handle, leaving long windows between chunks (and
        # before the first chunk, during the multipart/init network
        # round-trip) where nothing is open and the file can be deleted or
        # moved out from under the upload, corrupting it part-way through.
        #
        # On Linux/macOS this same open() does NOT block delete/rename
        # (POSIX allows unlinking a file that's still open — the data stays
        # readable via existing handles until the last one closes), so this
        # is best-effort there: it still lets us detect a vanished/replaced
        # file via the stat check below, it just can't stop the deletion
        # itself.
        try:
            guard_fh = open(local_path, "rb")
        except OSError as e:
            raise RuntimeError(f"Couldn't open {file_name!r} for upload: {e}") from e

        try:
            try:
                guard_stat = os.fstat(guard_fh.fileno())
            except OSError:
                guard_stat = None

            def _verify_file_unchanged():
                """
                Best-effort check that the file we locked hasn't been
                replaced in-place (same path, different underlying file)
                since we opened the guard handle. Cheap — just an fstat,
                no extra I/O — so safe to call before every chunk read.
                """
                if guard_stat is None:
                    return
                try:
                    current = os.stat(local_path)
                except OSError as e:
                    raise RuntimeError(
                        f"{file_name} was deleted or moved during upload: {e}"
                    ) from e
                # On POSIX, st_ino/st_dev identify the same underlying file
                # even after a rename; on Windows these aren't reliable for
                # this purpose, so fall back to a size sanity-check there.
                if os.name != "nt":
                    if (current.st_ino, current.st_dev) != (guard_stat.st_ino, guard_stat.st_dev):
                        raise RuntimeError(
                            f"{file_name} was replaced during upload (different file at the same path)"
                        )
                elif current.st_size != guard_stat.st_size:
                    raise RuntimeError(
                        f"{file_name} changed size during upload "
                        f"(was {guard_stat.st_size} bytes, now {current.st_size})"
                    )

            return self._do_multipart_upload(
                file_size, local_path, dest_path, file_name, dest_dir, mime_type,
                bytes_progress_cb=bytes_progress_cb,
                verify_file_unchanged=_verify_file_unchanged,
            )
        finally:
            guard_fh.close()

    def _do_multipart_upload(self, file_size, local_path, dest_path, file_name,
                              dest_dir, mime_type, bytes_progress_cb=None,
                              verify_file_unchanged=None):

        # Debug: log request details (excluding sensitive data)
        url = f"{self.base_url}/api/files/multipart/init"
        payload = {
            "originalName": file_name,
            "path": dest_dir,
            "size": file_size,
            "mimeType": mime_type,
        }
        debug_headers = {**self._headers(), "Content-Type": "application/json"}
        debug_headers["Authorization"] = "(hidden)"
        self.status.emit(f"[DEBUG] Multipart init URL: {url}")
        self.status.emit(f"[DEBUG] Payload: {payload}")
        self.status.emit(f"[DEBUG] Headers: {debug_headers}")
        # Retry init on transient 5xx — concurrent mass uploads can cause
        # the server to return 500 when folder creation races or S3 is busy.
        init_resp = None
        last_init_error = None
        for _init_attempt in range(1, 6):
            if self._cancel:
                return None
            try:
                init_resp = requests.post(
                    url,
                    headers={**self._headers(), "Content-Type": "application/json"},
                    json=payload,
                    timeout=30,
                )
                init_resp.raise_for_status()
                last_init_error = None
                break
            except requests.HTTPError as e:
                status_code = getattr(e.response, 'status_code', None)
                self.status.emit(f"[DEBUG] HTTPError (init attempt {_init_attempt}/5): {e}")
                self.status.emit(f"[DEBUG] Response status: {status_code}")
                self.status.emit(f"[DEBUG] Response content: {getattr(e.response, 'text', None)}")
                last_init_error = e
                if status_code not in (429, 500, 502, 503, 504):
                    raise  # 4xx client errors are not retryable
                wait = min(2 ** (_init_attempt - 1), 10)
                self.status.emit(f"[DEBUG] Retrying multipart init in {wait}s…")
                time.sleep(wait)
            except Exception as e:
                self.status.emit(f"[DEBUG] Exception (init attempt {_init_attempt}/5): {e}")
                last_init_error = e
                time.sleep(min(2 ** (_init_attempt - 1), 10))
        if last_init_error is not None:
            raise last_init_error
        init_data  = init_resp.json()
        self.status.emit(f"[DEBUG] Init response: {init_data}")
        # Store the init response fields in one session payload so every
        # multipart request uses the same uploadId, key, nodeId, and path.
        # The backend uses those values to find the existing upload session.
        strategy   = init_data.get("strategy")
        upload_id  = init_data.get("uploadId")
        key        = init_data.get("key")
        node_id    = init_data.get("nodeId")
        direct     = init_data.get("directUploadEnabled") is not False

        if strategy not in ("s3", "webdav") or not upload_id or not key or not node_id:
            raise RuntimeError(f"Invalid multipart init response: {init_data}")

        session = {
            "strategy": strategy,
            "uploadId": upload_id,
            "key": key,
            "nodeId": node_id,
            "originalName": init_data.get("originalName") or file_name,
            "path": dest_dir,
            "size": file_size,
            "mimeType": mime_type,
        }

        # Use configured chunk size; max concurrent parts is capped by _max_chunks.
        # partSizeBytes from the server is the *maximum* allowed, not a requirement.
        chunk_size  = self._chunk_size
        total_parts = math.ceil(file_size / chunk_size)
        mode = "direct S3" if strategy == "s3" and direct else "server relay"
        concurrency = self._multipart_concurrency(init_data, total_parts, mode, self._max_chunks)
        self.status.emit(f"[DEBUG] Multipart upload: {total_parts} parts… (strategy={strategy}, mode={mode}, partSize={self._fmt_size(chunk_size)}, concurrency={concurrency})")
        self.status.emit(f"[DEBUG] Session: {upload_id}")

        # Shared progress tracker — fires UI updates as bytes leave the socket
        # across all parallel part workers rather than only on part completion.
        # Use the caller-supplied callback when available (run() injects a
        # cumulative wrapper so multi-file batches show the right grand total).
        _bytes_cb = bytes_progress_cb or (
            lambda done, total: self.bytes_progress.emit(done, total)
        )
        tracker = ProgressTracker(
            file_size,
            on_progress=lambda pct: self.progress.emit(pct),
            on_speed=lambda bps: self.speed.emit(bps),
            on_bytes_progress=_bytes_cb,
        )

        parts = []
        active_parts: set[int] = set()
        active_lock  = threading.Lock()

        # Each worker opens its own file handle and seeks to its part offset.
        # Sharing one file object across parallel uploads would race the read
        # position and corrupt the parts.
        def upload_part(part_num):
            with active_lock:
                active_parts.add(part_num)
            offset    = (part_num - 1) * chunk_size
            read_size = min(chunk_size, file_size - offset)
            if self._cancel:
                return None
            if verify_file_unchanged is not None:
                verify_file_unchanged()
            with open(local_path, "rb") as part_file:
                part_file.seek(offset)
                chunk = part_file.read(read_size)
            if self._cancel:
                return None
            self.status.emit(f"[DEBUG] Chunk size for part {part_num}: {len(chunk)} bytes")
            if strategy == "s3" and direct:
                etag = self._upload_part_s3(session, part_num, chunk, tracker)
            else:
                etag = self._upload_part_relay(session, part_num, chunk, tracker)
            return {"partNumber": part_num, "etag": etag, "size": len(chunk)}

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {
                executor.submit(upload_part, part_num): part_num
                for part_num in range(1, total_parts + 1)
            }
            # Give workers a moment to register then emit the initial in-flight set
            time.sleep(0.05)
            with active_lock:
                current = sorted(active_parts)
            if current:
                parts_str = " & ".join(f"part {p}" for p in current)
                self.status.emit(f"[DEBUG] Uploading {parts_str} out of {total_parts} total…")
            for future in as_completed(futures):
                if self._cancel:
                    self._stop_multipart_futures(futures, session, total_parts)
                    return None

                try:
                    result = future.result()
                except Exception:
                    self._cancel = True
                    self._stop_multipart_futures(futures, session, total_parts)
                    raise

                # If etag is None the part worker already aborted the session
                # and emitted an error — don't fall through to /complete.
                if result is None or result["etag"] is None:
                    self._stop_multipart_futures(futures, session, total_parts)
                    return None

                parts.append({"partNumber": result["partNumber"], "etag": result["etag"]})
                done = len(parts)

                with active_lock:
                    active_parts.discard(result["partNumber"])

        # 3. Complete
        complete_payload = {**session, "parts": sorted(parts, key=lambda part: part["partNumber"])}
        j = self._complete_multipart_upload(complete_payload)
        file_id = j.get("fileId") or j.get("id") or (j.get("file") or {}).get("id")
        self.status.emit(f"[DEBUG] Multipart complete. File ID: {file_id}")
        tracker.finish()
        return file_id

    def _complete_multipart_upload(self, payload):
        url = f"{self.base_url}/api/files/multipart/complete"
        last_error = None
        for attempt in range(1, 9):
            if self._cancel:
                return {}
            try:
                self.status.emit(f"[DEBUG] Completing multipart upload… attempt {attempt}/8")
                resp = requests.post(
                    url,
                    headers={**self._headers(), "Content-Type": "application/json"},
                    json=payload,
                    timeout=180,
                )
                self.status.emit(f"[DEBUG] Complete response status: {resp.status_code}")
                self.status.emit(f"[DEBUG] Complete response body: {resp.text[:500]}")
                resp.raise_for_status()
                return resp.json()
            except requests.HTTPError as e:
                last_error = e
                status = getattr(e.response, "status_code", None)
                body = getattr(e.response, "text", "") or ""
                if status not in (409, 423, 429, 500, 502, 503, 504, 524) and "524" not in body:
                    raise
                self.status.emit(f"[DEBUG] Multipart complete still pending/retryable ({status}): {body[:200]}")
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                last_error = e
                self.status.emit(f"[DEBUG] Multipart complete connection issue: {e}")

            wait_seconds = min(2 * attempt, 20)
            self.status.emit(f"[DEBUG] Waiting {wait_seconds}s before checking complete again…")
            time.sleep(wait_seconds)

        raise last_error

    @staticmethod
    def _multipart_concurrency(init_data, total_parts, mode, user_max_chunks=None):
        default = S3_DEFAULT_CONCURRENCY if mode == "direct S3" else RELAY_DEFAULT_CONCURRENCY
        maximum = S3_MAX_CONCURRENCY if mode == "direct S3" else RELAY_MAX_CONCURRENCY
        if user_max_chunks is not None:
            maximum = user_max_chunks
        value = init_data.get("partUploadConcurrency", default)
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(1, min(parsed, total_parts, maximum))

    @staticmethod
    def _cancel_futures(futures):
        for future in futures:
            future.cancel()

    def _abort_all_parts(self, session, total_parts):
        self._abort(session, list(range(1, total_parts + 1)))

    def _stop_multipart_futures(self, futures, session, total_parts):
        self._cancel_futures(futures)
        self._abort_all_parts(session, total_parts)

    def _wait_before_part_retry(self, label, part_num, attempt, error):
        if attempt >= PART_UPLOAD_RETRIES or not self._is_retryable_upload_error(error):
            raise error

        delay = min(2 ** (attempt - 1), 10)
        self.status.emit(f"[DEBUG] Retrying {label} part {part_num} after transient failure in {delay}s…")
        time.sleep(delay)

    def _upload_part_relay(self, session, part_num, chunk, tracker: "ProgressTracker"):
        """Upload one part through the Mocha relay."""
        part_url    = f"{self.base_url}/api/files/multipart/part"
        part_params = {
            "strategy": session["strategy"],
            "uploadId": session["uploadId"],
            "key": session["key"],
            "nodeId": session["nodeId"],
            "originalName": session["originalName"],
            "path": session["path"],
            "partNumber": part_num,
        }
        self.status.emit(f"[DEBUG] Part upload URL: {part_url}")
        self.status.emit(f"[DEBUG] Params: {part_params}")
        self.status.emit(f"[DEBUG] Headers: {{'Authorization': '(hidden)'}}")
        last_error = None
        with requests.Session() as http:
            for attempt in range(1, PART_UPLOAD_RETRIES + 1):
                if self._cancel:
                    return None
                body = None
                try:
                    body = tracker.make_streaming_body(chunk)
                    resp = http.put(
                        part_url,
                        headers=self._headers(),
                        params=part_params,
                        data=body,
                        timeout=PART_UPLOAD_TIMEOUT,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    etag = data.get("etag") or resp.headers.get("ETag", "")
                    if not etag:
                        raise RuntimeError(f"No ETag returned for part {part_num}: {data}")
                    return etag
                except requests.HTTPError as e:
                    self.status.emit(f"[DEBUG] HTTPError: {e}")
                    self.status.emit(f"[DEBUG] Response status: {getattr(e.response, 'status_code', None)}")
                    self.status.emit(f"[DEBUG] Response content: {getattr(e.response, 'text', None)}")
                    last_error = e
                except Exception as e:
                    self.status.emit(f"[DEBUG] Exception: {e}")
                    last_error = e

                # Subtract bytes this attempt fed so the retry doesn't double-count.
                # Guard against body never being assigned (e.g. exception before
                # make_streaming_body was called).
                if body is not None:
                    tracker.unfeed(body.fed)
                self._wait_before_part_retry("relay", part_num, attempt, last_error)

        raise last_error

    def _presign_part_url(self, session, part_num, http=None):
        # Step 1: ask Mocha for a presigned URL for this part
        presign_url     = f"{self.base_url}/api/files/multipart/presigned"
        # Send the full session context from init so the backend signs the URL
        # for the same object key and multipart upload session.
        presign_payload = {**session, "partNumbers": [part_num]}
        self.status.emit(f"[DEBUG] Presign URL: {presign_url}")
        self.status.emit(f"[DEBUG] Presign payload: {presign_payload}")
        request_client = http or requests
        try:
            presign_resp = request_client.post(
                presign_url,
                headers={**self._headers(), "Content-Type": "application/json"},
                json=presign_payload,
                timeout=30,
            )
            presign_resp.raise_for_status()
        except requests.HTTPError as e:
            self.status.emit(f"[DEBUG] HTTPError (presign): {e}")
            self.status.emit(f"[DEBUG] Response status: {getattr(e.response, 'status_code', None)}")
            self.status.emit(f"[DEBUG] Response content: {getattr(e.response, 'text', None)}")
            raise
        except Exception as e:
            self.status.emit(f"[DEBUG] Exception (presign): {e}")
            raise

        presign_data = presign_resp.json()
        signed_url = None
        if "url" in presign_data:
            signed_url = presign_data["url"]
        elif "presignedUrl" in presign_data:
            signed_url = presign_data["presignedUrl"]
        elif "urls" in presign_data and isinstance(presign_data["urls"], list):
            # Find the url for the current part_num
            for entry in presign_data["urls"]:
                if entry.get("partNumber") == part_num and "url" in entry:
                    signed_url = entry["url"]
                    break
        if not signed_url:
            raise RuntimeError(f"No presigned URL in response: {presign_data}")
        return signed_url

    @staticmethod
    def _is_retryable_upload_error(error):
        if isinstance(error, (requests.exceptions.SSLError, requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
            return True
        if not isinstance(error, requests.HTTPError):
            return False
        response = error.response
        status = getattr(response, "status_code", None)
        content = getattr(response, "text", "") if response is not None else ""
        retryable_codes = ("RequestTimeout", "SlowDown", "InternalError", "ServiceUnavailable")
        return status in (408, 429, 500, 502, 503, 504) or any(code in content for code in retryable_codes)

    def _upload_part_s3(self, session, part_num, chunk, tracker: "ProgressTracker"):
        """Upload one part directly to S3 via a presigned URL (strategy='s3')."""
        last_error = None
        with requests.Session() as http:
            for attempt in range(1, PART_UPLOAD_RETRIES + 1):
                if self._cancel:
                    return None
                body = None
                try:
                    signed_url = self._presign_part_url(session, part_num, http)
                    # Step 2: PUT the chunk directly to S3 (no auth header — the URL is pre-signed)
                    body = tracker.make_streaming_body(chunk)
                    s3_resp = http.put(
                        signed_url,
                        data=body,
                        timeout=PART_UPLOAD_TIMEOUT,
                    )
                    s3_resp.raise_for_status()
                    etag = s3_resp.headers.get("ETag", "")
                    if not etag:
                        raise RuntimeError(f"No ETag returned for S3 part {part_num}")
                    return etag
                except requests.HTTPError as e:
                    content = getattr(e.response, 'text', '')
                    self.status.emit(f"[DEBUG] HTTPError (S3 PUT): {e}")
                    self.status.emit(f"[DEBUG] Response status: {getattr(e.response, 'status_code', None)}")
                    self.status.emit(f"[DEBUG] Response content: {content}")
                    if e.response is not None and 'NoSuchUpload' in content:
                        self._abort(session)
                        self.error.emit("S3 upload session expired or invalid (NoSuchUpload). Please retry the upload.")
                        return None
                    last_error = e
                except Exception as e:
                    self.status.emit(f"[DEBUG] Exception (S3 PUT): {e}")
                    last_error = e

                # Subtract bytes this attempt fed so the retry doesn't double-count.
                # Guard against body never being assigned (e.g. presign threw before
                # make_streaming_body was called).
                if body is not None:
                    tracker.unfeed(body.fed)
                self._wait_before_part_retry("S3", part_num, attempt, last_error)

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
                timeout=self._TIMEOUT,
            )
        except Exception:
            pass
        self.status.emit("[DEBUG] Upload aborted.")

    def _ensure_folder(self, path):
        """Create a folder and all missing parents via POST /api/files/folders.
        The API takes {"path": <parent>, "name": <folder_name>}.
        409 (already exists) and connection/timeout errors are both treated as
        non-fatal — the folder either exists already or the server will create
        it implicitly when the file is uploaded.  Only hard 4xx client errors
        (excluding 409) are re-raised.
        """
        parts = path.strip("/").split("/")
        for depth in range(1, len(parts) + 1):
            name   = parts[depth - 1]
            parent = ("/" + "/".join(parts[:depth - 1])).rstrip("/") or "/"
            try:
                resp = requests.post(
                    f"{self.base_url}/api/files/folders",
                    headers={**self._headers(), "Content-Type": "application/json"},
                    json={"path": parent, "name": name},
                    timeout=self._TIMEOUT,
                )
                if resp.status_code == 409:
                    self.status.emit(f"[DEBUG] Folder already exists: {parent}/{name}")
                else:
                    resp.raise_for_status()
                    self.status.emit(f"[DEBUG] Created folder: {parent}/{name}")
            except requests.HTTPError as e:
                status = getattr(e.response, "status_code", None)
                if status and status != 409 and status < 500:
                    # Hard client error (e.g. 403, 422) — re-raise
                    self.status.emit(f"[DEBUG] Folder create hard error {parent}/{name}: {e}")
                    raise
                # 5xx or ambiguous — folder likely exists, press on
                self.status.emit(f"[DEBUG] Folder create non-fatal error {parent}/{name}: {e}")
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                # Network hiccup — folder almost certainly already exists
                self.status.emit(f"[DEBUG] Folder create connection error (ignored) {parent}/{name}: {e}")

    def _move_file(self, file_id, dest_path):
        """Move an uploaded file to dest_path via POST /api/files/move."""
        try:
            resp = requests.post(
                f"{self.base_url}/api/files/move",
                headers={**self._headers(), "Content-Type": "application/json"},
                json={"fileId": file_id, "toPath": dest_path.rstrip("/") + "/"},
                timeout=30,
            )
            resp.raise_for_status()
            j = resp.json()
            self.status.emit(f"[DEBUG] Move response: {j}")
            return j.get("fileId") or j.get("id") or file_id
        except requests.HTTPError as e:
            self.status.emit(f"[DEBUG] Move HTTPError: {e}")
            self.status.emit(f"[DEBUG] Move response: {getattr(e.response, 'text', '')[:200]}")
            # Don't raise — upload succeeded even if move fails
            return file_id
        except Exception as e:
            self.status.emit(f"[DEBUG] Move exception: {e}")
            return file_id

    def _create_share(self, file_id):
        payload = {"fileId": file_id}
        if self.share_expiry_hours is not None:
            payload["expiresInHours"] = self.share_expiry_hours
        if self.share_max_downloads > 0:
            payload["maxDownloads"] = self.share_max_downloads

        share_url_endpoint = f"{self.base_url}/api/shares"
        self.status.emit(f"[DEBUG] Share URL: {share_url_endpoint}")
        self.status.emit(f"[DEBUG] Share payload: {payload}")
        try:
            resp = requests.post(
                share_url_endpoint,
                headers={**self._headers(), "Content-Type": "application/json"},
                json=payload,
                timeout=30,
            )
            self.status.emit(f"[DEBUG] Share response status: {resp.status_code}")
            self.status.emit(f"[DEBUG] Share response body: {resp.text[:500]}")
            resp.raise_for_status()
        except requests.HTTPError as e:
            self.status.emit(f"[DEBUG] Share HTTPError: {e}")
            self.status.emit(f"[DEBUG] Share response status: {getattr(e.response, 'status_code', None)}")
            self.status.emit(f"[DEBUG] Share response content: {getattr(e.response, 'text', None)}")
            raise
        except Exception as e:
            self.status.emit(f"[DEBUG] Share exception: {e}")
            raise
        data  = resp.json()
        token = data.get("token") or data.get("share", {}).get("token", "")
        self.status.emit(f"[DEBUG] Share token: {token!r}  full JSON: {data}")
        return f"{SHARE_BASE_URL}/share/{token}" if token else "(no share URL returned)"

    @staticmethod
    def _fmt_size(b):
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if b < 1024:
                return f"{b:.3f} {unit}"
            b /= 1024
        return f"{b:.3f} PB"


# ── Files API Worker ─────────────────────────────────────────────────────────
class FilesWorker(QThread):
    """Generic background worker for Files-tab API operations."""
    done    = pyqtSignal(object)   # result payload (varies by op)
    error   = pyqtSignal(str)
    # (connect_timeout, read_timeout) — fail fast on unreachable hosts
    _TIMEOUT = (5, 60)

    def __init__(self, op, api_key, base_url, **kwargs):
        super().__init__()
        self.op       = op          # 'list' | 'delete' | 'move' | 'share' | 'mkdir' | 'shares'
        self.api_key  = api_key
        self.base_url = base_url.rstrip("/")
        self.kwargs   = kwargs

    def _h(self):
        return {"Authorization": f"Bearer {self.api_key}",
                "Content-Type":  "application/json"}

    def run(self):
        try:
            if self.op == "list":
                self._list()
            elif self.op == "delete":
                self._delete()
            elif self.op == "move":
                self._move()
            elif self.op == "share":
                self._share()
            elif self.op == "mkdir":
                self._mkdir()
            elif self.op == "shares":
                self._list_shares()
            elif self.op == "delete_folder":
                self._delete_folder()
            elif self.op == "delete_shares":
                self._delete_shares()
            elif self.op == "rename":
                self._rename()
        except Exception as e:
            self.error.emit(str(e))

    def _list(self):
        path = self.kwargs.get("path", "/")
        resp = requests.get(
            f"{self.base_url}/api/files",
            headers={"Authorization": f"Bearer {self.api_key}"},
            params={"path": path, "includeSubfolders": "0"},
            timeout=self._TIMEOUT,
        )
        resp.raise_for_status()
        self.done.emit({"op": "list", "path": path, "data": resp.json()})

    def _delete(self):
        file_name = self.kwargs["file_name"]   # full remote path / filename
        # Strip leading slash — API path is /api/files/{fileName}
        encoded = requests.utils.quote(file_name.lstrip("/"), safe="")
        resp = requests.delete(
            f"{self.base_url}/api/files/{encoded}",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self._TIMEOUT,
        )
        resp.raise_for_status()
        self.done.emit({"op": "delete", "file_name": file_name})

    def _delete_folder(self):
        full_path = self.kwargs["path"].rstrip("/")
        if not full_path or full_path == "/":
            # Cannot delete root
            raise ValueError("Cannot delete root folder")

        # Split path into parent and folder name
        # Example: /Functionality/New folder → parent=/Functionality, name=New folder
        #          /music → parent=/, name=music
        if "/" in full_path.lstrip("/"):
            # Has a parent folder
            parts  = full_path.rsplit("/", 1)
            parent = parts[0] if parts[0] else "/"
            name   = parts[1]
        else:
            # Root-level folder
            parent = "/"
            name   = full_path.lstrip("/")

        url = f"{self.base_url}/api/files/folders"
        payload = {"path": parent, "name": name}
        headers = self._h()

        # Debug logging
        write_debug_log(f"[DEBUG] Delete folder request:")
        write_debug_log(f"[DEBUG]   URL: {url}")
        write_debug_log(f"[DEBUG]   Full path: {full_path}")
        write_debug_log(f"[DEBUG]   Parent: {parent}")
        write_debug_log(f"[DEBUG]   Name: {name}")
        write_debug_log(f"[DEBUG]   Payload: {payload}")
        write_debug_log(f"[DEBUG]   Headers: {dict(headers)}")

        try:
            resp = requests.delete(
                url,
                headers=headers,
                json=payload,
                timeout=self._TIMEOUT,
            )
            write_debug_log(f"[DEBUG] Response status: {resp.status_code}")
            write_debug_log(f"[DEBUG] Response body: {resp.text}")
            resp.raise_for_status()
        except requests.HTTPError as e:
            write_debug_log(f"[DEBUG] HTTPError: {e}")
            write_debug_log(f"[DEBUG] Response status: {getattr(e.response, 'status_code', None)}")
            write_debug_log(f"[DEBUG] Response content: {getattr(e.response, 'text', None)}")
            raise
        except Exception as e:
            write_debug_log(f"[DEBUG] Exception: {e}")
            raise

        self.done.emit({"op": "delete_folder", "path": full_path})

    def _move(self):
        file_id     = self.kwargs.get("file_id")
        is_folder   = self.kwargs.get("is_folder", False)
        new_path    = self.kwargs["new_path"]
        to_path     = new_path if new_path.endswith("/") else new_path.rstrip("/") + "/"
        if is_folder:
            # Folder move: {"folderPath": "/from/folder/", "toPath": "/to/"}
            payload = {
                "folderPath": self.kwargs.get("source_path", ""),
                "toPath": to_path,
            }
        elif file_id:
            # File move by ID (preferred): {"fileId": "...", "toPath": "/dest/"}
            payload = {"fileId": file_id, "toPath": to_path}
        else:
            # File move by path fallback
            payload = {"sourcePath": self.kwargs.get("source_path", ""), "toPath": to_path}
        resp = requests.post(
            f"{self.base_url}/api/files/move",
            headers=self._h(),
            json=payload,
            timeout=self._TIMEOUT,
        )
        resp.raise_for_status()
        self.done.emit({"op": "move", "new_path": new_path})

    # label → hours mapping for the Files-tab share dialog
    _EXPIRY_LABEL_TO_HOURS = {
        "1h": 1, "6h": 6, "12h": 12,
        "1d": 24, "3d": 72, "7d": 168, "14d": 336, "30d": 720,
    }

    def _share(self):
        file_id       = self.kwargs["file_id"]
        expiry_label  = self.kwargs.get("expiry", "Never")
        expiry_hours  = self._EXPIRY_LABEL_TO_HOURS.get(expiry_label)  # None → omit field
        max_dl        = self.kwargs.get("max_downloads", 0)
        payload       = {"fileId": file_id}
        if expiry_hours is not None:
            payload["expiresInHours"] = expiry_hours
        if max_dl > 0:
            payload["maxDownloads"] = max_dl
        resp = requests.post(
            f"{self.base_url}/api/shares",
            headers=self._h(),
            json=payload,
            timeout=self._TIMEOUT,
        )
        resp.raise_for_status()
        data  = resp.json()
        token = data.get("token") or (data.get("share") or {}).get("token", "")
        url   = f"{SHARE_BASE_URL}/share/{token}" if token else ""
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
            timeout=self._TIMEOUT,
        )
        resp.raise_for_status()
        self.done.emit({"op": "mkdir", "path": full_path})

    def _rename(self):
        resp = requests.patch(
            f"{self.base_url}/api/files/folders",
            headers=self._h(),
            json={
                "path":    self.kwargs.get("path", "/"),
                "oldName": self.kwargs.get("old_name", ""),
                "newName": self.kwargs.get("new_name", ""),
            },
            timeout=self._TIMEOUT,
        )
        resp.raise_for_status()
        self.done.emit({"op": "rename"})

    def _delete_shares(self):
        """Delete multiple shares by token. Attempts all; collects errors."""
        tokens  = self.kwargs.get("tokens", [])
        deleted = 0
        errors  = []
        for token in tokens:
            try:
                resp = requests.delete(
                    f"{self.base_url}/api/shares/{token}",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=self._TIMEOUT,
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
            timeout=self._TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
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
                        timeout=self._TIMEOUT,
                    )
                    meta_resp.raise_for_status()
                    meta = meta_resp.json().get("share", {})
                except Exception:
                    continue

                original_name = (
                    meta.get("originalName")
                    or meta.get("original_name")
                    or meta.get("fileName")
                    or meta.get("file_name")
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
    _TIMEOUT = (5, 60)  # (connect, read)

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
            "fileName": self.kwargs["file_name"],
            "path": self.kwargs["path"],
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
            timeout=self._TIMEOUT,
        )
        resp.raise_for_status()
        self.done.emit({"op": "jobs", "data": resp.json()})

    def _cancel(self):
        job_id = self.kwargs["job_id"]
        resp = requests.delete(
            f"{self.base_url}/api/admin/transfer-jobs",
            headers={"Authorization": f"Bearer {self.api_key}"},
            params={"id": job_id},
            timeout=self._TIMEOUT,
        )
        resp.raise_for_status()
        self.done.emit({"op": "cancel", "job_id": job_id, "data": resp.json()})

# ── Storage Capacity Worker ───────────────────────────────────────────────────
class StorageWorker(QThread):
    """Fetches remote storage capacity for the titlebar indicator."""
    done  = pyqtSignal(object)   # dict: usedBytes, availableBytes, maxStorageBytes, storagePercent
    error = pyqtSignal(str)
    _TIMEOUT = (5, 60)  # (connect, read)

    def __init__(self, api_key, base_url):
        super().__init__()
        self.api_key  = api_key
        self.base_url = base_url.rstrip("/")

    def run(self):
        try:
            resp = requests.get(
                f"{self.base_url}/api/storage/available",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self._TIMEOUT,
            )
            resp.raise_for_status()
            self.done.emit(resp.json())
        except Exception as e:
            self.error.emit(str(e))


# ── Direct Download Worker ────────────────────────────────────────────────────
class DownloadWorker(QThread):
    """Downloads a file from a presigned URL directly to a local path."""
    progress = pyqtSignal(float)    # 0.0-100.0
    speed    = pyqtSignal(float)    # bytes/sec
    done     = pyqtSignal(str)      # local file path on success
    error    = pyqtSignal(str)

    def __init__(self, url: str, dest_path: str, parent=None):
        super().__init__(parent)
        self.url       = url
        self.dest_path = dest_path
        self._cancel   = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            resp = requests.get(self.url, stream=True, timeout=60)
            resp.raise_for_status()
            total   = int(resp.headers.get("content-length", 0))
            fetched = 0
            start   = time.monotonic()
            with open(self.dest_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    if self._cancel:
                        return
                    if chunk:
                        fh.write(chunk)
                        fetched += len(chunk)
                        elapsed  = max(time.monotonic() - start, 0.001)
                        self.speed.emit(fetched / elapsed)
                        if total:
                            self.progress.emit(min(fetched / total * 100, 99.999))
            self.progress.emit(100.0)
            self.done.emit(self.dest_path)
        except Exception as e:
            self.error.emit(str(e))