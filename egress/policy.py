"""Offline policy reference only: no sockets, DNS, proxy or packet enforcement.

Closed schema: registry version/profiles/denied_networks; profiles contain public
id/project/service/owner, integration (http/socks/both), mode (pilot), routes.
Routes contain id/action/matcher/ports and a PROXY-only upstream_group. Ports
are TCP destination numbers; suffix matches subdomains, never its apex.
"""

from dataclasses import dataclass
import ipaddress
import re

PUBLIC_ID = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}\Z")
_SPECIAL = tuple(
    ipaddress.ip_network(n)
    for n in (
        "100.64.0.0/10",
        "192.0.0.0/24",
        "192.31.196.0/24",
        "192.52.193.0/24",
        "192.88.99.0/24",
        "192.175.48.0/24",
        "2620:4f:8000::/48",
        "64:ff9b::/96",
        "64:ff9b:1::/48",
        "2001::/23",
        "2002::/16",
        "100::/64",
        "100:0:0:1::/64",
        "3fff::/20",
        "fec0::/10",
    )
)


def _fail(code="invalid_registry"):
    raise ValueError(code) from None


def _keys(value, required, optional=()):
    if (
        type(value) is not dict
        or not set(required) <= value.keys()
        or value.keys() - set(required) - set(optional)
    ):
        _fail()


def _id(value):
    if type(value) is not str or not PUBLIC_ID.fullmatch(value):
        _fail()
    return value


def _list(value, maximum=256, empty=False):
    if type(value) is not list or len(value) > maximum or (not value and not empty):
        _fail()
    return value


def _ip(value):
    if type(value) is not str or "%" in value or len(value) > 64:
        _fail("invalid_destination")
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        _fail("invalid_destination")
    return getattr(address, "ipv4_mapped", None) or address


def _host(value):
    if (
        type(value) is not str
        or not value
        or len(value) > 254
        or value != value.strip()
    ):
        _fail("invalid_destination")
    try:
        return str(_ip(value))
    except ValueError:
        pass
    # No scoped/bracketed literals or legacy inet_aton decimal/octal/hex forms.
    if (
        ":" in value
        or "%" in value
        or re.fullmatch(
            r"(?:0[xX][0-9a-fA-F]+|[0-9]+)(?:\.(?:0[xX][0-9a-fA-F]+|[0-9]+))*\.?", value
        )
    ):
        _fail("invalid_destination")
    value = value[:-1] if value.endswith(".") else value
    try:
        value = value.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        _fail("invalid_destination")
    if re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*", value):
        _fail("invalid_destination")
    labels = value.split(".")
    if (
        len(value) > 253
        or len(labels) < 2
        or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in labels
        )
    ):
        _fail("invalid_destination")
    return value


def _network(value):
    if type(value) is not str or len(value) > 80 or "%" in value or "/" not in value:
        _fail()
    try:
        network = ipaddress.ip_network(value)
        if (
            network.version == 6
            and network.prefixlen < 96
            and network.overlaps(ipaddress.ip_network("::ffff:0:0/96"))
        ):
            _fail()
        if network.version == 6 and network.network_address.ipv4_mapped:
            if network.prefixlen < 96:
                _fail()
            network = ipaddress.ip_network(
                (str(network.network_address.ipv4_mapped), network.prefixlen - 96)
            )
        return network
    except ValueError:
        _fail()


def _literal(value):
    try:
        return _ip(value)
    except ValueError:
        return None


def _overlap(a, b):
    ak, av = a["kind"], a["value"]
    bk, bv = b["kind"], b["value"]
    if ak == "cidr" or bk == "cidr":
        if ak != "cidr":
            return _overlap(b, a)
        network = _network(av)
        if bk == "cidr":
            other = _network(bv)
            return network.version == other.version and network.overlaps(other)
        ip = _literal(bv) if bk == "exact" else None
        return ip is not None and ip.version == network.version and ip in network
    if ak == bk == "exact":
        return av == bv
    if ak == "exact":
        return av.endswith("." + bv)
    if bk == "exact":
        return bv.endswith("." + av)
    return av == bv or av.endswith("." + bv) or bv.endswith("." + av)


