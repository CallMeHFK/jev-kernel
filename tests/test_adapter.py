"""Adapter robustness tests: malformed POSTs get a 400, never a wedge."""

from __future__ import annotations

import importlib.util
import socket
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

ADAPTER_PATH = Path(__file__).resolve().parent.parent / "scripts" / "systemone_adapter.py"


def load_adapter():
    spec = importlib.util.spec_from_file_location("jev_systemone_adapter", ADAPTER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class AdapterMalformedInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adapter = load_adapter()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), cls.adapter.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server = None

    def _raw_exchange(self, head: bytes, body: bytes = b"") -> bytes:
        """Send a raw request, half-close the write side, read the response."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.sendall(head + body)
            sock.shutdown(socket.SHUT_WR)
            sock.settimeout(5)
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)

    def _assert_alive(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.sendall(b"GET / HTTP/1.0\r\nHost: x\r\n\r\n")
            sock.settimeout(5)
            resp = sock.makefile("rb").read()
        self.assertIn(b" 200 ", resp.split(b"\r\n", 1)[0])
        self.assertIn(b'"status": "ok"', resp)

    def test_malformed_posts_return_400(self):
        # truncated JSON body: Content-Length promises more than arrives
        head = (
            b"POST /v1/systemone HTTP/1.0\r\nHost: x\r\n"
            b"Content-Type: application/json\r\nContent-Length: 64\r\n\r\n"
        )
        resp = self._raw_exchange(head, b'{"questions": {')  # short of 64 bytes
        self.assertIn(b" 400 ", resp.split(b"\r\n", 1)[0])
        self._assert_alive()

        # non-numeric Content-Length
        head = (
            b"POST /v1/systemone HTTP/1.0\r\nHost: x\r\n"
            b"Content-Type: application/json\r\nContent-Length: abc\r\n\r\n"
        )
        resp = self._raw_exchange(head)
        self.assertIn(b" 400 ", resp.split(b"\r\n", 1)[0])
        self._assert_alive()


if __name__ == "__main__":
    unittest.main(verbosity=2)
