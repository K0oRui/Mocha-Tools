"""
upload_pipeline.py — Shared upload pipeline for MochaTools.

Single-file, mass, and sync uploads all funnel through the same
UploadWorker engine.  This module adds the one orchestrator they share:
a queue with a global concurrency cap, per-source priority, and a stable
job ID on every signal so tabs can correlate results back to their UI.

Public API
----------
  UploadJob            – dataclass describing one upload unit
  UploadManager        – QObject that owns the queue and worker lifecycle
  PRIORITY_SINGLE      – priority for user-initiated single uploads
  PRIORITY_MASS        – priority for user-initiated mass uploads
  PRIORITY_SYNC        – priority for background sync uploads

Tabs enqueue UploadJob instances and subscribe to the manager's signals.
The manager never knows about tab widgets; callers correlate via the
job ID and the optional ``ref`` they attached to the job.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QTimer, Signal

from .workers import UploadWorker

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
        self, client, max_concurrency: int = DEFAULT_GLOBAL_CONCURRENCY, parent=None
    ):
        super().__init__(parent)
        self._client = client
        self._max_concurrency = max(1, int(max_concurrency))
        self._pending: list[tuple[int, UploadJob]] = []
        self._active: dict[int, UploadWorker] = {}
        self._draining: list[UploadWorker] = []
        self._drain_timer: QTimer | None = None
        self._next_id = 1

    # ── Public API ──────────────────────────────────────────────────────────

    def enqueue(self, job: UploadJob) -> int:
        """Queue a job and return its job ID.  Scheduling happens immediately."""
        job_id = self._next_id
        self._next_id += 1
        self._pending.append((job_id, job))
        self._schedule()
        return job_id

    def cancel(self, job_id: int):
        """Cancel one job: stop its worker if active, drop it if pending."""
        worker = self._active.pop(job_id, None)
        if worker is not None:
            worker.cancel()
            self._draining.append(worker)
            self._ensure_drain_timer()
        else:
            self._pending = [(jid, j) for jid, j in self._pending if jid != job_id]
        self._schedule()

    def cancel_all(self):
        """Cancel every active and pending job."""
        for worker in self._active.values():
            worker.cancel()
            self._draining.append(worker)
        self._active.clear()
        self._pending.clear()
        if self._draining:
            self._ensure_drain_timer()
        self._schedule()

    def set_concurrency(self, max_concurrency: int):
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

    def _schedule(self):
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

    def _launch(self, job_id: int, job: UploadJob):
        w = UploadWorker(
            self._client,
            job.file_pairs,
            job.create_share,
            job.share_expiry_hours,
            job.share_max_downloads,
            chunk_size_mb=job.chunk_size_mb,
            max_chunks=job.max_chunks,
        )
        setattr(w, "_job_id", job_id)
        setattr(w, "_job_ref", job.ref)
        setattr(w, "_job_source", job.source)

        w.progress.connect(
            lambda pct, jid=job_id, ref=job.ref: self.job_progress.emit(jid, ref, pct)
        )
        w.speed.connect(
            lambda bps, jid=job_id, ref=job.ref: self.job_speed.emit(jid, ref, bps)
        )
        w.status.connect(
            lambda msg, jid=job_id, ref=job.ref: self.job_status.emit(jid, ref, msg)
        )
        w.finished.connect(
            lambda result, jid=job_id, ref=job.ref: self._on_done(jid, ref, result)
        )
        w.error.connect(
            lambda msg, jid=job_id, ref=job.ref: self._on_error(jid, ref, msg)
        )
        if hasattr(w, "bytes_progress"):
            w.bytes_progress.connect(
                lambda done, total, jid=job_id, ref=job.ref: self.job_bytes.emit(
                    jid, ref, done, total
                )
            )

        self._active[job_id] = w
        w.start()

    # ── Worker completion ───────────────────────────────────────────────────

    def _on_done(self, job_id: int, ref, result: dict):
        self._active.pop(job_id, None)
        self.job_done.emit(job_id, ref, result)
        self._schedule()

    def _on_error(self, job_id: int, ref, msg: str):
        self._active.pop(job_id, None)
        self.job_error.emit(job_id, ref, msg)
        self._schedule()

    # ── Cancelled-worker cleanup ───────────────────────────────────────────
    # A cancelled UploadWorker returns from run() without emitting its
    # finished signal, so the manager can't rely on _on_done to release it.
    # Keep cancelled workers referenced (never GC a running QThread) and
    # sweep them once their thread reports finished.

    def _ensure_drain_timer(self):
        if self._drain_timer is None:
            self._drain_timer = QTimer(self)
            self._drain_timer.setInterval(500)
            self._drain_timer.timeout.connect(self._sweep_draining)
            self._drain_timer.start()

    def _sweep_draining(self):
        self._draining = [w for w in self._draining if not w.isFinished()]
        if not self._draining and self._drain_timer is not None:
            self._drain_timer.stop()
            self._drain_timer = None
            self._schedule()
