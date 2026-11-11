import importlib.util
from pathlib import Path
import subprocess
import socket
import threading
import time
import sys
import unittest
from unittest.mock import patch


class ClientModeTests(unittest.TestCase):
    def module(self, path):
        spec = importlib.util.spec_from_file_location("client_fixture", path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def test_required_mode_dials_only_gateway_and_closes_on_failure(self):
        for path in (
            "commands/app/scripts/egress_client.py",
            "avtobot/egress_client.py",
        ):
            m = self.module(path)
            with patch.object(
                m.socket, "create_connection", side_effect=OSError("fixture")
            ) as connect:
                with self.assertRaises(OSError):
                    m.open_required(
                        {
                            "SUI_EGRESS_MODE": "required",
                            "SUI_EGRESS_HOST": "172.29.40.2",
                            "SUI_EGRESS_PORT": "1080",
                        },
                        "api.example.invalid",
                        443,
                    )
                self.assertEqual(connect.call_count, 1)
                self.assertEqual(connect.call_args.args[0], ("172.29.40.2", 1080))

    def test_unknown_mode_and_credentials_fail_before_connect(self):
        m = self.module("avtobot/egress_client.py")
        for config in (
            {"SUI_EGRESS_MODE": "typo"},
            {"SUI_EGRESS_MODE": "required", "SUI_EGRESS_HOST": "user:pass@host"},
        ):
            with patch.object(m.socket, "create_connection") as connect:
                with self.assertRaises(ValueError):
                    m.open_required(config, "api.example.invalid", 443)
                connect.assert_not_called()

    def test_slow_drip_uses_one_total_handshake_deadline(self):
        for path in (
            "commands/app/scripts/egress_client.py",
            "avtobot/egress_client.py",
        ):
            m = self.module(path)
            with socket.socket() as server:
                server.bind(("127.0.0.1", 0))
                server.listen()

                def slow():
                    conn, _ = server.accept()
                    with conn:
                        conn.recv(3)
                        conn.sendall(b"\x05")
                        time.sleep(0.3)

                worker = threading.Thread(target=slow, daemon=True)
                worker.start()
                start = time.monotonic()
                with self.assertRaises((TimeoutError, OSError)):
                    m.open_required(
                        {
                            "SUI_EGRESS_MODE": "required",
                            "SUI_EGRESS_HOST": "127.0.0.1",
                            "SUI_EGRESS_PORT": str(server.getsockname()[1]),
                        },
                        "api.example.invalid",
                        443,
                        timeout=0.15,
                    )
                self.assertLess(time.monotonic() - start, 0.3)
                worker.join(1)

    def test_overlay_preserves_external_image_and_explicit_internal_peers(self):
        from egress.tests.test_runtime import enforced_installation
        from egress.migrate import client_overlay

        c = enforced_installation()
        c["internal_peers"] = {
            "demo-pilot": [
                {
                    "ip": "172.29.42.30",
                    "protocol": "tcp",
                    "ports": [8008],
                    "names": ["synapse"],
                }
            ]
        }
        overlay = client_overlay(
            c, "worker", ["/usr/local/bin/original-entrypoint", "gateway", "run"]
        )
        service = overlay["services"]["worker"]
        self.assertNotIn("image", service)
        self.assertEqual(
            service["environment"]["http_proxy"], service["environment"]["HTTP_PROXY"]
        )
        self.assertEqual(
            service["environment"]["no_proxy"], service["environment"]["NO_PROXY"]
        )
        self.assertIn("synapse", service["environment"]["NO_PROXY"])
        self.assertEqual(service["environment"]["all_proxy"], "")

    def test_boot_guard_never_executes_without_root_boot_marker(self):
        result = subprocess.run(
            [
                sys.executable,
                "egress/guard.py",
                "--identity",
                "worker",
                "--token",
                "test",
                "--",
                sys.executable,
                "-c",
                'print("executed")',
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("executed", result.stdout)


if __name__ == "__main__":
    unittest.main()
