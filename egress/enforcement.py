"""Scoped nftables inet+bridge policy; explicit operator-only host operations.

No global flush or Docker chain modification. The permanent reserved source
identities are denied on every route including host/same-bridge. Enforcement
must be installed before Docker starts; applications do not apply host rules.
"""

import argparse
import copy
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from .policy import _id, _keys, _list, _ip
from .__main__ import SafeParser
from .private_files import load_private_json, write_private_json


def validate_inventory(value):
    try:
        _keys(
            value,
            ("version", "backend", "ipv6_disabled", "bridge_netfilter", "identities"),
        )
        if (
            value["backend"] != "nftables"
            or value["ipv6_disabled"] is not True
            or value["bridge_netfilter"] is not True
        ):
            raise ValueError()
        _id(value["version"])
        result = copy.deepcopy(value)
        ids = set()
        ips = set()
        macs = set()
        for identity in _list(result["identities"], maximum=64):
            _keys(
                identity,
                (
                    "id",
                    "role",
                    "project",
                    "service",
                    "profile",
                    "interfaces",
                    "allow",
                    "inbound",
                ),
                ("retired",),
            )
            if "retired" in identity and identity["retired"] is not True:
                raise ValueError()
            for key in ("id", "project", "service", "profile"):
                _id(identity[key])
            if identity["id"] in ids or identity["role"] not in (
                "client",
                "gateway",
                "dns",
                "status",
            ):
                raise ValueError()
            ids.add(identity["id"])
            for interface in _list(identity["interfaces"], maximum=8):
                _keys(interface, ("network", "ip", "mac"))
                _id(interface["network"])
                ip = _ip(interface["ip"])
                mac = interface["mac"]
                if (
                    ip.version != 4
                    or not ip.is_private
                    or str(ip) in ips
                    or not re.fullmatch(r"02:(?:[0-9a-f]{2}:){4}[0-9a-f]{2}", mac)
                    or mac in macs
                ):
                    raise ValueError()
                ips.add(str(ip))
                macs.add(mac)
            for kind in ("allow", "inbound"):
                for endpoint in _list(identity[kind], maximum=256, empty=True):
                    _keys(endpoint, ("ip", "protocol", "ports"))
                    ip = _ip(endpoint["ip"])
                    if (
                        ip.version != 4
                        or ip.is_unspecified
                        or ip.is_multicast
                        or ip.is_loopback
                        or endpoint["protocol"] not in ("tcp", "udp")
                    ):
                        raise ValueError()
                    ports = _list(endpoint["ports"], maximum=64)
                    if any(
                        type(p) is not int or not 1 <= p <= 65535 for p in ports
                    ) or len(set(ports)) != len(ports):
                        raise ValueError()
        return result
    except Exception:
        raise ValueError("inventory_invalid") from None


