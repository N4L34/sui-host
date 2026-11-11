"""Loopback-only fixtures. They NEVER resolve or dial a requested destination."""

import base64
import contextlib
import select
import socket
import socketserver
import ssl
import subprocess
import threading
from pathlib import Path


def certificate(directory):
    cert, key = Path(directory) / "cert.pem", Path(directory) / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=origin.test.invalid",
            "-addext",
            "subjectAltName=DNS:origin.test.invalid,DNS:upstream.test.invalid,DNS:resolver.test.invalid,DNS:direct.test.invalid",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=15,
    )
    key.chmod(0o600)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    return str(cert), ctx


def exact(sock, count):
    result = b""
    while len(result) < count:
        part = sock.recv(count - len(result))
        if not part:
            raise EOFError()
        result += part
    return result


def headers(sock):
    result = b""
    while b"\r\n\r\n" not in result:
        result += exact(sock, 1)
        if len(result) > 16384:
            raise ValueError("headers_limit")
    return result


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def handle_error(self, request, client_address):
        # Tests intentionally cause TLS/handshake errors. Never log raw input.
        pass


@contextlib.contextmanager
def origin(tls, stream=False):
    state = {"requests": 0, "headers": []}
    finished = threading.Event()

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(3)
            with tls.wrap_socket(self.request, server_side=True) as conn:
                data = headers(conn)
                state["requests"] += 1
                state["headers"].append(data)
                if stream:
                    conn.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n"
                    )
                    while not finished.wait(0.1):
                        conn.sendall(b"data: fixture\n\n")
                else:
                    conn.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
                    )

    with Server(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address, state
        finally:
            finished.set()
            server.shutdown()
            thread.join(timeout=3)


@contextlib.contextmanager
def proxy(
    target,
    protocol="http",
    tls=None,
    credentials=None,
    reject=False,
    slow=False,
    bind="127.0.0.1",
):
    state = {"requests": 0, "auth": 0, "destinations": []}

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(3)
            conn = (
                tls.wrap_socket(self.request, server_side=True) if tls else self.request
            )
            with conn:
                if slow:
                    # Wait for client cancellation, bounded by the socket deadline.
                    while conn.recv(1024):
                        pass
                    return
                if protocol == "http":
                    request = headers(conn)
                    state["requests"] += 1
                    state["destinations"].append(request.split(b" ")[1])
                    if credentials:
                        wanted = base64.b64encode((":".join(credentials)).encode())
                        if b"Proxy-Authorization: Basic " + wanted not in request:
                            conn.sendall(
                                b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n"
                            )
                            return
                        state["auth"] += 1
                    if state.get("reject", reject):
                        conn.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                        return
                    conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                else:
                    version, count = exact(conn, 2)
                    methods = exact(conn, count)
                    if version != 5:
                        return
                    if credentials:
                        if 2 not in methods:
                            conn.sendall(b"\x05\xff")
                            return
                        conn.sendall(b"\x05\x02")
                        auth_version, count = exact(conn, 2)
                        user = exact(conn, count).decode()
                        password = exact(conn, exact(conn, 1)[0]).decode()
                        good = auth_version == 1 and (user, password) == credentials
                        conn.sendall(bytes([1, 0 if good else 1]))
                        if not good:
                            return
                        state["auth"] += 1
                    else:
                        conn.sendall(b"\x05\x00")
                    version, command, reserved, atyp = exact(conn, 4)
                    size = {1: 4, 4: 16}.get(atyp)
                    if atyp == 3:
                        size = exact(conn, 1)[0]
                    if size is None:
                        return
                    destination = exact(conn, size)
                    port = exact(conn, 2)
                    state["requests"] += 1
                    state["destinations"].append((atyp, destination, port))
                    if reject or command != 1 or reserved != 0:
                        conn.sendall(b"\x05\x02\x00\x01" + bytes(6))
                        return
                    conn.sendall(b"\x05\x00\x00\x01" + bytes(6))
                # Fixed fixture origin only: never use user-supplied destination.
                selected = (
                    target.get(state["destinations"][-1])
                    if isinstance(target, dict)
                    else target
                )
                if selected is None:
                    return
                with socket.create_connection(selected, timeout=3) as remote:
                    for _ in range(1000):
                        ready, _, _ = select.select([conn, remote], [], [], 3)
                        if not ready:
                            return
                        for source in ready:
                            data = source.recv(16384)
                            if not data:
                                return
                            (remote if source is conn else conn).sendall(data)

    with Server((bind, 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address, state
        finally:
            server.shutdown()
            thread.join(timeout=3)
