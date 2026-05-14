"""Zuordnung der EQ-Werte zwischen menschenlesbaren Daten und CAT-Rohwerten.

Basiert auf dem **offiziellen FT-991A CAT Operation Reference Manual
(1711-D, 2017)**. Im Zweifel ist das die Quelle der Wahrheit.

Wire-Format der EQ-Menüs
------------------------
Pro Band gibt es **drei** aufeinanderfolgende Menüs in der Reihenfolge
**Freq / Level / BW(Q)**:

- **Frequenz** (2-stellig zero-padded Index):
    Normal-EQ:    EX119, EX122, EX125
    Processor-EQ: EX128, EX131, EX134
    ``00`` = OFF, ``01..`` = bandabhängige Frequenz (siehe Tabellen).

- **Level** (3-stelliger signierter Wert):
    Normal-EQ:    EX120, EX123, EX126
    Processor-EQ: EX129, EX132, EX135
    Bereich ``-20`` bis ``+10`` dB, z. B. ``+02``, ``-05``, ``+00``.

- **Bandbreite / Q-Faktor** (2-stellig zero-padded):
    Normal-EQ:    EX121, EX124, EX127
    Processor-EQ: EX130, EX133, EX136
    Bereich ``01..10``.

Achtung — historische Stolperfalle: ältere Versionen dieses Moduls
hatten die Menüs zwei Stellen höher (EX121–EX129 / EX130–EX138) und die
Slot-Reihenfolge Freq/BW/Level. Beides war falsch. EX137/EX138 sind
laut Manual ``HF TX MAX POWER`` und ``50M TX MAX POWER`` — also gar
nicht audio-bezogen.
"""

from __future__ import annotations

from typing import List, Sequence, Tuple, Union


FreqValue = Union[int, str]  # int = Hz; "OFF" als String


# ----------------------------------------------------------------------
# Frequenztabellen — Index 0 = OFF (Manual: 1711-D, Seite 9)
# ----------------------------------------------------------------------

#: EX119 / EX128 — Tiefband (LOW): OFF + 100..700 Hz in 100-Hz-Schritten (8 Einträge).
EQ_LOW_FREQS: List[FreqValue] = [
    "OFF",
    100, 200, 300, 400, 500, 600, 700,
]

#: EX122 / EX131 — Mittenband (MID): OFF + 700..1500 Hz in 100-Hz-Schritten (10 Einträge).
EQ_MID_FREQS: List[FreqValue] = [
    "OFF",
    700, 800, 900, 1000, 1100, 1200, 1300, 1400, 1500,
]

#: EX125 / EX134 — Hochband (HIGH): OFF + 1500..3200 Hz in 100-Hz-Schritten (19 Einträge).
EQ_HIGH_FREQS: List[FreqValue] = [
    "OFF",
    1500, 1600, 1700, 1800, 1900,
    2000, 2100, 2200, 2300, 2400,
    2500, 2600, 2700, 2800, 2900,
    3000, 3100, 3200,
]


# ----------------------------------------------------------------------
# Level (dB) — Manual: –20 ~ 0 ~ +10
# ----------------------------------------------------------------------

LEVEL_DB_MIN = -20
LEVEL_DB_MAX = +10


# ----------------------------------------------------------------------
# Bandbreite / Q-Faktor — Manual: 01 ~ 10
# ----------------------------------------------------------------------

BW_MIN = 1
BW_MAX = 10


# ----------------------------------------------------------------------
# Menü-Layout: welche Menü-Nummer hat welche Bedeutung
# ----------------------------------------------------------------------


class EqMenuSet:
    """Die 9 Menü-Nummern eines kompletten Parametric-EQ-Sets.

    Reihenfolge pro Band: **Freq, Level, BW (Q)**.
    """

    def __init__(
        self,
        band1: Tuple[int, int, int],
        band2: Tuple[int, int, int],
        band3: Tuple[int, int, int],
    ) -> None:
        self.band1_freq, self.band1_level, self.band1_bw = band1
        self.band2_freq, self.band2_level, self.band2_bw = band2
        self.band3_freq, self.band3_level, self.band3_bw = band3

    def all_menus(self) -> List[int]:
        return [
            self.band1_freq, self.band1_level, self.band1_bw,
            self.band2_freq, self.band2_level, self.band2_bw,
            self.band3_freq, self.band3_level, self.band3_bw,
        ]


#: Parametric Mic EQ (ohne Speech Processor) — Manual: EX119..EX127.
NORMAL_EQ_MENUS = EqMenuSet(
    band1=(119, 120, 121),
    band2=(122, 123, 124),
    band3=(125, 126, 127),
)

#: Processor EQ (bei aktivem Speech Processor) — Manual: EX128..EX136.
PROCESSOR_EQ_MENUS = EqMenuSet(
    band1=(128, 129, 130),
    band2=(131, 132, 133),
    band3=(134, 135, 136),
)


