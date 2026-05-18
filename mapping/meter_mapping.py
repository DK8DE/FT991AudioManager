"""Mappings für die Live-Meter (Version 0.4, korrigiert in 0.6).

Das Yaesu FT-991/FT-991A liefert über das ``RM``-Kommando mehrere Meter.
Die Indizes laut Manual 1711-D (FT-991 CAT Operation Reference Book, S. 16):

============ ====== ===========================================
``RM0;``     —      hängt vom Front-Panel-Meter ab (nicht nutzen)
``RM1;``     S      S-Meter (RX-Signalstärke)
``RM2;``     —      hängt vom Front-Panel-Meter ab (nicht nutzen)
``RM3;``     COMP   Speech-Processor-Kompression
``RM4;``     ALC    ALC-Aussteuerung
``RM5;``     PO     Power Out (Rohwert; Watt-Skala in der GUI bandabhängig)
``RM6;``     SWR    Stehwellen-Verhältnis (Rohwert)
``RM7;``     ID     Endstufen-Drain-Strom
``RM8;``     VDD    Endstufen-Versorgungsspannung
============ ====== ===========================================

Antwort-Format ist ``RMn<value>;`` mit ``<value>`` als 3-stelliger
Dezimalzahl (typisch 000..255).

Für das S-Meter wird ``SM0;`` verwendet (dedizierter S-Meter 000..255).
``RM1;`` kann am FT-991 feste Fehlwerte liefern (z. B. dauerhaft ~193) und
wird nicht mehr abgefragt.

Zusätzlich kennen wir den TX-Status über das ``TX``-Kommando:
``TX0;`` = RX, ``TX1;`` = TX per PTT/Mic, ``TX2;`` = TX per CAT.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

from model.smeter_calibration_settings import SmeterCalibrationSettings

#: PO (RM5): Standard-Stützpunkte (werden durch ``po_calibration.json`` ersetzt).
PO_WATTS_CALIB_HF_DEFAULT: List[Tuple[int, int]] = [
    (0, 0),
    (34, 5),
    (58, 10),
    (72, 15),
    (83, 20),
    (93, 25),
    (104, 30),
    (127, 35),
    (147, 50),
    (171, 70),
    (183, 80),
    (195, 90),
    (207, 100),
]
PO_WATTS_CALIB_VHF_DEFAULT: List[Tuple[int, int]] = list(PO_WATTS_CALIB_HF_DEFAULT)

# Rückwärtskompatibler Alias
PO_WATTS_CALIB_HF = PO_WATTS_CALIB_HF_DEFAULT

CALIB_BAND_HF = "hf_10m"
CALIB_BAND_VHF_2M = "vhf_2m"
CALIB_BAND_UHF_70CM = "uhf_70cm"

#: Kalibrierung: ``(eingestellte_watt, rm5_rohwert)`` je Band.
_po_calib_watt_raw_by_band: Dict[str, List[Tuple[int, int]]] = {
    CALIB_BAND_HF: [(w, r) for r, w in PO_WATTS_CALIB_HF_DEFAULT if w > 0],
    CALIB_BAND_VHF_2M: [(w, r) for r, w in PO_WATTS_CALIB_VHF_DEFAULT if w > 0],
    CALIB_BAND_UHF_70CM: [(w, r) for r, w in PO_WATTS_CALIB_VHF_DEFAULT if w > 0],
}
_po_calib_by_band: Dict[str, List[Tuple[int, int]]] = {}
# Rückwärtskompatibel
_po_calib_hf: List[Tuple[int, int]] = list(PO_WATTS_CALIB_HF_DEFAULT)
_po_calib_vhf: List[Tuple[int, int]] = list(PO_WATTS_CALIB_VHF_DEFAULT)
PO_CAT_RAW_FULL_HF = _po_calib_hf[-1][0]
PO_CAT_RAW_FULL_VHF = 147
PO_WATTS_HF = 100
PO_WATTS_VHF = 50


def calib_band_id_for_freq(freq_hz: Optional[int]) -> str:
    """Kalibrierkurve: nur 10-m-KW-Messung (für alle Bänder in der Anzeige)."""
    return CALIB_BAND_HF


def po_max_watts_for_freq(freq_hz: Optional[int]) -> int:
    """Nennleistung der Skala (100 W HF/6 m, 50 W 2 m / 70 cm)."""
    if freq_hz is None or freq_hz <= 0:
        return PO_WATTS_HF
    f = int(freq_hz)
    if f >= 144_000_000 and f < 420_000_000:
        return PO_WATTS_VHF
    if f >= 420_000_000:
        return PO_WATTS_VHF
    return PO_WATTS_HF


def _watt_raw_usable(pairs: List[Tuple[int, int]]) -> bool:
    """Kalibrierkurve brauchbar: mindestens zwei verschiedene RM5-Rohwerte."""
    raws = [r for w, r in pairs if w > 0]
    if len(raws) < 2:
        return False
    return len(set(raws)) >= 2 and (max(raws) - min(raws)) >= 5


def po_calib_watt_raw_for_freq(freq_hz: Optional[int]) -> List[Tuple[int, int]]:
    """Watt→Roh-Kalibrierpunkte — immer die 10-m-KW-Kalibrierung (RM5 ist bandunabhängig)."""
    pairs = _po_calib_watt_raw_by_band.get(CALIB_BAND_HF, [])
    if _watt_raw_usable(pairs):
        return pairs
    return [(w, r) for r, w in PO_WATTS_CALIB_HF_DEFAULT if w > 0]


def po_calib_table_for_freq(freq_hz: Optional[int]) -> List[Tuple[int, int]]:
    """``(rohwert, watt)`` für Skalen-Ticks (aus Watt→Roh-Punkten abgeleitet)."""
    return _raw_watt_table_from_watt_raw(po_calib_watt_raw_for_freq(freq_hz))


def _raw_watt_table_from_watt_raw(
    watt_raw: List[Tuple[int, int]],
) -> List[Tuple[int, int]]:
    raw_to_w: Dict[int, int] = {}
    for watts, raw in watt_raw:
        w, r = int(watts), int(raw)
        if w > 0:
            raw_to_w[r] = max(raw_to_w.get(r, 0), w)
    if not raw_to_w:
        return [(0, 0)]
    return [(0, 0)] + sorted(raw_to_w.items())


def _raw_at_watts(table: List[Tuple[int, int]], watts: int) -> Optional[int]:
    for raw, w in table:
        if w == watts:
            return raw
    return None


def _sync_full_scale_constants() -> None:
    global PO_CAT_RAW_FULL_HF, PO_CAT_RAW_FULL_VHF, _po_calib_hf, _po_calib_vhf
    global _po_calib_by_band, PO_WATTS_CALIB_HF
    hf_tbl = _raw_watt_table_from_watt_raw(
        _po_calib_watt_raw_by_band.get(CALIB_BAND_HF, [])
    )
    _po_calib_by_band[CALIB_BAND_HF] = hf_tbl
    _po_calib_hf = hf_tbl
    PO_WATTS_CALIB_HF = hf_tbl
    PO_CAT_RAW_FULL_HF = hf_tbl[-1][0] if hf_tbl else 255
    _po_calib_by_band[CALIB_BAND_VHF_2M] = hf_tbl
    _po_calib_by_band[CALIB_BAND_UHF_70CM] = hf_tbl
    _po_calib_vhf = hf_tbl
    raw50 = _raw_at_watts(hf_tbl, 50)
    PO_CAT_RAW_FULL_VHF = raw50 if raw50 is not None else PO_CAT_RAW_FULL_HF


def apply_po_calibration_watt_raw(
    bands: Dict[str, List[Tuple[int, int]]],
) -> None:
    """Setzt die 10-m-KW-Kalibrierung (andere Band-IDs werden ignoriert)."""
    pairs = bands.get(CALIB_BAND_HF)
    if pairs:
        cleaned = sorted(
            (int(w), int(r)) for w, r in pairs if int(w) > 0 and int(r) >= 0
        )
        _po_calib_watt_raw_by_band[CALIB_BAND_HF] = cleaned
    _sync_full_scale_constants()


def _table_raw_watt_to_watt_raw(
    table: List[Tuple[int, int]],
) -> List[Tuple[int, int]]:
    """``(rohwert, watt)`` → ``(watt, rohwert)``."""
    out: List[Tuple[int, int]] = []
    for raw, watts in table:
        if raw == 0 and watts == 0:
            continue
        out.append((int(watts), int(raw)))
    return sorted(out, key=lambda t: t[0])


def apply_po_calibration_tables(
    *,
    hf: Optional[List[Tuple[int, int]]] = None,
    vhf: Optional[List[Tuple[int, int]]] = None,
    uhf: Optional[List[Tuple[int, int]]] = None,
) -> None:
    """Setzt PO-Kurven aus ``(rohwert, watt)``-Tabellen (Tests/Legacy)."""
    bands: Dict[str, List[Tuple[int, int]]] = {}
    if hf:
        bands[CALIB_BAND_HF] = _table_raw_watt_to_watt_raw(hf)
    if vhf:
        bands[CALIB_BAND_VHF_2M] = _table_raw_watt_to_watt_raw(vhf)
    if uhf:
        bands[CALIB_BAND_UHF_70CM] = _table_raw_watt_to_watt_raw(uhf)
    if bands:
        apply_po_calibration_watt_raw(bands)


def load_po_calibration_from_disk() -> bool:
    """Lädt ``data/po_calibration.json``; ``True`` wenn angewendet."""
    try:
        from model.po_calibration_store import load_po_calibration

        cal = load_po_calibration()
    except Exception:
        return False
    pairs = cal.watt_raw_pairs(CALIB_BAND_HF)
    if not pairs:
        return False
    apply_po_calibration_watt_raw({CALIB_BAND_HF: pairs})
    return True


def _raw_to_watts_from_watt_raw(
    watt_raw: List[Tuple[int, int]],
    raw: int,
    *,
    max_watts: float,
) -> float:
    """Rohwert → Watt entlang der gemessenen Leistungsstufen (interpoliert)."""
    val = max(0, min(255, int(raw)))
    pts = sorted([(0, 0)] + [(int(w), int(r)) for w, r in watt_raw if w > 0], key=lambda t: t[0])
    if len(pts) < 2:
        return 0.0

    candidates: List[float] = []
    for (w0, r0), (w1, r1) in zip(pts, pts[1:]):
        if r0 == r1:
            if val == r0:
                candidates.append(float(w1))
            continue
        lo_r, hi_r = min(r0, r1), max(r0, r1)
        if lo_r <= val <= hi_r:
            frac = (val - r0) / (r1 - r0)
            candidates.append(w0 + frac * (w1 - w0))

    if candidates:
        return min(max(candidates), max_watts)

    _w1, r1 = pts[1]
    if val < r1 and r1 > 0:
        return min(max_watts, _w1 * val / r1)

    w_max, r_max = pts[-1]
    if val >= r_max:
        return min(max_watts, float(w_max))

    return 0.0


def _watts_to_raw_from_watt_raw(
    watt_raw: List[Tuple[int, int]],
    watts: float,
) -> int:
    target = max(0.0, float(watts))
    if target <= 0:
        return 0
    pts = sorted([(int(w), int(r)) for w, r in watt_raw if w > 0], key=lambda t: t[0])
    if not pts:
        return 0
    exact = {w: r for w, r in pts}
    tw = int(round(target))
    if tw in exact:
        return exact[tw]
    for (w0, r0), (w1, r1) in zip(pts, pts[1:]):
        if w0 <= target <= w1:
            if w1 <= w0:
                return r1
            frac = (target - w0) / (w1 - w0)
            return int(round(r0 + frac * (r1 - r0)))
    return pts[-1][1]


_sync_full_scale_constants()

# Beim Import gespeicherte Kalibrierung laden (falls vorhanden).
load_po_calibration_from_disk()


def po_use_50w_scale(freq_hz: Optional[int]) -> bool:
    """True → 50-W-Skala (2 m / 70 cm), sonst 100-W-Skala."""
    return po_max_watts_for_freq(freq_hz) <= PO_WATTS_VHF


def po_raw_to_watts(
    raw: int,
    *,
    vhf_uhf: bool = False,
    freq_hz: Optional[int] = None,
) -> float:
    """PO-Rohwert → Watt (Interpolation entlang der Kalibrier-Leistungsstufen)."""
    if freq_hz is None:
        freq_hz = 145_500_000 if vhf_uhf else 14_000_000
    max_w = float(po_max_watts_for_freq(freq_hz))
    pairs = po_calib_watt_raw_for_freq(freq_hz)
    return _raw_to_watts_from_watt_raw(pairs, raw, max_watts=max_w)


def po_watts_to_raw(
    watts: float,
    *,
    vhf_uhf: bool = False,
    freq_hz: Optional[int] = None,
) -> int:
    """Watt → Rohwert (aus den Kalibrier-Stützpunkten)."""
    if freq_hz is None:
        freq_hz = 145_500_000 if vhf_uhf else 14_000_000
    return _watts_to_raw_from_watt_raw(po_calib_watt_raw_for_freq(freq_hz), watts)


def po_bar_fraction(
    raw: int,
    *,
    vhf_uhf: bool = False,
    freq_hz: Optional[int] = None,
) -> float:
    """Relativer Balkenfüllgrad 0..1 entlang der Watt-Skala."""
    if freq_hz is None:
        freq_hz = 145_500_000 if vhf_uhf else 14_000_000
    ref = po_max_watts_for_freq(freq_hz)
    if ref <= 0:
        return 0.0
    return max(0.0, min(1.0, po_raw_to_watts(raw, freq_hz=freq_hz) / ref))


def po_power_ticks_for_freq(freq_hz: Optional[int]) -> List[Tuple[int, str]]:
    if po_use_50w_scale(freq_hz):
        labels = (0, 10, 20, 30, 40, 50)
    else:
        labels = (0, 25, 50, 75, 100)
    ticks = [(po_watts_to_raw(w, freq_hz=freq_hz), str(w)) for w in labels]
    return sorted(ticks, key=lambda t: t[0])


def po_power_ticks_hf() -> List[Tuple[int, str]]:
    return po_power_ticks_for_freq(14_000_000)


def po_power_ticks_vhf() -> List[Tuple[int, str]]:
    return po_power_ticks_for_freq(145_500_000)


def format_po_watts(
    raw: int,
    *,
    vhf_uhf: bool = False,
    freq_hz: Optional[int] = None,
) -> str:
    """PO-Rohwert → Watt-Anzeige (Kalibrierkurve)."""
    if freq_hz is None:
        freq_hz = 145_500_000 if vhf_uhf else 14_000_000
    w = po_raw_to_watts(raw, freq_hz=freq_hz)
    return f"{max(0, round(w))} W"


class MeterKind(str, Enum):
    COMP = "comp"
    ALC = "alc"
    PO = "po"
    SWR = "swr"


# ----------------------------------------------------------------------
# Tick-Tabellen für die Skalen-Beschriftung der TX-Meter
# ----------------------------------------------------------------------

#: ALC / COMP: Prozent — Rohwert 0..255 auf 0..100 %.
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
        index=5, label="POWER", raw_max=255, warn=0.80, danger=0.95,
        unit="", ticks=po_power_ticks_hf(),
        value_formatter=lambda raw: format_po_watts(raw, vhf_uhf=False),
    ),
    MeterKind.SWR: MeterInfo(
        index=6, label="SWR", raw_max=255, warn=0.30, danger=0.50,
        unit=":1", ticks=_SWR_TICKS, value_formatter=_format_swr,
    ),
}


def format_meter_value(kind: MeterKind, raw: int) -> str:
    """Formatiert einen Rohwert in der zum Meter passenden Einheit."""
    return METER_INFO[kind].value_formatter(raw)


def format_meter_value_po(raw: int, *, vhf_uhf: bool) -> str:
    """PO-Anzeige mit bandrichtiger Watt-Skala (für Tests/Hilfen)."""
    return format_po_watts(raw, vhf_uhf=vhf_uhf)


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
# S-Meter (``SM0;`` -> ``SM0nnn;``)
# ----------------------------------------------------------------------

SMETER_QUERY = "SM0;"
"""S-Meter-Rohwert 000..255 (Panel-Näherung, Kalibrierung unten)."""

SMETER_RAW_MIN = 0
SMETER_RAW_MAX = 255


def format_sm_query() -> str:
    return SMETER_QUERY


def parse_sm_response(response: str) -> int:
    """Holt den Rohwert aus ``SM0nnn;``."""
    if not response.startswith("SM0") or not response.endswith(";") or len(response) < 5:
        raise ValueError(
            f"SM-Antwort hat falsches Format (erwartet SM0nnn;): {response!r}"
        )
    body = response[3:-1]
    if not body:
        raise ValueError(f"S-Meter-Antwort ohne Wert: {response!r}")
    try:
        return int(body)
    except ValueError as exc:
        raise ValueError(f"S-Meter-Wert nicht numerisch: {response!r}") from exc


# ``SM0`` → Panel-Anzeige (FT-991/991A), Messpunkte vom Anwender.
# S9+38 = raw 138, S9+40 = raw 144 → S9 = raw 104.
#: List[(raw_value, db_over_s9)] — Stützpunkte, linear interpoliert.
SMETER_CAL_RAW_DB: List[Tuple[int, int]] = [
    (0,   -54),  # S0
    (12,  -48),  # S1
    (23,  -42),  # S2
    (35,  -36),  # S3
    (46,  -30),  # S4
    (58,  -24),  # S5
    (69,  -18),  # S6
    (81,  -12),  # S7
    (92,   -6),  # S8
    (104,   0),  # S9
    (138,  38),  # gemessen
    (144,  40),  # gemessen
    (204,  60),  # Fortsetzung 2 dB / 6 raw (wie 138..144)
]
SMETER_RAW_S9 = 104
SMETER_RAW_S9P60 = 204
SMETER_DB_MIN = SMETER_CAL_RAW_DB[0][1]
SMETER_DB_MAX = SMETER_CAL_RAW_DB[-1][1]

#: Balkenfüllung: die Spitze entspricht immer S9+60 (nicht dem Extrapolationswert bei raw 255).
SMETER_BAR_DISPLAY_DB_MAX = float(SMETER_DB_MAX)

#: Ab dieser VFO-Frequenz (Hz): zweite Kalibrierkurve (2 m / 70 cm); darunter Kurzwelle/HF inkl. 6 m.
SMETER_CALIB_VHF_MIN_HZ = 50_000_000

#: S-Meter: je Band mindestens zwei ``(raw, db_über_s9)``, sonst :data:`SMETER_CAL_RAW_DB`.
_user_smeter_pts_hf: Optional[List[Tuple[int, float]]] = None
_user_smeter_pts_vhf: Optional[List[Tuple[int, float]]] = None
_smeter_calibration_freq_hz: int = 0

#: Feste Skalen-Beschriftung: dB über S9 → Rohwert per aktueller Kalibrierung.
#: Nur diese Marken werden gezeichnet (keine weiteren Zwischenstriche).
SMETER_SCALE_MARKS: List[Tuple[float, str]] = [
    (-48.0, "S1"),
    (-36.0, "S3"),
    (-24.0, "S5"),
    (-12.0, "S7"),
    (0.0, "S9"),
    (20.0, "S9+20"),
    (40.0, "S9+40"),
    (60.0, "S9+60"),
]


def smeter_set_calibration_frequency_hz(hz: Optional[int]) -> None:
    """VFO-A-Frequenz für die Wahl HF- vs. VHF-Kalibrierkurve (Rig-Bridge / Anzeige)."""
    global _smeter_calibration_freq_hz
    try:
        _smeter_calibration_freq_hz = max(0, int(hz or 0))
    except (TypeError, ValueError):
        _smeter_calibration_freq_hz = 0


def _smeter_use_vhf_calibration(hz: int) -> bool:
    return int(hz) >= SMETER_CALIB_VHF_MIN_HZ


def _active_user_smeter_pts() -> Optional[List[Tuple[int, float]]]:
    """Nutzerkurve für aktuelle Frequenz, oder ``None`` → Werkstabelle."""
    hz = _smeter_calibration_freq_hz
    cand = (
        _user_smeter_pts_vhf
        if _smeter_use_vhf_calibration(hz)
        else _user_smeter_pts_hf
    )
    if cand is not None and len(cand) >= 2:
        return cand
    return None


def _smeter_cal_sequence_raw_sorted() -> List[Tuple[float, float]]:
    """Stützpunkte ``(raw, db)`` sortiert nach *raw* (aktuelle Kalibrierkurve)."""
    u = _active_user_smeter_pts()
    if u is not None:
        return sorted((float(r), float(db)) for r, db in u)
    return [(float(r), float(db)) for r, db in SMETER_CAL_RAW_DB]


def _piecewise_smeter_db_to_raw(target_db: float) -> float:
    """Umkehrung der Rohwert→dB-Kurve (linear pro Segment, Rand extrapoliert)."""
    seq = _smeter_cal_sequence_raw_sorted()
    if not seq:
        raise ValueError("S-Meter-Kalibrierung: leere Kurve")
    if len(seq) == 1:
        return seq[0][0]
    t = float(target_db)
    r0, d0 = seq[0]
    r1, d1 = seq[1]
    if t <= min(d0, d1):
        if d1 == d0:
            return r0
        return r0 + (t - d0) * (r1 - r0) / (d1 - d0)
    r_lm1, d_lm1 = seq[-2]
    r_l, d_l = seq[-1]
    if t >= max(d_lm1, d_l):
        if d_l == d_lm1:
            return r_l
        return r_l + (t - d_l) * (r_l - r_lm1) / (d_l - d_lm1)
    for i in range(len(seq) - 1):
        ra, da = seq[i]
        rb, db_ = seq[i + 1]
        lo, hi = min(da, db_), max(da, db_)
        if lo <= t <= hi:
            if db_ == da:
                return rb
            return ra + (t - da) / (db_ - da) * (rb - ra)
    return r_l


def smeter_db_to_raw(target_db: float) -> int:
    """Ziel-dB über S9 → ``SM0``-Rohwert (0…255) laut Kalibrierung."""
    x = _piecewise_smeter_db_to_raw(float(target_db))
    return int(max(SMETER_RAW_MIN, min(SMETER_RAW_MAX, round(x))))


def smeter_scale_ticks() -> List[Tuple[int, str]]:
    """Skalen-Ticks nur für :data:`SMETER_SCALE_MARKS`; Rohwerte aus der Kurve."""
    return [(smeter_db_to_raw(db), label) for db, label in SMETER_SCALE_MARKS]


def _piecewise_linear_smeter_db(
    x: float, pts: List[Tuple[int, float]]
) -> float:
    """Linear zwischen Stützpunkten; nach außen mit dem Steigungsmaß des Randsegments."""
    if not pts:
        raise ValueError("S-Meter-Kalibrierung: keine Stützpunkte")
    seq = sorted((float(r), float(db)) for r, db in pts)
    x = max(float(SMETER_RAW_MIN), min(float(SMETER_RAW_MAX), float(x)))
    if len(seq) == 1:
        return seq[0][1]
    x0, y0 = seq[0]
    x1, y1 = seq[1]
    if x <= x0:
        if x1 == x0:
            return y0
        slope = (y1 - y0) / (x1 - x0)
        return y0 + (x - x0) * slope
    xm1, ym1 = seq[-2]
    x_last, y_last = seq[-1]
    if x >= x_last:
        if x_last == xm1:
            return y_last
        slope = (y_last - ym1) / (x_last - xm1)
        return y_last + (x - x_last) * slope
    for i in range(len(seq) - 1):
        xa, ya = seq[i]
        xb, yb = seq[i + 1]
        if xa <= x <= xb:
            if xb == xa:
                return yb
            return ya + (x - xa) / (xb - xa) * (yb - ya)
    return seq[-1][1]


def _smeter_bar_db_endpoints() -> Tuple[float, float]:
    """Untere Grenze aus der Kurve; obere Grenze fest :data:`SMETER_BAR_DISPLAY_DB_MAX` (S9+60)."""
    hi = SMETER_BAR_DISPLAY_DB_MAX
    u = _active_user_smeter_pts()
    if u is not None:
        pts = u
        at0 = _piecewise_linear_smeter_db(0.0, pts)
        at255 = _piecewise_linear_smeter_db(255.0, pts)
        anchor = [db for _, db in pts]
        lo = min(at0, at255, *anchor)
    else:
        lo = float(SMETER_DB_MIN)
    if hi <= lo:
        return lo, lo + 1.0
    return lo, hi


def reset_smeter_calibration() -> None:
    """Zur fest eingebauten S-Meter-Tabelle zurück (Tests / nach Deaktivierung)."""
    global _user_smeter_pts_hf, _user_smeter_pts_vhf, _smeter_calibration_freq_hz
    _user_smeter_pts_hf = None
    _user_smeter_pts_vhf = None
    _smeter_calibration_freq_hz = 0


def apply_smeter_calibration_from_settings(s: SmeterCalibrationSettings) -> None:
    """Übernimmt die Kalibrierung aus den App-Einstellungen (oder schaltet sie ab)."""
    global _user_smeter_pts_hf, _user_smeter_pts_vhf
    if not s.use_custom:
        _user_smeter_pts_hf = None
        _user_smeter_pts_vhf = None
        return
    hf = s.effective_points_hf()
    vhf = s.effective_points_vhf()
    _user_smeter_pts_hf = (
        [(p.raw, p.db_over_s9) for p in hf] if len(hf) >= 2 else None
    )
    _user_smeter_pts_vhf = (
        [(p.raw, p.db_over_s9) for p in vhf] if len(vhf) >= 2 else None
    )


def _smeter_interp_db(raw: int) -> float:
    """Rohwert → dB über S9 (Nutzerkurve oder :data:`SMETER_CAL_RAW_DB`)."""
    val = max(SMETER_RAW_MIN, min(SMETER_RAW_MAX, int(raw)))
    u = _active_user_smeter_pts()
    if u is not None:
        return _piecewise_linear_smeter_db(float(val), u)
    pts = SMETER_CAL_RAW_DB
    if val <= pts[0][0]:
        return float(pts[0][1])
    if val >= pts[-1][0]:
        return float(pts[-1][1])
    for (r0, db0), (r1, db1) in zip(pts, pts[1:]):
        if r0 <= val <= r1:
            if r1 == r0:
                return float(db1)
            frac = (val - r0) / (r1 - r0)
            return db0 + frac * (db1 - db0)
    return float(pts[-1][1])


def smeter_raw_to_db(raw: int) -> float:
    """``SM0``-Rohwert → dB relativ zu S9."""
    return _smeter_interp_db(raw)


def smeter_bar_fraction(raw: int) -> float:
    """Balkenfüllgrad 0..1 entlang der kalibrierten dB-Skala."""
    db = _smeter_interp_db(raw)
    lo, hi = _smeter_bar_db_endpoints()
    span = hi - lo
    if span <= 0:
        return 0.0
    return max(0.0, min(1.0, (db - lo) / span))


def format_smeter_label(raw: int) -> str:
    """Anzeige wie am Funkgerät inkl. Rohwert zur Diagnose."""
    db = _smeter_interp_db(raw)
    if db >= -3.0:
        if db < 0.5:
            label = "S9"
        else:
            label = f"S9+{round(db)}"
    else:
        s = max(0, min(9, round((db + 54.0) / 6.0)))
        label = f"S{s}"
    return f"{label} · {int(raw)}"


#: TX-Status laut FT-991 CAT-Manual:
#:  - 0: RX (kein TX)
#:  - 1: CAT TX aktiv (TX1; gesendet, MIC PTT aus)
#:  - 2: Radio TX (MIC PTT / Front-Panel), CAT TX aus
TX_STATE_RX = 0
TX_STATE_CAT_TX = 1
TX_STATE_MIC_PTT = 2


def parse_tx_state(response: str) -> int:
    """``TXn;`` → 0/1/2 (siehe ``TX_STATE_*``)."""
    if not response.startswith("TX") or not response.endswith(";") or len(response) < 4:
        raise ValueError(f"TX-Antwort hat falsches Format: {response!r}")
    state = response[2:-1]
    if state not in ("0", "1", "2"):
        raise ValueError(f"TX-Antwort hat unbekannten Status {state!r}: {response!r}")
    return int(state)


def parse_tx_response(response: str) -> bool:
    """``TX0;`` -> False, ``TX1;`` / ``TX2;`` -> True."""
    return parse_tx_state(response) != TX_STATE_RX


def classify_value(
    kind: MeterKind,
    value: int,
    *,
    po_vhf_uhf: bool = False,
    po_freq_hz: Optional[int] = None,
) -> str:
    """Liefert ``'ok'`` / ``'warn'`` / ``'danger'`` für die UI-Farbgebung."""
    info = METER_INFO[kind]
    if kind == MeterKind.PO:
        frac = po_bar_fraction(
            value,
            vhf_uhf=po_vhf_uhf,
            freq_hz=po_freq_hz,
        )
    elif info.raw_max <= 0:
        return "ok"
    else:
        frac = max(0.0, min(1.0, value / info.raw_max))
    if frac >= info.danger:
        return "danger"
    if frac >= info.warn:
        return "warn"
    return "ok"


def meter_choices() -> List[Tuple[MeterKind, MeterInfo]]:
    """Liefert ``[(kind, info), ...]`` in Anzeigereihenfolge."""
    return [(kind, METER_INFO[kind]) for kind in METER_DISPLAY_ORDER]
