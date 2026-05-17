"""Amateurfunk-Bänder (Region 1 / DE-typisch) für VFO-Anzeige und Bandwahl.

Nur für die GUI-Farbmarkierung (grün = im Amateurband, rot = außerhalb).
Die CAT-Bandgrenzen des FT-991/A liegen in :mod:`mapping.vfo_bands`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

#: Combo-Daten: nur VFO-Modus, keine Frequenz setzen.
VFO_BAND_CHOICE = -1


@dataclass(frozen=True)
class AmateurBand:
    """Ein Amateurband mit ITU-Grenzen (Hz, inklusive)."""

    min_hz: int
    max_hz: int
    name: str

    @property
    def center_hz(self) -> int:
        return (self.min_hz + self.max_hz) // 2

    def combo_label(self) -> str:
        """``14,000 – 14,350 MHz (20 m)`` — Meterangabe in Klammern."""
        lo_mhz = self.min_hz / 1_000_000.0
        hi_mhz = self.max_hz / 1_000_000.0
        return f"{lo_mhz:.3f} – {hi_mhz:.3f} MHz ({self.name})"


# (min_hz, max_hz, Kurzname) — Grenzen inklusive; aufsteigend nach Frequenz.
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

AMATEUR_BANDS: Tuple[AmateurBand, ...] = tuple(
    AmateurBand(lo, hi, name) for lo, hi, name in _AMATEUR_BANDS
)

#: Von 70 cm abwärts (für Band-Dropdown).
AMATEUR_BANDS_HIGH_TO_LOW: Tuple[AmateurBand, ...] = tuple(reversed(AMATEUR_BANDS))


def amateur_band_for_hz(hz: int) -> Optional[str]:
    """Liefert den Bandnamen oder ``None`` wenn außerhalb aller Amateurbänder."""
    f = int(hz)
    if f <= 0:
        return None
    for band in AMATEUR_BANDS:
        if band.min_hz <= f <= band.max_hz:
            return band.name
    return None


def amateur_band_at_hz(hz: int) -> Optional[AmateurBand]:
    f = int(hz)
    if f <= 0:
        return None
    for band in AMATEUR_BANDS:
        if band.min_hz <= f <= band.max_hz:
            return band
    return None


def is_in_amateur_band(hz: int) -> bool:
    return amateur_band_for_hz(hz) is not None


def combo_entries_high_to_low() -> List[Tuple[str, int]]:
    """``[(Anzeigetext, Nutzdaten), …]`` — Nutzdaten = ``VFO_BAND_CHOICE`` oder ``center_hz``."""
    out: List[Tuple[str, int]] = [("VFO", VFO_BAND_CHOICE)]
    for band in AMATEUR_BANDS_HIGH_TO_LOW:
        out.append((band.combo_label(), band.center_hz))
    return out
