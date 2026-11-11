"""Pinned GOST candidate experiments, explicitly separate from unit tests.

Run from the repository root:
  python3 egress/tests/gost_lab.py --binary /absolute/path/to/gost

Only loopback listeners and fixture targets are used. No Docker, production
files, target DNS or external upstreams. Config files contain artificial auth
and are private temporary files. A green experiment suite is NOT product
acceptance: two tests deliberately demonstrate unsupported safety assumptions.
"""

import argparse
import base64
import contextlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from egress.probes import Probe, probe  # noqa: E402
from lab import certificate, exact, origin, proxy  # noqa: E402

BINARY = None
CANARY = 'artificial:@/\\"雪\nonly'


def unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextlib.contextmanager
def gateway(config, ports):
    with tempfile.TemporaryDirectory(prefix="egress-gost-lab-") as directory:
        path = Path(directory) / "gateway.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(config, stream, ensure_ascii=False)
        # Logs are captured privately and inspected as booleans, never printed.
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(
                [BINARY, "-C", str(path)],
                stdout=output,
                stderr=output,
                env={"PATH": os.defpath},
                cwd=directory,
            )
            try:
                deadline = time.monotonic() + 5
                for port in ports:
                    while True:
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise AssertionError("candidate_start_failed")
                        try:
                            with socket.create_connection(
                                ("127.0.0.1", port), timeout=0.1
                            ):
                                break
                        except OSError:
                            time.sleep(0.02)
                yield
            finally:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
                output.seek(0)
                raw = output.read(1024 * 1024)
                canaries = [
                    CANARY.encode(),
                    json.dumps(CANARY).encode()[1:-1],
                    base64.b64encode(("test:" + CANARY).encode()),
                    b"artificial:",
                ]
                if any(canary in raw for canary in canaries):
                    raise AssertionError("candidate_log_canary_leak")


class CandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="egress-certs-")
        cls.ca, cls.tls = certificate(cls.directory.name)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def config(
        self, upstream, transport, password=CANARY, server_name="upstream.test.invalid"
    ):
        http, socks = unused_port(), unused_port()
        config = {
            "services": [
                {
                    "name": protocol,
                    "addr": f"127.0.0.1:{port}",
                    "bypasses": ["allow", "special"],
                    "handler": {
                        "type": protocol,
                        "chain": "upstream",
                        "retries": 0,
                        "metadata": {"udp": False, "bind": False},
                    },
                    "listener": {"type": "tcp"},
                }
                for protocol, port in [("http", http), ("socks5", socks)]
            ],
            "chains": [
                {
                    "name": "upstream",
                    "hops": [
                        {
                            "name": "primary",
                            "nodes": [
                                {
                                    "name": "primary",
                                    "addr": f"{upstream[0]}:{upstream[1]}",
                                    "connector": {
                                        "type": transport,
                                        "auth": {
                                            "username": "test",
                                            "password": password,
                                        },
                                    },
                                    "dialer": {
                                        "type": "tls",
                                        "tls": {
                                            "caFile": self.ca,
                                            "secure": True,
                                            "serverName": server_name,
                                        },
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
            "bypasses": [
                {
                    "name": "allow",
                    "whitelist": True,
                    "matchers": ["origin.test.invalid:443"],
                },
                {"name": "special", "matchers": ["127.0.0.0/8", "::1", "10.0.0.0/8"]},
            ],
            "log": {"output": "none"},
        }
        return config, (http, socks)

    def attempt(self, protocol, port, target="origin.test.invalid", target_port=443):
        return probe(
            Probe(
                "pilot",
                "primary",
                protocol,
                "127.0.0.1",
                port,
                target,
                target_port,
                self.ca,
            ),
            timeout=2,
        )

    def test_http_and_socks_cross_protocol_with_tls_auth(self):
        for transport in ("http", "socks5"):
            with self.subTest(upstream=transport), origin(self.tls) as (target, seen):
                with proxy(target, transport, self.tls, ("test", CANARY)) as (
                    upstream,
                    auth,
                ):
                    config, ports = self.config(upstream, transport)
                    with gateway(config, ports):
                        for protocol, port in zip(("http", "socks5"), ports):
                            self.assertEqual(self.attempt(protocol, port).reason, "ok")
                    self.assertEqual(auth["auth"], 2)
                    self.assertEqual(seen["requests"], 2)
                    self.assertTrue(
                        all(b"Proxy-Authorization" not in h for h in seen["headers"])
                    )

    def test_unknown_target_and_wrong_port_are_denied_before_upstream(self):
        with (
            origin(self.tls) as (target, seen),
            proxy(target, "http", self.tls, ("test", CANARY)) as (upstream, auth),
        ):
            config, ports = self.config(upstream, "http")
            with gateway(config, ports):
                for protocol, port in zip(("http", "socks5"), ports):
                    self.assertEqual(
                        self.attempt(protocol, port, "unknown.test.invalid").reason,
                        "proxy_rejected",
                    )
                    self.assertEqual(
                        self.attempt(protocol, port, target_port=8443).reason,
                        "proxy_rejected",
                    )
            self.assertEqual(auth["auth"], 0)
            self.assertEqual(seen["requests"], 0)

    def test_upstream_failure_auth_and_tls_never_reach_origin(self):
        with (
            origin(self.tls) as (target, seen),
            proxy(target, "http", self.tls, ("test", CANARY)) as (upstream, _),
        ):
            for change in ("password", "tls", "stopped"):
                with self.subTest(failure=change):
                    config, ports = self.config(
                        upstream
                        if change != "stopped"
                        else ("127.0.0.1", unused_port()),
                        "http",
                        password="wrong" if change == "password" else CANARY,
                        server_name="wrong.test.invalid"
                        if change == "tls"
                        else "upstream.test.invalid",
                    )
                    with gateway(config, ports):
                        for protocol, port in zip(("http", "socks5"), ports):
                            self.assertNotEqual(
                                self.attempt(protocol, port).reason, "ok"
                            )
            self.assertEqual(seen["requests"], 0)

    def test_socks_bind_and_udp_are_rejected(self):
        config, ports = self.config(("127.0.0.1", unused_port()), "http")
        with gateway(config, ports):
            for command in (2, 3):
                with socket.create_connection(
                    ("127.0.0.1", ports[1]), timeout=2
                ) as sock:
                    sock.sendall(b"\x05\x01\x00")
                    self.assertEqual(exact(sock, 2), b"\x05\x00")
                    name = b"origin.test.invalid"
                    sock.sendall(
                        bytes([5, command, 0, 3, len(name)]) + name + b"\x01\xbb"
                    )
                    self.assertNotEqual(exact(sock, 2)[1], 0)

    def test_known_gap_hostname_allowlist_does_not_validate_remote_resolution(self):
        # The fixture upstream resolves an allowed hostname to loopback. The
        # service-level special-address bypass sees the name, not that result.
        with (
            origin(self.tls) as (target, seen),
            proxy(target, "http", self.tls, ("test", CANARY)) as (upstream, _),
        ):
            config, ports = self.config(upstream, "http")
            with gateway(config, ports):
                self.assertEqual(self.attempt("http", ports[0]).reason, "ok")
            self.assertEqual(
                seen["requests"], 1, "remote_dns_guard_is_not_provided_by_local_bypass"
            )

    def test_known_gap_hop_bypass_is_direct_not_deny(self):
        with origin(self.tls) as (target, seen):
            config, ports = self.config(("127.0.0.1", unused_port()), "http")
            config["hosts"] = [
                {
                    "name": "lab",
                    "mappings": [
                        {"hostname": "origin.test.invalid", "ip": "127.0.0.1"}
                    ],
                }
            ]
            config["bypasses"][0]["matchers"] = [f"origin.test.invalid:{target[1]}"]
            # Hop routing receives the resolved address (chain/router.go:144).
            config["bypasses"].append({"name": "skip", "matchers": ["127.0.0.1"]})
            config["chains"][0]["hops"][0]["bypass"] = "skip"
            for service in config["services"]:
                service["hosts"] = "lab"
            with gateway(config, ports):
                self.assertEqual(
                    self.attempt("http", ports[0], target_port=target[1]).reason, "ok"
                )
            self.assertEqual(seen["requests"], 1, "hop_bypass_can_dial_direct")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    args = parser.parse_args()
    BINARY = str(Path(args.binary).resolve(strict=True))
    version = subprocess.run(
        [BINARY, "-V"], capture_output=True, text=True, timeout=5, check=True
    )
    if "gost 3.3.3 " not in version.stdout + version.stderr:
        raise SystemExit("candidate_version_mismatch")
    unittest.main(argv=[sys.argv[0]], verbosity=2)
