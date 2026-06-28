"""Stereo-Aufnahme (L=TX, R=RX) aus Live-Engine-Taps."""

from __future__ import annotations

import time
import wave
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Optional

import numpy as np

from PySide6.QtCore import QObject, Signal

from i18n import tr
from model.audio_recorder_settings import build_recording_filename

from .audio_recorder import RecorderState, post_process_wav_to_mp3

_INT16_MAX = 32767


def _float_mono_to_int16(mono: Any) -> np.ndarray:
    arr = np.asarray(mono, dtype=np.float32).reshape(-1)
    np.clip(arr, -1.0, 1.0, out=arr)
    return (arr * _INT16_MAX).astype(np.int16)


class LiveDualRecorder(QObject):
    """Nimmt TX/RX-Mono-Blöcke von :class:`live.live_audio_engine.LiveAudioEngine` auf."""

    state_changed = Signal(object)
    duration_changed = Signal(int)
    error = Signal(str)
    file_finalized = Signal(object)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._state = RecorderState.IDLE
        self._lock = Lock()
        self._tx_carry = np.zeros(0, dtype=np.float32)
        self._rx_carry = np.zeros(0, dtype=np.float32)
        self._wav: Optional[wave.Wave_write] = None
        self._sample_rate = 48000
        self._total_samples = 0
        self._start_mono: Optional[float] = None
        self._current_path: Optional[Path] = None
        self._current_wav_path: Optional[Path] = None
        self._current_bitrate_kbps = 64

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
        """Stereo Live-Aufnahme: Normalisierung bleibt aus (TX/RX getrennt)."""
        del enabled

    def start(
        self,
        folder: Path,
        *,
        sample_rate: int,
        bitrate_kbps: int = 64,
        now: Optional[datetime] = None,
    ) -> Optional[Path]:
        if self.is_busy():
            self.error.emit(tr("recorder.error.already_recording"))
            return None
        folder = Path(folder)
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.error.emit(tr("recorder.error.folder_create", exc=exc))
            return None

        filename = build_recording_filename(now=now)
        target_mp3 = folder / filename
        wav_tmp = target_mp3.with_suffix(".wav.tmp")

        sr = max(8000, int(sample_rate))
        try:
            wf = wave.open(str(wav_tmp.resolve()), "wb")
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(sr)
        except OSError as exc:
            self.error.emit(tr("recorder.error.start_failed", exc=exc))
            return None

        with self._lock:
            self._wav = wf
            self._sample_rate = sr
            self._tx_carry = np.zeros(0, dtype=np.float32)
            self._rx_carry = np.zeros(0, dtype=np.float32)
            self._total_samples = 0
            self._start_mono = time.monotonic()
            self._current_path = target_mp3
            self._current_wav_path = wav_tmp
            self._current_bitrate_kbps = int(bitrate_kbps)

        self._set_state(RecorderState.RECORDING)
        return target_mp3

    def stop(self) -> None:
        if self._state not in (
            RecorderState.RECORDING,
            RecorderState.STARTING,
        ):
            return
        self._set_state(RecorderState.STOPPING)
        self._finalize_recording()

    def shutdown(self) -> None:
        if self.is_busy():
            self.stop()

    def on_tx_block(self, mono: Any, sample_rate: float) -> None:
        del sample_rate
        if self._state != RecorderState.RECORDING:
            return
        chunk = np.asarray(mono, dtype=np.float32).reshape(-1)
        with self._lock:
            if self._wav is None:
                return
            self._tx_carry = np.concatenate([self._tx_carry, chunk])
            self._flush_stereo_pairs()

    def on_rx_block(self, mono: Any, sample_rate: float) -> None:
        del sample_rate
        if self._state != RecorderState.RECORDING:
            return
        chunk = np.asarray(mono, dtype=np.float32).reshape(-1)
        with self._lock:
            if self._wav is None:
                return
            self._rx_carry = np.concatenate([self._rx_carry, chunk])
            self._flush_stereo_pairs()

    def on_pair(self, tx: Any, rx: Any, sample_rate: float) -> None:
        """Zeitlich abgestimmtes Stereo-Frame (L=TX, R=RX) — bevorzugter Pfad."""
        del sample_rate
        if self._state != RecorderState.RECORDING:
            return
        tx_a = np.asarray(tx, dtype=np.float32).reshape(-1)
        rx_a = np.asarray(rx, dtype=np.float32).reshape(-1)
        n = max(tx_a.shape[0], rx_a.shape[0])
        if n <= 0:
            return
        if tx_a.shape[0] != n:
            tx_a = self._pad_or_take(tx_a, n)
        if rx_a.shape[0] != n:
            rx_a = self._pad_or_take(rx_a, n)
        tx_i = _float_mono_to_int16(tx_a)
        rx_i = _float_mono_to_int16(rx_a)
        stereo = np.empty(n * 2, dtype=np.int16)
        stereo[0::2] = tx_i
        stereo[1::2] = rx_i
        with self._lock:
            wf = self._wav
            if wf is None:
                return
            wf.writeframes(stereo.tobytes())
            self._total_samples += n
            if self._start_mono is not None:
                elapsed_ms = int((time.monotonic() - self._start_mono) * 1000)
                self.duration_changed.emit(elapsed_ms)

    def _flush_stereo_pairs(self) -> None:
        wf = self._wav
        if wf is None:
            return
        n = min(self._tx_carry.shape[0], self._rx_carry.shape[0])
        if n <= 0:
            return
        tx = self._tx_carry[:n]
        rx = self._rx_carry[:n]
        self._tx_carry = self._tx_carry[n:]
        self._rx_carry = self._rx_carry[n:]
        tx_i = _float_mono_to_int16(tx)
        rx_i = _float_mono_to_int16(rx)
        stereo = np.empty(n * 2, dtype=np.int16)
        stereo[0::2] = tx_i
        stereo[1::2] = rx_i
        wf.writeframes(stereo.tobytes())
        self._total_samples += n
        if self._start_mono is not None:
            elapsed_ms = int((time.monotonic() - self._start_mono) * 1000)
            self.duration_changed.emit(elapsed_ms)

    @staticmethod
    def _pad_or_take(carry: np.ndarray, n: int) -> np.ndarray:
        if carry.shape[0] >= n:
            return carry[:n]
        if carry.shape[0] == 0:
            return np.zeros(n, dtype=np.float32)
        out = np.zeros(n, dtype=np.float32)
        out[: carry.shape[0]] = carry
        return out

    def _finalize_recording(self) -> None:
        mp3_path = self._current_path
        wav_path = self._current_wav_path
        bitrate = self._current_bitrate_kbps

        with self._lock:
            if self._wav is not None:
                self._flush_stereo_pairs()
                if self._tx_carry.shape[0] > 0 or self._rx_carry.shape[0] > 0:
                    n = max(self._tx_carry.shape[0], self._rx_carry.shape[0])
                    tx = self._pad_or_take(self._tx_carry, n)
                    rx = self._pad_or_take(self._rx_carry, n)
                    self._tx_carry = np.zeros(0, dtype=np.float32)
                    self._rx_carry = np.zeros(0, dtype=np.float32)
                    tx_i = _float_mono_to_int16(tx)
                    rx_i = _float_mono_to_int16(rx)
                    stereo = np.empty(n * 2, dtype=np.int16)
                    stereo[0::2] = tx_i
                    stereo[1::2] = rx_i
                    self._wav.writeframes(stereo.tobytes())
                    self._total_samples += n
                try:
                    self._wav.close()
                except Exception:
                    pass
                self._wav = None

        self._current_path = None
        self._current_wav_path = None
        self._start_mono = None

        if mp3_path is None or wav_path is None:
            self._set_state(RecorderState.IDLE)
            return

        if self._total_samples <= 0:
            try:
                wav_path.unlink(missing_ok=True)
            except OSError:
                pass
            self.error.emit(tr("recorder.error.empty"))
            self._set_state(RecorderState.IDLE)
            return

        self._set_state(RecorderState.POST_PROCESSING)
        try:
            post_process_wav_to_mp3(
                wav_path=wav_path,
                mp3_path=mp3_path,
                bitrate_kbps=bitrate,
                normalize=False,
            )
        except Exception as exc:  # noqa: BLE001
            self.error.emit(tr("recorder.error.post_process", exc=exc))
            try:
                wav_path.unlink(missing_ok=True)
            except OSError:
                pass
            self._set_state(RecorderState.IDLE)
            return

        self._set_state(RecorderState.IDLE)
        self.file_finalized.emit(mp3_path)

    def _set_state(self, state: RecorderState) -> None:
        if self._state != state:
            self._state = state
            self.state_changed.emit(state)
