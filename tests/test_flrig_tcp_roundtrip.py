"""FLRig-TCP: XML-RPC-Roundtrip und Bridge-Logging."""

from __future__ import annotations

import socket
import threading
import time
import unittest

from rig_bridge.protocol_flrig import FlrigBridgeServer
from rig_bridge.state import RadioStateCache


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


class FlrigTcpRoundtripTest(unittest.TestCase):
    def test_main_get_version_xmlrpc(self) -> None:
        port = _free_port()
        state = RadioStateCache()
        state.update(frequency_hz=14_074_000, mode="USB")
        log_lines: list[str] = []
        srv = FlrigBridgeServer(
            get_state=state.snapshot,
            enqueue_write=lambda *_a, **_k: None,
            on_clients_changed=lambda _n: None,
            log_write=lambda _lvl, msg: log_lines.append(msg),
            log_client_traffic=True,
        )
        srv.start("127.0.0.1", port)
        try:
            body = (
                '<?xml version="1.0"?><methodCall>'
                "<methodName>main.get_version</methodName>"
                "<params></params></methodCall>"
            )
            req = (
                "POST /RPC2 HTTP/1.1\r\n"
                "Host: 127.0.0.1\r\n"
                "Content-Type: text/xml\r\n"
                f"Content-Length: {len(body.encode('utf-8'))}\r\n"
                "\r\n"
                f"{body}"
            ).encode("utf-8")
            with socket.create_connection(("127.0.0.1", port), timeout=5.0) as sock:
                sock.settimeout(5.0)
                sock.sendall(req)
                resp = b""
                while b"\r\n\r\n" not in resp and len(resp) < 65536:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    resp += chunk
            self.assertIn(b"HTTP/1.1 200", resp)
            self.assertIn(b"1.4.2", resp)
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if any("main.get_version" in ln for ln in log_lines):
                    break
                time.sleep(0.05)
            self.assertTrue(
                any("Client verbunden" in ln for ln in log_lines),
                f"connect log missing: {log_lines!r}",
            )
            self.assertTrue(
                any("main.get_version" in ln for ln in log_lines),
                f"method log missing: {log_lines!r}",
            )
        finally:
            srv.stop()

    def test_connect_always_logged_without_tcp_detail(self) -> None:
        port = _free_port()
        log_lines: list[str] = []
        srv = FlrigBridgeServer(
            get_state=RadioStateCache().snapshot,
            enqueue_write=lambda *_a, **_k: None,
            on_clients_changed=lambda _n: None,
            log_write=lambda _lvl, msg: log_lines.append(msg),
            log_client_traffic=False,
        )
        srv.start("127.0.0.1", port)
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=3.0)
            time.sleep(0.15)
            sock.close()
            time.sleep(0.2)
            self.assertTrue(
                any("Client verbunden" in ln for ln in log_lines),
                log_lines,
            )
            self.assertFalse(
                any("HTTP-Rohdaten" in ln for ln in log_lines),
                log_lines,
            )
        finally:
            srv.stop()


if __name__ == "__main__":
    unittest.main()
