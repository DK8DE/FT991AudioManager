"""Unit-Tests ohne Audiostream (nur Hüllkurven-/Hilfen)."""

from __future__ import annotations

import numpy as np

from live.live_dsp import NoiseGateState, _smooth_coef_for_block, linear_to_db
from model.live_settings import LiveGateSettings


def test_smooth_coef_bounded() -> None:
    """Block-Koeffizient 0<t<1 bei typischen Werten."""
    c = _smooth_coef_for_block(50.0, 256 / 48000.0)
    assert 0.0 < float(c) < 1.0


def test_linear_to_db_floor() -> None:
    assert linear_to_db(0.0) < -119.0


def test_noise_gate_closes_after_signal_drops() -> None:
    """Nach lautem Pegel und anschließender Stille soll das Gate wieder zugehen."""
    fs = 48000.0
    n = int(0.01 * fs)
    cfg = LiveGateSettings(
        enabled=True,
        threshold_db=-30.0,
        attack_ms=1.0,
        hold_ms=10.0,
        release_ms=20.0,
    )
    gate = NoiseGateState()
    loud = np.ones(n, dtype=np.float32) * 0.3
    silence = np.zeros(n, dtype=np.float32)

    for _ in range(60):
        gate.process(loud, fs, cfg)
    assert gate.smoothed_gate > 0.5

    for _ in range(800):
        gate.process(silence, fs, cfg)

    assert gate.smoothed_gate < 0.05, (
        "Gate bleibt offen unter Schwelle — prüfe gate_open ohne smoothed_gate-Fallback"
    )


def test_mic_dsp_peak_dbfs_follows_gate() -> None:
    """Mic‑Send‑Pegel (Vorschau) fällt nach Gate‑Schließen unter lauten Pegel."""
    import numpy as np

    from live.live_audio_engine import LiveAudioEngine
    from model.live_settings import LiveGateSettings, LiveSettings

    eng = LiveAudioEngine()
    ls = LiveSettings()
    ls.gate = LiveGateSettings(
        enabled=True,
        threshold_db=-20.0,
        attack_ms=1.0,
        hold_ms=5.0,
        release_ms=15.0,
    )
    ls.eq_enabled = False
    ls.compressor.enabled = False

    n = 480
    loud = np.ones((n, 1), dtype=np.float32) * 0.5
    silence = np.zeros((n, 1), dtype=np.float32)

    for _ in range(30):
        eng._mic_dsp_peak_dbfs(loud, ls, n)
    open_peak = eng._mic_dsp_peak_dbfs(loud, ls, n)

    closed_peak = open_peak
    for _ in range(400):
        closed_peak = eng._mic_dsp_peak_dbfs(silence, ls, n)

    assert open_peak > -30.0
    assert closed_peak < open_peak - 12.0
