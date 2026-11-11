import tempfile
import time
import unittest

from egress.probes import Probe, probe
from lab import certificate, origin, proxy


class ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.ca, cls.tls = certificate(cls.directory.name)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def config(self, address, protocol="http", hostname="origin.test.invalid"):
        return Probe(
            "pilot", "primary", protocol, address[0], address[1], hostname, 443, self.ca
        )

    def test_connect_and_socks_verify_origin_tls_without_local_target_dns(self):
        for protocol in ("http", "socks5"):
            with self.subTest(protocol=protocol), origin(self.tls) as (target, seen):
                with proxy(target, protocol) as (address, upstream):
                    result = probe(self.config(address, protocol))
                self.assertEqual(result.reason, "ok")
                self.assertEqual(seen["requests"], 1)
                self.assertEqual(upstream["requests"], 1)
                self.assertNotIn(b"Proxy-Authorization", seen["headers"][0])
                if protocol == "socks5":
                    self.assertEqual(upstream["destinations"][0][0], 3)

    def test_wrong_certificate_and_denial_are_clean_errors(self):
        with origin(self.tls) as (target, seen):
            with proxy(target) as (address, _):
                self.assertEqual(
                    probe(self.config(address, hostname="wrong.test.invalid")).reason,
                    "origin_tls",
                )
            for protocol in ("http", "socks5"):
                with proxy(target, protocol, reject=True) as (address, _):
                    self.assertEqual(
                        probe(self.config(address, protocol)).reason, "proxy_rejected"
                    )
            self.assertEqual(seen["requests"], 0)

    def test_proxy_auth_never_requests_or_exposes_upstream_credentials(self):
        with origin(self.tls) as (target, seen):
            with proxy(target, credentials=("fake-user", "canary-never-log")) as (
                address,
                _,
            ):
                result = probe(self.config(address))
            self.assertEqual(result.reason, "proxy_auth")
            self.assertNotIn("canary", repr(result))
            self.assertEqual(seen["requests"], 0)

    def test_deadline_is_total_and_no_direct_retry_occurs(self):
        with (
            origin(self.tls) as (target, seen),
            proxy(target, slow=True) as (address, _),
        ):
            start = time.monotonic()
            result = probe(self.config(address), timeout=0.15)
            self.assertEqual(result.reason, "timeout")
            self.assertLess(time.monotonic() - start, 0.6)
            self.assertEqual(seen["requests"], 0)

    def test_invalid_config_is_sanitized(self):
        config = self.config(("user:canary@proxy.invalid", 3128))
        self.assertEqual(probe(config).reason, "config")
        self.assertEqual(probe(self.config(("127.0.0.1", 0))).reason, "config")


if __name__ == "__main__":
    unittest.main()
