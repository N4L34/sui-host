"""Offline synthetic policy fixtures; never resolves a hostname."""

import copy
import unittest

try:
    from egress.policy import validate_registry, decide
except ImportError:
    validate_registry = decide = None


def example():
    return {
        "version": "fixture-1",
        "denied_networks": ["93.184.216.0/24"],
        "profiles": [
            {
                "id": "pilot",
                "project": "demo",
                "service": "worker",
                "owner": "platform",
                "integration": "both",
                "mode": "pilot",
                "routes": [
                    {
                        "id": "api",
                        "action": "PROXY",
                        "matcher": {"kind": "exact", "value": "api.example.invalid"},
                        "ports": [443],
                        "upstream_group": "primary",
                    },
                    {
                        "id": "subdomains",
                        "action": "DIRECT",
                        "matcher": {"kind": "suffix", "value": "direct.invalid"},
                        "ports": [443],
                    },
                    {
                        "id": "network",
                        "action": "DIRECT",
                        "matcher": {"kind": "cidr", "value": "8.8.8.0/24"},
                        "ports": [53],
                    },
                    {
                        "id": "blocked",
                        "action": "DENY",
                        "matcher": {"kind": "exact", "value": "blocked.invalid"},
                        "ports": [443],
                    },
                ],
            }
        ],
    }


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(
            callable(validate_registry), "policy validation is not implemented"
        )

    def test_explicit_routes_and_unknown_default_deny(self):
        registry = validate_registry(example())
        result = decide(registry, "pilot", "API.Example.INVALID.", 443, ["8.8.8.8"])
        self.assertEqual(
            (result.action, result.route_id, result.upstream_group),
            ("PROXY", "api", "primary"),
        )
        for host, port, ips, action in [
            ("x.direct.invalid", 443, ["8.8.8.8"], "DIRECT"),
            ("direct.invalid", 443, ["8.8.8.8"], "DENY"),
            ("8.8.8.8", 53, [], "DIRECT"),
            ("unlisted.invalid", 443, ["8.8.8.8"], "DENY"),
            ("api.example.invalid", 80, ["8.8.8.8"], "DENY"),
        ]:
            with self.subTest(host=host, port=port):
                self.assertEqual(
                    decide(registry, "pilot", host, port, ips).action, action
                )

    def test_normalizes_idna_and_mapped_ipv6(self):
        raw = example()
        raw["profiles"][0]["routes"][0]["matcher"]["value"] = "BÜCHER.INVALID."
        registry = validate_registry(raw)
        self.assertEqual(
            registry["profiles"][0]["routes"][0]["matcher"]["value"],
            "xn--bcher-kva.invalid",
        )
        self.assertEqual(
            decide(registry, "pilot", "bücher.invalid", 443, ["::ffff:8.8.8.8"]).action,
            "PROXY",
        )
        self.assertEqual(
            decide(registry, "pilot", "::ffff:8.8.8.8", 53, []).action, "DIRECT"
        )
        self.assertEqual(
            raw["profiles"][0]["routes"][0]["matcher"]["value"], "BÜCHER.INVALID."
        )

    def test_any_unsafe_address_and_missing_dns_deny(self):
        registry = validate_registry(example())
        addresses = [
            "127.0.0.1",
            "10.1.2.3",
            "169.254.169.254",
            "100.64.0.1",
            "192.0.2.1",
            "224.0.0.1",
            "0.0.0.0",
            "255.255.255.255",
            "::1",
            "fe80::1",
            "fd00::1",
            "2001:db8::1",
            "ff02::1",
            "::ffff:127.0.0.1",
            "93.184.216.10",
            "64:ff9b::808:808",
            "2002:808:808::1",
        ]
        for ip in addresses:
            with self.subTest(ip=ip):
                self.assertEqual(
                    decide(
                        registry, "pilot", "api.example.invalid", 443, ["8.8.8.8", ip]
                    ).action,
                    "DENY",
                )
        for ips in ([], None, ["bad"], "8.8.8.8"):
            self.assertEqual(
                decide(registry, "pilot", "api.example.invalid", 443, ips).action,
                "DENY",
            )

    def test_malformed_alternative_ip_and_unknown_profile_deny(self):
        registry = validate_registry(example())
        for host in [
            "127.1",
            "2130706433",
            "0177.0.0.1",
            "0x7f000001",
            "0x7f.1",
            "[::1]",
            "fe80::1%eth0",
            "https://api.example.invalid",
            "bad..invalid",
            " api.example.invalid",
            "api.example.invalid..",
            "api.example.invalid/path",
            "localhost",
            None,
            4,
        ]:
            with self.subTest(host=host):
                self.assertEqual(
                    decide(registry, "pilot", host, 443, ["8.8.8.8"]).action, "DENY"
                )
        self.assertEqual(
            decide(registry, "missing", "api.example.invalid", 443, ["8.8.8.8"]).action,
            "DENY",
        )
        self.assertEqual(
            decide(registry, "pilot", "api.example.invalid", True, ["8.8.8.8"]).action,
            "DENY",
        )

    def test_closed_schema_types_and_bounds(self):
        mutations = [
            lambda x: x.update(extra=True),
            lambda x: x.update(version=1),
            lambda x: x.update(version="x" * 65),
            lambda x: x.update(denied_networks=["garbage"]),
            lambda x: x.update(profiles=[]),
            lambda x: x["profiles"][0].update(mode="enforced"),
            lambda x: x["profiles"][0].update(integration="udp"),
            lambda x: x["profiles"][0].update(owner="private@example.invalid"),
            lambda x: x["profiles"][0].update(extra=True),
            lambda x: x["profiles"][0]["routes"][0].update(ports=[True]),
            lambda x: x["profiles"][0]["routes"][0].update(ports=[0]),
            lambda x: x["profiles"][0]["routes"][0].update(ports=[65536]),
            lambda x: x["profiles"][0]["routes"][0].update(ports=[443, 443]),
            lambda x: x["profiles"][0]["routes"][0].update(protocol="udp"),
            lambda x: x["profiles"][0]["routes"][0].pop("upstream_group"),
            lambda x: x["profiles"][0]["routes"][1].update(upstream_group="primary"),
            lambda x: x["profiles"][0]["routes"][0]["matcher"].update(extra=True),
        ]
        for mutate in mutations:
            raw = example()
            mutate(raw)
            with self.subTest(mutation=mutations.index(mutate)):
                with self.assertRaisesRegex(ValueError, "^[a-z_]+$"):
                    validate_registry(raw)

    def test_duplicate_ids_and_route_conflicts(self):
        pairs = [
            (
                "exact",
                "API.EXAMPLE.INVALID.",
                "PROXY",
                "exact",
                "api.example.invalid",
                "DIRECT",
            ),
            (
                "suffix",
                "example.invalid",
                "PROXY",
                "exact",
                "api.example.invalid",
                "DIRECT",
            ),
            (
                "suffix",
                "example.invalid",
                "PROXY",
                "suffix",
                "nested.example.invalid",
                "DIRECT",
            ),
            ("cidr", "8.8.8.0/24", "PROXY", "cidr", "8.8.8.8/32", "DIRECT"),
            ("exact", "8.8.8.8", "PROXY", "cidr", "8.8.8.0/24", "DIRECT"),
        ]
        for k1, v1, a1, k2, v2, a2 in pairs:
            raw = example()
            routes = []
            for idx, (kind, value, action) in enumerate([(k1, v1, a1), (k2, v2, a2)]):
                route = {
                    "id": "r" + str(idx),
                    "action": action,
                    "matcher": {"kind": kind, "value": value},
                    "ports": [443],
                }
                if action == "PROXY":
                    route["upstream_group"] = "primary"
                routes.append(route)
            raw["profiles"][0]["routes"] = routes
            with self.subTest(pair=(k1, k2)):
                with self.assertRaisesRegex(ValueError, "^route_conflict$"):
                    validate_registry(raw)
            routes[1]["ports"] = [80]
            validate_registry(raw)
        raw = example()
        raw["profiles"].append(copy.deepcopy(raw["profiles"][0]))
        with self.assertRaises(ValueError):
            validate_registry(raw)
        raw = example()
        raw["profiles"][0]["routes"].append(
            copy.deepcopy(raw["profiles"][0]["routes"][0])
        )
        with self.assertRaises(ValueError):
            validate_registry(raw)

    def test_deny_precedes_allow_and_cidr_cannot_bypass_proxy(self):
        raw = example()
        raw["profiles"][0]["routes"].append(
            {
                "id": "block-sub",
                "action": "DENY",
                "matcher": {"kind": "suffix", "value": "example.invalid"},
                "ports": [443],
            }
        )
        result = decide(
            validate_registry(raw), "pilot", "api.example.invalid", 443, ["8.8.8.8"]
        )
        self.assertEqual((result.action, result.route_id), ("DENY", "block-sub"))
        raw = example()
        raw["profiles"][0]["routes"][2]["ports"] = [443]
        result = decide(
            validate_registry(raw), "pilot", "api.example.invalid", 443, ["8.8.8.8"]
        )
        self.assertEqual(result.action, "DENY")


