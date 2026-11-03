#!/usr/bin/env python3
"""Tests that runtime configuration is moved without overwriting host state."""

from pathlib import Path
import tempfile
import unittest

from migrate_runtime_env import migrate


class RuntimeEnvMigrationTest(unittest.TestCase):
    def test_moves_legacy_file_with_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "repo/commands/runtime.env"
            target = root / "host/commands/runtime.env"
            source.parent.mkdir(parents=True)
            source.write_text("MATRIX_SERVER_NAME=matrix.example.com\n", encoding="utf-8")

            migrate(source, target)

            self.assertTrue(source.is_symlink())
            self.assertEqual(source.resolve(), target.resolve())
            self.assertEqual(target.read_text(encoding="utf-8"), "MATRIX_SERVER_NAME=matrix.example.com\n")
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_keeps_existing_host_file_and_removes_legacy_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "repo/runtime.env"
            target = root / "host/runtime.env"
            source.parent.mkdir(parents=True)
            target.parent.mkdir(parents=True)
            source.write_text("legacy\n", encoding="utf-8")
            target.write_text("host-owned\n", encoding="utf-8")

            migrate(source, target)

            self.assertTrue(source.is_symlink())
            self.assertEqual(source.resolve(), target.resolve())
            self.assertEqual(target.read_text(encoding="utf-8"), "host-owned\n")


if __name__ == "__main__":
    unittest.main()
