"""MP3-Aufnahme über ``QMediaRecorder`` + ``QAudioInput`` mit Soft-Compressor.

Aufbau parallel zu :class:`audio.player_controller.PlayerController`:
Qt Multimedia wird lazy geladen, damit ein zu früher Import von
``PySide6.QtMultimedia`` (vor ``QApplication``) nicht das Backend zerschiesst.

Aufnahme-Pipeline::

    QAudioInput(device)  →  QMediaCaptureSession  →  QMediaRecorder(WAV, Stereo)
                                                       → Record_…_HH_MM_SS.wav (tmp)
    ↓ nach Stop:                  Soft-Compressor + Limiter (pure Python)
    ↓                             ↓
    lameenc (LAME-MP3-Encoder) → Record_…_HH_MM_SS.mp3
    ↓ am Ende:                    .wav-Tempdatei wird gelöscht

Warum WAV → MP3 statt direktem MP3 aus dem ``QMediaRecorder``:

1. Das Qt-MP3-Encoding (sowohl Windows-WMF als auch der gebündelte
   ffmpeg-Backend) liefert oft einen sehr **niedrigen Pegel** — typisches
   Yaesu-USB-CODEC-RX-Audio kommt damit deutlich leiser als z. B. eine
   normalisierte Musik-MP3 aus dem Audio-Player. Wir lesen darum den
   sauberen Roh-Pegel aus dem WAV und heben ihn vor dem MP3-Encoding
   gezielt an.
2. Wir können einen einfachen, transparenten Soft-Kompressor in pure
   Python anwenden (RMS-basierte Verstärkung + tanh-Soft-Knee-Limiter
   gegen Verzerrung).
3. Das Endformat bleibt MP3 mit der vom User gewählten Bitrate — also
   keine Änderung für den Replay/Play-PC-Pfad.

Aufgenommen wird **stereo**: der FT-991-USB-CODEC dupliziert das RX-Audio
intern auf beide Kanäle (L+R). Mono würde beim Replay unter Windows/WMF
häufig nur den linken Kanal bedienen — Pegelverlust auf TX.
"""

from __future__ import annotations

import enum
import math
import os
import struct
import wave
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QUrl, Signal

from i18n import tr
from model.audio_recorder_settings import build_recording_filename

from .qt_multimedia_lazy import qt_multimedia_types, qt_recorder_types

# Optionaler audioop-Import: deutlich schneller als pure-Python-struct-Loops
# (~10x für typische Aufnahmegrößen). audioop ist in Python 3.13 deprecated
# und in 3.14 entfernt — wir fallen dann automatisch auf pure Python zurück.
try:
    import audioop as _audioop  # type: ignore[import-deprecated]
except ImportError:  # pragma: no cover — Python 3.14+
    _audioop = None  # type: ignore[assignment]


class RecorderState(enum.Enum):
    IDLE = "idle"
    STARTING = "starting"
    RECORDING = "recording"
    STOPPING = "stopping"
    #: WAV ist fertig — Soft-Compressor + MP3-Encoding läuft (kurz).
    POST_PROCESSING = "post_processing"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Soft-Compressor + Peak-Limiter + LAME-MP3-Encoder
# ---------------------------------------------------------------------------
#: Ziel-RMS in dBFS für normalisierte Aufnahmen. -14 LUFS ist die
#: Streaming-Loudness-Norm (Spotify/YT). Voice-Recordings landen damit
#: subjektiv auf dem Pegel-Niveau eines typischen kommerziellen
#: Musikfiles.
_TARGET_RMS_DBFS = -14.0
#: Max. Boost (verhindert Hochziehen reiner Hintergrund-Stille).
_MAX_BOOST_DB = 30.0
#: Soft-Limiter-Ceiling. -1 dBFS = sicher unter 0 dBFS, kein True-Peak-
#: Overshoot beim MP3-Encoder.
_CEILING_DBFS = -1.0
#: Wo der Limiter sanft einsetzt (tanh-Knee).
_KNEE_FRACTION = 0.7

_INT16_MIN = -32768
_INT16_MAX = 32767


