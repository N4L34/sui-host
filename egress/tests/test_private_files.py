"""Private JSON tests use artificial values in disposable local directories."""

import contextlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

try:
    from egress.private_files import load_private_json, write_private_json
except ImportError:
    load_private_json = write_private_json = None
from egress.policy import validate_registry
from test_policy import example


class PrivateFilesTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(load_private_json), "private JSON is not implemented")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # macOS /var is a symlink; use its physical spelling in fixtures.
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / "private.json"

    def put(self, content, mode=0o600):
        self.path.write_bytes(content)
        self.path.chmod(mode)

    def test_unicode_newline_roundtrip_private_modes_and_no_stdout(self):
        data = {"password": 'ARTIFICIAL_пароль\n"\\雪', "n": 4}
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            write_private_json(self.root / "new" / "private.json", data)
            self.assertEqual(
                load_private_json(self.root / "new" / "private.json"), data
            )
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(stat.S_IMODE((self.root / "new").stat().st_mode), 0o700)
        self.assertEqual(
            stat.S_IMODE((self.root / "new" / "private.json").stat().st_mode), 0o600
        )

    def test_duplicate_keys_bad_json_size_and_constant_errors(self):
        for content in [
            b'{"CANARY":1,"CANARY":2}',
            b'{"CANARY":',
            b"NaN",
            b"Infinity",
            b"\xff",
        ]:
            self.put(content)
            with self.subTest(content_class=len(content)):
                with self.assertRaisesRegex(ValueError, "^[a-z_]+$") as error:
                    load_private_json(self.path)
                self.assertNotIn("CANARY", str(error.exception))
        self.put(b'{"padding":"1234567890"}')
        with self.assertRaisesRegex(ValueError, "^file_too_large$"):
            load_private_json(self.path, max_bytes=8)

    def test_rejects_symlink_file_directory_and_permissive_modes(self):
        self.put(b"{}")
        alias = self.root / "alias.json"
        alias.symlink_to(self.path)
        for action in (
            lambda: load_private_json(alias),
            lambda: write_private_json(alias, {}),
        ):
            with self.assertRaisesRegex(ValueError, "^[a-z_]+$"):
                action()
        link = self.root / "link"
        link.symlink_to(self.root, target_is_directory=True)
        for action in (
            lambda: load_private_json(link / "private.json"),
            lambda: write_private_json(link / "other.json", {}),
        ):
            with self.assertRaisesRegex(ValueError, "^[a-z_]+$"):
                action()
        self.path.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "^unsafe_permissions$"):
            load_private_json(self.path)
        self.path.chmod(0o600)
        self.root.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "^unsafe_permissions$"):
            load_private_json(self.path)
        with self.assertRaisesRegex(ValueError, "^unsafe_permissions$"):
            write_private_json(self.path, {})
        self.root.chmod(0o700)

    def test_validator_and_serialization_failure_preserve_existing_file(self):
        original = b'{"old":true}\n'
        self.put(original)
        invalid = example()
        invalid["profiles"][0]["mode"] = "enforced"
        for data, options in [
            (invalid, {"validator": validate_registry}),
            ({"canary": object()}, {}),
            ({"too_big": "x" * 50}, {"max_bytes": 8}),
            ({"bad": float("nan")}, {}),
        ]:
            with self.assertRaisesRegex(ValueError, "^[a-z_]+$"):
                write_private_json(self.path, data, **options)
            self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["private.json"])

    def test_load_validates_and_write_replaces_atomically(self):
        write_private_json(self.path, example(), validator=validate_registry)
        self.assertEqual(
            load_private_json(self.path, validator=validate_registry)["version"],
            "fixture-1",
        )
        invalid = example()
        invalid["extra"] = "CANARY"
        self.put(json.dumps(invalid).encode())
        with self.assertRaisesRegex(ValueError, "^validation_failed$"):
            load_private_json(self.path, validator=validate_registry)
        write_private_json(self.path, {"replacement": True})
        self.assertEqual(load_private_json(self.path), {"replacement": True})

    def test_rejects_non_regular_files_without_blocking(self):
        os.mkfifo(self.path, 0o600)
        with self.assertRaisesRegex(ValueError, "^[a-z_]+$"):
            load_private_json(self.path)
        with self.assertRaisesRegex(ValueError, "^[a-z_]+$"):
            write_private_json(self.path, {})


class PrivateErrorTests(unittest.TestCase):
    def test_invalid_paths_and_bad_validator_have_safe_errors(self):
        class InvalidPath:
            def __fspath__(self):
                raise ValueError("ARTIFICIAL_CANARY")

        for path in (None, InvalidPath()):
            for function, args in (
                (load_private_json, ()),
                (write_private_json, ({},)),
            ):
                with self.subTest(
                    function=function.__name__, path_type=type(path).__name__
                ):
                    with self.assertRaisesRegex(ValueError, "^[a-z_]+$") as error:
                        function(path, *args)
                    self.assertNotIn("CANARY", str(error.exception))

        def validator(_data):
            raise RuntimeError("ARTIFICIAL_CANARY")

        with self.assertRaisesRegex(ValueError, "^validation_failed$"):
            write_private_json("/unused", {}, validator=validator)


class PrivateSerializationTests(unittest.TestCase):
    def test_arbitrary_serialization_exceptions_are_sanitized_and_preserve_target(self):
        class BrokenDict(dict):
            def items(self):
                raise RuntimeError("ARTIFICIAL_SERIALIZER_CANARY")

        class BrokenList(list):
            def __iter__(self):
                raise RuntimeError("ARTIFICIAL_SERIALIZER_CANARY")

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp).resolve() / "private.json"
            original = b'{"old":true}\n'
            path.write_bytes(original)
            path.chmod(0o600)
            cases = [
                (BrokenDict(value=1), None),
                (BrokenList([1]), None),
                ({}, lambda _: BrokenDict(value=1)),
            ]
            for data, validator in cases:
                with self.subTest(
                    data_type=type(data).__name__, validator=validator is not None
                ):
                    sanitized = False
                    try:
                        write_private_json(path, data, validator=validator)
                    except Exception as error:
                        sanitized = (
                            type(error) is ValueError
                            and str(error) == "serialization_failed"
                        )
                    self.assertTrue(sanitized, "serialization error is not sanitized")
                    self.assertEqual(path.read_bytes(), original)
                    self.assertEqual(
                        [p.name for p in path.parent.iterdir()], ["private.json"]
                    )


if __name__ == "__main__":
    unittest.main()
