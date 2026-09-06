from __future__ import annotations

import math
import operator
import os
import pathlib
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any, ClassVar

import requests
from PySide6.QtCore import QObject, QThread, Signal

from .constants import (
    DEFAULT_CHUNK_SIZE_MB,
    DEFAULT_MAX_CHUNKS,
    PART_UPLOAD_RETRIES,
    RELAY_DEFAULT_CONCURRENCY,
    RELAY_MAX_CONCURRENCY,
    S3_DEFAULT_CONCURRENCY,
    S3_MAX_CONCURRENCY,
    SHARE_BASE_URL,
)
from .logging_utils import write_debug_log
from .mocha_client import MochaAPIError, MochaClient, StreamingBody

if TYPE_CHECKING:
    from collections.abc import Callable


_KB = 1024
_MIN_SAMPLES = 2
_HTTP_CONFLICT = 409
_HTTP_SERVER_ERROR_MIN = 500
_FOLDER_CREATE_CONCURRENCY = 8


# ── Progress Tracker ─────────────────────────────────────────────────────────
class _SlidingWindow:
    """Lightweight ring-buffer speed calculator.

    Stores (timestamp, cumulative_bytes) samples.  Speed is derived
    from the oldest sample within *window* seconds, giving a stable
    moving average that reacts to changes within one window length.
    Thread-safe when used from *feed* (the only writer).
    """

    def __init__(self, window: float = 5.0) -> None:
        self._window = window
        self._samples: list[tuple[float, int]] = []  # (monotonic, cum_bytes)

    def add(self, now: float, cum_bytes: int) -> float:
        """Add a sample and return the speed (bytes/sec) over the window."""
        samples = self._samples
        samples.append((now, cum_bytes))
        # Prune samples older than window
        cutoff = now - self._window
        # Keep at least two samples so we can always compute a slope
        while len(samples) > _MIN_SAMPLES and samples[0][0] < cutoff:
            samples.pop(0)
        # If the window hasn't filled yet, use whatever span we have
        oldest_ts, oldest_bytes = samples[0]
        span = max(now - oldest_ts, 0.001)
        return (cum_bytes - oldest_bytes) / span


class ProgressTracker:
    """Thread-safe byte counter shared across all parallel upload workers.

    Each worker calls feed(n) as bytes leave the socket. The tracker
    accumulates totals and fires progress/speed callbacks at most once
    every EMIT_INTERVAL seconds so the UI isn't flooded.  Speed uses a
    sliding window average (default 5 s) for responsive ETA estimates.
    """

    EMIT_INTERVAL = 0.25  # seconds between UI updates

    def __init__(
        self,
        total_bytes: int,
        on_progress: Callable[[float], None],
        on_speed: Callable[[float], None],
        on_bytes_progress: Callable[[int, int], None] | None = None,
    ) -> None:
        self._total = total_bytes
        self._sent = 0  # bytes confirmed sent
        self._lock = threading.Lock()
        self._start = time.monotonic()
        self._last_emit = 0.0
        self._window = _SlidingWindow(5.0)
        self._on_prog = on_progress  # callable(int pct)
        self._on_speed = on_speed  # callable(float bps)
        self._on_bytes = on_bytes_progress  # callable(int done, int total) or None

    def feed(self, n_bytes: int) -> None:
        """Called by upload threads as bytes leave the socket."""
        with self._lock:
            self._sent = min(self._sent + n_bytes, self._total)
            now = time.monotonic()
            if now - self._last_emit >= self.EMIT_INTERVAL:
                self._last_emit = now
                pct = min(self._sent / self._total * 100, 99.999)
                bps = self._window.add(now, self._sent)
                self._on_prog(pct)
                self._on_speed(bps)
                if self._on_bytes:
                    self._on_bytes(self._sent, self._total)

    def unfeed(self, n_bytes: int) -> None:
        """Subtract bytes that were fed for a part that is being retried,
        so the counter doesn't accumulate duplicate data.
        """
        with self._lock:
            self._sent = max(self._sent - n_bytes, 0)

    def finish(self) -> None:
        """Call once when all parts are done to snap to 100%."""
        with self._lock:
            now = time.monotonic()
            elapsed = max(now - self._start, 0.001)
            bps = self._window.add(now, self._sent) or (self._sent / elapsed)
            total = self._total
        self._on_prog(100)
        self._on_speed(bps)
        if self._on_bytes:
            self._on_bytes(total, total)

    def make_streaming_body(
        self,
        chunk: bytes,
        read_size: int = 65536,
    ) -> StreamingBody:
        class ChunkStream:
            def __init__(
                self,
                chunk_bytes: bytes,
                tracker: ProgressTracker,
                block_size: int,
            ) -> None:
                self.chunk = chunk_bytes
                self.tracker = tracker
                self.block_size = block_size
                self.offset = 0
                self.length = len(chunk_bytes)
                self.len = self.length
                self.fed = 0  # bytes fed to the tracker during this attempt

            def read(self, size: int = -1) -> bytes:
                if self.offset >= self.length:
                    return b""
                if size is None or size < 0:
                    size = self.block_size
                end = min(self.offset + size, self.length)
                piece = self.chunk[self.offset : end]
                if piece:
                    self.tracker.feed(len(piece))
                    self.fed += len(piece)
                    self.offset = end
                return piece

            def __len__(self) -> int:
                return self.length

        return ChunkStream(chunk, self, read_size)


