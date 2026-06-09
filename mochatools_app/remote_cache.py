"""
remote_cache.py — Shared in-memory cache for all remote API data.

Keyed by (op, **kwargs) so each unique list path / shares / jobs
gets its own cache slot.  The background poller refreshes every
POLL_INTERVAL seconds.  Tabs subscribe via on_update(key, data)
callbacks and are always served stale data instantly while a
fresh fetch runs behind the scenes.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from PyQt6.QtCore import QThread, pyqtSignal, QObject

POLL_INTERVAL = 5          # seconds between refreshes
_CACHE_VERSION = 0         # bumped on invalidation so stale renders never block


# ── Cache store ───────────────────────────────────────────────────────────────
class _CacheStore:
    """Thread-safe key → {data, ts, version} dictionary."""

    def __init__(self):
        self._lock  = threading.Lock()
        self._data: dict[str, dict] = {}

    def _key(self, op: str, **kwargs) -> str:
        parts = [op] + [f"{k}={v}" for k, v in sorted(kwargs.items())]
        return "|".join(parts)

    def get(self, op: str, **kwargs) -> Any | None:
        """Return cached data or None if never fetched."""
        with self._lock:
            entry = self._data.get(self._key(op, **kwargs))
            return entry["data"] if entry else None

    def set(self, op: str, data: Any, **kwargs):
        with self._lock:
            self._data[self._key(op, **kwargs)] = {
                "data": data,
                "ts":   time.monotonic(),
            }

    def invalidate(self, op: str, **kwargs):
        """Remove one entry so the next poll fetches it immediately."""
        with self._lock:
            self._data.pop(self._key(op, **kwargs), None)

    def invalidate_op(self, op: str):
        """Remove all entries for an op (e.g. invalidate all 'list' paths)."""
        prefix = f"{op}|"
        with self._lock:
            stale = [k for k in self._data if k == op or k.startswith(prefix)]
            for k in stale:
                del self._data[k]

    def age(self, op: str, **kwargs) -> float:
        """Seconds since last fetch, or inf if never fetched."""
        with self._lock:
            entry = self._data.get(self._key(op, **kwargs))
            return time.monotonic() - entry["ts"] if entry else float("inf")


# Module-level singleton
cache = _CacheStore()


# ── Subscriber registry ───────────────────────────────────────────────────────
class _SubscriberRegistry:
    """
    Maps (op, kwargs_key) → list of callables.
    Each callable is called with (op, data) whenever the cache for that
    key is refreshed.  Callables are held weakly so dead tabs don't leak.
    """

    def __init__(self):
        self._lock  = threading.Lock()
        self._subs: dict[str, list[Callable]] = {}

    def _key(self, op: str, **kwargs) -> str:
        return cache._key(op, **kwargs)

    def subscribe(self, op: str, callback: Callable, **kwargs):
        k = self._key(op, **kwargs)
        with self._lock:
            self._subs.setdefault(k, [])
            if callback not in self._subs[k]:
                self._subs[k].append(callback)

    def unsubscribe(self, op: str, callback: Callable, **kwargs):
        k = self._key(op, **kwargs)
        with self._lock:
            lst = self._subs.get(k, [])
            if callback in lst:
                lst.remove(callback)

    def notify(self, op: str, data: Any, **kwargs):
        k = self._key(op, **kwargs)
        with self._lock:
            cbs = list(self._subs.get(k, []))
        for cb in cbs:
            try:
                cb(data)
            except Exception:
                pass


registry = _SubscriberRegistry()


# ── Poll worker ───────────────────────────────────────────────────────────────
class CachePollWorker(QThread):
    """
    Fetches one (op, kwargs) slot and emits refreshed(op, data, kwargs_tuple).
    Used by the poller to do network I/O off the main thread.
    """
    refreshed = pyqtSignal(str, object, object)   # op, data, kwargs_dict

    def __init__(self, op: str, api_key: str, base_url: str, kwargs: dict):
        super().__init__()
        self.op       = op
        self.api_key  = api_key
        self.base_url = base_url.rstrip("/")
        self.kwargs   = kwargs

    # (connect_timeout, read_timeout) — fail fast on unreachable hosts without
    # cutting off slow-but-alive transfers.
    _TIMEOUT = (5, 60)

    def run(self):
        import requests as _req
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            if self.op == "list":
                path = self.kwargs.get("path", "/")
                resp = _req.get(
                    f"{self.base_url}/api/files",
                    headers=headers,
                    params={"path": path, "includeSubfolders": "0"},
                    timeout=self._TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json()

            elif self.op == "shares":
                resp = _req.get(
                    f"{self.base_url}/api/shares",
                    headers=headers,
                    timeout=self._TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json()

            elif self.op == "jobs":
                active_only = self.kwargs.get("active_only", True)
                params = {"active": "true"} if active_only else {}
                resp = _req.get(
                    f"{self.base_url}/api/admin/transfer-jobs",
                    headers=headers,
                    params=params,
                    timeout=self._TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json()

            else:
                return   # unknown op — skip

            cache.set(self.op, data, **self.kwargs)
            self.refreshed.emit(self.op, data, self.kwargs)

        except Exception:
            # Network error: keep old cache data, don't notify
            pass


# ── Background poller ─────────────────────────────────────────────────────────
class CachePoller(QObject):
    """
    Keeps a set of (op, api_key_getter, base_url, kwargs) subscriptions and
    re-fetches each one every POLL_INTERVAL seconds.

    Usage:
        poller = CachePoller()
        poller.add("list",   get_api_key, BASE_URL, path="/")
        poller.add("shares", get_api_key, BASE_URL)
        poller.start()
        poller.stop()
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._slots:    list[dict] = []
        self._workers:  list[CachePollWorker] = []
        self._timer     = None
        self._lock      = threading.Lock()

    def add(self, op: str, get_api_key: Callable[[], str],
            base_url: str, **kwargs):
        """Register a slot to be polled.  Idempotent."""
        with self._lock:
            for s in self._slots:
                if s["op"] == op and s["kwargs"] == kwargs:
                    return
            self._slots.append({
                "op":          op,
                "get_api_key": get_api_key,
                "base_url":    base_url,
                "kwargs":      kwargs,
            })

    def remove(self, op: str, **kwargs):
        with self._lock:
            self._slots = [
                s for s in self._slots
                if not (s["op"] == op and s["kwargs"] == kwargs)
            ]

    def start(self):
        from PyQt6.QtCore import QTimer
        if self._timer is None:
            self._timer = QTimer()
            self._timer.setInterval(POLL_INTERVAL * 1000)
            self._timer.timeout.connect(self._poll)
        self._timer.start()
        # Immediate first fetch
        self._poll()

    def stop(self):
        if self._timer:
            self._timer.stop()

    def force_refresh(self, op: str | None = None, **kwargs):
        """Invalidate cache and trigger an immediate poll."""
        if op:
            if kwargs:
                cache.invalidate(op, **kwargs)
            else:
                cache.invalidate_op(op)
        self._poll()

    def _poll(self):
        with self._lock:
            slots = list(self._slots)

        # Clean up finished workers
        self._workers = [w for w in self._workers if not w.isFinished()]

        for slot in slots:
            api_key = slot["get_api_key"]()
            if not api_key:
                continue
            w = CachePollWorker(
                slot["op"], api_key, slot["base_url"], slot["kwargs"]
            )
            w.refreshed.connect(self._on_refreshed)
            w.finished.connect(lambda _w=w: self._workers.remove(_w)
                               if _w in self._workers else None)
            self._workers.append(w)
            w.start()

    def _on_refreshed(self, op: str, data: object, kwargs: dict):
        registry.notify(op, data, **kwargs)