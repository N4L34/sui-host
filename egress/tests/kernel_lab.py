"""Run only inside disposable --network none --cap-add NET_ADMIN container.

Never invoke against the host. Tests create their own dummy link and only the
owned sui_egress tables. The test container is removed after completion.
"""

import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from egress.enforcement import ruleset, preserve_scopes
from egress.guard import interfaces, allowed
from egress.tests.test_enforcement import inventory


def run(*args, input=None):
    result = subprocess.run(args, input=input, capture_output=True, timeout=5)
    if result.returncode:
        raise AssertionError("isolated_kernel_operation_failed")


class KernelPolicy(unittest.TestCase):
    def test_nft_transaction_packet_deny_and_identity_guard(self):
        self.assertEqual(os.getuid(), 0, "test_container_root_required")
        run("ip", "link", "add", "fixture0", "type", "dummy")
        run("ip", "link", "set", "fixture0", "address", "02:42:ac:1d:28:0a")
        run("ip", "addr", "add", "172.29.40.10/32", "dev", "fixture0")
        run("ip", "link", "set", "fixture0", "up")
        observed = interfaces()
        self.assertEqual(observed, [{"ip": "172.29.40.10", "mac": "02:42:ac:1d:28:0a"}])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ready.json"
            Path(directory).chmod(0o755)
            data = {
                "boot_id": "boot-fixture",
                "identities": {
                    "worker": {"token": "fixed-token", "interfaces": observed}
                },
            }
            path.write_text(json.dumps(data))
            path.chmod(0o644)
            self.assertTrue(allowed(path, "worker", "fixed-token", "boot-fixture"))
            self.assertFalse(allowed(path, "worker", "fixed-token", "previous-boot"))
            for wrong in (
                [{"ip": "172.29.40.11", "mac": observed[0]["mac"]}],
                [dict(observed[0], mac="02:42:ac:1d:28:0b")],
                observed + [{"ip": "172.29.40.12", "mac": "02:42:ac:1d:28:0c"}],
            ):
                self.assertFalse(
                    allowed(path, "worker", "fixed-token", "boot-fixture", actual=wrong)
                )
            path.chmod(0o666)
            self.assertFalse(allowed(path, "worker", "fixed-token", "boot-fixture"))
        value = inventory()
        text = ruleset(value).encode()
        run("nft", "--check", "-f", "-", input=text)
        run("nft", "-f", "-", input=text)
        # A real TCP dial from the selected source to an unlisted local target
        # is denied. An unaffected source remains usable in the same namespace.
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            port = server.getsockname()[1]
            with socket.socket() as client:
                client.bind(("172.29.40.10", 0))
                client.settimeout(0.2)
                with self.assertRaises(OSError):
                    client.connect(("127.0.0.1", port))
            with socket.create_connection(("127.0.0.1", port), timeout=0.5) as client:
                accepted, _ = server.accept()
                accepted.close()
        # A transactional replacement remains valid with retired deny scopes.
        newer = copy.deepcopy(value)
        newer["identities"][0]["interfaces"][0].update(
            ip="172.29.40.11", mac="02:42:ac:1d:28:0b"
        )
        merged = preserve_scopes(value, newer)
        run("nft", "--check", "-f", "-", input=ruleset(merged, replace=True).encode())
        run("nft", "-f", "-", input=ruleset(merged, replace=True).encode())
        with socket.socket() as client:
            client.bind(("172.29.40.10", 0))
            client.settimeout(0.2)
            with self.assertRaises(OSError):
                client.connect(("127.0.0.1", port))


class DirectRouting(unittest.TestCase):
    def test_direct_and_proxy_domains_sharing_ip_never_fail_open(self):
        import contextlib, ssl, socketserver
        from egress.tests import singbox_lab
        from egress.compiler import _policy_rules
        from egress.probes import Probe, probe
        from lab import certificate, Server, headers

        singbox_lab.BINARY = "/usr/local/bin/sing-box"
        singbox_lab.GOST = "/usr/local/bin/gost"
        run("ip", "addr", "add", "8.8.4.4/32", "dev", "lo")
        with tempfile.TemporaryDirectory() as directory:
            ca, tls = certificate(directory)
            seen = []

            class Handler(socketserver.BaseRequestHandler):
                def handle(self):
                    self.request.settimeout(3)
                    with tls.wrap_socket(self.request, server_side=True) as conn:
                        seen.append(headers(conn))
                        conn.sendall(
                            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
                        )

            with Server(("8.8.4.4", 443), Handler) as origin:
                worker = threading.Thread(target=origin.serve_forever, daemon=True)
                worker.start()
                lab = singbox_lab.Conformance()
                lab.ca = ca
                lab.tls = tls
                bundle, ports = lab.config(
                    ("127.0.0.10", 1),
                    addresses={
                        "origin.test.invalid": ["8.8.4.4"],
                        "direct.test.invalid": ["8.8.4.4"],
                    },
                )
                route = {
                    "id": "direct",
                    "action": "DIRECT",
                    "matcher": {"kind": "exact", "value": "direct.test.invalid"},
                    "ports": [443],
                }
                bundle["gateway"]["singbox"]["route"]["rules"][-1:-1] = _policy_rules(
                    {"routes": [route]}, ["demo-pilot-http"], ["127.0.0.1"]
                )
                try:
                    with lab.gateway(bundle):
                        self.assertEqual(
                            probe(
                                Probe(
                                    "demo",
                                    "direct",
                                    "http",
                                    "127.0.0.1",
                                    ports["demo-pilot-http"],
                                    "direct.test.invalid",
                                    443,
                                    ca,
                                ),
                                timeout=2,
                            ).reason,
                            "ok",
                        )
                        self.assertNotEqual(lab.attempt(ports).reason, "ok")
                    self.assertEqual(len(seen), 1)
                finally:
                    origin.shutdown()
                    worker.join(2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
