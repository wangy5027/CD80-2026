"""Cancellation ownership tests; no network or provider credentials."""

import socket
import threading
import unittest
from types import SimpleNamespace

from backend.app.modules.workbench.transport import AbortableResponse


class RecordingSocket:
    def __init__(self):
        self.shutdowns = []

    def shutdown(self, how):
        self.shutdowns.append((how, threading.get_ident()))


class RecordingResponse:
    def __init__(self, connection):
        self.raw = SimpleNamespace(_connection=connection)
        self.closes = []

    def close(self):
        self.closes.append(threading.get_ident())


class AbortableResponseTests(unittest.TestCase):
    def test_abort_wakes_socket_without_closing_reader_response(self):
        upstream_socket = RecordingSocket()
        response = RecordingResponse(SimpleNamespace(sock=upstream_socket))
        transport = AbortableResponse(response)
        caller = threading.get_ident()

        transport.abort()
        transport.abort()
        self.assertEqual(upstream_socket.shutdowns, [(socket.SHUT_RDWR, caller)])
        self.assertEqual(response.closes, [])

        reader = threading.Thread(target=transport.finish)
        reader.start()
        reader.join(timeout=1)
        self.assertFalse(reader.is_alive())
        self.assertEqual(response.closes, [reader.ident])
        transport.finish()
        transport.abort()
        self.assertEqual(response.closes, [reader.ident])
        self.assertEqual(len(upstream_socket.shutdowns), 1)

    def test_no_socket_can_finish_without_cross_thread_close(self):
        response = RecordingResponse(SimpleNamespace(sock=None))
        transport = AbortableResponse(response)
        transport.abort()
        self.assertEqual(response.closes, [])
        transport.finish()
        self.assertEqual(response.closes, [threading.get_ident()])


if __name__ == "__main__":
    unittest.main()
