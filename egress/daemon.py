"""Bounded status server and in-container supervisor. Never logs raw errors."""

import concurrent.futures
import copy
from dataclasses import fields
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import ipaddress
import json
import os
from pathlib import Path
import random
import signal
import socket
import struct
import subprocess
import sys
import threading
import time

from .bundles import seal, validate_bundle
from .private_files import load_private_json, write_private_json
from .probes import Probe, probe
from .status import Status
from .telemetry import Telemetry, serve as serve_telemetry, fetch as fetch_telemetry

STOP = threading.Event()


def health_server(host, port, state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            now = time.monotonic()
            snapshot = state.snapshot(now)
            dns_ok = not hasattr(state, "dns_last_success") or (
                state.dns_last_success is not None
                and now - state.dns_last_success <= 60
            )
            snapshot["ready"] = snapshot["ready"] and dns_ok
            if self.path == "/metrics":
                code, body, kind = (
                    200,
                    (
                        state.metrics(now).replace(
                            "egress_ready 1", "egress_ready " + str(int(dns_ok))
                        )
                        + f"egress_dns_ready {int(dns_ok)}\n"
                        + getattr(state, "runtime_metrics", "")
                    ).encode(),
                    "text/plain; version=0.0.4",
                )
            elif self.path in ("/health/live", "/health/ready"):
                flag = "live" if self.path.endswith("live") else "ready"
                code = 200 if snapshot[flag] else 503
                body = json.dumps(snapshot, separators=(",", ":")).encode()
                kind = "application/json"
            else:
                code, body, kind = 404, b"not_found", "text/plain"
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        request_queue_size = 16

        def get_request(self):
            sock, address = super().get_request()
            sock.settimeout(1)
            return sock, address

        def handle_error(self, *args):
            pass

    return Server((host, port), Handler)


def _probe_config(p, ca_path=None):
    allowed = {f.name for f in fields(Probe)}
    value = {k: v for k, v in p.items() if k in allowed}
    if value.get("ca_file") and ca_path:
        value["ca_file"] = str(ca_path)
    return Probe(**value)


def dns_probe(ip, port, target, timeout=3):
    """One A query through the selected DNS; no fallback or system resolver."""
    try:
        ident = os.urandom(2)
        labels = target.encode("idna").split(b".")
        query = (
            ident
            + struct.pack("!HHHHH", 0x100, 1, 0, 0, 0)
            + b"".join(bytes([len(v)]) + v for v in labels)
            + b"\0\0\1\0\1"
        )
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.connect((str(ipaddress.ip_address(ip)), port))
            s.send(query)
            reply = s.recv(4096)
        return (
            len(reply) >= 12
            and reply[:2] == ident
            and reply[3] & 15 == 0
            and int.from_bytes(reply[6:8], "big") > 0
        )
    except Exception:
        return False


def status_main(config):
    local_stop = threading.Event()
    probes = config["probes"]
    keys = [(p["profile"], p["upstream"], p["protocol"]) for p in probes]
    required = [k for k, p in zip(keys, probes) if p["required"]]
    state = Status(config["version"], keys, required=required)
    state.dns_last_success = None
    server = health_server(config["listen"], config["port"], state)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    ca = Path("/tmp/egress/probe-ca.pem")
    if config["probe_ca"]:
        _write_text(ca, config["probe_ca"])

    # Each path has its own bounded worker. A slow upstream cannot delay the
    # scheduler heartbeat, cached endpoints, or checks of another upstream.
    def path_loop(p, key):
        while not STOP.is_set() and not local_stop.is_set():
            result = probe(_probe_config(p, ca), timeout=15)
            state.record(key, result, time.monotonic())
            local_stop.wait(15 + random.uniform(-1, 1))

    workers = [
        threading.Thread(target=path_loop, args=(p, k), daemon=True)
        for p, k in zip(probes, keys)
    ]

    def telemetry_loop():
        while not STOP.is_set() and not local_stop.is_set():
            state.runtime_metrics = fetch_telemetry(
                config["telemetry_ip"], config["telemetry_port"]
            )
            local_stop.wait(5)

    workers.append(threading.Thread(target=telemetry_loop, daemon=True))

    def dns_loop():
        while not STOP.is_set() and not local_stop.is_set():
            if dns_probe(config["dns_ip"], config["dns_port"], config["dns_target"]):
                state.dns_last_success = time.monotonic()
            local_stop.wait(15)

    workers.append(threading.Thread(target=dns_loop, daemon=True))
    for worker in workers:
        worker.start()
    try:
        while not STOP.wait(1):
            if all(w.is_alive() for w in workers):
                state.tick(time.monotonic())
                _write_text("/tmp/egress/live", str(time.monotonic()))
            try:
                if (
                    load_private_json(
                        "/run/private/runtime.json",
                        lambda v: validate_bundle(v, "status"),
                    )
                    != config
                ):
                    break
            except Exception:
                pass
    finally:
        local_stop.set()
        server.shutdown()
        server.server_close()
        for worker in workers:
            worker.join(timeout=0.1)


def _write_text(path, text):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())


