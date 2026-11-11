import json
import socket
import threading
import time
import unittest
from egress import daemon
from egress.probes import Result
from egress.status import Status


class DaemonTests(unittest.TestCase):
    def test_health_is_cached_and_monitor_loss_changes_liveness(self):
        state = Status("v1", [("demo", "primary", "http")])
        state.tick(time.monotonic())
        server = daemon.health_server("127.0.0.1", 0, state)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:

            def request(path):
                with socket.create_connection(server.server_address, timeout=1) as s:
                    s.sendall(f"GET {path} HTTP/1.0\r\n\r\n".encode())
                    data = b""
                    while part := s.recv(16384):
                        data += part
                    return data

            self.assertIn(b"200 OK", request("/health/live"))
            self.assertIn(b"503 Service Unavailable", request("/health/ready"))
            now = time.monotonic()
            for _ in range(2):
                state.record(("demo", "primary", "http"), Result("ok", 0.001), now)
            self.assertIn(b"200 OK", request("/health/ready"))
            self.assertIn(b"egress_ready 1", request("/metrics"))
            self.assertIn(b"404 Not Found", request("/private"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_staging_changes_all_local_ports_without_remote_change(self):
        value = {
            "singbox": {
                "inbounds": [{"listen_port": 3128}],
                "outbounds": [
                    {"server": "127.0.0.1", "server_port": 18000},
                    {"server": "8.8.8.8", "server_port": 443},
                ],
                "experimental": {
                    "clash_api": {"external_controller": "127.0.0.1:19090"}
                },
            },
            "adapters": [
                {
                    "config": {
                        "services": [{"addr": "172.18.0.2:1080"}],
                        "chains": [
                            {"hops": [{"nodes": [{"addr": "127.0.0.1:24000"}]}]}
                        ],
                    }
                }
            ],
            "probes": [{"proxy_port": 22000}],
        }
        stage = daemon.staged(value)
        self.assertEqual(stage["singbox"]["inbounds"][0]["listen_port"], 33128)
        self.assertEqual(stage["singbox"]["inbounds"][0]["listen"], "127.0.0.1")
        self.assertEqual(stage["singbox"]["outbounds"][1]["server_port"], 443)
        self.assertEqual(value["singbox"]["inbounds"][0]["listen_port"], 3128)


if __name__ == "__main__":
    unittest.main()