def _peak_abs_int16(pcm_bytes: bytes) -> int:
    """Größter Absolutwert in 16-bit signed PCM. 0 bei leerem Buffer."""
    if not pcm_bytes:
        return 0
    if _audioop is not None:
        try:
            return int(_audioop.max(pcm_bytes, 2))
        except _audioop.error:
            return 0
    n = len(pcm_bytes) // 2
    if n == 0:
        return 0
    samples = struct.unpack(f"<{n}h", pcm_bytes[: n * 2])
    return max(abs(s) for s in samples)


def _rms_int16(pcm_bytes: bytes) -> float:
    """RMS-Pegel in 16-bit signed PCM. 0.0 bei Stille."""
    if not pcm_bytes:
        return 0.0
    if _audioop is not None:
        try:
            return float(_audioop.rms(pcm_bytes, 2))
        except _audioop.error:
            return 0.0
    n = len(pcm_bytes) // 2
    if n == 0:
        return 0.0
    samples = struct.unpack(f"<{n}h", pcm_bytes[: n * 2])
    sq_sum = sum(s * s for s in samples)
    return math.sqrt(sq_sum / n)


def _apply_gain_int16(pcm_bytes: bytes, factor: float) -> bytes:
    """Verstärkt 16-bit signed PCM um den Faktor (clipt sauber)."""
    if not pcm_bytes or factor == 1.0:
        return pcm_bytes
    if _audioop is not None:
        try:
            # audioop.mul clipt bei Überlauf — exakt das, was wir nach
            # dem Soft-Limiter als letzten Schutz wollen.
            return _audioop.mul(pcm_bytes, 2, factor)
        except _audioop.error:
            pass
    n = len(pcm_bytes) // 2
    samples = struct.unpack(f"<{n}h", pcm_bytes[: n * 2])
    scaled = tuple(
        max(_INT16_MIN, min(_INT16_MAX, int(s * factor))) for s in samples
    )
    return struct.pack(f"<{n}h", *scaled)


def _soft_clip_int16(pcm_bytes: bytes) -> bytes:
    """Soft-Knee-Limiter: tanh-Saturation ab ``_KNEE_FRACTION * Ceiling``.

    Bringt Spitzen oberhalb des Knees sanft asymptotisch ans Ceiling
    heran — keine harten Clipping-Verzerrungen, auch bei aggressiver
    Vorverstärkung. Wir arbeiten in float und schreiben am Ende zurück.
    """
    if not pcm_bytes:
        return pcm_bytes
    n = len(pcm_bytes) // 2
    samples = struct.unpack(f"<{n}h", pcm_bytes[: n * 2])
    ceiling = _INT16_MAX * (10 ** (_CEILING_DBFS / 20.0))
    knee = ceiling * _KNEE_FRACTION
    span = ceiling - knee
    if span <= 0:
        return pcm_bytes
    tanh = math.tanh
    out = []
    out_append = out.append
    for s in samples:
        abs_s = -s if s < 0 else s
        if abs_s <= knee:
            out_append(s)
            continue
        # y = knee + span * tanh((x - knee) / span)
        compressed = knee + span * tanh((abs_s - knee) / span)
        out_append(int(-compressed if s < 0 else compressed))
    return struct.pack(f"<{n}h", *out)


def soft_compress_normalize(
    pcm_bytes: bytes,
    *,
    target_rms_dbfs: float = _TARGET_RMS_DBFS,
    max_boost_db: float = _MAX_BOOST_DB,
) -> bytes:
    """Hebt RMS auf Zielpegel + Soft-Limit gegen Verzerrung.

    Pipeline: RMS messen → fehlende dB berechnen → Verstärkung anwenden
    (gedeckelt auf ``max_boost_db``) → Soft-Knee-Limiter gegen Peaks
    über ``_CEILING_DBFS``. Eingabe/Ausgabe sind 16-bit signed PCM-Bytes
    (interleaved bei Stereo).
    """
    if not pcm_bytes:
        return pcm_bytes
    rms = _rms_int16(pcm_bytes)
    if rms < 1.0:
        # Praktisch Stille — kein sinnvolles Anheben möglich.
        return pcm_bytes
    rms_dbfs = 20.0 * math.log10(rms / _INT16_MAX)
    gain_db = target_rms_dbfs - rms_dbfs
    if gain_db > max_boost_db:
        gain_db = max_boost_db
    if gain_db <= 0.1:
        # Bereits laut genug — kein No-op-Pass durch den Limiter
        # (würde unnötig Rechenzeit kosten und nichts ändern).
        return pcm_bytes
    factor = 10 ** (gain_db / 20.0)
    boosted = _apply_gain_int16(pcm_bytes, factor)
    return _soft_clip_int16(boosted)


