"""Shared cancellation helper for Gitee HTTP response streams."""

from __future__ import annotations

import socket
import threading

import requests


class AbortableResponse:
    """Wake a blocked reader; let that reader close its own HTTP response."""

    def __init__(self, response: requests.Response):
        self.response = response
        self._lock = threading.Lock()
        self._aborted = False
        self._finished = False

    def _socket(self):
        raw = getattr(self.response, "raw", None)
        connection = getattr(raw, "_connection", None)
        direct = getattr(connection, "sock", None)
        if direct is not None:
            return direct
        fp = getattr(raw, "_fp", None)
        buffered = getattr(fp, "fp", None)
        stream = getattr(buffered, "raw", None)
        return getattr(stream, "_sock", None)

    def abort(self):
        """Wake the reader without closing its file object from another thread.

        Concurrent ``response.close()`` and ``iter_content()`` can race inside
        ``http.client._close_conn``.  Shutting down the socket unblocks the read
        while the reading thread remains responsible for the eventual close.
        """
        with self._lock:
            if self._aborted or self._finished:
                return
            self._aborted = True
            try:
                connection = self._socket()
                if connection is not None:
                    connection.shutdown(socket.SHUT_RDWR)
            except (OSError, AttributeError, ValueError):
                # A completed response may already have detached its socket.
                pass

    def finish(self):
        """Close once, from the thread that owns the response reader."""
        with self._lock:
            if self._finished:
                return
            self._finished = True
        self.response.close()
