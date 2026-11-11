"""Opt-in client entrypoint guard; reads only a root-owned boot marker."""

import argparse
import ctypes
import socket
import fcntl
import struct
import json
import os
from pathlib import Path
import stat
import sys


def interfaces():
    """Linux getifaddrs enumerates *all* IPv4 and IPv6 address assignments."""
    if not sys.platform.startswith("linux"):
        raise ValueError("linux_required")

    class Ifaddrs(ctypes.Structure):
        pass

    Ifaddrs._fields_ = [
        ("next", ctypes.POINTER(Ifaddrs)),
        ("name", ctypes.c_char_p),
        ("flags", ctypes.c_uint),
        ("addr", ctypes.c_void_p),
        ("netmask", ctypes.c_void_p),
        ("ifu", ctypes.c_void_p),
        ("data", ctypes.c_void_p),
    ]
    libc = ctypes.CDLL(None)
    head = ctypes.POINTER(Ifaddrs)()
    if libc.getifaddrs(ctypes.byref(head)) != 0:
        raise ValueError("interface_read_failed")
    result = []
    seen_names = set()
    current = head
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            while current:
                item = current.contents
                current = item.next
                name = item.name.decode()
                if name == "lo":
                    continue
                if not item.addr:
                    continue
                family = ctypes.c_ushort.from_address(item.addr).value
                if item.flags & 1 or family in (socket.AF_INET, socket.AF_INET6):
                    seen_names.add(name)
                if family == socket.AF_INET6:
                    raise ValueError("native_ipv6_forbidden")
                if family != socket.AF_INET:
                    continue
                address = socket.inet_ntop(
                    socket.AF_INET, ctypes.string_at(item.addr + 4, 4)
                )
                data = fcntl.ioctl(
                    sock.fileno(), 0x8927, struct.pack("256s", item.name[:15])
                )
                mac = ":".join(f"{v:02x}" for v in data[18:24])
                result.append({"ip": address, "mac": mac, "name": name})
        if seen_names != {i["name"] for i in result}:
            raise ValueError("unaccounted_interface")
        return [{k: v for k, v in item.items() if k != "name"} for item in result]
    finally:
        libc.freeifaddrs(head)


def allowed(path, identity, token, boot_id=None, actual=None):
    try:
        path = Path(path)
        info = path.lstat()
        parent = path.parent.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_mode & 0o022
            or info.st_size > 65536
        ):
            return False
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != 0
            or parent.st_mode & 0o022
        ):
            return False
        data = json.loads(path.read_text())
        boot_id = boot_id or Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if data.keys() != {"boot_id", "identities"} or data["boot_id"] != boot_id:
            return False
        entry = data["identities"].get(identity)
        if (
            type(entry) is not dict
            or entry.keys() != {"token", "interfaces"}
            or entry["token"] != token
        ):
            return False
        observed = interfaces() if actual is None else actual
        normalized = lambda items: sorted((i["ip"], i["mac"]) for i in items)
        return bool(observed) and normalized(entry["interfaces"]) == normalized(
            observed
        )
    except Exception:
        return False


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "invalid_arguments\n")


def main():
    p = SafeParser(description=__doc__)
    p.add_argument("--identity", required=True)
    p.add_argument("--token", required=True)
    p.add_argument("command", nargs=argparse.REMAINDER)
    a = p.parse_args()
    command = a.command[1:] if a.command and a.command[0] == "--" else a.command
    if not command or not allowed("/run/egress-policy/ready.json", a.identity, a.token):
        print("egress_policy_not_ready", file=sys.stderr)
        return 1
    try:
        os.execvp(command[0], command)
    except OSError:
        print("egress_application_exec_failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
