"""Smoke-Tests für ``audio.audio_recorder`` (ohne echtes Qt Multimedia)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys

from PySide6.QtWidgets import QApplication

from audio.audio_recorder import AudioRecorder, RecorderState

_app = QApplication.instance() or QApplication(sys.argv if hasattr(sys, "argv") else [])


def _install_fake_backend(rec: AudioRecorder) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Setzt einen vollständig gemockten Qt-Multimedia-Backend in den Recorder.

    Liefert (audio_input_cls, capture_cls, recorder_cls) — jeweils MagicMock,
    deren ``return_value`` als Instanz dient. Die Konstruktoren werden später
    von :meth:`AudioRecorder.start` aufgerufen.
    """

    class _Enum:
        class FileFormat:
            MP3 = "MP3"

        class AudioCodec:
            MP3 = "MP3"

        class RecorderState:
            RecordingState = "recording"
            StoppedState = "stopped"
            PausedState = "paused"

        class EncodingMode:
            ConstantBitRateEncoding = "cbr"

        class Quality:
            HighQuality = "high"

        class Error:
            NoError = 0

    audio_input_cls = MagicMock(return_value=MagicMock(name="QAudioInput"))
    capture_cls = MagicMock(return_value=MagicMock(name="QMediaCaptureSession"))

    recorder_instance = MagicMock(name="QMediaRecorder")
    recorder_instance.RecorderState = _Enum.RecorderState
    recorder_instance.EncodingMode = _Enum.EncodingMode
    recorder_instance.Quality = _Enum.Quality
    recorder_cls = MagicMock(return_value=recorder_instance)
    recorder_cls.RecorderState = _Enum.RecorderState
    recorder_cls.EncodingMode = _Enum.EncodingMode
    recorder_cls.Quality = _Enum.Quality

    fmt_instance = MagicMock(name="QMediaFormat")
    fmt_cls = MagicMock(return_value=fmt_instance)
    fmt_cls.FileFormat = _Enum.FileFormat
    fmt_cls.AudioCodec = _Enum.AudioCodec

    rec._QAudioInput = audio_input_cls  # type: ignore[assignment]
    rec._QMediaCaptureSession = capture_cls  # type: ignore[assignment]
    rec._QMediaRecorder = recorder_cls  # type: ignore[assignment]
    rec._QMediaFormat = fmt_cls  # type: ignore[assignment]
    rec._media_ok = True
    return audio_input_cls, capture_cls, recorder_cls


class AudioRecorderSmokeTest(unittest.TestCase):
    def test_start_builds_path_and_configures_recorder(self) -> None:
        rec = AudioRecorder()
        try:
            ai_cls, cap_cls, rec_cls = _install_fake_backend(rec)
            with tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                # Geräte-Lookup mocken — wir wollen keine echten Audio-Geräte berühren.
                with patch(
                    "audio.audio_recorder._find_audio_input_device",
                    return_value=None,
                ):
                    target = rec.start(
                        folder=folder,
                        device_id="",
                        bitrate_kbps=128,
                        now=datetime(2026, 5, 17, 9, 28, 0),
                    )
            self.assertIsNotNone(target)
            assert target is not None
            self.assertEqual(
                target.name,
                "Record_2026_05_17_Stunde_09_28_00.mp3",
            )
            self.assertEqual(target.parent.resolve(), folder.resolve())

            recorder_instance = rec_cls.return_value
            recorder_instance.setAudioChannelCount.assert_called_once_with(1)
            recorder_instance.setAudioBitRate.assert_called_once_with(128_000)
            recorder_instance.record.assert_called_once()
            cap_cls.return_value.setRecorder.assert_called_once_with(recorder_instance)
            cap_cls.return_value.setAudioInput.assert_called_once_with(
                ai_cls.return_value
            )
            self.assertEqual(rec.state, RecorderState.STARTING)
        finally:
            rec.shutdown()

    def test_start_refuses_when_busy(self) -> None:
        rec = AudioRecorder()
        try:
            _install_fake_backend(rec)
            errors: list[str] = []
            rec.error.connect(errors.append)
            with tempfile.TemporaryDirectory() as tmp:
                with patch(
                    "audio.audio_recorder._find_audio_input_device",
                    return_value=None,
                ):
                    rec.start(folder=Path(tmp), device_id="", bitrate_kbps=64)
                # zweiter Start → soll Error feuern, kein zweites Backend bauen.
                second = rec.start(folder=Path(tmp), device_id="")
            self.assertIsNone(second)
            self.assertTrue(errors)
            self.assertIn("läuft bereits", errors[0].lower())
        finally:
            rec.shutdown()

    def test_stop_transitions_via_qt_state_signal(self) -> None:
        rec = AudioRecorder()
        try:
            _, _, rec_cls = _install_fake_backend(rec)
            states: list[RecorderState] = []
            finalized: list[Path] = []
            rec.state_changed.connect(states.append)
            rec.file_finalized.connect(finalized.append)

            with tempfile.TemporaryDirectory() as tmp:
                with patch(
                    "audio.audio_recorder._find_audio_input_device",
                    return_value=None,
                ):
                    target = rec.start(
                        folder=Path(tmp),
                        device_id="",
                        bitrate_kbps=64,
                        now=datetime(2026, 1, 2, 3, 4, 5),
                    )
            self.assertIsNotNone(target)
            # Qt würde nach record() RecordingState melden — wir simulieren das:
            rec_state_enum = rec_cls.RecorderState
            rec._on_recorder_state(rec_state_enum.RecordingState)
            self.assertEqual(rec.state, RecorderState.RECORDING)

            # Stop anfordern → State STOPPING, dann Qt liefert StoppedState.
            rec.stop()
            self.assertEqual(rec.state, RecorderState.STOPPING)

            rec._on_recorder_state(rec_state_enum.StoppedState)
            self.assertEqual(rec.state, RecorderState.IDLE)
            self.assertEqual(len(finalized), 1)
            self.assertEqual(
                finalized[0].name,
                "Record_2026_01_02_Stunde_03_04_05.mp3",
            )
        finally:
            rec.shutdown()

    def test_start_without_backend_emits_error(self) -> None:
        rec = AudioRecorder()
        try:
            with patch(
                "audio.audio_recorder.qt_recorder_types", return_value=None
            ):
                errors: list[str] = []
                rec.error.connect(errors.append)
                with tempfile.TemporaryDirectory() as tmp:
                    target = rec.start(folder=Path(tmp), device_id="")
            self.assertIsNone(target)
            self.assertTrue(errors)
            self.assertIn("recorder", errors[0].lower())
        finally:
            rec.shutdown()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
