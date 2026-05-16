"""TX-Leistung per CAT (Manual 1711-D).

* ``EX137``–``EX140`` — **TX MAX POWER** (Obergrenze je Band)
* ``PC`` — **POWER CONTROL** (tatsächliche Sendeleistung, 005–100)
"""

from __future__ import annotations

from typing import List

#: EX-Menüs: TX MAX POWER (Deckel)
MENU_HF_TX_MAX_POWER = 137
MENU_50M_TX_MAX_POWER = 138
MENU_144M_TX_MAX_POWER = 139
MENU_430M_TX_MAX_POWER = 140

PC_POWER_MIN = 5
PC_POWER_MAX = 100


def encode_tx_max_power_menu(watts: int) -> str:
    """Menü-Rohwert für EX137/138/139/140 (P2 = 005 … 100)."""
    w = int(watts)
    if not PC_POWER_MIN <= w <= PC_POWER_MAX or w % 5 != 0:
        raise ValueError(
            f"TX-MAX-Leistung muss {PC_POWER_MIN}..{PC_POWER_MAX} in 5-W-Schritten sein: {watts}"
        )
    return f"{w:03d}"


# Rückwärtskompatibel
encode_tx_power_menu = encode_tx_max_power_menu


def clamp_pc_power_watts(watts: int, *, max_watts: int = PC_POWER_MAX) -> int:
    w = int(watts)
    cap = max(PC_POWER_MIN, min(PC_POWER_MAX, int(max_watts)))
    return max(PC_POWER_MIN, min(cap, w))


def format_pc_set(watts: int, *, max_watts: int = PC_POWER_MAX) -> str:
    """``PCnnn;`` — Sendeleistung stellen (005–100)."""
    w = clamp_pc_power_watts(watts, max_watts=max_watts)
    return f"PC{w:03d};"


def parse_pc_response(response: str) -> int:
    """``PC050;`` → 50."""
    if not response.startswith("PC") or not response.endswith(";"):
        raise ValueError(f"PC-Antwort hat falsches Format: {response!r}")
    body = response[2:-1]
    if not body:
        raise ValueError(f"PC-Antwort ohne Wert: {response!r}")
    try:
        return int(body)
    except ValueError as exc:
        raise ValueError(f"PC-Wert nicht numerisch: {response!r}") from exc


def power_steps_watts(*, max_w: int, step: int = 5) -> List[int]:
    """5, 10, … bis ``max_w`` (inklusive)."""
    if max_w < step:
        raise ValueError(f"max_w ({max_w}) kleiner als step ({step})")
    return list(range(step, max_w + 1, step))
