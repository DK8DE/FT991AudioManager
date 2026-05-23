"""Logarithmische Zuordnung Live-Lautstärke-Slider (0–200) ↔ Verstärkung (0–2,0).

Die Slider-Position ist **nicht** linear zur Lautheit: unterhalb 100 % liegt ein
größerer dB-Bereich (−40…0 dB), oberhalb 100 % nur 0…+6 dB bis Faktor 2,0.
100 % auf dem Regler = Verstärkung 1,0 (0 dB), 200 % = 2,0 (+6 dB).
"""

from __future__ import annotations

import math

LIVE_GAIN_SLIDER_MAX = 200
LIVE_GAIN_UNITY_SLIDER = 100
_LIVE_GAIN_DB_FLOOR = -40.0
_LIVE_GAIN_DB_CEIL = 6.0


def live_gain_from_slider(slider: int) -> float:
    """Slider 0…200 → lineare Verstärkung 0…2,0 (logarithmische Wahrnehmung)."""
    s = max(0, min(LIVE_GAIN_SLIDER_MAX, int(slider)))
    if s <= 0:
        return 0.0
    if s <= LIVE_GAIN_UNITY_SLIDER:
        db = _LIVE_GAIN_DB_FLOOR + (s / float(LIVE_GAIN_UNITY_SLIDER)) * (
            -_LIVE_GAIN_DB_FLOOR
        )
    else:
        t = (s - LIVE_GAIN_UNITY_SLIDER) / float(
            LIVE_GAIN_SLIDER_MAX - LIVE_GAIN_UNITY_SLIDER
        )
        db = t * _LIVE_GAIN_DB_CEIL
    gain = 10.0 ** (db / 20.0)
    if s >= LIVE_GAIN_SLIDER_MAX:
        return 2.0
    return min(2.0, max(0.0, float(gain)))


def live_slider_from_gain(gain: float) -> int:
    """Verstärkung 0…2,0 → nächster Slider 0…200 (Invers zu :func:`live_gain_from_slider`)."""
    g = max(0.0, min(2.0, float(gain)))
    if g <= 0.0:
        return 0
    db = 20.0 * math.log10(max(g, 1e-12))
    if db <= 0.0:
        span = -_LIVE_GAIN_DB_FLOOR
        s = (db - _LIVE_GAIN_DB_FLOOR) / span * float(LIVE_GAIN_UNITY_SLIDER)
    else:
        s = float(LIVE_GAIN_UNITY_SLIDER) + (
            db / _LIVE_GAIN_DB_CEIL
        ) * float(LIVE_GAIN_SLIDER_MAX - LIVE_GAIN_UNITY_SLIDER)
    return int(round(max(0, min(LIVE_GAIN_SLIDER_MAX, s))))


def live_gain_display_percent(gain: float) -> int:
    """Anzeige „effektiv %“ (gerundeter linearer Faktor × 100)."""
    return int(round(max(0.0, min(2.0, float(gain))) * 100.0))
