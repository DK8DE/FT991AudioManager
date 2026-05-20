"""Tests für Versionsvergleich (GitHub-Update-Check)."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gui.update_check import remote_version_is_newer  # noqa: E402


class UpdateCheckVersionTest(unittest.TestCase):
    def test_remote_newer(self) -> None:
        self.assertTrue(remote_version_is_newer("1.5.4", "1.5.3"))
        self.assertTrue(remote_version_is_newer("1.6.0", "1.5.99"))
        self.assertTrue(remote_version_is_newer("v2.0.0", "1.9.9"))

    def test_same_or_older(self) -> None:
        self.assertFalse(remote_version_is_newer("1.5.3", "1.5.3"))
        self.assertFalse(remote_version_is_newer("1.5.2", "1.5.3"))
        self.assertFalse(remote_version_is_newer("1.4.0", "1.5.0"))

    def test_patch_width(self) -> None:
        self.assertTrue(remote_version_is_newer("1.5.10", "1.5.9"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
