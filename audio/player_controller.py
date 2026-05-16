"""Steuerlogik Audio-Player mit CAT-PTT und Qt Multimedia."""

from __future__ import annotations

import enum
from pathlib import Path
from typing import Literal, Optional

from PySide6.QtCore import QMetaObject, QObject, QThread, Qt, QTimer, QUrl, Signal, Slot, Q_ARG

from cat import SerialCAT
from model.audio_player_settings import PlaybackMode

from .cat_ptt_worker import CatPttWorker
from .qt_media_env import ensure_qt_media_backend

ensure_qt_media_backend()

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer

    _MULTIMEDIA_IMPORT = True
except ImportError:  # pragma: no cover
    QAudioOutput = None  # type: ignore[misc, assignment]
    QMediaDevices = None  # type: ignore[misc, assignment]
    QMediaPlayer = None  # type: ignore[misc, assignment]
    _MULTIMEDIA_IMPORT = False

_MULTIMEDIA_AVAILABLE = _MULTIMEDIA_IMPORT

AfterRx = Literal["idle", "paused", "gap", "stop"]


class PlayerState(enum.Enum):
    IDLE = "idle"
    PRE_ROLL = "pre_roll"
    WAITING_TX = "waiting_tx"
    PLAYING = "playing"
    PAUSED_RX = "paused_rx"
    WAITING_RX = "waiting_rx"
    GAP = "gap"
    STOPPING = "stopping"


def multimedia_available() -> bool:
    return bool(_MULTIMEDIA_IMPORT and _MULTIMEDIA_AVAILABLE)


def _player_backend_ok(player: object) -> bool:
    if not _MULTIMEDIA_IMPORT or player is None:
        return False
    err = player.error()  # type: ignore[union-attr]
    return err == QMediaPlayer.Error.NoError  # type: ignore[union-attr]


def list_audio_output_devices() -> list[tuple[str, str]]:
    """(id, Anzeigename) — leere id = System-Standard."""
    if not _MULTIMEDIA_AVAILABLE:
        return [("", "Qt Multimedia nicht verfügbar")]
    out: list[tuple[str, str]] = [("", "System-Standard")]
    for dev in QMediaDevices.audioOutputs():
        out.append((dev.id().data().decode("utf-8", errors="replace"), dev.description()))
    return out


