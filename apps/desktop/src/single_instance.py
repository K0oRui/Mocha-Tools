"""single_instance.py — Prevent multiple running instances of Mocha Tools.

The first instance to start listens on a named QLocalServer.  Any later
instance connects to that server, asks it to focus the existing window,
and exits immediately.

Public API
----------
  ensure_single_instance(on_activate) -> bool
"""

from __future__ import annotations

import contextlib
from functools import partial
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .constants import APP_NAME, ORG_NAME

if TYPE_CHECKING:
    from collections.abc import Callable

_SERVER_NAME = f"{ORG_NAME}-{APP_NAME}-instance"
_CONNECT_TIMEOUT_MS = 500
_ACTIVATE_MSG = b"show"
_ACK_MSG = b"ok"


class _SingleInstanceGuard(QObject):
    """Owns the QLocalServer so it stays alive for the app's lifetime."""

    def __init__(self, on_activate: Callable[[], None]) -> None:
        super().__init__()
        self._on_activate = on_activate
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)

    def _on_new_connection(self) -> None:
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            sock.readyRead.connect(partial(self._on_ready_read, sock))
            sock.disconnected.connect(sock.deleteLater)

    def _on_ready_read(self, sock: QLocalSocket) -> None:
        data = sock.readAll().data()
        if _ACTIVATE_MSG in data:
            with contextlib.suppress(Exception):
                self._on_activate()
        sock.write(_ACK_MSG)
        sock.flush()
        sock.disconnectFromServer()


def _notify_existing_instance() -> bool:
    """Return True if a running instance accepted our activation request."""
    sock = QLocalSocket()
    sock.connectToServer(_SERVER_NAME)
    if not sock.waitForConnected(_CONNECT_TIMEOUT_MS):
        return False
    sock.write(_ACTIVATE_MSG)
    sock.flush()
    # Wait for the server's ack so the message is processed before we exit.
    if not sock.waitForReadyRead(_CONNECT_TIMEOUT_MS):
        sock.disconnectFromServer()
        return True
    sock.disconnectFromServer()
    return True


_guard_refs: list[_SingleInstanceGuard] = []


def ensure_single_instance(on_activate: Callable[[], None]) -> bool:
    """Return True if this process should continue starting up.

    If another instance is already running, notify it to focus its window
    and return False so the caller exits.
    """
    if _notify_existing_instance():
        return False

    guard = _SingleInstanceGuard(on_activate)
    QLocalServer.removeServer(_SERVER_NAME)
    if not guard._server.listen(_SERVER_NAME):
        # A stale server may still hold the name; clear it and retry once.
        QLocalServer.removeServer(_SERVER_NAME)
        if not guard._server.listen(_SERVER_NAME):
            # Lost the race to another instance starting at the same time.
            return not _notify_existing_instance()
    _guard_refs.append(guard)
    return True
