import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from egress.private_files import write_private_json


class CLITests(unittest.TestCase):
    def invoke(self, command, path):
        return subprocess.run(
            [sys.executable, "-m", "egress", command, str(path)],
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_registry_validation_is_private_offline_and_pilot_only(self):
        example = json.loads(Path("egress/examples/registry.example.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "registry.json"
            write_private_json(path, example)
            result = self.invoke("validate", path)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "registry_valid_pilot")
            example["profiles"][0]["mode"] = "enforced"
            write_private_json(path, example)
            result = self.invoke("validate", path)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stderr.strip(), "validation_failed")

    def test_argument_errors_never_echo_unknown_or_malformed_values(self):
        for args in (
            ["--password", "SYNTHETIC_CLI_CANARY"],
            ["SYNTHETIC_CLI_CANARY", "path"],
        ):
            result = subprocess.run(
                [sys.executable, "-m", "egress", *args], capture_output=True, text=True
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("SYNTHETIC_CLI_CANARY", result.stdout + result.stderr)

    def test_invalid_probe_fields_never_echo_input_or_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "canary-private-path.json"
            write_private_json(path, {"password": "CANARY_secret"})
            result = self.invoke("probe", path)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stderr.strip(), "validation_failed")
            self.assertNotIn("canary", result.stderr)
            self.assertNotIn("CANARY", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
