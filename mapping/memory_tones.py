"""CTCSS-/DCS-Hilfen fuer den Speicherkanal-Editor."""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional, Tuple

# Standard-CTCSS-Toene (Hz) — Reihenfolge = CAT-Index 1..N (Yaesu-typisch).
CTCSS_TONES_HZ: Tuple[float, ...] = (
    67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5,
    94.8, 97.4, 100.0, 103.5, 107.2, 110.9, 114.8, 118.8, 123.0,
    127.3, 131.8, 136.5, 141.3, 146.2, 151.4, 156.7, 159.8, 162.2,
    165.5, 167.9, 171.3, 173.8, 177.3, 179.9, 183.5, 186.2, 189.9,
    192.8, 196.6, 199.5, 203.5, 206.5, 210.7, 218.1, 225.7, 229.1,
    233.6, 241.8, 250.3, 254.1,
)

# Gaengige DCS-Codes (dezimal).
DCS_CODES: Tuple[int, ...] = (
    23, 25, 26, 31, 32, 36, 43, 47, 51, 53, 54, 65, 71, 72, 73, 74,
    114, 115, 116, 122, 125, 131, 132, 134, 143, 145, 152, 155, 156,
    162, 165, 172, 174, 205, 212, 223, 225, 226, 243, 244, 245, 246,
    251, 252, 255, 256, 261, 263, 265, 266, 271, 274, 306, 311, 315,
    325, 331, 332, 343, 346, 351, 356, 364, 365, 371, 411, 412, 413,
    423, 431, 432, 445, 446, 452, 454, 455, 462, 464, 465, 466, 503,
    506, 516, 523, 526, 532, 546, 565, 606, 612, 624, 627, 631, 632,
    654, 662, 664, 703, 712, 723, 731, 732, 734, 743, 754,
)

_CTCSS_INDEX: Dict[float, int] = {hz: i + 1 for i, hz in enumerate(CTCSS_TONES_HZ)}


class ToneMode(str, Enum):
    OFF = "Aus"
    CTCSS_ENC = "CTCSS Encode"
    CTCSS_ENC_DEC = "CTCSS Encode/Decode"
    DCS_ENC = "DCS Encode"
    DCS_ENC_DEC = "DCS Encode/Decode"


# P8 laut FT-991A CAT Manual (MT/MW): 0=OFF, 1=CTCSS ENC/DEC, 2=CTCSS ENC,
# 3=DCS ENC/DEC, 4=DCS ENC.
_TONE_MODE_TO_P8: Dict[ToneMode, str] = {
    ToneMode.OFF: "0",
    ToneMode.CTCSS_ENC_DEC: "1",
    ToneMode.CTCSS_ENC: "2",
    ToneMode.DCS_ENC_DEC: "3",
    ToneMode.DCS_ENC: "4",
}

_P8_TO_TONE_MODE: Dict[str, ToneMode] = {v: k for k, v in _TONE_MODE_TO_P8.items()}


def tone_mode_from_p8(digit: str) -> ToneMode:
    return _P8_TO_TONE_MODE.get(digit, ToneMode.OFF)


def tone_mode_to_p8(mode: ToneMode) -> str:
    return _TONE_MODE_TO_P8.get(mode, "0")


def ctcss_hz_to_index(hz: float) -> Optional[int]:
    """Liefert den 1-basierten Yaesu-CTCSS-Index oder ``None``."""
    if hz in _CTCSS_INDEX:
        return _CTCSS_INDEX[hz]
    # Naechster bekannter Ton (Toleranz 0.05 Hz).
    for known, idx in _CTCSS_INDEX.items():
        if abs(known - hz) < 0.06:
            return idx
    return None


def ctcss_index_to_hz(index: int) -> Optional[float]:
    if 1 <= index <= len(CTCSS_TONES_HZ):
        return CTCSS_TONES_HZ[index - 1]
    return None


def ctcss_labels() -> List[str]:
    return [f"{hz:.1f}" for hz in CTCSS_TONES_HZ]


def dcs_labels() -> List[str]:
    return [str(code) for code in DCS_CODES]