# ── Upload Worker ────────────────────────────────────────────────────────────
class UploadWorker(QThread):
    progress = Signal(float)  # 0.0-100.0
    speed = Signal(float)  # bytes/sec
    bytes_progress = Signal(
        "qint64",
        "qint64",
    )  # (bytes_done, bytes_total) — 64-bit to handle files > 2 GB
    status = Signal(str)  # log message
    finished = Signal(dict)  # result dict
    error = Signal(str)

    def __init__(
        self,
        client: MochaClient,
        file_pairs: list[tuple[str, str]],
        create_share: bool,
        share_expiry: int | None,
        share_max_downloads: int,
        chunk_size_mb: int | None = None,
        max_chunks: int | None = None,
    ) -> None:
        """client: shared MochaClient instance.
        file_pairs: list of (local_abs_path, remote_dest_path) tuples.
        remote_dest_path is already the full absolute path on Mocha,
        e.g. '/Music/Album/CD1/track.flac'.
        chunk_size_mb: size of each multipart chunk in MB (1-100).
        max_chunks: maximum number of in-flight parallel chunks (1-20).
        """
        super().__init__()
        self._client = client
        self.file_pairs = file_pairs  # [(local, dest), ...]
        self.create_share = create_share
        self.share_expiry_hours = share_expiry  # int hours or None
        self.share_max_downloads = share_max_downloads
        # Chunk config — clamp to valid ranges
        mb = int(chunk_size_mb) if chunk_size_mb is not None else DEFAULT_CHUNK_SIZE_MB
        self._chunk_size = max(1, min(mb, 100)) * 1024 * 1024  # bytes
        mc = int(max_chunks) if max_chunks is not None else DEFAULT_MAX_CHUNKS
        self._max_chunks = max(1, min(mc, 20))
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        total_files = len(self.file_pairs)
        last_file_id = None
        last_share_url = None

        # ── Pre-create every unique destination directory ──────────────────────
        dest_dirs = sorted(
            {
                "/".join(dest.rstrip("/").split("/")[:-1]) or "/"
                for _, dest in self.file_pairs
            },
        )
        # Collect every ancestor path so each folder is created exactly once
        # instead of re-walking the same chain for every destination.
        all_dirs: set[str] = set()
        for d in dest_dirs:
            if d == "/":
                continue
            parts = d.strip("/").split("/")
            for depth in range(1, len(parts) + 1):
                all_dirs.add("/" + "/".join(parts[:depth]))
        if all_dirs:
            self.status.emit(f"[DEBUG] Ensuring {len(all_dirs)} folders…")
            by_depth: dict[int, list[str]] = {}
            for d in all_dirs:
                by_depth.setdefault(d.count("/"), []).append(d)
            try:
                # Parents are guaranteed to exist by the previous depth level,
                # so siblings at the same depth can be created in parallel.
                with ThreadPoolExecutor(max_workers=_FOLDER_CREATE_CONCURRENCY) as pool:
                    for depth in sorted(by_depth):
                        futures = [
                            pool.submit(self._ensure_folder, d) for d in by_depth[depth]
                        ]
                        for fut in as_completed(futures):
                            fut.result()
            except Exception as e:  # noqa: BLE001
                self.error.emit(f"Failed to create folder: {e}")
                return

        # ── Compute grand total bytes ─────────────────────────────────────────
        file_sizes: list[int] = []
        for local_path, _ in self.file_pairs:
            try:
                sz = pathlib.Path(local_path).stat().st_size
            except OSError:
                sz = 0
            file_sizes.append(sz)
        grand_total: int = sum(file_sizes)

        # One tracker per job, shared across every file: progress stays
        # cumulative and the speed window keeps its history, so multi-file
        # uploads don't reset the bar or re-ramp the ETA at each file.
        tracker = ProgressTracker(
            grand_total,
            on_progress=self.progress.emit,
            on_speed=self.speed.emit,
            on_bytes_progress=self.bytes_progress.emit,
        )

        # ── Upload each file ──────────────────────────────────────────────────
        for idx, (local_path, dest_path) in enumerate(self.file_pairs, 1):
            if self._cancel:
                return

            file_name = pathlib.Path(local_path).name
            prefix = f"[{idx}/{total_files}] " if total_files > 1 else ""
            file_size = file_sizes[idx - 1]

            try:
                if file_size == 0:
                    self.status.emit(f"{prefix}{file_name}  ⊘ Skipped (empty file)")
                    continue

                self.status.emit(f"{prefix}{file_name}  ({self._fmt_size(file_size)})")
                self.status.emit(f"[DEBUG] Remote dest: {dest_path}")

                self.status.emit("[DEBUG] Strategy: multipart upload")
                file_id = self._multipart_upload(
                    file_size,
                    local_path,
                    dest_path,
                    tracker,
                )

                if self._cancel or file_id is None:
                    return

                last_file_id = file_id

                if self.create_share and idx == total_files:
                    self.status.emit("Creating share link…")
                    last_share_url = self._create_share(file_id)
                    self.status.emit(f"Share: {last_share_url}")

            except Exception as e:  # noqa: BLE001
                self.error.emit(f"{prefix}{file_name}: {e}")
                return

        tracker.finish()
        self.finished.emit({"file_id": last_file_id, "share_url": last_share_url})

    # ── multipart upload (> 50 MB) ───────────────────────────────────────────
    def _multipart_upload(
        self,
        file_size: int,
        local_path: str,
        dest_path: str,
        tracker: ProgressTracker,
    ) -> str | None:
        import mimetypes

        file_name = pathlib.Path(local_path).name
        dest_dir = "/".join(dest_path.rstrip("/").split("/")[:-1]) or "/"
        dest_dir = dest_dir.rstrip("/") + "/"
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
            guard_fh = pathlib.Path(local_path).open("rb")  # noqa: SIM115
        except OSError as e:
            msg = f"Couldn't open {file_name!r} for upload: {e}"
            raise RuntimeError(msg) from e

        try:
            try:
                guard_stat = os.fstat(guard_fh.fileno())
            except OSError:
                guard_stat = None

            def _verify_file_unchanged() -> None:
                """Best-effort check that the file we locked hasn't been
                replaced in-place (same path, different underlying file)
                since we opened the guard handle. Cheap — just an fstat,
                no extra I/O — so safe to call before every chunk read.
                """
                if guard_stat is None:
                    return
                try:
                    current = pathlib.Path(local_path).stat()
                except OSError as e:
                    msg = f"{file_name} was deleted or moved during upload: {e}"
                    raise RuntimeError(
                        msg,
                    ) from e
                # On POSIX, st_ino/st_dev identify the same underlying file
                # even after a rename; on Windows these aren't reliable for
                # this purpose, so fall back to a size sanity-check there.
                if os.name != "nt":
                    if (current.st_ino, current.st_dev) != (
                        guard_stat.st_ino,
                        guard_stat.st_dev,
                    ):
                        msg = f"{file_name} was replaced during upload (different file at the same path)"
                        raise RuntimeError(
                            msg,
                        )
                elif current.st_size != guard_stat.st_size:
                    msg = (
                        f"{file_name} changed size during upload "
                        f"(was {guard_stat.st_size} bytes, now {current.st_size})"
                    )
                    raise RuntimeError(
                        msg,
                    )

            return self._do_multipart_upload(
                file_size,
                local_path,
                dest_path,
                file_name,
                dest_dir,
                mime_type,
                tracker=tracker,
                verify_file_unchanged=_verify_file_unchanged,
            )
        finally:
            guard_fh.close()

    def _do_multipart_upload(
        self,
        file_size: int,
        local_path: str,
        _dest_path: str,
        file_name: str,
        dest_dir: str,
        mime_type: str,
        tracker: ProgressTracker,
        verify_file_unchanged: Callable[[], None] | None = None,
    ) -> str | None:

        # Retry init on transient 5xx — concurrent mass uploads can cause
        # the server to return 500 when folder creation races or S3 is busy.
        init_data: dict | None = None
        last_init_error: Exception | None = None
        for init_attempt in range(1, 6):
            if self._cancel:
                return None
            try:
                init_data = self._client.multipart_init(
                    file_name,
                    dest_dir,
                    file_size,
                    mime_type,
                    direct_part_size_bytes=self._chunk_size,
                    logger=self.status.emit,
                )
                last_init_error = None
                break
            except MochaAPIError as e:
                self.status.emit(
                    f"[DEBUG] HTTPError (init attempt {init_attempt}/5): {e}",
                )
                self.status.emit(f"[DEBUG] Response status: {e.status_code}")
                self.status.emit(f"[DEBUG] Response content: {e.response_text}")
                last_init_error = e
                if e.kind == "protocol" or (
                    e.kind == "http" and e.status_code not in (429, 500, 502, 503, 504)
                ):
                    raise  # 4xx client errors are not retryable
                wait = min(2 ** (init_attempt - 1), 10)
                self.status.emit(f"[DEBUG] Retrying multipart init in {wait}s…")
                time.sleep(wait)
            except Exception as e:  # noqa: BLE001
                self.status.emit(
                    f"[DEBUG] Exception (init attempt {init_attempt}/5): {e}",
                )
                last_init_error = e
                time.sleep(min(2 ** (init_attempt - 1), 10))
        if last_init_error is not None:
            raise last_init_error
        if init_data is None:
            raise RuntimeError("multipart init returned no data")  # noqa: TRY003
        self.status.emit(f"[DEBUG] Init response: {init_data}")
        # Store the init response fields in one session payload so every
        # multipart request uses the same uploadId, key, nodeId, and path.
        # The backend uses those values to find the existing upload session.
        upload_id = init_data.get("uploadId")
        key = init_data.get("key")
        node_id = init_data.get("nodeId")
        direct = init_data.get("directPartUpload", True) is not False

        if not upload_id or not key or not node_id:
            msg = f"Invalid multipart init response: {init_data}"
            raise RuntimeError(msg)

        session = {
            "uploadId": upload_id,
            "key": key,
            "nodeId": node_id,
            "originalName": init_data.get("originalName") or file_name,
            "path": dest_dir,
            "size": file_size,
            "mimeType": mime_type,
        }

        # Chunk by the server's part size (docs: chunk by the response's
        # directPartSizeBytes, not the one you asked for).  Clamp to the
        # configured chunk size so a smaller user setting is respected —
        # an oversized part is rejected mid-body and surfaces as a write
        # timeout.  Relay uploads chunk by partSizeBytes (50 MB max).
        if direct:
            server_part_size = init_data.get("directPartSizeBytes") or init_data.get(
                "partSizeBytes"
            )
        else:
            server_part_size = init_data.get("partSizeBytes")
        if server_part_size:
            chunk_size = min(self._chunk_size, int(server_part_size))
        else:
            chunk_size = self._chunk_size
        total_parts = math.ceil(file_size / chunk_size)
        mode = "direct S3" if direct else "server relay"
        concurrency = self._multipart_concurrency(
            init_data,
            total_parts,
            mode,
            self._max_chunks,
        )
        self.status.emit(
            f"[DEBUG] Multipart upload: {total_parts} parts… (mode={mode}, partSize={self._fmt_size(chunk_size)}, concurrency={concurrency})",
        )
        self.status.emit(f"[DEBUG] Session: {upload_id}")

        # The job-level tracker fires UI updates as bytes leave the socket
        # across all parallel part workers rather than only on part completion.
        # It is shared across every file so progress and speed stay cumulative.
        parts = []
        active_parts: set[int] = set()
        active_lock = threading.Lock()

        # Each worker opens its own file handle and seeks to its part offset.
        # Sharing one file object across parallel uploads would race the read
        # position and corrupt the parts.
        def upload_part(part_num: int) -> dict | None:
            with active_lock:
                active_parts.add(part_num)
            offset = (part_num - 1) * chunk_size
            read_size = min(chunk_size, file_size - offset)
            if self._cancel:
                return None
            if verify_file_unchanged is not None:
                verify_file_unchanged()
            with pathlib.Path(local_path).open("rb") as part_file:
                part_file.seek(offset)
                chunk = part_file.read(read_size)
            if self._cancel:
                return None
            self.status.emit(
                f"[DEBUG] Chunk size for part {part_num}: {len(chunk)} bytes",
            )
            if direct:
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
                self.status.emit(
                    f"[DEBUG] Uploading {parts_str} out of {total_parts} total…",
                )
            for future in as_completed(futures):
                if self._cancel:
                    self._stop_multipart_futures(futures, session, total_parts)
                    return None

                try:
                    result = future.result()
                except (AttributeError, TypeError, RuntimeError) as e:
                    write_debug_log(f"[Silenced] _do_multipart_upload: {e}")
                    self._cancel = True
                    self._stop_multipart_futures(futures, session, total_parts)
                    raise

                # If etag is None the part worker already aborted the session
                # and emitted an error — don't fall through to /complete.
                if result is None or result["etag"] is None:
                    self._stop_multipart_futures(futures, session, total_parts)
                    return None

                parts.append(
                    {"partNumber": result["partNumber"], "etag": result["etag"]},
                )
                len(parts)

                with active_lock:
                    active_parts.discard(result["partNumber"])

        # 3. Complete
        complete_payload = {
            **session,
            "parts": sorted(parts, key=operator.itemgetter("partNumber")),
        }
        j = self._complete_multipart_upload(complete_payload)
        file_id = j.get("fileId") or j.get("id") or (j.get("file") or {}).get("id")
        self.status.emit(f"[DEBUG] Multipart complete. File ID: {file_id}")
        return file_id

    def _complete_multipart_upload(self, payload: dict) -> dict:
        last_error: MochaAPIError | None = None
        for attempt in range(1, 9):
            if self._cancel:
                return {}
            try:
                self.status.emit(
                    f"[DEBUG] Completing multipart upload… attempt {attempt}/8",
                )
                return self._client.multipart_complete(payload, logger=self.status.emit)
            except MochaAPIError as e:
                last_error = e
                if e.kind == "connection":
                    self.status.emit(
                        f"[DEBUG] Multipart complete connection issue: {e}",
                    )
                else:
                    status = e.status_code
                    body = e.response_text or ""
                    if e.kind == "protocol" or (
                        status not in (409, 423, 429, 500, 502, 503, 504, 524)
                        and "524" not in body
                    ):
                        raise
                    self.status.emit(
                        f"[DEBUG] Multipart complete still pending/retryable ({status}): {body[:200]}",
                    )

            wait_seconds = min(2 * attempt, 20)
            self.status.emit(
                f"[DEBUG] Waiting {wait_seconds}s before checking complete again…",
            )
            time.sleep(wait_seconds)

        if last_error is not None:
            raise last_error
        return {}

    @staticmethod
    def _multipart_concurrency(
        init_data: dict,
        total_parts: int,
        mode: str,
        user_max_chunks: int | None = None,
    ) -> int:
        default = (
            S3_DEFAULT_CONCURRENCY if mode == "direct S3" else RELAY_DEFAULT_CONCURRENCY
        )
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
    def _cancel_futures(futures: dict[Future, int]) -> None:
        for future in futures:
            future.cancel()

    def _abort_all_parts(self, session: dict, total_parts: int) -> None:
        self._abort(session, list(range(1, total_parts + 1)))

    def _stop_multipart_futures(
        self,
        futures: dict[Future, int],
        session: dict,
        total_parts: int,
    ) -> None:
        self._cancel_futures(futures)
        self._abort_all_parts(session, total_parts)

    def _wait_before_part_retry(
        self,
        label: str,
        part_num: int,
        attempt: int,
        error: Exception | None,
    ) -> None:
        if error is None:
            return
        if attempt >= PART_UPLOAD_RETRIES or not self._is_retryable_upload_error(error):
            raise error

        delay = min(2 ** (attempt - 1), 10)
        self.status.emit(
            f"[DEBUG] Retrying {label} part {part_num} after transient failure in {delay}s…",
        )
        time.sleep(delay)

    def _upload_part_relay(
        self,
        session: dict,
        part_num: int,
        chunk: bytes,
        tracker: ProgressTracker,
    ) -> str | None:
        """Upload one part through the Mocha relay."""
        last_error: Exception | None = None
        for attempt in range(1, PART_UPLOAD_RETRIES + 1):
            if self._cancel:
                return None
            body = None
            try:
                body = tracker.make_streaming_body(chunk)
                return self._client.multipart_part_relay(
                    session,
                    part_num,
                    body,
                    logger=self.status.emit,
                )
            except MochaAPIError as e:
                self.status.emit(f"[DEBUG] HTTPError: {e}")
                self.status.emit(f"[DEBUG] Response status: {e.status_code}")
                self.status.emit(f"[DEBUG] Response content: {e.response_text}")
                last_error = e
            except Exception as e:  # noqa: BLE001
                self.status.emit(f"[DEBUG] Exception: {e}")
                last_error = e

            # Subtract bytes this attempt fed so the retry doesn't double-count.
            # Guard against body never being assigned (e.g. exception before
            # make_streaming_body was called).
            if body is not None:
                tracker.unfeed(body.fed)
            self._wait_before_part_retry("relay", part_num, attempt, last_error)

        if last_error is not None:
            raise last_error
        return None

    def _presign_part_url(
        self,
        session: dict,
        part_num: int,
        _http: object | None = None,
    ) -> str:
        # Step 1: ask Mocha for a presigned URL for this part
        self.status.emit(f"[DEBUG] Presign payload: partNumbers=[{part_num}]")
        try:
            signed_url = self._client.multipart_presign(
                session,
                part_num,
                logger=self.status.emit,
            )
        except MochaAPIError as e:
            self.status.emit(f"[DEBUG] HTTPError (presign): {e}")
            self.status.emit(f"[DEBUG] Response status: {e.status_code}")
            self.status.emit(f"[DEBUG] Response content: {e.response_text}")
            raise
        except Exception as e:
            self.status.emit(f"[DEBUG] Exception (presign): {e}")
            raise
        return signed_url

    @staticmethod
    def _is_retryable_upload_error(error: Exception) -> bool:
        if isinstance(
            error,
            (
                requests.exceptions.SSLError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
            ),
        ):
            return True
        if isinstance(error, MochaAPIError):
            if error.kind == "connection":
                return True
            if error.kind == "protocol":
                return False
            status = error.status_code
            content = error.response_text or ""
            retryable_codes = (
                "RequestTimeout",
                "SlowDown",
                "InternalError",
                "ServiceUnavailable",
            )
            return status in (408, 429, 500, 502, 503, 504) or any(
                code in content for code in retryable_codes
            )
        if not isinstance(error, requests.HTTPError):
            return False
        response = error.response
        status = getattr(response, "status_code", None)
        content = getattr(response, "text", "") if response is not None else ""
        retryable_codes = (
            "RequestTimeout",
            "SlowDown",
            "InternalError",
            "ServiceUnavailable",
        )
        return status in (408, 429, 500, 502, 503, 504) or any(
            code in content for code in retryable_codes
        )

    def _upload_part_s3(
        self,
        session: dict,
        part_num: int,
        chunk: bytes,
        tracker: ProgressTracker,
    ) -> str | None:
        """Upload one part directly to S3 via a presigned URL (directPartUpload)."""
        last_error: Exception | None = None
        for attempt in range(1, PART_UPLOAD_RETRIES + 1):
            if self._cancel:
                return None
            body = None
            try:
                signed_url = self._presign_part_url(session, part_num)
                # Step 2: PUT the chunk directly to S3 (no auth header — the URL is pre-signed)
                body = tracker.make_streaming_body(chunk)
                return self._client.multipart_part_s3(
                    signed_url,
                    body,
                    logger=self.status.emit,
                    part_num=part_num,
                )
            except MochaAPIError as e:
                content = e.response_text or ""
                self.status.emit(f"[DEBUG] HTTPError (S3 PUT): {e}")
                self.status.emit(f"[DEBUG] Response status: {e.status_code}")
                self.status.emit(f"[DEBUG] Response content: {content}")
                if e.kind == "http" and "NoSuchUpload" in content:
                    self._abort(session)
                    self.error.emit(
                        "S3 upload session expired or invalid (NoSuchUpload). Please retry the upload.",
                    )
                    return None
                last_error = e
            except Exception as e:  # noqa: BLE001
                self.status.emit(f"[DEBUG] Exception (S3 PUT): {e}")
                last_error = e

            # Subtract bytes this attempt fed so the retry doesn't double-count.
            # Guard against body never being assigned (e.g. presign threw before
            # make_streaming_body was called).
            if body is not None:
                tracker.unfeed(body.fed)
            self._wait_before_part_retry("S3", part_num, attempt, last_error)

        if last_error is not None:
            raise last_error
        return None

    def _abort(self, session: dict, part_numbers: list[int] | None = None) -> None:
        try:
            payload = dict(session)
            if part_numbers:
                payload["partNumbers"] = part_numbers
            self._client.multipart_abort(payload, logger=self.status.emit)
        except (AttributeError, TypeError, RuntimeError) as e:
            write_debug_log(f"[Silenced] _abort: {e}")
        self.status.emit("[DEBUG] Upload aborted.")

    def _ensure_folder(self, path: str) -> None:
        """Create a single folder at ``path`` via POST /api/files/folders.

        The API takes {"path": <parent>, "name": <folder_name>}.  409 (already
        exists) and connection/timeout errors are both treated as non-fatal —
        the folder either exists already or the server will create it
        implicitly when the file is uploaded.  Only hard 4xx client errors
        (excluding 409) are re-raised.
        """
        parts = path.strip("/").split("/")
        if not parts:
            return
        name = parts[-1]
        parent = ("/" + "/".join(parts[:-1])).rstrip("/") or "/"
        full = f"{parent}/{name}" if parent != "/" else f"/{name}"
        try:
            self._client.create_folder(parent, name, logger=self.status.emit)
            self.status.emit(f"[DEBUG] Created folder: {full}")
        except MochaAPIError as e:
            if e.kind == "http" and e.status_code == _HTTP_CONFLICT:
                self.status.emit(f"[DEBUG] Folder already exists: {full}")
            elif (
                e.kind == "http"
                and e.status_code is not None
                and e.status_code < _HTTP_SERVER_ERROR_MIN
            ):
                # Hard client error (e.g. 403, 422) — re-raise
                self.status.emit(
                    f"[DEBUG] Folder create hard error {full}: {e}",
                )
                raise
            else:
                # 5xx or ambiguous — folder likely exists, press on
                self.status.emit(
                    f"[DEBUG] Folder create non-fatal error {full}: {e}",
                )
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as e:
            # Network hiccup — folder almost certainly already exists
            self.status.emit(
                f"[DEBUG] Folder create connection error (ignored) {full}: {e}",
            )

    def _move_file(self, file_id: str, dest_path: str) -> str:
        """Move an uploaded file to dest_path via POST /api/files/move."""
        try:
            j = self._client.move(
                file_id=file_id,
                to_path=dest_path.rstrip("/") + "/",
                logger=self.status.emit,
            )
            self.status.emit(f"[DEBUG] Move response: {j}")
            return j.get("fileId") or j.get("id") or file_id
        except MochaAPIError as e:
            self.status.emit(f"[DEBUG] Move HTTPError: {e}")
            self.status.emit(f"[DEBUG] Move response: {(e.response_text or '')[:200]}")
            # Don't raise — upload succeeded even if move fails
            return file_id
        except Exception as e:  # noqa: BLE001
            self.status.emit(f"[DEBUG] Move exception: {e}")
            return file_id

    def _create_share(self, file_id: str) -> str:
        self.status.emit(
            f"[DEBUG] Share payload: fileId={file_id}, "
            f"expiresInHours={self.share_expiry_hours}, maxDownloads={self.share_max_downloads}",
        )
        try:
            data = self._client.create_share(
                file_id,
                expires_in_hours=self.share_expiry_hours,
                max_downloads=self.share_max_downloads,
                logger=self.status.emit,
            )
        except MochaAPIError as e:
            self.status.emit(f"[DEBUG] Share HTTPError: {e}")
            self.status.emit(f"[DEBUG] Share response status: {e.status_code}")
            self.status.emit(f"[DEBUG] Share response content: {e.response_text}")
            raise
        except Exception as e:
            self.status.emit(f"[DEBUG] Share exception: {e}")
            raise
        token = data.get("token") or data.get("share", {}).get("token", "")
        self.status.emit(f"[DEBUG] Share token: {token!r}  full JSON: {data}")
        return f"{SHARE_BASE_URL}/share/{token}" if token else "(no share URL returned)"

    @staticmethod
    def _fmt_size(b: float) -> str:
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if b < _KB:
                return f"{b:.3f} {unit}"
            b /= _KB
        return f"{b:.3f} PB"


