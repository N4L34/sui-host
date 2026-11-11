import contextlib
import io
import unittest
from unittest.mock import patch
from egress import smoke, guard


class DeploymentGateTests(unittest.TestCase):
    def test_optional_upstream_failure_does_not_block_working_required_path(self):
        probes = [{"required": True}, {"required": False}, {"required": False}]
        self.assertTrue(smoke.required_ready(probes, {0: 2, 1: 0, 2: 2}))
        self.assertFalse(smoke.required_ready(probes, {0: 1, 1: 2, 2: 2}))
        self.assertFalse(smoke.required_ready([{"required": False}], {0: 2}))

    def test_guard_exec_error_never_echoes_private_path(self):
        output = io.StringIO()
        with (
            patch(
                "sys.argv",
                [
                    "guard",
                    "--identity",
                    "worker",
                    "--token",
                    "fixed",
                    "--",
                    "SYNTHETIC_PRIVATE_EXEC",
                ],
            ),
            patch.object(guard, "allowed", return_value=True),
            patch.object(
                guard.os, "execvp", side_effect=OSError("SYNTHETIC_PRIVATE_EXEC")
            ),
            contextlib.redirect_stderr(output),
        ):
            self.assertEqual(guard.main(), 1)
        self.assertEqual(output.getvalue().strip(), "egress_application_exec_failed")


if __name__ == "__main__":
    unittest.main()