class PolicyEdgeTests(unittest.TestCase):
    def test_normalized_numeric_alternatives_and_mapped_networks(self):
        for host in ("１２７.１", "０x７f０００００１"):
            raw = example()
            raw["profiles"][0]["routes"][0]["matcher"]["value"] = host
            with self.subTest(host=host):
                with self.assertRaises(ValueError):
                    validate_registry(raw)
        raw = example()
        raw["denied_networks"] = ["::ffff:8.8.8.0/120"]
        registry = validate_registry(raw)
        self.assertEqual(registry["denied_networks"], ["8.8.8.0/24"])
        self.assertEqual(
            decide(registry, "pilot", "api.example.invalid", 443, ["8.8.8.8"]).action,
            "DENY",
        )
        raw = example()
        raw["denied_networks"] = ["::/80"]
        with self.assertRaises(ValueError):
            validate_registry(raw)

    def test_overlapping_proxy_groups_are_rejected(self):
        raw = example()
        route = copy.deepcopy(raw["profiles"][0]["routes"][0])
        route.update(id="other", upstream_group="other")
        raw["profiles"][0]["routes"].append(route)
        with self.assertRaisesRegex(ValueError, "^route_conflict$"):
            validate_registry(raw)


class CandidateAuthorizationTests(unittest.TestCase):
    def test_literal_cannot_borrow_unrelated_resolved_address(self):
        registry = validate_registry(example())
        self.assertEqual(
            decide(registry, "pilot", "9.9.9.9", 53, ["8.8.8.8"]).action, "DENY"
        )

    def test_cidr_allow_must_cover_every_candidate(self):
        registry = validate_registry(example())
        self.assertEqual(
            decide(
                registry, "pilot", "dns.example.invalid", 53, ["8.8.8.8", "9.9.9.9"]
            ).action,
            "DENY",
        )
        self.assertEqual(
            decide(
                registry, "pilot", "dns.example.invalid", 53, ["8.8.8.8", "8.8.8.9"]
            ).action,
            "DIRECT",
        )