# ── Files API Worker ─────────────────────────────────────────────────────────
class FilesWorker(QThread):
    """Generic background worker for Files-tab API operations."""

    done = Signal(object)  # result payload (varies by op)
    error = Signal(str)

    def __init__(self, op: str, client: MochaClient, **kwargs: Any) -> None:
        super().__init__()
        self.op = op  # 'list' | 'delete' | 'move' | 'share' | 'mkdir' | 'shares' | ...
        self._client = client
        self.kwargs = kwargs

    def run(self) -> None:
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
            elif self.op == "toggle_shares":
                self._toggle_shares()
            elif self.op == "rename":
                self._rename()
            elif self.op == "presigned":
                self._presigned()
        except Exception as e:  # noqa: BLE001
            self.error.emit(str(e))

    def _list(self) -> None:
        path = self.kwargs.get("path", "/")
        data = self._client.list_files(path)
        self.done.emit({"op": "list", "path": path, "data": data})

    def _delete(self) -> None:
        file_name = self.kwargs["file_name"]  # full remote path / filename
        self._client.delete_file(file_name)
        self.done.emit({"op": "delete", "file_name": file_name})

    def _delete_folder(self) -> None:
        full_path = self.kwargs["path"].rstrip("/")
        if not full_path or full_path == "/":
            # Cannot delete root
            msg = "Cannot delete root folder"
            raise ValueError(msg)

        # Split path into parent and folder name
        # Example: /Functionality/New folder → parent=/Functionality, name=New folder
        #          /music → parent=/, name=music
        if "/" in full_path.lstrip("/"):
            # Has a parent folder
            parts = full_path.rsplit("/", 1)
            parent = parts[0] or "/"
            name = parts[1]
        else:
            # Root-level folder
            parent = "/"
            name = full_path.lstrip("/")

        write_debug_log("[DEBUG] Delete folder request:")
        write_debug_log(f"[DEBUG]   Full path: {full_path}")
        write_debug_log(f"[DEBUG]   Parent: {parent}")
        write_debug_log(f"[DEBUG]   Name: {name}")

        try:
            self._client.delete_folder(parent, name, logger=write_debug_log)
        except MochaAPIError as e:
            write_debug_log(f"[DEBUG] HTTPError: {e}")
            write_debug_log(f"[DEBUG] Response status: {e.status_code}")
            write_debug_log(f"[DEBUG] Response content: {e.response_text}")
            raise
        except Exception as e:
            write_debug_log(f"[DEBUG] Exception: {e}")
            raise

        self.done.emit({"op": "delete_folder", "path": full_path})

    def _move(self) -> None:
        file_id = self.kwargs.get("file_id")
        is_folder = self.kwargs.get("is_folder", False)
        new_path = self.kwargs["new_path"]
        to_path = new_path if new_path.endswith("/") else new_path.rstrip("/") + "/"
        if is_folder:
            # Folder move: {"folderPath": "/from/folder/", "toPath": "/to/"}
            folder_path = self.kwargs.get("source_path", "")
            if not folder_path:
                msg = "Folder move requires a source folder path"
                raise ValueError(msg)
            self._client.move(folder_path=folder_path, to_path=to_path)
        elif file_id:
            # File move by ID (preferred): {"fileId": "...", "toPath": "/dest/"}
            self._client.move(file_id=file_id, to_path=to_path)
        else:
            msg = "File move requires a file ID"
            raise ValueError(msg)
        self.done.emit({"op": "move", "new_path": new_path})

    # label → hours mapping for the Files-tab share dialog
    _EXPIRY_LABEL_TO_HOURS: ClassVar[dict[str, int]] = {
        "1h": 1,
        "6h": 6,
        "12h": 12,
        "1d": 24,
        "3d": 72,
        "7d": 168,
        "14d": 336,
        "30d": 720,
    }

    def _share(self) -> None:
        file_id = self.kwargs["file_id"]
        expiry_label = self.kwargs.get("expiry", "Never")
        expiry_hours = self._EXPIRY_LABEL_TO_HOURS.get(
            expiry_label,
        )  # None → expiresInHours: null (never expires)
        max_dl = self.kwargs.get("max_downloads", 0)
        data = self._client.create_share(
            file_id,
            expires_in_hours=expiry_hours,
            max_downloads=max_dl,
        )
        token = data.get("token") or (data.get("share") or {}).get("token", "")
        url = self._client.share_url(token)
        self.done.emit({"op": "share", "url": url, "token": token})

    def _mkdir(self) -> None:
        full_path = self.kwargs["path"].rstrip("/")
        parts = full_path.rsplit("/", 1)
        parent = parts[0] or "/"
        name = parts[1] if len(parts) > 1 else full_path.lstrip("/")
        self._client.create_folder(parent, name)
        self.done.emit({"op": "mkdir", "path": full_path})

    def _rename(self) -> None:
        self._client.rename_folder(
            self.kwargs.get("path", "/"),
            self.kwargs.get("old_name", ""),
            self.kwargs.get("new_name", ""),
        )
        self.done.emit({"op": "rename"})

    def _delete_shares(self) -> None:
        """Delete multiple shares by token. Attempts all; collects errors."""
        tokens = self.kwargs.get("tokens", [])
        deleted_tokens = []
        errors = []
        for token in tokens:
            try:
                self._client.delete_share(token)
                deleted_tokens.append(token)
            except (AttributeError, TypeError, RuntimeError) as e:
                write_debug_log(f"[Silenced] _delete_shares: {e}")
                errors.append(f"{token}: {e}")
        self.done.emit(
            {
                "op": "delete_shares",
                "deleted": len(deleted_tokens),
                "deleted_tokens": deleted_tokens,
                "errors": errors,
            },
        )

    def _toggle_shares(self) -> None:
        """Toggle share active state. items = [(token, is_active), ...]."""
        items = self.kwargs.get("items", [])
        toggled = []
        errors = []
        for token, is_active in items:
            try:
                self._client.set_share_active(token, is_active)
                toggled.append(token)
            except (AttributeError, TypeError, RuntimeError) as e:
                write_debug_log(f"[Silenced] _toggle_shares: {e}")
                errors.append(f"{token}: {e}")
        self.done.emit(
            {
                "op": "toggle_shares",
                "toggled": toggled,
                "errors": errors,
            },
        )

    def _list_shares(self) -> None:
        data = self._client.list_shares()
        shares = data.get("shares", data) if isinstance(data, dict) else data

        if isinstance(shares, list):
            for share in shares:
                if not isinstance(share, dict):
                    continue
                token = share.get("token")
                if not token:
                    continue
                try:
                    meta = self._client.get_share(token).get("share", {})
                except (AttributeError, TypeError, RuntimeError) as e:
                    write_debug_log(f"[Silenced] _list_shares: {e}")
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

    def _presigned(self) -> None:
        """Fetch a presigned download URL in the background."""
        file_id = self.kwargs["file_id"]
        url = self._client.presigned_url(file_id)
        self.done.emit({"op": "presigned", "file_id": file_id, "url": url})