class PlayerController(QObject):
    """Zustandsmaschine: Vorlauf → CAT-TX → Wiedergabe → CAT-RX."""

    state_changed = Signal(object)
    position_changed = Signal(int, int)
    current_file_changed = Signal(str)
    error = Signal(str)
    status_message = Signal(str)

    def __init__(self, serial_cat: SerialCAT, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._cat = serial_cat
        self._state = PlayerState.IDLE
        self._paths: list[Path] = []
        self._index = 0
        self._pre_roll_ms = 1000
        self._gap_ms = 500
        self._mode: PlaybackMode = "single"
        self._resume_after_pause = False
        self._after_rx: AfterRx = "idle"
        self._output_device_id = ""
        self._volume_percent = 100

        self._pre_roll_timer = QTimer(self)
        self._pre_roll_timer.setSingleShot(True)
        self._pre_roll_timer.timeout.connect(self._on_pre_roll_done)

        self._gap_timer = QTimer(self)
        self._gap_timer.setSingleShot(True)
        self._gap_timer.timeout.connect(self._on_gap_done)

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(200)
        self._tick_timer.timeout.connect(self._emit_position)

        self._ptt_thread = QThread(self)
        self._ptt_worker = CatPttWorker(self._cat)
        self._ptt_worker.moveToThread(self._ptt_thread)
        self._ptt_worker.succeeded.connect(self._on_ptt_succeeded)
        self._ptt_worker.failed.connect(self._on_ptt_failed)
        self._ptt_thread.start()

        self._expect_ptt_on: Optional[bool] = None
        self._pending_media_play = False

        self._player: Optional[QMediaPlayer] = None
        self._audio_out: Optional[QAudioOutput] = None
        self._media_ok = False
        if _MULTIMEDIA_IMPORT:
            self._player = QMediaPlayer(self)
            if _player_backend_ok(self._player):
                self._media_ok = True
                global _MULTIMEDIA_AVAILABLE
                _MULTIMEDIA_AVAILABLE = True
                self._audio_out = QAudioOutput(self)
                self._player.setAudioOutput(self._audio_out)
                self._player.mediaStatusChanged.connect(self._on_media_status)
                self._player.errorOccurred.connect(self._on_media_error)
                self._apply_output_device()
                self._apply_volume()
            else:
                _MULTIMEDIA_AVAILABLE = False
                err = self._player.errorString()  # type: ignore[union-attr]
                self._player.deleteLater()
                self._player = None
                self.status_message.emit(
                    f"QMediaPlayer nicht verfügbar ({err}). "
                    "Unter Windows: PySide6-Addons installiert? "
                    "Neustart nach „pip install PySide6-Addons“."
                )

    def shutdown(self) -> None:
        self.stop()
        self._ptt_thread.quit()
        self._ptt_thread.wait(3000)

    @property
    def state(self) -> PlayerState:
        return self._state

    @property
    def current_path(self) -> Optional[Path]:
        return self._current_path()

    def set_playlist(self, paths: list[Path]) -> None:
        """Playlist ersetzen; Index an dieselbe Datei koppeln (wichtig nach Drag & Drop)."""
        current = self._current_path()
        self._paths = list(paths)
        if current is not None:
            try:
                self._index = self._paths.index(current)
            except ValueError:
                self._index = min(self._index, max(0, len(self._paths) - 1))
        elif self._index >= len(self._paths):
            self._index = max(0, len(self._paths) - 1)

    def set_index(self, index: int) -> None:
        if 0 <= index < len(self._paths):
            self._index = index

    def set_timing(self, pre_roll_ms: int, gap_between_files_ms: int) -> None:
        self._pre_roll_ms = max(0, int(pre_roll_ms))
        self._gap_ms = max(0, int(gap_between_files_ms))

    def set_playback_mode(self, mode: PlaybackMode) -> None:
        self._mode = mode

    def set_output_device_id(self, device_id: str) -> None:
        self._output_device_id = device_id or ""
        self._apply_output_device()

    def set_volume_percent(self, percent: int) -> None:
        self._volume_percent = max(0, min(100, int(percent)))
        self._apply_volume()

    def volume_percent(self) -> int:
        return self._volume_percent

    def _apply_output_device(self) -> None:
        if not _MULTIMEDIA_AVAILABLE or self._audio_out is None:
            return
        if not self._output_device_id:
            self._audio_out.setDevice(QMediaDevices.defaultAudioOutput())
        else:
            matched = False
            for dev in QMediaDevices.audioOutputs():
                dev_id = dev.id().data().decode("utf-8", errors="replace")
                if dev_id == self._output_device_id:
                    self._audio_out.setDevice(dev)
                    matched = True
                    break
            if not matched:
                self._audio_out.setDevice(QMediaDevices.defaultAudioOutput())
        self._apply_volume()

    def _apply_volume(self) -> None:
        if self._audio_out is not None:
            self._audio_out.setVolume(self._volume_percent / 100.0)

    def is_busy(self) -> bool:
        return self._state not in (PlayerState.IDLE, PlayerState.PAUSED_RX)

    def play(self, index: Optional[int] = None) -> None:
        if not self._paths:
            self.error.emit("Keine Audiodateien in der Liste.")
            return
        if index is not None:
            if index < 0 or index >= len(self._paths):
                self.error.emit("Ungültiger Dateiindex.")
                return
            self._index = index
        if not _MULTIMEDIA_AVAILABLE or not self._media_ok:
            self.error.emit(
                "Audio-Wiedergabe nicht verfügbar. Unter Windows PySide6-Addons "
                "installieren und App neu starten: pip install PySide6-Addons"
            )
            return
        if not self._cat.is_connected():
            self.error.emit("CAT nicht verbunden — bitte zuerst verbinden.")
            return
        if self._state == PlayerState.PAUSED_RX:
            self._resume_after_pause = True
            self._begin_pre_roll()
            return
        if self.is_busy():
            return
        self._resume_after_pause = False
        self._begin_pre_roll()

    def pause(self) -> None:
        if self._state != PlayerState.PLAYING:
            return
        if self._player is not None:
            self._player.pause()
        self._tick_timer.stop()
        self._after_rx = "paused"
        self._goto_waiting_rx()

    def stop(self) -> None:
        self._pre_roll_timer.stop()
        self._gap_timer.stop()
        self._tick_timer.stop()
        self._resume_after_pause = False
        self._pending_media_play = False
        if self._player is not None:
            self._player.stop()
            # Quelle leeren — sonst feuert setSource(dieselbe Datei) oft kein LoadedMedia erneut.
            self._player.setSource(QUrl())
        if self._state in (PlayerState.IDLE, PlayerState.PAUSED_RX):
            self._set_state(PlayerState.IDLE)
            self.status_message.emit("Gestoppt")
            return
        if self._state == PlayerState.PRE_ROLL:
            self._set_state(PlayerState.IDLE)
            self.status_message.emit("Gestoppt")
            return
        if self._state == PlayerState.GAP:
            self._set_state(PlayerState.IDLE)
            self.status_message.emit("Gestoppt")
            return
        self._after_rx = "stop"
        self._goto_waiting_rx()

    def _goto_waiting_rx(self) -> None:
        self._set_state(PlayerState.WAITING_RX)
        self._request_ptt(False)

    def _begin_pre_roll(self) -> None:
        path = self._current_path()
        if path is None:
            self.error.emit("Ungültiger Dateiindex.")
            return
        self.current_file_changed.emit(path.name)
        self.status_message.emit(f"Vorlauf {self._pre_roll_ms} ms …")
        self._set_state(PlayerState.PRE_ROLL)
        if self._pre_roll_ms <= 0:
            self._on_pre_roll_done()
        else:
            self._pre_roll_timer.start(self._pre_roll_ms)

    def _on_pre_roll_done(self) -> None:
        if self._state != PlayerState.PRE_ROLL:
            return
        self._set_state(PlayerState.WAITING_TX)
        self.status_message.emit("CAT-TX wird geschaltet …")
        self._request_ptt(True)

    def _request_ptt(self, on: bool) -> None:
        self._expect_ptt_on = on
        QMetaObject.invokeMethod(
            self._ptt_worker,
            "set_transmit",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(bool, on),
        )

    @Slot(bool)
    def _on_ptt_succeeded(self, on: bool) -> None:
        if self._expect_ptt_on is not None and on != self._expect_ptt_on:
            return
        if on:
            self._on_tx_ready()
        else:
            self._on_rx_ready()

    @Slot(str)
    def _on_ptt_failed(self, message: str) -> None:
        self.error.emit(message)
        self._pre_roll_timer.stop()
        self._gap_timer.stop()
        self._tick_timer.stop()
        if self._player is not None:
            self._player.stop()
        self._set_state(PlayerState.IDLE)
        self.status_message.emit("Fehler")

    def _on_tx_ready(self) -> None:
        if self._state != PlayerState.WAITING_TX:
            return
        path = self._current_path()
        if path is None or self._player is None:
            self._set_state(PlayerState.IDLE)
            return
        self._set_state(PlayerState.PLAYING)
        self.status_message.emit("Sendung — Wiedergabe")
        if self._resume_after_pause:
            self._resume_after_pause = False
            self._pending_media_play = False
            self._player.play()
            self._tick_timer.start()
        else:
            url = QUrl.fromLocalFile(str(path.resolve()))
            if self._try_play_loaded_url(url):
                return
            self._pending_media_play = True
            self._player.setSource(url)

    def _on_rx_ready(self) -> None:
        action = self._after_rx
        self._after_rx = "idle"

        if action == "stop":
            self._set_state(PlayerState.IDLE)
            self.status_message.emit("Gestoppt")
            return

        if action == "paused":
            self._set_state(PlayerState.PAUSED_RX)
            self.status_message.emit("Hörpause (RX)")
            return

        if action == "gap":
            self._set_state(PlayerState.GAP)
            self.status_message.emit(f"Pause {self._gap_ms} ms (RX) …")
            if self._gap_ms <= 0:
                self._on_gap_done()
            else:
                self._gap_timer.start(self._gap_ms)
            return

        self._set_state(PlayerState.IDLE)
        self.status_message.emit("Bereit (RX)")

    def _on_gap_done(self) -> None:
        if self._state != PlayerState.GAP:
            return
        self._begin_pre_roll()

    def _try_play_loaded_url(self, url: QUrl) -> bool:
        """Dieselbe URL erneut abspielen, wenn Qt kein erneutes LoadedMedia sendet."""
        if self._player is None:
            return False
        if self._player.source() != url:
            return False
        if self._player.mediaStatus() not in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
            QMediaPlayer.MediaStatus.EndOfMedia,
        ):
            return False
        self._pending_media_play = False
        self._player.setPosition(0)
        self._player.play()
        self._tick_timer.start()
        return True

    def _on_media_status(self, status) -> None:
        if not _MULTIMEDIA_AVAILABLE or self._player is None:
            return

        if self._pending_media_play and self._state == PlayerState.PLAYING:
            if status in (
                QMediaPlayer.MediaStatus.LoadedMedia,
                QMediaPlayer.MediaStatus.BufferedMedia,
            ):
                self._pending_media_play = False
                self._player.play()
                self._tick_timer.start()
                return
            if status == QMediaPlayer.MediaStatus.InvalidMedia:
                self._pending_media_play = False
                self.error.emit(
                    self._player.errorString() or "Audiodatei konnte nicht geladen werden."
                )
                self.stop()
                return

        if status != QMediaPlayer.MediaStatus.EndOfMedia:
            return
        if self._state != PlayerState.PLAYING:
            return
        self._tick_timer.stop()
        self._player.stop()
        self._resume_after_pause = False
        if self._mode == "playlist" and self._index + 1 < len(self._paths):
            self._index += 1
            self._after_rx = "gap"
        else:
            self._after_rx = "idle"
        self.status_message.emit("Datei Ende — RX …")
        self._goto_waiting_rx()

    def _on_media_error(self, _error, message: str = "") -> None:
        if self._state == PlayerState.IDLE:
            return
        self.error.emit(message or "Wiedergabefehler")
        self.stop()

    def _emit_position(self) -> None:
        if self._player is None:
            return
        pos = int(self._player.position() or 0)
        dur = int(self._player.duration() or 0)
        if dur < 0:
            dur = 0
        self.position_changed.emit(pos, dur)

    def _current_path(self) -> Optional[Path]:
        if 0 <= self._index < len(self._paths):
            return self._paths[self._index]
        return None

    def _set_state(self, state: PlayerState) -> None:
        if self._state != state:
            self._state = state
            self.state_changed.emit(state)
