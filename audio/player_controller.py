"""Steuerlogik Audio-Player mit CAT-PTT und Qt Multimedia."""

from __future__ import annotations

import enum
import os
from pathlib import Path
from typing import Literal, Optional

from PySide6.QtCore import QMetaObject, QObject, QThread, Qt, QTimer, QUrl, Signal, Slot, Q_ARG

from cat import SerialCAT
from model.audio_player_settings import PlaybackMode

from .cat_ptt_worker import CatPttWorker
from .qt_multimedia_lazy import qt_multimedia_types

_MULTIMEDIA_IMPORT = False
_MULTIMEDIA_AVAILABLE = False

AfterRx = Literal["idle", "paused", "gap", "stop", "contest_pause", "single_voice"]


class PlayerState(enum.Enum):
    IDLE = "idle"
    PRE_ROLL = "pre_roll"
    WAITING_TX = "waiting_tx"
    PLAYING = "playing"
    PAUSED_RX = "paused_rx"
    WAITING_RX = "waiting_rx"
    GAP = "gap"
    #: Lange Hörpause im Kontest-Loop — Funkgerät wird auf Sprach-Mode
    #: geschaltet, damit Stationen antworten können.
    LISTEN_PAUSE = "listen_pause"
    STOPPING = "stopping"


def multimedia_available() -> bool:
    return bool(_MULTIMEDIA_IMPORT and _MULTIMEDIA_AVAILABLE)


def ensure_playback_backend(parent: Optional[QObject] = None) -> bool:
    """Multimedia-Backend prüfen/initialisieren (auch ohne Audio-Player-Fenster)."""
    global _MULTIMEDIA_IMPORT, _MULTIMEDIA_AVAILABLE

    if multimedia_available():
        return True

    mm = qt_multimedia_types()
    if mm is None:
        _MULTIMEDIA_IMPORT = False
        _MULTIMEDIA_AVAILABLE = False
        return False

    _QAudioOutput, _QMediaDevices, QMediaPlayer = mm
    probe_parent = parent if parent is not None else QObject()
    probe = QMediaPlayer(probe_parent)
    ok = _player_backend_ok(probe, QMediaPlayer)
    try:
        probe.deleteLater()
    except Exception:
        pass
    _MULTIMEDIA_IMPORT = True
    _MULTIMEDIA_AVAILABLE = ok
    return ok


def _player_backend_ok(player: object, qmedia_player_cls: type) -> bool:
    if player is None:
        return False
    err = player.error()  # type: ignore[union-attr]
    return err == qmedia_player_cls.Error.NoError  # type: ignore[union-attr]