# ── Remote Ingest Worker ─────────────────────────────────────────────────────
class RemoteWorker(QThread):
    done = Signal(object)
    error = Signal(str)

    def __init__(self, op: str, client: MochaClient, **kwargs: Any) -> None:
        super().__init__()
        self.op = op
        self._client = client
        self.kwargs = kwargs

    def run(self) -> None:
        try:
            if self.op == "ingest":
                self._ingest()
            elif self.op == "jobs":
                self._jobs()
            elif self.op == "cancel":
                self._cancel()
        except Exception as e:  # noqa: BLE001
            self.error.emit(str(e))

    def _ingest(self) -> None:
        data = self._client.remote_ingest(
            self.kwargs["source_url"],
            self.kwargs["file_name"],
            self.kwargs["path"],
        )
        self.done.emit({"op": "ingest", "data": data})

    def _jobs(self) -> None:
        data = self._client.list_transfer_jobs(
            active_only=self.kwargs.get("active_only", True),
        )
        self.done.emit({"op": "jobs", "data": data})

    def _cancel(self) -> None:
        job_id = self.kwargs["job_id"]
        data = self._client.cancel_transfer_job(job_id)
        self.done.emit({"op": "cancel", "job_id": job_id, "data": data})


# ── Storage Capacity Worker ───────────────────────────────────────────────────
class StorageWorker(QThread):
    """Fetches remote storage capacity for the titlebar indicator."""

    done = Signal(
        object,
    )  # dict: usedBytes, availableBytes, maxStorageBytes, storagePercent
    error = Signal(str)

    def __init__(self, client: MochaClient) -> None:
        super().__init__()
        self._client = client

    def run(self) -> None:
        try:
            self.done.emit(self._client.storage_available())
        except Exception as e:  # noqa: BLE001
            self.error.emit(str(e))


# ── Direct Download Worker ────────────────────────────────────────────────────
class DownloadWorker(QThread):
    """Downloads a file from a presigned URL directly to a local path."""

    progress = Signal(float)  # 0.0-100.0
    speed = Signal(float)  # bytes/sec
    done = Signal(str)  # local file path on success
    error = Signal(str)

    def __init__(self, url: str, dest_path: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.url = url
        self.dest_path = dest_path
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            resp = requests.get(self.url, stream=True, timeout=60)
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            fetched = 0
            start = time.monotonic()
            with pathlib.Path(self.dest_path).open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    if self._cancel:
                        return
                    if chunk:
                        fh.write(chunk)
                        fetched += len(chunk)
                        elapsed = max(time.monotonic() - start, 0.001)
                        self.speed.emit(fetched / elapsed)
                        if total:
                            self.progress.emit(min(fetched / total * 100, 99.999))
            self.progress.emit(100.0)
            self.done.emit(self.dest_path)
        except Exception as e:  # noqa: BLE001
            self.error.emit(str(e))