def _encode_pcm_to_mp3(
    pcm_bytes: bytes,
    *,
    sample_rate: int,
    channels: int,
    bitrate_kbps: int,
    target_path: Path,
) -> None:
    """Schreibt 16-bit signed PCM nach MP3 (CBR) per ``lameenc``."""
    # Lazy import — lameenc ist eine C-Extension und braucht den Wheel
    # erst, wenn der User wirklich aufnimmt.
    import lameenc

    encoder = lameenc.Encoder()
    encoder.set_bit_rate(int(bitrate_kbps))
    encoder.set_in_sample_rate(int(sample_rate))
    encoder.set_channels(int(channels))
    # quality 2 = "very high" — bei CBR wirkt das vor allem auf die
    # Encoder-Internals (Joint-Stereo-Heuristiken etc.), nicht auf die
    # Bitrate. Encoding ist trotzdem sehr schnell (< 100 ms / s Audio).
    try:
        encoder.set_quality(2)
    except Exception:  # noqa: BLE001 — alte lameenc-Versionen ohne set_quality
        pass

    mp3 = encoder.encode(pcm_bytes)
    mp3 += encoder.flush()
    target_path.write_bytes(bytes(mp3))


def list_audio_input_devices() -> list[tuple[str, str]]:
    """(id, Anzeigename) — leere id = System-Standard."""
    mm = qt_multimedia_types()
    if mm is None:
        return [("", tr("common.qt_multimedia_unavailable"))]
    _QAudioOutput, QMediaDevices, _QMediaPlayer = mm
    out: list[tuple[str, str]] = [("", tr("common.system_default"))]
    for dev in QMediaDevices.audioInputs():
        try:
            dev_id = dev.id().data().decode("utf-8", errors="replace")
        except Exception:
            dev_id = dev.description()
        out.append((dev_id, dev.description()))
    return out


def _find_audio_input_device(device_id: str, device_label: str = ""):
    """Sucht das ``QAudioDevice`` zu einer Geräte-ID; ``None`` = Default."""
    from audio.qt_device_resolve import resolve_qt_device_id

    mm = qt_multimedia_types()
    if mm is None:
        return None
    _QAudioOutput, QMediaDevices, _QMediaPlayer = mm
    target_id = resolve_qt_device_id(
        device_id, device_label, input_device=True
    )
    if not target_id:
        return QMediaDevices.defaultAudioInput()
    for dev in QMediaDevices.audioInputs():
        try:
            dev_id = dev.id().data().decode("utf-8", errors="replace")
        except Exception:
            dev_id = ""
        if dev_id == target_id:
            return dev
    return QMediaDevices.defaultAudioInput()


def post_process_wav_to_mp3(
    *,
    wav_path: Path,
    mp3_path: Path,
    bitrate_kbps: int,
    normalize: bool,
) -> None:
    """Temp-WAV normalisieren (optional) und als MP3 speichern."""
    if not wav_path.is_file():
        raise RuntimeError(
            tr("recorder.error.temp_missing", name=wav_path.name)
        )

    try:
        with wave.open(str(wav_path), "rb") as wf:
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            sample_rate = wf.getframerate()
            n_frames = wf.getnframes()
            pcm = wf.readframes(n_frames)
    except (wave.Error, EOFError) as exc:
        raise RuntimeError(
            tr("recorder.error.invalid_wav", exc=exc)
        ) from exc

    if sample_width != 2:
        raise RuntimeError(
            tr(
                "recorder.error.unexpected_bit_depth",
                bits=sample_width * 8,
            )
        )
    if not pcm:
        raise RuntimeError(tr("recorder.error.empty"))

    if normalize:
        pcm = soft_compress_normalize(pcm)

    _encode_pcm_to_mp3(
        pcm,
        sample_rate=sample_rate,
        channels=channels,
        bitrate_kbps=bitrate_kbps,
        target_path=mp3_path,
    )

    try:
        wav_path.unlink()
    except OSError:
        pass


