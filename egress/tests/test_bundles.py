import copy
import unittest
from egress.bundles import seal, validate_bundle
from egress.compiler import compile_installation
from egress.tests.test_runtime import installation


class BundleTests(unittest.TestCase):
    def test_generated_roles_validate_but_mutations_are_closed(self):
        bundle = compile_installation(installation())
        for role in ("gateway", "dns", "status"):
            validate_bundle(bundle[role], role)
        for mutate in (
            lambda c: c["singbox"]["route"]["rules"].pop(),
            lambda c: c["singbox"]["log"].update(disabled=False),
            lambda c: c["singbox"]["outbounds"][-1]["outbounds"].append("direct"),
            lambda c: c["adapters"][0]["config"].update(chains=[]),
        ):
            config = copy.deepcopy(bundle["gateway"])
            mutate(config)
            with self.assertRaises(ValueError):
                validate_bundle(config, "gateway")
            with self.assertRaises(ValueError):
                validate_bundle(seal(config), "gateway")

    def test_cold_invalid_config_does_not_launch_native_process(self):
        from egress.daemon import Children
        from unittest.mock import patch

        config = compile_installation(installation())["gateway"]
        config["singbox"]["route"]["rules"].pop()
        with patch("egress.daemon.subprocess.Popen") as spawn:
            with self.assertRaises(ValueError):
                Children(config, "/tmp/unused-fixture").start()
            spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
