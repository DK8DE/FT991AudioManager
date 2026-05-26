"""Standardwerte für das Funk-Rückweg-Rauschgate (Erstinstallation)."""

from __future__ import annotations

DEFAULT_ENABLED = True
DEFAULT_THRESHOLD_DB = -34.3
DEFAULT_ATTACK_MS = 1.5
DEFAULT_HOLD_MS = 80.0
DEFAULT_RELEASE_MS = 80.0

# Rückwärtskompatibilität für Tests / ältere Referenzen
FIXED_ENABLED = DEFAULT_ENABLED
FIXED_THRESHOLD_DB = DEFAULT_THRESHOLD_DB
FIXED_ATTACK_MS = DEFAULT_ATTACK_MS
FIXED_HOLD_MS = DEFAULT_HOLD_MS
FIXED_RELEASE_MS = DEFAULT_RELEASE_MS


def default_funk_listen_gate_settings() -> "LiveFunkListenGateSettings":
    from model.live_settings import LiveFunkListenGateSettings

    g = LiveFunkListenGateSettings(
        enabled=DEFAULT_ENABLED,
        threshold_db=DEFAULT_THRESHOLD_DB,
        attack_ms=DEFAULT_ATTACK_MS,
        hold_ms=DEFAULT_HOLD_MS,
        release_ms=DEFAULT_RELEASE_MS,
    )
    g.clamp()
    return g


def fixed_funk_listen_gate_settings() -> "LiveFunkListenGateSettings":
    """Alias — früher fest verdrahtet, jetzt Installations-Standard."""
    return default_funk_listen_gate_settings()
