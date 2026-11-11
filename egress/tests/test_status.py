import unittest

from egress.probes import Result
from egress.status import Status


class StatusTests(unittest.TestCase):
    def test_startup_hysteresis_and_freshness(self):
        status = Status("v1", [("pilot", "primary", "http")])
        key = ("pilot", "primary", "http")
        self.assertFalse(status.snapshot(0)["ready"])
        status.record(key, Result("ok", 0.2), 1)
        self.assertFalse(status.snapshot(1)["ready"])
        status.record(key, Result("ok", 0.2), 16)
        self.assertTrue(status.snapshot(16)["ready"])
        for at in (31, 46):
            status.record(key, Result("timeout", 15), at)
            self.assertTrue(status.snapshot(at)["ready"])
        status.record(key, Result("timeout", 15), 61)
        self.assertFalse(status.snapshot(61)["ready"])
        status.record(key, Result("ok", 0.2), 70)
        status.record(key, Result("ok", 0.2), 85)
        self.assertTrue(status.snapshot(85)["ready"])
        self.assertFalse(status.snapshot(146)["ready"])
        status.record(key, Result("ok", 0.2), 147)
        self.assertFalse(status.snapshot(147)["ready"])
        status.record(key, Result("ok", 0.2), 162)
        self.assertTrue(status.snapshot(162)["ready"])

    def test_each_protocol_and_upstream_is_required(self):
        keys = [
            ("pilot", upstream, protocol)
            for upstream in ("primary", "backup")
            for protocol in ("http", "socks5")
        ]
        status = Status("v1", keys)
        for key in keys[:-1]:
            status.record(key, Result("ok", 0.1), 1)
            status.record(key, Result("ok", 0.1), 2)
        self.assertFalse(status.snapshot(2)["ready"])
        status.record(keys[-1], Result("ok", 0.1), 1)
        status.record(keys[-1], Result("ok", 0.1), 2)
        self.assertTrue(status.snapshot(2)["ready"])

    def test_safe_bounded_metrics_and_errors(self):
        key = ("pilot", "primary", "http")
        status = Status("v1", [key])
        with self.assertRaisesRegex(ValueError, "^status_result$"):
            status.record(key, Result("secret@upstream.invalid", 0.1), 1)
        with self.assertRaisesRegex(ValueError, "^status_key$"):
            status.record(
                ("secret@upstream.invalid", "primary", "http"), Result("ok", 0.1), 1
            )
        with self.assertRaisesRegex(ValueError, "^status_config$"):
            Status("secret@upstream.invalid", [key])
        self.assertNotIn("secret", status.metrics(1))
        self.assertIn(
            'egress_probe_ready{profile="pilot",upstream="primary",protocol="http"} 0',
            status.metrics(1),
        )
        self.assertIn("egress_probe_age_seconds", status.metrics(1))
        self.assertFalse(status.snapshot(1)["live"])
        status.tick(1)
        self.assertTrue(status.snapshot(1)["live"])
        self.assertFalse(status.snapshot(62)["live"])

    def test_optional_upstream_diagnostic_does_not_break_effective_path(self):
        effective = ("pilot", "effective", "http")
        primary = ("pilot", "primary", "http")
        status = Status("v1", [effective, primary], required=[effective])
        status.record(effective, Result("ok", 0.1), 1)
        status.record(effective, Result("ok", 0.1), 2)
        status.record(primary, Result("timeout", 15), 2)
        self.assertTrue(status.snapshot(2)["ready"])
        self.assertFalse(status.snapshot(2)["paths"][1]["ready"])


if __name__ == "__main__":
    unittest.main()
