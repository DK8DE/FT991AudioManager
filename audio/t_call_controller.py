"""1750-Hz-Rufton (T.CALL): Ton auf Sende- + PC-Ausgabe (CAT-TX im Hauptfenster)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QObject, QUrl, Signal

from .player_controller import _player_backend_ok, ensure_playback_backend
from .qt_multimedia_lazy import qt_multimedia_types

if TYPE_CHECKING:
    from .audio_settings_hub import AudioSettingsHub

from model.global_audio_settings import ROLE_PC, ROLE_SEND

_T_CALL_FILENAMES = ("1750.wav", "1750.waf")


def resolve_t_call_wav_path() -> Optional[Path]:
    """Pfad zur Rufton-Datei im ``audio/``-Paketordner."""
    base = Path(__file__).resolve().parent
    for name in _T_CALL_FILENAMES:
        path = base / name
        if path.is_file():
            return path
    return None


def _apply_qt_output_device(audio_out, device_id: str) -> None:
    mm = qt_multimedia_types()
    if mm is None or audio_out is None:
        return
    _QAudioOutput, QMediaDevices, _QMediaPlayer = mm
    if not device_id:
        audio_out.setDevice(QMediaDevices.defaultAudioOutput())
        return
    for dev in QMediaDevices.audioOutputs():
        try:
            dev_uid = dev.id().data().decode("utf-8", errors="replace")
        except Exception:
            dev_uid = ""
        if dev_uid == device_id:
            audio_out.setDevice(dev)
            return
    audio_out.setDevice(QMediaDevices.defaultAudioOutput())


class TCallController(QObject):
    """Rufton-Wiedergabe; PTT (``TX1;``/``TX0;``) steuert das Hauptfenster."""

    error = Signal(str)
    active_changed = Signal(bool)

    def __init__(
        self,
        audio_hub: Optional["AudioSettingsHub"] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._audio_hub = audio_hub
        self._want_active = False
        self._pending_play = False

        self._player_send = None
        self._player_pc = None
        self._audio_out_send = None
        self._audio_out_pc = None
        self._QMediaPlayer: Optional[type] = None
        self._media_ok = False
        self._wav_path = resolve_t_call_wav_path()

    @property
    def is_active(self) -> bool:
        return self._want_active

    def shutdown(self) -> None:
        self.stop()

    def start(self) -> None:
        """Ton starten (nach CAT-TX im Hauptfenster)."""
        if self._want_active:
            return
        if self._wav_path is None:
            self.error.emit(
                "Rufton-Datei fehlt (1750.wav im Ordner audio/)."
            )
            return
        if not ensure_playback_backend(self):
            self.error.emit(
                "Audio-Wiedergabe nicht verfügbar — PySide6-Addons installieren "
                "und App neu starten (Windows: QT_MEDIA_BACKEND=windows)."
            )
            return
        send_id = self._device_id(ROLE_SEND)
        if not send_id:
            self.error.emit(
                "Keine Sende-Ausgabe gewählt — unter Bearbeiten → "
                "Soundeinstellung die Funk-USB-Karte (nicht „System-Standard“) wählen."
            )
            return
        if not self._ensure_players():
            return
        if self._audio_hub is not None and self._audio_hub.uses_windows_volume():
            if not self._audio_hub.push_role_to_windows(ROLE_SEND, unmute=True):
                self.error.emit(
                    "Windows-Mixer: Sende-Soundkarte nicht erreichbar — "
                    "Gerät in den Soundeinstellungen prüfen."
                )
                return
            self._audio_hub.push_role_to_windows(ROLE_PC, unmute=False)

        self._want_active = True
        self.active_changed.emit(True)
        self._apply_output_devices()
        self._apply_volumes()
        self._start_playback()

    def stop(self) -> None:
        """Ton stoppen."""
        if not self._want_active:
            return
        self._want_active = False
        self._pending_play = False
        self.active_changed.emit(False)
        self._stop_playback()

    def _ensure_players(self) -> bool:
        if self._media_ok and self._player_send is not None:
            return True
        mm = qt_multimedia_types()
        if mm is None:
            self.error.emit("Qt Multimedia nicht verfügbar.")
            return False
        QAudioOutput, _QMediaDevices, QMediaPlayer = mm
        self._QMediaPlayer = QMediaPlayer
        url = QUrl.fromLocalFile(str(self._wav_path.resolve()))

        send_id = self._device_id(ROLE_SEND)
        pc_id = self._device_id(ROLE_PC)

        self._player_send = QMediaPlayer(self)
        self._audio_out_send = QAudioOutput(self)
        _apply_qt_output_device(self._audio_out_send, send_id)
        self._player_send.setAudioOutput(self._audio_out_send)
        self._player_send.setSource(url)
        self._player_send.errorOccurred.connect(self._on_media_error)
        self._player_send.mediaStatusChanged.connect(self._on_media_status)

        self._player_pc = QMediaPlayer(self)
        self._audio_out_pc = QAudioOutput(self)
        _apply_qt_output_device(self._audio_out_pc, pc_id)
        self._player_pc.setAudioOutput(self._audio_out_pc)
        self._player_pc.setSource(url)
        self._player_pc.errorOccurred.connect(self._on_media_error)
        self._player_pc.mediaStatusChanged.connect(self._on_media_status)

        for player in (self._player_send, self._player_pc):
            if not _player_backend_ok(player, QMediaPlayer):
                err = player.errorString() or "QMediaPlayer nicht verfügbar"
                self.error.emit(err)
                self._player_send = None
                self._player_pc = None
                self._media_ok = False
                return False
            try:
                player.setLoops(QMediaPlayer.Loops.Infinite)
            except (AttributeError, TypeError):
                pass

        self._media_ok = True
        return True

    def _device_id(self, role: str) -> str:
        if self._audio_hub is not None:
            return self._audio_hub.device_id(role)
        return ""

    def _volume_percent(self, role: str) -> int:
        if self._audio_hub is not None:
            return self._audio_hub.volume_percent(role)
        return 100

    def _apply_output_devices(self) -> None:
        if not self._media_ok:
            return
        _apply_qt_output_device(self._audio_out_send, self._device_id(ROLE_SEND))
        _apply_qt_output_device(self._audio_out_pc, self._device_id(ROLE_PC))

    def _apply_volumes(self) -> None:
        if not self._media_ok:
            return
        try:
            if self._audio_out_send is not None:
                # Voller Qt-Pegel auf die Sende-Karte (Funk-USB), unabhängig vom PC-Slider.
                self._audio_out_send.setVolume(1.0)
            if self._audio_out_pc is not None:
                self._audio_out_pc.setVolume(
                    self._volume_percent(ROLE_PC) / 100.0
                )
        except (AttributeError, TypeError):
            pass

    def _media_ready(self, player) -> bool:
        QMP = self._QMediaPlayer
        if QMP is None or player is None:
            return False
        return player.mediaStatus() in (
            QMP.MediaStatus.LoadedMedia,
            QMP.MediaStatus.BufferedMedia,
            QMP.MediaStatus.EndOfMedia,
        )

    def _start_playback(self) -> None:
        self._pending_play = True
        all_ready = True
        for player in (self._player_send, self._player_pc):
            if player is None:
                continue
            if self._media_ready(player):
                player.play()
            else:
                all_ready = False
        if all_ready:
            self._pending_play = False

    def _stop_playback(self) -> None:
        for player in (self._player_send, self._player_pc):
            if player is None:
                continue
            try:
                player.stop()
            except Exception:
                pass

    def _on_media_status(self, status) -> None:
        if not self._want_active or not self._pending_play:
            return
        QMP = self._QMediaPlayer
        if QMP is None:
            return
        if status in (
            QMP.MediaStatus.LoadedMedia,
            QMP.MediaStatus.BufferedMedia,
        ):
            self._apply_output_devices()
            self._apply_volumes()
            sender = self.sender()
            if sender is not None and self._media_ready(sender):
                sender.play()
            if not self._pending_play:
                return
            if all(
                self._media_ready(p)
                for p in (self._player_send, self._player_pc)
                if p is not None
            ):
                self._pending_play = False
            return
        if status == QMP.MediaStatus.InvalidMedia:
            self._pending_play = False
            player = self.sender()
            msg = ""
            if player is not None:
                try:
                    msg = player.errorString()
                except Exception:
                    pass
            self.error.emit(msg or "1750.wav konnte nicht geladen werden.")
            self.stop()

    def _on_media_error(self, _error, message: str = "") -> None:
        if self._want_active:
            self.error.emit(message or "Rufton-Wiedergabe fehlgeschlagen.")
            self.stop()
