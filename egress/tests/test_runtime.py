import copy
import json
from pathlib import Path
import unittest

from egress import compiler


def installation():
    registry = json.loads(Path("egress/examples/registry.example.json").read_text())
    return {
        "version": "install-1",
        "registry": registry,
        "uid": 1000,
        "gid": 1000,
        "ingress": [
            {
                "profile": "demo-pilot",
                "subnet": "172.29.40.0/24",
                "gateway": "172.29.40.2",
                "dns": "172.29.40.3",
                "status": "172.29.40.4",
                "sources": ["172.29.40.10"],
            }
        ],
        "control": {
            "subnet": "172.29.41.0/24",
            "gateway": "172.29.41.2",
            "dns": "172.29.41.3",
            "status": "172.29.41.4",
        },
        "monitoring": {
            "gateway": "172.29.42.2",
            "dns": "172.29.42.3",
            "status": "172.29.42.4",
        },
        "uplink": {"subnet": "172.29.43.0/24", "gateway": "172.29.43.2"},
        "upstreams": [
            {
                "id": "node-a",
                "transport": "https",
                "ip": "8.8.8.8",
                "port": 443,
                "server_name": "upstream.example.invalid",
                "username": "test",
                "password": 'CANARY_雪\n:@/"',
                "ca_pem": "",
            }
        ],
        "groups": [{"id": "primary", "members": ["node-a"]}],
        "resolver": {
            "ip": "1.1.1.1",
            "port": 443,
            "server_name": "resolver.example.invalid",
            "path": "/dns-query",
            "group": "primary",
            "ca_pem": "",
        },
        "probe": {"target": "api.example.invalid", "port": 443, "ca_pem": ""},
    }


def enforced_installation():
    c = installation()
    c["registry"]["profiles"][0]["mode"] = "enforced"
    c["monitoring_sources"] = ["172.29.42.20"]
    identities = []
    counter = 0

    def identity(iid, role, project, service, profile, entries, allow, inbound):
        nonlocal counter
        interfaces = []
        for network, ip in entries:
            counter += 1
            interfaces.append(
                {"network": network, "ip": ip, "mac": f"02:00:00:00:00:{counter:02x}"}
            )
        identities.append(
            {
                "id": iid,
                "role": role,
                "project": project,
                "service": service,
                "profile": profile,
                "interfaces": interfaces,
                "allow": allow,
                "inbound": inbound,
            }
        )

    endpoint = lambda ip, protocol, ports: {
        "ip": ip,
        "protocol": protocol,
        "ports": ports,
    }
    ingress = c["ingress"][0]
    identity(
        "worker",
        "client",
        "demo",
        "worker",
        "demo-pilot",
        [("egress_ingress-demo-pilot", ingress["sources"][0])],
        [
            endpoint(ingress["gateway"], "tcp", [3128, 1080]),
            endpoint(ingress["dns"], "tcp", [53]),
            endpoint(ingress["dns"], "udp", [53]),
        ],
        [],
    )
    for component in ("gateway", "dns", "status"):
        entries = [
            ("egress_control", c["control"][component]),
            ("monitoring", c["monitoring"][component]),
            ("egress_ingress-demo-pilot", ingress[component]),
        ]
        if component == "gateway":
            entries.append(("egress_uplink", c["uplink"][component]))
            allow = [
                endpoint("8.8.8.8", "tcp", [443]),
                endpoint(c["control"]["dns"], "udp", [5353]),
            ]
        elif component == "dns":
            allow = [endpoint(c["control"]["gateway"], "tcp", [15353])]
        else:
            allow = [
                endpoint(ingress["gateway"], "tcp", [3128, 1080]),
                endpoint(c["control"]["gateway"], "tcp", [22000, 22001]),
                endpoint(c["control"]["dns"], "udp", [5353]),
            ]
        identity(
            component, component, "egress", component, "system", entries, allow, []
        )
    c["enforcement"] = {
        "version": "rules-1",
        "backend": "nftables",
        "ipv6_disabled": True,
        "bridge_netfilter": True,
        "identities": identities,
    }
    return c


class CompilerTests(unittest.TestCase):
    def test_compiles_closed_default_deny_and_resolved_ip_policy(self):
        bundle = compiler.compile_installation(installation())
        gateway = bundle["gateway"]["singbox"]
        self.assertEqual(gateway["log"], {"level": "error", "timestamp": False})
        self.assertEqual(gateway["route"]["rules"][-1]["action"], "reject")
        self.assertTrue(
            any(
                "100.64.0.0/10" in r.get("ip_cidr", [])
                for r in gateway["route"]["rules"]
            )
        )
        actions = [r["action"] for r in gateway["route"]["rules"]]
        self.assertLess(
            actions.index("resolve"),
            next(
                i
                for i, r in enumerate(gateway["route"]["rules"])
                if r.get("ip_is_private")
            ),
        )
        self.assertFalse(
            any(
                o["type"] == "direct"
                for o in gateway["outbounds"]
                if o["tag"].startswith("group-")
            )
        )
        for component in ("dns", "status", "compose"):
            self.assertNotIn("CANARY", json.dumps(bundle[component]))
        self.assertNotIn("chat_data", json.dumps(bundle["compose"]))
        self.assertEqual(
            bundle["compose"]["services"]["gateway"]["networks"]["ingress-demo-pilot"][
                "ipv4_address"
            ],
            "172.29.40.2",
        )

    def test_rejects_unknown_insecure_and_ambiguous_inputs(self):
        for edit in (
            lambda c: c.update(debug=True),
            lambda c: c["upstreams"][0].update(transport="http"),
            lambda c: c["upstreams"][0].update(insecure=True),
            lambda c: c["upstreams"][0].update(ip="127.0.0.1"),
            lambda c: c["ingress"][0].update(sources=["172.29.40.2"]),
            lambda c: c["control"].update(subnet="172.29.40.0/24"),
            lambda c: c["groups"][0].update(members=["direct"]),
        ):
            config = installation()
            edit(config)
            with self.assertRaisesRegex(ValueError, "^installation_invalid$"):
                compiler.compile_installation(config)

    def test_tls_socks_uses_patched_native_transport_without_remote_gost_auth(self):
        config = installation()
        config["upstreams"][0]["transport"] = "socks5-tls"
        bundle = compiler.compile_installation(config)
        outbound = bundle["gateway"]["singbox"]["outbounds"][1]
        self.assertEqual(outbound["type"], "socks")
        self.assertEqual(outbound["version"], "5")
        self.assertTrue(outbound["tls"]["enabled"])
        self.assertNotIn("CANARY", json.dumps(bundle["gateway"]["adapters"]))
        self.assertTrue(all(a["index"] >= 100 for a in bundle["gateway"]["adapters"]))

    def test_enforced_compiles_with_full_inventory_but_rejects_extra_client_exit(self):
        config = enforced_installation()
        bundle = compiler.compile_installation(config)
        self.assertIn(
            "mac_address",
            bundle["compose"]["services"]["gateway"]["networks"]["control"],
        )
        config["enforcement"]["identities"][0]["allow"].append(
            {"ip": "8.8.8.8", "protocol": "tcp", "ports": [443]}
        )
        with self.assertRaises(ValueError):
            compiler.compile_installation(config)

    def test_enforced_requires_complete_matching_inventory(self):
        config = installation()
        config["registry"]["profiles"][0]["mode"] = "enforced"
        with self.assertRaises(ValueError):
            compiler.compile_installation(config)


if __name__ == "__main__":
    unittest.main()