def validate_bindings(config, inventory):
    """Enforced allowlists are an exact subset of compiled permitted flows."""
    identities = inventory["identities"]
    components = ("gateway", "dns", "status")

    def tuples(entries):
        return {(e["ip"], e["protocol"], p) for e in entries for p in e["ports"]}

    internal = config.get("internal_peers", {})
    if type(internal) is not dict or not set(internal) <= set(
        i["profile"] for i in config["ingress"]
    ):
        raise ValueError("inventory_bindings")
    direct = config.get("direct_addresses", {})
    if type(direct) is not dict:
        raise ValueError("inventory_bindings")
    direct_ips = set()
    for rid, addresses in direct.items():
        routes = [
            r
            for p in config["registry"]["profiles"]
            for r in p["routes"]
            if r["id"] == rid and r["action"] == "DIRECT"
        ]
        if len(routes) != 1:
            raise ValueError("inventory_bindings")
        for address in _list(addresses, maximum=64):
            ip = _ip(address)
            if not ip.is_global or ip.version != 4:
                raise ValueError("inventory_bindings")
            for port in routes[0]["ports"]:
                direct_ips.add((str(ip), "tcp", port))
    monitoring_sources = config.get("monitoring_sources", [])
    for address in _list(monitoring_sources, maximum=16, empty=True):
        if not _ip(address).is_private or _ip(address).is_loopback:
            raise ValueError("inventory_bindings")
    for profile in config["registry"]["profiles"]:
        matching = [
            i
            for i in identities
            if i["role"] == "client"
            and i["profile"] == profile["id"]
            and i["project"] == profile["project"]
            and i["service"] == profile["service"]
        ]
        ingress = next(i for i in config["ingress"] if i["profile"] == profile["id"])
        if profile["mode"] == "enforced" and (
            not matching
            or not set(ingress["sources"])
            <= set(n["ip"] for i in matching for n in i["interfaces"])
        ):
            raise ValueError("inventory_bindings")
        peers = internal.get(profile["id"], [])
        for peer in _list(peers, empty=True):
            _keys(peer, ("ip", "protocol", "ports"), ("names",))
            ip = _ip(peer["ip"])
            for name in _list(peer.get("names", []), maximum=8, empty=True):
                if (
                    type(name) is not str
                    or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", name)
                    or ".." in name
                ):
                    raise ValueError("inventory_bindings")
            if not ip.is_private or ip.is_link_local or ip.is_loopback:
                raise ValueError("inventory_bindings")
        permitted = {
            (ingress["gateway"], "tcp", 3128),
            (ingress["gateway"], "tcp", 1080),
            (ingress["dns"], "udp", 53),
            (ingress["dns"], "tcp", 53),
        } | tuples(peers)
        for identity in matching:
            if not tuples(identity["allow"]) <= permitted:
                raise ValueError("inventory_bindings")
            if any(e["ip"] not in monitoring_sources for e in identity["inbound"]):
                raise ValueError("inventory_bindings")
    for component in components:
        expected = {config["control"][component], config["monitoring"][component]} | {
            i[component] for i in config["ingress"]
        }
        if component == "gateway":
            expected.add(config["uplink"]["gateway"])
        component_identities = [i for i in identities if i["role"] == component]
        found = {n["ip"] for i in component_identities for n in i["interfaces"]}
        if expected != found or any(
            i["project"] != "egress" or i["service"] != component
            for i in component_identities
        ):
            raise ValueError("inventory_bindings")
        if component == "gateway":
            permitted = (
                {(n["ip"], "tcp", n["port"]) for n in config["upstreams"]}
                | {(config["control"]["dns"], "udp", 5353)}
                | direct_ips
            )
        elif component == "dns":
            permitted = {(config["control"]["gateway"], "tcp", 15353)}
        else:
            permitted = {
                (i["gateway"], "tcp", p)
                for i in config["ingress"]
                for p in (3128, 1080)
            } | {
                (config["control"]["dns"], "udp", 5353),
                (config["control"]["gateway"], "tcp", 18081),
            }
            permitted |= {
                (config["control"]["gateway"], "tcp", 22000 + n * 2 + j)
                for n in range(len(config["upstreams"]))
                for j in (0, 1)
            }
        for identity in component_identities:
            if not tuples(identity["allow"]) <= permitted:
                raise ValueError("inventory_bindings")
    # Native IPv6 is disabled on selected clients. The gateway still forwards
    # IPv6 SOCKS/CONNECT destinations numerically through encrypted upstream.


