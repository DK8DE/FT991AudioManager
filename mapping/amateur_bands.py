"""Amateurfunk-Bänder (Region 1 / DE-typisch) für VFO-Anzeige und Bandwahl.

Grün = Amateurband, gelb = CB/Freenet, rot = sonst außerhalb.
Die CAT-Bandgrenzen des FT-991/A liegen in :mod:`mapping.vfo_bands`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

from i18n import tr
from mapping.rx_mapping import RxMode

#: Combo-Daten: nur VFO-Modus, keine Frequenz setzen.
VFO_BAND_CHOICE = -1


class BandKind(Enum):
    """Kategorie für Farbe und Band-Streifen."""

    AMATEUR = "amateur"
    CB = "cb"
    FREENET = "freenet"


@dataclass(frozen=True)
class AmateurBand:
    """Ein Band mit Grenzen (Hz, inklusive) für Anzeige und Band-Streifen."""

    min_hz: int
    max_hz: int
    key: str
    kind: BandKind = BandKind.AMATEUR

    @property
    def name(self) -> str:
        return tr(f"amateur_bands.{self.key}")

    @property
    def center_hz(self) -> int:
        return (self.min_hz + self.max_hz) // 2

    def combo_label(self) -> str:
        """``14,000 – 14,350 MHz (20 m)`` — Meterangabe in Klammern."""
        lo_mhz = self.min_hz / 1_000_000.0
        hi_mhz = self.max_hz / 1_000_000.0
        return tr(
            "amateur_bands.combo_label",
            lo=lo_mhz,
            hi=hi_mhz,
            name=self.name,
        )


# (min_hz, max_hz, i18n-key) — Grenzen inklusive; aufsteigend nach Frequenz.
_AMATEUR_BANDS: Tuple[Tuple[int, int, str], ...] = (
    (1_810_000, 1_850_000, "160m"),
    (3_500_000, 3_800_000, "80m"),
    (5_351_500, 5_366_500, "60m"),
    (7_000_000, 7_200_000, "40m"),
    (10_100_000, 10_150_000, "30m"),
    (14_000_000, 14_350_000, "20m"),
    (18_068_000, 18_168_000, "17m"),
    (21_000_000, 21_450_000, "15m"),
    (24_890_000, 24_990_000, "12m"),
    (28_000_000, 29_700_000, "10m"),
    (50_000_000, 54_000_000, "6m"),
    (144_000_000, 146_000_000, "2m"),
    (430_000_000, 440_000_000, "70cm"),
)

AMATEUR_BANDS: Tuple[AmateurBand, ...] = tuple(
    AmateurBand(lo, hi, key) for lo, hi, key in _AMATEUR_BANDS
)

#: CB (DE 80-Kanal) und Freenet (149 MHz) — gelbe Markierung, eigener Band-Streifen.
_SPECIAL_BANDS: Tuple[Tuple[int, int, str, BandKind], ...] = (
    (26_565_000, 27_405_000, "cb", BandKind.CB),
    (149_025_000, 149_087_500, "freenet", BandKind.FREENET),
)

SPECIAL_BANDS: Tuple[AmateurBand, ...] = tuple(
    AmateurBand(lo, hi, key, kind) for lo, hi, key, kind in _SPECIAL_BANDS
)

DISPLAY_BANDS: Tuple[AmateurBand, ...] = AMATEUR_BANDS + SPECIAL_BANDS

#: CEPT/FCC CB-Kanäle 1–40 (26,965–27,405 MHz). Nicht linear (Kanäle 23–25 versetzt).
_CB_CHANNELS_1_40_HZ: Tuple[int, ...] = (
    26_965_000,
    26_975_000,
    26_985_000,
    27_005_000,
    27_015_000,
    27_025_000,
    27_035_000,
    27_055_000,
    27_065_000,
    27_075_000,
    27_085_000,
    27_105_000,
    27_115_000,
    27_125_000,
    27_135_000,
    27_155_000,
    27_165_000,
    27_175_000,
    27_185_000,
    27_205_000,
    27_215_000,
    27_225_000,
    27_255_000,
    27_235_000,
    27_245_000,
    27_265_000,
    27_275_000,
    27_285_000,
    27_295_000,
    27_305_000,
    27_315_000,
    27_325_000,
    27_335_000,
    27_345_000,
    27_355_000,
    27_365_000,
    27_375_000,
    27_385_000,
    27_395_000,
    27_405_000,
)

#: Kanäle 41–80 (26,565–26,955 MHz, 10-kHz-Raster) — im Band vor Kanal 1.
CB_LOW_BLOCK_FIRST_HZ = 26_565_000
CB_LOW_BLOCK_STEP_HZ = 10_000
CB_LOW_BLOCK_CHANNEL_COUNT = 40

CB_BAND_MIN_HZ = CB_LOW_BLOCK_FIRST_HZ
CB_BAND_MAX_HZ = _CB_CHANNELS_1_40_HZ[-1]
CB_TOTAL_CHANNEL_COUNT = CB_LOW_BLOCK_CHANNEL_COUNT + len(_CB_CHANNELS_1_40_HZ)

#: Beschriftete Kanäle unter dem CB-Band-Streifen (41–80, dann 1–40).
CB_STRIP_LABEL_CHANNELS: Tuple[int, ...] = (
    41,
    50,
    60,
    70,
    80,
    1,
    10,
    20,
    30,
    40,
)

#: Freenet Kanal 1 … 6 (12,5-kHz-Raster ab 149,025 MHz).
FREENET_FIRST_CHANNEL_HZ = 149_025_000
FREENET_CHANNEL_STEP_HZ = 12_500
FREENET_CHANNEL_COUNT = 6

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


def display_band_at_hz(hz: int) -> Optional[AmateurBand]:
    """Amateur-, CB- oder Freenet-Band für VFO-Farbe und Band-Streifen."""
    f = int(hz)
    if f <= 0:
        return None
    for band in DISPLAY_BANDS:
        if band.min_hz <= f <= band.max_hz:
            return band
    return None


def is_cb_block_hz(hz: int) -> bool:
    """True im gesamten 80-Kanal-CB-Bereich (26,565–27,405 MHz)."""
    f = int(hz)
    return CB_BAND_MIN_HZ <= f <= CB_BAND_MAX_HZ


def is_cb_channels_1_40_hz(hz: int) -> bool:
    f = int(hz)
    return _CB_CHANNELS_1_40_HZ[0] <= f <= _CB_CHANNELS_1_40_HZ[-1]


def is_cb_channels_41_80_hz(hz: int) -> bool:
    f = int(hz)
    return (
        cb_channel_frequency_hz(41)
        <= f
        <= cb_channel_frequency_hz(80)
    )


def cb_channel_frequency_hz(channel: int) -> int:
    """Frequenz eines CB-Kanals 1–40 (CEPT) oder 41–80 (FM-Zusatzblock) in Hz."""
    ch = int(channel)
    if 1 <= ch <= 40:
        return _CB_CHANNELS_1_40_HZ[ch - 1]
    if 41 <= ch <= 80:
        return CB_LOW_BLOCK_FIRST_HZ + (ch - 41) * CB_LOW_BLOCK_STEP_HZ
    raise ValueError(f"CB-Kanal ausserhalb 1–{CB_TOTAL_CHANNEL_COUNT}: {ch}")


def cb_all_channel_frequencies_hz() -> List[int]:
    """Alle 80 Kanäle in Frequenz-Reihenfolge (41–80, dann 1–40)."""
    return [
        cb_channel_frequency_hz(ch)
        for ch in list(range(41, 81)) + list(range(1, 41))
    ]


def cb_band_strip_label_frequencies() -> List[int]:
    """Beschriftete Kanäle unter dem CB-Band-Streifen."""
    return [cb_channel_frequency_hz(ch) for ch in CB_STRIP_LABEL_CHANNELS]


def display_band_for_hz(hz: int) -> Optional[str]:
    band = display_band_at_hz(hz)
    return band.name if band is not None else None


def _channel_number_at_hz(
    hz: int,
    *,
    first_hz: int,
    step_hz: int,
    channel_count: int,
    band_min_hz: int,
    band_max_hz: int,
    tolerance_hz: int = 50,
) -> Optional[int]:
    """Kanalnummer, wenn ``hz`` praktisch auf einem Kanal liegt."""
    f = int(hz)
    if not (band_min_hz <= f <= band_max_hz):
        return None
    idx = round((f - first_hz) / step_hz)
    ch = int(idx) + 1
    if not (1 <= ch <= channel_count):
        return None
    ch_hz = first_hz + int(idx) * step_hz
    if abs(f - ch_hz) > tolerance_hz:
        return None
    return ch


def cb_channel_at_hz(hz: int, *, tolerance_hz: int = 50) -> Optional[int]:
    f = int(hz)
    for ch in list(range(41, 81)) + list(range(1, 41)):
        ch_hz = cb_channel_frequency_hz(ch)
        if abs(f - ch_hz) <= tolerance_hz:
            return ch
    return None


def freenet_channel_at_hz(hz: int) -> Optional[int]:
    if not is_freenet_block_hz(hz):
        return None
    return _channel_number_at_hz(
        hz,
        first_hz=FREENET_FIRST_CHANNEL_HZ,
        step_hz=FREENET_CHANNEL_STEP_HZ,
        channel_count=FREENET_CHANNEL_COUNT,
        band_min_hz=FREENET_FIRST_CHANNEL_HZ,
        band_max_hz=freenet_channel_frequency_hz(FREENET_CHANNEL_COUNT),
    )


def is_freenet_block_hz(hz: int) -> bool:
    f = int(hz)
    return (
        FREENET_FIRST_CHANNEL_HZ
        <= f
        <= freenet_channel_frequency_hz(FREENET_CHANNEL_COUNT)
    )


def freenet_channel_frequency_hz(channel: int) -> int:
    ch = int(channel)
    if not 1 <= ch <= FREENET_CHANNEL_COUNT:
        raise ValueError(f"Freenet-Kanal ausserhalb 1–{FREENET_CHANNEL_COUNT}: {ch}")
    return FREENET_FIRST_CHANNEL_HZ + (ch - 1) * FREENET_CHANNEL_STEP_HZ


def freenet_all_channel_frequencies_hz() -> List[int]:
    return [
        freenet_channel_frequency_hz(ch)
        for ch in range(1, FREENET_CHANNEL_COUNT + 1)
    ]


def freenet_band_strip_tick_frequencies() -> List[int]:
    return freenet_all_channel_frequencies_hz()


def band_strip_snap_frequencies_hz(band: AmateurBand) -> Optional[List[int]]:
    """Kanalfrequenzen für Einrasten im Band-Streifen (CB/Freenet)."""
    if band.kind is BandKind.CB:
        return cb_all_channel_frequencies_hz()
    if band.kind is BandKind.FREENET:
        return freenet_all_channel_frequencies_hz()
    return None


def snap_band_strip_frequency_hz(hz: int, band: AmateurBand) -> int:
    """Frequenz auf nächsten Kanal rasten (CB/Freenet) oder im Band belassen."""
    freqs = band_strip_snap_frequencies_hz(band)
    f = int(hz)
    if not freqs:
        return max(band.min_hz, min(band.max_hz, f))
    if f <= freqs[0]:
        return freqs[0]
    if f >= freqs[-1]:
        return freqs[-1]
    best = freqs[0]
    best_dist = abs(f - best)
    for ch_hz in freqs[1:]:
        d = abs(f - ch_hz)
        if d < best_dist:
            best_dist = d
            best = ch_hz
    return best


def display_band_label_at_hz(hz: int) -> Optional[str]:
    """Bandname für die Anzeige — bei CB/Freenet inkl. Kanalnummer, wenn erkannt."""
    band = display_band_at_hz(hz)
    if band is None:
        return None
    if band.kind is BandKind.CB:
        ch = cb_channel_at_hz(hz)
        if ch is not None:
            return tr("amateur_bands.cb_channel", channel=ch)
    elif band.kind is BandKind.FREENET:
        ch = freenet_channel_at_hz(hz)
        if ch is not None:
            return tr("amateur_bands.freenet_channel", channel=ch)
    return band.name


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
    if band.key in ("6m", "2m", "70cm"):
        return RxMode.FM
    if band.max_hz < 10_000_000:
        return RxMode.LSB
    if band.min_hz >= 10_000_000:
        return RxMode.USB
    return RxMode.USB if int(hz) >= 10_000_000 else RxMode.LSB


def combo_entries_high_to_low() -> List[Tuple[str, int]]:
    """``[(Anzeigetext, Nutzdaten), …]`` — Nutzdaten = ``VFO_BAND_CHOICE`` oder ``center_hz``."""
    out: List[Tuple[str, int]] = [(tr("amateur_bands.vfo"), VFO_BAND_CHOICE)]
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


def band_strip_groove_tick_frequencies(band: AmateurBand) -> List[int]:
    """Striche im Band-Streifen — bei CB/Freenet alle Kanäle."""
    if band.kind is BandKind.CB:
        return cb_all_channel_frequencies_hz()
    if band.kind is BandKind.FREENET:
        return freenet_all_channel_frequencies_hz()
    span = band.max_hz - band.min_hz
    if span > 1_000_000:
        return band_100khz_tick_frequencies(band)
    return band_tick_frequencies(band, max_ticks=7)


def band_strip_label_tick_frequencies(band: AmateurBand) -> List[int]:
    """Beschriftete Tick-Markierungen unter dem Band-Streifen."""
    if band.kind is BandKind.CB:
        return cb_band_strip_label_frequencies()
    if band.kind is BandKind.FREENET:
        return freenet_all_channel_frequencies_hz()
    return band_strip_groove_tick_frequencies(band)


def band_strip_tick_frequencies(band: AmateurBand) -> List[int]:
    """Rückwärtskompatibel — Groove-Ticks (alle Kanäle bei CB/Freenet)."""
    return band_strip_groove_tick_frequencies(band)


def band_strip_tick_label(hz: int, band: AmateurBand) -> str:
    """Beschriftung unter einer Tick-Marke im Band-Streifen."""
    if band.kind is BandKind.CB:
        ch = cb_channel_at_hz(hz)
        return str(ch if ch is not None else "?")
    if band.kind is BandKind.FREENET:
        ch = freenet_channel_at_hz(hz)
        return str(ch if ch is not None else "?")
    span = band.max_hz - band.min_hz
    if span > 1_000_000:
        return frequency_label_100khz(hz)
    return frequency_label_for_tick(hz, band)
