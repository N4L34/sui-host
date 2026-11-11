"""Generated routing conformance with pinned native binaries; loopback only.

Fixture DNS returns artificial public addresses; fixture upstream NEVER dials
those addresses, and forwards exclusively to its fixed loopback origin.
"""

import argparse
import contextlib
import copy
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
from egress.compiler import compile_installation, _policy_rules
from egress.probes import Probe, probe
from egress.tests.test_runtime import installation
from lab import certificate, origin, proxy, headers, exact

BINARY = None
GOST = None
CANARY = 'CANARY_雪\n:@/"'


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def running(config, binary, argument="-c"):
    with tempfile.TemporaryDirectory() as directory:
        p = Path(directory) / "config.json"
        p.write_text(json.dumps(config))
        p.chmod(0o600)
        with tempfile.TemporaryFile() as logs:
            args = (
                [binary, "run", "-c", str(p)]
                if argument == "-c"
                else [binary, "-C", str(p)]
            )
            child = subprocess.Popen(
                args, stdout=logs, stderr=logs, env={"PATH": os.defpath}, cwd=directory
            )
            try:
                time.sleep(0.15)
                if child.poll() is not None:
                    raise AssertionError("product_start_failed")
                yield child
            finally:
                child.terminate()
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=2)
                logs.seek(0)
                raw = logs.read()
                if any(
                    c in raw
                    for c in [CANARY.encode(), b"CANARY_", b"Proxy-Authorization"]
                ):
                    raise AssertionError("secret_log_leak")


