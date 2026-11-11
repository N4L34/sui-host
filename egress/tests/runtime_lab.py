"""Real controlled DoH + TLS upstream + gateway + DNS + status integration.

Only numeric synthetic destinations are routed by a fixture map to fixed
loopback servers. No external request, host route, or production file access.
Run in the test image with --network none. No skipped required tests.
"""

import contextlib
import copy
import json
from pathlib import Path
import signal
import socket
import socketserver
import ssl
import struct
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from egress.compiler import compile_installation
from egress.bundles import seal
from egress.daemon import Children, STOP, dns_probe, status_main, supervise
from egress.private_files import write_private_json
from egress.probes import Probe, probe
from egress.tests.test_runtime import installation
from lab import Server, certificate, exact, headers, origin, proxy


@contextlib.contextmanager
def doh(tls):
    state = {"queries": 0, "addresses": ["8.8.4.4"], "rcode": 0}

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(3)
            with tls.wrap_socket(self.request, server_side=True) as conn:
                request = headers(conn)
                length = next(
                    int(line.split(b":", 1)[1])
                    for line in request.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
                query = exact(conn, length)
                state["queries"] += 1
                # Copy only this bounded fixture question. No DNS parser or
                # arbitrary relay in production code.
                qtype = int.from_bytes(query[-4:-2], "big")
                answers = []
                for ip in state["addresses"]:
                    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
                    if qtype != (28 if family == socket.AF_INET6 else 1):
                        continue
                    addr = socket.inet_pton(family, ip)
                    answers.append(
                        b"\xc0\x0c"
                        + struct.pack("!HHIH", qtype, 1, 1, len(addr))
                        + addr
                    )
                body = (
                    query[:2]
                    + struct.pack(
                        "!HHHHH",
                        0x8180 | state["rcode"],
                        1,
                        len(answers) if not state["rcode"] else 0,
                        0,
                        0,
                    )
                    + query[12:]
                    + (b"".join(answers) if not state["rcode"] else b"")
                )
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/dns-message\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\nConnection: close\r\n\r\n"
                    + body
                )

    with Server(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address, state
        finally:
            server.shutdown()
            thread.join(2)


class RuntimeConformance(unittest.TestCase):
    def test_controlled_dns_status_failover_and_lkg_revocation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ca, tls = certificate(directory)
            with (
                origin(tls, stream=True) as (target, seen),
                doh(tls) as (resolver, dns_seen),
            ):
                mapping = {b"8.8.4.4:443": target, b"1.1.1.1:443": resolver}
                with (
                    proxy(
                        mapping,
                        "http",
                        tls,
                        ("test", "fixture-password"),
                        bind="127.0.0.10",
                    ) as (primary, pseen),
                    proxy(
                        mapping,
                        "http",
                        tls,
                        ("test", "fixture-password"),
                        bind="127.0.0.11",
                    ) as (backup, bseen),
                ):
                    c = installation()
                    c["registry"]["profiles"][0]["routes"][0]["matcher"]["value"] = (
                        "origin.test.invalid"
                    )
                    c["probe"].update(
                        target="origin.test.invalid", ca_pem=Path(ca).read_text()
                    )
                    c["resolver"].update(
                        server_name="resolver.test.invalid", ca_pem=Path(ca).read_text()
                    )
                    c["upstreams"][0].update(
                        server_name="upstream.test.invalid",
                        password="fixture-password",
                        ca_pem=Path(ca).read_text(),
                    )
                    c["upstreams"].append(dict(c["upstreams"][0], id="node-b"))
                    c["groups"][0]["members"].append("node-b")
                    b = compile_installation(c)
                    for role in ("gateway", "dns"):
                        for inbound in b[role]["singbox"]["inbounds"]:
                            if inbound["listen"] != "127.0.0.1":
                                inbound["listen"] = (
                                    "127.0.0.2" if role == "gateway" else "127.0.0.3"
                                )

                        def sources(r):
                            if "source_ip_cidr" in r:
                                r["source_ip_cidr"] = ["127.0.0.1"]
                            for nested in r.get("rules", []):
                                sources(nested)

                        for r in b[role]["singbox"]["route"]["rules"]:
                            sources(r)
                    gateway = b["gateway"]
                    dns = b["dns"]
                    status = b["status"]
                    gateway["singbox"]["dns"]["servers"][0]["server"] = "127.0.0.3"
                    gateway["telemetry"].update(
                        listen="127.0.0.2", sources=["127.0.0.1"]
                    )
                    status.update(telemetry_ip="127.0.0.2")
                    for outbound, up in zip(
                        gateway["singbox"]["outbounds"][1:3], (primary, backup)
                    ):
                        outbound.update(server=up[0], server_port=up[1])
                    dns["singbox"]["outbounds"][0]["server"] = "127.0.0.2"
                    for adapter in gateway["adapters"]:
                        conf = adapter["config"]
                        conf["services"][0]["addr"] = (
                            "127.0.0.2:" + conf["services"][0]["addr"].split(":")[-1]
                        )
                        conf["admissions"][0]["matchers"] = ["127.0.0.1"]
                    for p in gateway["probes"] + gateway["bootstrap_probes"]:
                        p["proxy_ip"] = "127.0.0.2"
                    status.update(listen="127.0.0.4", dns_ip="127.0.0.3")
                    for p in status["probes"]:
                        p["proxy_ip"] = "127.0.0.2"
                    paths = {
                        role: root / role / "runtime.json"
                        for role in ("gateway", "dns")
                    }
                    for role, path in paths.items():
                        write_private_json(path, seal(b[role]))
                    STOP.clear()
                    errors = []

                    def run(role):
                        try:
                            supervise(role, paths[role])
                        except Exception:
                            errors.append(role)

                    workers = [
                        threading.Thread(target=run, args=(r,), daemon=True)
                        for r in paths
                    ]
                    workers.append(
                        threading.Thread(
                            target=status_main, args=(status,), daemon=True
                        )
                    )
                    for worker in workers:
                        worker.start()
                    attempt = lambda: probe(
                        Probe(
                            "demo",
                            "effective",
                            "http",
                            "127.0.0.2",
                            3128,
                            "origin.test.invalid",
                            443,
                            ca,
                        ),
                        timeout=3,
                    )

                    def until(check, timeout=25):
                        deadline = time.monotonic() + timeout
                        while time.monotonic() < deadline:
                            if check():
                                return
                            self.assertFalse(errors, "supervisor_failed")
                            time.sleep(0.2)
                        self.fail("runtime_condition_deadline")

                    try:
                        until(lambda: attempt().reason == "ok")
                        self.assertTrue(
                            dns_probe("127.0.0.3", 5353, "origin.test.invalid")
                        )
                        self.assertGreater(dns_seen["queries"], 0)
                        # Real mixed DoH answers deny before target dial.
                        for forbidden in ("127.0.0.1", "100.64.1.2", "100.100.100.100"):
                            before = seen["requests"]
                            dns_seen["addresses"] = ["8.8.4.4", forbidden]
                            self.assertNotEqual(attempt().reason, "ok")
                            self.assertEqual(seen["requests"], before)
                        dns_seen["addresses"] = ["8.8.4.4"]
                        dns_seen["rcode"] = 3
                        self.assertNotEqual(attempt().reason, "ok")
                        dns_seen["rcode"] = 2
                        self.assertNotEqual(attempt().reason, "ok")
                        dns_seen["rcode"] = 0
                        # Invalid semantic auth rotation preserves active LKG.
                        candidate = copy.deepcopy(gateway)
                        candidate["version"] = "bad-rotation"
                        candidate["singbox"]["outbounds"][1]["password"] = "wrong"
                        write_private_json(paths["gateway"], seal(candidate))
                        time.sleep(3)
                        self.assertEqual(attempt().reason, "ok")
                        write_private_json(paths["gateway"], seal(gateway))
                        # Primary down, backup selected from pinned health.
                        pseen["reject"] = True
                        until(lambda: attempt().reason == "ok", timeout=30)
                        self.assertGreater(bseen["auth"], 0)
                        pseen["reject"] = False
                        # Valid policy revoke removes application route, while
                        # pinned rotation probes continue to validate auth.
                        held = socket.create_connection(("127.0.0.2", 3128), timeout=2)
                        held.sendall(
                            b"CONNECT origin.test.invalid:443 HTTP/1.1\r\nHost: origin.test.invalid:443\r\n\r\n"
                        )
                        self.assertIn(b"200", headers(held))
                        held = ssl.create_default_context(cafile=ca).wrap_socket(
                            held, server_hostname="origin.test.invalid"
                        )
                        held.sendall(
                            b"GET /events HTTP/1.1\r\nHost: origin.test.invalid\r\n\r\n"
                        )
                        self.assertIn(b"200", headers(held))
                        self.assertTrue(held.recv(64))
                        # Successful dial contexts must not own the stream.
                        # Keep reading actual SSE data beyond the 15s budget.
                        stream_until = time.monotonic() + 18.2
                        while time.monotonic() < stream_until:
                            held.settimeout(2)
                            self.assertTrue(held.recv(4096))
                        revoked_at = time.monotonic()
                        candidate = copy.deepcopy(gateway)
                        candidate["version"] = "revoked-1"
                        candidate["singbox"]["route"]["rules"] = [
                            r
                            for r in candidate["singbox"]["route"]["rules"]
                            if "demo-pilot-http" not in r.get("inbound", [])
                        ]
                        write_private_json(paths["gateway"], seal(candidate))
                        until(lambda: attempt().reason != "ok", timeout=29)
                        held.settimeout(max(0.1, 29 - (time.monotonic() - revoked_at)))
                        try:
                            while held.recv(4096):
                                pass
                        except (ConnectionResetError, ssl.SSLError):
                            pass
                        finally:
                            held.close()
                        self.assertLess(time.monotonic() - revoked_at, 30)
                    finally:
                        STOP.set()
                        for worker in workers:
                            worker.join(timeout=20)
                    self.assertFalse(errors, "supervisor_failed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
