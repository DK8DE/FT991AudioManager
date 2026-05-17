"""MP3-Aufnahme über ``QMediaRecorder`` + ``QAudioInput``.

Aufbau parallel zu :class:`audio.player_controller.PlayerController`:
Qt Multimedia wird lazy geladen, damit ein zu früher Import von
``PySide6.QtMultimedia`` (vor ``QApplication``) nicht das Backend zerschiesst.

Aufnahme-Pipeline::

    QAudioInput(device)  →  QMediaCaptureSession  →  QMediaRecorder(MP3, Mono)
                                                       → Record_…_HH_MM_SS.mp3
"""

from __future__ import annotations

import enum
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QUrl, Signal

from model.audio_recorder_settings import build_recording_filename

from .qt_multimedia_lazy import qt_multimedia_types, qt_recorder_types


class RecorderState(enum.Enum):
    IDLE = "idle"
    STARTING = "starting"
    RECORDING = "recording"
    STOPPING = "stopping"
    ERROR = "error"


def list_audio_input_devices() -> list[tuple[str, str]]:
    """(id, Anzeigename) — leere id = System-Standard."""
    mm = qt_multimedia_types()
    if mm is None:
        return [("", "Qt Multimedia nicht verfügbar")]
    _QAudioOutput, QMediaDevices, _QMediaPlayer = mm
    out: list[tuple[str, str]] = [("", "System-Standard")]
    for dev in QMediaDevices.audioInputs():
        try:
            dev_id = dev.id().data().decode("utf-8", errors="replace")
        except Exception:
            dev_id = dev.description()
        out.append((dev_id, dev.description()))
    return out


def _find_audio_input_device(device_id: str):
    """Sucht das ``QAudioDevice`` zu einer Geräte-ID; ``None`` = Default."""
    mm = qt_multimedia_types()
    if mm is None:
        return None
    _QAudioOutput, QMediaDevices, _QMediaPlayer = mm
    if not device_id:
        return QMediaDevices.defaultAudioInput()
    for dev in QMediaDevices.audioInputs():
        try:
            dev_id = dev.id().data().decode("utf-8", errors="replace")
        except Exception:
            dev_id = ""
        if dev_id == device_id:
            return dev
    return QMediaDevices.defaultAudioInput()


