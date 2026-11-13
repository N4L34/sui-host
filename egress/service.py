"""One Xray process, private config and cached end-to-end health."""

import base64
import copy
import http.client
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONFIG = Path("/run/private/config.json")
BINARY = "/usr/local/bin/xray"


def subscription_config(raw):
    text = raw.decode("utf-8-sig").strip()
    if not text.startswith("vless://"):
        text = (
            base64.b64decode(text + "=" * (-len(text) % 4), validate=True)
            .decode()
            .strip()
        )
    lines = [s.strip() for s in text.splitlines() if s.strip()]
    if not lines or any(not s.startswith("vless://") for s in lines):
        raise ValueError("subscription_invalid")
    u = urllib.parse.urlsplit(lines[0])
    q = urllib.parse.parse_qs(u.query)
    get = lambda k: q.get(k, [""])[0]
    uid = str(uuid.UUID(u.username))
    if (
        not u.hostname
        or not u.port
        or get("security") != "reality"
        or get("type") not in ("tcp", "raw")
    ):
        raise ValueError("unsupported_subscription")
    if (
        not get("sni")
        or not re.fullmatch("[A-Za-z0-9_-]{43}", get("pbk"))
        or not re.fullmatch("(?:[a-fA-F0-9]{2}){0,8}", get("sid"))
    ):
        raise ValueError("subscription_invalid")
    if get("flow") not in ("", "xtls-rprx-vision"):
        raise ValueError("subscription_invalid")
    return {
        "log": {"loglevel": "none", "access": "none", "error": "none"},
        "inbounds": [
            {
                "listen": "0.0.0.0",
                "port": 1080,
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": False},
            },
            {"listen": "0.0.0.0", "port": 3128, "protocol": "http", "settings": {}},
        ],
        "outbounds": [
            {
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": u.hostname,
                            "port": u.port,
                            "users": [
                                {"id": uid, "encryption": "none", "flow": get("flow")}
                            ],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "serverName": get("sni"),
                        "publicKey": get("pbk"),
                        "shortId": get("sid"),
                        "fingerprint": get("fp") or "chrome",
                    },
                },
            }
        ],
    }


def private_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("private_path_invalid")
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(value, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def probe(http_port=3128, socks_port=1080):
    try:
        conn = http.client.HTTPSConnection("127.0.0.1", http_port, timeout=6)
        try:
            conn.set_tunnel("api.telegram.org", 443)
            conn.request("GET", "/")
            r = conn.getresponse()
            if r.status not in (200, 302):
                return False
        finally:
            conn.close()
        with socket.create_connection(("127.0.0.1", socks_port), timeout=6) as s:
            s.settimeout(6)

            def exact(n):
                b = b""
                while len(b) < n:
                    part = s.recv(n - len(b))
                    if not part:
                        raise OSError()
                    b += part
                return b

            s.sendall(b"\x05\x01\x00")
            if exact(2) != b"\x05\x00":
                return False
            h = b"api.telegram.org"
            s.sendall(
                b"\x05\x01\x00\x03" + bytes([len(h)]) + h + struct.pack("!H", 443)
            )
            head = exact(4)
            if head[:3] != b"\x05\x00\x00":
                return False
            n = exact(1)[0] if head[3] == 3 else {1: 4, 4: 16}[head[3]]
            exact(n + 2)
            with ssl.create_default_context().wrap_socket(
                s, server_hostname="api.telegram.org"
            ) as tls:
                tls.sendall(
                    b"GET / HTTP/1.1\r\nHost: api.telegram.org\r\nConnection: close\r\n\r\n"
                )
                response = http.client.HTTPResponse(tls)
                response.begin()
                return response.status in (200, 302)
    except Exception:
        return False


def start(path):
    subprocess.run(
        [BINARY, "run", "-test", "-c", str(path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=8,
    )
    return subprocess.Popen(
        [BINARY, "run", "-c", str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop(child):
    child.terminate()
    try:
        child.wait(timeout=3)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def check(value):
    candidate = copy.deepcopy(value)
    for i in candidate["inbounds"]:
        i.update(listen="127.0.0.1", port=i["port"] + 20000)
    with tempfile.TemporaryDirectory() as directory:
        p = Path(directory) / "check.json"
        private_write(p, candidate)
        child = start(p)
        try:
            time.sleep(0.3)
            return child.poll() is None and probe(23128, 21080)
        finally:
            stop(child)


def serve():
    child = start(CONFIG)
    current = CONFIG.read_bytes()
    private_write(CONFIG.with_name("applied.json"), hashlib.sha256(current).hexdigest())
    state = {"ready": False, "checked": 0.0}
    done = threading.Event()

    def worker():
        while not done.is_set():
            observed = child
            ready = observed.poll() is None and probe()
            if observed is child:
                state.update(ready=ready, checked=time.monotonic())
            done.wait(15)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            live = child.poll() is None
            ready = live and state["ready"] and time.monotonic() - state["checked"] < 45
            code = (
                (200 if live else 503)
                if self.path == "/health/live"
                else (200 if ready else 503)
            )
            if self.path not in ("/health", "/health/live"):
                code = 404
            body = json.dumps({"ready": ready}).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=worker, daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: done.set())
    signal.signal(signal.SIGINT, lambda *_: done.set())
    try:
        while not done.wait(1):
            if child.poll() is not None:
                raise RuntimeError("proxy_stopped")
            candidate = CONFIG.read_bytes()
            if candidate != current:
                value = json.loads(candidate)
                if check(value):
                    old = current
                    stop(child)
                    try:
                        child = start(CONFIG)
                        current = candidate
                        private_write(
                            CONFIG.with_name("applied.json"),
                            hashlib.sha256(current).hexdigest(),
                        )
                        state["ready"] = False
                        print("config_applied", flush=True)
                    except Exception:
                        private_write(CONFIG, json.loads(old))
                        child = start(CONFIG)
                        raise
                else:
                    private_write(CONFIG, json.loads(current))
                    print("config_rejected", flush=True)
    finally:
        done.set()
        server.shutdown()
        stop(child)


if __name__ == "__main__":
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "check":
            value = json.loads(Path(sys.argv[2]).read_text())
            ok = check(value)
            print("proxy_check_ok" if ok else "proxy_check_failed")
            sys.exit(0 if ok else 1)
        serve()
    except Exception:
        print("egress_failed", file=sys.stderr)
        sys.exit(1)