class AudioRecorder(QObject):
    """Stereo-MP3-Aufnahme mit konfigurierbarer Bitrate.

    Stereo (statt Mono) deshalb, damit beim Replay über die USB-Soundkarte
    beide Kanäle bedient werden — sonst landet das Signal je nach Qt-
    Backend nur auf dem linken Kanal und der FT-991-USB-TX-Pegel bricht
    spürbar ein. Der FT-991-USB-CODEC liefert das RX-Audio intern bereits
    auf beiden Kanälen, daher entsteht durch Stereo-Aufnahme kein
    Informationsverlust.
    """

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
        #: Temporäre WAV-Datei, in die QMediaRecorder schreibt. Wird nach
        #: dem Stop in MP3 umgewandelt und dann gelöscht.
        self._current_wav_path: Optional[Path] = None
        self._current_bitrate_kbps: int = 64
        #: Aktiv = Soft-Compressor/Limiter beim WAV→MP3-Encoding anwenden.
        self._normalize_enabled: bool = True
        self._media_ok = False
        self._QMediaRecorder: Optional[type] = None
        self._QMediaFormat: Optional[type] = None
        self._QAudioInput: Optional[type] = None
        self._QMediaCaptureSession: Optional[type] = None
        #: Aufnahme-Lautstärke (0..100 %). Wird beim Anlegen des
        #: ``QAudioInput`` angewendet und kann auch live verändert werden.
        self._input_volume_percent: int = 100

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
            RecorderState.POST_PROCESSING,
        )

    def set_normalize_enabled(self, enabled: bool) -> None:
        """Soft-Compressor/Limiter für die nächste Aufnahme an/aus.

        Bei aktiver Aufnahme greift die Änderung erst beim nächsten Start.
        """
        self._normalize_enabled = bool(enabled)

    def normalize_enabled(self) -> bool:
        return self._normalize_enabled

    def set_input_volume_percent(self, percent: int) -> None:
        """Aufnahme-Lautstärke setzen (0..100 %).

        Wirkt sofort auf ein laufendes ``QAudioInput`` und wird bei der
        nächsten Aufnahme automatisch wieder angewendet.
        """
        self._input_volume_percent = max(0, min(100, int(percent)))
        self._apply_input_volume()

    def input_volume_percent(self) -> int:
        return self._input_volume_percent

    def _apply_input_volume(self) -> None:
        if self._audio_input is None:
            return
        try:
            self._audio_input.setVolume(self._input_volume_percent / 100.0)
        except (AttributeError, TypeError):
            # Sehr alte Qt-Versionen / Stub-Backends ohne setVolume —
            # dann bleibt es bei der Geräte-Default-Lautstärke.
            pass

    # ------------------------------------------------------------------
    # Backend init (lazy nach QApplication)
    # ------------------------------------------------------------------

    def _init_backend(self) -> bool:
        if self._media_ok:
            return True
        rec = qt_recorder_types()
        if rec is None:
            self.error.emit(tr("recorder.error.recorder_unavailable"))
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
        device_label: str = "",
        bitrate_kbps: int = 64,
        now: Optional[datetime] = None,
    ) -> Optional[Path]:
        """Aufnahme starten. Liefert den geplanten MP3-Dateipfad oder ``None``.

        QMediaRecorder schreibt intern erst eine ``.wav``-Tempdatei. Nach
        dem Stop wandeln wir die in MP3 mit dem User-Bitrate-Setting um
        (optional mit Soft-Compressor) und löschen die WAV.
        """
        if self.is_busy():
            self.error.emit(tr("recorder.error.already_recording"))
            return None
        if not self._init_backend():
            return None

        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.error.emit(tr("recorder.error.folder_create", exc=exc))
            return None
        if not os.access(folder, os.W_OK):
            self.error.emit(tr("recorder.error.folder_not_writable", folder=folder))
            return None

        filename = build_recording_filename(now)
        target_mp3 = folder / filename
        # WAV-Tempdatei mit doppelter Endung — direkt erkennbar als
        # Zwischenstand, falls die App während des Encoding abstürzt.
        wav_tmp = target_mp3.with_suffix(".wav.tmp")

        # Vorhandene Session sauber abräumen — selbst nach Fehler hängt
        # sonst ggf. noch ein altes ``QMediaRecorder`` an der Session.
        self._teardown_session()

        device = _find_audio_input_device(device_id, device_label)
        try:
            self._audio_input = self._QAudioInput(self)  # type: ignore[misc]
            if device is not None:
                self._audio_input.setDevice(device)
            self._apply_input_volume()

            self._capture = self._QMediaCaptureSession(self)  # type: ignore[misc]
            self._capture.setAudioInput(self._audio_input)

            self._recorder = self._QMediaRecorder(self)  # type: ignore[misc]
            self._capture.setRecorder(self._recorder)
            self._configure_recorder(self._recorder, bitrate_kbps, wav_tmp)

            self._recorder.recorderStateChanged.connect(self._on_recorder_state)
            self._recorder.durationChanged.connect(self._on_duration)
            self._recorder.errorOccurred.connect(self._on_error)

            self._current_path = target_mp3
            self._current_wav_path = wav_tmp
            self._current_bitrate_kbps = int(bitrate_kbps)
            self._set_state(RecorderState.STARTING)
            self._recorder.record()
            return target_mp3
        except Exception as exc:  # noqa: BLE001 — wir wollen jede Backend-Fehlerart loggen
            self.error.emit(tr("recorder.error.start_failed", exc=exc))
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
            self.error.emit(tr("recorder.error.stop_failed", exc=exc))
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
    # Recorder-Konfiguration (Stereo, WAV/PCM 16-bit, MP3 kommt im Post-Step)
    # ------------------------------------------------------------------

    def _configure_recorder(self, recorder, bitrate_kbps: int, target: Path) -> None:
        QMF = self._QMediaFormat
        QMR = self._QMediaRecorder
        assert QMF is not None and QMR is not None

        fmt = QMF()
        # WAV/PCM ist von allen Qt-Multimedia-Backends (Windows-WMF,
        # ffmpeg, GStreamer) zuverlässig unterstützt — anders als MP3,
        # wo der ffmpeg-Backend im PyInstaller-Bundle gerne stumme
        # 0-Byte-Dateien produziert hat. Wir encoden in einem zweiten
        # Schritt selbst zu MP3 (per ``lameenc``), so haben wir volle
        # Kontrolle über den Pegel.
        fmt.setFileFormat(QMF.FileFormat.Wave)
        fmt.setAudioCodec(QMF.AudioCodec.Wave)
        self._verify_wav_support(fmt)
        recorder.setMediaFormat(fmt)

        try:
            # Stereo: USB-CODEC vom FT-991 liefert RX-Audio auf beiden
            # Kanälen — Aufnahme spiegelt das, Replay füllt damit auch
            # beide Kanäle des USB-Output (vermeidet Pegelverlust auf TX).
            recorder.setAudioChannelCount(2)
        except AttributeError:
            pass
        try:
            # 44.1 kHz fix — guter Default für Sprachaufnahmen und
            # spätere MP3-Encoding-Effizienz.
            recorder.setAudioSampleRate(44100)
        except AttributeError:
            pass
        # Bitrate gilt hier nur fürs spätere MP3-Encoding; WAV ist
        # immer 16-bit PCM (sample_size = 16). Wir merken uns die
        # gewünschte Bitrate in ``start()`` und nutzen sie im Post-Step.
        try:
            recorder.setAudioBitRate(int(bitrate_kbps) * 1000)
        except AttributeError:
            pass
        try:
            recorder.setEncodingMode(QMR.EncodingMode.ConstantQualityEncoding)
        except AttributeError:
            pass
        try:
            recorder.setQuality(QMR.Quality.HighQuality)
        except AttributeError:
            pass

        recorder.setOutputLocation(QUrl.fromLocalFile(str(target.resolve())))

    def _verify_wav_support(self, fmt) -> None:
        """Sicherstellen, dass das Backend WAV/PCM tatsaechlich encoden kann.

        Wir vergleichen die Liste der von ``supportedFileFormats(Encode)``
        bzw. ``supportedAudioCodecs(Encode)`` gemeldeten Werte gegen
        ``FileFormat.Wave``/``AudioCodec.Wave``. Hintergrund: Der
        Windows-Media-Foundation-Backend kennt **kein** WAV-Encoding, er
        schreibt sonst stillschweigend AAC mit ``.wav``-Endung
        ("file does not start with RIFF id"-Fehler beim Lesen).

        Wir verlassen uns hier bewusst NICHT auf ``resolveForEncoding`` —
        das liefert auf manchen Backends einen falsch normalisierten Wert
        zurueck und ist deshalb kein zuverlaessiges Signal.
        """
        QMF = self._QMediaFormat
        assert QMF is not None
        try:
            file_formats = list(fmt.supportedFileFormats(QMF.ConversionMode.Encode))
            codecs = list(fmt.supportedAudioCodecs(QMF.ConversionMode.Encode))
        except AttributeError:
            return

        if (
            QMF.FileFormat.Wave not in file_formats
            or QMF.AudioCodec.Wave not in codecs
        ):
            active_backend = os.environ.get("QT_MEDIA_BACKEND", "(unset)")
            ff_names = ", ".join(
                str(getattr(f, "name", f)) for f in file_formats
            ) or "(keine)"
            cdc_names = ", ".join(
                str(getattr(c, "name", c)) for c in codecs
            ) or "(keine)"
            raise RuntimeError(
                tr(
                    "recorder.error.backend_wav",
                    backend=active_backend,
                    file_formats=ff_names,
                    codecs=cdc_names,
                )
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
            mp3_path = self._current_path
            wav_path = self._current_wav_path
            bitrate = self._current_bitrate_kbps
            normalize = self._normalize_enabled
            self._teardown_session()

            self._current_path = None
            self._current_wav_path = None

            if mp3_path is None or wav_path is None:
                self._set_state(RecorderState.IDLE)
                return

            self._set_state(RecorderState.POST_PROCESSING)
            try:
                self._post_process_wav_to_mp3(
                    wav_path=wav_path,
                    mp3_path=mp3_path,
                    bitrate_kbps=bitrate,
                    normalize=normalize,
                )
            except Exception as exc:  # noqa: BLE001 — User soll Fehlertext sehen
                self.error.emit(tr("recorder.error.post_process", exc=exc))
                # WAV-Reste aufräumen, wenn die MP3 nicht zustande kam.
                self._safe_unlink(wav_path)
                self._set_state(RecorderState.IDLE)
                return

            self._set_state(RecorderState.IDLE)
            self.file_finalized.emit(mp3_path)
            return
        # PausedState wird absichtlich nicht genutzt.

    def _post_process_wav_to_mp3(
        self,
        *,
        wav_path: Path,
        mp3_path: Path,
        bitrate_kbps: int,
        normalize: bool,
    ) -> None:
        """Legacy-Wrapper — siehe :func:`post_process_wav_to_mp3`."""
        post_process_wav_to_mp3(
            wav_path=wav_path,
            mp3_path=mp3_path,
            bitrate_kbps=bitrate_kbps,
            normalize=normalize,
        )

    @staticmethod
    def _safe_unlink(path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            pass

    def _on_duration(self, ms: int) -> None:
        self.duration_changed.emit(int(ms))

    def _on_error(self, _error, message: str = "") -> None:
        msg = message or tr("recorder.error.generic")
        self.error.emit(msg)
        # Backend stoppt sich beim Error i.d.R. selbst — wir räumen auf.
        self._teardown_session()
        self._current_path = None
        self._set_state(RecorderState.ERROR)

    def _set_state(self, state: RecorderState) -> None:
        if self._state != state:
            self._state = state
            self.state_changed.emit(state)
