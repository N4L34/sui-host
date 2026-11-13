"""Explicit optional gateway mode. Required never attempts a direct socket."""

import ipaddress
import re
import socket
import time


def required(config):
    mode = config.get("SUI_EGRESS_MODE", "legacy")
    if mode not in ("legacy", "required"):
        raise ValueError("egress_mode_invalid")
    return mode == "required"


def endpoint(config):
    if not required(config):
        raise ValueError("egress_mode_invalid")
    try:
        host = config["SUI_EGRESS_HOST"]
        try:
            host = str(ipaddress.ip_address(host))
        except ValueError:
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}", host):
                raise ValueError("egress_endpoint_invalid")
        port = int(config.get("SUI_EGRESS_PORT", "1080"))
        if not 1 <= port <= 65535:
            raise ValueError()
        return host, port
    except Exception:
        raise ValueError("egress_endpoint_invalid") from None


def open_required(config, target, port, timeout=15):
    if type(timeout) not in (int, float) or not 0 < timeout <= 15:
        raise ValueError("egress_timeout_invalid")
    deadline = time.monotonic() + timeout
    address = endpoint(config)
    name = target.encode("idna")
    if not 1 <= len(name) <= 255 or not 1 <= port <= 65535:
        raise ValueError("egress_target_invalid")
    sock = socket.create_connection(address, min(2, timeout))

    def remaining():
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("egress_deadline")
        sock.settimeout(left)

    def exact(count):
        data = b""
        while len(data) < count:
            remaining()
            chunk = sock.recv(count - len(data))
            if not chunk:
                raise OSError("egress_handshake_failed")
            data += chunk
        return data

    try:
        remaining()
        sock.sendall(b"\x05\x01\x00")
        if exact(2) != b"\x05\x00":
            raise OSError("egress_handshake_failed")
        remaining()
        sock.sendall(
            b"\x05\x01\x00\x03" + bytes([len(name)]) + name + port.to_bytes(2, "big")
        )
        head = exact(4)
        if head[:3] != b"\x05\x00\x00" or head[3] not in (1, 3, 4):
            raise OSError("egress_handshake_failed")
        count = exact(1)[0] if head[3] == 3 else {1: 4, 4: 16}[head[3]]
        exact(count + 2)
        return sock
    except Exception:
        sock.close()
        raise


def chrome_arguments(config):
    if not required(config):
        return []
    host, _ = endpoint(config)
    if ":" in host:
        host = "[" + host + "]"
    # HTTP proxy handles CONNECT; QUIC and browser resolver fallbacks are
    # disabled. This mode configures the application; it is not host enforcement.
    return [
        f"--proxy-server=http://{host}:3128",
        "--proxy-bypass-list=<-loopback>",
        "--disable-quic",
        "--dns-over-https-mode=off",
        f"--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE {host}",
    ]
