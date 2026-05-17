"""Tests fuer Qt-Multimedia-Backend-Auswahl."""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

from audio.qt_media_env import default_qt_media_backend, ensure_qt_media_backend


class QtMediaEnvTest(unittest.TestCase):
    def test_dev_windows_when_both_plugins_exist(self) -> None:
        with mock.patch.object(sys, "frozen", False, create=True):
            with mock.patch(
                "audio.qt_media_env._has_plugin",
                side_effect=lambda name: name in ("windows", "ffmpeg"),
            ):
                self.assertEqual(default_qt_media_backend(), "windows")

    def test_frozen_prefers_windows_for_mp3_encoding(self) -> None:
        """Im PyInstaller-Bundle muss WMF gewaehlt werden — das
        gebuendelte FFmpeg kann unter Windows MP3 nicht encodieren."""
        with mock.patch.object(sys, "frozen", True, create=True):
            with mock.patch(
                "audio.qt_media_env._has_plugin",
                side_effect=lambda name: name in ("windows", "ffmpeg"),
            ):
                self.assertEqual(default_qt_media_backend(), "windows")

    def test_frozen_falls_back_to_ffmpeg_when_no_windows_plugin(self) -> None:
        with mock.patch.object(sys, "frozen", True, create=True):
            with mock.patch(
                "audio.qt_media_env._has_plugin",
                side_effect=lambda name: name == "ffmpeg",
            ):
                self.assertEqual(default_qt_media_backend(), "ffmpeg")

    def test_ensure_overrides_ffmpeg_in_dev(self) -> None:
        env = os.environ.copy()
        try:
            os.environ.clear()
            os.environ.update(env)
            os.environ["QT_MEDIA_BACKEND"] = "ffmpeg"
            with mock.patch.object(sys, "frozen", False, create=True):
                with mock.patch(
                    "audio.qt_media_env.default_qt_media_backend",
                    return_value="windows",
                ):
                    self.assertEqual(ensure_qt_media_backend(), "windows")
                    self.assertEqual(os.environ.get("QT_MEDIA_BACKEND"), "windows")
        finally:
            os.environ.clear()
            os.environ.update(env)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
