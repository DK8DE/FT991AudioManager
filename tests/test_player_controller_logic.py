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


def _disable_multimedia(ctrl: PlayerController) -> None:
    ctrl._media_ok = False
    ctrl._player = None


class PlayerControllerLogicTest(unittest.TestCase):
    def test_play_without_multimedia_emits_error(self) -> None:
        with patch("audio.player_controller.qt_multimedia_types", return_value=None):
            cat = _FakeCat()
            ctrl = PlayerController(cat)  # type: ignore[arg-type]
            try:
                _disable_multimedia(ctrl)
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
        mock_player.error.return_value = 0

        def _fake_init(self: PlayerController) -> None:
            import audio.player_controller as pc

            pc._MULTIMEDIA_IMPORT = True
            pc._MULTIMEDIA_AVAILABLE = True
            self._media_ok = True
            self._player = mock_player
            self._QMediaPlayer = MagicMock()
            self._QMediaPlayer.Error.NoError = 0
            self._audio_out = MagicMock()

        with patch.object(PlayerController, "_init_multimedia", _fake_init):
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
        with patch("audio.player_controller.qt_multimedia_types", return_value=None):
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
        with patch("audio.player_controller.qt_multimedia_types", return_value=None):
            cat = _FakeCat()
            ctrl = PlayerController(cat)  # type: ignore[arg-type]
            try:
                _disable_multimedia(ctrl)
                errors: list[str] = []
                ctrl.error.connect(errors.append)
                ctrl.set_playlist([Path("a.mp3")])
                ctrl.play(5)
                self.assertTrue(errors)
                self.assertIn("index", errors[0].lower())
            finally:
                ctrl.shutdown()

    def test_stop_keeps_media_source_for_preview_seek(self) -> None:
        cat = _FakeCat()
        mock_player = MagicMock()
        mock_player.error.return_value = 0

        def _fake_init(self: PlayerController) -> None:
            import audio.player_controller as pc

            pc._MULTIMEDIA_IMPORT = True
            pc._MULTIMEDIA_AVAILABLE = True
            self._media_ok = True
            self._player = mock_player
            self._QMediaPlayer = MagicMock()
            self._QMediaPlayer.Error.NoError = 0
            self._audio_out = MagicMock()

        with patch.object(PlayerController, "_init_multimedia", _fake_init):
            ctrl = PlayerController(cat)  # type: ignore[arg-type]
            try:
                ctrl.stop()
                mock_player.setSource.assert_not_called()
            finally:
                ctrl.shutdown()

    def test_stop_from_idle(self) -> None:
        with patch("audio.player_controller.qt_multimedia_types", return_value=None):
            cat = _FakeCat()
            ctrl = PlayerController(cat)  # type: ignore[arg-type]
            try:
                ctrl.stop()
                self.assertEqual(ctrl.state, PlayerState.IDLE)
            finally:
                ctrl.shutdown()

    def test_contest_pause_done_emits_signal_without_beginning_pre_roll(self) -> None:
        cat = _FakeCat()
        mock_player = MagicMock()
        mock_player.error.return_value = 0

        def _fake_init(self: PlayerController) -> None:
            import audio.player_controller as pc

            pc._MULTIMEDIA_IMPORT = True
            pc._MULTIMEDIA_AVAILABLE = True
            self._media_ok = True
            self._player = mock_player
            self._QMediaPlayer = MagicMock()
            self._QMediaPlayer.Error.NoError = 0
            self._audio_out = MagicMock()

        with patch.object(PlayerController, "_init_multimedia", _fake_init):
            ctrl = PlayerController(cat)  # type: ignore[arg-type]
            try:
                hits: list[bool] = []
                ctrl.contest_pre_roll_requested.connect(lambda: hits.append(True))
                ctrl._state = PlayerState.LISTEN_PAUSE
                ctrl._on_contest_pause_done()
                self.assertEqual(hits, [True])
                self.assertEqual(ctrl.state, PlayerState.LISTEN_PAUSE)
            finally:
                ctrl.shutdown()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
