"""Tests fuer Qt-Multimedia-Backend-Auswahl.

Hintergrund: Der Audio-Recorder schreibt **WAV/PCM** und encoded MP3 in
einem zweiten Schritt selbst per ``lameenc``. Der Windows-Media-Foundation-
Backend kann zwar MP3 encodieren, aber **kein WAV** — er produziert dann
stillschweigend AAC mit ``.wav``-Endung. Der ffmpeg-Backend hingegen
beherrscht WAV-Encoding zuverlaessig. Deshalb ist ``ffmpeg`` die
Default-Wahl auf Windows.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

from audio.qt_media_env import default_qt_media_backend, ensure_qt_media_backend


class QtMediaEnvTest(unittest.TestCase):
    def test_prefers_ffmpeg_when_both_plugins_exist(self) -> None:
        with mock.patch.object(sys, "frozen", False, create=True):
            with mock.patch(
                "audio.qt_media_env._has_plugin",
                side_effect=lambda name: name in ("windows", "ffmpeg"),
            ):
                self.assertEqual(default_qt_media_backend(), "ffmpeg")

    def test_falls_back_to_windows_when_no_ffmpeg_plugin(self) -> None:
        """Ohne ffmpeg-Plugin nutzen wir WMF — Recorder wird zwar einen
        klaren Fehler liefern (WMF kann kein WAV-Encoding), aber der
        Audio-Player funktioniert weiter (MP3-Decoding kann WMF)."""
        with mock.patch.object(sys, "frozen", True, create=True):
            with mock.patch(
                "audio.qt_media_env._has_plugin",
                side_effect=lambda name: name == "windows",
            ):
                self.assertEqual(default_qt_media_backend(), "windows")

    def test_frozen_prefers_ffmpeg_for_wav_encoding(self) -> None:
        """Auch im PyInstaller-Bundle: ffmpeg ist die richtige Wahl, weil
        der Recorder WAV/PCM-Encoding braucht."""
        with mock.patch.object(sys, "frozen", True, create=True):
            with mock.patch(
                "audio.qt_media_env._has_plugin",
                side_effect=lambda name: name in ("windows", "ffmpeg"),
            ):
                self.assertEqual(default_qt_media_backend(), "ffmpeg")

    def test_ensure_respects_explicit_env(self) -> None:
        """Bewusst per Shell gesetzte ``QT_MEDIA_BACKEND`` wird respektiert
        — fuer Power-User-Debugging."""
        env = os.environ.copy()
        try:
            os.environ.clear()
            os.environ.update(env)
            os.environ["QT_MEDIA_BACKEND"] = "windows"
            with mock.patch(
                "audio.qt_media_env.default_qt_media_backend",
                return_value="ffmpeg",
            ):
                self.assertEqual(ensure_qt_media_backend(), "windows")
                self.assertEqual(os.environ.get("QT_MEDIA_BACKEND"), "windows")
        finally:
            os.environ.clear()
            os.environ.update(env)

    def test_ensure_sets_default_when_env_unset(self) -> None:
        env = os.environ.copy()
        try:
            os.environ.clear()
            os.environ.update(env)
            os.environ.pop("QT_MEDIA_BACKEND", None)
            with mock.patch(
                "audio.qt_media_env.default_qt_media_backend",
                return_value="ffmpeg",
            ):
                self.assertEqual(ensure_qt_media_backend(), "ffmpeg")
                self.assertEqual(os.environ.get("QT_MEDIA_BACKEND"), "ffmpeg")
        finally:
            os.environ.clear()
            os.environ.update(env)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
