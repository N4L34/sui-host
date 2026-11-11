"""Closed private installation compiler for pinned sing-box/GOST runtimes.

No network access or shell interpolation. Callers persist each component in a
separate 0700 directory; only gateway output contains upstream credentials.
"""

import copy
import ipaddress
import re
import ssl

from .policy import (
    _host,
    _id,
    _ip,
    _keys,
    _list,
    _literal,
    _network,
    _SPECIAL,
    validate_registry,
)

SINGBOX_VERSION = "1.13.14-sui.1"
GOST_VERSION = "3.3.3"
COMPONENTS = ("gateway", "dns", "status")


def _port(value):
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError()
    return value


def _text(value, minimum=1, maximum=4096):
    if (
        type(value) is not str
        or not minimum <= len(value.encode()) <= maximum
        or "\0" in value
    ):
        raise ValueError()
    return value


def _ca(value):
    _text(value, 0, 65536)
    if value:
        ssl.create_default_context(cadata=value)
    return value


def _public(value):
    ip = _ip(value)
    if (
        not ip.is_global
        or ip.is_multicast
        or any(ip.version == n.version and ip in n for n in _SPECIAL)
    ):
        raise ValueError()
    return str(ip)


def _tls(server_name, ca):
    result = {"enabled": True, "server_name": server_name, "min_version": "1.2"}
    if ca:
        result["certificate"] = [ca]
    return result


def _base():
    return {
        "log": {"level": "error", "timestamp": False},
        "inbounds": [],
        "outbounds": [{"type": "direct", "tag": "direct", "connect_timeout": "5s"}],
        "route": {"rules": []},
    }


def _reject(**match):
    return dict(match, action="reject", no_drop=True)


def _matcher(route):
    m = route["matcher"]
    if m["kind"] == "cidr":
        return {"ip_cidr": [m["value"]]}
    if m["kind"] == "suffix":
        # A leading dot explicitly excludes the apex in sing-box.
        return {"domain_suffix": ["." + m["value"]]}
    if _literal(m["value"]):
        return {"ip_cidr": [m["value"]]}
    return {"domain": [m["value"]]}


def _route(route, inbound, sources):
    return dict(
        _matcher(route),
        inbound=inbound,
        source_ip_cidr=sources,
        network="tcp",
        port=route["ports"],
    )


def _address_set(route):
    m = route["matcher"]
    if m["kind"] == "cidr":
        return [m["value"]]
    address = _literal(m["value"]) if m["kind"] == "exact" else None
    return (
        [str(address) + ("/32" if address.version == 4 else "/128")]
        if address
        else None
    )


def _complement(networks):
    result = []
    for version, root in [(4, "0.0.0.0/0"), (6, "::/0")]:
        remaining = [ipaddress.ip_network(root)]
        for allowed in ipaddress.collapse_addresses(
            [
                ipaddress.ip_network(n)
                for n in networks
                if ipaddress.ip_network(n).version == version
            ]
        ):
            next_remaining = []
            for network in remaining:
                if allowed.subnet_of(network):
                    next_remaining.extend(network.address_exclude(allowed))
                elif not network.subnet_of(allowed):
                    next_remaining.append(network)
            remaining = next_remaining
        result.extend(str(n) for n in remaining)
    return result


