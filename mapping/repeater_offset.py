"""Relais / Repeater: Eingangs- vs. Ausgangs-QRG aus VFO-A-Frequenz und Shift."""

from __future__ import annotations

from typing import Optional

from mapping.vfo_bands import clamp_vfo_frequency_hz

SHIFT_SIMPLEX = 0
SHIFT_PLUS = 1
SHIFT_MINUS = 2


def default_repeater_offset_hz(freq_hz: int) -> int:
    """Typische Shift-Werte (2 m / 6 m 600 kHz, 70 cm 7,6 MHz)."""
    f = int(freq_hz)
    if f >= 420_000_000:
        return 7_600_000
    if f >= 144_000_000:
        return 600_000
    if f >= 50_000_000:
        return 600_000
    return 600_000


def parse_if_shift_direction(response: str) -> int:
    """P10 aus ``IF…;`` (0 Simplex, 1 Plus, 2 Minus)."""
    if not response.startswith("IF") or not response.endswith(";"):
        raise ValueError(f"IF-Antwort hat falsches Format: {response!r}")
    body = response[2:-1]
    if len(body) < 25:
        raise ValueError(f"IF-Antwort zu kurz: {response!r}")
    ch = body[24]
    if ch not in ("0", "1", "2"):
        raise ValueError(f"IF P10 unbekannt {ch!r}: {response!r}")
    return int(ch)


def relay_listen_hz(
    output_hz: int,
    *,
    shift_dir: int = SHIFT_MINUS,
    offset_hz: Optional[int] = None,
) -> int:
    """Eingangs-QRG zum Abhören (REV) aus Ausgangs-QRG (Relais-Ausgang)."""
    out = int(output_hz)
    off = default_repeater_offset_hz(out) if offset_hz is None else int(offset_hz)
    if shift_dir == SHIFT_PLUS:
        target = out + off
    elif shift_dir == SHIFT_MINUS:
        target = out - off
    else:
        target = out - off
    return clamp_vfo_frequency_hz(target)
