"""Amateurfunk-Bänder (Region 1 / DE-typisch) für VFO-Anzeige.

Nur für die GUI-Farbmarkierung (grün = im Amateurband, rot = außerhalb).
Die CAT-Bandgrenzen des FT-991/A liegen in :mod:`mapping.vfo_bands`.
"""

from __future__ import annotations

from typing import Optional, Tuple

# (min_hz, max_hz, Kurzname) — Grenzen inklusive
_AMATEUR_BANDS: Tuple[Tuple[int, int, str], ...] = (
    (1_810_000, 1_850_000, "160 m"),
    (3_500_000, 3_800_000, "80 m"),
    (5_351_500, 5_366_500, "60 m"),
    (7_000_000, 7_200_000, "40 m"),
    (10_100_000, 10_150_000, "30 m"),
    (14_000_000, 14_350_000, "20 m"),
    (18_068_000, 18_168_000, "17 m"),
    (21_000_000, 21_450_000, "15 m"),
    (24_890_000, 24_990_000, "12 m"),
    (28_000_000, 29_700_000, "10 m"),
    (50_000_000, 54_000_000, "6 m"),
    (144_000_000, 146_000_000, "2 m"),
    (430_000_000, 440_000_000, "70 cm"),
)


def amateur_band_for_hz(hz: int) -> Optional[str]:
    """Liefert den Bandnamen oder ``None`` wenn außerhalb aller Amateurbänder."""
    f = int(hz)
    if f <= 0:
        return None
    for lo, hi, name in _AMATEUR_BANDS:
        if lo <= f <= hi:
            return name
    return None


def is_in_amateur_band(hz: int) -> bool:
    return amateur_band_for_hz(hz) is not None
