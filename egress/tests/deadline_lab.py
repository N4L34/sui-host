"""Generic-client native deadlines; socket EOF and upstream closure are oracle.

No Probe timeout is used. Test socket timeouts are longer failure sentinels.
All listeners/destinations remain fixture loopback, no external networking.
"""

import contextlib
from pathlib import Path
import socket
import socketserver
import ssl
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import singbox_lab
from lab import Server, certificate, exact, headers


@contextlib.contextmanager
def withheld(tls, stage):
    state = {"accepted": 0, "closed": 0, "durations": []}
    stopping = threading.Event()

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            started = time.monotonic()
            state["accepted"] += 1
            conn = self.request
            conn.settimeout(0.1)
            try:
                if stage != "tls":
                    conn.settimeout(5)
                    conn = tls.wrap_socket(conn, server_side=True)
                    if stage in ("http", "http-drip"):
                        headers(conn)
                    else:
                        version, count = exact(conn, 2)
                        exact(conn, count)
                        conn.sendall(b"\x05\x02")
                        version, count = exact(conn, 2)
                        exact(conn, count)
                        exact(conn, exact(conn, 1)[0])
                        if stage == "socks-connect":
                            conn.sendall(b"\x01\x00")
                            version, command, reserved, atyp = exact(conn, 4)
                            count = (
                                exact(conn, 1)[0] if atyp == 3 else {1: 4, 4: 16}[atyp]
                            )
                            exact(conn, count + 2)
                        else:
                            conn.sendall(b"\x01")  # incomplete auth reply
                conn.settimeout(0.1)
                next_byte = time.monotonic()
                while not stopping.is_set():
                    try:
                        data = conn.recv(4096)
                        if not data:
                            state["closed"] += 1
                            break
                    except socket.timeout:
                        pass
                    if stage == "http-drip" and time.monotonic() >= next_byte:
                        conn.sendall(b"H")
                        next_byte = time.monotonic() + 0.5
            except (OSError, EOFError):
                state["closed"] += 1
            finally:
                state["durations"].append(time.monotonic() - started)
                conn.close()

    with Server(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address, state
        finally:
            stopping.set()
            server.shutdown()
            thread.join(2)


class NativeDeadlines(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.ca, cls.tls = certificate(cls.directory.name)
        singbox_lab.BINARY = "/usr/local/bin/sing-box"
        singbox_lab.GOST = "/usr/local/bin/gost"

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def lab(self):
        lab = singbox_lab.Conformance()
        lab.ca = self.ca
        lab.tls = self.tls
        return lab

    def rejected(self, ports, ceiling):
        started = time.monotonic()
        with socket.create_connection(
            ("127.0.0.1", ports["demo-pilot-http"]), timeout=2
        ) as client:
            client.settimeout(ceiling + 2)
            client.sendall(
                b"CONNECT origin.test.invalid:443 HTTP/1.1\r\nHost: origin.test.invalid:443\r\n\r\n"
            )
            # sing-box may acknowledge CONNECT before lazily establishing the
            # upstream. Payload makes the native dial happen; no client timer
            # cancels it before the native expected deadline.
            headers(client)
            client.sendall(b"fixture-payload")
            try:
                while client.recv(4096):
                    pass
            except ConnectionResetError:
                pass
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, ceiling)
        return elapsed

    def test_total_tcp_tls_auth_and_connect_handshake_budget(self):
        for stage, transport in [
            ("tls", "https"),
            ("http", "https"),
            ("http-drip", "https"),
            ("socks-auth", "socks5-tls"),
            ("socks-connect", "socks5-tls"),
        ]:
            with self.subTest(stage=stage), withheld(self.tls, stage) as (up, state):
                lab = self.lab()
                bundle, ports = lab.config(up, transport)
                with lab.gateway(bundle):
                    elapsed = self.rejected(ports, 5.9)
                self.assertGreater(state["accepted"], 0)
                self.assertGreater(state["closed"], 0)
                self.assertLess(max(state["durations"]), 5.9)
                self.assertGreater(elapsed, 3.5)

    def test_address_retries_share_fifteen_second_total(self):
        with withheld(self.tls, "http") as (up, state):
            lab = self.lab()
            bundle, ports = lab.config(
                up,
                addresses={
                    "origin.test.invalid": ["8.8.4.4", "8.8.4.5", "8.8.4.6", "8.8.4.7"]
                },
            )
            with lab.gateway(bundle):
                elapsed = self.rejected(ports, 15.9)
            self.assertGreater(elapsed, 12)
            self.assertGreaterEqual(state["accepted"], 2)

    def test_dns_blackhole_is_bounded_by_three_seconds(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as dns:
            dns.bind(("127.0.0.1", 0))
            lab = self.lab()
            bundle, ports = lab.config(("127.0.0.1", 1))
            bundle["gateway"]["singbox"]["dns"] = {
                "servers": [
                    {
                        "type": "udp",
                        "tag": "controlled",
                        "server": "127.0.0.1",
                        "server_port": dns.getsockname()[1],
                    }
                ],
                "final": "controlled",
                "disable_cache": True,
            }
            with lab.gateway(bundle):
                elapsed = self.rejected(ports, 3.9)
            self.assertGreater(elapsed, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