def ruleset(value, replace=False):
    value = validate_inventory(value)
    identities = value["identities"]
    lines = []
    if replace:
        lines.extend(["delete table inet sui_egress", "delete table bridge sui_egress"])
    for family in ("inet", "bridge"):
        lines.append(f"table {family} sui_egress {{")
        for index, identity in enumerate(identities):
            lines.append(f" chain out_{index} {{")
            for endpoint in identity["inbound"]:
                ports = ", ".join(str(p) for p in endpoint["ports"])
                prefix = (
                    "ct direction reply ct state established "
                    if family == "inet"
                    else ""
                )
                lines.append(
                    f"  {prefix}ip daddr {endpoint['ip']} {endpoint['protocol']} sport {{ {ports} }} return"
                )
            for endpoint in identity["allow"]:
                ports = ", ".join(str(p) for p in endpoint["ports"])
                lines.append(
                    f"  ip daddr {endpoint['ip']} {endpoint['protocol']} dport {{ {ports} }} return"
                )
            lines.extend(["  counter drop", " }", f" chain in_{index} {{"])
            for endpoint in identity["allow"]:
                ports = ", ".join(str(p) for p in endpoint["ports"])
                prefix = (
                    "ct direction reply ct state established "
                    if family == "inet"
                    else ""
                )
                lines.append(
                    f"  {prefix}ip saddr {endpoint['ip']} {endpoint['protocol']} sport {{ {ports} }} return"
                )
            for endpoint in identity["inbound"]:
                ports = ", ".join(str(p) for p in endpoint["ports"])
                lines.append(
                    f"  ip saddr {endpoint['ip']} {endpoint['protocol']} dport {{ {ports} }} return"
                )
            lines.extend(["  counter drop", " }"])
        if family == "inet":
            for hook in ("forward", "input", "output"):
                lines.append(
                    f" chain {hook} {{ type filter hook {hook} priority -20; policy accept;"
                )
                for index, identity in enumerate(identities):
                    for interface in identity["interfaces"]:
                        lines.append(f"  ip saddr {interface['ip']} jump out_{index}")
                        if hook != "input":
                            lines.append(
                                f"  ip daddr {interface['ip']} jump in_{index}"
                            )
                lines.append(" }")
        else:
            lines.append(
                " chain identity { type filter hook prerouting priority -300; policy accept;"
            )
            for identity in identities:
                for interface in identity["interfaces"]:
                    ip, mac = interface["ip"], interface["mac"]
                    lines.extend(
                        [
                            f"  ether saddr {mac} ether type ip6 counter drop",
                            f"  ether daddr {mac} ether type ip6 counter drop",
                            f"  ether saddr {mac} ether type ip ip saddr != {ip} counter drop",
                            f"  ether type ip ip saddr {ip} ether saddr != {mac} counter drop",
                            f"  ether saddr {mac} ether type arp arp saddr ip != {{ 0.0.0.0, {ip} }} counter drop",
                            f"  ether type arp arp saddr ip {ip} ether saddr != {mac} counter drop",
                        ]
                    )
            lines.extend(
                [
                    " }",
                    " chain forward { type filter hook forward priority -200; policy accept;",
                ]
            )
            for index, identity in enumerate(identities):
                for interface in identity["interfaces"]:
                    lines.append(
                        f"  ether saddr {interface['mac']} ether type ip jump out_{index}"
                    )
                    lines.append(
                        f"  ether daddr {interface['mac']} ether type ip jump in_{index}"
                    )
            lines.append(" }")
        lines.append("}")
    return "\n".join(lines) + "\n"


def _run(args, data=None):
    result = subprocess.run(args, input=data, capture_output=True, timeout=15)
    if result.returncode:
        raise ValueError("host_policy_failed")
    return result.stdout


def preserve_scopes(previous, current):
    current = validate_inventory(current)
    previous = validate_inventory(previous) if previous else None
    if not previous:
        return current
    seen = {(n["ip"], n["mac"]) for i in current["identities"] for n in i["interfaces"]}
    for identity in previous["identities"]:
        missing = [n for n in identity["interfaces"] if (n["ip"], n["mac"]) not in seen]
        if missing:
            retired = copy.deepcopy(identity)
            retired.update(
                id="retired-"
                + hashlib.sha256(
                    json.dumps(missing, sort_keys=True).encode()
                ).hexdigest()[:16],
                interfaces=missing,
                allow=[],
                inbound=[],
                retired=True,
            )
            current["identities"].append(retired)
    return validate_inventory(current)


def apply_inventory(value, backup_path):
    if os.geteuid() != 0:
        raise ValueError("root_required")
    value = validate_inventory(value)
    state = Path(backup_path).with_name("active.json")
    if any(
        Path("/proc/sys/net/bridge/" + name).read_text().strip() != "1"
        for name in ("bridge-nf-call-iptables", "bridge-nf-call-ip6tables")
    ):
        raise ValueError("bridge_netfilter_required")
    previous = load_private_json(state, validate_inventory) if state.exists() else None
    present = []
    for family in ("inet", "bridge"):
        result = subprocess.run(
            ["nft", "list", "table", family, "sui_egress"],
            capture_output=True,
            timeout=5,
        )
        present.append(result.returncode == 0)
    if present[0] != present[1] or (present[0] and previous is None):
        raise ValueError("policy_state_requires_repair")
    value = preserve_scopes(previous, value)
    text = ruleset(value, replace=present[0]).encode()
    _run(["nft", "--check", "-f", "-"], text)
    write_private_json(backup_path, {"version": "backup-2", "inventory": previous})
    _run(["nft", "-f", "-"], text)
    write_private_json(state, value)
    publish_marker(value)