def staged(value):
    result = copy.deepcopy(value)
    offset = 30000

    def addr(text):
        host, port = text.rsplit(":", 1)
        return f"{host}:{int(port) + offset}"

    for inbound in result["singbox"]["inbounds"]:
        inbound["listen_port"] += offset
        inbound["listen"] = "127.0.0.1"

    def local_source(rule):
        if "source_ip_cidr" in rule:
            rule["source_ip_cidr"] = ["127.0.0.1"]
        for nested in rule.get("rules", []):
            local_source(nested)

    for rule in result["singbox"].get("route", {}).get("rules", []):
        local_source(rule)
    for outbound in result["singbox"]["outbounds"]:
        if outbound.get("server") == "127.0.0.1":
            outbound["server_port"] += offset
    api = result["singbox"].get("experimental", {}).get("clash_api")
    if api:
        api["external_controller"] = addr(api["external_controller"])
    for adapter in result.get("adapters", []):
        for service in adapter["config"]["services"]:
            service["addr"] = "127.0.0.1:" + addr(service["addr"]).rsplit(":", 1)[1]
        for admission in adapter["config"].get("admissions", []):
            admission["matchers"] = ["127.0.0.1"]
        for chain in adapter["config"].get("chains", []):
            for hop in chain["hops"]:
                for node in hop["nodes"]:
                    if node["addr"].startswith("127.0.0.1:"):
                        node["addr"] = addr(node["addr"])
    for p in result.get("probes", []) + result.get("bootstrap_probes", []):
        p["proxy_port"] += offset
        p["proxy_ip"] = "127.0.0.1"
    return seal(result)


