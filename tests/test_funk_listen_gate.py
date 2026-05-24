"""Tests für das Funk-Rückweg-Rauschgate."""

from __future__ import annotations

import numpy as np

from live.live_audio_engine import LiveAudioEngine
from model.live_settings import LiveFunkListenGateSettings, LiveSettings


def test_funk_listen_gate_passes_loud_signal_at_unity() -> None:
    from live.live_dsp import FunkListenNoiseGateState
    from model.live_settings import LiveGateSettings

    fs = 48000.0
    n = 256
    cfg = LiveGateSettings(
        enabled=True,
        threshold_db=-56.0,
        hold_ms=60.0,
        release_ms=120.0,
    )
    gate = FunkListenNoiseGateState()
    loud = np.full(n, 0.12, dtype=np.float32)

    for _ in range(5):
        out = gate.process(loud, fs, cfg)

    assert float(np.max(np.abs(out))) == float(np.max(np.abs(loud)))


def test_funk_listen_gate_blocks_quiet_hiss() -> None:
    eng = LiveAudioEngine()
    eng._reset_funk_listen_gate()
    ls = LiveSettings()
    ls.funk_listen_gate = LiveFunkListenGateSettings(
        enabled=True,
        threshold_db=-50.0,
        attack_ms=1.0,
        hold_ms=20.0,
        release_ms=80.0,
    )
    ls.funk_listen_gain = 1.0
    ls.samplerate = 48000

    n = 256
    quiet = np.full((n, 1), 0.0003, dtype=np.float32)
    loud = np.full((n, 1), 0.08, dtype=np.float32)

    for _ in range(80):
        eng._process_funk_listen_block(quiet, ls, n, 1)
    quiet_out = eng._process_funk_listen_block(quiet, ls, n, 1)
    quiet_peak = float(np.max(np.abs(quiet_out)))

    for _ in range(80):
        eng._process_funk_listen_block(loud, ls, n, 1)
    loud_out = eng._process_funk_listen_block(loud, ls, n, 1)
    loud_peak = float(np.max(np.abs(loud_out)))

    assert quiet_peak < loud_peak * 0.2
    assert loud_peak > 0.01


def test_funk_listen_gate_effective_respects_tuning_flag() -> None:
    from live import funk_listen_gate as flg

    if flg.SHOW_TUNING_UI:
        g = LiveFunkListenGateSettings.effective({"threshold_db": -48.0})
        assert abs(g.threshold_db + 48.0) < 0.01
    else:
        g = LiveFunkListenGateSettings.effective({"threshold_db": -48.0})
        assert abs(g.threshold_db - flg.FIXED_THRESHOLD_DB) < 0.01
        assert abs(g.hold_ms - flg.FIXED_HOLD_MS) < 0.01
        assert abs(g.release_ms - flg.FIXED_RELEASE_MS) < 0.01


def test_funk_listen_gate_settings_roundtrip() -> None:
    raw = {
        "enabled": True,
        "threshold_db": -56.5,
        "attack_ms": 2.0,
        "hold_ms": 40.0,
        "release_ms": 100.0,
    }
    g = LiveFunkListenGateSettings.from_dict(raw)
    assert g.enabled is True
    assert abs(g.threshold_db + 56.5) < 0.01
    gate = g.to_gate_settings()
    assert gate.enabled is True
