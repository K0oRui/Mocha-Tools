"""upload_pipeline.py — Shared upload pipeline for MochaTools.

Single-file, mass, and sync uploads all funnel through the same
UploadWorker engine.  This module adds the one orchestrator they share:
a queue with a global concurrency cap, per-source priority, and a stable
job ID on every signal so tabs can correlate results back to their UI.

Public API
----------
  UploadJob            - dataclass describing one upload unit
  UploadManager        - QObject that owns the queue and worker lifecycle
  PRIORITY_SINGLE      - priority for user-initiated single uploads
  PRIORITY_MASS        - priority for user-initiated mass uploads
  PRIORITY_SYNC        - priority for background sync uploads

Tabs enqueue UploadJob instances and subscribe to the manager's signals.
The manager never knows about tab widgets; callers correlate via the
job ID and the optional ``ref`` they attached to the job.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer, Signal

from .workers import UploadWorker

if TYPE_CHECKING:
    from .mocha_client import MochaClient

# ── Priorities ────────────────────────────────────────────────────────────────
# Higher values are scheduled first when the global cap is reached.
PRIORITY_SYNC = 0
PRIORITY_MASS = 1
PRIORITY_SINGLE = 1

DEFAULT_GLOBAL_CONCURRENCY = 4


# ── Job description ───────────────────────────────────────────────────────────


@dataclass
class UploadJob:
    """One unit of work for the UploadManager.

    file_pairs: list of (local_abs_path, remote_dest_path) tuples, where
        remote_dest_path is the full absolute path on Mocha, e.g.
        '/Music/Album/CD1/track.flac'.
    source: which tab enqueued this job ('single' | 'mass' | 'sync').
    ref: opaque caller-supplied token carried through every signal so the
        tab can correlate results (e.g. a sync pair_id or queue index).
    priority: scheduling priority; higher runs first under the global cap.
    """

    file_pairs: list[tuple[str, str]]
    create_share: bool = False
    share_expiry_hours: int | None = None
    share_max_downloads: int = 0
    chunk_size_mb: int | None = None
    max_chunks: int | None = None
    source: str = "single"
    ref: object = None
    priority: int = PRIORITY_SINGLE


# ── Shared orchestrator ───────────────────────────────────────────────────────


class UploadManager(QObject):
    """Owns the upload queue, worker lifecycle, and global concurrency cap.

    Lives on the GUI thread (one instance per app, on AppContext).  Workers
    run on their own QThreads; their signals are queued back to this thread,
    so emitting from here is safe.

    Signals carry the job ID first, then the job's ``ref`` token, so
    subscribers can route to the right row/entry without building their
    own lookup maps.
    """

    job_progress = Signal(int, object, float)  # (job_id, ref, pct 0.0-100.0)
    job_speed = Signal(int, object, float)  # (job_id, ref, bytes/sec)
    job_bytes = Signal(int, object, int, int)  # (job_id, ref, bytes_done, bytes_total)
    job_status = Signal(int, object, str)  # (job_id, ref, log message)
    job_done = Signal(int, object, dict)  # (job_id, ref, result dict from UploadWorker)
    job_error = Signal(int, object, str)  # (job_id, ref, error message)
    queue_idle = Signal()  # emitted when the queue drains to empty

    def __init__(
        self,
        client: MochaClient,
        max_concurrency: int = DEFAULT_GLOBAL_CONCURRENCY,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._max_concurrency = max(1, int(max_concurrency))
        self._pending: list[tuple[int, UploadJob]] = []
        self._active: dict[int, UploadWorker] = {}
        self._draining: list[UploadWorker] = []
        self._drain_timer: QTimer | None = None
        self._next_id = 1
        self._subscribers: dict[int, dict] = {}

    # ── Public API ──────────────────────────────────────────────────────────

    def enqueue(self, job: UploadJob) -> int:
        """Queue a job and return its job ID.  Scheduling happens immediately."""
        job_id = self._next_id
        self._next_id += 1
        self._pending.append((job_id, job))
        self._schedule()
        return job_id

    def subscribe(self, job_id: int, callbacks: dict) -> None:
        """Register per-job callbacks routed by this manager.

        ``callbacks`` keys: ``progress``, ``speed``, ``bytes``, ``status``,
        ``done``, ``error``.  Each value is a callable receiving the payload
        args of the matching worker signal (without the job id/ref prefix).
        The ``done``/``error`` callbacks fire once and the subscription is
        removed automatically.
        """
        self._subscribers[job_id] = callbacks

    def unsubscribe(self, job_id: int) -> None:
        """Drop a per-job subscription (no-op if not subscribed)."""
        self._subscribers.pop(job_id, None)

    def cancel(self, job_id: int) -> None:
        """Cancel one job: stop its worker if active, drop it if pending."""
        worker = self._active.pop(job_id, None)
        if worker is not None:
            worker.cancel()
            self._draining.append(worker)
            self._ensure_drain_timer()
        else:
            self._pending = [(jid, j) for jid, j in self._pending if jid != job_id]
        self._schedule()

    def cancel_all(self) -> None:
        """Cancel every active and pending job."""
        for worker in self._active.values():
            worker.cancel()
            self._draining.append(worker)
        self._active.clear()
        self._pending.clear()
        if self._draining:
            self._ensure_drain_timer()
        self._schedule()

    def set_concurrency(self, max_concurrency: int) -> None:
        """Raise/lower the global cap.  Takes effect on the next schedule."""
        self._max_concurrency = max(1, int(max_concurrency))
        self._schedule()

    @property
    def concurrency(self) -> int:
        return self._max_concurrency

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    # ── Scheduling ──────────────────────────────────────────────────────────

    def _schedule(self) -> None:
        """Launch pending jobs up to the global cap, highest priority first."""
        if not self._pending:
            if not self._active:
                self.queue_idle.emit()
            return
        while self._pending and len(self._active) < self._max_concurrency:
            # Stable sort by priority descending; pick the best candidate.
            self._pending.sort(key=lambda item: item[1].priority, reverse=True)
            job_id, job = self._pending.pop(0)
            self._launch(job_id, job)

    def _launch(self, job_id: int, job: UploadJob) -> None:
        w = UploadWorker(
            self._client,
            job.file_pairs,
            job.create_share,
            job.share_expiry_hours,
            job.share_max_downloads,
            chunk_size_mb=job.chunk_size_mb,
            max_chunks=job.max_chunks,
        )
        w._job_id = job_id
        w._job_ref = job.ref
        w._job_source = job.source

        w.progress.connect(partial(self.job_progress.emit, job_id, job.ref))
        w.speed.connect(partial(self.job_speed.emit, job_id, job.ref))
        w.status.connect(partial(self.job_status.emit, job_id, job.ref))
        w.finished.connect(partial(self._on_done, job_id, job.ref))
        w.error.connect(partial(self._on_error, job_id, job.ref))
        if hasattr(w, "bytes_progress"):
            w.bytes_progress.connect(partial(self.job_bytes.emit, job_id, job.ref))

        w.progress.connect(partial(self._dispatch, job_id, "progress"))
        w.speed.connect(partial(self._dispatch, job_id, "speed"))
        w.status.connect(partial(self._dispatch, job_id, "status"))
        if hasattr(w, "bytes_progress"):
            w.bytes_progress.connect(partial(self._dispatch, job_id, "bytes"))

        self._active[job_id] = w
        w.start()

    # ── Per-subscriber routing ──────────────────────────────────────────────

    def _dispatch(self, job_id: int, kind: str, *args: Any) -> None:
        """Forward a worker payload to the job's subscriber, if any."""
        sub = self._subscribers.get(job_id)
        if sub is None:
            return
        cb = sub.get(kind)
        if cb is not None:
            with contextlib.suppress(Exception):
                cb(*args)

    # ── Worker completion ───────────────────────────────────────────────────

    def _on_done(self, job_id: int, ref: object, result: dict) -> None:
        self._active.pop(job_id, None)
        self.job_done.emit(job_id, ref, result)
        sub = self._subscribers.pop(job_id, None)
        if sub is not None:
            cb = sub.get("done")
            if cb is not None:
                with contextlib.suppress(Exception):
                    cb(result)
        self._schedule()

    def _on_error(self, job_id: int, ref: object, msg: str) -> None:
        self._active.pop(job_id, None)
        self.job_error.emit(job_id, ref, msg)
        sub = self._subscribers.pop(job_id, None)
        if sub is not None:
            cb = sub.get("error")
            if cb is not None:
                with contextlib.suppress(Exception):
                    cb(msg)
        self._schedule()

    # ── Cancelled-worker cleanup ───────────────────────────────────────────
    # A cancelled UploadWorker returns from run() without emitting its
    # finished signal, so the manager can't rely on _on_done to release it.
    # Keep cancelled workers referenced (never GC a running QThread) and
    # sweep them once their thread reports finished.

    def _ensure_drain_timer(self) -> None:
        if self._drain_timer is None:
            self._drain_timer = QTimer(self)
            self._drain_timer.setInterval(500)
            self._drain_timer.timeout.connect(self._sweep_draining)
            self._drain_timer.start()

    def _sweep_draining(self) -> None:
        self._draining = [w for w in self._draining if not w.isFinished()]
        if not self._draining and self._drain_timer is not None:
            self._drain_timer.stop()
            self._drain_timer = None
            self._schedule()