class Conformance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.ca, cls.tls = certificate(cls.directory.name)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def config(self, upstream, transport="https", addresses=None):
        c = installation()
        c["registry"]["profiles"][0]["routes"][0]["matcher"]["value"] = (
            "origin.test.invalid"
        )
        c["probe"]["target"] = "origin.test.invalid"
        c["upstreams"][0].update(
            ip="8.8.8.8",
            transport=transport,
            server_name="upstream.test.invalid",
            ca_pem=Path(self.ca).read_text(),
        )
        b = compile_installation(c)
        g = b["gateway"]["singbox"]
        g["experimental"]["clash_api"]["external_controller"] = f"127.0.0.1:{port()}"
        g["dns"] = {
            "servers": [
                {
                    "type": "hosts",
                    "tag": "controlled",
                    "predefined": addresses or {"origin.test.invalid": ["8.8.4.4"]},
                }
            ],
            "final": "controlled",
            "disable_cache": True,
        }
        ports = {}
        for inbound in g["inbounds"]:
            inbound["_oldport"] = inbound["listen_port"]
            inbound["listen"] = "127.0.0.1"
            inbound["listen_port"] = port()
            ports[inbound["tag"]] = inbound["listen_port"]

        def source(r):
            if "source_ip_cidr" in r:
                r["source_ip_cidr"] = ["127.0.0.1"]
            for nested in r.get("rules", []):
                source(nested)

        for rule in g["route"]["rules"]:
            source(rule)
        outbound = g["outbounds"][1]
        outbound.update(
            server=upstream[0],
            server_port=upstream[1],
            username="test",
            password=CANARY,
        )
        for adapter in b["gateway"]["adapters"]:
            if adapter["index"] < 100:
                continue
            a = adapter["config"]
            old = a["chains"][0]["hops"][0]["nodes"][0]["addr"]
            original_port = int(old.split(":")[-1])
            inbound = next(
                i
                for i in g["inbounds"]
                if i.get("_oldport", i["listen_port"]) == original_port
            )
            a["chains"][0]["hops"][0]["nodes"][0]["addr"] = (
                f"127.0.0.1:{inbound['listen_port']}"
            )
            external_port = port()
            a["services"][0]["addr"] = f"127.0.0.1:{external_port}"
            a["admissions"][0]["matchers"] = ["127.0.0.1"]
            ports[inbound["tag"]] = external_port
        for i in g["inbounds"]:
            i.pop("_oldport", None)
        return b, ports

    @contextlib.contextmanager
    def gateway(self, b):
        with contextlib.ExitStack() as stack:
            for a in b["gateway"]["adapters"]:
                stack.enter_context(running(a["config"], GOST, "-C"))
            stack.enter_context(running(b["gateway"]["singbox"], BINARY))
            yield

    def attempt(
        self, ports, protocol="http", target="origin.test.invalid", target_port=443
    ):
        return probe(
            Probe(
                "demo",
                "primary",
                protocol,
                "127.0.0.1",
                ports["demo-pilot-" + ("http" if protocol == "http" else "socks")],
                target,
                target_port,
                self.ca,
            ),
            timeout=2,
        )

    def test_both_frontends_both_tls_upstreams_numeric_target_and_no_auth_leak(self):
        for transport in ("https", "socks5-tls"):
            with (
                self.subTest(transport=transport),
                origin(self.tls) as (target, seen),
                proxy(
                    target,
                    "http" if transport == "https" else "socks5",
                    self.tls,
                    ("test", CANARY),
                ) as (up, auth),
            ):
                b, ports = self.config(up, transport)
                with contextlib.ExitStack() as stack:
                    for a in b["gateway"]["adapters"]:
                        stack.enter_context(running(a["config"], GOST, "-C"))
                    stack.enter_context(running(b["gateway"]["singbox"], BINARY))
                    for protocol in ("http", "socks5"):
                        self.assertEqual(self.attempt(ports, protocol).reason, "ok")
                self.assertEqual(seen["requests"], 2)
                self.assertEqual(auth["auth"], 2)
                if transport == "https":
                    self.assertTrue(
                        all(d == b"8.8.4.4:443" for d in auth["destinations"])
                    )
                else:
                    self.assertTrue(
                        all(
                            d[0] == 1 and d[1] == socket.inet_aton("8.8.4.4")
                            for d in auth["destinations"]
                        )
                    )
                self.assertTrue(
                    all(b"Proxy-Authorization" not in h for h in seen["headers"])
                )

    def test_mixed_private_and_denied_candidates_unknown_port_and_source(self):
        for answers in (
            ["8.8.4.4", "127.0.0.1"],
            ["8.8.4.4", "::1"],
            ["8.8.4.4", "192.0.2.3"],
        ):
            with (
                self.subTest(answers=answers),
                origin(self.tls) as (target, seen),
                proxy(target, "http", self.tls, ("test", CANARY)) as (up, auth),
            ):
                b, ports = self.config(up, addresses={"origin.test.invalid": answers})
                with self.gateway(b):
                    for protocol in ("http", "socks5"):
                        self.assertNotEqual(self.attempt(ports, protocol).reason, "ok")
                self.assertEqual(auth["auth"], 0)
                self.assertEqual(seen["requests"], 0)
        with (
            origin(self.tls) as (target, seen),
            proxy(target, "http", self.tls, ("test", CANARY)) as (up, auth),
        ):
            b, ports = self.config(up)
            with self.gateway(b):
                self.assertNotEqual(self.attempt(ports, target_port=8443).reason, "ok")
                self.assertNotEqual(
                    self.attempt(ports, target="other.example.invalid").reason, "ok"
                )
            for rule in b["gateway"]["singbox"]["route"]["rules"]:
                if "source_ip_cidr" in rule:
                    rule["source_ip_cidr"] = ["127.0.0.2"]
            with self.gateway(b):
                self.assertNotEqual(self.attempt(ports).reason, "ok")
            self.assertEqual(auth["auth"], 0)

    def test_http_absolute_form_strips_proxy_auth(self):
        from lab import Server
        import socketserver, threading

        seen = []

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(3)
                seen.append(headers(self.request))
                self.request.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
                )

        with Server(("127.0.0.1", 0), Handler) as target:
            thread = threading.Thread(target=target.serve_forever, daemon=True)
            thread.start()
            try:
                with proxy(
                    target.server_address, "http", self.tls, ("test", CANARY)
                ) as (up, auth):
                    b, ports = self.config(up)
                    g = b["gateway"]["singbox"]
                    for rule in g["route"]["rules"]:
                        if (
                            "demo-pilot-http" in rule.get("inbound", [])
                            and rule.get("action") == "route"
                        ):
                            rule["port"].append(80)
                    with self.gateway(b):
                        with socket.create_connection(
                            ("127.0.0.1", ports["demo-pilot-http"]), timeout=2
                        ) as conn:
                            conn.sendall(
                                b"GET http://origin.test.invalid/path?fixture=1 HTTP/1.1\r\nHost: origin.test.invalid\r\nProxy-Authorization: Basic Zml4dHVyZQ==\r\nConnection: close\r\n\r\n"
                            )
                            self.assertIn(b"200", headers(conn))
                    self.assertEqual(len(seen), 1)
                    self.assertNotIn(b"Proxy-Authorization", seen[0])
                    self.assertTrue(seen[0].startswith(b"GET /path?fixture=1 "))
            finally:
                target.shutdown()
                thread.join(2)

    def test_ipv4_ipv6_literals_and_mixed_cidr_candidates(self):
        for address in ("8.8.4.4", "2606:4700:4700::1111"):
            with (
                origin(self.tls) as (target, seen),
                proxy(target, "http", self.tls, ("test", CANARY)) as (up, auth),
            ):
                b, ports = self.config(up)
                g = b["gateway"]["singbox"]
                route = {
                    "id": "literal",
                    "action": "PROXY",
                    "matcher": {"kind": "exact", "value": address},
                    "ports": [443],
                    "upstream_group": "primary",
                }
                extra = _policy_rules(
                    {"routes": [route]},
                    ["demo-pilot-http", "demo-pilot-socks"],
                    ["127.0.0.1"],
                )
                g["route"]["rules"][-1:-1] = extra
                with self.gateway(b):
                    for proto in ("http", "socks5"):
                        result = probe(
                            Probe(
                                "demo",
                                "primary",
                                proto,
                                "127.0.0.1",
                                ports[
                                    "demo-pilot-"
                                    + ("http" if proto == "http" else "socks")
                                ],
                                "origin.test.invalid",
                                443,
                                self.ca,
                                target_ip=address,
                            ),
                            timeout=2,
                        )
                        self.assertEqual(result.reason, "ok")
                wanted = (("[" + address + "]") if ":" in address else address) + ":443"
                self.assertTrue(all(d.decode() == wanted for d in auth["destinations"]))
        for candidates, allowed in [
            (["8.8.4.4", "8.8.4.5"], True),
            (["8.8.4.4", "9.9.9.9"], False),
        ]:
            with (
                origin(self.tls) as (target, seen),
                proxy(target, "http", self.tls, ("test", CANARY)) as (up, auth),
            ):
                b, ports = self.config(up, addresses={"cidr.test.invalid": candidates})
                g = b["gateway"]["singbox"]
                route = {
                    "id": "cidr",
                    "action": "PROXY",
                    "matcher": {"kind": "cidr", "value": "8.8.4.0/24"},
                    "ports": [443],
                    "upstream_group": "primary",
                }
                g["route"]["rules"][-1:-1] = _policy_rules(
                    {"routes": [route]}, ["demo-pilot-http"], ["127.0.0.1"]
                )
                with self.gateway(b):
                    # TLS verifies origin hostname while CONNECT carries a
                    # domain requiring the CIDR-only candidate guard.
                    with socket.create_connection(
                        ("127.0.0.1", ports["demo-pilot-http"]), timeout=2
                    ) as conn:
                        conn.sendall(
                            b"CONNECT cidr.test.invalid:443 HTTP/1.1\r\nHost: cidr.test.invalid:443\r\n\r\n"
                        )
                        try:
                            response = headers(conn)
                            context = __import__("ssl").create_default_context(
                                cafile=self.ca
                            )
                            secure = context.wrap_socket(
                                conn, server_hostname="origin.test.invalid"
                            )
                            secure.sendall(
                                b"GET / HTTP/1.1\r\nHost: origin.test.invalid\r\n\r\n"
                            )
                            self.assertIn(b"200", headers(secure))
                            secure.close()
                            self.assertTrue(allowed)
                        except (EOFError, OSError):
                            self.assertFalse(allowed)
                self.assertEqual(seen["requests"], int(allowed))

    def test_bad_auth_tls_closed_upstream_no_direct_and_socks_commands(self):
        with (
            origin(self.tls) as (target, seen),
            proxy(target, "http", self.tls, ("test", CANARY)) as (up, auth),
        ):
            for fault in ("auth", "tls", "closed"):
                b, ports = self.config(up)
                node = b["gateway"]["singbox"]["outbounds"][1]
                if fault == "auth":
                    node["password"] = "wrong"
                elif fault == "tls":
                    node["tls"]["server_name"] = "wrong.example.invalid"
                else:
                    node["server_port"] = port()
                with self.gateway(b):
                    self.assertNotEqual(self.attempt(ports).reason, "ok")
            b, ports = self.config(up)
            with self.gateway(b):
                for command in (2, 3):
                    with socket.create_connection(
                        ("127.0.0.1", ports["demo-pilot-socks"]), timeout=2
                    ) as s:
                        s.sendall(b"\x05\x01\x00")
                        self.assertEqual(exact(s, 2), b"\x05\x00")
                        s.sendall(bytes([5, command, 0, 1, 8, 8, 4, 4, 1, 187]))
                        try:
                            self.assertNotEqual(exact(s, 2), b"\x05\x00")
                        except EOFError:
                            pass
            self.assertEqual(seen["requests"], 0)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--binary", required=True)
    p.add_argument("--gost", required=True)
    a = p.parse_args()
    BINARY = a.binary
    GOST = a.gost
    unittest.main(argv=[sys.argv[0]], verbosity=2)
