"""Mappings für TX-Audio-Grundwerte (Version 0.3).

Hier liegen die Tabellen und Hilfsfunktionen für:

- **MIC Gain** (Kommando ``MG``) — 3-stelliger Wert 000..100
- **Speech Processor an/aus + Level** (``PR0n;`` und ``PL``)
- **Parametric MIC EQ an/aus** (``PR1n;``)
- **SSB TX-Bandbreite** (EX110) — 1-stelliger Index 0..4 laut Manual
"""

from __future__ import annotations

from typing import List, Optional, Tuple


# ----------------------------------------------------------------------
# Wertebereiche
# ----------------------------------------------------------------------

MIC_GAIN_MIN = 0
MIC_GAIN_MAX = 100
MIC_GAIN_DEFAULT = 50

PROCESSOR_LEVEL_MIN = 0
PROCESSOR_LEVEL_MAX = 100
PROCESSOR_LEVEL_DEFAULT = 35


# ----------------------------------------------------------------------
# PR-Kommando (Speech Processor / Parametric MIC EQ)
# ----------------------------------------------------------------------
#
# Laut FT-991A CAT Operation Reference Manual (DOC No. 1612-C):
#   P1 = 0 : Speech Processor
#        1 : Parametric Microphone Equalizer
#   P2 = 0 : OFF
#        1 : ON
#
# Also: ``PR00;`` = Speech Processor OFF, ``PR01;`` = ON.
#       ``PR10;`` = Parametric MIC EQ OFF, ``PR11;`` = ON.
#
# Hinweis: Frühere Versionen dieser App haben fälschlich ``1/2`` als
# P2 gesendet (das ist die Codierung der FTDX-Serie). Das FT-991A
# ignoriert die ``2`` still — die Folge: Haken in der GUI gesetzt,
# aber am Radio passiert nichts; beim Re-Read kommt ``PR00;``, der
# Parser wirft, und der Haken springt wieder raus. Deshalb akzeptiert
# der Parser zusätzlich die alte ``2`` als ON-Synonym (= Lese-
# Toleranz für gemischte Setups, geschrieben wird konsequent ``0/1``).

#: Erste Stelle nach ``PR`` selektiert die Funktion.
PR_FUNCTION_PROCESSOR = 0
PR_FUNCTION_MIC_EQ = 1

# P2-Werte laut Manual.
_PR_STATE_OFF = 0
_PR_STATE_ON = 1


def format_pr_query(function: int) -> str:
    """``PR0;`` / ``PR1;`` — fragt den Zustand einer PR-Funktion ab."""
    if function not in (0, 1):
        raise ValueError(f"PR-Funktion muss 0 oder 1 sein, war {function}")
    return f"PR{function};"


def format_pr_set(function: int, enabled: bool) -> str:
    """``PR0n;`` / ``PR1n;`` mit n=0 (OFF) oder 1 (ON).

    Beispiele: ``PR00;`` Processor aus, ``PR01;`` Processor an,
    ``PR10;`` MIC EQ aus, ``PR11;`` MIC EQ an.
    """
    if function not in (0, 1):
        raise ValueError(f"PR-Funktion muss 0 oder 1 sein, war {function}")
    state = _PR_STATE_ON if enabled else _PR_STATE_OFF
    return f"PR{function}{state};"


def parse_pr_response(response: str, function: int) -> bool:
    """Erwartet ``PR<function><state>;`` mit state ∈ {0, 1}.

    Aus Robustheit wird auch ``2`` toleriert (siehe Kommentar oben) und
    als ON interpretiert — so verkraftet die App ein Radio, dem irgendwer
    noch ein altes ``PR02;`` untergeschoben hat.
    """
    expected_prefix = f"PR{function}"
    if not response.startswith(expected_prefix) or not response.endswith(";"):
        raise ValueError(
            f"PR-Antwort hat falsches Format (erwartet {expected_prefix}n;): "
            f"{response!r}"
        )
    payload = response[len(expected_prefix):-1]
    if payload not in ("0", "1", "2"):
        raise ValueError(
            f"PR-Antwort hat unerwarteten Zustand {payload!r} (erwartet '0'/'1'): "
            f"{response!r}"
        )
    return payload in ("1", "2")


# ----------------------------------------------------------------------
# SSB TX-Bandbreite (EX110)
# ----------------------------------------------------------------------

#: Indizes 0..4 entsprechen den 5 wählbaren Filter-Bandpässen.
#: Jeder Eintrag: (CAT-Index, Profil-Key, Anzeige-Label).
SSB_BPF_TABLE: List[Tuple[int, str, str]] = [
    (0, "50-3000",  "50 – 3000 Hz"),
    (1, "100-2900", "100 – 2900 Hz"),
    (2, "200-2800", "200 – 2800 Hz"),
    (3, "300-2700", "300 – 2700 Hz"),
    (4, "400-2600", "400 – 2600 Hz"),
]

#: Menü-Nummer für die SSB-TX-Bandbreite.
SSB_BPF_MENU = 110

SSB_BPF_DEFAULT_KEY = "100-2900"


def ssb_bpf_index_to_key(index: int) -> str:
    for i, key, _label in SSB_BPF_TABLE:
        if i == index:
            return key
    raise ValueError(f"SSB-BPF-Index {index} unbekannt (0..4 erlaubt)")


def ssb_bpf_key_to_index(key: str) -> int:
    for i, k, _label in SSB_BPF_TABLE:
        if k == key:
            return i
    raise ValueError(f"SSB-BPF-Schlüssel {key!r} unbekannt")


def ssb_bpf_key_to_label(key: str) -> str:
    for _i, k, label in SSB_BPF_TABLE:
        if k == key:
            return label
    return key


def ssb_bpf_choices() -> List[Tuple[str, str]]:
    """Liefert ``[(key, label), ...]`` für GUI-Dropdowns."""
    return [(key, label) for _i, key, label in SSB_BPF_TABLE]


def ssb_bpf_encode_for_menu(key: str) -> str:
    """Profil-Schlüssel -> CAT-Rohwert für EX110 (1-stellig, 0..4)."""
    return f"{ssb_bpf_key_to_index(key):d}"


def ssb_bpf_decode_from_menu(raw: str) -> str:
    """CAT-Rohwert von EX110 -> Profil-Schlüssel."""
    try:
        idx = int(raw)
    except ValueError as exc:
        raise ValueError(f"Ungültiger SSB-BPF-Rohwert: {raw!r}") from exc
    return ssb_bpf_index_to_key(idx)


# ----------------------------------------------------------------------
# 3-stellige 0..100-Werte (MIC Gain, Processor Level)
# ----------------------------------------------------------------------


def format_three_digit(prefix: str, value: int) -> str:
    """``MG050;`` / ``PL035;`` — geklemmt auf 0..100."""
    v = max(0, min(100, int(value)))
    return f"{prefix}{v:03d};"


def parse_three_digit(prefix: str, response: str) -> int:
    """Parsed ``MG050;`` → ``50``."""
    if not response.startswith(prefix) or not response.endswith(";"):
        raise ValueError(
            f"{prefix}-Antwort hat falsches Format: {response!r}"
        )
    body = response[len(prefix):-1]
    try:
        return int(body)
    except ValueError as exc:
        raise ValueError(f"{prefix}-Wert nicht parsebar: {response!r}") from exc
