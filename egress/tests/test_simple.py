import base64
import unittest
from egress.service import subscription_config
from egress.manage import client_override

URI = "vless://12345678-1234-4234-8234-123456789abc@vpn.example.org:443?security=reality&type=tcp&sni=www.example.org&fp=chrome&pbk=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA&sid=abcdef12&flow=xtls-rprx-vision"


class SimpleTests(unittest.TestCase):
    def test_subscription_is_closed_and_credentials_stay_in_native_config(self):
        a = subscription_config(base64.b64encode(URI.encode()))
        self.assertEqual(a["outbounds"][0]["protocol"], "vless")
        self.assertEqual([i["port"] for i in a["inbounds"]], [1080, 3128])
        self.assertEqual(a["log"]["loglevel"], "none")
        for bad in [
            b"hello",
            URI.replace("security=reality", "security=none").encode(),
            URI.replace("type=tcp", "type=xhttp").encode(),
        ]:
            with self.assertRaises(ValueError):
                subscription_config(bad)

    def test_clients_get_only_local_proxy_settings(self):
        a = client_override("commands/commands", ["synapse"])
        env = a["services"]["commands"]["environment"]
        self.assertEqual(env["SUI_EGRESS_HOST"], "sui-egress")
        self.assertEqual(env["SUI_EGRESS_MODE"], "required")
        self.assertIn("synapse", env["NO_PROXY"])
        self.assertNotIn("network_mode", a["services"]["commands"])
        self.assertNotIn("entrypoint", a["services"]["commands"])


if __name__ == "__main__":
    unittest.main()


class ApplyTests(unittest.TestCase):
    def test_apply_is_idempotent_and_removal_restarts_only_selected_service(self):
        import tempfile, json
        from pathlib import Path
        from unittest.mock import patch
        from egress import manage

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "egress").mkdir()
            (root / "commands").mkdir()
            manifest = root / "egress/clients.json"
            manifest.write_text(json.dumps({"services": ["commands/commands"]}))
            calls = []

            def run(args, project="egress", timeout=90):
                calls.append((project, args))
                if "config" in args:
                    return json.dumps(
                        {
                            "services": {
                                "commands": {"networks": {"monitoring": {}}},
                                "unrelated": {},
                            }
                        }
                    )
                return ""

            with patch.object(manage, "ROOT", root), patch.object(manage, "run", run):
                manage.enroll()
                first = sum("up" in a for _, a in calls)
                manage.enroll()
                self.assertEqual(first, 1)
                self.assertEqual(sum("up" in a for _, a in calls), 1)
                manifest.write_text('{"services":[]}')
                manage.enroll()
                self.assertFalse((root / "commands/compose.override.yml").exists())
                self.assertEqual(
                    [a[-1] for _, a in calls if "up" in a], ["commands", "commands"]
                )

    def test_existing_unmanaged_override_is_preserved(self):
        import tempfile, json
        from pathlib import Path
        from unittest.mock import patch
        from egress import manage

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "egress").mkdir()
            (root / "commands").mkdir()
            (root / "egress/clients.json").write_text(
                '{"services":["commands/commands"]}'
            )
            original = '{"services":{"commands":{"volumes":["keep:/data"]}}}'
            path = root / "commands/compose.override.yml"
            path.write_text(original)
            with (
                patch.object(manage, "ROOT", root),
                patch.object(
                    manage,
                    "run",
                    return_value='{"services":{"commands":{"networks":{"monitoring":{}}}}}',
                ),
            ):
                with self.assertRaises(ValueError):
                    manage.enroll()
            self.assertEqual(path.read_text(), original)


class DeployTests(unittest.TestCase):
    def test_sync_preserves_private_config_and_managed_client_override(self):
        import tempfile, subprocess, re
        from pathlib import Path

        script = Path("scripts/deploy.sh").read_text()
        block = script.split("managed_excludes=(", 1)[1].split(")", 1)[0]
        excludes = re.findall(r"--exclude='([^']+)'", block)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / "source"
            dest = root / "dest"
            source.mkdir()
            dest.mkdir()
            (source / "compose.yml").write_text("new")
            for name in [
                "compose.override.yml",
                "secrets/subscription.url",
                "runtime/config.json",
            ]:
                p = dest / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("private-canary")
            subprocess.run(
                [
                    "rsync",
                    "-a",
                    "--delete",
                    *[f"--exclude={x}" for x in excludes],
                    str(source) + "/",
                    str(dest) + "/",
                ],
                check=True,
                capture_output=True,
            )
            self.assertEqual((dest / "compose.yml").read_text(), "new")
            for name in [
                "compose.override.yml",
                "secrets/subscription.url",
                "runtime/config.json",
            ]:
                self.assertEqual((dest / name).read_text(), "private-canary")