def list_audio_output_devices() -> list[tuple[str, str]]:
    """(id, Anzeigename) — leere id = System-Standard."""
    mm = qt_multimedia_types()
    if mm is None:
        return [("", "Qt Multimedia nicht verfügbar")]
    _QAudioOutput, QMediaDevices, _QMediaPlayer = mm
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
    #: Sprach-Mode (USB/LSB/FM) — nach Stopp oder Einzeldatei-Ende.
    voice_mode_requested = Signal()
    #: Kontest: Hörpause zu Ende — Vorlauf erst nach DATA-Mode (Fenster entscheidet).
    contest_pre_roll_requested = Signal()

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
        #: Sende-Out zusätzlich auf PC-Ausgabegerät (Mithören, zweiter Player).
        self._tx_monitor_pc_enabled = False
        self._tx_monitor_pc_device_id = ""
        self._tx_monitor_pc_volume_percent = 100
        self._monitor_player = None
        self._monitor_audio_out = None
        #: Kontest-Loop: dieselbe Datei wiederholen mit langer Hörpause.
        self._contest_mode = False
        self._contest_listen_pause_ms = 5000

        self._pre_roll_timer = QTimer(self)
        self._pre_roll_timer.setSingleShot(True)
        self._pre_roll_timer.timeout.connect(self._on_pre_roll_done)

        self._gap_timer = QTimer(self)
        self._gap_timer.setSingleShot(True)
        self._gap_timer.timeout.connect(self._on_gap_done)

        self._contest_pause_timer = QTimer(self)
        self._contest_pause_timer.setSingleShot(True)
        self._contest_pause_timer.timeout.connect(self._on_contest_pause_done)

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
        self._preview_loading = False

        self._player = None
        self._audio_out = None
        self._media_ok = False
        self._QMediaPlayer: Optional[type] = None
        self._init_multimedia()

    def shutdown(self) -> None:
        self._stop_monitor_playback()
        self.stop()
        self._ptt_thread.quit()
        self._ptt_thread.wait(3000)

    def _init_multimedia(self) -> None:
        """QMediaPlayer erst mit laufender QApplication initialisieren."""
        global _MULTIMEDIA_IMPORT, _MULTIMEDIA_AVAILABLE

        if self._media_ok:
            return

        mm = qt_multimedia_types()
        if mm is None:
            _MULTIMEDIA_IMPORT = False
            _MULTIMEDIA_AVAILABLE = False
            return

        QAudioOutput, QMediaDevices, QMediaPlayer = mm
        self._QMediaPlayer = QMediaPlayer
        _MULTIMEDIA_IMPORT = True

        self._player = QMediaPlayer(self)
        if not _player_backend_ok(self._player, QMediaPlayer):
            err = self._player.errorString()
            self._player.deleteLater()
            self._player = None
            _MULTIMEDIA_AVAILABLE = False
            backend = os.environ.get("QT_MEDIA_BACKEND", "?")
            self.status_message.emit(
                f"QMediaPlayer nicht verfügbar ({err}, Backend={backend}). "
                "pip install PySide6-Addons, App neu starten. "
                "Dev: QT_MEDIA_BACKEND=windows setzen."
            )
            return

        _MULTIMEDIA_AVAILABLE = True
        self._media_ok = True
        self._audio_out = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_out)
        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._player.errorOccurred.connect(self._on_media_error)
        self._apply_output_device()
        self._apply_volume()

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

    def load_track(self, index: Optional[int] = None) -> None:
        """Datei laden (ohne Sendung) — Dauer/Position fuer Vorab-Spulen."""
        if index is not None:
            if index < 0 or index >= len(self._paths):
                return
            self._index = index
        if self.is_busy():
            return
        self._stop_monitor_playback()
        path = self._current_path()
        if path is None:
            self.position_changed.emit(0, 0)
            return
        if not self._media_ok:
            self._init_multimedia()
        if not self._media_ok or self._player is None:
            return

        url = QUrl.fromLocalFile(str(path.resolve()))
        self.current_file_changed.emit(path.name)
        QMP = self._QMediaPlayer
        if (
            QMP is not None
            and self._state == PlayerState.IDLE
            and self._player.source() == url
            and self._player.mediaStatus()
            in (
                QMP.MediaStatus.LoadedMedia,
                QMP.MediaStatus.BufferedMedia,
                QMP.MediaStatus.EndOfMedia,
            )
        ):
            self._emit_position()
            return

        self._preview_loading = True
        self._pending_media_play = False
        self._player.stop()
        self._tick_timer.stop()
        self._player.setSource(url)

    def set_timing(self, pre_roll_ms: int, gap_between_files_ms: int) -> None:
        self._pre_roll_ms = max(0, int(pre_roll_ms))
        self._gap_ms = max(0, int(gap_between_files_ms))

    def set_playback_mode(self, mode: PlaybackMode) -> None:
        self._mode = mode

    def set_contest_mode(self, enabled: bool, listen_pause_ms: int) -> None:
        """Kontest-Loop ein/aus + Hörpause-Dauer (ms)."""
        self._contest_mode = bool(enabled)
        self._contest_listen_pause_ms = max(0, int(listen_pause_ms))

    @property
    def contest_mode(self) -> bool:
        return self._contest_mode

    def set_output_device_id(self, device_id: str) -> None:
        self._output_device_id = device_id or ""
        self._apply_output_device()

    def set_volume_percent(self, percent: int) -> None:
        self._volume_percent = max(0, min(100, int(percent)))
        self._apply_volume()

    def volume_percent(self) -> int:
        return self._volume_percent

    def set_tx_monitor_to_pc_enabled(self, enabled: bool) -> None:
        """CAT-Sendewiedergabe zusätzlich auf dem PC-Ausgabegerät mithören."""
        self._tx_monitor_pc_enabled = bool(enabled)
        if not self._tx_monitor_pc_enabled:
            self._stop_monitor_playback()
        elif self._state == PlayerState.PLAYING:
            self._sync_monitor_with_main_playback()

    def set_tx_monitor_pc_device_id(self, device_id: str) -> None:
        self._tx_monitor_pc_device_id = device_id or ""
        self._apply_monitor_device()
        if self._tx_monitor_pc_enabled and self._state == PlayerState.PLAYING:
            self._sync_monitor_with_main_playback()

    def set_tx_monitor_pc_volume_percent(self, percent: int) -> None:
        self._tx_monitor_pc_volume_percent = max(0, min(100, int(percent)))
        self._apply_monitor_volume()

    def _apply_output_device(self) -> None:
        if not _MULTIMEDIA_AVAILABLE or self._audio_out is None:
            return
        mm = qt_multimedia_types()
        if mm is None:
            return
        _QAudioOutput, QMediaDevices, _QMediaPlayer = mm
        log = self._cat.get_log() if self._cat else None
        if not self._output_device_id:
            chosen = QMediaDevices.defaultAudioOutput()
            self._audio_out.setDevice(chosen)
            if log is not None:
                log.log_info(
                    f"Audio-Out: System-Standard → "
                    f"'{chosen.description()}'"
                )
        else:
            matched_dev = None
            available = []
            for dev in QMediaDevices.audioOutputs():
                dev_id = dev.id().data().decode("utf-8", errors="replace")
                available.append((dev_id, dev.description()))
                if dev_id == self._output_device_id:
                    matched_dev = dev
                    break
            if matched_dev is not None:
                self._audio_out.setDevice(matched_dev)
                if log is not None:
                    log.log_info(
                        f"Audio-Out: gewählt '{matched_dev.description()}' "
                        f"(id={self._output_device_id})"
                    )
            else:
                fallback = QMediaDevices.defaultAudioOutput()
                self._audio_out.setDevice(fallback)
                if log is not None:
                    log.log_warn(
                        f"Audio-Out: gespeicherte Geräte-ID "
                        f"'{self._output_device_id}' NICHT gefunden → "
                        f"Fallback System-Standard '{fallback.description()}'. "
                        f"Verfügbar wären: {available}"
                    )
        self._apply_volume()

    def _apply_volume(self) -> None:
        if self._audio_out is not None:
            self._audio_out.setVolume(self._volume_percent / 100.0)

    def _init_monitor_player(self) -> None:
        if self._monitor_player is not None:
            return
        if not self._media_ok or self._player is None:
            return
        mm = qt_multimedia_types()
        if mm is None:
            return
        QAudioOutput, QMediaDevices, QMediaPlayer = mm
        self._monitor_audio_out = QAudioOutput(self)
        self._monitor_player = QMediaPlayer(self)
        if not _player_backend_ok(self._monitor_player, QMediaPlayer):
            err = self._monitor_player.errorString()
            self._monitor_player.deleteLater()
            self._monitor_player = None
            self._monitor_audio_out.deleteLater()
            self._monitor_audio_out = None
            self.status_message.emit(
                f"Mithören PC: kein zweiter Player ({err})."
            )
            return
        self._monitor_player.setAudioOutput(self._monitor_audio_out)
        self._monitor_player.errorOccurred.connect(self._on_monitor_media_error)
        self._apply_monitor_device()
        self._apply_monitor_volume()

    def _apply_monitor_device(self) -> None:
        if not _MULTIMEDIA_AVAILABLE or self._monitor_audio_out is None:
            return
        mm = qt_multimedia_types()
        if mm is None:
            return
        _QAudioOutput, QMediaDevices, _QMediaPlayer = mm
        if not self._tx_monitor_pc_device_id:
            self._monitor_audio_out.setDevice(QMediaDevices.defaultAudioOutput())
            return
        for dev in QMediaDevices.audioOutputs():
            dev_id = dev.id().data().decode("utf-8", errors="replace")
            if dev_id == self._tx_monitor_pc_device_id:
                self._monitor_audio_out.setDevice(dev)
                return
        self._monitor_audio_out.setDevice(QMediaDevices.defaultAudioOutput())

    def _apply_monitor_volume(self) -> None:
        if self._monitor_audio_out is not None:
            try:
                self._monitor_audio_out.setVolume(
                    self._tx_monitor_pc_volume_percent / 100.0
                )
            except (AttributeError, TypeError):
                pass

    def _stop_monitor_playback(self) -> None:
        if self._monitor_player is None:
            return
        try:
            self._monitor_player.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._monitor_player.setSource(QUrl())
        except Exception:  # noqa: BLE001
            pass

    def _sync_monitor_with_main_playback(self) -> None:
        """Hauptplayer sendet — optional identisches Signal auf PC-Gerät."""
        if not self._tx_monitor_pc_enabled:
            self._stop_monitor_playback()
            return
        if self._state != PlayerState.PLAYING:
            return
        if not _MULTIMEDIA_AVAILABLE or not self._media_ok or self._player is None:
            return
        path = self._current_path()
        if path is None:
            return
        self._init_monitor_player()
        if self._monitor_player is None:
            return
        url = QUrl.fromLocalFile(str(path.resolve()))
        pos = int(self._player.position() or 0)
        try:
            if self._monitor_player.source() != url:
                self._monitor_player.setSource(url)
            self._monitor_player.setPosition(pos)
            self._monitor_player.play()
        except Exception:  # noqa: BLE001
            pass

    def _on_monitor_media_error(self, _error, message: str = "") -> None:
        self._stop_monitor_playback()
        if message:
            self.status_message.emit(f"Mithören PC: {message}")

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
        if not self._media_ok:
            self._init_multimedia()
        if not _MULTIMEDIA_AVAILABLE or not self._media_ok:
            self.error.emit(
                "Audio-Wiedergabe nicht verfügbar. "
                "pip install PySide6-Addons, App neu starten. "
                "Dev: QT_MEDIA_BACKEND=windows probieren."
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

    def seek_position_ms(self, pos_ms: int) -> None:
        """Wiedergabeposition setzen (IDLE-Vorschau, PLAYING oder PAUSED_RX)."""
        if self._player is None or not self._media_ok:
            return
        if self._state not in (
            PlayerState.IDLE,
            PlayerState.PLAYING,
            PlayerState.PAUSED_RX,
        ):
            return
        pos_ms = max(0, int(pos_ms))
        self._player.setPosition(pos_ms)
        if self._state == PlayerState.PLAYING and self._monitor_player is not None:
            try:
                self._monitor_player.setPosition(pos_ms)
            except Exception:  # noqa: BLE001
                pass
        if self._state in (
            PlayerState.IDLE,
            PlayerState.PLAYING,
            PlayerState.PAUSED_RX,
        ):
            self._emit_position()

    def pause(self) -> None:
        if self._state != PlayerState.PLAYING:
            return
        if self._monitor_player is not None:
            try:
                self._monitor_player.pause()
            except Exception:  # noqa: BLE001
                pass
        if self._player is not None:
            self._player.pause()
        self._tick_timer.stop()
        self._after_rx = "paused"
        self._goto_waiting_rx()

    def release_source(self) -> None:
        """Stoppt + gibt die aktuell geladene Mediendatei frei.

        Wichtig fuer Fall "Datei loeschen": Windows haelt den File-Handle
        auch noch nach ``QMediaPlayer.stop()``, solange ``source()`` auf
        die Datei zeigt. ``setSource(QUrl())`` zwingt das Backend, das
        Handle wirklich zu schliessen, sodass ``unlink()`` durchgeht.

        Nur in Ruhezustand (IDLE/PAUSED_RX) — nicht während CAT-Sendung.
        """
        if self.is_busy() and self._state != PlayerState.PAUSED_RX:
            return
        self._preview_loading = False
        self._pending_media_play = False
        if self._player is None:
            return
        try:
            self._player.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._player.setSource(QUrl())
        except Exception:  # noqa: BLE001
            pass
        self._stop_monitor_playback()

    def stop(self) -> None:
        self._stop_monitor_playback()
        self._pre_roll_timer.stop()
        self._gap_timer.stop()
        self._contest_pause_timer.stop()
        self._tick_timer.stop()
        self._resume_after_pause = False
        self._pending_media_play = False
        if self._player is not None:
            self._player.stop()
        if self._state in (PlayerState.IDLE, PlayerState.PAUSED_RX):
            self._finish_stop_idle()
            if self._player is not None:
                self._emit_position()
            return
        if self._state == PlayerState.PRE_ROLL:
            self._finish_stop_idle()
            return
        if self._state in (PlayerState.GAP, PlayerState.LISTEN_PAUSE):
            self._finish_stop_idle()
            return
        self._after_rx = "stop"
        self._goto_waiting_rx()

    def _finish_stop_idle(self) -> None:
        """Stopp abgeschlossen → IDLE und Sprach-Mode anfordern."""
        self._set_state(PlayerState.IDLE)
        self.status_message.emit("Gestoppt")
        self.voice_mode_requested.emit()

    def _goto_waiting_rx(self) -> None:
        self._set_state(PlayerState.WAITING_RX)
        self._request_ptt(False)

    def begin_pre_roll_now(self) -> None:
        """Vorlauf starten (nach asynchronem CAT, z. B. DATA-Mode beim Kontest)."""
        self._begin_pre_roll()

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
        self._stop_monitor_playback()
        self._set_state(PlayerState.IDLE)
        self.status_message.emit("Fehler")

    def _on_tx_ready(self) -> None:
        if self._state != PlayerState.WAITING_TX:
            return
        path = self._current_path()
        if path is None or self._player is None:
            self._set_state(PlayerState.IDLE)
            return
        # Diagnose: vor dem Wiedergabestart das tatsächlich verwendete
        # Output-Device + die Software-Lautstärke loggen. Wenn die App den
        # Träger ohne Modulation sendet, sieht man hier ob das Audio in
        # die richtige (USB-)Soundkarte des Funkgeräts geroutet wird.
        log = self._cat.get_log() if self._cat else None
        if log is not None:
            dev_desc = "?"
            backend_vol = "?"
            try:
                if self._audio_out is not None:
                    dev = self._audio_out.device()
                    dev_desc = dev.description()
                    backend_vol = f"{self._audio_out.volume():.2f}"
            except Exception:  # noqa: BLE001
                pass
            log.log_info(
                f"Replay TX: Datei '{path.name}', "
                f"Output='{dev_desc}', "
                f"App-Lautstärke={self._volume_percent}% "
                f"(Backend={backend_vol})"
            )
        self._set_state(PlayerState.PLAYING)
        self.status_message.emit("Sendung — Wiedergabe")
        if self._resume_after_pause:
            self._resume_after_pause = False
            self._pending_media_play = False
            self._player.play()
            self._tick_timer.start()
            self._sync_monitor_with_main_playback()
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
            self._finish_stop_idle()
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

        if action == "contest_pause":
            self._set_state(PlayerState.LISTEN_PAUSE)
            secs = self._contest_listen_pause_ms / 1000.0
            self.status_message.emit(
                f"Kontest-Hörpause {secs:.1f} s (Sprach-Mode) …"
            )
            if self._contest_listen_pause_ms <= 0:
                self._on_contest_pause_done()
            else:
                self._contest_pause_timer.start(self._contest_listen_pause_ms)
            return

        if action == "single_voice":
            if len(self._paths) > 1:
                self._index = (self._index + 1) % len(self._paths)
            self._set_state(PlayerState.IDLE)
            self.status_message.emit("Datei Ende — Sprach-Mode (MIC)")
            self.voice_mode_requested.emit()
            # Nächste Datei vorladen — Start kann sofort erneut gedrückt werden.
            if self._paths:
                self.load_track()
            return

        if action == "playlist_done_voice":
            self._set_state(PlayerState.IDLE)
            self.status_message.emit("Playlist Ende — Sprach-Mode (MIC)")
            self.voice_mode_requested.emit()
            if self._paths:
                self.load_track()
            return

        self._set_state(PlayerState.IDLE)
        self.status_message.emit("Bereit (RX)")

    def _on_gap_done(self) -> None:
        if self._state != PlayerState.GAP:
            return
        self._begin_pre_roll()

    def _on_contest_pause_done(self) -> None:
        if self._state != PlayerState.LISTEN_PAUSE:
            return
        self.contest_pre_roll_requested.emit()

    def _try_play_loaded_url(self, url: QUrl) -> bool:
        """Dieselbe URL erneut abspielen, wenn Qt kein erneutes LoadedMedia sendet."""
        if self._player is None:
            return False
        if self._player.source() != url:
            return False
        QMP = self._QMediaPlayer
        if QMP is None:
            return False
        if self._player.mediaStatus() not in (
            QMP.MediaStatus.LoadedMedia,
            QMP.MediaStatus.BufferedMedia,
            QMP.MediaStatus.EndOfMedia,
        ):
            return False
        self._pending_media_play = False
        self._player.play()
        self._tick_timer.start()
        self._sync_monitor_with_main_playback()
        return True

    def _on_media_status(self, status) -> None:
        if not _MULTIMEDIA_AVAILABLE or self._player is None:
            return

        QMP = self._QMediaPlayer
        if QMP is None:
            return

        if self._preview_loading and self._state == PlayerState.IDLE:
            if status in (
                QMP.MediaStatus.LoadedMedia,
                QMP.MediaStatus.BufferedMedia,
            ):
                self._preview_loading = False
                self._emit_position()
                return
            if status == QMP.MediaStatus.InvalidMedia:
                self._preview_loading = False
                self.error.emit(
                    self._player.errorString() or "Audiodatei konnte nicht geladen werden."
                )
                return

        if self._pending_media_play and self._state == PlayerState.PLAYING:
            if status in (
                QMP.MediaStatus.LoadedMedia,
                QMP.MediaStatus.BufferedMedia,
            ):
                self._pending_media_play = False
                self._player.play()
                self._tick_timer.start()
                self._sync_monitor_with_main_playback()
                return
            if status == QMP.MediaStatus.InvalidMedia:
                self._pending_media_play = False
                self.error.emit(
                    self._player.errorString() or "Audiodatei konnte nicht geladen werden."
                )
                self.stop()
                return

        if status != QMP.MediaStatus.EndOfMedia:
            return
        if self._state != PlayerState.PLAYING:
            return
        self._tick_timer.stop()
        self._stop_monitor_playback()
        self._player.stop()
        self._resume_after_pause = False
        if self._contest_mode:
            # Kontest-Loop: dieselbe Datei nochmal nach Hörpause.
            self._after_rx = "contest_pause"
        elif self._mode == "playlist" and self._index + 1 < len(self._paths):
            # Playlist: nur kurze Pause, kein Sprach-Mode.
            self._index += 1
            self._after_rx = "gap"
        elif self._mode == "single":
            # Einzeldatei: stoppen und auf Sprach-Mode (MIC vorne).
            self._after_rx = "single_voice"
        else:
            # Letzter Titel der Playlist: Sprach-Mode, Index bleibt (letzte Datei).
            self._after_rx = "playlist_done_voice"
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
        if (
            self._state == PlayerState.PLAYING
            and self._tx_monitor_pc_enabled
            and self._monitor_player is not None
        ):
            try:
                mpos = int(self._monitor_player.position() or 0)
                if abs(mpos - pos) > 300:
                    self._monitor_player.setPosition(pos)
            except Exception:  # noqa: BLE001
                pass
        self.position_changed.emit(pos, dur)

    def _current_path(self) -> Optional[Path]:
        if 0 <= self._index < len(self._paths):
            return self._paths[self._index]
        return None

    def _set_state(self, state: PlayerState) -> None:
        if self._state != state:
            self._state = state
            self.state_changed.emit(state)