# ----------------------------------------------------------------------
# Frequenz: Rohwert <-> menschenlesbarer Wert
# ----------------------------------------------------------------------


def freq_table_for_band(band_index: int) -> Sequence[FreqValue]:
    """``band_index`` ist 0 (LOW), 1 (MID) oder 2 (HIGH)."""
    if band_index == 0:
        return EQ_LOW_FREQS
    if band_index == 1:
        return EQ_MID_FREQS
    if band_index == 2:
        return EQ_HIGH_FREQS
    raise ValueError(f"band_index muss 0..2 sein, war {band_index}")


def decode_freq(raw: str, band_index: int) -> FreqValue:
    """CAT-Rohwert -> menschenlesbarer Frequenzwert (Hz oder ``"OFF"``)."""
    table = freq_table_for_band(band_index)
    try:
        idx = int(raw)
    except ValueError as exc:
        raise ValueError(f"Ungültiger Frequenz-Rohwert: {raw!r}") from exc
    if idx < 0 or idx >= len(table):
        raise ValueError(
            f"Frequenz-Index {idx} ausserhalb der Tabelle "
            f"({len(table)} Einträge für Band {band_index})"
        )
    return table[idx]


def encode_freq(value: FreqValue, band_index: int) -> str:
    """Menschenlesbarer Frequenzwert -> CAT-Rohwert (2-stellig)."""
    table = freq_table_for_band(band_index)
    if isinstance(value, str) and value.strip().upper() in ("OFF", "", "AUS"):
        idx = 0
    else:
        try:
            idx = table.index(value)
        except ValueError as exc:
            raise ValueError(
                f"Frequenzwert {value!r} ist in Band {band_index} nicht "
                f"erlaubt. Erlaubt: {list(table)}"
            ) from exc
    return f"{idx:02d}"


# ----------------------------------------------------------------------
# Level (signiert, 3-stellig)
# ----------------------------------------------------------------------


def decode_level(raw: str) -> int:
    """CAT-Rohwert (z. B. ``"+02"``, ``"-05"``, ``"+00"``) -> dB-Wert.

    Toleriert auch Werte ohne Vorzeichen (``"02"``) — die werden positiv
    interpretiert.
    """
    raw = raw.strip()
    if not raw:
        raise ValueError(f"Leerer Level-Rohwert: {raw!r}")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"Ungültiger Level-Rohwert: {raw!r}") from exc
    if value < LEVEL_DB_MIN or value > LEVEL_DB_MAX:
        raise ValueError(
            f"Level-Wert {value} ausserhalb {LEVEL_DB_MIN}..{LEVEL_DB_MAX}"
        )
    return value


def encode_level(db: int) -> str:
    """dB-Wert -> CAT-Rohwert (3-stellig signiert).

    Beispiele: ``+02``, ``-05``, ``+00``, ``+10``, ``-20``.
    Klemmt automatisch in den erlaubten Bereich.
    """
    db_clamped = max(LEVEL_DB_MIN, min(LEVEL_DB_MAX, int(db)))
    return f"{db_clamped:+03d}"


# ----------------------------------------------------------------------
# Bandbreite (Q-Faktor)
# ----------------------------------------------------------------------


def decode_bw(raw: str) -> int:
    """CAT-Rohwert -> Q-Wert (1..10)."""
    try:
        bw = int(raw)
    except ValueError as exc:
        raise ValueError(f"Ungültiger BW-Rohwert: {raw!r}") from exc
    if bw < BW_MIN or bw > BW_MAX:
        raise ValueError(f"BW-Wert {bw} ausserhalb {BW_MIN}..{BW_MAX}")
    return bw


def encode_bw(bw: int) -> str:
    """Q-Wert -> CAT-Rohwert (2-stellig, zero-padded)."""
    bw_clamped = max(BW_MIN, min(BW_MAX, int(bw)))
    return f"{bw_clamped:02d}"


# ----------------------------------------------------------------------
# Komfort
# ----------------------------------------------------------------------


def freq_choices(band_index: int) -> List[FreqValue]:
    """Liste der zulässigen Frequenzen für ein Band (für GUI-Dropdowns)."""
    return list(freq_table_for_band(band_index))


def freq_to_label(value: FreqValue) -> str:
    """``300 -> "300 Hz"``, ``"OFF" -> "OFF"``."""
    if isinstance(value, str):
        return value
    return f"{value} Hz"


def label_to_freq(label: str) -> FreqValue:
    """Umkehrung von :func:`freq_to_label`."""
    label = label.strip()
    if not label or label.upper() == "OFF":
        return "OFF"
    if label.endswith(" Hz"):
        label = label[:-3]
    try:
        return int(label)
    except ValueError as exc:
        raise ValueError(f"Frequenz-Label nicht parsebar: {label!r}") from exc
