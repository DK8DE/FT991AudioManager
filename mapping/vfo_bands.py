"""Abdeckungsbereiche VFO-A/B am FT-991/991A (laut Datenblatt / CAT-Praxis).

Zwischen den Segmenten gibt es Lücken — dort antwortet das Gerät auf ``FA`` mit ``?;``.
Beim Scrollen springt die GUI an die Bandgrenze bzw. in den nächsten Bereich
(z. B. 56 MHz → 2 m / 144 MHz, 164 MHz → 70 cm / 430 MHz).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

# CAT-Grenzen am FT-991/A (Praxis: 0,030000 MHz .. 469,999999 MHz)
VFO_CAT_MIN_HZ: int = 30_000
VFO_CAT_MAX_HZ: int = 469_999_999


@dataclass(frozen=True)
class VfoBandSegment:
    """Ein zusammenhängender RX/TX-Bereich."""

    min_hz: int
    max_hz: int
    up_entry_hz: int  # Hochscrollen über max_hz (z. B. 144 MHz für 2 m)
    down_entry_hz: int  # Runterscrollen unter min_hz


# Reihenfolge aufsteigend; Grenzen inkl. (56,0 MHz = letztes gültiges HF vor der Lücke).
FT991_VFO_SEGMENTS: Tuple[VfoBandSegment, ...] = (
    VfoBandSegment(
        min_hz=30_000,
        max_hz=55_999_999,
        up_entry_hz=144_000_000,
        down_entry_hz=55_999_999,
    ),
    VfoBandSegment(
        min_hz=76_000_000,
        max_hz=108_000_000,
        up_entry_hz=144_000_000,
        down_entry_hz=76_000_000,
    ),
    VfoBandSegment(
        min_hz=122_000_000,
        max_hz=164_999_999,
        up_entry_hz=430_000_000,
        down_entry_hz=164_999_999,
    ),
    VfoBandSegment(
        min_hz=420_000_000,
        max_hz=469_999_999,
        up_entry_hz=469_999_999,
        down_entry_hz=420_000_000,
    ),
)


def _segment_index_for_hz(hz: int) -> Optional[int]:
    for i, seg in enumerate(FT991_VFO_SEGMENTS):
        if seg.min_hz <= hz <= seg.max_hz:
            return i
    return None


def segment_for_hz(hz: int) -> Optional[VfoBandSegment]:
    idx = _segment_index_for_hz(hz)
    if idx is None:
        return None
    return FT991_VFO_SEGMENTS[idx]


def snap_to_valid_vfo_hz(hz: int, *, direction: int = 0) -> int:
    """Frequenz in ein gültiges Segment bringen (Lücken überspringen).

    ``direction``: +1 = von unten, -1 = von oben, 0 = nächstes Segment ab Stand.
    """
    h = int(hz)
    if _segment_index_for_hz(h) is not None:
        return _clamp_segment(h)

    if direction > 0:
        for seg in FT991_VFO_SEGMENTS:
            if h < seg.min_hz:
                return seg.min_hz
        return FT991_VFO_SEGMENTS[-1].max_hz

    if direction < 0:
        for seg in reversed(FT991_VFO_SEGMENTS):
            if h > seg.max_hz:
                return seg.max_hz
        return FT991_VFO_SEGMENTS[0].min_hz

    # In Lücke: nächstes Segment in Richtung der Lücke-Mitte
    for seg in FT991_VFO_SEGMENTS:
        if h < seg.min_hz:
            return seg.min_hz
    return FT991_VFO_SEGMENTS[-1].max_hz


def _clamp_segment(hz: int) -> int:
    seg = segment_for_hz(hz)
    if seg is None:
        return snap_to_valid_vfo_hz(hz)
    return max(seg.min_hz, min(seg.max_hz, int(hz)))


def step_vfo_frequency_hz(hz: int, delta_hz: int) -> int:
    """Frequenz um ``delta_hz`` ändern, Bandlücken als Sprung behandeln."""
    if delta_hz == 0:
        return snap_to_valid_vfo_hz(hz)

    current = snap_to_valid_vfo_hz(hz, direction=1 if delta_hz > 0 else -1)
    idx = _segment_index_for_hz(current)
    if idx is None:
        return snap_to_valid_vfo_hz(current + delta_hz, direction=1 if delta_hz > 0 else -1)

    seg = FT991_VFO_SEGMENTS[idx]
    target = current + int(delta_hz)

    if delta_hz > 0:
        if target <= seg.max_hz:
            return target
        if idx + 1 < len(FT991_VFO_SEGMENTS):
            return seg.up_entry_hz
        return seg.max_hz

    # delta_hz < 0
    if target >= seg.min_hz:
        return target
    if idx > 0:
        return FT991_VFO_SEGMENTS[idx - 1].down_entry_hz
    return seg.min_hz


def is_valid_vfo_frequency_hz(hz: int) -> bool:
    return _segment_index_for_hz(int(hz)) is not None


def clamp_vfo_frequency_hz(hz: int) -> int:
    """Für CAT-Schreiben: gültige Frequenz oder Sprung in nächstes Segment."""
    return snap_to_valid_vfo_hz(int(hz))
