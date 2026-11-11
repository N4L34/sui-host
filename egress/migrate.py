"""Generate explicit opt-in selected-client overlays; never edits active files."""

import hashlib
import json


def identity_token(identity):
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def client_overlay(config, identity, command):
    from .compiler import compile_installation

    compile_installation(config)
    from .enforcement import validate_inventory

    inventory = validate_inventory(config["enforcement"])
    identity = next(
        i
        for i in inventory["identities"]
        if i["id"] == identity and i["role"] == "client"
    )
    if not command or not all(type(v) is str and v for v in command):
        raise ValueError("migration_invalid")
    ingress = next(i for i in config["ingress"] if i["profile"] == identity["profile"])
    networks = {
        i["network"]: {"ipv4_address": i["ip"], "mac_address": i["mac"]}
        for i in identity["interfaces"]
    }
    service = {
        "cap_drop": ["ALL"],
        "cap_add": [],
        "security_opt": ["no-new-privileges:true"],
        "sysctls": {"net.ipv6.conf.all.disable_ipv6": "1"},
        "dns": [ingress["dns"]],
        "networks": networks,
        "entrypoint": [
            "python3",
            "/opt/egress/guard.py",
            "--identity",
            identity["id"],
            "--token",
            identity_token(identity),
            "--",
        ],
        "command": command,
        "environment": {
            "SUI_EGRESS_MODE": "required",
            "SUI_EGRESS_HOST": ingress["gateway"],
            "SUI_EGRESS_PORT": "1080",
            "HTTP_PROXY": "http://sui-egress:3128",
            "HTTPS_PROXY": "http://sui-egress:3128",
            "ALL_PROXY": "",
            "NO_PROXY": "localhost,127.0.0.1,::1," + identity["service"],
        },
        "volumes": [
            {
                "type": "bind",
                "source": "/home/sui/sui-host/egress/guard.py",
                "target": "/opt/egress/guard.py",
                "read_only": True,
            },
            {
                "type": "bind",
                "source": "/run/sui-egress-policy",
                "target": "/run/egress-policy",
                "read_only": True,
                "bind": {"create_host_path": False},
            },
        ],
    }
    peers = config.get("internal_peers", {}).get(identity["profile"], [])
    local = ["localhost", "127.0.0.1", "::1", identity["service"]]
    for peer in peers:
        local.extend([peer["ip"], *peer.get("names", [])])
    environment = service["environment"]
    environment["NO_PROXY"] = ",".join(dict.fromkeys(local))
    for upper, lower in [
        ("HTTP_PROXY", "http_proxy"),
        ("HTTPS_PROXY", "https_proxy"),
        ("ALL_PROXY", "all_proxy"),
        ("NO_PROXY", "no_proxy"),
    ]:
        environment[lower] = environment[upper]
    # New tags force an explicit build of the opt-in client code; production
    # base Compose and its existing image/routes remain unchanged.
    if identity["project"] in ("commands", "avtobot"):
        service["image"] = identity["project"] + ":2026.11.10-egress"
    output = {
        "services": {identity["service"]: service},
        "networks": {name: {"external": True, "name": name} for name in networks},
    }
    if identity["project"] in ("commands", "avtobot"):
        output["services"]["proxy"] = {"profiles": ["legacy-egress-disabled"]}
    return output
