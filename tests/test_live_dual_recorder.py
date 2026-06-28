"""Tests für LiveDualRecorder (Stereo L=TX, R=RX)."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from audio.audio_recorder import RecorderState
from audio.live_dual_recorder import LiveDualRecorder, _float_mono_to_int16


def test_float_mono_to_int16_clips() -> None:
    out = _float_mono_to_int16(np.array([1.5, -1.5, 0.0], dtype=np.float32))
    assert out[0] == 32767
    assert out[1] == -32767
    assert out[2] == 0


def test_live_dual_recorder_stereo_wav(tmp_path: Path) -> None:
    rec = LiveDualRecorder()
    folder = tmp_path / "rec"
    folder.mkdir()
    target = rec.start(folder, sample_rate=48000, bitrate_kbps=64)
    assert target is not None
    assert rec.state == RecorderState.RECORDING

    tx = np.ones(256, dtype=np.float32) * 0.5
    rx = np.ones(256, dtype=np.float32) * 0.25
    rec.on_pair(tx, rx, 48000.0)

    rec.stop()
    assert rec.state == RecorderState.IDLE
    assert target.is_file()
    assert target.suffix == ".mp3"
    assert not target.with_suffix(".wav.tmp").exists()


def test_live_dual_recorder_tx_only_zeros_rx(tmp_path: Path) -> None:
    rec = LiveDualRecorder()
    folder = tmp_path / "rec2"
    folder.mkdir()
    target = rec.start(folder, sample_rate=16000, bitrate_kbps=64)
    assert target is not None
    rec.on_pair(np.full(128, 0.8, dtype=np.float32), np.zeros(128, dtype=np.float32), 16000.0)
    rec.stop()
    assert target.is_file()


def test_record_tx_tap_ignores_funk_send_gain() -> None:
    from live.live_audio_engine import LiveAudioEngine

    y = np.full(64, 0.4, dtype=np.float32)
    tap = LiveAudioEngine._fit_record_tx_tap(y, 64)
    assert np.max(np.abs(tap)) == 0.4
    funk_hot = LiveAudioEngine._fit_mono_to_frames(y * 2.0, 64)
    assert np.max(np.abs(funk_hot)) == 0.8
