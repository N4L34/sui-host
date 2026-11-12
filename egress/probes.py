"""Bounded probes through an explicit local proxy; never a traffic relay.

Proxy addresses are locally configured numeric ingress IPs. Target names are
sent through CONNECT/SOCKS (no target getaddrinfo). Only the gateway owns
upstream credentials. This module deliberately ignores all proxy environment
variables and never retries, follows redirects, or connects to the target.
"""

from dataclasses import dataclass
import ipaddress
import math
import re
import socket
import ssl
import struct
import time

PUBLIC_ID = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}\Z")
REASONS = frozenset(
    {
        "ok",
        "config",
        "timeout",
        "proxy_connect",
        "proxy_auth",
        "proxy_rejected",
        "proxy_protocol",
        "origin_tls",
        "origin_http",
    }
)


@dataclass(frozen=True)
class Probe:
    profile: str
    upstream: str
    protocol: str
    proxy_ip: str
    proxy_port: int
    target: str
    target_port: int = 443
    ca_file: str | None = None
    target_ip: str | None = None
    tls_only: bool = False
    expected_status: int = 200


@dataclass(frozen=True)
class Result:
    reason: str
    duration: float


class ProbeError(Exception):
    pass


def _hostname(value):
    if not isinstance(value, str) or len(value) > 253:
        raise ValueError()
    value = value.rstrip(".").encode("idna").decode("ascii").lower()
    if not value or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in value.split(".")
    ):
        raise ValueError()
    return value


def probe(config: Probe, timeout: float = 15) -> Result:
    start = time.monotonic()
    conn = None
    reason = "config"
    try:
        if (
            not isinstance(timeout, (float, int))
            or not math.isfinite(timeout)
            or not 0 < timeout <= 15
        ):
            raise ValueError()
        if not PUBLIC_ID.fullmatch(config.profile) or not PUBLIC_ID.fullmatch(
            config.upstream
        ):
            raise ValueError()
        address = ipaddress.ip_address(config.proxy_ip)
        if "%" in config.proxy_ip or config.protocol not in {"http", "socks5"}:
            raise ValueError()
        for port in (config.proxy_port, config.target_port):
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError()
        if (
            type(config.expected_status) is not int
            or not 200 <= config.expected_status < 400
        ):
            raise ValueError()
        hostname = _hostname(config.target)
        deadline = start + timeout

        def remaining():
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError()
            conn.settimeout(left)

        def send(data):
            remaining()
            conn.sendall(data)

        def receive(count):
            data = b""
            while len(data) < count:
                remaining()
                part = conn.recv(count - len(data))
                if not part:
                    raise ProbeError("proxy_protocol")
                data += part
            return data

        def read_headers():
            data = b""
            while not data.endswith(b"\r\n\r\n"):
                if len(data) >= 16384:
                    raise ProbeError("proxy_protocol")
                data += receive(1)
            line = data.split(b"\r\n", 1)[0]
            if not re.fullmatch(rb"HTTP/1\.[01] [0-9]{3}(?: [^\r\n]*)?", line):
                raise ProbeError("proxy_protocol")
            return int(line.split(b" ")[1])

        reason = "proxy_connect"
        conn = socket.socket(
            socket.AF_INET6 if address.version == 6 else socket.AF_INET,
            socket.SOCK_STREAM,
        )
        remaining()
        conn.connect((str(address), config.proxy_port))
        reason = "proxy_protocol"
        connect_host = hostname
        if config.target_ip is not None:
            address_target = ipaddress.ip_address(config.target_ip)
            connect_host = (
                "[" + str(address_target) + "]"
                if address_target.version == 6
                else str(address_target)
            )
        if type(config.tls_only) is not bool:
            raise ValueError()
        authority = f"{connect_host}:{config.target_port}".encode("ascii")
        if config.protocol == "http":
            send(
                b"CONNECT "
                + authority
                + b" HTTP/1.1\r\nHost: "
                + authority
                + b"\r\n\r\n"
            )
            status = read_headers()
            if status != 200:
                raise ProbeError("proxy_auth" if status == 407 else "proxy_rejected")
        else:
            send(b"\x05\x01\x00")
            if receive(2) != b"\x05\x00":
                raise ProbeError("proxy_auth")
            encoded = hostname.encode("ascii")
            destination = (
                (b"\x03" + bytes([len(encoded)]) + encoded)
                if config.target_ip is None
                else bytes([1 if address_target.version == 4 else 4])
                + address_target.packed
            )
            send(b"\x05\x01\x00" + destination + struct.pack("!H", config.target_port))
            version, reply, reserved, atyp = receive(4)
            if version != 5 or reserved != 0:
                raise ProbeError("proxy_protocol")
            if reply != 0:
                raise ProbeError("proxy_rejected")
            if atyp not in {1, 3, 4}:
                raise ProbeError("proxy_protocol")
            count = receive(1)[0] if atyp == 3 else {1: 4, 4: 16}[atyp]
            receive(count + 2)
        reason = "origin_tls"
        context = ssl.create_default_context(cafile=config.ca_file)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        remaining()
        conn = context.wrap_socket(conn, server_hostname=hostname)
        if config.tls_only:
            return Result("ok", time.monotonic() - start)
        reason = "origin_http"
        send(b"GET / HTTP/1.1\r\nHost: " + authority + b"\r\nConnection: close\r\n\r\n")
        if read_headers() != config.expected_status:
            raise ProbeError("origin_http")
        reason = "ok"
    except (TimeoutError, socket.timeout):
        reason = "timeout"
    except ProbeError as exc:
        reason = (
            exc.args[0] if exc.args and exc.args[0] in REASONS else "proxy_protocol"
        )
    except OSError:
        # SSL errors are OSError subclasses. Never return exception messages.
        pass
    except (ValueError, TypeError, AttributeError):
        reason = "config"
    finally:
        if conn is not None:
            conn.close()
    return Result(reason, time.monotonic() - start)
