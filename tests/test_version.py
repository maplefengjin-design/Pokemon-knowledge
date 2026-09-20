from __future__ import annotations

import sys
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pokemon_knowledge import __version__  # noqa: E402


class VersionTests(unittest.TestCase):
    def test_package_release_and_changelog_versions_match(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertEqual(project["project"]["version"], __version__)
        self.assertIn(f"## [{__version__}]", changelog)


if __name__ == "__main__":
    unittest.main()
