"""Tests für model._app_paths."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class AppPathsTest(unittest.TestCase):
    def tearDown(self) -> None:
        import model._app_paths as ap

        ap._legacy_migrated = False

    def test_source_layout_uses_project_data(self) -> None:
        import model._app_paths as ap

        with mock.patch.object(sys, "frozen", False, create=True):
            root = ap.app_data_dir()
        self.assertTrue(str(root).endswith("data") or root.name == "data")
        self.assertTrue(root.is_dir())

    def test_frozen_uses_appdata_on_windows(self) -> None:
        import model._app_paths as ap

        with tempfile.TemporaryDirectory() as tmp:
            appdata = Path(tmp) / "Roaming"
            appdata.mkdir()
            exe = Path(tmp) / "FT991AudioManager.exe"
            exe.write_bytes(b"")

            with (
                mock.patch.object(sys, "frozen", True, create=True),
                mock.patch.object(sys, "executable", str(exe), create=True),
                mock.patch.object(sys, "platform", "win32"),
                mock.patch.dict(os.environ, {"APPDATA": str(appdata)}),
            ):
                root = ap.app_data_dir()

            self.assertEqual(root, appdata / "FT991AudioManager")
            self.assertTrue(root.is_dir())

    def test_migrates_legacy_exe_data_dir(self) -> None:
        import model._app_paths as ap

        ap._legacy_migrated = False
        with tempfile.TemporaryDirectory() as tmp:
            appdata = Path(tmp) / "Roaming"
            appdata.mkdir()
            exe_dir = Path(tmp) / "bin"
            exe_dir.mkdir()
            legacy = exe_dir / "data"
            legacy.mkdir()
            (legacy / "settings.json").write_text('{"cat":{}}', encoding="utf-8")
            exe = exe_dir / "FT991AudioManager.exe"
            exe.write_bytes(b"")

            with (
                mock.patch.object(sys, "frozen", True, create=True),
                mock.patch.object(sys, "executable", str(exe), create=True),
                mock.patch.object(sys, "platform", "win32"),
                mock.patch.dict(os.environ, {"APPDATA": str(appdata)}),
            ):
                root = ap.app_data_dir()

            migrated = root / "settings.json"
            self.assertTrue(migrated.is_file())
            self.assertIn("cat", migrated.read_text(encoding="utf-8"))

    def test_installed_icon_prefers_exe_dir(self) -> None:
        import model._app_paths as ap

        with tempfile.TemporaryDirectory() as tmp:
            exe_dir = Path(tmp) / "bin"
            exe_dir.mkdir()
            ico = exe_dir / "logo.ico"
            ico.write_bytes(b"\x00\x00\x01\x00")
            exe = exe_dir / "FT991AudioManager.exe"
            exe.write_bytes(b"")

            with (
                mock.patch.object(sys, "frozen", True, create=True),
                mock.patch.object(sys, "executable", str(exe), create=True),
            ):
                found = ap.installed_icon_path()

            self.assertEqual(found, ico)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
