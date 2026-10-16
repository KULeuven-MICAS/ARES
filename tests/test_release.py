# Copyright 2026 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
"""Protect release bundles against accidental inclusion of local state."""

import importlib.util
from pathlib import Path
import tempfile
import unittest

PUBLIC_ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "release_check", PUBLIC_ROOT / "tools" / "check_release.py"
)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "README.md").write_text("Public instructions\n")
        (self.root / "RELEASE_FILES.txt").write_text("README.md\nRELEASE_FILES.txt\n")

    def test_clean_source_is_accepted(self):
        self.assertEqual(len(checker.validate(self.root, [])), 2)

    def test_unlisted_runtime_file_is_rejected(self):
        (self.root / "run.jsonl").write_text("{}\n")
        with self.assertRaisesRegex(ValueError, "Unexpected file"):
            checker.validate(self.root, [])

    def test_auth_file_is_rejected_even_when_listed(self):
        (self.root / "auth.json").write_text("{}\n")
        with (self.root / "RELEASE_FILES.txt").open("a") as manifest:
            manifest.write("auth.json\n")
        with self.assertRaisesRegex(ValueError, "Private state"):
            checker.validate(self.root, [])

    def test_symlink_is_rejected(self):
        (self.root / "README.md").unlink()
        (self.root / "outside.txt").write_text("Not a release file\n")
        (self.root / "README.md").symlink_to(self.root / "outside.txt")
        with self.assertRaisesRegex(ValueError, "symlink"):
            checker.validate(self.root, [])

    def test_external_excluded_identifier_is_rejected(self):
        (self.root / "README.md").write_text("Confidential unit identifier\n")
        with self.assertRaisesRegex(ValueError, "excluded term"):
            checker.validate(self.root, ["Confidential"])


    def test_private_rtl_is_rejected_but_the_example_is_accepted(self):
        (self.root / "design.v").write_text("module design; endmodule\n")
        (self.root / "examples").mkdir()
        (self.root / "examples" / "unit.v").write_text("module unit; endmodule\n")
        with (self.root / "RELEASE_FILES.txt").open("a") as manifest:
            manifest.write("examples/unit.v\n")
        with self.assertRaisesRegex(ValueError, "Unexpected file"):
            checker.validate(self.root, [])
        (self.root / "design.v").unlink()
        self.assertEqual(len(checker.validate(self.root, [])), 3)
        with (self.root / "RELEASE_FILES.txt").open("a") as manifest:
            manifest.write("design.v\n")
        (self.root / "design.v").write_text("module design; endmodule\n")
        with self.assertRaisesRegex(ValueError, "only in examples/ and tech/"):
            checker.validate(self.root, [])

if __name__ == "__main__":
    unittest.main()