class AudioRecorder(QObject):
    """Mono-MP3-Aufnahme mit konfigurierbarer Bitrate."""

    state_changed = Signal(object)            # RecorderState
    duration_changed = Signal(int)            # ms
    error = Signal(str)
    #: Wird genau einmal pro Aufnahme emittiert, sobald die Datei
    #: tatsächlich geschrieben wurde (RecorderState wechselt auf StoppedState).
    file_finalized = Signal(object)           # Path

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._state = RecorderState.IDLE
        self._audio_input = None
        self._capture = None
        self._recorder = None
        self._current_path: Optional[Path] = None
        self._media_ok = False
        self._QMediaRecorder: Optional[type] = None
        self._QMediaFormat: Optional[type] = None
        self._QAudioInput: Optional[type] = None
        self._QMediaCaptureSession: Optional[type] = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> RecorderState:
        return self._state

    @property
    def current_path(self) -> Optional[Path]:
        return self._current_path

    def is_busy(self) -> bool:
        return self._state in (
            RecorderState.STARTING,
            RecorderState.RECORDING,
            RecorderState.STOPPING,
        )

    # ------------------------------------------------------------------
    # Backend init (lazy nach QApplication)
    # ------------------------------------------------------------------

    def _init_backend(self) -> bool:
        if self._media_ok:
            return True
        rec = qt_recorder_types()
        if rec is None:
            self.error.emit(
                "Qt Multimedia-Recorder nicht verfügbar. "
                "pip install PySide6-Addons, App neu starten. "
                "Dev: QT_MEDIA_BACKEND=windows probieren."
            )
            return False
        QAudioInput, QMediaCaptureSession, QMediaRecorder, QMediaFormat = rec
        self._QAudioInput = QAudioInput
        self._QMediaCaptureSession = QMediaCaptureSession
        self._QMediaRecorder = QMediaRecorder
        self._QMediaFormat = QMediaFormat
        self._media_ok = True
        return True

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    def start(
        self,
        folder: Path,
        device_id: str = "",
        bitrate_kbps: int = 64,
        now: Optional[datetime] = None,
    ) -> Optional[Path]:
        """Aufnahme starten. Liefert den geplanten Dateipfad oder ``None``."""
        if self.is_busy():
            self.error.emit("Es läuft bereits eine Aufnahme.")
            return None
        if not self._init_backend():
            return None

        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.error.emit(f"Aufnahme-Ordner konnte nicht angelegt werden: {exc}")
            return None
        if not os.access(folder, os.W_OK):
            self.error.emit(f"Aufnahme-Ordner ist nicht beschreibbar: {folder}")
            return None

        filename = build_recording_filename(now)
        target = folder / filename

        # Vorhandene Session sauber abräumen — selbst nach Fehler hängt
        # sonst ggf. noch ein altes ``QMediaRecorder`` an der Session.
        self._teardown_session()

        device = _find_audio_input_device(device_id)
        try:
            self._audio_input = self._QAudioInput(self)  # type: ignore[misc]
            if device is not None:
                self._audio_input.setDevice(device)

            self._capture = self._QMediaCaptureSession(self)  # type: ignore[misc]
            self._capture.setAudioInput(self._audio_input)

            self._recorder = self._QMediaRecorder(self)  # type: ignore[misc]
            self._capture.setRecorder(self._recorder)
            self._configure_recorder(self._recorder, bitrate_kbps, target)

            self._recorder.recorderStateChanged.connect(self._on_recorder_state)
            self._recorder.durationChanged.connect(self._on_duration)
            self._recorder.errorOccurred.connect(self._on_error)

            self._current_path = target
            self._set_state(RecorderState.STARTING)
            self._recorder.record()
            return target
        except Exception as exc:  # noqa: BLE001 — wir wollen jede Backend-Fehlerart loggen
            self.error.emit(f"Aufnahme konnte nicht gestartet werden: {exc}")
            self._teardown_session()
            self._set_state(RecorderState.ERROR)
            return None

    def stop(self) -> None:
        if self._state in (RecorderState.IDLE, RecorderState.STOPPING):
            return
        if self._recorder is None:
            self._set_state(RecorderState.IDLE)
            return
        self._set_state(RecorderState.STOPPING)
        try:
            self._recorder.stop()
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"Aufnahme konnte nicht gestoppt werden: {exc}")
            self._teardown_session()
            self._set_state(RecorderState.ERROR)

    def shutdown(self) -> None:
        try:
            if self._recorder is not None and self._state in (
                RecorderState.STARTING,
                RecorderState.RECORDING,
            ):
                self._recorder.stop()
        finally:
            self._teardown_session()
            self._set_state(RecorderState.IDLE)

    # ------------------------------------------------------------------
    # Recorder-Konfiguration (Mono, MP3, CBR)
    # ------------------------------------------------------------------

    def _configure_recorder(self, recorder, bitrate_kbps: int, target: Path) -> None:
        QMF = self._QMediaFormat
        QMR = self._QMediaRecorder
        assert QMF is not None and QMR is not None

        fmt = QMF()
        # Wenn das Backend MP3 nicht kennt, brechen wir lieber sofort ab —
        # sonst landet stillschweigend eine 0-Byte-Datei auf der Platte
        # (typischer Fehler im PyInstaller-Bundle mit ffmpeg-Backend ohne
        # MP3-Encoder).
        fmt.setFileFormat(QMF.FileFormat.MP3)
        fmt.setAudioCodec(QMF.AudioCodec.MP3)
        self._verify_mp3_support(fmt)
        recorder.setMediaFormat(fmt)

        try:
            recorder.setAudioChannelCount(1)
        except AttributeError:
            pass
        try:
            recorder.setAudioBitRate(int(bitrate_kbps) * 1000)
        except AttributeError:
            pass
        try:
            recorder.setEncodingMode(QMR.EncodingMode.ConstantBitRateEncoding)
        except AttributeError:
            pass
        try:
            recorder.setQuality(QMR.Quality.HighQuality)
        except AttributeError:
            pass

        recorder.setOutputLocation(QUrl.fromLocalFile(str(target.resolve())))

    def _verify_mp3_support(self, fmt) -> None:
        """Stellt sicher, dass das Backend MP3-Encoding kann.

        ``QMediaFormat.resolveForEncoding(NoFlags)`` aendert das Format
        in-place und liefert in PySide6 ``None`` zurueck. Wir lesen
        anschliessend ``fileFormat()`` / ``audioCodec()`` aus dem
        Original-Objekt und vergleichen.

        Bricht der Call ueberhaupt nicht durch (alte Qt-Version ohne die
        API), gehen wir davon aus, dass das Backend das gewuenschte
        Format unterstuetzt.
        """
        QMF = self._QMediaFormat
        assert QMF is not None
        try:
            fmt.resolveForEncoding(QMF.ResolveFlags.NoFlags)
        except (AttributeError, TypeError):
            return

        try:
            resolved_file_format = fmt.fileFormat()
            resolved_audio_codec = fmt.audioCodec()
        except AttributeError:
            return

        if resolved_file_format != QMF.FileFormat.MP3:
            raise RuntimeError(
                "MP3-Encoding wird vom aktiven Qt-Multimedia-Backend nicht "
                "unterstuetzt. Bitte Umgebungsvariable "
                "QT_MEDIA_BACKEND=windows setzen und Programm neu starten."
            )
        if resolved_audio_codec != QMF.AudioCodec.MP3:
            raise RuntimeError(
                "MP3-Audiocodec nicht verfuegbar (Backend liefert "
                f"{resolved_audio_codec}). Bitte QT_MEDIA_BACKEND=windows "
                "setzen und Programm neu starten."
            )

    def _teardown_session(self) -> None:
        for obj_name in ("_recorder", "_capture", "_audio_input"):
            obj = getattr(self, obj_name, None)
            if obj is not None:
                try:
                    obj.deleteLater()
                except Exception:
                    pass
                setattr(self, obj_name, None)

    # ------------------------------------------------------------------
    # Qt-Signal-Bridges
    # ------------------------------------------------------------------

    def _on_recorder_state(self, qt_state) -> None:
        QMR = self._QMediaRecorder
        if QMR is None:
            return
        if qt_state == QMR.RecorderState.RecordingState:
            self._set_state(RecorderState.RECORDING)
            return
        if qt_state == QMR.RecorderState.StoppedState:
            finalized = self._current_path
            self._set_state(RecorderState.IDLE)
            self._teardown_session()
            if finalized is not None:
                self.file_finalized.emit(finalized)
            self._current_path = None
            return
        # PausedState wird absichtlich nicht genutzt.

    def _on_duration(self, ms: int) -> None:
        self.duration_changed.emit(int(ms))

    def _on_error(self, _error, message: str = "") -> None:
        msg = message or "Aufnahme-Fehler"
        self.error.emit(msg)
        # Backend stoppt sich beim Error i.d.R. selbst — wir räumen auf.
        self._teardown_session()
        self._current_path = None
        self._set_state(RecorderState.ERROR)

    def _set_state(self, state: RecorderState) -> None:
        if self._state != state:
            self._state = state
            self.state_changed.emit(state)
