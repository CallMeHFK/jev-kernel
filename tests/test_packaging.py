"""Publish hygiene: what the release archive may and may not contain."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
import zipfile
from pathlib import Path, PurePosixPath

REPO = Path(__file__).resolve().parent.parent


def load_builder():
    spec = importlib.util.spec_from_file_location(
        "jev_build_plugin_zip", REPO / "packaging" / "build_plugin_zip.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PackagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.builder = load_builder()

    def test_dev_only_payload_is_excluded(self):
        dev = self.builder._dev_only
        self.assertTrue(dev(PurePosixPath("tests/test_kernel.py")))
        self.assertTrue(dev(PurePosixPath("packaging/build_plugin_zip.py")))
        self.assertTrue(dev(PurePosixPath("backend/__pycache__/judges.pyc")))
        self.assertTrue(dev(PurePosixPath(".github/workflows/ci.yml")))
        self.assertFalse(dev(PurePosixPath("backend/main.py")))
        self.assertFalse(dev(PurePosixPath("jev.example.json")))

    def test_private_config_is_refused(self):
        found = self.builder.find_private_payload(
            [PurePosixPath("jev.json"), PurePosixPath("README.md")]
        )
        self.assertEqual(found, ["jev.json"])
        self.assertEqual(self.builder.find_private_payload([PurePosixPath("jev.example.json")]), [])

    def test_repo_ships_no_real_config(self):
        self.assertFalse((REPO / "jev.json").exists())

    def test_archive_layout_and_manifest(self):
        manifest = json.loads((REPO / "plugin.json").read_text(encoding="utf-8"))
        out = self.builder.build(manifest["id"], manifest["version"])
        try:
            with zipfile.ZipFile(out) as archive:
                names = archive.namelist()
                self.assertEqual({n.split("/", 1)[0] for n in names}, {manifest["id"]})
                self.assertIn(f"{manifest['id']}/plugin.json", names)
                self.assertIn(f"{manifest['id']}/backend/main.py", names)
                self.assertIn(f"{manifest['id']}/jev.example.json", names)
                self.assertNotIn(f"{manifest['id']}/jev.json", names)
                self.assertNotIn(f"{manifest['id']}/tests/test_kernel.py", names)
        finally:
            out.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
