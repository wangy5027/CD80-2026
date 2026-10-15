"""Cancellation ownership tests; no network or provider credentials."""

import socket
import ssl
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

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

    def test_windows_native_handle_is_closed_once_even_if_shutdown_fails(self):
        class DetachedSocket:
            def __init__(self):
                self.detaches = 0

            def shutdown(self, _how):
                raise OSError('already shut down')

            def detach(self):
                self.detaches += 1
                return 12345

        upstream_socket = DetachedSocket()
        response = RecordingResponse(SimpleNamespace(sock=upstream_socket))
        transport = AbortableResponse(response)
        with patch('backend.app.modules.workbench.transport._WINDOWS', True), \
                patch('backend.app.modules.workbench.transport.socket.close') as close_handle:
            transport.abort()
            transport.abort()
        self.assertEqual(upstream_socket.detaches, 1)
        close_handle.assert_called_once_with(12345)
        self.assertEqual(response.closes, [])
        transport.finish()
        self.assertEqual(response.closes, [threading.get_ident()])

    def test_actual_buffered_socket_reader_is_interrupted_without_peer_release(self):
        local, peer = socket.socketpair()
        local.settimeout(5)
        buffered = local.makefile('rb')
        local.close()  # HTTP/1.0 commonly leaves only makefile() owning the handle.
        response = RecordingResponse(SimpleNamespace(sock=None))
        response.raw._fp = SimpleNamespace(fp=buffered)
        transport = AbortableResponse(response)
        entered = threading.Event()
        outcomes = []

        def read():
            entered.set()
            try:
                outcomes.append(buffered.read(1))
            except OSError as error:
                outcomes.append(error)
            finally:
                buffered.close()
                transport.finish()

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        try:
            self.assertTrue(entered.wait(1))
            transport.abort()
            reader.join(1)
            self.assertFalse(reader.is_alive(), 'The actual socket reader still waits for its peer')
            self.assertEqual(len(outcomes), 1)
            self.assertEqual(response.closes, [reader.ident])
        finally:
            peer.close()
            reader.join(6)
            buffered.close()
            local.close()

    def test_windows_abort_detaches_ssl_socket_without_closing_http_response(self):
        local, peer = socket.socketpair()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        wrapped = context.wrap_socket(local, server_hostname='localhost', do_handshake_on_connect=False)
        response = RecordingResponse(SimpleNamespace(sock=wrapped))
        transport = AbortableResponse(response)
        try:
            with patch('backend.app.modules.workbench.transport._WINDOWS', True):
                transport.abort()
            self.assertEqual(wrapped.fileno(), -1)
            self.assertEqual(response.closes, [])
            transport.finish()
            self.assertEqual(response.closes, [threading.get_ident()])
        finally:
            wrapped.close()
            peer.close()


if __name__ == "__main__":
    unittest.main()
