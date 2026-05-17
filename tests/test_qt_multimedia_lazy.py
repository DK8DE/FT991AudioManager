"""Qt Multimedia darf nicht vor QApplication importiert werden."""

from __future__ import annotations

import os
import sys
import unittest


class QtMultimediaLazyTest(unittest.TestCase):
    def test_lazy_load_after_qapplication(self) -> None:
        os.environ["QT_MEDIA_BACKEND"] = "ffmpeg"
        import audio.qt_multimedia_lazy as lazy

        from PySide6.QtWidgets import QApplication

        if QApplication.instance() is None:
            QApplication(sys.argv)
        lazy._mm_types = None
        types = lazy.qt_multimedia_types()
        self.assertIsNotNone(types)
        _QAudioOutput, QMediaDevices, QMediaPlayer = types  # type: ignore[misc]
        player = QMediaPlayer()
        self.assertEqual(
            player.error(),
            QMediaPlayer.Error.NoError,  # type: ignore[attr-defined]
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
