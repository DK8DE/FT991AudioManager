"""CAT-Befehle für Band- und Speicherkanal-Umschaltung (FT-991/991A).

Quelle: FT-991(A) CAT Operation Reference Book (1711-D / 1612-D).
"""

from __future__ import annotations


def format_band_up() -> str:
    """``BU0;`` — nächstes Band (wie Band-Up-Taste)."""
    return "BU0;"


def format_band_down() -> str:
    """``BD0;`` — vorheriges Band (wie Band-Down-Taste)."""
    return "BD0;"


def format_memory_channel_up() -> str:
    """``CH0;`` — Speicherkanal hoch."""
    return "CH0;"


def format_memory_channel_down() -> str:
    """``CH1;`` — Speicherkanal runter."""
    return "CH1;"