def validate_registry(value, *, allow_enforced=False):
    """Return a fresh normalized dictionary; errors contain only constant codes.

    Duplicate JSON keys must be caught by load_private_json before this call:
    a Python dictionary cannot retain evidence of overwritten keys.
    """
    _keys(value, ("version", "profiles", "denied_networks"))
    result = {"version": _id(value["version"]), "profiles": [], "denied_networks": []}
    for network in _list(value["denied_networks"], empty=True):
        normalized = str(_network(network))
        if normalized in result["denied_networks"]:
            _fail()
        result["denied_networks"].append(normalized)
    profile_ids = set()
    for profile in _list(value["profiles"], maximum=64):
        _keys(
            profile,
            ("id", "project", "service", "owner", "integration", "mode", "routes"),
        )
        normalized = {
            key: _id(profile[key]) for key in ("id", "project", "service", "owner")
        }
        if normalized["id"] in profile_ids:
            _fail("duplicate_id")
        profile_ids.add(normalized["id"])
        if profile["mode"] == "enforced" and not allow_enforced:
            _fail("enforcement_inventory_required")
        if profile["mode"] not in ("pilot", "enforced") or profile[
            "integration"
        ] not in ("http", "socks", "both"):
            _fail()
        normalized.update(
            mode=profile["mode"], integration=profile["integration"], routes=[]
        )
        route_ids = set()
        for route in _list(profile["routes"], empty=True):
            _keys(route, ("id", "action", "matcher", "ports"), ("upstream_group",))
            rid = _id(route["id"])
            if rid in route_ids:
                _fail("duplicate_id")
            route_ids.add(rid)
            if route["action"] not in ("PROXY", "DIRECT", "DENY"):
                _fail()
            if route["action"] == "PROXY":
                group = _id(route.get("upstream_group"))
            elif "upstream_group" in route:
                _fail()
            _keys(route["matcher"], ("kind", "value"))
            matcher = dict(route["matcher"])
            if matcher["kind"] == "cidr":
                matcher["value"] = str(_network(matcher["value"]))
            elif matcher["kind"] in ("exact", "suffix"):
                matcher["value"] = _host(matcher["value"])
                if matcher["kind"] == "suffix" and _literal(matcher["value"]):
                    _fail()
            else:
                _fail()
            ports = _list(route["ports"], maximum=64)
            if any(
                type(port) is not int or not 1 <= port <= 65535 for port in ports
            ) or len(set(ports)) != len(ports):
                _fail()
            entry = {
                "id": rid,
                "action": route["action"],
                "matcher": matcher,
                "ports": sorted(ports),
            }
            if entry["action"] == "PROXY":
                entry["upstream_group"] = group
            for prior in normalized["routes"]:
                conflict = {prior["action"], entry["action"]} == {"PROXY", "DIRECT"}
                conflict |= (
                    prior["action"] == entry["action"] == "PROXY"
                    and prior["upstream_group"] != entry["upstream_group"]
                )
                if (
                    conflict
                    and set(prior["ports"]) & set(ports)
                    and _overlap(prior["matcher"], matcher)
                ):
                    _fail("route_conflict")
            normalized["routes"].append(entry)
        result["profiles"].append(normalized)
    return result


@dataclass(frozen=True)
class Decision:
    action: str
    route_id: str | None
    reason: str
    upstream_group: str | None = None


def _deny(reason, route_id=None):
    return Decision("DENY", route_id, reason)


def _matches(matcher, host, addresses):
    if matcher["kind"] == "exact":
        literal = _literal(matcher["value"])
        return literal in addresses if literal is not None else matcher["value"] == host
    if matcher["kind"] == "suffix":
        return host.endswith("." + matcher["value"])
    network = _network(matcher["value"])
    return any(ip.version == network.version and ip in network for ip in addresses)


def decide(registry, profile_id, host, port, resolved_ips):
    """Decide using caller-supplied DNS candidates; performs no network I/O.

    Revalidation protects against callers modifying a previously validated dict.
    The caller must supply every candidate; DNS provenance/TTL and rebinding
    resistance require future data-plane integration and are not proven here.
    """
    try:
        registry = validate_registry(registry, allow_enforced=True)
        profile = next((p for p in registry["profiles"] if p["id"] == profile_id), None)
        if profile is None:
            return _deny("unknown_profile")
        host = _host(host)
        if type(port) is not int or not 1 <= port <= 65535:
            return _deny("invalid_destination")
        literal = _literal(host)
        if type(resolved_ips) not in (list, tuple) or len(resolved_ips) > 64:
            return _deny("invalid_destination")
        addresses = [_ip(ip) for ip in resolved_ips]
        if literal:
            if any(ip != literal for ip in addresses):
                return _deny("invalid_destination")
            addresses.append(literal)
        elif not addresses:
            return _deny("dns_required")
        denied = [_network(n) for n in registry["denied_networks"]]
        for ip in addresses:
            if (
                not ip.is_global
                or ip.is_multicast
                or ip.is_reserved
                or any(
                    ip.version == net.version and ip in net
                    for net in (*_SPECIAL, *denied)
                )
            ):
                return _deny("forbidden_address")
        matches = sorted(
            (
                r
                for r in profile["routes"]
                if port in r["ports"] and _matches(r["matcher"], host, addresses)
            ),
            key=lambda r: r["id"],
        )
        explicit_deny = next((r for r in matches if r["action"] == "DENY"), None)
        if explicit_deny:
            return _deny("explicit_deny", explicit_deny["id"])
        if len({(r["action"], r.get("upstream_group")) for r in matches}) > 1:
            return _deny("route_conflict")
        if matches and any(
            not any(_matches(r["matcher"], host, [ip]) for r in matches)
            for ip in addresses
        ):
            return _deny("no_route")
        if matches:
            route = matches[0]
            return Decision(
                route["action"], route["id"], "allowed", route.get("upstream_group")
            )
        return _deny("no_route")
    except (ValueError, TypeError, RecursionError):
        return _deny("invalid_destination")
