"""Release notes: Keep a Changelog headings, sub-headings, and the legacy form."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load_release_notes():
    spec = importlib.util.spec_from_file_location(
        "jev_release_notes", REPO / "packaging" / "release_notes.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHANGELOG = """# Changelog

## [0.2.4] - 2026-10-28

### Fixed

- report reads the rotated ledger

### Added

- governance files

## [0.2.3] - 2026-10-27

- older entry
"""


class ReleaseNotesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.notes = load_release_notes()

    def extract(self, text: str, tag: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CHANGELOG.md"
            path.write_text(text, encoding="utf-8")
            return self.notes.extract(path, tag)

    def test_subheadings_stay_in_the_section(self):
        text, found = self.extract(CHANGELOG, "v0.2.4")
        self.assertTrue(found)
        self.assertIn("### Fixed", text)
        self.assertIn("### Added", text)
        self.assertIn("governance files", text)
        self.assertNotIn("older entry", text)

    def test_legacy_v_heading_still_works(self):
        legacy = "# Changelog\n\n## v0.2.3\n\n- legacy entry\n\n## v0.2.2\n\n- next\n"
        text, found = self.extract(legacy, "v0.2.3")
        self.assertTrue(found)
        self.assertIn("legacy entry", text)
        self.assertNotIn("next", text)

    def test_tag_without_v_prefix_matches(self):
        text, found = self.extract(CHANGELOG, "0.2.4")
        self.assertTrue(found)
        self.assertIn("governance files", text)

    def test_missing_section_falls_back(self):
        text, found = self.extract(CHANGELOG, "v9.9.9")
        self.assertFalse(found)
        self.assertEqual(text, "See `CHANGELOG.md` for v9.9.9.")

    def test_repo_ships_changelog_at_the_root(self):
        self.assertTrue((REPO / "CHANGELOG.md").is_file())


if __name__ == "__main__":
    unittest.main()