"""remote_cache.py — Shared in-memory cache for all remote API data.

Keyed by (op, **kwargs) so each unique list path / shares / jobs
gets its own cache slot.  The background poller refreshes every
POLL_INTERVAL seconds.  Tabs subscribe via on_update(key, data)
callbacks and are always served stale data instantly while a
fresh fetch runs behind the scenes.
"""

from __future__ import annotations

import contextlib
import threading
import time
from functools import partial
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QThread, Signal

from .logging_utils import write_debug_log

if TYPE_CHECKING:
    from collections.abc import Callable

POLL_INTERVAL = 15  # seconds between refreshes
_CACHE_VERSION = 0  # bumped on invalidation so stale renders never block


# ── Cache store ───────────────────────────────────────────────────────────────
class _CacheStore:
    """Thread-safe key → {data, ts, version} dictionary."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}

    def _key(self, op: str, **kwargs: Any) -> str:
        parts = [op] + [f"{k}={v}" for k, v in sorted(kwargs.items())]
        return "|".join(parts)

    def get(self, op: str, **kwargs: Any) -> Any | None:
        """Return cached data or None if never fetched."""
        with self._lock:
            entry = self._data.get(self._key(op, **kwargs))
            return entry["data"] if entry else None

    def set(self, op: str, data: Any, **kwargs: Any) -> None:
        with self._lock:
            self._data[self._key(op, **kwargs)] = {
                "data": data,
                "ts": time.monotonic(),
            }

    def invalidate(self, op: str, **kwargs: Any) -> None:
        """Remove one entry so the next poll fetches it immediately."""
        with self._lock:
            self._data.pop(self._key(op, **kwargs), None)

    def invalidate_op(self, op: str) -> None:
        """Remove all entries for an op (e.g. invalidate all 'list' paths)."""
        prefix = f"{op}|"
        with self._lock:
            stale = [k for k in self._data if k == op or k.startswith(prefix)]
            for k in stale:
                del self._data[k]

    def age(self, op: str, **kwargs: Any) -> float:
        """Seconds since last fetch, or inf if never fetched."""
        with self._lock:
            entry = self._data.get(self._key(op, **kwargs))
            return time.monotonic() - entry["ts"] if entry else float("inf")


# Module-level singleton
cache = _CacheStore()


# ── Subscriber registry ───────────────────────────────────────────────────────
class _SubscriberRegistry:
    """Maps (op, kwargs_key) → list of callables.
    Each callable is called with (op, data) whenever the cache for that
    key is refreshed.  Callables are held weakly so dead tabs don't leak.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: dict[str, list[Callable]] = {}

    def _key(self, op: str, **kwargs: Any) -> str:
        return cache._key(op, **kwargs)

    def subscribe(self, op: str, callback: Callable, **kwargs: Any) -> None:
        k = self._key(op, **kwargs)
        with self._lock:
            self._subs.setdefault(k, [])
            if callback not in self._subs[k]:
                self._subs[k].append(callback)

    def unsubscribe(self, op: str, callback: Callable, **kwargs: Any) -> None:
        k = self._key(op, **kwargs)
        with self._lock:
            lst = self._subs.get(k, [])
            if callback in lst:
                lst.remove(callback)

    def notify(self, op: str, data: Any, **kwargs: Any) -> None:
        k = self._key(op, **kwargs)
        with self._lock:
            cbs = list(self._subs.get(k, []))
        for cb in cbs:
            with contextlib.suppress(Exception):
                cb(data)


registry = _SubscriberRegistry()


# ── Poll worker ───────────────────────────────────────────────────────────────
class CachePollWorker(QThread):
    """Fetches one (op, kwargs) slot and emits refreshed(op, data, kwargs_tuple).
    Used by the poller to do network I/O off the main thread.
    """

    refreshed = Signal(str, object, object)  # op, data, kwargs_dict

    def __init__(self, op: str, client: Any, kwargs: dict) -> None:
        super().__init__()
        self.op = op
        self._client = client
        self.kwargs = kwargs

    def run(self) -> None:
        try:
            if self.op == "list":
                path = self.kwargs.get("path", "/")
                data = self._client.list_files(path)

            elif self.op == "shares":
                data = self._client.list_shares()

            elif self.op == "jobs":
                active_only = self.kwargs.get("active_only", True)
                data = self._client.list_transfer_jobs(active_only=active_only)

            else:
                return  # unknown op — skip

            cache.set(self.op, data, **self.kwargs)
            self.refreshed.emit(self.op, data, self.kwargs)

        except (AttributeError, TypeError, RuntimeError) as e:
            # Network error: keep old cache data, don't notify
            write_debug_log(f"[Silenced] run: {e}")


# ── Background poller ─────────────────────────────────────────────────────────
class CachePoller(QObject):
    """Keeps a set of (op, api_key_getter, base_url, kwargs) subscriptions and
    re-fetches each one every POLL_INTERVAL seconds.

    Usage:
        poller = CachePoller(client)
        poller.add("list",   path="/")
        poller.add("shares")
        poller.start()
        poller.stop()
    """

    def __init__(self, client: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._client: object = client
        self._slots: list[dict] = []
        self._workers: list[CachePollWorker] = []
        self._timer = None
        self._lock = threading.Lock()

    def add(self, op: str, **kwargs: Any) -> None:
        """Register a slot to be polled.  Idempotent."""
        with self._lock:
            for s in self._slots:
                if s["op"] == op and s["kwargs"] == kwargs:
                    return
            self._slots.append(
                {
                    "op": op,
                    "kwargs": kwargs,
                },
            )

    def remove(self, op: str, **kwargs: Any) -> None:
        with self._lock:
            self._slots = [
                s for s in self._slots if not (s["op"] == op and s["kwargs"] == kwargs)
            ]
            if not self._slots:
                self.stop()

    def start(self) -> None:
        from PySide6.QtCore import QTimer

        if self._timer is None:
            self._timer = QTimer()
            self._timer.setInterval(POLL_INTERVAL * 1000)
            self._timer.timeout.connect(self._poll)
        self._timer.start()
        # Immediate first fetch
        self._poll()

    def stop(self) -> None:
        if self._timer:
            self._timer.stop()

    def force_refresh(self, op: str | None = None, **kwargs: Any) -> None:
        """Invalidate cache and trigger an immediate poll."""
        if op:
            if kwargs:
                cache.invalidate(op, **kwargs)
            else:
                cache.invalidate_op(op)
        self._poll()

    def _poll(self) -> None:
        with self._lock:
            slots = list(self._slots)

        # Clean up finished workers
        self._workers = [w for w in self._workers if not w.isFinished()]

        # Skip slots that already have an in-flight fetch, so a concurrent
        # force_refresh + start() doesn't spawn duplicate requests.
        in_flight = {(w.op, tuple(sorted(w.kwargs.items()))) for w in self._workers}
        for slot in slots:
            key = (slot["op"], tuple(sorted(slot["kwargs"].items())))
            if key in in_flight:
                continue
            w = CachePollWorker(slot["op"], self._client, slot["kwargs"])
            w.refreshed.connect(self._on_refreshed)
            w.finished.connect(partial(self._remove_worker, w))
            self._workers.append(w)
            w.start()

    def _remove_worker(self, w: CachePollWorker) -> None:
        if w in self._workers:
            self._workers.remove(w)

    def _on_refreshed(self, op: str, data: object, kwargs: dict) -> None:
        registry.notify(op, data, **kwargs)
