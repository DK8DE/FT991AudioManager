"""Mappings für RX-Status-Werte (Version 0.6).

Diese Befehle sind keine "Meter" im klassischen Sinn (kein RM…), sondern
**Status-/Pegel-Werte**, die wir beim Empfang anzeigen wollen:

============ ====== ===========================================
``SQ0;``     0..100 Squelch-Schwelle (User-Einstellung)
``AG0;``     0..255 AF-Gain (Lautstärke)
``RG0;``     0..255 RF-Gain (Eingangsverstärkung)
``BC0;``     0/1    Auto-Notch (DNF)
``NB0;``     0/1    Noise-Blanker an/aus
``NL0;``     0..10  Noise-Blanker-Level
``NR0;``     0/1    Noise-Reduction (DNR) an/aus
``RL0;``     1..15  Noise-Reduction-Level
``GT0;``     0..6   AGC-Modus (OFF/FAST/MID/SLOW/AUTO-F/AUTO-M/AUTO-S)
``MD0;``     1..E   aktuelle Betriebsart (Hex-Ziffer)
``FA;``      Hz     VFO-A Frequenz
============ ====== ===========================================

Alle Funktionen sind reine Encoder/Decoder; sie wirft :class:`ValueError`,
wenn etwas nicht parsebar ist. Die ``cat``-Schicht setzt das später in
:class:`CatProtocolError` um.

Quelle: Manual 1711-D (FT-991 CAT Operation Reference Book).
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, Tuple


# ----------------------------------------------------------------------
# AGC (GT)
# ----------------------------------------------------------------------

class AgcMode(str, Enum):
    OFF = "off"
    FAST = "fast"
    MID = "mid"
    SLOW = "slow"
    AUTO = "auto"


#: Empirisch belegtes Mapping für das FT-991A. Die Frontplatte zeigt nur
#: OFF/FAST/MID/SLOW/AUTO, das CAT-Statusregister verwendet aber **das
#: erweiterte FTDX-Schema** mit drei AUTO-Sub-Modi:
#:
#:   0=OFF, 1=FAST, 2=MID, 3=SLOW, 4=AUTO-F, 5=AUTO-M, 6=AUTO-S
#:
#: Bei „AUTO" wählt das Radio je nach Betriebsart und Band intern eine
#: dieser drei AUTO-Varianten — wir bekommen über ``GT0;`` z. B. auf 2 m /
#: 70 cm gerne ``GT06;`` zurück. Für die GUI sind alle drei AUTO-Sub-Modi
#: gleichbedeutend „AUTO".
#:
#: Frühere Versionen dieses Mappings hatten entweder das falsche FTDX-
#: Mapping (Schreiben ``GT05;`` für AUTO klappte nur teilweise) oder ein
#: zu strenges 0..4-Mapping (Lesen ``GT06;`` warf ValueError und die GUI
#: zeigte AUTO nicht). Beide Varianten haben dazu geführt, dass „AUTO
#: setzen" nicht zuverlässig funktioniert hat.
_AGC_INDEX_TO_MODE: Dict[int, AgcMode] = {
    0: AgcMode.OFF,
    1: AgcMode.FAST,
    2: AgcMode.MID,
    3: AgcMode.SLOW,
    4: AgcMode.AUTO,  # AUTO-FAST intern
    5: AgcMode.AUTO,  # AUTO-MID intern
    6: AgcMode.AUTO,  # AUTO-SLOW intern
}

AGC_LABELS = {
    AgcMode.OFF: "OFF",
    AgcMode.FAST: "FAST",
    AgcMode.MID: "MID",
    AgcMode.SLOW: "SLOW",
    AgcMode.AUTO: "AUTO",
}


def format_agc_query() -> str:
    return "GT0;"


#: Mapping :class:`AgcMode` -> Index, den wir beim **Schreiben** verwenden.
#: Für AUTO senden wir ``GT04;`` (AUTO-FAST). Das Funkgerät akzeptiert das
#: als „AUTO" und wechselt intern eigenständig auf AUTO-MID/AUTO-SLOW, je
#: nach Modus und Band — beim nächsten Read kommt dann z. B. ``GT06;``
#: zurück, das wir oben tolerant wieder zu ``AUTO`` mappen.
_AGC_INDEX_BY_MODE: Dict[AgcMode, int] = {
    AgcMode.OFF: 0,
    AgcMode.FAST: 1,
    AgcMode.MID: 2,
    AgcMode.SLOW: 3,
    AgcMode.AUTO: 4,
}


def format_agc_set(mode: AgcMode) -> str:
    """``AgcMode`` -> ``GT0n;``."""
    try:
        idx = _AGC_INDEX_BY_MODE[mode]
    except KeyError as exc:
        raise ValueError(f"Unbekannter AGC-Mode: {mode!r}") from exc
    return f"GT0{idx};"


#: Die 4 Auswahl-Positionen, die der AGC-Slider in der GUI anbietet
#: (AUTO, FAST, MID, SLOW). OFF kommt aus dem Menü weiter unten / über
#: das Radio selbst und wird im Slider als „neutral" dargestellt.
AGC_SLIDER_MODES: Tuple[AgcMode, ...] = (
    AgcMode.AUTO,
    AgcMode.FAST,
    AgcMode.MID,
    AgcMode.SLOW,
)
AGC_SLIDER_LABELS: Tuple[str, ...] = ("AUTO", "FAST", "MID", "SLOW")


def agc_mode_to_slider_pos(mode: AgcMode) -> int:
    """Mappt einen vom Radio gemeldeten AGC-Modus auf die Slider-Position.

    ``OFF`` liefert ``-1`` zurück — der Aufrufer entscheidet, wie er das
    anzeigt (z.B. Slider neutral / kein Wert).
    """
    if mode == AgcMode.AUTO:
        return 0
    if mode == AgcMode.FAST:
        return 1
    if mode == AgcMode.MID:
        return 2
    if mode == AgcMode.SLOW:
        return 3
    return -1


def parse_agc_response(response: str) -> AgcMode:
    """``GT0n;`` -> :class:`AgcMode`.

    Akzeptiert die Indizes 0..6 (FTDX-Schema). Indizes 4/5/6 werden alle
    auf :data:`AgcMode.AUTO` gemappt — siehe Doku zu
    ``_AGC_INDEX_TO_MODE``.
    """
    if not response.startswith("GT0") or not response.endswith(";") or len(response) != 5:
        raise ValueError(f"GT-Antwort hat falsches Format: {response!r}")
    body = response[3:-1]
    try:
        index = int(body)
    except ValueError as exc:
        raise ValueError(f"GT-Wert nicht numerisch: {response!r}") from exc
    mode = _AGC_INDEX_TO_MODE.get(index)
    if mode is None:
        raise ValueError(
            f"AGC-Index {index} unbekannt (erwartet 0..6)"
        )
    return mode


# ----------------------------------------------------------------------
# Betriebsart (MD)
# ----------------------------------------------------------------------

class RxMode(str, Enum):
    LSB = "LSB"
    USB = "USB"
    CW_U = "CW-U"
    FM = "FM"
    AM = "AM"
    RTTY_LSB = "RTTY-LSB"
    CW_L = "CW-L"
    DATA_LSB = "DATA-LSB"
    RTTY_USB = "RTTY-USB"
    DATA_FM = "DATA-FM"
    FM_N = "FM-N"
    DATA_USB = "DATA-USB"
    AM_N = "AM-N"
    C4FM = "C4FM"
    UNKNOWN = "?"


#: Hex-Index aus dem Manual → ``RxMode``. Beachten: 'A'..'E' sind Hex-Buchstaben.
_MD_CODE_TO_MODE = {
    "1": RxMode.LSB,
    "2": RxMode.USB,
    "3": RxMode.CW_U,
    "4": RxMode.FM,
    "5": RxMode.AM,
    "6": RxMode.RTTY_LSB,
    "7": RxMode.CW_L,
    "8": RxMode.DATA_LSB,
    "9": RxMode.RTTY_USB,
    "A": RxMode.DATA_FM,
    "B": RxMode.FM_N,
    "C": RxMode.DATA_USB,
    "D": RxMode.AM_N,
    "E": RxMode.C4FM,
}


def format_mode_query() -> str:
    return "MD0;"


def parse_mode_response(response: str) -> RxMode:
    """``MD0X;`` -> :class:`RxMode`. Unbekannte Codes liefern ``UNKNOWN``."""
    if not response.startswith("MD0") or not response.endswith(";") or len(response) != 5:
        raise ValueError(f"MD-Antwort hat falsches Format: {response!r}")
    code = response[3].upper()
    return _MD_CODE_TO_MODE.get(code, RxMode.UNKNOWN)


def mode_group_for(mode: RxMode) -> str:
    """Liefert die Profil-Modusgruppe (SSB/AM/FM/DATA/CW/RTTY/C4FM)."""
    return _MODE_GROUPS.get(mode, "OTHER")


_MODE_GROUPS = {
    RxMode.LSB: "SSB",
    RxMode.USB: "SSB",
    RxMode.AM: "AM",
    RxMode.AM_N: "AM",
    RxMode.FM: "FM",
    RxMode.FM_N: "FM",
    RxMode.CW_U: "CW",
    RxMode.CW_L: "CW",
    RxMode.RTTY_LSB: "RTTY",
    RxMode.RTTY_USB: "RTTY",
    RxMode.DATA_LSB: "DATA",
    RxMode.DATA_USB: "DATA",
    RxMode.DATA_FM: "DATA",
    RxMode.C4FM: "C4FM",
}


# Umkehrung des MD-Code-Mappings (RxMode → CAT-Hex-Code).
MODE_TO_CODE: Dict[RxMode, str] = {v: k for k, v in _MD_CODE_TO_MODE.items()}


#: Standardmodus pro Profil-Mode-Gruppe — wird verwendet, wenn die GUI die
#: Mode-Gruppe wechselt und dem Radio einen passenden Operating-Mode setzen
#: muss. Für SSB nehmen wir USB (auf Bändern <10 MHz kann der Anwender am
#: Radio jederzeit auf LSB schalten — Gruppe bleibt dabei „SSB").
DEFAULT_MODE_FOR_GROUP: Dict[str, RxMode] = {
    "SSB": RxMode.USB,
    "AM": RxMode.AM,
    "FM": RxMode.FM,
    "DATA": RxMode.DATA_USB,
    "C4FM": RxMode.C4FM,
}


def format_mode_set(mode: RxMode) -> str:
    """``RxMode`` → ``MD0X;`` Schreibkommando."""
    code = MODE_TO_CODE.get(mode)
    if code is None:
        raise ValueError(f"Mode {mode!r} kennt keinen MD-Code")
    return f"MD0{code};"


# ----------------------------------------------------------------------
# Frequenz (FA — VFO-A)
# ----------------------------------------------------------------------

def format_frequency_query() -> str:
    return "FA;"


def parse_frequency_response(response: str) -> int:
    """``FAnnnnnnnnn;`` -> Frequenz in Hz (9-stellig)."""
    return _parse_vfo_response(response, "FA")


def format_frequency_b_query() -> str:
    return "FB;"


def parse_frequency_b_response(response: str) -> int:
    """``FBnnnnnnnnn;`` -> VFO-B-Frequenz in Hz (9-stellig)."""
    return _parse_vfo_response(response, "FB")


def _parse_vfo_response(response: str, prefix: str) -> int:
    if not response.startswith(prefix) or not response.endswith(";") or len(response) != 12:
        raise ValueError(f"{prefix}-Antwort hat falsches Format: {response!r}")
    body = response[2:-1]
    try:
        return int(body)
    except ValueError as exc:
        raise ValueError(f"{prefix}-Wert nicht numerisch: {response!r}") from exc


def format_frequency_hz(hz: int) -> str:
    """Hübsche Darstellung: ``14.250.000 Hz`` -> ``14.250.000 MHz`` nein,
    Yaesu zeigt typisch ``14.250.000`` (Hz mit Punkten) — wir nehmen MHz
    mit 6 Nachkommastellen, das ist eindeutig und kompakt."""
    mhz = hz / 1_000_000.0
    return f"{mhz:.6f} MHz"


# ----------------------------------------------------------------------
# Pegel-Werte (SQ, AG, RG)
# ----------------------------------------------------------------------

def _parse_three_digit_value(response: str, prefix: str, max_value: int) -> int:
    if not response.startswith(prefix) or not response.endswith(";") or len(response) != len(prefix) + 4:
        raise ValueError(f"{prefix}-Antwort hat falsches Format: {response!r}")
    body = response[len(prefix):-1]
    try:
        value = int(body)
    except ValueError as exc:
        raise ValueError(f"{prefix}-Wert nicht numerisch: {response!r}") from exc
    if not 0 <= value <= max_value:
        # Yaesu-Geräte können ausserhalb senden — wir clampen NICHT, sondern lassen
        # die GUI das erkennen; ein hartes Werfen wäre hier zu strikt.
        pass
    return value


def format_squelch_query() -> str:
    return "SQ0;"


def parse_squelch_response(response: str) -> int:
    """``SQ0nnn;`` -> 0..100."""
    return _parse_three_digit_value(response, "SQ0", 100)


def format_af_gain_query() -> str:
    return "AG0;"


def parse_af_gain_response(response: str) -> int:
    """``AG0nnn;`` -> 0..255."""
    return _parse_three_digit_value(response, "AG0", 255)


def format_rf_gain_query() -> str:
    return "RG0;"


def parse_rf_gain_response(response: str) -> int:
    """``RG0nnn;`` -> 0..255."""
    return _parse_three_digit_value(response, "RG0", 255)


# ----------------------------------------------------------------------
# Noise-Blanker (NB + NL)
# ----------------------------------------------------------------------

def format_nb_query() -> str:
    return "NB0;"


def parse_nb_response(response: str) -> bool:
    """``NB0n;`` -> True wenn NB an."""
    if not response.startswith("NB0") or not response.endswith(";") or len(response) != 5:
        raise ValueError(f"NB-Antwort hat falsches Format: {response!r}")
    state = response[3]
    if state not in ("0", "1"):
        raise ValueError(f"NB-Status {state!r} unbekannt (erwartet 0 oder 1)")
    return state == "1"


def format_nb_level_query() -> str:
    return "NL0;"


def parse_nb_level_response(response: str) -> int:
    """``NL0nnn;`` -> 0..10."""
    return _parse_three_digit_value(response, "NL0", 10)


# ----------------------------------------------------------------------
# Noise-Reduction (NR + RL)
# ----------------------------------------------------------------------

def format_nr_query() -> str:
    return "NR0;"


def parse_nr_response(response: str) -> bool:
    """``NR0n;`` -> True wenn NR an."""
    if not response.startswith("NR0") or not response.endswith(";") or len(response) != 5:
        raise ValueError(f"NR-Antwort hat falsches Format: {response!r}")
    state = response[3]
    if state not in ("0", "1"):
        raise ValueError(f"NR-Status {state!r} unbekannt (erwartet 0 oder 1)")
    return state == "1"


def format_nr_level_query() -> str:
    return "RL0;"


def parse_nr_level_response(response: str) -> int:
    """``RL0nn;`` -> 1..15. RL nutzt 2-stellige Werte."""
    if not response.startswith("RL0") or not response.endswith(";") or len(response) != 6:
        raise ValueError(f"RL-Antwort hat falsches Format: {response!r}")
    body = response[3:-1]
    try:
        value = int(body)
    except ValueError as exc:
        raise ValueError(f"RL-Wert nicht numerisch: {response!r}") from exc
    return value


# ----------------------------------------------------------------------
# Auto-Notch (BC)
# ----------------------------------------------------------------------

def format_auto_notch_query() -> str:
    return "BC0;"


def parse_auto_notch_response(response: str) -> bool:
    """``BC0n;`` -> True wenn Auto-Notch an."""
    if not response.startswith("BC0") or not response.endswith(";") or len(response) != 5:
        raise ValueError(f"BC-Antwort hat falsches Format: {response!r}")
    state = response[3]
    if state not in ("0", "1"):
        raise ValueError(f"BC-Status {state!r} unbekannt (erwartet 0 oder 1)")
    return state == "1"


# ----------------------------------------------------------------------
# Schreiben — DSP-Schalter & Pegel (NB / NL / NR / RL / BC)
#
# Die Werte werden interaktiv aus den vertikalen Slidern im MeterWidget
# gesetzt. Range-Bereiche:
#   * NB  on/off       (NB0n;)
#   * NL  0..10        (NL0nnn;)
#   * NR  on/off       (NR0n;)
#   * RL  1..15        (RL0nn;)
#   * BC  on/off       (BC0n;)
# ----------------------------------------------------------------------

NB_LEVEL_MIN = 0
NB_LEVEL_MAX = 10

NR_LEVEL_MIN = 1
NR_LEVEL_MAX = 15


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(value)))


def format_nb_set(on: bool) -> str:
    return f"NB0{1 if on else 0};"


def format_nb_level_set(level: int) -> str:
    return f"NL0{_clamp(level, NB_LEVEL_MIN, NB_LEVEL_MAX):03d};"


def format_nr_set(on: bool) -> str:
    return f"NR0{1 if on else 0};"


def format_nr_level_set(level: int) -> str:
    return f"RL0{_clamp(level, NR_LEVEL_MIN, NR_LEVEL_MAX):02d};"


def format_auto_notch_set(on: bool) -> str:
    return f"BC0{1 if on else 0};"
