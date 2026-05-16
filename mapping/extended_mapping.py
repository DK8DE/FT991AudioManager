"""Erweiterte Audio-Einstellungen.

Mapping basiert auf dem **offiziellen FT-991A CAT Operation Reference
Manual (1711-D, 2017)**. Im Zweifel ist das die Quelle der Wahrheit.

Übersicht der Menü-Nummern (laut Manual, Seite 8/9):

- **SSB Cut-Filter** (RX-Klangformung) + SSB-spezifisch:
  - EX102 SSB LCUT FREQ, EX103 SSB LCUT SLOPE
  - EX104 SSB HCUT FREQ, EX105 SSB HCUT SLOPE
  - EX106 SSB MIC SELECT (0=MIC, 1=REAR)
  - EX107 SSB OUT LEVEL (0..100, 3-stellig)
- **AM Audio**:
  - EX045 AM MIC SELECT (0=MIC, 1=REAR)
  - EX046 AM OUT LEVEL (0..100) — Yaesu nennt das im Bedienteil oft "AM Carrier"
- **FM Audio**:
  - EX074 FM MIC SELECT
  - EX075 FM OUT LEVEL (0..100) — "FM Carrier"
- **DATA Audio**:
  - EX070 DATA IN SELECT (0=MIC, 1=REAR)
  - EX073 DATA OUT LEVEL (0..100)

Historie: ältere Versionen hatten die SSB-Cut-Menüs zwei Stellen zu hoch
(EX104..EX107) — das passte zur **TX**-Cut-Spec eines anderen Yaesu-Geräts,
nicht zur FT-991/A-Realität. EX107 in dieser Firmware liefert **SSB OUT
LEVEL** als 3-stelligen Wert, nicht den HCUT-Slope.

Das Modul wirft nur ``ValueError`` (keine ``CatProtocolError``), damit
es vom cat-Package unabhängig bleibt.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Tuple, Union


FreqValue = Union[str, int]   # "OFF" oder Hz-Zahl


# ----------------------------------------------------------------------
# SSB-Cut-Filter (RX-Klangformung) + SSB-spezifisch
# Manual: EX102..EX107
# ----------------------------------------------------------------------

SSB_LCUT_FREQ_MENU = 102
SSB_LCUT_SLOPE_MENU = 103
SSB_HCUT_FREQ_MENU = 104
SSB_HCUT_SLOPE_MENU = 105
# Hinweis: EX106 (SSB MIC SELECT) und EX107 (SSB OUT LEVEL) werden von der
# App bewusst nicht mehr verwaltet — der Bereich war im Bedienfluss
# überflüssig und wurde am 14.05.2026 entfernt.

#: Index 0 = OFF, 1..19 = 100..1000 Hz in 50-Hz-Schritten (Manual EX102).
SSB_LCUT_FREQS: List[FreqValue] = ["OFF"] + list(range(100, 1001, 50))

#: Index 0 = OFF, 1..67 = 700..4000 Hz in 50-Hz-Schritten (Manual EX104).
SSB_HCUT_FREQS: List[FreqValue] = ["OFF"] + list(range(700, 4001, 50))


class SsbSlope(str, Enum):
    DB6 = "6dB/oct"
    DB18 = "18dB/oct"


SSB_SLOPE_INDEX: Dict[SsbSlope, int] = {
    SsbSlope.DB6: 0,
    SsbSlope.DB18: 1,
}
SSB_SLOPE_FROM_INDEX: Dict[int, SsbSlope] = {v: k for k, v in SSB_SLOPE_INDEX.items()}


def encode_ssb_freq(value: FreqValue, table: List[FreqValue]) -> str:
    """``OFF`` / Hz-Zahl -> 2-stelliger Menü-Rohwert."""
    if isinstance(value, str) and value.upper() == "OFF":
        idx = 0
    else:
        try:
            idx = table.index(int(value))
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"Frequenz {value!r} nicht in Tabelle (erlaubt: {table})"
            ) from exc
    return f"{idx:02d}"


def decode_ssb_freq(raw: str, table: List[FreqValue]) -> FreqValue:
    try:
        idx = int(raw)
    except ValueError as exc:
        raise ValueError(f"Ungültiger Frequenz-Index: {raw!r}") from exc
    if not 0 <= idx < len(table):
        raise ValueError(
            f"Frequenz-Index {idx} ausserhalb Tabelle ({len(table)} Einträge)"
        )
    return table[idx]


def encode_ssb_slope(slope: Union[str, SsbSlope]) -> str:
    if isinstance(slope, str):
        try:
            slope = SsbSlope(slope)
        except ValueError as exc:
            raise ValueError(f"Unbekannte Slope: {slope!r}") from exc
    return f"{SSB_SLOPE_INDEX[slope]:d}"


def decode_ssb_slope(raw: str) -> SsbSlope:
    try:
        idx = int(raw)
    except ValueError as exc:
        raise ValueError(f"Ungültiger Slope-Index: {raw!r}") from exc
    if idx not in SSB_SLOPE_FROM_INDEX:
        raise ValueError(f"Slope-Index {idx} unbekannt (erwartet 0 oder 1)")
    return SSB_SLOPE_FROM_INDEX[idx]


# ----------------------------------------------------------------------
# Carrier-Level / Out-Level (AM, FM) — laut Manual EX046 und EX075
# ----------------------------------------------------------------------

AM_CARRIER_MENU = 46
FM_CARRIER_MENU = 75

#: Carrier-Level ist 3-stellig 000..100 wie MIC GAIN.
CARRIER_LEVEL_MIN = 0
CARRIER_LEVEL_MAX = 100
CARRIER_LEVEL_DEFAULT = 50


def encode_carrier_level(value: int) -> str:
    v = max(CARRIER_LEVEL_MIN, min(CARRIER_LEVEL_MAX, int(value)))
    return f"{v:03d}"


def decode_carrier_level(raw: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Ungültiger Carrier-Wert: {raw!r}") from exc


# ----------------------------------------------------------------------
# Mikrofon-Wahl (Front MIC / Rear DATA) — laut Manual EX045 / EX074
# (EX106 — SSB MIC SELECT — wird von der App nicht verwaltet.)
# Menü 072 am Gerät: DATA-Port / USB-Audio (CAT EX072, gleiche Kodierung 0/1).
# ----------------------------------------------------------------------

AM_MIC_SEL_MENU = 45
FM_MIC_SEL_MENU = 74
DATA_PORT_MENU = 72


class MicSource(str, Enum):
    MIC = "MIC"
    REAR = "REAR"


MIC_SOURCE_INDEX: Dict[MicSource, int] = {
    MicSource.MIC: 0,
    MicSource.REAR: 1,
}
MIC_SOURCE_FROM_INDEX: Dict[int, MicSource] = {v: k for k, v in MIC_SOURCE_INDEX.items()}


def encode_mic_source(source: Union[str, MicSource]) -> str:
    if isinstance(source, str):
        try:
            source = MicSource(source.upper())
        except ValueError as exc:
            raise ValueError(f"Unbekannte Mic-Quelle: {source!r}") from exc
    return f"{MIC_SOURCE_INDEX[source]:d}"


def decode_mic_source(raw: str) -> MicSource:
    try:
        idx = int(raw)
    except ValueError as exc:
        raise ValueError(f"Ungültiger Mic-Source-Index: {raw!r}") from exc
    if idx not in MIC_SOURCE_FROM_INDEX:
        raise ValueError(f"Mic-Source-Index {idx} unbekannt (erwartet 0 oder 1)")
    return MIC_SOURCE_FROM_INDEX[idx]


# ----------------------------------------------------------------------
# DATA TX-Level — laut Manual EX073 (DATA OUT LEVEL), 0..100
# ----------------------------------------------------------------------

DATA_TX_LEVEL_MENU = 73
DATA_TX_LEVEL_MIN = 0
DATA_TX_LEVEL_MAX = 100
DATA_TX_LEVEL_DEFAULT = 50


def encode_data_level(value: int) -> str:
    v = max(DATA_TX_LEVEL_MIN, min(DATA_TX_LEVEL_MAX, int(value)))
    return f"{v:03d}"


def decode_data_level(raw: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Ungültiger DATA-Level: {raw!r}") from exc


# ----------------------------------------------------------------------
# Beschreibungs-Schema für Worker / GUI
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class ExtendedSettingDef:
    """Beschreibt einen einzelnen erweiterten Wert (Mapping + Default)."""

    key: str
    label: str
    menu: int
    relevant_modes: Tuple[str, ...]
    encoder: Callable[[object], str]
    decoder: Callable[[str], object]
    tooltip: str = ""


def _encode_lcut_freq(v: object) -> str:
    return encode_ssb_freq(v, SSB_LCUT_FREQS)  # type: ignore[arg-type]


def _decode_lcut_freq(r: str) -> object:
    return decode_ssb_freq(r, SSB_LCUT_FREQS)


def _encode_hcut_freq(v: object) -> str:
    return encode_ssb_freq(v, SSB_HCUT_FREQS)  # type: ignore[arg-type]


def _decode_hcut_freq(r: str) -> object:
    return decode_ssb_freq(r, SSB_HCUT_FREQS)


#: Liste aller bekannten erweiterten Einstellungen — dies ist die zentrale
#: Wahrheitsquelle für Reading/Writing.
EXTENDED_DEFS: Tuple[ExtendedSettingDef, ...] = (
    ExtendedSettingDef(
        "ssb_lcut_freq", "SSB Low Cut Freq", SSB_LCUT_FREQ_MENU, ("SSB", "DATA"),
        _encode_lcut_freq, _decode_lcut_freq,
        f"EX{SSB_LCUT_FREQ_MENU:03d}: OFF / 100..1000 Hz",
    ),
    ExtendedSettingDef(
        "ssb_lcut_slope", "SSB Low Cut Slope", SSB_LCUT_SLOPE_MENU, ("SSB", "DATA"),
        lambda v: encode_ssb_slope(v),  # type: ignore[arg-type]
        decode_ssb_slope,
        f"EX{SSB_LCUT_SLOPE_MENU:03d}: 6 dB / 18 dB pro Oktave",
    ),
    ExtendedSettingDef(
        "ssb_hcut_freq", "SSB High Cut Freq", SSB_HCUT_FREQ_MENU, ("SSB", "DATA"),
        _encode_hcut_freq, _decode_hcut_freq,
        f"EX{SSB_HCUT_FREQ_MENU:03d}: OFF / 700..4000 Hz",
    ),
    ExtendedSettingDef(
        "ssb_hcut_slope", "SSB High Cut Slope", SSB_HCUT_SLOPE_MENU, ("SSB", "DATA"),
        lambda v: encode_ssb_slope(v),  # type: ignore[arg-type]
        decode_ssb_slope,
        f"EX{SSB_HCUT_SLOPE_MENU:03d}: 6 dB / 18 dB pro Oktave",
    ),
    ExtendedSettingDef(
        "am_carrier_level", "AM Carrier-Level", AM_CARRIER_MENU, ("AM",),
        lambda v: encode_carrier_level(int(v)),  # type: ignore[arg-type]
        decode_carrier_level,
        f"EX{AM_CARRIER_MENU:03d}: 0..100",
    ),
    ExtendedSettingDef(
        "fm_carrier_level", "FM Carrier-Level", FM_CARRIER_MENU, ("FM", "C4FM"),
        lambda v: encode_carrier_level(int(v)),  # type: ignore[arg-type]
        decode_carrier_level,
        f"EX{FM_CARRIER_MENU:03d}: 0..100",
    ),
    ExtendedSettingDef(
        "am_mic_sel", "AM Mikrofon", AM_MIC_SEL_MENU, ("AM",),
        lambda v: encode_mic_source(v),  # type: ignore[arg-type]
        decode_mic_source,
        f"EX{AM_MIC_SEL_MENU:03d}: Front-MIC oder Rear-DATA",
    ),
    ExtendedSettingDef(
        "fm_mic_sel", "FM Mikrofon", FM_MIC_SEL_MENU, ("FM", "C4FM"),
        lambda v: encode_mic_source(v),  # type: ignore[arg-type]
        decode_mic_source,
        f"EX{FM_MIC_SEL_MENU:03d}: Front-MIC oder Rear-DATA",
    ),
    ExtendedSettingDef(
        "data_tx_level", "DATA TX-Level (DT-1)", DATA_TX_LEVEL_MENU, ("DATA",),
        lambda v: encode_data_level(int(v)),  # type: ignore[arg-type]
        decode_data_level,
        f"EX{DATA_TX_LEVEL_MENU:03d}: 0..100",
    ),
)


EXTENDED_DEFS_BY_KEY: Dict[str, ExtendedSettingDef] = {d.key: d for d in EXTENDED_DEFS}


def defs_for_mode(mode_group: str) -> Tuple[ExtendedSettingDef, ...]:
    """Liefert die für eine Mode-Gruppe relevanten Settings."""
    mg = mode_group.upper()
    return tuple(d for d in EXTENDED_DEFS if mg in d.relevant_modes)