def publish_marker(inventory):
    from .migrate import identity_token
    from .daemon import _write_text

    directory = Path("/run/sui-egress-policy")
    directory.mkdir(mode=0o755, exist_ok=True)
    if (
        directory.is_symlink()
        or directory.stat().st_uid != 0
        or directory.stat().st_mode & 0o022
    ):
        raise ValueError("marker_permissions")
    data = {
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "identities": {
            i["id"]: {
                "token": identity_token(i),
                "interfaces": [
                    {"ip": n["ip"], "mac": n["mac"]} for n in i["interfaces"]
                ],
            }
            for i in inventory["identities"]
            if not i.get("retired")
        },
    }
    temporary = directory / "ready.new"
    _write_text(temporary, json.dumps(data))
    temporary.chmod(0o644)
    os.replace(temporary, directory / "ready.json")


def rollback(path):
    if os.geteuid() != 0:
        raise ValueError("root_required")
    backup = load_private_json(path)
    if (
        type(backup) is not dict
        or backup.keys() != {"version", "inventory"}
        or backup["version"] != "backup-2"
        or backup["inventory"] is None
    ):
        raise ValueError("no_previous_deny_policy")
    state = Path(path).with_name("active.json")
    current = load_private_json(state, validate_inventory)
    previous = preserve_scopes(current, validate_inventory(backup["inventory"]))
    # Recompile validated inventory; never execute saved arbitrary nft text.
    text = ruleset(previous, replace=True).encode()
    _run(["nft", "--check", "-f", "-"], text)
    _run(["nft", "-f", "-"], text)
    write_private_json(state, previous)
    publish_marker(previous)


def check_container(identity, inspect):
    """Validate restricted docker inspect metadata; never return raw values."""
    try:
        host = inspect["HostConfig"]
        networks = inspect["NetworkSettings"]["Networks"]
        config = inspect["Config"]
        if (
            host.get("Privileged")
            or host.get("NetworkMode") == "host"
            or set(host.get("CapDrop") or []) != {"ALL"}
            or host.get("CapAdd")
        ):
            raise ValueError()
        if "no-new-privileges:true" not in host.get("SecurityOpt", []):
            raise ValueError()
        if any("docker.sock" in m.get("Source", "") for m in inspect.get("Mounts", [])):
            raise ValueError()
        expected = {i["network"]: i for i in identity["interfaces"]}
        if set(networks) != set(expected):
            raise ValueError()
        if host.get("Sysctls", {}).get("net.ipv6.conf.all.disable_ipv6") != "1":
            raise ValueError()
        for name, network in networks.items():
            target = expected[name]
            if (
                network["IPAddress"] != target["ip"]
                or network["MacAddress"].lower() != target["mac"]
                or network.get("GlobalIPv6Address")
            ):
                raise ValueError()
        labels = config["Labels"]
        if (
            labels.get("com.docker.compose.project") != identity["project"]
            or labels.get("com.docker.compose.service") != identity["service"]
        ):
            raise ValueError()
        return True
    except Exception:
        raise ValueError("container_identity_invalid") from None


def main():
    p = SafeParser(description=__doc__)
    p.add_argument(
        "command", choices=["render", "check", "apply", "rollback", "verify-container"]
    )
    p.add_argument("file")
    p.add_argument("--backup")
    p.add_argument("--identity")
    p.add_argument("--output")
    a = p.parse_args()
    try:
        if a.command == "rollback":
            rollback(a.file)
        else:
            inventory = load_private_json(a.file, validate_inventory)
            if a.command == "render":
                if not a.output:
                    raise ValueError()
                write_private_json(
                    a.output,
                    {"version": inventory["version"], "ruleset": ruleset(inventory)},
                )
            if a.command == "check":
                _run(
                    ["nft", "--check", "-f", "-"],
                    ruleset(inventory, replace=False).encode(),
                )
            elif a.command == "apply":
                if not a.backup:
                    raise ValueError()
                apply_inventory(inventory, a.backup)
            elif a.command == "verify-container":
                identity = next(
                    i for i in inventory["identities"] if i["id"] == a.identity
                )
                # Docker output captured, checked, never emitted.
                data = json.loads(
                    _run(
                        [
                            "docker",
                            "inspect",
                            "--type",
                            "container",
                            identity["project"] + "-" + identity["service"] + "-1",
                        ]
                    )
                )
                check_container(identity, data[0])
        print("host_policy_ok")
        return 0
    except Exception:
        print("host_policy_failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
