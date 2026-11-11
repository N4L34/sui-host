"""Integrity and fail-closed invariants for generated private role bundles.

The private digest detects accidental/manual mutation; it is not a signature
against the trusted configuration owner. Never expose it as a metric/version.
"""

import copy
import hashlib
import ipaddress
import json
import re

from .policy import _id, _keys


def seal(value):
    value = copy.deepcopy(value)
    value.pop("integrity", None)
    value["integrity"] = hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
    return value


def validate_bundle(value, role):
    try:
        common = {"version", "integrity"}
        optional = {"guard"}
        fields = {
            "gateway": {
                "singbox",
                "adapters",
                "groups",
                "probes",
                "probe_ca",
                "bootstrap_probes",
                "resolver_ca",
                "telemetry",
            },
            "dns": {"singbox", "adapters"},
            "status": {
                "listen",
                "port",
                "probes",
                "probe_ca",
                "dns_ip",
                "dns_port",
                "dns_target",
                "telemetry_ip",
                "telemetry_port",
            },
        }
        _keys(value, common | fields[role], optional)
        _id(value["version"])
        if value["integrity"] != seal(value)["integrity"]:
            raise ValueError()
        if "guard" in value:
            _keys(value["guard"], ("identity", "token"))
            _id(value["guard"]["identity"])
            if not re.fullmatch("[0-9a-f]{64}", value["guard"]["token"]):
                raise ValueError()
        if role == "status":
            if (
                ipaddress.ip_address(value["listen"]).is_unspecified
                or value["port"] != 8080
                or not 0 < len(value["probes"]) <= 128
            ):
                raise ValueError()
            return copy.deepcopy(value)
        native = value["singbox"]
        if (
            native["log"] != {"level": "error", "timestamp": False}
            or native["route"]["rules"][-1].get("action") != "reject"
            or set(native["route"]["rules"][-1]) - {"action", "method", "no_drop"}
        ):
            raise ValueError()
        for inbound in native["inbounds"]:
            if (
                inbound["type"] not in ("http", "direct")
                or ipaddress.ip_address(inbound["listen"]).is_unspecified
            ):
                raise ValueError()
        outbounds = {o["tag"]: o for o in native["outbounds"]}
        for outbound in native["outbounds"]:
            if outbound["type"] not in ("http", "socks", "direct", "selector"):
                raise ValueError()
            if outbound["type"] == "selector" and (
                not outbound["outbounds"]
                or any(
                    not t.startswith("node-")
                    or outbounds[t]["type"] not in ("http", "socks")
                    for t in outbound["outbounds"]
                )
            ):
                raise ValueError()
            if (
                role == "gateway"
                and outbound["type"] in ("http", "socks")
                and outbound.get("server") != "127.0.0.1"
                and not outbound.get("tls", {}).get("enabled")
            ):
                raise ValueError()
            if outbound["type"] == "socks" and (
                outbound.get("version") != "5"
                or outbound.get("network") != "tcp"
                or not outbound.get("tls", {}).get("enabled")
                or outbound.get("udp_over_tcp")
            ):
                raise ValueError()
            if outbound["type"] in ("http", "socks") and outbound.get("tls"):
                tls = outbound["tls"]
                if (
                    tls.get("enabled") is not True
                    or tls.get("insecure")
                    or not tls.get("server_name")
                ):
                    raise ValueError()
        for rule in native["route"]["rules"]:
            if rule.get("action") not in ("route", "resolve", "reject", "hijack-dns"):
                raise ValueError()
            if rule.get("action") == "route" and rule["outbound"] not in outbounds:
                raise ValueError()
        if not native["dns"].get("disable_cache"):
            raise ValueError()
        if role == "gateway":
            if (
                native["dns"]["final"] != "controlled"
                or native["dns"]["servers"][0]["type"] != "udp"
            ):
                raise ValueError()
            actions = native["route"]["rules"]
            resolve = next(
                i for i, r in enumerate(actions) if r.get("action") == "resolve"
            )
            private = next(i for i, r in enumerate(actions) if r.get("ip_is_private"))
            if private <= resolve or not any(
                r.get("ip_cidr") and r.get("action") == "reject"
                for r in actions[private + 1 :]
            ):
                raise ValueError()
            if native["experimental"]["clash_api"]["external_controller"] not in (
                "127.0.0.1:19090",
                "127.0.0.1:49090",
            ):
                raise ValueError()
        else:
            if (
                value["adapters"]
                or native["dns"]["final"] != "doh"
                or set(outbounds) != {"relay"}
            ):
                raise ValueError()
            doh = native["dns"]["servers"][0]
            if (
                doh["type"] != "https"
                or doh["detour"] != "relay"
                or not doh["tls"].get("enabled")
                or doh["tls"].get("insecure")
            ):
                raise ValueError()
        for adapter in value["adapters"]:
            _keys(adapter, ("index", "config", "ca_pem"))
            config = adapter["config"]
            if config.get("log") != {"output": "none"} or any(
                k in config for k in ("bypass", "bypasses", "api", "metrics")
            ):
                raise ValueError()
            if (
                len(config["chains"]) != 1
                or len(config["chains"][0]["hops"]) != 1
                or len(config["chains"][0]["hops"][0]["nodes"]) != 1
            ):
                raise ValueError()
            chain = config["chains"][0]
            for service in config["services"]:
                if (
                    service["handler"].get("chain") != chain["name"]
                    or service["handler"].get("retries") != 0
                ):
                    raise ValueError()
                if service["handler"]["type"] == "socks5" and service["handler"].get(
                    "metadata"
                ) != {"udp": False, "bind": False}:
                    raise ValueError()
            node = chain["hops"][0]["nodes"][0]
            if any(k in node for k in ("bypass", "bypasses")):
                raise ValueError()
            if adapter["index"] < 100:
                raise ValueError()
            if adapter["index"] < 100 and (
                node["connector"]["type"] != "socks5"
                or node["dialer"]["type"] != "tls"
                or node["dialer"]["tls"].get("secure") is not True
            ):
                raise ValueError()
        return copy.deepcopy(value)
    except Exception:
        raise ValueError("runtime_bundle_invalid") from None
