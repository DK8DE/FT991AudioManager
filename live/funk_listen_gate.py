"""Fest verdrahtetes Rauschgate am Funk-Rückweg (Zirpen bei geschlossenem SQL)."""

from __future__ import annotations

SHOW_TUNING_UI = False

FIXED_ENABLED = True
FIXED_THRESHOLD_DB = -34.3
FIXED_ATTACK_MS = 1.5
FIXED_HOLD_MS = 80.0
FIXED_RELEASE_MS = 80.0


def fixed_funk_listen_gate_settings() -> "LiveFunkListenGateSettings":
    from model.live_settings import LiveFunkListenGateSettings

    g = LiveFunkListenGateSettings(
        enabled=FIXED_ENABLED,
        threshold_db=FIXED_THRESHOLD_DB,
        attack_ms=FIXED_ATTACK_MS,
        hold_ms=FIXED_HOLD_MS,
        release_ms=FIXED_RELEASE_MS,
    )
    g.clamp()
    return g
