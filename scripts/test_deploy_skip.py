"""Exercise the deployment opt-out without accessing a host or Docker."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class DeploymentSkipTests(unittest.TestCase):
    def exercise(self, message):
        source = Path(__file__).with_name("deploy.sh")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            shutil.copy2(source, root / "scripts" / "deploy.sh")
            environment = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull,
                               GIT_CONFIG_NOSYSTEM="1")
            subprocess.run(["git", "init", "-q", str(root)], check=True,
                           env=environment, capture_output=True)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True,
                           env=environment, capture_output=True)
            subprocess.run([
                "git", "-C", str(root), "-c", "user.name=Test",
                "-c", "user.email=test@example.invalid", "-c", "commit.gpgSign=false",
                "-c", "core.hooksPath=/dev/null", "commit", "-qm", message,
            ], check=True, env=environment, capture_output=True)
            blocked = root / "blocked"
            blocked.mkdir()
            calls = root / "calls"
            for command in ("docker", "rsync", "systemctl", "df", "python3"):
                executable = blocked / command
                executable.write_text(
                    '#!/bin/sh\nprintf "%s\\n" "${0##*/}" >> "$DEPLOY_TEST_CALLS"\nexit 91\n'
                )
                executable.chmod(0o755)
            environment.update(PATH=str(blocked) + os.pathsep + environment["PATH"],
                               DEPLOY_TEST_CALLS=str(calls))
            result = subprocess.run(
                ["bash", str(root / "scripts" / "deploy.sh")], env=environment,
                text=True, capture_output=True, timeout=10,
            )
            return result, calls.read_text() if calls.exists() else ""

    def test_marker_skips_before_any_host_operation(self):
        result, calls = self.exercise("Update platform egress\n\n[skip deploy]")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "deployment skipped by commit marker")
        self.assertEqual(calls, "")

    def test_regular_commit_is_not_skipped(self):
        result, _ = self.exercise("Update platform egress")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("deployment skipped", result.stdout)


if __name__ == "__main__":
    unittest.main()
