"""Mappings für die Live-Meter (Version 0.4, korrigiert in 0.6).

Das Yaesu FT-991/FT-991A liefert über das ``RM``-Kommando mehrere Meter.
Die Indizes laut Manual 1711-D (FT-991 CAT Operation Reference Book, S. 16):

============ ====== ===========================================
``RM0;``     —      hängt vom Front-Panel-Meter ab (nicht nutzen)
``RM1;``     S      S-Meter (RX-Signalstärke)
``RM2;``     —      hängt vom Front-Panel-Meter ab (nicht nutzen)
``RM3;``     COMP   Speech-Processor-Kompression
``RM4;``     ALC    ALC-Aussteuerung
``RM5;``     PO     Power Out (relativ)
``RM6;``     SWR    Stehwellen-Verhältnis (Rohwert)
``RM7;``     ID     Endstufen-Drain-Strom
``RM8;``     VDD    Endstufen-Versorgungsspannung
============ ====== ===========================================

Antwort-Format ist ``RMn<value>;`` mit ``<value>`` als 3-stelliger
Dezimalzahl (typisch 000..255).

Für das S-Meter gibt es zusätzlich das dedizierte ``SM`` Kommando
(``SM0;`` -> ``SM0nnn;``), das unabhängig vom Front-Panel-Meter funktioniert
und deshalb in dieser App bevorzugt verwendet wird.

Zusätzlich kennen wir den TX-Status über das ``TX``-Kommando:
``TX0;`` = RX, ``TX1;`` = TX per PTT/Mic, ``TX2;`` = TX per CAT.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Tuple


class MeterKind(str, Enum):
    COMP = "comp"
    ALC = "alc"
    PO = "po"
    SWR = "swr"


# ----------------------------------------------------------------------
# Tick-Tabellen für die Skalen-Beschriftung der TX-Meter
# ----------------------------------------------------------------------

#: ALC / COMP / PO werden als Prozent dargestellt — Rohwert 0..255 auf 0..100 %.
_PERCENT_TICKS: List[Tuple[int, str]] = [
    (0,   "0"),
    (64,  "25"),
    (128, "50"),
    (191, "75"),
    (255, "100"),
]

#: SWR-Marken sind firmware-spezifisch und nicht offiziell dokumentiert.
#: Diese Tabelle entspricht den verbreiteten Yaesu-Empirie-Werten **für
#: KW**: ``raw 0 ≈ SWR 1.0``, ``raw 80 ≈ 1.5``, ``raw 128 ≈ 2.0``,
#: ``raw 204 ≈ 3.0``. Damit liegt die "gelbe" Schwelle (warn=0.30) nah an
#: SWR 1.5 und die "rote" Schwelle (danger=0.50) ungefähr bei SWR 2.0.
#:
#: Auf VHF/UHF (2 m, 70 cm) wird die Skala vom Radio nach unserer aktuellen
#: Erfahrung steiler abgebildet — schon bei SWR ~1.5 liefert ``RM6;``
#: deutlich höhere Roh-Werte. Bis wir eine empirische Tabelle haben, bleibt
#: hier die KW-Skala (und der Tooltip der SWR-Bar zeigt zur Diagnose stets
#: den Roh-Wert mit an).
#:
#: Das oberste Tick-Label heißt ``>3`` statt früher ``∞`` — "Unendlich"
#: war missverständlich (man kann ja keinen unendlichen SWR sinnvoll
#: angeben). ``>3`` macht klar: alles oberhalb davon bündelt die Skala.
_SWR_TICKS: List[Tuple[int, str]] = [
    (0,   "1.0"),
    (80,  "1.5"),
    (128, "2"),
    (204, "3"),
    (255, ">3"),
]


def _format_percent(raw: int) -> str:
    pct = round(raw * 100 / 255)
    return f"{pct}%"


def _format_swr(raw: int) -> str:
    """Liefert eine SWR-Annäherung als ``"≈ 1.8:1"`` oder ``"> 3:1"``.

    * ``raw == 0``: wir zeigen ``"—"``. Beim FT-991A liefert ``RM6;`` im
      RX-Pfad oder ganz am Anfang einer kurzen PTT regelmäßig ``000`` —
      hier eine konkrete SWR-Zahl zu suggerieren wäre irreführend.
    * Werte oberhalb des höchsten numerischen Stützpunkts (``raw > 204``,
      d. h. SWR > 3) werden zu ``"> 3:1"`` zusammengefasst — eine
      genauere Aussage erlaubt der Roh-Wert nicht.
    * Sonst wird linear zwischen den Stützpunkten interpoliert.
    """
    if raw <= 0:
        return "—"
    # Über der letzten numerischen Stütze bündeln wir auf "> 3:1".
    last_numeric_raw = None
    for tick_raw, tick_lbl in _SWR_TICKS:
        try:
            float(tick_lbl)
        except ValueError:
            continue
        last_numeric_raw = (tick_raw, tick_lbl)
    if last_numeric_raw is not None and raw > last_numeric_raw[0]:
        return f"> {last_numeric_raw[1]}:1"
    for (raw_a, lbl_a), (raw_b, lbl_b) in zip(_SWR_TICKS, _SWR_TICKS[1:]):
        if raw_a <= raw <= raw_b:
            try:
                val_a = float(lbl_a)
                val_b = float(lbl_b)
            except ValueError:
                return f"≈ {lbl_a}:1"
            frac = (raw - raw_a) / (raw_b - raw_a) if raw_b > raw_a else 0.0
            value = val_a + frac * (val_b - val_a)
            return f"≈ {value:.1f}:1"
    return f"> {_SWR_TICKS[-2][1]}:1"


@dataclass(frozen=True)
class MeterInfo:
    index: int                                       # Position im RM-Kommando
    label: str                                       # GUI-Anzeige
    raw_max: int                                     # Skala 0..raw_max (typisch 255)
    warn: float                                      # 0..1: ab hier gelb
    danger: float                                    # 0..1: ab hier rot
    unit: str                                        # z. B. "%" oder ":1"
    ticks: List[Tuple[int, str]]                     # Skalen-Labels
    value_formatter: Callable[[int], str] = field(   # raw -> Anzeige
        default=_format_percent
    )


#: Tabelle aller TX-Meter. Indizes laut Manual 1711-D, S. 16.
#: Schwellwerte sind Erfahrungswerte und können später vom Nutzer
#: konfiguriert werden.
METER_INFO: Dict[MeterKind, MeterInfo] = {
    MeterKind.COMP: MeterInfo(
        index=3, label="COMP", raw_max=255, warn=0.50, danger=0.80,
        unit="%", ticks=_PERCENT_TICKS, value_formatter=_format_percent,
    ),
    MeterKind.ALC: MeterInfo(
        index=4, label="ALC", raw_max=255, warn=0.50, danger=0.80,
        unit="%", ticks=_PERCENT_TICKS, value_formatter=_format_percent,
    ),
    MeterKind.PO: MeterInfo(
        index=5, label="PO", raw_max=255, warn=0.80, danger=0.95,
        unit="%", ticks=_PERCENT_TICKS, value_formatter=_format_percent,
    ),
    MeterKind.SWR: MeterInfo(
        index=6, label="SWR", raw_max=255, warn=0.30, danger=0.50,
        unit=":1", ticks=_SWR_TICKS, value_formatter=_format_swr,
    ),
}


def format_meter_value(kind: MeterKind, raw: int) -> str:
    """Formatiert einen Rohwert in der zum Meter passenden Einheit."""
    return METER_INFO[kind].value_formatter(raw)


# Reihenfolge fürs UI (von oben nach unten)
METER_DISPLAY_ORDER: List[MeterKind] = [
    MeterKind.ALC,
    MeterKind.COMP,
    MeterKind.PO,
    MeterKind.SWR,
]


def format_rm_query(kind: MeterKind) -> str:
    return f"RM{METER_INFO[kind].index};"


def parse_rm_response(response: str, kind: MeterKind) -> int:
    """Holt den Rohwert aus ``RMn<value>;``.

    Wirft :class:`ValueError` (nicht ``CatProtocolError``), um nicht von
    der cat-Schicht abzuhängen — die ruft uns auf und wandelt um.
    """
    info = METER_INFO[kind]
    prefix = f"RM{info.index}"
    if not response.startswith(prefix) or not response.endswith(";"):
        raise ValueError(
            f"RM-Antwort hat falsches Format (erwartet {prefix}nnn;): {response!r}"
        )
    body = response[len(prefix):-1]
    if not body:
        raise ValueError(f"RM-Antwort ohne Wert: {response!r}")
    try:
        value = int(body)
    except ValueError as exc:
        raise ValueError(f"RM-Wert nicht numerisch: {response!r}") from exc
    # Wir clamping nicht — wenn das Funkgerät 256+ sendet, wollen wir das sehen.
    return value


# ----------------------------------------------------------------------
# S-Meter (``SM0;`` -> ``SM0nnn;`` mit nnn = 000..255)
# ----------------------------------------------------------------------

SMETER_QUERY = "SM0;"
"""Dedizierter S-Meter-Abruf. Liefert die Rohstärke 000..255 unabhängig
vom Front-Panel-Meter."""

SMETER_RAW_MIN = 0
SMETER_RAW_MAX = 255


def format_sm_query() -> str:
    return SMETER_QUERY


def parse_sm_response(response: str) -> int:
    """Holt den Rohwert aus ``SM0<value>;``."""
    if not response.startswith("SM0") or not response.endswith(";") or len(response) < 5:
        raise ValueError(
            f"SM-Antwort hat falsches Format (erwartet SM0nnn;): {response!r}"
        )
    body = response[3:-1]
    if not body:
        raise ValueError(f"SM-Antwort ohne Wert: {response!r}")
    try:
        return int(body)
    except ValueError as exc:
        raise ValueError(f"SM-Wert nicht numerisch: {response!r}") from exc


# S-Punkte-Skala laut Yaesu-Konvention (annähernd; firmware-abhängig):
# 0 .. 128 deckt S0..S9 ab, 128..255 deckt S9+10 .. S9+60 dB ab.
# Die Tabelle wird in der GUI für die Beschriftung der S-Meter-Skala
# verwendet.
#: List[(raw_value, label)] in aufsteigender Reihenfolge.
SMETER_TICKS: List[Tuple[int, str]] = [
    (0,   "S0"),
    (14,  "S1"),
    (28,  "S3"),
    (42,  "S5"),
    (56,  "S7"),
    (84,  "S9"),
    (128, "+10"),
    (170, "+20"),
    (212, "+40"),
    (255, "+60"),
]


def parse_tx_response(response: str) -> bool:
    """``TX0;`` -> False, ``TX1;`` / ``TX2;`` -> True."""
    if not response.startswith("TX") or not response.endswith(";") or len(response) < 4:
        raise ValueError(f"TX-Antwort hat falsches Format: {response!r}")
    state = response[2:-1]
    if state not in ("0", "1", "2"):
        raise ValueError(f"TX-Antwort hat unbekannten Status {state!r}: {response!r}")
    return state in ("1", "2")


def classify_value(kind: MeterKind, value: int) -> str:
    """Liefert ``'ok'`` / ``'warn'`` / ``'danger'`` für die UI-Farbgebung."""
    info = METER_INFO[kind]
    if info.raw_max <= 0:
        return "ok"
    frac = max(0.0, min(1.0, value / info.raw_max))
    if frac >= info.danger:
        return "danger"
    if frac >= info.warn:
        return "warn"
    return "ok"


def meter_choices() -> List[Tuple[MeterKind, MeterInfo]]:
    """Liefert ``[(kind, info), ...]`` in Anzeigereihenfolge."""
    return [(kind, METER_INFO[kind]) for kind in METER_DISPLAY_ORDER]
