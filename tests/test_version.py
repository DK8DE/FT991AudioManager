"""Tests für zentrale Versionsmetadaten."""

from __future__ import annotations

import unittest

from version import APP_DATE, APP_NAME, APP_VERSION


class VersionModuleTest(unittest.TestCase):
    def test_app_version_is_semver_like(self) -> None:
        parts = APP_VERSION.split(".")
        self.assertGreaterEqual(len(parts), 2)
        for part in parts:
            self.assertTrue(part.isdigit(), f"Ungültiges Versionssegment: {part!r}")

    def test_metadata_present(self) -> None:
        self.assertIn("FT-991", APP_NAME)
        self.assertIn("2026", APP_DATE)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
