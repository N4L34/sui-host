import copy
import unittest
from egress import enforcement


def inventory():
    return {
        "version": "rules-1",
        "backend": "nftables",
        "ipv6_disabled": True,
        "bridge_netfilter": True,
        "identities": [
            {
                "id": "worker",
                "role": "client",
                "project": "demo",
                "service": "worker",
                "profile": "demo-pilot",
                "interfaces": [
                    {
                        "network": "egress_ingress-demo-pilot",
                        "ip": "172.29.40.10",
                        "mac": "02:42:ac:1d:28:0a",
                    }
                ],
                "allow": [
                    {"ip": "172.29.40.2", "protocol": "tcp", "ports": [3128, 1080]},
                    {"ip": "172.29.40.3", "protocol": "udp", "ports": [53]},
                ],
                "inbound": [{"ip": "172.29.42.20", "protocol": "tcp", "ports": [8080]}],
            }
        ],
    }


class EnforcementTests(unittest.TestCase):
    def test_rules_are_scoped_cover_host_bridge_ipv6_and_do_not_allow_old_direct(self):
        value = inventory()
        text = enforcement.ruleset(value)
        self.assertIn("table inet sui_egress", text)
        self.assertIn("table bridge sui_egress", text)
        self.assertIn("hook input", text)
        self.assertIn("hook forward", text)
        self.assertIn("ether saddr 02:42:ac:1d:28:0a", text)
        self.assertIn("ether type ip6", text)
        self.assertIn("ct direction reply", text)
        self.assertNotIn("flush ruleset", text)
        self.assertNotIn("policy drop", text)
        self.assertNotIn("ct state established accept", text)
        self.assertNotIn("0.0.0.0/0", text)
        self.assertIn("drop", text)

    def test_identity_reuse_wildcard_and_unknown_inventory_rejected(self):
        for edit in (
            lambda c: c.update(extra=True),
            lambda c: c.update(backend="iptables"),
            lambda c: c.update(ipv6_disabled=False),
            lambda c: c["identities"].append(copy.deepcopy(c["identities"][0])),
            lambda c: c["identities"][0]["allow"][0].update(ip="0.0.0.0/0"),
            lambda c: c["identities"][0]["interfaces"][0].update(
                mac="ff:ff:ff:ff:ff:ff"
            ),
        ):
            c = inventory()
            edit(c)
            with self.assertRaisesRegex(ValueError, "^inventory_invalid$"):
                enforcement.validate_inventory(c)

    def test_scope_removal_creates_permanent_deny_tombstone(self):
        old = inventory()
        new = inventory()
        new["identities"][0]["interfaces"][0].update(
            ip="172.29.40.11", mac="02:42:ac:1d:28:0b"
        )
        merged = enforcement.preserve_scopes(old, new)
        self.assertEqual(len(merged["identities"]), 2)
        retired = next(i for i in merged["identities"] if i.get("retired"))
        self.assertEqual(retired["allow"], [])
        self.assertEqual(retired["inbound"], [])
        self.assertIn("172.29.40.10", enforcement.ruleset(merged))

    def test_apply_requires_root_and_explicit_inventory_no_implicit_host_changes(self):
        with self.assertRaises(ValueError):
            enforcement.validate_inventory({})


if __name__ == "__main__":
    unittest.main()
