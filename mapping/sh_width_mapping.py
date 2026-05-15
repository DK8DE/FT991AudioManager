"""CAT **SH WIDTH** — Sendebandbreite (FT-991/991A).

Quelle: FT-991 CAT Operation Reference Book (Tabellen SSB/CW/RTTY).

* Lesen: ``SH0;`` → Antwort ``SH0P1P2P2;`` mit P1=``0`` und P2 zweistellig ``00``…``21``.
* Schreiben: ``SH0`` + **zweistelliger** P2, z. B. ``SH05;`` für P2=5,
  ``SH016;`` für P2=16 (Echo wie vom FT-991/991A). **Nicht** ``SH0005;`` —
  das liefert am Gerät oft ``?;``.

Die Zuordnung P2 → Anzeige-Hz hängt von Modus und Narrow/Wide ab. Für die GUI
bevorzugen wir die **Wide**-Spalte, falls belegt, sonst **Narrow** — damit
liegt der sichtbare Bereich typischerweise zwischen **50 Hz** und **3200 Hz**.
"""

from __future__ import annotations

from typing import Dict, Optional

from mapping.rx_mapping import RxMode, mode_group_for

SH_P2_MIN = 0
SH_P2_MAX = 21

# ---------------------------------------------------------------------------
# Tabellen wie im Handbuch (Screenshot); „-“ = kein Eintrag in dieser Spalte.
# ---------------------------------------------------------------------------

_SSB_NARROW: Dict[int, int] = {
    0: 1500,
    1: 200,
    2: 400,
    3: 600,
    4: 850,
    5: 1100,
    6: 1350,
    7: 1500,
    8: 1650,
    9: 1800,
}

_SSB_WIDE: Dict[int, int] = {
    0: 2400,
    9: 1800,
    10: 1950,
    11: 2100,
    12: 2200,
    13: 2300,
    14: 2400,
    15: 2500,
    16: 2600,
    17: 2700,
    18: 2800,
    19: 2900,
    20: 3000,
    21: 3200,
}

_CW_NARROW: Dict[int, int] = {
    0: 500,
    1: 50,
    2: 100,
    3: 150,
    4: 200,
    5: 250,
    6: 300,
    7: 350,
    8: 400,
    9: 450,
    10: 500,
}

_CW_WIDE: Dict[int, int] = {
    0: 2400,
    10: 500,
    11: 800,
    12: 1200,
    13: 1400,
    14: 1700,
    15: 2000,
    16: 2400,
    17: 3000,
}

_RT_NARROW: Dict[int, int] = {
    0: 300,
    1: 50,
    2: 100,
    3: 150,
    4: 200,
    5: 250,
    6: 300,
    7: 350,
    8: 400,
    9: 450,
    10: 500,
}

_RT_WIDE: Dict[int, int] = {
    0: 500,
    10: 500,
    11: 800,
    12: 1200,
    13: 1400,
    14: 1700,
    15: 2000,
    16: 2400,
    17: 3000,
}


def _merge_wide_first(wide: Dict[int, int], narrow: Dict[int, int]) -> Dict[int, int]:
    out: Dict[int, int] = {}
    for p2 in range(SH_P2_MIN, SH_P2_MAX + 1):
        hz = wide.get(p2)
        if hz is None:
            hz = narrow.get(p2)
        if hz is not None:
            out[p2] = hz
    return out


# Für schnelle Lookups (optional)
_SH_HZ_SSB = _merge_wide_first(_SSB_WIDE, _SSB_NARROW)
_SH_HZ_CW = _merge_wide_first(_CW_WIDE, _CW_NARROW)
_SH_HZ_RT = _merge_wide_first(_RT_WIDE, _RT_NARROW)

# Skala für die **symmetrische** Mini-Anzeige (50 … 3200 Hz)
VISUAL_HZ_MIN = 50
VISUAL_HZ_MAX = 3200


def sh_display_hz(mode: Optional[RxMode], p2: int) -> Optional[int]:
    """Interpretiert P2 für die Anzeige (Hz) je nach Funk-Modusgruppe."""
    if mode is None or mode is RxMode.UNKNOWN:
        return None
    p = int(p2)
    if not SH_P2_MIN <= p <= SH_P2_MAX:
        return None
    mg = mode_group_for(mode)
    if mg == "SSB":
        return _SH_HZ_SSB.get(p)
    if mg == "CW":
        return _SH_HZ_CW.get(p)
    if mg in ("RTTY", "DATA"):
        return _SH_HZ_RT.get(p)
    return None


def sh_bandwidth_visible_for_mode(mode: Optional[RxMode]) -> bool:
    """True, wenn SH WIDTH am FT-991 für diese Betriebsart üblichweise Sinn ergibt."""
    if mode is None or mode is RxMode.UNKNOWN:
        return False
    return mode_group_for(mode) in frozenset({"SSB", "CW", "RTTY", "DATA"})


def sh_supported_p2_indices(mode: Optional[RxMode]) -> frozenset[int]:
    """CAT-P2-Indizes, die für die Modusgruppe in den Handbuchstabellen vorkommen.

    Andere Indizes können am Gerät mit ``?;`` abgewiesen werden (z. B. CW/Data:
    kein P2>17).
    """
    if mode is None or mode is RxMode.UNKNOWN:
        return frozenset(range(SH_P2_MIN, SH_P2_MAX + 1))
    mg = mode_group_for(mode)
    if mg == "SSB":
        table = _SH_HZ_SSB
    elif mg == "CW":
        table = _SH_HZ_CW
    elif mg in ("RTTY", "DATA"):
        table = _SH_HZ_RT
    else:
        return frozenset()
    return frozenset(table.keys())


def sh_snap_p2_to_supported(p2: int, mode: Optional[RxMode]) -> int:
    """Klemmt P2 auf den Bereich 0..21 und snapt auf die nächstliegende gültige Stufe."""
    p = max(SH_P2_MIN, min(SH_P2_MAX, int(p2)))
    valid = sh_supported_p2_indices(mode)
    if not valid:
        return p
    if p in valid:
        return p
    return min(valid, key=lambda x: abs(x - p))


def format_sh_width_query() -> str:
    return "SH0;"


def format_sh_width_set(p2: int) -> str:
    p = int(p2)
    if not SH_P2_MIN <= p <= SH_P2_MAX:
        raise ValueError(f"SH P2 muss {SH_P2_MIN}..{SH_P2_MAX} sein, war {p2}")
    # Zwei Ziffern nach SH0 — wie die SH0;-Antwort (z.B. SH016;), nicht SH0016;.
    return f"SH0{p:02d};"


def parse_sh_width_response(response: str) -> int:
    """``SH0P1P2P2;`` → P2 (0..21)."""
    s = response.strip()
    if not s.startswith("SH0") or not s.endswith(";"):
        raise ValueError(f"SH-Antwort hat falsches Format: {response!r}")
    body = s[3:-1]
    if len(body) == 3 and body.isdigit():
        p1 = int(body[0])
        p2 = int(body[1:])
        if p1 != 0:
            raise ValueError(f"SH P1 unerwartet (erwartet 0): {response!r}")
        if not SH_P2_MIN <= p2 <= SH_P2_MAX:
            raise ValueError(f"SH P2 ausserhalb 0..21: {response!r}")
        return p2
    if len(body) == 2 and body.isdigit():
        p2 = int(body)
        if not SH_P2_MIN <= p2 <= SH_P2_MAX:
            raise ValueError(f"SH P2 ausserhalb 0..21: {response!r}")
        return p2
    raise ValueError(f"SH-Antwort nicht parsebar: {response!r}")
