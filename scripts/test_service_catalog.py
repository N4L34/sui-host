#!/usr/bin/env python3
"""Regression tests for service discovery and changed-path mapping."""

from pathlib import Path
import tempfile
import unittest

from service_catalog import changed_services, discover_services


class ServiceCatalogTest(unittest.TestCase):
    def test_discovers_standard_and_descriptor_projects_without_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "systemd").mkdir()
            for name in ("api", "monitoring", "runner"):
                project = root / name
                project.mkdir()
                (project / "compose.yml").write_text("name: test\n", encoding="utf-8")
            (root / "systemd/api.service").touch()
            (root / "systemd/monitoring.service").touch()
            chat = root / "chat/deploy"
            chat.mkdir(parents=True)
            (chat / "project.json").write_text(
                '{"compose_files":["deploy/compose.yml"]}\n', encoding="utf-8"
            )
            names = [service["name"] for service in discover_services(root)]
            self.assertEqual(names, ["api", "chat", "monitoring"])

    def test_maps_changed_files_to_only_their_service_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "systemd").mkdir()
            for name in ("api", "billing"):
                (root / name).mkdir()
                (root / name / "compose.yml").touch()
                (root / "systemd" / f"{name}.service").touch()
            projects = changed_services(
                root,
                ["api/compose.yml", "docs/standard.md", "billing/app.py"],
            )
            self.assertEqual([service["name"] for service in projects], ["api", "billing"])


if __name__ == "__main__":
    unittest.main()
