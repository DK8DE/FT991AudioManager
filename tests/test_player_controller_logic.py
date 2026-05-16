"""Tests für PlayerController-Zustandslogik (ohne echtes Audio)."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication

from audio.player_controller import PlayerController, PlayerState

_app = QApplication.instance() or QApplication([])


class _FakeCat:
    def __init__(self, connected: bool = True) -> None:
        self._connected = connected

    def is_connected(self) -> bool:
        return self._connected


class PlayerControllerLogicTest(unittest.TestCase):
    def test_play_without_multimedia_emits_error(self) -> None:
        with patch("audio.player_controller._MULTIMEDIA_IMPORT", False):
            with patch("audio.player_controller._MULTIMEDIA_AVAILABLE", False):
                cat = _FakeCat()
                ctrl = PlayerController(cat)  # type: ignore[arg-type]
                try:
                    errors: list[str] = []
                    ctrl.error.connect(errors.append)
                    ctrl.set_playlist([Path("a.mp3")])
                    ctrl.play()
                    self.assertTrue(errors)
                    self.assertEqual(ctrl.state, PlayerState.IDLE)
                finally:
                    ctrl.shutdown()

    def test_play_without_cat_emits_error(self) -> None:
        cat = _FakeCat(connected=False)
        mock_player = MagicMock()
        mock_player.error.return_value = 0  # NoError
        with patch("audio.player_controller._MULTIMEDIA_IMPORT", True):
            with patch("audio.player_controller._MULTIMEDIA_AVAILABLE", True):
                with patch("audio.player_controller.QMediaPlayer", return_value=mock_player):
                    with patch("audio.player_controller.QAudioOutput"):
                        with patch(
                            "audio.player_controller._player_backend_ok",
                            return_value=True,
                        ):
                            ctrl = PlayerController(cat)  # type: ignore[arg-type]
                            try:
                                errors: list[str] = []
                                ctrl.error.connect(errors.append)
                                ctrl.set_playlist([Path("a.mp3")])
                                ctrl.play()
                                self.assertTrue(errors)
                                self.assertIn("nicht verbunden", errors[0].lower())
                            finally:
                                ctrl.shutdown()

    def test_set_playlist_keeps_current_file_after_reorder(self) -> None:
        with patch("audio.player_controller._MULTIMEDIA_IMPORT", False):
            with patch("audio.player_controller._MULTIMEDIA_AVAILABLE", False):
                cat = _FakeCat()
                ctrl = PlayerController(cat)  # type: ignore[arg-type]
                try:
                    a, b, c = Path("a.mp3"), Path("b.mp3"), Path("c.mp3")
                    ctrl.set_playlist([a, b, c])
                    ctrl.set_index(1)
                    ctrl.set_playlist([c, b, a])
                    self.assertEqual(ctrl.current_path, b)
                finally:
                    ctrl.shutdown()

    def test_play_rejects_invalid_index(self) -> None:
        with patch("audio.player_controller._MULTIMEDIA_IMPORT", False):
            with patch("audio.player_controller._MULTIMEDIA_AVAILABLE", False):
                cat = _FakeCat()
                ctrl = PlayerController(cat)  # type: ignore[arg-type]
                try:
                    errors: list[str] = []
                    ctrl.error.connect(errors.append)
                    ctrl.set_playlist([Path("a.mp3")])
                    ctrl.play(5)
                    self.assertTrue(errors)
                    self.assertIn("index", errors[0].lower())
                finally:
                    ctrl.shutdown()

    def test_stop_clears_media_source(self) -> None:
        cat = _FakeCat()
        mock_player = MagicMock()
        mock_player.error.return_value = 0
        with patch("audio.player_controller._MULTIMEDIA_IMPORT", True):
            with patch("audio.player_controller._MULTIMEDIA_AVAILABLE", True):
                with patch("audio.player_controller.QMediaPlayer", return_value=mock_player):
                    with patch("audio.player_controller.QAudioOutput"):
                        with patch(
                            "audio.player_controller._player_backend_ok",
                            return_value=True,
                        ):
                            ctrl = PlayerController(cat)  # type: ignore[arg-type]
                            try:
                                from PySide6.QtCore import QUrl

                                ctrl.stop()
                                mock_player.setSource.assert_called_with(QUrl())
                            finally:
                                ctrl.shutdown()

    def test_stop_from_idle(self) -> None:
        with patch("audio.player_controller._MULTIMEDIA_IMPORT", False):
            with patch("audio.player_controller._MULTIMEDIA_AVAILABLE", False):
                cat = _FakeCat()
                ctrl = PlayerController(cat)  # type: ignore[arg-type]
                try:
                    ctrl.stop()
                    self.assertEqual(ctrl.state, PlayerState.IDLE)
                finally:
                    ctrl.shutdown()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
