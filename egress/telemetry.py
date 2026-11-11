"""Allowlisted aggregate view of the loopback native API; no raw export."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import math
import threading
import time

FIELDS = frozenset(
    {
        "active_connections",
        "active_proxy_connections",
        "active_direct_connections",
        "upload_bytes_total",
        "download_bytes_total",
        "native_errors_total",
        "config_rejections_total",
        "config_applied_timestamp_seconds",
        "native_telemetry_success",
    }
)


class Telemetry:
    def __init__(self):
        self.lock = threading.Lock()
        self.values = {k: 0 for k in FIELDS}
        self.values["config_applied_timestamp_seconds"] = time.time()

    def increment(self, key):
        with self.lock:
            self.values[key] += 1

    def applied(self):
        with self.lock:
            self.values["config_applied_timestamp_seconds"] = time.time()

    def refresh(self):
        connection = http.client.HTTPConnection("127.0.0.1", 19090, timeout=0.3)
        try:
            connection.request("GET", "/connections")
            response = connection.getresponse()
            raw = response.read(1024 * 1024 + 1)
            if response.status != 200 or len(raw) > 1024 * 1024:
                raise ValueError()
            data = json.loads(raw)
            connections = data["connections"] or []
            if type(connections) is not list or len(connections) > 4096:
                raise ValueError()
            numbers = {
                "upload_bytes_total": data["uploadTotal"],
                "download_bytes_total": data["downloadTotal"],
            }
            if any(type(v) is not int or v < 0 for v in numbers.values()):
                raise ValueError()
            numbers.update(
                active_connections=len(connections),
                active_direct_connections=sum(
                    "direct" in c.get("chains", []) for c in connections
                ),
                active_proxy_connections=sum(
                    any(str(tag).startswith("node-") for tag in c.get("chains", []))
                    for c in connections
                ),
                native_telemetry_success=1,
            )
            with self.lock:
                self.values.update(numbers)
        except Exception:
            with self.lock:
                self.values["native_telemetry_success"] = 0
        finally:
            connection.close()

    def snapshot(self):
        with self.lock:
            return dict(self.values)


def serve(host, port, sources, telemetry):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.client_address[0] not in sources or self.path != "/aggregates":
                self.send_error(403)
                return
            body = json.dumps(telemetry.snapshot()).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, *args):
            pass

        def get_request(self):
            sock, address = super().get_request()
            sock.settimeout(1)
            return sock, address

    server = Server((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def fetch(host, port):
    connection = http.client.HTTPConnection(host, port, timeout=0.5)
    try:
        connection.request("GET", "/aggregates")
        response = connection.getresponse()
        raw = response.read(8193)
        if response.status != 200 or len(raw) > 8192:
            raise ValueError()
        values = json.loads(raw)
        if (
            type(values) is not dict
            or values.keys() != FIELDS
            or any(
                type(v) not in (int, float) or not math.isfinite(v) or v < 0
                for v in values.values()
            )
        ):
            raise ValueError()
        return "\n".join(f"egress_{key} {values[key]}" for key in sorted(FIELDS)) + "\n"
    except Exception:
        return "egress_native_telemetry_success 0\n"
    finally:
        connection.close()