def _policy_rules(profile, tags, sources):
    result = []
    allows = [r for r in profile["routes"] if r["action"] != "DENY"]
    for r in profile["routes"]:
        if r["action"] == "DENY":
            result.append(_reject(**_route(r, tags, sources)))
    # Domain/address collisions cannot be resolved by rule ordering.
    for domain in (r for r in allows if _address_set(r) is None):
        for address in (r for r in allows if _address_set(r) is not None):
            if (domain["action"], domain.get("upstream_group")) == (
                address["action"],
                address.get("upstream_group"),
            ):
                continue
            ports = sorted(set(domain["ports"]) & set(address["ports"]))
            if ports:
                result.append(
                    _reject(
                        type="logical",
                        mode="and",
                        rules=[
                            dict(_route(domain, tags, sources), port=ports),
                            _matcher(address),
                        ],
                    )
                )
    for r in (r for r in allows if _address_set(r) is None):
        result.append(
            dict(
                _route(r, tags, sources),
                action="route",
                outbound="direct"
                if r["action"] == "DIRECT"
                else "group-" + r["upstream_group"],
            )
        )
    # CIDR/IP allow may match DNS, but EVERY candidate must be covered by the
    # same action/group. Matching any complement address rejects mixed answers.
    for port in sorted({p for r in allows if _address_set(r) for p in r["ports"]}):
        groups = {
            (r["action"], r.get("upstream_group"))
            for r in allows
            if _address_set(r) and port in r["ports"]
        }
        for action, group in groups:
            addresses = [
                n
                for r in allows
                if _address_set(r)
                and port in r["ports"]
                and (r["action"], r.get("upstream_group")) == (action, group)
                for n in _address_set(r)
            ]
            scope = {
                "inbound": tags,
                "source_ip_cidr": sources,
                "network": "tcp",
                "port": [port],
            }
            result.append(
                _reject(
                    type="logical",
                    mode="and",
                    rules=[
                        dict(scope, ip_cidr=addresses),
                        {"ip_cidr": _complement(addresses)},
                    ],
                )
            )
            result.append(
                dict(
                    scope,
                    ip_cidr=addresses,
                    action="route",
                    outbound="direct" if action == "DIRECT" else "group-" + group,
                )
            )
    return result


