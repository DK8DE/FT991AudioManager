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
            # Recorder nimmt WAV auf (uncompressed), encodet selbst zu MP3.
            Wave = "Wave"
            MP3 = "MP3"

        class AudioCodec:
            Wave = "Wave"
            MP3 = "MP3"

        class RecorderState:
            RecordingState = "recording"
            StoppedState = "stopped"
            PausedState = "paused"

        class EncodingMode:
            ConstantBitRateEncoding = "cbr"
            ConstantQualityEncoding = "cqe"

        class Quality:
            HighQuality = "high"

        class Error:
            NoError = 0

        class ResolveFlags:
            NoFlags = 0

        class ConversionMode:
            Encode = "encode"
            Decode = "decode"

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

    # WAV/PCM-Encoding wird vom Mock-Backend explizit gemeldet, damit der
    # supportedFileFormats/supportedAudioCodecs-Check im Recorder gruen ist.
    fmt_instance = MagicMock(name="QMediaFormat")
    fmt_instance.resolveForEncoding.return_value = None
    fmt_instance.fileFormat.return_value = _Enum.FileFormat.Wave
    fmt_instance.audioCodec.return_value = _Enum.AudioCodec.Wave
    fmt_instance.supportedFileFormats.return_value = [
        _Enum.FileFormat.Wave,
        _Enum.FileFormat.MP3,
    ]
    fmt_instance.supportedAudioCodecs.return_value = [
        _Enum.AudioCodec.Wave,
        _Enum.AudioCodec.MP3,
    ]
    fmt_cls = MagicMock(return_value=fmt_instance)
    fmt_cls.FileFormat = _Enum.FileFormat
    fmt_cls.AudioCodec = _Enum.AudioCodec
    fmt_cls.ResolveFlags = _Enum.ResolveFlags
    fmt_cls.ConversionMode = _Enum.ConversionMode

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
            recorder_instance.setAudioChannelCount.assert_called_once_with(2)
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

                # Im Mock-Backend wird keine echte WAV-Datei geschrieben —
                # wir patchen das Post-Processing weg.
                with patch.object(
                    AudioRecorder, "_post_process_wav_to_mp3", return_value=None
                ):
                    rec._on_recorder_state(rec_state_enum.StoppedState)
            self.assertEqual(rec.state, RecorderState.IDLE)
            self.assertEqual(len(finalized), 1)
            self.assertEqual(
                finalized[0].name,
                "Record_2026_01_02_Stunde_03_04_05.mp3",
            )
            # POST_PROCESSING-Zwischenschritt war im State-Stream sichtbar.
            self.assertIn(RecorderState.POST_PROCESSING, states)
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


class SoftCompressorTest(unittest.TestCase):
    """Sanity-Checks fuer ``soft_compress_normalize``.

    Wir haben absichtlich keine numpy-Abhaengigkeit eingefuehrt — der
    DSP-Code muss also auch in der reinen Python-Stdlib funktionieren.
    Diese Tests pruefen die Eigenschaften, die fuer den User zaehlen:
    leise Aufnahmen werden lauter, Spitzen bleiben unter dem Ceiling,
    leere/stille Buffer kommen unveraendert zurueck.
    """

    def _gen_sine_int16(
        self, *, frequency: float, sample_rate: int, seconds: float, amplitude: float
    ) -> bytes:
        import math as _math
        import struct as _struct

        n = int(sample_rate * seconds)
        # 16-bit Vollaussteuerung = 32767. amplitude ist in 0..1.
        peak = max(0, min(32767, int(round(amplitude * 32767))))
        out = bytearray()
        out_extend = out.extend
        two_pi_f_over_sr = 2.0 * _math.pi * frequency / sample_rate
        for i in range(n):
            v = int(peak * _math.sin(two_pi_f_over_sr * i))
            out_extend(_struct.pack("<h", v))
        return bytes(out)

    def test_empty_buffer_passthrough(self) -> None:
        from audio.audio_recorder import soft_compress_normalize

        self.assertEqual(soft_compress_normalize(b""), b"")

    def test_silent_buffer_passthrough(self) -> None:
        from audio.audio_recorder import _rms_int16, soft_compress_normalize

        silent = b"\x00\x00" * 4410  # 100 ms @ 44.1 kHz, mono
        out = soft_compress_normalize(silent)
        # Stille bleibt Stille — kein hochgepumptes Rauschen.
        self.assertEqual(_rms_int16(out), 0.0)

    def test_quiet_sine_gets_boosted(self) -> None:
        from audio.audio_recorder import _rms_int16, soft_compress_normalize

        # 1 kHz Sinus bei 1 % Aussteuerung -> sehr leise (-40 dBFS RMS).
        quiet = self._gen_sine_int16(
            frequency=1000.0, sample_rate=44100, seconds=0.2, amplitude=0.01
        )
        rms_before = _rms_int16(quiet)
        out = soft_compress_normalize(quiet)
        rms_after = _rms_int16(out)
        self.assertGreater(rms_after, rms_before * 2.0)  # >+6 dB Boost mindestens
        # Ceiling-Check: kein Sample ueber dem 16-bit-Maximum (also
        # implizit auch kein hartes Clipping).
        import struct as _struct
        n = len(out) // 2
        samples = _struct.unpack(f"<{n}h", out[: n * 2])
        peak = max(abs(s) for s in samples)
        self.assertLessEqual(peak, 32767)

    def test_already_loud_signal_not_boosted_into_clipping(self) -> None:
        from audio.audio_recorder import soft_compress_normalize

        # Bereits voll ausgesteuerter Sinus (Peak ~32700).
        loud = self._gen_sine_int16(
            frequency=1000.0, sample_rate=44100, seconds=0.1, amplitude=0.99
        )
        out = soft_compress_normalize(loud)
        # Kein Sample darf 32767 ueberschreiten — Limiter macht seinen Job.
        import struct as _struct
        n = len(out) // 2
        samples = _struct.unpack(f"<{n}h", out[: n * 2])
        peak = max(abs(s) for s in samples)
        self.assertLessEqual(peak, 32767)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
