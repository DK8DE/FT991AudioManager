"""Amateurfunk-Bänder (Region 1 / DE-typisch) für VFO-Anzeige und Bandwahl.

Nur für die GUI-Farbmarkierung (grün = im Amateurband, rot = außerhalb).
Die CAT-Bandgrenzen des FT-991/A liegen in :mod:`mapping.vfo_bands`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from mapping.rx_mapping import RxMode

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


def preferred_voice_rx_mode_for_amateur_hz(hz: int) -> Optional[RxMode]:
    """Phone-typische Betriebsart für eine Frequenz im Amateurband (ITU Region 1 üblich).

    - **6 m / 2 m / 70 cm:** FM
    - **HF unter 10 MHz** (z. B. 160–40 m, 60 m): LSB
    - **HF ab 10 MHz** (30–10 m): USB

    Außerhalb der definierten Amateurbänder: ``None``.
    """
    band = amateur_band_at_hz(int(hz))
    if band is None:
        return None
    if band.name in ("6 m", "2 m", "70 cm"):
        return RxMode.FM
    if band.max_hz < 10_000_000:
        return RxMode.LSB
    if band.min_hz >= 10_000_000:
        return RxMode.USB
    return RxMode.USB if int(hz) >= 10_000_000 else RxMode.LSB


def combo_entries_high_to_low() -> List[Tuple[str, int]]:
    """``[(Anzeigetext, Nutzdaten), …]`` — Nutzdaten = ``VFO_BAND_CHOICE`` oder ``center_hz``."""
    out: List[Tuple[str, int]] = [("VFO", VFO_BAND_CHOICE)]
    for band in AMATEUR_BANDS_HIGH_TO_LOW:
        out.append((band.combo_label(), band.center_hz))
    return out


# „Schöne“ Raster für Band-Streifen-Ticks (Hz, aufsteigend).
_NICE_TICK_STEPS_HZ: Tuple[int, ...] = (
    5_000,
    10_000,
    25_000,
    50_000,
    100_000,
    200_000,
    500_000,
    1_000_000,
    2_000_000,
    5_000_000,
    10_000_000,
)


def _choose_tick_step_hz(span_hz: int, max_ticks: int) -> int:
    """Feinste „schöne“ Schrittweite; ggf. in :func:`_subsample_ticks` reduzieren."""
    if span_hz <= 0:
        return 1_000
    finest: Optional[int] = None
    for step in _NICE_TICK_STEPS_HZ:
        if step > span_hz:
            continue
        if span_hz // step + 1 >= 2:
            finest = step
    if finest is not None:
        return finest
    return max(span_hz // max(1, max_ticks - 1), 1_000)


def _subsample_ticks(ticks: List[int], max_ticks: int) -> List[int]:
    if len(ticks) <= max_ticks:
        return ticks
    if max_ticks < 2:
        return [ticks[0]]
    out: List[int] = []
    last_idx = len(ticks) - 1
    for i in range(max_ticks):
        idx = round(i * last_idx / (max_ticks - 1))
        out.append(ticks[idx])
    deduped: List[int] = []
    for hz in out:
        if not deduped or hz != deduped[-1]:
            deduped.append(hz)
    return deduped


def band_tick_frequencies(band: AmateurBand, *, max_ticks: int = 7) -> List[int]:
    """Frequenzen für Band-Streifen-Markierungen (inkl. min/max)."""
    cap = max(2, int(max_ticks))
    span = band.max_hz - band.min_hz
    step = _choose_tick_step_hz(span, cap)
    ticks: List[int] = []
    hz = band.min_hz
    while hz < band.max_hz:
        ticks.append(hz)
        hz += step
    if not ticks or ticks[-1] != band.max_hz:
        ticks.append(band.max_hz)
    return _subsample_ticks(ticks, cap)


def frequency_label_for_tick(hz: int, band: AmateurBand) -> str:
    """Kurzes Label unter einer Tick-Marke (MHz mit passender Genauigkeit)."""
    span = band.max_hz - band.min_hz
    mhz = hz / 1_000_000.0
    if span <= 200_000:
        return f"{mhz:.3f}"
    if span <= 2_000_000:
        return f"{mhz:.2f}"
    return f"{mhz:.1f}"


BAND_TICK_STEP_100KHZ_HZ = 100_000


def band_100khz_tick_frequencies(band: AmateurBand) -> List[int]:
    """Alle 100-kHz-Rasterpunkte innerhalb des Bandes (inklusive)."""
    step = BAND_TICK_STEP_100KHZ_HZ
    hz = ((band.min_hz + step - 1) // step) * step
    ticks: List[int] = []
    while hz <= band.max_hz:
        ticks.append(hz)
        hz += step
    return ticks


def frequency_label_100khz(hz: int) -> str:
    """MHz-Label für 100-kHz-Markierungen (z. B. ``14.0``, ``14.1``)."""
    mhz = hz // 1_000_000
    frac = (hz % 1_000_000) // 100_000
    if frac == 0:
        return f"{mhz}.0"
    return f"{mhz}.{frac}"