def _compile(value):
    _keys(
        value,
        (
            "version",
            "registry",
            "uid",
            "gid",
            "ingress",
            "control",
            "monitoring",
            "uplink",
            "upstreams",
            "groups",
            "resolver",
            "probe",
        ),
        (
            "enforcement",
            "internal_dns",
            "internal_peers",
            "direct_addresses",
            "monitoring_sources",
        ),
    )
    config = copy.deepcopy(value)
    version = _id(config["version"])
    for field in ("uid", "gid"):
        if type(config[field]) is not int or not 1 <= config[field] < 65535:
            raise ValueError()
    registry = validate_registry(config["registry"], allow_enforced=True)
    profiles = {p["id"]: p for p in registry["profiles"]}
    networks, ips = [], set()
    for field in ("control", "uplink"):
        _keys(
            config[field],
            ("subnet", *(["gateway"] if field == "uplink" else COMPONENTS)),
        )
        network = _network(config[field]["subnet"])
        if network.version != 4 or network.prefixlen > 28 or not network.is_private:
            raise ValueError()
        networks.append(network)
        for component in ["gateway"] if field == "uplink" else COMPONENTS:
            ip = _ip(config[field][component])
            if (
                ip not in network
                or ip in (network.network_address, network.broadcast_address)
                or str(ip) in ips
            ):
                raise ValueError()
            ips.add(str(ip))
    _keys(config["monitoring"], COMPONENTS)
    for ip in config["monitoring"].values():
        if _ip(ip).version != 4 or ip in ips:
            raise ValueError()
        ips.add(ip)
    ingress = {}
    for item in _list(config["ingress"], maximum=32):
        _keys(item, ("profile", "subnet", *COMPONENTS, "sources"))
        pid = _id(item["profile"])
        if pid not in profiles or pid in ingress:
            raise ValueError()
        network = _network(item["subnet"])
        if network.version != 4 or not network.is_private or network.prefixlen > 28:
            raise ValueError()
        networks.append(network)
        sources = _list(item["sources"], maximum=32)
        for ip in [item[c] for c in COMPONENTS] + sources:
            address = _ip(ip)
            if (
                address not in network
                or address in (network.network_address, network.broadcast_address)
                or ip in ips
            ):
                raise ValueError()
            ips.add(ip)
        ingress[pid] = item
    if set(ingress) != set(profiles) or any(
        a.overlaps(b) for i, a in enumerate(networks) for b in networks[i + 1 :]
    ):
        raise ValueError()
    nodes = {}
    for node in _list(config["upstreams"], maximum=8):
        _keys(
            node,
            (
                "id",
                "transport",
                "ip",
                "port",
                "server_name",
                "username",
                "password",
                "ca_pem",
            ),
        )
        node["id"] = _id(node["id"])
        if node["id"] in nodes or node["transport"] not in ("https", "socks5-tls"):
            raise ValueError()
        node["ip"] = _public(node["ip"])
        _port(node["port"])
        node["server_name"] = _host(node["server_name"])
        if _literal(node["server_name"]):
            raise ValueError()
        _text(node["username"], maximum=255)
        _text(
            node["password"], maximum=255 if node["transport"] == "socks5-tls" else 4096
        )
        if ":" in node["username"]:
            raise ValueError()
        _ca(node["ca_pem"])
        nodes[node["id"]] = node
    groups = {}
    for group in _list(config["groups"], maximum=8):
        _keys(group, ("id", "members"))
        gid = _id(group["id"])
        members = _list(group["members"], maximum=2)
        if (
            gid in groups
            or len(set(members)) != len(members)
            or not set(members) <= nodes.keys()
        ):
            raise ValueError()
        groups[gid] = members
    for profile in profiles.values():
        if any(
            r.get("upstream_group") not in groups
            for r in profile["routes"]
            if r["action"] == "PROXY"
        ):
            raise ValueError()
    resolver = config["resolver"]
    _keys(resolver, ("ip", "port", "server_name", "path", "group", "ca_pem"))
    resolver["ip"] = _public(resolver["ip"])
    _port(resolver["port"])
    _ca(resolver["ca_pem"])
    resolver["server_name"] = _host(resolver["server_name"])
    if (
        _literal(resolver["server_name"])
        or resolver["group"] not in groups
        or not re.fullmatch(r"/[a-zA-Z0-9/_-]{1,120}", resolver["path"])
    ):
        raise ValueError()
    target = config["probe"]
    _keys(target, ("target", "port", "ca_pem"))
    target["target"] = _host(target["target"])
    _port(target["port"])
    _ca(target["ca_pem"])
    if _literal(target["target"]):
        raise ValueError()
    internal = config.get("internal_dns", {})
    if type(internal) is not dict or len(internal) > 64:
        raise ValueError()
    for host, addresses in internal.items():
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", host):
            raise ValueError()
        for ip in _list(addresses, maximum=8):
            if not _ip(ip).is_private:
                raise ValueError()

    gateway = _base()
    dns = _base()
    adapters = []
    for i, node in enumerate(nodes.values()):
        outbound = {
            "type": "http",
            "tag": "node-" + node["id"],
            "server": node["ip"],
            "server_port": node["port"],
            "username": node["username"],
            "password": node["password"],
            "tls": _tls(node["server_name"], node["ca_pem"]),
            "connect_timeout": "5s",
        }
        if node["transport"] == "socks5-tls":
            outbound.update(type="socks", version="5", network="tcp")
        gateway["outbounds"].append(outbound)
    for gid, members in groups.items():
        gateway["outbounds"].append(
            {
                "type": "selector",
                "tag": "group-" + gid,
                "outbounds": ["node-" + m for m in members],
                "default": "node-" + members[0],
                "interrupt_exist_connections": False,
            }
        )
    gateway["experimental"] = {"clash_api": {"external_controller": "127.0.0.1:19090"}}
    gateway["dns"] = {
        "servers": [
            {
                "type": "udp",
                "tag": "controlled",
                "server": config["control"]["dns"],
                "server_port": 5353,
            }
        ],
        "final": "controlled",
        "disable_cache": True,
    }
    rules = gateway["route"]["rules"]
    rules.append(_reject(network="udp"))
    # DNS DoH bootstrap relay is an IP-only pinned service route; no name lookup.
    gateway["inbounds"].append(
        {
            "type": "http",
            "tag": "dns-relay",
            "listen": config["control"]["gateway"],
            "listen_port": 15353,
        }
    )
    rules.append(
        {
            "inbound": ["dns-relay"],
            "source_ip_cidr": [config["control"]["dns"]],
            "ip_cidr": [resolver["ip"]],
            "port": [resolver["port"]],
            "action": "route",
            "outbound": "group-" + resolver["group"],
        }
    )
    rules.append(_reject(inbound=["dns-relay"]))
    bootstrap_probes = []
    for i, nid in enumerate(nodes):
        tag = "bootstrap-" + nid
        port = 23000 + i
        gateway["inbounds"].append(
            {
                "type": "http",
                "tag": tag,
                "listen": config["control"]["gateway"],
                "listen_port": port,
            }
        )
        rules.append(
            {
                "inbound": [tag],
                "source_ip_cidr": [config["control"]["gateway"]],
                "ip_cidr": [resolver["ip"]],
                "port": [resolver["port"]],
                "action": "route",
                "outbound": "node-" + nid,
            }
        )
        rules.append(_reject(inbound=[tag]))
        bootstrap_probes.append(
            {
                "profile": "system",
                "upstream": nid,
                "protocol": "http",
                "proxy_ip": config["control"]["gateway"],
                "proxy_port": port,
                "target": resolver["server_name"],
                "target_ip": resolver["ip"],
                "target_port": resolver["port"],
                "tls_only": True,
                "ca_file": "/run/private/resolver-ca.pem"
                if resolver["ca_pem"]
                else None,
            }
        )
    # Resolve *before* every candidate-based deny; the deny match is ANY candidate.
    rules.append({"action": "resolve", "server": "controlled", "disable_cache": True})
    rules.append(_reject(ip_is_private=True))
    forbidden = sorted(
        set(
            registry["denied_networks"]
            + [str(n) for n in _SPECIAL]
            + [str(n) for n in networks]
            + [str(_ip(ip)) for ip in config["monitoring"].values()]
            + ["::ffff:0:0/96"]
        )
    )
    rules.append(_reject(ip_cidr=forbidden))
    status_probes = []
    local_probes = []

    def add_probe(pid, upstream, protocol, ip, port, required):
        p = {
            "profile": pid,
            "upstream": upstream,
            "protocol": protocol,
            "proxy_ip": ip,
            "proxy_port": port,
            "target": target["target"],
            "target_port": target["port"],
            "ca_file": "/run/private/probe-ca.pem" if target["ca_pem"] else None,
            "required": required,
        }
        status_probes.append(p)
        return p

    for i, (pid, profile) in enumerate(profiles.items()):
        item = ingress[pid]
        tags = []
        for proto, port in [("http", 3128), ("socks", 1080)]:
            if profile["integration"] not in ("both", proto):
                continue
            tag = pid + "-" + proto
            tags.append(tag)
            gateway["inbounds"].append(
                {
                    "type": proto,
                    "tag": tag,
                    "listen": item["gateway"],
                    "listen_port": port,
                }
            )
            add_probe(
                pid,
                "effective",
                "socks5" if proto == "socks" else "http",
                item["gateway"],
                port,
                True,
            )
        sources = item["sources"] + [item["status"]]
        rules.extend(_policy_rules(profile, tags, sources))
    for i, nid in enumerate(nodes):
        for j, proto in enumerate(("http", "socks")):
            tag = "probe-" + nid + "-" + proto
            port = 22000 + i * 2 + j
            gateway["inbounds"].append(
                {
                    "type": proto,
                    "tag": tag,
                    "listen": config["control"]["gateway"],
                    "listen_port": port,
                }
            )
            match = {
                "inbound": [tag],
                "source_ip_cidr": [
                    config["control"]["status"],
                    config["control"]["gateway"],
                ],
                "domain": [target["target"]],
                "port": [target["port"]],
                "network": "tcp",
            }
            rules.append(dict(match, action="route", outbound="node-" + nid))
            p = add_probe(
                "system",
                nid,
                "http" if proto == "http" else "socks5",
                config["control"]["gateway"],
                port,
                False,
            )
            local_probes.append(
                dict(
                    p, ca_file="/run/private/probe-ca.pem" if target["ca_pem"] else None
                )
            )
    rules.append(_reject())
    # sing-box's SOCKS inbound also enables SOCKS4/UDP. Use GOST's explicit
    # SOCKS5 CONNECT-only handler, with source admission, into a private HTTP
    # inbound tied to exactly the same profile. No public mixed listener.
    for i, inbound in enumerate(list(gateway["inbounds"])):
        if inbound["type"] != "socks":
            continue
        external_ip, external_port = inbound["listen"], inbound["listen_port"]
        internal_port = 24000 + i
        inbound.update(type="http", listen="127.0.0.1", listen_port=internal_port)
        if inbound["tag"].startswith("probe-"):
            sources = [config["control"]["status"], config["control"]["gateway"]]
        else:
            pid = inbound["tag"][:-6]
            sources = ingress[pid]["sources"] + [ingress[pid]["status"]]
        frontend = {
            "services": [
                {
                    "name": "socks5",
                    "addr": f"{external_ip}:{external_port}",
                    "admission": "clients",
                    "handler": {
                        "type": "socks5",
                        "chain": "local",
                        "retries": 0,
                        "metadata": {"udp": False, "bind": False},
                    },
                    "listener": {"type": "tcp"},
                }
            ],
            "admissions": [{"name": "clients", "whitelist": True, "matchers": sources}],
            "chains": [
                {
                    "name": "local",
                    "hops": [
                        {
                            "name": "local",
                            "nodes": [
                                {
                                    "name": "local",
                                    "addr": f"127.0.0.1:{internal_port}",
                                    "connector": {"type": "http"},
                                    "dialer": {"type": "tcp"},
                                }
                            ],
                        }
                    ],
                }
            ],
            "log": {"output": "none"},
        }
        adapters.append({"index": 100 + i, "config": frontend, "ca_pem": ""})
        for rule in rules:

            def allow_loopback(r):
                if (
                    inbound["tag"] in r.get("inbound", [])
                    and "source_ip_cidr" in r
                    and "127.0.0.1" not in r["source_ip_cidr"]
                ):
                    r["source_ip_cidr"].append("127.0.0.1")
                for nested in r.get("rules", []):
                    allow_loopback(nested)

            allow_loopback(rule)
    dns["outbounds"] = [
        {
            "type": "http",
            "tag": "relay",
            "server": config["control"]["gateway"],
            "server_port": 15353,
            "connect_timeout": "5s",
        }
    ]
    dns["dns"] = {
        "servers": [
            {
                "type": "https",
                "tag": "doh",
                "server": resolver["ip"],
                "server_port": resolver["port"],
                "path": resolver["path"],
                "tls": _tls(resolver["server_name"], resolver["ca_pem"]),
                "detour": "relay",
            }
        ],
        "final": "doh",
        "disable_cache": True,
    }
    dns["dns"]["rules"] = []
    for host, addresses in internal.items():
        dns["dns"]["rules"].append(
            {
                "domain": [host],
                "action": "predefined",
                "answer": [
                    f"{host}. 30 IN {'A' if _ip(ip).version == 4 else 'AAAA'} {ip}"
                    for ip in addresses
                ],
            }
        )
    for i, item in enumerate([config["control"], *ingress.values()]):
        tag = "dns-" + str(i)
        dns["inbounds"].append(
            {
                "type": "direct",
                "tag": tag,
                "listen": item["dns"],
                "listen_port": 5353 if i == 0 else 53,
            }
        )
        sources = (
            [config["control"]["gateway"], config["control"]["status"]]
            if i == 0
            else item["sources"] + [item["gateway"], item["status"]]
        )
        dns["route"]["rules"].append(
            {"inbound": [tag], "source_ip_cidr": sources, "action": "hijack-dns"}
        )
    dns["route"]["rules"].append(_reject())
    services = {
        c: {"user": f"{config['uid']}:{config['gid']}", "networks": {}}
        for c in COMPONENTS
    }
    compose = {"services": services, "networks": {}}
    for name, values in [
        ("control", config["control"]),
        ("uplink", config["uplink"]),
    ] + [("ingress-" + pid, item) for pid, item in ingress.items()]:
        compose["networks"][name] = {
            "name": "egress_" + name,
            "internal": name != "uplink",
            "ipam": {"config": [{"subnet": values["subnet"]}]},
        }
        for c in COMPONENTS:
            if c not in values:
                continue
            entry = {"ipv4_address": values[c]}
            if name.startswith("ingress-"):
                entry["aliases"] = [
                    "sui-egress" if c == "gateway" else "sui-egress-" + c
                ]
            services[c]["networks"][name] = entry
    for c in COMPONENTS:
        services[c]["networks"]["monitoring"] = {
            "ipv4_address": config["monitoring"][c]
        }
    services["status"]["networks"]["monitoring"]["aliases"] = ["sui-egress-status"]
    output = {
        "gateway": {
            "version": version,
            "singbox": gateway,
            "adapters": adapters,
            "groups": groups,
            "probes": local_probes,
            "telemetry": {
                "listen": config["control"]["gateway"],
                "port": 18081,
                "sources": [config["control"]["status"]],
            },
            "probe_ca": target["ca_pem"],
            "bootstrap_probes": bootstrap_probes,
            "resolver_ca": resolver["ca_pem"],
        },
        "dns": {"version": version, "singbox": dns, "adapters": []},
        "status": {
            "version": version,
            "listen": config["monitoring"]["status"],
            "port": 8080,
            "probes": status_probes,
            "telemetry_ip": config["control"]["gateway"],
            "telemetry_port": 18081,
            "probe_ca": target["ca_pem"],
            "dns_ip": config["control"]["dns"],
            "dns_port": 5353,
            "dns_target": target["target"],
        },
        "compose": compose,
    }
    if "enforcement" in config or any(
        p["mode"] == "enforced" for p in registry["profiles"]
    ):
        if "enforcement" not in config:
            raise ValueError()
        from .enforcement import validate_inventory, validate_bindings

        inventory = validate_inventory(config["enforcement"])
        validate_bindings(config, inventory)
        by_ip = {
            n["ip"]: n["mac"] for i in inventory["identities"] for n in i["interfaces"]
        }
        for service in services.values():
            for network in service["networks"].values():
                if network["ipv4_address"] not in by_ip:
                    raise ValueError()
                network["mac_address"] = by_ip[network["ipv4_address"]]
        from .migrate import identity_token

        for component in COMPONENTS:
            identities = [i for i in inventory["identities"] if i["role"] == component]
            if len(identities) != 1:
                raise ValueError()
            identity = identities[0]
            output[component]["guard"] = {
                "identity": identity["id"],
                "token": identity_token(identity),
            }
            services[component]["volumes"] = [
                {
                    "type": "bind",
                    "source": "/run/sui-egress-policy",
                    "target": "/run/egress-policy",
                    "read_only": True,
                    "bind": {"create_host_path": False},
                }
            ]
    from .bundles import seal

    for component in COMPONENTS:
        output[component] = seal(output[component])
    return output


def compile_installation(value):
    try:
        return _compile(value)
    except Exception:
        raise ValueError("installation_invalid") from None