class Children:
    def __init__(
        self,
        config,
        directory,
        singbox="/usr/local/bin/sing-box",
        gost="/usr/local/bin/gost",
        telemetry=None,
    ):
        self.config = copy.deepcopy(config)
        self.directory = Path(directory)
        self.singbox = singbox
        self.gost = gost
        self.children = []
        self.telemetry = telemetry

    def prepare(self):
        validate_bundle(self.config, "gateway" if "groups" in self.config else "dns")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_private_json(self.directory / "singbox.json", self.config["singbox"])
        # Tool diagnostics may contain private paths or auth. Discard *before*
        # process creation, including parse/startup/panic output.
        subprocess.run(
            [self.singbox, "check", "-c", str(self.directory / "singbox.json")],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            env={"PATH": os.defpath},
        )
        for adapter in self.config.get("adapters", []):
            i = adapter["index"]
            for chain in adapter["config"].get("chains", []):
                for hop in chain["hops"]:
                    for node in hop["nodes"]:
                        tls = node.get("dialer", {}).get("tls", {})
                        if tls.get("caFile"):
                            path = self.directory / f"adapter-{i}.pem"
                            _write_text(path, adapter["ca_pem"])
                            tls["caFile"] = str(path)
            write_private_json(self.directory / f"adapter-{i}.json", adapter["config"])
        if self.config.get("probe_ca"):
            _write_text(self.directory / "probe-ca.pem", self.config["probe_ca"])
        if self.config.get("resolver_ca"):
            _write_text(self.directory / "resolver-ca.pem", self.config["resolver_ca"])

    def start(self):
        try:
            self.prepare()
            commands = [
                [self.gost, "-C", str(self.directory / f"adapter-{a['index']}.json")]
                for a in self.config.get("adapters", [])
            ]
            commands.append(
                [self.singbox, "run", "-c", str(self.directory / "singbox.json")]
            )

            def drain(stream):
                partial = False
                while True:
                    chunk = stream.readline(4096)
                    if not chunk:
                        break
                    if not partial and self.telemetry:
                        self.telemetry.increment("native_errors_total")
                    partial = not chunk.endswith(b"\n")
                stream.close()

            for command in commands:
                child = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env={"PATH": os.defpath},
                    cwd=self.directory,
                )
                self.children.append(child)
                threading.Thread(
                    target=drain, args=(child.stdout,), daemon=True
                ).start()
            time.sleep(0.2)
            if not self.alive():
                raise ValueError("runtime_start_failed")
        except Exception:
            self.close()
            raise

    def alive(self):
        return bool(self.children) and all(c.poll() is None for c in self.children)

    def close(self):
        for c in self.children:
            if c.poll() is None:
                c.terminate()
        deadline = time.monotonic() + 2
        for c in self.children:
            try:
                c.wait(timeout=max(0.01, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                c.kill()
                c.wait(timeout=1)
        self.children = []

    def accepted(self, upstreams=None):
        def check(p):
            return (
                probe(
                    _probe_config(p, self.directory / "probe-ca.pem"), timeout=5
                ).reason
                == "ok"
            )

        probes = [
            p
            for p in self.config.get("probes", [])
            if upstreams is None or p["upstream"] in upstreams
        ]
        if not probes:
            return True
        # Both frontends for every pinned upstream must work twice; bounded
        # parallelism and deadline keep reload handling below 30 seconds.
        for _ in range(2):
            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                if not all(pool.map(check, probes)):
                    return False
        return True


def _select(groups, availability, port=19090):
    for gid, members in groups.items():
        chosen = next((m for m in members if availability.get(m, False)), members[0])
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
        try:
            conn.request(
                "PUT",
                "/proxies/group-" + gid,
                json.dumps({"name": "node-" + chosen}),
                {"Content-Type": "application/json"},
            )
            response = conn.getresponse()
            response.read(1024)
            if response.status != 204:
                raise ValueError()
        finally:
            conn.close()


def supervise(role, path):
    config = load_private_json(path, lambda v: validate_bundle(v, role))

    def authorize(candidate):
        if "guard" in candidate:
            from .guard import allowed

            if not allowed(
                "/run/egress-policy/ready.json",
                candidate["guard"]["identity"],
                candidate["guard"]["token"],
            ):
                raise ValueError("identity_not_ready")

    authorize(config)
    telemetry = Telemetry()
    server = None
    if role == "gateway":
        t = config["telemetry"]
        server = serve_telemetry(t["listen"], t["port"], t["sources"], telemetry)
    active = Children(config, f"/tmp/egress/{role}/active", telemetry=telemetry)
    active.start()
    fingerprint = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).digest()
    availability = {}
    failures = {}
    successes = {}
    next_probe = 0
    last_rejected = None
    try:
        while not STOP.wait(1):
            if not active.alive():
                raise ValueError("runtime_child_failed")
            if role == "gateway":
                telemetry.refresh()
            _write_text("/tmp/egress/live", str(time.monotonic()))
            try:
                candidate = load_private_json(path, lambda v: validate_bundle(v, role))
                digest = hashlib.sha256(
                    json.dumps(candidate, sort_keys=True).encode()
                ).digest()
                if digest != fingerprint:
                    authorize(candidate)
                    stage = Children(staged(candidate), f"/tmp/egress/{role}/stage")
                    try:
                        stage.start()

                        def auth_nodes(c):
                            remote = {
                                o["tag"][5:]: o
                                for o in c["singbox"]["outbounds"]
                                if o["tag"].startswith("node-")
                            }
                            remote["_adapters"] = [
                                a for a in c.get("adapters", []) if a["index"] < 100
                            ]
                            return remote

                        old_auth, new_auth = auth_nodes(config), auth_nodes(candidate)
                        changed = {
                            n
                            for n in new_auth
                            if n != "_adapters" and new_auth.get(n) != old_auth.get(n)
                        }
                        if old_auth.get("_adapters") != new_auth.get("_adapters"):
                            changed.update(
                                candidate.get("groups", {}).get(
                                    next(iter(candidate.get("groups", {})), ""), []
                                )
                            )
                        if changed and not stage.accepted(changed):
                            raise ValueError("rotation_probe_failed")
                    finally:
                        stage.close()
                    previous = active.config
                    active.close()
                    try:
                        active = Children(
                            candidate, f"/tmp/egress/{role}/active", telemetry=telemetry
                        )
                        active.start()
                    except Exception:
                        active = Children(
                            previous, f"/tmp/egress/{role}/active", telemetry=telemetry
                        )
                        active.start()
                        raise
                    config = candidate
                    fingerprint = digest
                    availability = {}
                    failures = {}
                    successes = {}
                    telemetry.applied()
                    print("config_applied", flush=True)
            except Exception:
                try:
                    info = os.stat(path)
                    attempt = (info.st_ino, info.st_mtime_ns, info.st_size)
                except OSError:
                    attempt = "unreadable"
                if attempt != last_rejected:
                    telemetry.increment("config_rejections_total")
                    print("config_rejected", flush=True)
                    last_rejected = attempt
            if role == "gateway" and time.monotonic() >= next_probe:
                probes = config.get("bootstrap_probes", [])

                def check(p):
                    return p["upstream"], probe(
                        _probe_config(p, active.directory / "resolver-ca.pem"),
                        timeout=5,
                    ).reason == "ok"

                with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                    results = list(pool.map(check, probes))
                for nid in config.get("groups", {}).values():
                    for node in nid:
                        good = all(ok for name, ok in results if name == node)
                        failures[node] = 0 if good else failures.get(node, 0) + 1
                        successes[node] = successes.get(node, 0) + 1 if good else 0
                        if successes[node] >= 2:
                            availability[node] = True
                        elif failures[node] >= 3:
                            availability[node] = False
                _select(config.get("groups", {}), availability)
                next_probe = time.monotonic() + 5
    finally:
        active.close()
        if server:
            server.shutdown()
            server.server_close()


def main():
    role = sys.argv[1] if len(sys.argv) == 2 else ""
    if role == "health":
        try:
            age = time.monotonic() - float(Path("/tmp/egress/live").read_text())
            return 0 if 0 <= age < 30 else 1
        except Exception:
            return 1
    if role not in ("gateway", "dns", "status"):
        return 2
    os.umask(0o077)
    Path("/tmp/egress").mkdir(mode=0o700, parents=True, exist_ok=True)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: STOP.set())
    try:
        path = "/run/private/runtime.json"
        if role == "status":
            while not STOP.is_set():
                config = load_private_json(path, lambda v: validate_bundle(v, "status"))
                if "guard" in config:
                    from .guard import allowed

                    if not allowed(
                        "/run/egress-policy/ready.json",
                        config["guard"]["identity"],
                        config["guard"]["token"],
                    ):
                        raise ValueError("identity_not_ready")
                status_main(config)
        else:
            supervise(role, path)
        return 0
    except Exception:
        print("runtime_failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