class SpecialUseTests(unittest.TestCase):
    def test_global_flag_does_not_authorize_special_purpose_allocations(self):
        registry = validate_registry(example())
        for ip in (
            "192.0.0.9",
            "192.0.0.10",
            "192.88.99.1",
            "192.175.48.1",
            "192.31.196.1",
            "192.52.193.1",
            "2620:4f:8000::1",
            "100:0:0:1::1",
            "fec0::1",
            "4000::1",
        ):
            with self.subTest(ip=ip):
                self.assertEqual(
                    decide(registry, "pilot", "api.example.invalid", 443, [ip]).action,
                    "DENY",
                )


class ExactAddressCandidateTests(unittest.TestCase):
    def add_address_route(self, raw, action, address="8.8.8.8"):
        route = {
            "id": "address",
            "action": action,
            "matcher": {"kind": "exact", "value": address},
            "ports": [443],
        }
        if action == "PROXY":
            route["upstream_group"] = "primary"
        raw["profiles"][0]["routes"].append(route)

    def test_exact_ip_deny_applies_to_every_dns_candidate(self):
        raw = example()
        self.add_address_route(raw, "DENY")
        for ips in (["8.8.8.8"], ["9.9.9.9", "::ffff:8.8.8.8"]):
            result = decide(
                validate_registry(raw), "pilot", "api.example.invalid", 443, ips
            )
            self.assertEqual((result.action, result.route_id), ("DENY", "address"))

    def test_domain_proxy_and_exact_ip_direct_collision_denies(self):
        raw = example()
        self.add_address_route(raw, "DIRECT")
        result = decide(
            validate_registry(raw), "pilot", "api.example.invalid", 443, ["8.8.8.8"]
        )
        self.assertEqual((result.action, result.reason), ("DENY", "route_conflict"))

    def test_exact_ip_allow_does_not_authorize_other_dns_candidates(self):
        raw = example()
        self.add_address_route(raw, "DIRECT")
        registry = validate_registry(raw)
        self.assertEqual(
            decide(
                registry, "pilot", "address.example.invalid", 443, ["::ffff:8.8.8.8"]
            ).action,
            "DIRECT",
        )
        self.assertEqual(
            decide(
                registry,
                "pilot",
                "address.example.invalid",
                443,
                ["8.8.8.8", "9.9.9.9"],
            ).action,
            "DENY",
        )


if __name__ == "__main__":
    unittest.main()
